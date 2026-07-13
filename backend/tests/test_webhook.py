"""Tests for api/billing.py's Stripe webhook handler — signature verification,
event-ID idempotency, and the new customer.subscription.updated handling.

stripe.Webhook.construct_event is monkeypatched to return a controlled event dict
instead of performing real HMAC verification — this tests OUR dedup/business-logic
code, not Stripe's SDK. The "missing signature header" test exercises the real
pre-verification code path (billing.py checks the header before ever calling
construct_event), so that one is a genuine regression guard.
"""

import asyncio
from datetime import datetime

import pytest
import stripe
from fastapi import HTTPException

from backend.app.api import billing as billing_mod
from backend.app.db.database import UserUsageDB, UsageAuditLogDB, StripeWebhookEventDB


class _FakeRequest:
    def __init__(self, body: bytes = b"{}", with_signature: bool = True):
        self._body = body
        self.headers = {"stripe-signature": "fake-sig"} if with_signature else {}

    async def body(self):
        return self._body


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def stripe_price_env(monkeypatch):
    monkeypatch.setenv("STRIPE_PRO_PRICE_ID", "price_core_test")
    monkeypatch.setenv("STRIPE_PREMIUM_PRICE_ID", "price_operator_test")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")


def test_missing_signature_header_returns_400(db):
    with pytest.raises(HTTPException) as exc_info:
        _run(billing_mod.stripe_webhook(_FakeRequest(with_signature=False)))
    assert exc_info.value.status_code == 400


def test_invalid_signature_returns_400(db, monkeypatch):
    def raise_sig_error(body, sig, secret):
        raise stripe.error.SignatureVerificationError("bad sig", sig)

    monkeypatch.setattr(stripe.Webhook, "construct_event", raise_sig_error)
    with pytest.raises(HTTPException) as exc_info:
        _run(billing_mod.stripe_webhook(_FakeRequest()))
    assert exc_info.value.status_code == 400


def test_checkout_completed_derives_tier_from_stripe_price_not_metadata(db, monkeypatch):
    """Tier must come from the subscription's actual Stripe price ID, not the
    client-supplied metadata.plan string — metadata says 'core' but the real
    subscription price is Operator's; the DB should end up with 'operator'."""
    event = {
        "id": "evt_1",
        "type": "checkout.session.completed",
        "data": {"object": {
            "metadata": {"user_id": "webhook_user_1", "plan": "core"},  # deliberately wrong
            "customer": "cus_1",
            "subscription": "sub_1",
        }},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda body, sig, secret: event)
    monkeypatch.setattr(
        stripe.Subscription, "retrieve",
        lambda sub_id: {"items": {"data": [{"price": {"id": "price_operator_test"}}]}},
    )

    result = _run(billing_mod.stripe_webhook(_FakeRequest()))
    assert result == {"status": "received"}

    with db.session_scope() as s:
        u = s.query(UserUsageDB).filter_by(user_id="webhook_user_1").first()
        assert u.plan == "operator"
        audit = s.query(UsageAuditLogDB).filter_by(user_id="webhook_user_1", event_type="tier_changed").count()
        assert audit == 1


def test_replayed_event_id_is_a_true_noop(db, monkeypatch):
    event = {
        "id": "evt_replay",
        "type": "checkout.session.completed",
        "data": {"object": {
            "metadata": {"user_id": "webhook_user_2", "plan": "core"},
            "customer": "cus_2",
            "subscription": "sub_2",
        }},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda body, sig, secret: event)
    monkeypatch.setattr(
        stripe.Subscription, "retrieve",
        lambda sub_id: {"items": {"data": [{"price": {"id": "price_core_test"}}]}},
    )

    first = _run(billing_mod.stripe_webhook(_FakeRequest()))
    assert first == {"status": "received"}

    # Simulate manual drift after the first delivery, to prove a replay doesn't stomp it.
    with db.session_scope() as s:
        u = s.query(UserUsageDB).filter_by(user_id="webhook_user_2").first()
        u.plan = "free"

    second = _run(billing_mod.stripe_webhook(_FakeRequest()))
    assert second == {"status": "already_processed"}

    with db.session_scope() as s:
        u = s.query(UserUsageDB).filter_by(user_id="webhook_user_2").first()
        assert u.plan == "free", "replay must not re-apply business logic"
        audit = s.query(UsageAuditLogDB).filter_by(user_id="webhook_user_2", event_type="tier_changed").count()
        assert audit == 1, "replay must not write a duplicate audit row"
        events = s.query(StripeWebhookEventDB).filter_by(event_id="evt_replay").count()
        assert events == 1


def test_subscription_updated_active_status_applies_new_tier(db, monkeypatch):
    with db.session_scope() as s:
        s.add(UserUsageDB(user_id="webhook_user_3", plan="free", tokens_used=0,
                           stripe_customer_id="cus_3", period_start=datetime.utcnow()))

    event = {
        "id": "evt_sub_updated",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "customer": "cus_3", "status": "active", "id": "sub_3",
            "items": {"data": [{"price": {"id": "price_operator_test"}}]},
        }},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda body, sig, secret: event)
    _run(billing_mod.stripe_webhook(_FakeRequest()))

    with db.session_scope() as s:
        u = s.query(UserUsageDB).filter_by(user_id="webhook_user_3").first()
        assert u.plan == "operator"


@pytest.mark.parametrize("status", ["past_due", "canceled", "unpaid"])
def test_subscription_updated_non_active_status_downgrades_to_free(db, monkeypatch, status):
    with db.session_scope() as s:
        s.add(UserUsageDB(user_id=f"webhook_user_{status}", plan="operator", tokens_used=0,
                           stripe_customer_id=f"cus_{status}", period_start=datetime.utcnow()))

    event = {
        "id": f"evt_{status}",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "customer": f"cus_{status}", "status": status, "id": "sub_x",
            "items": {"data": [{"price": {"id": "price_operator_test"}}]},
        }},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda body, sig, secret: event)
    _run(billing_mod.stripe_webhook(_FakeRequest()))

    with db.session_scope() as s:
        u = s.query(UserUsageDB).filter_by(user_id=f"webhook_user_{status}").first()
        assert u.plan == "free"


def test_subscription_updated_unknown_price_id_fails_safe_to_free(db, monkeypatch):
    """An unrecognized price ID must never grant a paid tier."""
    with db.session_scope() as s:
        s.add(UserUsageDB(user_id="webhook_user_unknown", plan="free", tokens_used=0,
                           stripe_customer_id="cus_unknown", period_start=datetime.utcnow()))

    event = {
        "id": "evt_unknown_price",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "customer": "cus_unknown", "status": "active", "id": "sub_unknown",
            "items": {"data": [{"price": {"id": "price_totally_unrecognized"}}]},
        }},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda body, sig, secret: event)
    _run(billing_mod.stripe_webhook(_FakeRequest()))

    with db.session_scope() as s:
        u = s.query(UserUsageDB).filter_by(user_id="webhook_user_unknown").first()
        assert u.plan == "free"


def test_subscription_deleted_downgrades_to_free(db, monkeypatch):
    with db.session_scope() as s:
        s.add(UserUsageDB(user_id="webhook_user_4", plan="core", tokens_used=0,
                           stripe_customer_id="cus_4", stripe_subscription_id="sub_4",
                           period_start=datetime.utcnow()))

    event = {
        "id": "evt_deleted",
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_4"}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda body, sig, secret: event)
    _run(billing_mod.stripe_webhook(_FakeRequest()))

    with db.session_scope() as s:
        u = s.query(UserUsageDB).filter_by(user_id="webhook_user_4").first()
        assert u.plan == "free"
        assert u.stripe_subscription_id is None

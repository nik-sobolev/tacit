"""Billing API — usage status, Stripe integration, webhooks"""

import os
import json
import structlog
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, Depends
import stripe

from uuid import uuid4

from ..core.auth import get_current_user
from ..core import tiers
from ..db.database import get_database, UserUsageDB, UsageAuditLogDB, StripeWebhookEventDB

logger = structlog.get_logger()
router = APIRouter()

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PRO_PRICE_ID = os.getenv("STRIPE_PRO_PRICE_ID")
STRIPE_PREMIUM_PRICE_ID = os.getenv("STRIPE_PREMIUM_PRICE_ID")
APP_DOMAIN = os.getenv("APP_DOMAIN", "https://www.trytacit.app")

# v1 (token-based) limits — kept for GET /billing/status's legacy response shape
# during the usage-v2 shadow period. See core/tiers.py for the v2 per-category
# limits and the v1(pro/premium)->v2(core/operator) alias.
LIMITS = {"free": 100_000, "pro": 500_000, "premium": 1_000_000}


@router.get("/billing/status")
async def get_usage_status(current_user: dict = Depends(get_current_user)):
    """Get user's current usage and plan.

    Response shape depends on FEATURE_USAGE_V2 (see api/features.py's
    usage_v2_enabled flag, checked by the frontend's loadUsageMeter() to know
    which shape to expect — this lets backend and frontend deploy independently
    during the transition). The v2 shape never includes tokens/cost — those are
    server-side-only, see core/entitlements.py's get_usage_snapshot()."""
    if os.getenv("FEATURE_USAGE_V2", "true").lower() == "true":
        from ..core.entitlements import get_usage_snapshot
        return get_usage_snapshot(current_user["id"])

    db = get_database()

    with db.session_scope() as session:
        usage = session.query(UserUsageDB).filter_by(user_id=current_user["id"]).first()

        if not usage:
            usage = UserUsageDB(
                user_id=current_user["id"],
                plan="free",
                tokens_used=0,
                period_start=datetime.utcnow()
            )
            session.add(usage)
            session.commit()

        plan = usage.plan
        limit = LIMITS.get(plan, LIMITS["free"])
        pct = (usage.tokens_used / limit * 100) if limit > 0 else 0

        return {
            "plan": plan,
            "tokens_used": usage.tokens_used,
            "tokens_limit": limit,
            "pct_used": round(pct, 1),
            "period_start": usage.period_start.isoformat(),
        }


@router.post("/billing/checkout/{plan}")
async def create_checkout_session(plan: str, current_user: dict = Depends(get_current_user)):
    """Create Stripe checkout session for specified plan (pro or premium)"""
    if plan == "pro":
        price_id = STRIPE_PRO_PRICE_ID
    elif plan == "premium":
        price_id = STRIPE_PREMIUM_PRICE_ID
    else:
        raise HTTPException(status_code=400, detail="Invalid plan")

    if not price_id:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    try:
        email = current_user.get("email") or ""
        checkout_params = dict(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=f"{APP_DOMAIN}?billing=success",
            cancel_url=APP_DOMAIN,
            metadata={"user_id": current_user["id"], "plan": plan},
            payment_method_collection="always",
        )
        # Pass email so Stripe pre-fills the correct account, not a cached Link session
        if email:
            checkout_params["customer_email"] = email
        session = stripe.checkout.Session.create(**checkout_params)
        return {"url": session.url}
    except Exception as e:
        logger.error("stripe_checkout_error", error=str(e), plan=plan)
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


@router.post("/billing/portal")
async def create_portal_session(current_user: dict = Depends(get_current_user)):
    """Create Stripe customer portal session"""
    db = get_database()

    with db.session_scope() as session:
        usage = session.query(UserUsageDB).filter_by(user_id=current_user["id"]).first()

        if not usage or not usage.stripe_customer_id:
            raise HTTPException(status_code=400, detail="No active subscription")

        try:
            portal_session = stripe.billing_portal.Session.create(
                customer=usage.stripe_customer_id,
                return_url=APP_DOMAIN,
            )
            return {"url": portal_session.url}
        except Exception as e:
            logger.error("stripe_portal_error", error=str(e))
            raise HTTPException(status_code=500, detail="Failed to create portal session")


def _write_tier_audit(db_session, user_id: str, from_value: str, to_value: str, source: str, detail: str) -> None:
    db_session.add(UsageAuditLogDB(
        id=str(uuid4()), user_id=user_id, event_type="tier_changed",
        from_value=from_value, to_value=to_value, source=source, detail=detail,
    ))


@router.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events (no Clerk auth — protected by signature verification)."""
    body = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing stripe-signature")

    try:
        event = stripe.Webhook.construct_event(body, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    db = get_database()
    event_id = event["id"]
    event_type = event["type"]

    # Event-ID dedup + the business-logic write for this event happen inside ONE
    # transaction, so a crash between "marked processed" and "applied the change"
    # can't happen — either both land or neither does. Stripe retries deliveries
    # on non-2xx/timeout, so without this a retried event would silently re-apply
    # (usually harmless here since writes are idempotent-by-value, but this makes
    # it structurally safe rather than accidentally safe, and gives an audit trail
    # of exactly which Stripe events were processed).
    with db.session_scope() as db_session:
        if db_session.query(StripeWebhookEventDB).filter_by(event_id=event_id).first():
            logger.info("webhook_event_already_processed", event_id=event_id, event_type=event_type)
            return {"status": "already_processed"}
        db_session.add(StripeWebhookEventDB(event_id=event_id, event_type=event_type))

        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            user_id = session["metadata"].get("user_id")
            customer_id = session["customer"]
            subscription_id = session.get("subscription")

            if user_id and customer_id:
                # Derive tier from the actual Stripe price the subscription was
                # created for, not the client-supplied metadata.plan string —
                # metadata is set by our own /billing/checkout/{plan} endpoint from
                # a path param, so trusting it verbatim here means the webhook's
                # tier assignment is only as trustworthy as that earlier request.
                # The subscription's price ID is what Stripe actually charged for.
                tier = "free"
                if subscription_id:
                    try:
                        subscription = stripe.Subscription.retrieve(subscription_id)
                        price_id = subscription["items"]["data"][0]["price"]["id"]
                        tier = tiers.price_id_to_tier(price_id)
                    except Exception as e:
                        logger.error("webhook_subscription_lookup_failed", error=str(e), event_id=event_id)
                        # Fail safe to metadata's plan (legacy alias resolved), rather
                        # than granting no plan at all when Stripe's API is unreachable.
                        tier = tiers.normalize_tier(session["metadata"].get("plan", "free"))

                usage = db_session.query(UserUsageDB).filter_by(user_id=user_id).first()
                old_tier = usage.plan if usage else None
                if not usage:
                    usage = UserUsageDB(user_id=user_id, tokens_used=0, period_start=datetime.utcnow())
                    db_session.add(usage)
                usage.plan = tier
                usage.stripe_customer_id = customer_id
                usage.stripe_subscription_id = subscription_id
                usage.updated_at = datetime.utcnow()
                _write_tier_audit(db_session, user_id, old_tier, tier, "stripe_webhook", "checkout.session.completed")
                logger.info("subscription_created", user_id=user_id, plan=tier)

        elif event_type == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            customer_id = subscription["customer"]

            usage = db_session.query(UserUsageDB).filter_by(stripe_customer_id=customer_id).first()
            if usage:
                old_tier = usage.plan
                usage.plan = "free"
                usage.stripe_subscription_id = None
                usage.updated_at = datetime.utcnow()
                if old_tier != "free":
                    _write_tier_audit(db_session, usage.user_id, old_tier, "free", "stripe_webhook", "customer.subscription.deleted")
                logger.info("subscription_deleted", user_id=usage.user_id, plan="free")

        elif event_type == "customer.subscription.updated":
            # Fixes a real gap: upgrading/downgrading via the Stripe customer portal
            # previously never reached our DB at all — only checkout.session.completed
            # (initial signup) and subscription.deleted were handled.
            subscription = event["data"]["object"]
            customer_id = subscription["customer"]
            status = subscription.get("status")  # "active" | "past_due" | "canceled" | ...
            items = subscription.get("items", {}).get("data", [])
            price_id = items[0]["price"]["id"] if items else None
            tier = tiers.price_id_to_tier(price_id) if (status == "active" and price_id) else "free"

            usage = db_session.query(UserUsageDB).filter_by(stripe_customer_id=customer_id).first()
            if usage:
                old_tier = usage.plan
                usage.plan = tier
                usage.stripe_subscription_id = subscription.get("id")
                usage.updated_at = datetime.utcnow()
                if old_tier != tier:
                    _write_tier_audit(
                        db_session, usage.user_id, old_tier, tier, "stripe_webhook",
                        f"customer.subscription.updated status={status}",
                    )
                logger.info("subscription_updated", user_id=usage.user_id, plan=tier, status=status)

    return {"status": "received"}


@router.post("/billing/set-superadmin/{user_id}")
async def set_superadmin(user_id: str, request: Request):
    """Set a user's plan to superadmin (unlimited). Protected by RECOVERY_KEY."""
    key = request.headers.get("X-Recovery-Key", "")
    expected_key = os.getenv("RECOVERY_KEY")
    if not expected_key:
        raise HTTPException(status_code=500, detail="Recovery key not configured")
    if key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid key")

    db = get_database()
    with db.session_scope() as session:
        usage = session.query(UserUsageDB).filter_by(user_id=user_id).first()
        old_plan = usage.plan if usage else None
        if usage:
            usage.plan = "superadmin"
            usage.tokens_used = 0
            usage.updated_at = datetime.utcnow()
        else:
            session.add(UserUsageDB(
                user_id=user_id, plan="superadmin",
                tokens_used=0, period_start=datetime.utcnow()
            ))
        _write_tier_audit(session, user_id, old_plan, "superadmin", "recovery_endpoint", "set_superadmin")
    logger.info("superadmin_set", user_id=user_id)
    return {"status": "ok", "plan": "superadmin"}


@router.post("/billing/set-plan/{user_id}/{plan}")
async def set_plan(user_id: str, plan: str, request: Request):
    """Set a user's plan manually. Protected by RECOVERY_KEY."""
    key = request.headers.get("X-Recovery-Key", "")
    expected_key = os.getenv("RECOVERY_KEY")
    if not expected_key or key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid key")
    # Accept both legacy (pro/premium) and v2 (core/operator) tier names — set-plan
    # is an admin/recovery tool, so either naming should work during the transition.
    if plan not in ("free", "pro", "premium", "core", "operator", "superadmin"):
        raise HTTPException(status_code=400, detail="Invalid plan")

    db = get_database()
    with db.session_scope() as session:
        usage = session.query(UserUsageDB).filter_by(user_id=user_id).first()
        old_plan = usage.plan if usage else None
        if usage:
            usage.plan = plan
            usage.updated_at = datetime.utcnow()
        else:
            session.add(UserUsageDB(
                user_id=user_id, plan=plan,
                tokens_used=0, period_start=datetime.utcnow()
            ))
        _write_tier_audit(session, user_id, old_plan, plan, "recovery_endpoint", "set_plan")
    logger.info("plan_set_manually", user_id=user_id, plan=plan)
    return {"status": "ok", "plan": plan}

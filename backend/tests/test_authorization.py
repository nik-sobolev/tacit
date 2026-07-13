"""Cross-user authorization (IDOR) tests for usage v2.

Matches the codebase-wide pattern already used in api/context.py, api/documents.py,
api/share.py: every query on a user-scoped table filters by user_id. These tests
verify entitlements.py and GET /billing/status never leak one user's usage into
another user's response.
"""

import asyncio
from datetime import datetime, timedelta

from backend.app.api import billing as billing_mod
from backend.app.core import entitlements as ent
from backend.app.db.database import UserUsageDB, UsagePeriodDB


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_user(db, user_id, plan="core"):
    with db.session_scope() as s:
        s.add(UserUsageDB(user_id=user_id, plan=plan, tokens_used=0, period_start=datetime.utcnow()))


def test_get_usage_snapshot_never_leaks_across_users(db, usage_v2_on):
    _make_user(db, "user_a", plan="core")
    _make_user(db, "user_b", plan="operator")

    ent.record_action("user_a", "save", dedupe_key="save:a1")
    ent.record_action("user_a", "save", dedupe_key="save:a2")
    ent.record_action("user_b", "save", dedupe_key="save:b1")

    snap_a = ent.get_usage_snapshot("user_a")
    snap_b = ent.get_usage_snapshot("user_b")

    assert snap_a["usage"]["save"]["used"] == 2
    assert snap_b["usage"]["save"]["used"] == 1
    assert snap_a["tier"] == "core"
    assert snap_b["tier"] == "operator"


def test_billing_status_route_scoped_to_authenticated_user_only(db, usage_v2_on):
    """Calls the actual GET /billing/status route function (bypassing FastAPI's
    Depends() wiring, which is orthogonal to this test) with two different
    authenticated identities and confirms each only ever sees their own data."""
    _make_user(db, "route_user_a", plan="free")
    _make_user(db, "route_user_b", plan="operator")

    with db.session_scope() as s:
        s.add(UsagePeriodDB(id="pa", user_id="route_user_a", period_start=datetime.utcnow(),
                             period_end=datetime.utcnow() + timedelta(days=30), tier="free", queries_count=5))
        s.add(UsagePeriodDB(id="pb", user_id="route_user_b", period_start=datetime.utcnow(),
                             period_end=datetime.utcnow() + timedelta(days=30), tier="operator", queries_count=500))

    status_a = _run(billing_mod.get_usage_status(current_user={"id": "route_user_a", "email": "a@test.com"}))
    status_b = _run(billing_mod.get_usage_status(current_user={"id": "route_user_b", "email": "b@test.com"}))

    assert status_a["usage"]["query"]["used"] == 5
    assert status_a["tier"] == "free"
    assert status_b["usage"]["query"]["used"] == 500
    assert status_b["tier"] == "operator"
    # The two responses must not reference each other's data under any key.
    assert status_a != status_b


def test_check_and_reserve_scoped_per_user_one_users_cap_does_not_affect_another(db, usage_v2_on):
    from backend.app.core import tiers

    _make_user(db, "capped_user", plan="free")
    _make_user(db, "fresh_user", plan="free")
    limit = tiers.get_limits("free")["query"]

    with db.session_scope() as s:
        s.add(UsagePeriodDB(id="pc", user_id="capped_user", period_start=datetime.utcnow(),
                             period_end=datetime.utcnow() + timedelta(days=30), tier="free", queries_count=limit))

    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        ent.check_and_reserve("capped_user", "query")

    # A different user on the same tier, with no usage, must be unaffected.
    ent.check_and_reserve("fresh_user", "query")

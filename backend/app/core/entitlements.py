"""Usage v2 — human-unit entitlement enforcement.

Replaces core/usage.py as the enforcement surface for the new tiered system
(Free/Core/Operator, metered in saves/queries/synthesis/digests/agent_runs
instead of tokens). usage.py is left in place, untouched, and keeps running
through the shadow-run + cutover period (see FEATURE_USAGE_V2 in api/features.py)
— it remains the rollback target if this system needs to be disabled.

FAIL-SAFE POLICY (deliberate, not accidental):
  - check_and_reserve() on a non-expensive category (currently: "save"): a DB
    error logs and returns — fail OPEN. Paying users are never blocked by our
    own infrastructure being down, and saving/capturing content is cheap.
  - check_and_reserve() on an EXPENSIVE category (query/synthesis/digest/
    agent_run): a DB error logs and raises 503 — fail CLOSED. We cannot verify
    entitlement, so we refuse the expensive action rather than guess and risk
    unbounded LLM cost during an outage.
  - record_action(): a DB error logs and is swallowed — never raises. The LLM
    call (if any) already happened and was already paid for; losing the count
    is a monitoring gap, not a safety issue. Mirrors usage.py's record_usage()
    behavior, but now stated explicitly instead of being an accident of a
    blanket try/except.
"""

import os
from datetime import datetime, timedelta
from uuid import uuid4

import structlog
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from . import tiers
from .usage import SUPERADMIN_EMAILS, SUPERADMIN_USER_IDS
from ..db.database import get_database, UserUsageDB, UsagePeriodDB, UsageEventDB, UsageAuditLogDB

logger = structlog.get_logger()


def _usage_v2_enforcing() -> bool:
    """Whether this module is allowed to actually block a request. Read fresh on
    every call (not cached at import time) so tests and ops can flip the env var
    without a process restart. See api/features.py for the matching frontend flag."""
    return os.getenv("FEATURE_USAGE_V2", "false").lower() == "true"

# Default period length for users with no Stripe subscription to anchor to (free
# tier) or on first-ever period creation before any webhook has landed. Paid tiers
# get corrected to the real Stripe boundary as soon as a checkout.session.completed
# or customer.subscription.updated webhook arrives (see api/billing.py).
_DEFAULT_PERIOD_DAYS = 30


def _is_superadmin(user_id: str, email: str = None) -> bool:
    if user_id in SUPERADMIN_USER_IDS:
        return True
    if email and email.lower() in SUPERADMIN_EMAILS:
        return True
    return False


def _resolve_tier(session, user_id: str) -> str:
    """Server-authoritative tier lookup — always the DB row written by the Stripe
    webhook (or recovery endpoint), never anything client-supplied."""
    usage = session.query(UserUsageDB).filter_by(user_id=user_id).first()
    if not usage:
        return "free"
    return tiers.normalize_tier(usage.plan)


def _get_or_create_current_period(session, user_id: str, tier: str) -> UsagePeriodDB:
    """Get the active billing-cycle period row for user_id, creating the next one
    if the most recent has expired. Self-healing: does not depend solely on a
    webhook having just fired — see module docstring in api/billing.py's webhook
    handler for how real Stripe period boundaries correct this over time."""
    now = datetime.utcnow()
    period = (
        session.query(UsagePeriodDB)
        .filter_by(user_id=user_id)
        .order_by(UsagePeriodDB.period_start.desc())
        .first()
    )
    if period and period.period_end > now:
        if period.tier != tier:
            # Mid-cycle tier change (e.g. webhook-driven upgrade) should raise the
            # user's limits immediately — counts are untouched, only the tier label.
            period.tier = tier
        return period

    if period:
        # Roll forward by the same duration as the just-expired period, preserving
        # the subscription's billing anchor day, until a real webhook corrects it.
        duration = period.period_end - period.period_start
        new_start = period.period_end
        new_end = new_start + duration
    else:
        # Truncate to the start of the UTC day rather than using the raw microsecond
        # timestamp. This is deliberate, not cosmetic: two concurrent requests both
        # creating a user's first-ever period must compute the IDENTICAL period_start
        # for the (user_id, period_start) unique constraint to actually collide and
        # dedupe them via the IntegrityError-and-re-query path below — with a raw
        # `datetime.utcnow()`, concurrent callers get microsecond-different values
        # that never collide, silently creating multiple period rows and splitting
        # the counters across them (caught by a concurrency smoke test).
        new_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        new_end = new_start + timedelta(days=_DEFAULT_PERIOD_DAYS)

    new_period = UsagePeriodDB(
        id=str(uuid4()), user_id=user_id, period_start=new_start, period_end=new_end, tier=tier,
    )
    session.add(new_period)
    try:
        session.flush()
        return new_period
    except IntegrityError:
        session.rollback()
        # Lost a race with a concurrent request creating the same period — use theirs.
        existing = (
            session.query(UsagePeriodDB)
            .filter_by(user_id=user_id, period_start=new_start)
            .first()
        )
        if existing:
            return existing
        raise


def _already_logged_this_period(session, user_id: str, period_id: str, event_type: str, category: str) -> bool:
    return (
        session.query(UsageAuditLogDB)
        .filter_by(user_id=user_id, event_type=event_type, to_value=category)
        .filter(UsageAuditLogDB.created_at >= _period_created_watermark(session, period_id))
        .first()
        is not None
    )


def _period_created_watermark(session, period_id: str) -> datetime:
    period = session.query(UsagePeriodDB).filter_by(id=period_id).first()
    return period.created_at if period else datetime.min


def get_usage_snapshot(user_id: str) -> dict:
    """Read-only usage summary for the current billing period, for GET /billing/status.
    No tokens or cost fields — those are server-side-only (see module docstring in
    api/billing.py's response-shape comment). This is a read, so it follows the same
    fail-open policy as check_and_reserve()'s non-expensive path: a DB error returns
    an empty/zeroed snapshot rather than 500ing the user's usage meter."""
    try:
        db = get_database()
        with db.session_scope() as session:
            tier = _resolve_tier(session, user_id)
            period = _get_or_create_current_period(session, user_id, tier)
            limits = tiers.get_limits(tier)
            usage = {}
            for category, field in tiers.CATEGORY_TO_COUNTER_FIELD.items():
                count = getattr(period, field)
                limit = limits.get(category, 0)
                pct = round((count / limit * 100), 1) if limit else 0.0
                usage[category] = {
                    "used": count, "limit": limit, "pct": pct,
                    "warn": pct >= tiers.WARN_THRESHOLD_PCT,
                }
            return {
                "tier": tier,
                "tier_label": tiers.get_label(tier),
                "period_end": period.period_end.isoformat(),
                "usage": usage,
            }
    except Exception as e:
        logger.error("get_usage_snapshot_failed", user_id=user_id, error=str(e))
        return {"tier": "free", "tier_label": "Free", "period_end": None, "usage": {}}


def check_and_reserve(user_id: str, category: str, email: str = None) -> None:
    """Server-authoritative pre-check for an action about to happen. Raises
    HTTPException(402) if hard-capped on an EXPENSIVE category. Never raises for
    non-expensive categories (currently just "save").

    This is a point-in-time reservation, not re-validated after the action
    completes — record_action() always proceeds once this has passed, even if
    the count changes concurrently in the meantime. This is what makes "never
    hard-cut a user mid-work" structural rather than a special case.
    """
    if _is_superadmin(user_id, email):
        return

    is_expensive = category in tiers.EXPENSIVE_CATEGORIES

    try:
        db = get_database()
        with db.session_scope() as session:
            tier = _resolve_tier(session, user_id)
            if tier == "superadmin":
                return
            period = _get_or_create_current_period(session, user_id, tier)
            field = tiers.CATEGORY_TO_COUNTER_FIELD[category]
            count = getattr(period, field)
            limit = tiers.get_limits(tier).get(category, 0)
            pct = (count / limit) if limit else 0

            if is_expensive and count >= limit:
                if not _already_logged_this_period(session, user_id, period.id, "cap_hit", category):
                    session.add(UsageAuditLogDB(
                        id=str(uuid4()), user_id=user_id, event_type="cap_hit",
                        from_value=None, to_value=category, source="enforcement",
                        detail=f"{count}/{limit} {category} this period"
                        + ("" if _usage_v2_enforcing() else " (shadow mode — not blocked)"),
                    ))
                # Shadow mode (FEATURE_USAGE_V2 off): record that this WOULD have
                # blocked (the audit row above) but let usage.py's older token-based
                # check_limit() remain the sole real enforcer, per the migration plan.
                if _usage_v2_enforcing():
                    raise HTTPException(
                        status_code=402,
                        detail={"category": category, "reason": "cap_reached"},
                    )

            if pct >= tiers.WARN_THRESHOLD_PCT / 100:
                if not _already_logged_this_period(session, user_id, period.id, "cap_warned", category):
                    session.add(UsageAuditLogDB(
                        id=str(uuid4()), user_id=user_id, event_type="cap_warned",
                        from_value=None, to_value=category, source="enforcement",
                        detail=f"{count}/{limit} {category} this period",
                    ))
    except HTTPException:
        raise
    except Exception as e:
        if is_expensive and _usage_v2_enforcing():
            logger.error("entitlement_check_failed_expensive", user_id=user_id, category=category, error=str(e))
            raise HTTPException(status_code=503, detail="Usage service unavailable — please try again shortly")
        # Shadow mode, or a non-expensive category: this system isn't the real
        # enforcer (or never blocks this category) — log and move on, usage.py's
        # older check_limit() (still called at every existing chat call site)
        # remains the actual gate until FEATURE_USAGE_V2 is on.
        logger.error("entitlement_check_failed_non_expensive", user_id=user_id, category=category, error=str(e))
        return


def record_action(
    user_id: str,
    category: str,
    dedupe_key: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_cents: int = 0,
) -> None:
    """Idempotent, atomic increment. Safe to call repeatedly with the same
    dedupe_key — a no-op after the first successful call for that key."""
    try:
        db = get_database()

        with db.session_scope() as session:
            tier = _resolve_tier(session, user_id)
            period = _get_or_create_current_period(session, user_id, tier)
            period_id = period.id  # extract before the session closes — avoid touching a detached instance

        field = tiers.CATEGORY_TO_COUNTER_FIELD[category]

        with db.session_scope() as session:
            existing = session.query(UsageEventDB).filter_by(user_id=user_id, dedupe_key=dedupe_key).first()
            if existing:
                return  # already recorded — idempotent no-op

            session.execute(
                update(UsagePeriodDB)
                .where(UsagePeriodDB.id == period_id)
                .values(**{field: getattr(UsagePeriodDB, field) + 1},
                        estimated_cost_cents=UsagePeriodDB.estimated_cost_cents + cost_cents,
                        updated_at=datetime.utcnow())
            )
            session.add(UsageEventDB(
                id=str(uuid4()), user_id=user_id, category=category, dedupe_key=dedupe_key,
                period_id=period_id, tokens_input=input_tokens, tokens_output=output_tokens,
                cost_cents=cost_cents,
            ))
    except IntegrityError:
        # Lost a race with a concurrent call using the same dedupe_key — the other
        # call's increment already landed, so this one is correctly a no-op.
        logger.info("usage_event_dedupe_race", user_id=user_id, category=category, dedupe_key=dedupe_key)
    except Exception as e:
        logger.error("record_action_failed", user_id=user_id, category=category, error=str(e))
        # Never raise — the user's request already succeeded; losing the count is
        # a monitoring gap, not a safety issue. See module docstring.

"""Token usage tracking and limits"""

from datetime import datetime, timedelta
from fastapi import HTTPException
import structlog

from ..db.database import get_database, UserUsageDB

logger = structlog.get_logger()

LIMITS = {"free": 100_000, "pro": 2_000_000}


def check_limit(user_id: str) -> None:
    """Check if user is over their monthly token limit. Raises HTTPException(402) if over."""
    db = get_database()

    with db.session_scope() as session:
        usage = session.query(UserUsageDB).filter_by(user_id=user_id).first()

        # Create row if missing — new users default to free plan
        if not usage:
            usage = UserUsageDB(user_id=user_id, plan="free", tokens_used=0, period_start=datetime.utcnow())
            session.add(usage)
            session.commit()
            return

        # Reset if new billing period (month boundary)
        now = datetime.utcnow()
        if usage.period_start.month != now.month or usage.period_start.year != now.year:
            usage.tokens_used = 0
            usage.period_start = now
            session.commit()
            return

        # Check against limit
        limit = LIMITS.get(usage.plan, LIMITS["free"])
        if usage.tokens_used >= limit:
            raise HTTPException(
                status_code=402,
                detail=f"Token limit reached ({usage.tokens_used}/{limit}). Upgrade to Pro for $9/mo."
            )


def record_usage(user_id: str, input_tokens: int, output_tokens: int) -> None:
    """Record token usage for a user. Non-blocking — failures are logged but don't crash."""
    try:
        db = get_database()
        total_tokens = input_tokens + output_tokens

        with db.session_scope() as session:
            usage = session.query(UserUsageDB).filter_by(user_id=user_id).first()

            if not usage:
                usage = UserUsageDB(
                    user_id=user_id,
                    plan="free",
                    tokens_used=total_tokens,
                    period_start=datetime.utcnow()
                )
                session.add(usage)
            else:
                # Reset if new billing period
                now = datetime.utcnow()
                if usage.period_start.month != now.month or usage.period_start.year != now.year:
                    usage.tokens_used = total_tokens
                    usage.period_start = now
                else:
                    usage.tokens_used += total_tokens

            usage.updated_at = datetime.utcnow()
            session.commit()

            logger.info(
                "usage_recorded",
                user_id=user_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cumulative=usage.tokens_used,
                plan=usage.plan
            )
    except Exception as e:
        logger.error("record_usage_failed", user_id=user_id, error=str(e))
        # Don't raise — usage recording failure should not break the user's request

"""Billing API — usage status, Stripe integration, webhooks"""

import os
import json
import structlog
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, Depends
import stripe

from ..core.auth import get_current_user
from ..db.database import get_database, UserUsageDB

logger = structlog.get_logger()
router = APIRouter()

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PRO_PRICE_ID = os.getenv("STRIPE_PRO_PRICE_ID")

LIMITS = {"free": 100_000, "pro": 2_000_000}


@router.get("/billing/status")
async def get_usage_status(current_user: dict = Depends(get_current_user)):
    """Get user's current usage and plan"""
    db = get_database()

    with db.session_scope() as session:
        usage = session.query(UserUsageDB).filter_by(user_id=current_user["id"]).first()

        if not usage:
            # New user defaults to free plan
            return {
                "plan": "free",
                "tokens_used": 0,
                "tokens_limit": LIMITS["free"],
                "pct_used": 0.0,
                "period_start": datetime.utcnow().isoformat(),
            }

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


@router.post("/billing/checkout")
async def create_checkout_session(current_user: dict = Depends(get_current_user)):
    """Create Stripe checkout session for Pro plan"""
    if not STRIPE_PRO_PRICE_ID:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price": STRIPE_PRO_PRICE_ID,
                    "quantity": 1,
                }
            ],
            mode="subscription",
            success_url="https://www.trytacit.app?billing=success",
            cancel_url="https://www.trytacit.app",
            customer_email=current_user.get("email"),
            metadata={
                "user_id": current_user["id"],
            },
        )
        return {"url": session.url}
    except Exception as e:
        logger.error("stripe_checkout_error", error=str(e))
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
                return_url="https://www.trytacit.app",
            )
            return {"url": portal_session.url}
        except Exception as e:
            logger.error("stripe_portal_error", error=str(e))
            raise HTTPException(status_code=500, detail="Failed to create portal session")


@router.post("/billing/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events (no Clerk auth)"""
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

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session["metadata"].get("user_id")
        customer_id = session["customer"]

        if user_id and customer_id:
            with db.session_scope() as db_session:
                usage = db_session.query(UserUsageDB).filter_by(user_id=user_id).first()
                if usage:
                    usage.plan = "pro"
                    usage.stripe_customer_id = customer_id
                    usage.stripe_subscription_id = session.get("subscription")
                    usage.updated_at = datetime.utcnow()
                    db_session.commit()
                    logger.info("subscription_created", user_id=user_id, plan="pro")

    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription["customer"]

        # Find user by stripe customer ID and downgrade to free
        with db.session_scope() as db_session:
            usage = db_session.query(UserUsageDB).filter_by(stripe_customer_id=customer_id).first()
            if usage:
                usage.plan = "free"
                usage.stripe_subscription_id = None
                usage.updated_at = datetime.utcnow()
                db_session.commit()
                logger.info("subscription_deleted", user_id=usage.user_id, plan="free")

    return {"status": "received"}


@router.post("/billing/set-superadmin/{user_id}")
async def set_superadmin(user_id: str, request: Request):
    """Set a user's plan to superadmin (unlimited). Protected by RECOVERY_KEY."""
    key = request.headers.get("X-Recovery-Key", "")
    expected = os.getenv("RECOVERY_KEY", "emergency-restore-nik")
    if key != expected:
        raise HTTPException(status_code=403, detail="Invalid key")

    db = get_database()
    with db.session_scope() as session:
        usage = session.query(UserUsageDB).filter_by(user_id=user_id).first()
        if usage:
            usage.plan = "superadmin"
            usage.tokens_used = 0
            usage.updated_at = datetime.utcnow()
        else:
            session.add(UserUsageDB(
                user_id=user_id, plan="superadmin",
                tokens_used=0, period_start=datetime.utcnow()
            ))
        session.commit()
    logger.info("superadmin_set", user_id=user_id)
    return {"status": "ok", "plan": "superadmin"}

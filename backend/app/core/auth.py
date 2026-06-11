"""Clerk JWT authentication middleware"""

import os
import jwt
import httpx
import structlog
from fastapi import Depends, HTTPException, Request
from functools import lru_cache

logger = structlog.get_logger()

CLERK_FRONTEND_API = os.getenv(
    "CLERK_FRONTEND_API",
    "https://clerk.trytacit.app"
)
JWKS_URL = f"{CLERK_FRONTEND_API}/.well-known/jwks.json"


@lru_cache(maxsize=1)
def _get_jwks() -> dict:
    """Fetch and cache Clerk JWKS public keys."""
    try:
        resp = httpx.get(JWKS_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("jwks_fetch_failed", error=str(e))
        return {"keys": []}


def _verify_token(token: str) -> dict:
    """Verify a Clerk JWT and return the payload."""
    jwks = _get_jwks()
    public_keys = jwt.PyJWKClient(JWKS_URL)
    signing_key = public_keys.get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        options={"verify_aud": False},
    )
    return payload


async def get_current_user(request: Request) -> dict:
    """Extract and verify Clerk JWT from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = auth[7:]
    try:
        payload = _verify_token(token)
        # Clerk puts email in `email` or inside `email_addresses[0].email_address`
        email = payload.get("email", "")
        if not email:
            addrs = payload.get("email_addresses", [])
            if addrs and isinstance(addrs, list):
                email = addrs[0].get("email_address", "") if isinstance(addrs[0], dict) else ""
        return {
            "id": payload["sub"],
            "email": email,
        }
    except Exception as e:
        logger.warning("auth_failed", error=str(e))
        raise HTTPException(status_code=401, detail="Invalid token")


# FastAPI dependency
CurrentUser = Depends(get_current_user)

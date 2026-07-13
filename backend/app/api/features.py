"""Feature flags API — control Notes and People sections"""

import os
from fastapi import APIRouter

router = APIRouter()


def get_flags() -> dict:
    """Get feature flags from environment variables."""
    return {
        "notes_enabled": os.getenv("FEATURE_NOTES", "false").lower() == "true",
        "people_enabled": os.getenv("FEATURE_PEOPLE", "false").lower() == "true",
        "usage_v2_enabled": os.getenv("FEATURE_USAGE_V2", "true").lower() == "true",
    }


@router.get("/features")
async def get_feature_flags():
    """Get current feature flag status (public endpoint)."""
    return get_flags()

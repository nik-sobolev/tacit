"""Tier/limit configuration for usage v2 — single source of truth.

Replaces the LIMITS dict duplicated in both core/usage.py and api/billing.py
(token-based, v1). Stripe price IDs are unchanged from v1: STRIPE_PRO_PRICE_ID
still maps to what's now labeled "Core", STRIPE_PREMIUM_PRICE_ID to "Operator" —
no new Stripe Products/Prices were created for this rename.

Limits below are placeholders scaled loosely off v1's token budgets
(free=100k, pro=500k, premium=1M tokens/mo) — tune after review, not final.
"""

import os

TIER_CONFIG = {
    "free": {
        "label": "Free",
        "stripe_price_env": None,
        "limits": {"save": 100, "query": 200, "synthesis": 100, "digest": 30, "agent_run": 20},
    },
    "core": {
        "label": "Core",
        "stripe_price_env": "STRIPE_PRO_PRICE_ID",
        "limits": {"save": 500, "query": 1000, "synthesis": 500, "digest": 150, "agent_run": 100},
    },
    "operator": {
        "label": "Operator",
        "stripe_price_env": "STRIPE_PREMIUM_PRICE_ID",
        "limits": {"save": 1000, "query": 2000, "synthesis": 1000, "digest": 300, "agent_run": 200},
    },
}

# superadmin bypasses all limits — no entry in TIER_CONFIG needed, checked explicitly
# in entitlements.py before any TIER_CONFIG lookup.

WARN_THRESHOLD_PCT = 80

# v1 plan values still live in UserUsageDB.plan for existing rows — resolved here
# rather than bulk-rewritten, so no one-shot data migration is required.
LEGACY_PLAN_ALIAS = {"pro": "core", "premium": "operator", "free": "free", "superadmin": "superadmin"}

# Only these categories are ever hard-gated at 100%. "save" is deliberately excluded —
# saving/capturing content is cheap and never blocks; only the downstream LLM-driven
# actions do. See check_and_reserve() in entitlements.py for how this is enforced.
EXPENSIVE_CATEGORIES = {"query", "synthesis", "digest", "agent_run"}

CATEGORY_TO_COUNTER_FIELD = {
    "save": "saves_count",
    "query": "queries_count",
    "synthesis": "synthesis_count",
    "digest": "digests_count",
    "agent_run": "agent_runs_count",
}


def normalize_tier(plan_or_tier: str) -> str:
    """Resolve a possibly-legacy plan string (v1: 'pro'/'premium') to a v2 tier key."""
    return LEGACY_PLAN_ALIAS.get(plan_or_tier, plan_or_tier)


def get_limits(plan_or_tier: str) -> dict:
    """Per-category limits for a tier, resolving legacy plan names. Falls back to
    free-tier limits for an unrecognized value (fail-safe: never grant unlimited
    usage for a tier we don't recognize)."""
    tier = normalize_tier(plan_or_tier)
    config = TIER_CONFIG.get(tier, TIER_CONFIG["free"])
    return config["limits"]


def get_label(plan_or_tier: str) -> str:
    tier = normalize_tier(plan_or_tier)
    config = TIER_CONFIG.get(tier)
    return config["label"] if config else "Free"


def price_id_to_tier(price_id: str) -> str:
    """Map a Stripe price ID to a tier key. Falls back to 'free' for an unrecognized
    price ID (fail-safe — never grant a paid tier for a price we don't recognize)."""
    for tier, config in TIER_CONFIG.items():
        env_name = config["stripe_price_env"]
        if env_name and price_id and price_id == os.getenv(env_name):
            return tier
    return "free"

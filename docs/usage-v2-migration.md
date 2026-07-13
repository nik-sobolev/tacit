# Usage v2 migration and rollback

Rebuild of usage tracking and pricing: token metering (Free/Pro/Premium) → tiered
human-unit entitlements (Free/Core/Operator, metered in saves/queries/synthesis/
digests/agent_runs). Full design in the approved plan; this note covers how to
actually flip it on safely and how to back out if something's wrong.

## What shipped, and what's still off by default

Everything in this change is additive and gated behind `FEATURE_USAGE_V2`
(env var, default unset/`false`). With the flag off:

- Four new tables exist (`usage_periods`, `usage_events`, `usage_audit_log`,
  `stripe_webhook_events`) but nothing reads from them for enforcement.
- Every save/query/synthesis call site now **also** calls the new
  `core/entitlements.py` functions, but in shadow mode: counts are recorded into
  the new tables and audit rows are written for what *would* have warned/blocked,
  but nothing is ever rejected because of them. `core/usage.py`'s original
  token-based `check_limit()`/`record_usage()` calls are untouched and remain the
  only thing that can actually 402 a user.
- The Stripe webhook handler's new event-ID dedup, `customer.subscription.updated`
  handling, and price-ID-derived tier resolution are **live regardless of the
  flag** — these are bug fixes to existing behavior (a portal-driven upgrade
  previously never reached the DB at all), not new-system behavior, so there's no
  reason to gate them.
- `GET /billing/status` and the frontend usage meter/402 modal still show the old
  token-based shape until the flag is on.

Nothing here changes what a real user experiences until `FEATURE_USAGE_V2=true`
is actually set.

## Rollout sequence

1. **Deploy with the flag off (default).** This is safe to ship any time — it's
   schema creation plus shadow-writes, with zero behavior change. Confirm on
   startup logs that no errors come from the new tables/migration code.

2. **Watch the shadow period.** With the flag off, every gated action is still
   double-recorded into `usage_periods`/`usage_events`. Before flipping the flag:
   - Compare `usage_periods` growth against `user_usage.tokens_used` trends for
     the same users — they should move together directionally (more usage in one
     should track more usage in the other), even though the units differ.
   - Check logs for `entitlement_check_failed_*` or `record_action_failed` —
     these indicate the new code path is erroring, which should be root-caused
     before cutover even though shadow mode means it can't affect real users yet.
   - Check `usage_audit_log` for `cap_hit`/`cap_warned` rows with
     `detail` containing "(shadow mode — not blocked)" — this tells you how many
     real users would start seeing warnings/blocks under the new limits, useful
     for sanity-checking the placeholder tier limits in `core/tiers.py` before
     they go live (they're explicitly placeholders, scaled loosely off the old
     token budgets — tune them based on what shadow mode shows).

3. **Run the backfill and confirm it's clean.** Set `FEATURE_USAGE_V2=true` in
   one environment (staging first, ideally). On startup, `main.py`'s
   `_migrate_backfill_tier_from_stripe()` runs automatically and reconciles every
   `user_usage` row that has a `stripe_subscription_id` against live Stripe state,
   correcting any drift and writing a `tier_reconciled` audit row per correction.
   Check the `backfill_tier_corrections_applied` log line's count — a large
   unexpected number of corrections means webhooks were being missed before this
   change and is worth understanding before trusting the tier data for
   enforcement. This function re-runs (idempotently, cheaply) on every startup
   while the flag is on, so it also functions as ongoing drift correction, not
   just a one-time backfill.

4. **Flip the flag in production.** Once staging looks clean, set
   `FEATURE_USAGE_V2=true` in production. From this point:
   - `core/entitlements.py` becomes the real enforcer for save/query/synthesis
     actions. Saves never hard-block. Query/synthesis/digest/agent_run block at
     100% of the tier's per-category limit, with an audit-logged warning at 80%.
   - `GET /billing/status` switches to the new per-category response shape (no
     tokens/cost fields). The frontend's `usage_v2_enabled` flag (served from
     `GET /api/features`) picks this up automatically — no separate frontend
     deploy/flag needed, it reads the same backend flag.
   - The old `core/usage.py` calls **keep running** — they were never removed,
     just made redundant. `user_usage.tokens_used` keeps incrementing and stays
     available as a secondary signal.

5. **Burn-in period, then optional cleanup.** After the flag has been on and
   stable for a period you're comfortable with, a follow-up change can remove the
   now-redundant `core/usage.py` call sites. Not required — leaving them running
   costs nothing and preserves the rollback path below for as long as you want it.

## Rollback

Set `FEATURE_USAGE_V2=false` (or unset it) and redeploy/restart.

Because the old `core/usage.py` calls were never removed, they've been running
continuously the entire time the flag was on — flipping back doesn't "restart" the
old system, it just makes it the sole enforcer again immediately, with its state
(`user_usage.tokens_used`) fully intact and never paused.

No data is lost in either direction:
- Turning the flag off does not drop or touch `usage_periods`/`usage_events`/
  `usage_audit_log`/`stripe_webhook_events` — they just stop being read for
  enforcement and go back to shadow-recording. Re-enabling later picks up
  exactly where they left off.
- The webhook fixes (event dedup, `subscription.updated` handling, price-ID-
  derived tier) are not part of the flag and are not rolled back by this —
  they're correctness fixes to the existing tier-sync mechanism regardless of
  which system is enforcing usage.

**What to check after a rollback:** confirm `GET /api/features` reports
`usage_v2_enabled: false` and that `GET /billing/status` is back to the
token-based response shape (`tokens_used`/`tokens_limit`/`pct_used` fields, no
`usage`/`tier` object). The frontend picks this up on its next `loadFeatureFlags()`
call (page load) with no separate deploy needed.

## What to watch for specifically

- **Synthesis-capped saves.** When a user hits the synthesis cap, `POST /api/ingest`
  now still succeeds but sets the node's status to `saved_no_synthesis` instead of
  scheduling AI enrichment. If anything downstream assumes every non-`done`,
  non-`error` node is actively `processing` (polling logic, stuck-node cleanup,
  etc.), it should be checked against this new status value.
- **`billing.py`'s recovery endpoints** (`/billing/set-plan/{user_id}/{plan}`,
  `/billing/set-superadmin/{user_id}`) now accept both legacy (`pro`/`premium`)
  and v2 (`core`/`operator`) plan names, and write an audit row on every use —
  no behavior change to the `X-Recovery-Key` auth itself.
- **Placeholder tier limits.** The numbers in `core/tiers.py`'s `TIER_CONFIG` are
  explicitly placeholders scaled loosely off the old token budgets. Tune them
  based on what the shadow period showed before (or shortly after) cutover.

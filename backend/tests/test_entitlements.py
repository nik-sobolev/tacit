"""Tests for core/entitlements.py — atomic counters, idempotency, cap enforcement,
billing-cycle reset, and the "never hard-cut mid-work" guarantee.
"""

import concurrent.futures
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.app.db.database import UserUsageDB, UsagePeriodDB, UsageEventDB, UsageAuditLogDB
from backend.app.core import entitlements as ent
from backend.app.core import tiers


def _make_user(db, user_id, plan="core"):
    with db.session_scope() as s:
        s.add(UserUsageDB(user_id=user_id, plan=plan, tokens_used=0, period_start=datetime.utcnow()))


class TestAtomicConcurrency:
    def test_distinct_dedupe_keys_no_lost_increments(self, db, usage_v2_on):
        _make_user(db, "race_user")
        n = 40

        def do_save(i):
            ent.record_action("race_user", "save", dedupe_key=f"save:node-{i}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(do_save, range(n)))

        with db.session_scope() as s:
            rows = s.query(UsagePeriodDB).filter_by(user_id="race_user").count()
            saves_count = s.query(UsagePeriodDB).filter_by(user_id="race_user").first().saves_count
            events = s.query(UsageEventDB).filter_by(user_id="race_user", category="save").count()

        assert rows == 1, "concurrent first-time period creation must collapse to one row"
        assert saves_count == n, "atomic UPDATE must not lose increments under concurrency"
        assert events == n

    def test_old_read_modify_write_pattern_would_lose_increments(self, db):
        """Regression artifact: proves the bug this design avoids. Directly
        reproduces usage.py's `usage.tokens_used += total_tokens` pattern
        (core/usage.py:80) under concurrency and shows it loses writes — the
        exact failure mode record_action()'s atomic UPDATE fixes."""
        _make_user(db, "legacy_race_user")
        with db.session_scope() as s:
            s.add(UsagePeriodDB(id="legacy-period", user_id="legacy_race_user",
                                 period_start=datetime.utcnow(), period_end=datetime.utcnow() + timedelta(days=30),
                                 tier="core", saves_count=0))

        def racy_increment():
            with db.session_scope() as s:
                period = s.query(UsagePeriodDB).filter_by(id="legacy-period").first()
                current = period.saves_count  # read
                import time; time.sleep(0.001)  # widen the race window deterministically
                period.saves_count = current + 1  # modify-write, no atomic SQL increment

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(lambda _: racy_increment(), range(20)))

        with db.session_scope() as s:
            saves_count = s.query(UsagePeriodDB).filter_by(id="legacy-period").first().saves_count
        assert saves_count < 20, (
            "if this assertion fails, SQLite's locking got lucky serializing every "
            "write — the point stands: this pattern has no correctness guarantee, "
            "unlike record_action()'s atomic UPDATE ... SET x = x + 1"
        )

    def test_same_dedupe_key_concurrent_collapses_to_one_increment(self, db, usage_v2_on):
        _make_user(db, "dup_race_user")
        n = 40

        def do_dup_save(_):
            ent.record_action("dup_race_user", "save", dedupe_key="save:same-node")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(do_dup_save, range(n)))

        with db.session_scope() as s:
            saves_count = s.query(UsagePeriodDB).filter_by(user_id="dup_race_user").first().saves_count
            events = s.query(UsageEventDB).filter_by(user_id="dup_race_user", category="save").count()

        assert saves_count == 1
        assert events == 1


class TestIdempotency:
    def test_repeat_dedupe_key_does_not_double_count(self, db, usage_v2_on):
        _make_user(db, "idem_user")
        ent.record_action("idem_user", "query", dedupe_key="query:s1:0")
        ent.record_action("idem_user", "query", dedupe_key="query:s1:0")
        ent.record_action("idem_user", "query", dedupe_key="query:s1:0")

        with db.session_scope() as s:
            queries_count = s.query(UsagePeriodDB).filter_by(user_id="idem_user").first().queries_count
            events = s.query(UsageEventDB).filter_by(user_id="idem_user").count()

        assert queries_count == 1
        assert events == 1

    def test_distinct_dedupe_keys_do_count_separately(self, db, usage_v2_on):
        _make_user(db, "idem_user2")
        for i in range(5):
            ent.record_action("idem_user2", "query", dedupe_key=f"query:s1:{i}")

        with db.session_scope() as s:
            queries_count = s.query(UsagePeriodDB).filter_by(user_id="idem_user2").first().queries_count
        assert queries_count == 5


class TestCapEnforcement:
    def test_below_warn_threshold_is_silent(self, db, usage_v2_on):
        _make_user(db, "u1", plan="core")
        ent.check_and_reserve("u1", "query")  # 0/1000, well under 80%
        with db.session_scope() as s:
            audit = s.query(UsageAuditLogDB).filter_by(user_id="u1").count()
        assert audit == 0

    def test_crossing_80pct_warns_once_not_on_every_call(self, db, usage_v2_on):
        _make_user(db, "u2", plan="core")
        limit = tiers.get_limits("core")["query"]
        with db.session_scope() as s:
            s.add(UsagePeriodDB(id="p2", user_id="u2", period_start=datetime.utcnow(),
                                 period_end=datetime.utcnow() + timedelta(days=30),
                                 tier="core", queries_count=int(limit * 0.85)))

        ent.check_and_reserve("u2", "query")
        ent.check_and_reserve("u2", "query")
        ent.check_and_reserve("u2", "query")

        with db.session_scope() as s:
            warned = s.query(UsageAuditLogDB).filter_by(user_id="u2", event_type="cap_warned").count()
        assert warned == 1

    def test_expensive_category_at_100pct_raises_402(self, db, usage_v2_on):
        _make_user(db, "u3", plan="free")
        limit = tiers.get_limits("free")["query"]
        with db.session_scope() as s:
            s.add(UsagePeriodDB(id="p3", user_id="u3", period_start=datetime.utcnow(),
                                 period_end=datetime.utcnow() + timedelta(days=30),
                                 tier="free", queries_count=limit))

        with pytest.raises(HTTPException) as exc_info:
            ent.check_and_reserve("u3", "query")
        assert exc_info.value.status_code == 402
        assert exc_info.value.detail["category"] == "query"

    def test_save_never_hard_gated_even_far_over_limit(self, db, usage_v2_on):
        _make_user(db, "u4", plan="free")
        with db.session_scope() as s:
            s.add(UsagePeriodDB(id="p4", user_id="u4", period_start=datetime.utcnow(),
                                 period_end=datetime.utcnow() + timedelta(days=30),
                                 tier="free", saves_count=999_999))
        ent.check_and_reserve("u4", "save")  # must not raise

    def test_shadow_mode_never_raises_even_over_cap(self, db, usage_v2_off):
        """FEATURE_USAGE_V2 off — usage.py remains sole enforcer, entitlements.py
        must not block anything, even a category it considers expensive."""
        _make_user(db, "u5", plan="free")
        limit = tiers.get_limits("free")["query"]
        with db.session_scope() as s:
            s.add(UsagePeriodDB(id="p5", user_id="u5", period_start=datetime.utcnow(),
                                 period_end=datetime.utcnow() + timedelta(days=30),
                                 tier="free", queries_count=limit * 10))
        ent.check_and_reserve("u5", "query")  # must not raise

        with db.session_scope() as s:
            hit = s.query(UsageAuditLogDB).filter_by(user_id="u5", event_type="cap_hit").count()
        assert hit == 1, "shadow mode should still log what WOULD have blocked, for validation"

    def test_superadmin_bypasses_cap(self, db, usage_v2_on):
        ent.check_and_reserve("user_3EVAoYRU4XFtkVMgBhvdoFV3xOd", "query")  # hardcoded superadmin ID

    def test_read_stays_available_even_when_hard_capped(self, db, usage_v2_on):
        """GET /billing/status is a read — get_usage_snapshot() must succeed and
        report accurate over-100% numbers even for a user who is hard-capped."""
        _make_user(db, "u6", plan="free")
        limit = tiers.get_limits("free")["query"]
        with db.session_scope() as s:
            s.add(UsagePeriodDB(id="p6", user_id="u6", period_start=datetime.utcnow(),
                                 period_end=datetime.utcnow() + timedelta(days=30),
                                 tier="free", queries_count=limit * 2))
        snapshot = ent.get_usage_snapshot("u6")
        assert snapshot["usage"]["query"]["used"] == limit * 2
        assert snapshot["usage"]["query"]["pct"] >= 100


class TestNeverAbortMidWork:
    def test_check_passing_then_concurrent_push_over_cap_does_not_block_record(self, db, usage_v2_on):
        """check_and_reserve() is a point-in-time reservation — once it passes,
        record_action() for that same action must always proceed, even if the
        count crosses the cap from a different concurrent request in between."""
        _make_user(db, "u7", plan="free")
        limit = tiers.get_limits("free")["query"]
        with db.session_scope() as s:
            s.add(UsagePeriodDB(id="p7", user_id="u7", period_start=datetime.utcnow(),
                                 period_end=datetime.utcnow() + timedelta(days=30),
                                 tier="free", queries_count=limit - 1))

        ent.check_and_reserve("u7", "query")  # passes at (limit-1)/limit

        # Simulate a concurrent action pushing the count to/over the cap before
        # THIS action's record_action() call runs.
        ent.record_action("u7", "query", dedupe_key="query:concurrent-other")

        # The original action's record must still land — never re-validated.
        ent.record_action("u7", "query", dedupe_key="query:original")

        with db.session_scope() as s:
            queries_count = s.query(UsagePeriodDB).filter_by(id="p7").first().queries_count
        assert queries_count == limit + 1


class TestBillingCycleReset:
    def test_expired_period_creates_fresh_period_old_untouched(self, db, usage_v2_on):
        _make_user(db, "cycle_user", plan="core")
        old_start = datetime.utcnow() - timedelta(days=40)
        old_end = old_start + timedelta(days=30)
        with db.session_scope() as s:
            s.add(UsagePeriodDB(id="old-period", user_id="cycle_user", period_start=old_start,
                                 period_end=old_end, tier="core", saves_count=250))

        ent.record_action("cycle_user", "save", dedupe_key="save:post-rollover")

        with db.session_scope() as s:
            rows = s.query(UsagePeriodDB).filter_by(user_id="cycle_user").order_by(UsagePeriodDB.period_start).all()
            counts = [r.saves_count for r in rows]
            count_total = len(rows)

        assert count_total == 2
        assert counts[0] == 250, "expired period's historical counts must not be touched"
        assert counts[1] == 1, "new period must start fresh"

    def test_mid_cycle_tier_change_updates_same_period_not_a_new_one(self, db, usage_v2_on):
        _make_user(db, "tier_switch_user", plan="free")
        ent.record_action("tier_switch_user", "save", dedupe_key="save:a")

        with db.session_scope() as s:
            u = s.query(UserUsageDB).filter_by(user_id="tier_switch_user").first()
            u.plan = "operator"

        ent.record_action("tier_switch_user", "save", dedupe_key="save:b")

        with db.session_scope() as s:
            rows = s.query(UsagePeriodDB).filter_by(user_id="tier_switch_user").all()
            row_count = len(rows)
            tier = rows[0].tier if rows else None
            saves_count = rows[0].saves_count if rows else None

        assert row_count == 1, "a tier change mid-cycle must not create a new period"
        assert tier == "operator"
        assert saves_count == 2, "counts must be preserved across the tier change"

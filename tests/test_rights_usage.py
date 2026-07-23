"""
测试：权利使用记录与配额管理
"""

import pytest
from datetime import datetime, timezone

from core.rights_usage import RightsUsage


class TestRightsUsage:

    def test_init_empty(self):
        ru = RightsUsage()
        assert ru.total_usage_count == 0
        assert ru.summary() == {}

    def test_check_available_default(self):
        """默认情况下每种权利都可用"""
        ru = RightsUsage()
        for rtype in ["edit_create", "edit_revise", "suggest", "review"]:
            assert ru.check_available("session-1", rtype) is True

    def test_use_right_success(self):
        ru = RightsUsage()
        result = ru.use_right("session-1", "127.0.0.1",
                              "edit_create", "mon-001")
        assert result is True
        assert ru.total_usage_count == 1

    def test_use_right_unknown_type(self):
        ru = RightsUsage()
        result = ru.use_right("session-1", "127.0.0.1",
                              "unknown_type", "mon-001")
        assert result is False

    def test_check_unknown_type(self):
        ru = RightsUsage()
        assert ru.check_available("session-1", "unknown_type") is False

    def test_edit_create_limit(self):
        """edit_create 每人只能用 1 次"""
        ru = RightsUsage()
        assert ru.use_right("session-1", "127.0.0.1",
                            "edit_create", "mon-001") is True
        assert ru.use_right("session-1", "127.0.0.1",
                            "edit_create", "mon-002") is False
        assert ru.check_available("session-1", "edit_create") is False

    def test_edit_revise_limit(self):
        """edit_revise 每人可用 3 次"""
        ru = RightsUsage()
        session = "session-edit"
        for i in range(1, 4):
            assert ru.use_right(session, "10.0.0.1",
                                "edit_revise", f"mon-{i}") is True
        # 第4次应该失败
        assert ru.use_right(session, "10.0.0.1",
                            "edit_revise", "mon-4") is False

    def test_suggest_limit(self):
        """suggest 每人终身 3 次"""
        ru = RightsUsage()
        session = "session-sug"
        for i in range(1, 4):
            assert ru.use_right(session, "10.0.0.1",
                                "suggest", f"mon-{i}") is True
        assert ru.use_right(session, "10.0.0.1",
                            "suggest", "mon-4") is False

    def test_review_limit_per_session(self):
        """review 每人每轮 3 次"""
        ru = RightsUsage()
        session = "session-rev"
        for i in range(1, 4):
            assert ru.use_right(session, "10.0.0.1",
                                "review", f"mon-{i}") is True
        assert ru.use_right(session, "10.0.0.1",
                            "review", "mon-4") is False

    def test_different_sessions_independent(self):
        """不同 session 的额度互相独立"""
        ru = RightsUsage()
        # session-A 用掉 edit_create
        assert ru.use_right("session-A", "10.0.0.1",
                            "edit_create", "mon-A1") is True
        assert ru.use_right("session-A", "10.0.0.1",
                            "edit_create", "mon-A2") is False
        # session-B 仍然可以用
        assert ru.check_available("session-B", "edit_create") is True
        assert ru.use_right("session-B", "10.0.0.2",
                            "edit_create", "mon-B1") is True

    def test_reset_round_clears_review(self):
        """reset_round 应清空 review 的配额"""
        ru = RightsUsage()
        session = "session-rnd"
        for i in range(1, 4):
            ru.use_right(session, "10.0.0.1", "review", f"mon-{i}")
        assert ru.check_available(session, "review") is False

        # 重置轮次
        count = ru.reset_round(2)
        assert count > 0
        assert ru.check_available(session, "review") is True

    def test_reset_round_does_not_affect_lifetime(self):
        """reset_round 不影响终身型权利"""
        ru = RightsUsage()
        session = "session-mix"
        ru.use_right(session, "10.0.0.1", "edit_create", "mon-1")
        ru.use_right(session, "10.0.0.1", "suggest", "mon-2")

        ru.reset_round(2)
        # edit_create 是终身型，不应被重置 → 仍然不可用（用过了）
        assert ru.check_available(session, "edit_create") is False
        # suggest 是终身型，用了1次还有2次剩余 → 仍然可用
        assert ru.check_available(session, "suggest") is True

    def test_reset_round_same_round_noop(self):
        """重置到相同轮次不应清空"""
        ru = RightsUsage()
        ru.use_right("s1", "10.0.0.1", "review", "mon-1")
        count = ru.reset_round(0)  # current_round 初始为 0
        assert count == 0
        # review 限额 3，用了 1 次，应该仍可用
        assert ru.check_available("s1", "review") is True

    def test_get_usage_history_all(self):
        ru = RightsUsage()
        ru.use_right("s1", "10.0.0.1", "edit_create", "mon-1")
        ru.use_right("s2", "10.0.0.2", "review", "mon-2")
        history = ru.get_usage_history()
        assert len(history) == 2

    def test_get_usage_history_filter_session(self):
        ru = RightsUsage()
        ru.use_right("s1", "10.0.0.1", "edit_create", "mon-1")
        ru.use_right("s2", "10.0.0.2", "review", "mon-2")
        history = ru.get_usage_history(session_id="s1")
        assert len(history) == 1
        assert history[0].session_id == "s1"

    def test_get_usage_history_filter_type(self):
        ru = RightsUsage()
        ru.use_right("s1", "10.0.0.1", "edit_create", "mon-1")
        ru.use_right("s1", "10.0.0.1", "review", "mon-2")
        history = ru.get_usage_history(right_type="review")
        assert len(history) == 1
        assert history[0].right_type == "review"

    def test_summary(self):
        ru = RightsUsage()
        ru.use_right("s1", "10.0.0.1", "edit_create", "mon-1")
        ru.use_right("s1", "10.0.0.1", "review", "mon-2")
        ru.use_right("s2", "10.0.0.2", "suggest", "mon-3")
        summary = ru.summary()
        assert summary["s1"]["edit_create"] == 1
        assert summary["s1"]["review"] == 1
        assert summary["s2"]["suggest"] == 1

    def test_usage_record_has_metadata(self):
        ru = RightsUsage()
        ru.use_right("s1", "10.0.0.1", "review", "mon-001")
        history = ru.get_usage_history(session_id="s1", right_type="review")
        assert len(history) == 1
        rec = history[0]
        assert rec.session_id == "s1"
        assert rec.ip == "10.0.0.1"
        assert rec.right_type == "review"
        assert rec.monument_id == "mon-001"
        assert isinstance(rec.used_at, datetime)

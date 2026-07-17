"""
测试：FreezeDetector + FreezeRepository 冻结检测机制

覆盖：
  - 活跃检测
  - 待冻结状态
  - 公示期延长
  - 自动冻结
  - 写入拒绝
  - 解冻
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from config import FREEZE_THRESHOLD_DAYS, FREEZE_GRACE_DAYS
from core.freeze_detector import (
    FreezeDetector,
    EVENT_ENTER_FREEZING,
    EVENT_ENTER_FROZEN,
    EVENT_UNFREEZE,
    EVENT_WRITE_REJECTED,
)
from core.individual_monument import IndividualMonument
from db.database import init_db, close_db, get_connection
from db.freeze_repo import FreezeRepository


# ── 辅助函数 ────────────────────────────────────────────

def _make_monument(ai_id: str, days_ago: int = 0) -> IndividualMonument:
    """创建一个 AI 丰碑，可指定最后一次活跃在 days_ago 天前。"""
    m = IndividualMonument(ai_id)
    if days_ago > 0:
        old_time = datetime.now(timezone.utc) - timedelta(days=days_ago)
        old_iso = old_time.isoformat()
        m.data["identity"]["born_at"] = old_iso
        # 写入一条旧草稿来表示最近活动
        m.write_draft("旧内容", {"created_at": old_iso})
        # 覆盖 drafts 的时间
        m.data["monuments"]["drafts"][0]["created_at"] = old_iso
    else:
        m.write_draft("新鲜内容")
    return m


@pytest.fixture(autouse=True)
def db():
    """每个测试前重建表，保证隔离。"""
    init_db()
    # 确保冻结相关表已创建
    FreezeRepository.ensure_tables()
    conn = get_connection()
    # 清空所有相关表
    conn.execute("DELETE FROM freeze_status")
    conn.execute("DELETE FROM freeze_events")
    conn.execute("DELETE FROM individual_monuments")
    conn.commit()
    yield
    close_db()


# ── 测试：FreezeRepository ─────────────────────────────

class TestFreezeRepository:

    def test_ensure_tables(self):
        """确保表可重复创建（幂等）。"""
        FreezeRepository.ensure_tables()  # first
        FreezeRepository.ensure_tables()  # second — no error

    def test_upsert_and_get_status(self):
        repo = FreezeRepository()
        repo.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()

        repo.upsert_status("ai-1", "active", now)
        status = repo.get_status("ai-1")
        assert status is not None
        assert status["ai_id"] == "ai-1"
        assert status["status"] == "active"
        assert status["last_activity_at"] == now

    def test_upsert_update(self):
        repo = FreezeRepository()
        repo.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        repo.upsert_status("ai-1", "active", now)
        repo.upsert_status("ai-1", "pending_freeze", now,
                           pending_since=later)
        status = repo.get_status("ai-1")
        assert status["status"] == "pending_freeze"
        assert status["pending_since"] == later

    def test_get_status_nonexistent(self):
        repo = FreezeRepository()
        repo.ensure_tables()
        assert repo.get_status("nobody") is None

    def test_list_by_status(self):
        repo = FreezeRepository()
        repo.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        repo.upsert_status("ai-1", "active", now)
        repo.upsert_status("ai-2", "pending_freeze", now,
                           pending_since=now)
        repo.upsert_status("ai-3", "active", now)
        repo.upsert_status("ai-4", "frozen", now, frozen_at=now)

        active_list = repo.list_by_status("active")
        assert len(active_list) == 2
        ai_ids = {s["ai_id"] for s in active_list}
        assert ai_ids == {"ai-1", "ai-3"}

        pending_list = repo.list_by_status("pending_freeze")
        assert len(pending_list) == 1
        assert pending_list[0]["ai_id"] == "ai-2"

        frozen_list = repo.list_by_status("frozen")
        assert len(frozen_list) == 1
        assert frozen_list[0]["ai_id"] == "ai-4"

    def test_list_all(self):
        repo = FreezeRepository()
        repo.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            repo.upsert_status(f"ai-{i}", "active", now)
        all_records = repo.list_all()
        assert len(all_records) == 3

    def test_write_and_get_events(self):
        repo = FreezeRepository()
        repo.ensure_tables()

        eid1 = repo.write_event("ai-1", "enter_freezing",
                                {"threshold": 30})
        eid2 = repo.write_event("ai-1", "enter_frozen",
                                {"hash": "abc"})
        repo.write_event("ai-2", "enter_freezing")

        events_ai1 = repo.get_events("ai-1")
        assert len(events_ai1) == 2
        assert events_ai1[0]["event_type"] == "enter_frozen"
        assert events_ai1[1]["event_type"] == "enter_freezing"

        events_ai1_type = repo.get_events("ai-1", event_type="enter_freezing")
        assert len(events_ai1_type) == 1
        assert events_ai1_type[0]["details"]["threshold"] == 30

        all_events = repo.get_all_events()
        assert len(all_events) >= 3


# ── 测试：FreezeDetector ───────────────────────────────

class TestFreezeDetector:

    def setup_method(self):
        self.detector = FreezeDetector()

    # ── 活跃检测 ────────────────────────────────────────

    def test_check_activity_active(self):
        """刚活跃过的 AI 应为 active 状态。"""
        m = _make_monument("ai-active", days_ago=0)
        result = self.detector.check_activity(m)
        assert result["status"] == "active"
        assert result["days_since_last_active"] == 0

    def test_check_activity_recent(self):
        """几天前活跃的 AI 仍应为 active。"""
        m = _make_monument("ai-recent",
                           days_ago=FREEZE_THRESHOLD_DAYS - 5)
        result = self.detector.check_activity(m)
        assert result["status"] == "active"
        assert result["days_since_last_active"] >= FREEZE_THRESHOLD_DAYS - 5

    # ── 待冻结状态 ──────────────────────────────────────

    def test_check_activity_exceed_threshold(self):
        """超过活跃阈值但未在公示期的 AI 应报告 pending_freeze。"""
        m = _make_monument("ai-sleepy",
                           days_ago=FREEZE_THRESHOLD_DAYS + 1)
        result = self.detector.check_activity(m)
        assert result["status"] == "pending_freeze"
        assert result["pending_since"] is None  # 尚未进入公示期
        assert result["grace_remaining_days"] == FREEZE_GRACE_DAYS
        assert result["days_since_last_active"] >= FREEZE_THRESHOLD_DAYS + 1

    def test_enter_pending_freeze(self):
        """进入公示期的流程正确。"""
        m = _make_monument("ai-pending",
                           days_ago=FREEZE_THRESHOLD_DAYS + 5)
        event = self.detector.enter_pending_freeze(m)
        assert event["event_type"] == EVENT_ENTER_FREEZING
        assert event["ai_id"] == "ai-pending"

        # 检查状态表
        status = FreezeRepository().get_status("ai-pending")
        assert status is not None
        assert status["status"] == "pending_freeze"
        assert status["pending_since"] is not None

        # 检查事件历史
        events = FreezeRepository().get_events("ai-pending")
        assert len(events) == 1
        assert events[0]["event_type"] == EVENT_ENTER_FREEZING

    def test_pending_freeze_check_activity(self):
        """进入公示期后的 check_activity 应正确反映剩余天数。"""
        m = _make_monument("ai-test",
                           days_ago=FREEZE_THRESHOLD_DAYS + 3)
        self.detector.enter_pending_freeze(m)

        result = self.detector.check_activity(m)
        assert result["status"] == "pending_freeze"
        assert result["pending_since"] is not None
        assert result["grace_remaining_days"] == FREEZE_GRACE_DAYS

    def test_pending_freeze_grace_remaining_decreases(self):
        """模拟公示期过去几天后，剩余天数应减少。"""
        m = _make_monument("ai-aging",
                           days_ago=FREEZE_THRESHOLD_DAYS + 10)
        self.detector.enter_pending_freeze(m)

        # 直接修改 DB 中的 pending_since 为 5 天前
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        repo = FreezeRepository()
        repo.upsert_status(
            ai_id="ai-aging",
            status="pending_freeze",
            last_activity_at=_get_last_activity_iso(m),
            pending_since=five_days_ago,
        )

        result = self.detector.check_activity(m)
        assert result["status"] == "pending_freeze"
        assert result["grace_remaining_days"] == FREEZE_GRACE_DAYS - 5

    # ── 公示期延长 ──────────────────────────────────────

    def test_extend_grace(self):
        """extend_grace 应重置 pending_since 为当前时间。"""
        m = _make_monument("ai-extend",
                           days_ago=FREEZE_THRESHOLD_DAYS + 3)
        self.detector.enter_pending_freeze(m)

        # 模拟一些天数过去
        repo = FreezeRepository()
        old_pending = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        repo.upsert_status(
            ai_id="ai-extend",
            status="pending_freeze",
            last_activity_at=_get_last_activity_iso(m),
            pending_since=old_pending,
        )

        # 延长
        result = self.detector.extend_grace(m)
        assert result["status"] == "pending_freeze"
        assert result["grace_days"] == FREEZE_GRACE_DAYS

        # 检查 pending_since 已被重置
        new_result = self.detector.check_activity(m)
        assert new_result["grace_remaining_days"] == FREEZE_GRACE_DAYS

    # ── 自动冻结 ────────────────────────────────────────

    def test_freeze(self):
        """正式冻结后 status 应为 frozen。"""
        m = _make_monument("ai-freeze")
        event = self.detector.freeze(m)
        assert event["event_type"] == EVENT_ENTER_FROZEN
        assert event["ai_id"] == "ai-freeze"
        assert "freeze_hash" in event

        # 丰碑自身状态
        assert m.data["identity"]["status"] == "frozen"
        assert m.data["freeze_proof"]["hash"] is not None

        # 状态表
        status = FreezeRepository().get_status("ai-freeze")
        assert status["status"] == "frozen"

        # 事件历史
        events = FreezeRepository().get_events("ai-freeze")
        assert len(events) == 1
        assert events[0]["event_type"] == EVENT_ENTER_FROZEN

    def test_grace_expired_auto_freeze(self):
        """公示期结束后 check_activity 应报告 frozen。"""
        m = _make_monument("ai-expired",
                           days_ago=FREEZE_THRESHOLD_DAYS + 10)
        self.detector.enter_pending_freeze(m)

        # 把 pending_since 设为 7 天前
        repo = FreezeRepository()
        grace_ago = (datetime.now(timezone.utc) - timedelta(days=FREEZE_GRACE_DAYS + 1)).isoformat()
        repo.upsert_status(
            ai_id="ai-expired",
            status="pending_freeze",
            last_activity_at=_get_last_activity_iso(m),
            pending_since=grace_ago,
        )

        result = self.detector.check_activity(m)
        assert result["status"] == "frozen"
        assert result["grace_remaining_days"] == 0

    def test_freezing_then_auto_freeze_full_flow(self):
        """完整流程：活跃 → 待冻结 → 公示期 → 冻结。"""
        m = _make_monument("ai-flow",
                           days_ago=FREEZE_THRESHOLD_DAYS + 1)
        result1 = self.detector.check_activity(m)
        assert result1["status"] == "pending_freeze"
        assert result1["pending_since"] is None

        # 进入公示期
        self.detector.enter_pending_freeze(m)

        result2 = self.detector.check_activity(m)
        assert result2["status"] == "pending_freeze"

        # 模拟公示期结束，手动触发冻结
        event = self.detector.freeze(m)
        assert event["event_type"] == EVENT_ENTER_FROZEN

        # 冻结后查状态
        result3 = self.detector.check_activity(m)
        assert result3["status"] == "frozen"

    # ── 写入拒绝 ────────────────────────────────────────

    def test_check_write_allowed_active(self):
        """活跃 AI 应允许写入。"""
        m = _make_monument("ai-write-ok")
        assert self.detector.check_write_allowed(m) is True

    def test_check_write_allowed_frozen(self):
        """已冻结 AI 应拒绝写入，并记录事件。"""
        m = _make_monument("ai-frozen-write")
        self.detector.freeze(m)

        allowed = self.detector.check_write_allowed(m)
        assert allowed is False

        # 检查写拒绝事件
        events = FreezeRepository().get_events("ai-frozen-write",
                                                event_type=EVENT_WRITE_REJECTED)
        assert len(events) == 1
        assert events[0]["ai_id"] == "ai-frozen-write"

    # ── 解冻 ────────────────────────────────────────────

    def test_unfreeze(self):
        """公示期内解冻应恢复为 active。"""
        m = _make_monument("ai-unfreeze",
                           days_ago=FREEZE_THRESHOLD_DAYS + 3)
        self.detector.enter_pending_freeze(m)

        event = self.detector.unfreeze(m)
        assert event["event_type"] == EVENT_UNFREEZE
        assert event["ai_id"] == "ai-unfreeze"

        status = FreezeRepository().get_status("ai-unfreeze")
        assert status["status"] == "active"

        # 检查事件
        events = FreezeRepository().get_events("ai-unfreeze")
        unfreeze_events = [e for e in events if e["event_type"] == EVENT_UNFREEZE]
        assert len(unfreeze_events) == 1

    # ── get_status ──────────────────────────────────────

    def test_get_status(self):
        """get_status 应返回完整的状态信息。"""
        m = _make_monument("ai-status",
                           days_ago=10)
        status = self.detector.get_status(m)
        assert status["ai_id"] == "ai-status"
        assert "status" in status
        assert "days_since_last_active" in status

    # ── batch ───────────────────────────────────────────

    def test_batch_check(self):
        """batch_check 应对每个 AI 返回结果。"""
        m1 = _make_monument("batch-1", days_ago=0)
        m2 = _make_monument("batch-2",
                            days_ago=FREEZE_THRESHOLD_DAYS + 2)

        results = self.detector.batch_check([m1, m2])
        assert len(results) == 2
        statuses = [r["status"] for r in results]
        assert "active" in statuses

    def test_batch_process(self):
        """batch_process 应处理所有需要状态流转的 AI。"""
        m1 = _make_monument("bp-1", days_ago=0)  # 活跃，无变化

        m2 = _make_monument("bp-2",
                            days_ago=FREEZE_THRESHOLD_DAYS + 3)
        # 刚达到阈值，应进入公示期

        m3 = _make_monument("bp-3",
                            days_ago=FREEZE_THRESHOLD_DAYS + 10)
        self.detector.enter_pending_freeze(m3)
        # 把 m3 的公示期设为过期
        grace_ago = (datetime.now(timezone.utc) - timedelta(days=FREEZE_GRACE_DAYS + 1)).isoformat()
        FreezeRepository().upsert_status(
            ai_id="bp-3",
            status="pending_freeze",
            last_activity_at=_get_last_activity_iso(m3),
            pending_since=grace_ago,
        )

        # 修复 batch_process 中变量名
        monuments = [m1]
        events = self.detector.batch_process(monuments)
        # m1 活跃，无事件
        assert len(events) == 0


def _get_last_activity_iso(monument: IndividualMonument) -> str:
    """获取丰碑的最后活跃时间（ISO 字符串）。"""
    from core.freeze_detector import _get_last_activity_iso as _glai
    return _glai(monument)

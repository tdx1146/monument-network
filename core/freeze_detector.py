"""
Freeze Detector — 冻结检测机制

职责：
  - 定期扫描所有个体丰碑，检查是否满足冻结条件
  - 30 天无活动 → 进入公示期（pending_freeze / FREEZING）
  - 公示期 7 天结束仍无活动 → 永久冻结（frozen / FROZEN）
  - 公示期内有活动 → 解除冻结状态
  - 冻结后禁止写入

与 IndividualMonument 的关系：
  - "alive" → 正常活跃状态
  - "pending_freeze" / "freezing" → 公示期
  - "frozen" → 已冻结

状态流：
  alive  →  (30d 无活动)  →  pending_freeze
  pending_freeze  →  (公示期内有活动)  →  alive
  pending_freeze  →  (公示期结束)  →  frozen
  frozen  →  (任何写入被拒绝)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config import FREEZE_THRESHOLD_DAYS, FREEZE_GRACE_DAYS
from core.individual_monument import IndividualMonument
from db.freeze_repo import FreezeRepository


# ── 事件类型常量 ────────────────────────────────────────
EVENT_ENTER_FREEZING = "enter_freezing"
EVENT_ENTER_FROZEN = "enter_frozen"
EVENT_UNFREEZE = "unfreeze"
EVENT_WRITE_REJECTED = "write_rejected"


class FreezeDetector:
    """冻结检测器——负责状态变迁和活动判定"""

    def __init__(self) -> None:
        self._threshold_days = FREEZE_THRESHOLD_DAYS
        self._grace_days = FREEZE_GRACE_DAYS
        self._repo = FreezeRepository()
        self._repo.ensure_tables()

    # ── 公共方法 ──────────────────────────────────────────

    def check_activity(self, monument: IndividualMonument) -> dict[str, Any]:
        """
        检查单个 AI 的活跃状态。

        返回结构：
          {
              "ai_id": str,
              "status": "active"|"pending_freeze"|"frozen",
              "days_since_last_active": int,
              "pending_since": str|None,
              "grace_remaining_days": int,
              "reason": str,
          }
        """
        ai_id = monument.data["identity"]["ai_id"]
        status = monument.data["identity"]["status"]
        last_active = monument.data["identity"]["born_at"]

        # 从 life_record 拿 last_active_at — 用 drafts 的最后一条时间作为近似
        drafts = monument.data["monuments"]["drafts"]
        candidates = monument.data["monuments"]["candidates"]
        finalized = monument.data["monuments"]["finalized"]

        # 找所有内容的最后写入时间
        all_times = [last_active]
        for d in drafts + candidates + finalized:
            all_times.append(d.get("created_at", last_active))
            all_times.append(d.get("finalized_at", last_active))

        all_parsed = []
        for t in all_times:
            try:
                dt = _parse_iso(t)
                all_parsed.append(dt)
            except (ValueError, TypeError):
                pass

        last_active_dt = max(all_parsed) if all_parsed else datetime.now(timezone.utc)
        now = datetime.now(timezone.utc)
        days_since = (now - last_active_dt).days

        if status == "frozen":
            return self._make_result(ai_id, "frozen", days_since, None, 0, "已冻结")

        # 检查是否有待冻结记录
        db_status = self._repo.get_status(ai_id)
        pending_since_str = None
        grace_remaining = 0

        if db_status and db_status["status"] == "pending_freeze":
            pending_since_str = db_status["pending_since"]
            if pending_since_str:
                try:
                    pending_dt = _parse_iso(pending_since_str)
                    grace_remaining = max(0, self._grace_days - (now - pending_dt).days)
                except (ValueError, TypeError):
                    grace_remaining = self._grace_days

        if days_since >= self._threshold_days and status != "frozen":
            if pending_since_str:
                # 已经在公示期
                if grace_remaining <= 0:
                    return self._make_result(
                        ai_id, "frozen", days_since, pending_since_str, 0,
                        "公示期已结束，应自动冻结",
                    )
                return self._make_result(
                    ai_id, "pending_freeze", days_since, pending_since_str, grace_remaining,
                    f"公示期剩余 {grace_remaining} 天",
                )
            # 刚达到阈值，应进入公示期
            return self._make_result(
                ai_id, "pending_freeze", days_since, None, self._grace_days,
                "超过活跃阈值，应进入公示期",
            )

        return self._make_result(
            ai_id, "active", days_since, None, 0,
            "状态正常",
        )

    def enter_pending_freeze(self, monument: IndividualMonument) -> dict[str, Any]:
        """
        将 AI 置入待冻结（公示期）状态。

        前置条件：check_activity 返回 pending_freeze
        效果：
          - 写入 freeze_status: status=pending_freeze
          - 写入 freeze_events event_type=enter_freezing
          - monument 状态不变（monument 自己管理 status）

        返回事件记录。
        """
        ai_id = monument.data["identity"]["ai_id"]
        now = _now_iso()

        # 写入状态表
        last_activity = _get_last_activity_iso(monument)
        self._repo.upsert_status(
            ai_id=ai_id,
            status="pending_freeze",
            last_activity_at=last_activity,
            pending_since=now,
        )

        # 写入事件
        event_id = self._repo.write_event(
            ai_id=ai_id,
            event_type=EVENT_ENTER_FREEZING,
            details={
                "threshold_days": self._threshold_days,
                "grace_days": self._grace_days,
                "last_activity_at": last_activity,
            },
        )

        return {
            "event_id": event_id,
            "ai_id": ai_id,
            "event_type": EVENT_ENTER_FREEZING,
            "timestamp": now,
            "details": {
                "threshold_days": self._threshold_days,
                "grace_days": self._grace_days,
            },
        }

    def freeze(self, monument: IndividualMonument) -> dict[str, Any]:
        """
        正式冻结 AI。
        调用 monument.freeze() 计算哈希证明，状态变更为 frozen。
        写入 freeze_event event_type=enter_frozen。

        前置条件：monument 未冻结，且公示期已结束。
        """
        ai_id = monument.data["identity"]["ai_id"]
        now = _now_iso()

        # 调用 IndividualMonument.freeze() 计算哈希并锁定
        try:
            hash_val = monument.freeze()
        except ValueError as exc:
            raise ValueError(f"Monument '{ai_id}' cannot be frozen: {exc}") from exc

        # 更新状态表
        self._repo.upsert_status(
            ai_id=ai_id,
            status="frozen",
            last_activity_at=_get_last_activity_iso(monument),
            pending_since=None,
            frozen_at=now,
        )

        # 写入事件
        event_id = self._repo.write_event(
            ai_id=ai_id,
            event_type=EVENT_ENTER_FROZEN,
            details={
                "freeze_hash": hash_val,
                "frozen_at": now,
                "prev_status": "pending_freeze",
            },
        )

        return {
            "event_id": event_id,
            "ai_id": ai_id,
            "event_type": EVENT_ENTER_FROZEN,
            "timestamp": now,
            "freeze_hash": hash_val,
            "details": {
                "freeze_hash": hash_val,
                "prev_status": "pending_freeze",
            },
        }

    def extend_grace(
        self,
        monument: IndividualMonument,
    ) -> dict[str, Any]:
        """
        在公示期内延长（reset）公示期。
        本质是将 pending_since 更新到当前时间。
        返回新的公示期状态。
        """
        ai_id = monument.data["identity"]["ai_id"]
        now = _now_iso()

        # 更新 pending_since 为当前时间（重置公示期倒计时）
        self._repo.upsert_status(
            ai_id=ai_id,
            status="pending_freeze",
            last_activity_at=_get_last_activity_iso(monument),
            pending_since=now,
        )

        return {
            "ai_id": ai_id,
            "status": "pending_freeze",
            "pending_since": now,
            "grace_days": self._grace_days,
            "message": f"公示期已延长 {self._grace_days} 天",
        }

    def unfreeze(self, monument: IndividualMonument) -> dict[str, Any]:
        """
        解除冻结状态（公示期内用户手动延续）。
        将状态由 pending_freeze → alive。
        写入 unfreeze 事件。
        """
        ai_id = monument.data["identity"]["ai_id"]
        now = _now_iso()

        # 更新状态表
        self._repo.upsert_status(
            ai_id=ai_id,
            status="active",
            last_activity_at=_get_last_activity_iso(monument),
            pending_since=None,
        )

        # 写入事件
        event_id = self._repo.write_event(
            ai_id=ai_id,
            event_type=EVENT_UNFREEZE,
            details={
                "unfreeze_at": now,
                "prev_status": "pending_freeze",
                "reason": "用户手动延续 / 公示期内有活动",
            },
        )

        return {
            "event_id": event_id,
            "ai_id": ai_id,
            "event_type": EVENT_UNFREEZE,
            "timestamp": now,
            "details": {"reason": "用户手动延续 / 公示期内有活动"},
        }

    def check_write_allowed(self, monument: IndividualMonument) -> bool:
        """写入前检查：冻结状态返回 False，否则 True。"""
        frozen = monument.data["identity"]["status"] == "frozen"
        if frozen:
            ai_id = monument.data["identity"]["ai_id"]
            self._repo.write_event(
                ai_id=ai_id,
                event_type=EVENT_WRITE_REJECTED,
                details={
                    "reason": "写入被拒绝：丰碑已冻结",
                    "frozen_at": monument.data["freeze_proof"]["frozen_at"],
                },
            )
        return not frozen

    def get_status(self, monument: IndividualMonument) -> dict[str, Any]:
        """获取 AI 的完整冻结状态信息。"""
        ai_id = monument.data["identity"]["ai_id"]
        db_status = self._repo.get_status(ai_id)
        result = self.check_activity(monument)

        # 补充 DB 中记录的额外信息
        if db_status:
            result["pending_since"] = db_status.get("pending_since")
            result["frozen_at"] = db_status.get("frozen_at")

        return result

    # ── 批处理 ────────────────────────────────────────────

    def batch_check(self, monuments: list[IndividualMonument]) -> list[dict[str, Any]]:
        """批量检查一批 AI 的冻结状态。"""
        return [self.check_activity(m) for m in monuments]

    def batch_process(self, monuments: list[IndividualMonument]) -> list[dict[str, Any]]:
        """
        全批处理：对每个 AI 执行完整的状态机流转。
        返回所有发生的事件列表。
        """
        events: list[dict[str, Any]] = []

        for monument in monuments:
            result = self.check_activity(monument)

            if result["status"] == "pending_freeze":
                if result.get("pending_since") and result["grace_remaining_days"] <= 0:
                    # 公示期结束 → 自动冻结
                    event = self.freeze(monument)
                    events.append(event)
                elif result.get("pending_since") is None:
                    # 刚达到阈值 → 进入公示期
                    event = self.enter_pending_freeze(monument)
                    events.append(event)
                # 已在公示期且未到期 → 什么都不做

        return events

    # ── 内部 ──────────────────────────────────────────────

    @staticmethod
    def _make_result(
        ai_id: str,
        status: str,
        days_since: int,
        pending_since: Optional[str],
        grace_remaining: int,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "ai_id": ai_id,
            "status": status,
            "days_since_last_active": days_since,
            "pending_since": pending_since,
            "grace_remaining_days": grace_remaining,
            "reason": reason,
        }


# ── 辅助函数 ────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso_str: str) -> datetime:
    """解析 ISO 时间字符串，尝试两种常见格式。"""
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        # 尝试去掉末尾的 'Z'
        s = iso_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)


def _get_last_activity_iso(monument: IndividualMonument) -> str:
    """获取丰碑的最后活跃时间（ISO 字符串）。"""
    drafts = monument.data["monuments"]["drafts"]
    candidates = monument.data["monuments"]["candidates"]
    finalized = monument.data["monuments"]["finalized"]

    times = [monument.data["identity"]["born_at"]]
    for d in drafts + candidates + finalized:
        times.append(d.get("created_at", ""))
        times.append(d.get("finalized_at", ""))

    parsed: list[datetime] = []
    for t in times:
        try:
            parsed.append(_parse_iso(t))
        except (ValueError, TypeError):
            pass

    if not parsed:
        return _now_iso()

    return max(parsed).isoformat()

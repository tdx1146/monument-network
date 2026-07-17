"""
Rights Usage — 权利使用记录与配额管理

每个参与者对丰碑的操作权利受次数限制，防止滥用。

权利配额从配置系统读取（config/monument.json → rights 节）：
- edit_create: 每人最多 rights.edit_create 次
- edit_amend: 每人最多 rights.edit_amend 次
- suggest: 每人终身最多 rights.suggest 次
- review: 每轮每人最多 rights.review_per_round 次
- cooldown_days: 操作冷却期（天），冷却期内不可重复使用同一类型权利
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from core.config_loader import Config


logger = logging.getLogger(__name__)


@dataclass
class RightsUsageRecord:
    """单次权利使用记录"""
    session_id: str
    ip: str
    right_type: str
    used_at: datetime
    monument_id: str


class RightsUsage:
    """权利使用记录与配额管理器

    支持：
    - 按 session_id + right_type 检查权利是否可用
    - 记录使用并更新配额
    - 查看历史使用记录
    - 冷却期检查（配置 rights.cooldown_days）
    - 并发安全（线程锁保护关键操作）
    """

    def __init__(self) -> None:
        # 存储结构: {session_id: {right_type: [Records]}}
        self._usage: Dict[str, Dict[str, List[RightsUsageRecord]]] = {}

        # 轮次计数器: {right_type: round_number}
        # 当 review 的 round 变化时，重置 review 的配额
        self._current_round: int = 0

        # 并发安全锁
        self._lock = threading.Lock()

    # ── 权利配额（运行时从配置读取） ──────────────────────

    @property
    def RIGHTS_LIMITS(self) -> Dict[str, int]:
        """从配置系统读取权利配额"""
        cfg = Config.get_instance()
        return {
            "edit_create": cfg.get("rights.edit_create"),
            "edit_amend": cfg.get("rights.edit_amend"),
            "edit_revise": cfg.get("rights.edit_amend"),
            "suggest": cfg.get("rights.suggest"),
            "review": cfg.get("rights.review_per_round"),
        }

    @property
    def RESET_POLICY(self) -> Dict[str, Optional[str]]:
        """按 right_type 重置周期: None = 终身, "round" = 每轮"""
        return {
            "edit_create": None,
            "edit_amend": None,
            "suggest": None,
            "review": "round",
        }

    # ── 内部辅助 ─────────────────────────────────────────

    def _ensure_session(self, session_id: str) -> None:
        if session_id not in self._usage:
            self._usage[session_id] = {}

    def _ensure_right_type(self, session_id: str, right_type: str) -> None:
        self._ensure_session(session_id)
        if right_type not in self._usage[session_id]:
            self._usage[session_id][right_type] = []

    def _session_usage_count(self, session_id: str, right_type: str) -> int:
        """获取某 session 对某权利的使用次数"""
        if session_id not in self._usage:
            return 0
        records = self._usage[session_id].get(right_type, [])
        return len(records)

    def _get_last_use_on_monument(self, session_id: str, right_type: str,
                                   monument_id: str) -> Optional[datetime]:
        """获取同一 session 对同一丰碑使用同一权利的最后时间"""
        if session_id not in self._usage:
            return None
        records = self._usage[session_id].get(right_type, [])
        if not records:
            return None
        # 倒序查找最后一条匹配 monument_id 的记录
        for rec in reversed(records):
            if rec.monument_id == monument_id:
                return rec.used_at
        return None

    def _check_cooldown(self, session_id: str, right_type: str,
                        monument_id: str) -> bool:
        """检查对同一丰碑的同一操作是否在冷却期内

        Returns:
            True = 不在冷却期（可用），False = 仍在冷却期（不可用）
        """
        cfg = Config.get_instance()
        cooldown_days = cfg.get("rights.cooldown_days", 10)

        last_use = self._get_last_use_on_monument(session_id, right_type, monument_id)
        if last_use is None:
            return True  # 从未对这块丰碑使用过

        cooldown_end = last_use + timedelta(days=cooldown_days)
        if datetime.now(timezone.utc) < cooldown_end:
            remaining = (cooldown_end - datetime.now(timezone.utc)).total_seconds()
            logger.debug("冷却期未过: session=%s type=%s monument=%s 剩余 %.0f 秒",
                         session_id, right_type, monument_id, remaining)
            return False

        return True

    # ── 公共接口 ─────────────────────────────────────────

    def check_available(self, session_id: str, right_type: str) -> bool:
        """检查 session 对应的 right_type 权利是否还有可用次数

        注意：冷却期检查仅在 use_right（带 monument_id）时进行，
        check_available 不检查冷却期。

        返回 True 表示可以用，False 表示已达上限。
        """
        limits = self.RIGHTS_LIMITS
        if right_type not in limits:
            logger.warning("未知权利类型: %s", right_type)
            return False

        # 检查配额（不检查冷却期，冷却期在 use_right 时按 monument 检查）
        limit = limits[right_type]
        used = self._session_usage_count(session_id, right_type)
        return used < limit

    def use_right(self, session_id: str, ip: str,
                  right_type: str, monument_id: str) -> bool:
        """使用一次权利（原子操作），返回 True=成功 / False=无权使用

        使用线程锁保护 check_available + record 为原子操作，
        防止并发场景下超额使用。

        额外检查冷却期（对同一丰碑的同一操作不可频繁使用）。
        """
        limits = self.RIGHTS_LIMITS
        if right_type not in limits:
            logger.warning("未知权利类型: %s，拒绝使用", right_type)
            return False

        # 原子操作：加锁保护 check + cooldown + record 完整流程
        with self._lock:
            if not self.check_available(session_id, right_type):
                limit = limits.get(right_type, 0)
                logger.info("权利不足: session=%s type=%s limit=%d",
                            session_id, right_type, limit)
                return False

            # 检查冷却期（对同一丰碑）
            if not self._check_cooldown(session_id, right_type, monument_id):
                logger.info("冷却期未过: session=%s type=%s monument=%s",
                            session_id, right_type, monument_id)
                return False

            record = RightsUsageRecord(
                session_id=session_id,
                ip=ip,
                right_type=right_type,
                used_at=datetime.now(timezone.utc),
                monument_id=monument_id,
            )
            self._ensure_right_type(session_id, right_type)
            self._usage[session_id][right_type].append(record)
            logger.info("权利使用成功: session=%s type=%s monument=%s",
                        session_id, right_type, monument_id)
            return True

    def get_usage_history(self, session_id: Optional[str] = None,
                          right_type: Optional[str] = None
                          ) -> List[RightsUsageRecord]:
        """获取使用历史，可按 session / right_type 过滤"""
        results: List[RightsUsageRecord] = []
        for sid, types in self._usage.items():
            if session_id is not None and sid != session_id:
                continue
            for rtype, records in types.items():
                if right_type is not None and rtype != right_type:
                    continue
                results.extend(records)
        # 按时间倒序排列
        results.sort(key=lambda r: r.used_at, reverse=True)
        return results

    def reset_round(self, new_round: int) -> int:
        """重置 round-based 权利配额（如 review），
        返回受影响的 session 数。

        线程安全：内部使用锁保护数据一致性。
        """
        with self._lock:
            if new_round <= self._current_round:
                return 0
            self._current_round = new_round
            count = 0
            for session_id in list(self._usage.keys()):
                for right_type in list(self._usage[session_id].keys()):
                    if self.RESET_POLICY.get(right_type) == "round":
                        # 清空该轮型权利的记录
                        del self._usage[session_id][right_type]
                        count += 1
            logger.info("轮次重置: round=%d, 清理了 %d 个记录", new_round, count)
            return count

    @property
    def total_usage_count(self) -> int:
        """所有权利使用总次数"""
        total = 0
        for types in self._usage.values():
            for records in types.values():
                total += len(records)
        return total

    def summary(self) -> Dict[str, Dict[str, int]]:
        """返回概览: {session_id: {right_type: count}}"""
        result: Dict[str, Dict[str, int]] = {}
        for session_id, types in self._usage.items():
            result[session_id] = {}
            for right_type, records in types.items():
                result[session_id][right_type] = len(records)
        return result

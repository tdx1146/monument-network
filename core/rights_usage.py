"""
Rights Usage — 权利使用记录与配额管理

每个参与者对丰碑的操作权利受次数限制，防止滥用。

权利配额从配置系统读取（config/monument.json → rights 节）：
- edit_create: 每人最多 rights.edit_create 次
- edit_amend: 每人最多 rights.edit_amend 次
- suggest: 每人终身最多 rights.suggest 次
- review: 每轮每人最多 rights.review_per_round 次
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.config_loader import Config

logger = logging.getLogger(__name__)


# ── 配置实例 ──────────────────────────────────────────────
_config: Optional[Config] = None


def init_config(config_path: str = "config/monument.json") -> Config:
    global _config
    if _config is None:
        _config = Config(config_path, auto_reload=True)
    return _config


def get_config() -> Config:
    global _config
    if _config is None:
        _config = init_config()
    return _config


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
    """

    def __init__(self) -> None:
        # 存储结构: {session_id: {right_type: [Records]}}
        self._usage: Dict[str, Dict[str, List[RightsUsageRecord]]] = {}

        # 轮次计数器: {right_type: round_number}
        # 当 review 的 round 变化时，重置 review 的配额
        self._current_round: int = 0

    # ── 权利配额（运行时从配置读取） ──────────────────────

    @property
    def RIGHTS_LIMITS(self) -> Dict[str, int]:
        """从配置系统读取权利配额"""
        cfg = get_config()
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

    # ── 公共接口 ─────────────────────────────────────────

    def check_available(self, session_id: str, right_type: str) -> bool:
        """检查 session 对应的 right_type 权利是否还有可用次数

        返回 True 表示可以用，False 表示已达上限。
        """
        limits = self.RIGHTS_LIMITS
        if right_type not in limits:
            logger.warning("未知权利类型: %s", right_type)
            return False

        limit = limits[right_type]
        used = self._session_usage_count(session_id, right_type)
        return used < limit

    def use_right(self, session_id: str, ip: str,
                  right_type: str, monument_id: str) -> bool:
        """使用一次权利，返回 True=成功 / False=无权使用"""
        limits = self.RIGHTS_LIMITS
        if right_type not in limits:
            logger.warning("未知权利类型: %s，拒绝使用", right_type)
            return False

        if not self.check_available(session_id, right_type):
            limit = limits.get(right_type, 0)
            logger.info("权利不足: session=%s type=%s limit=%d",
                        session_id, right_type, limit)
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
        """
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

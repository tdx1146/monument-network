"""
Monument Erosion — 丰碑磨损与加固机制

丰碑随时间自然磨损（得分衰减），用户行为（引用、建议、评审、编辑）
可以加固丰碑，延缓或逆转磨损。

所有数值从配置系统读取，不写死。

阈值系统（可配置）：
- normal: score > thresholds.normal
- warning: thresholds.warning < score <= thresholds.normal
- endangered: thresholds.endangered < score <= thresholds.warning
- archived: score <= thresholds.archived
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from core.config_loader import Config

logger = logging.getLogger(__name__)

# ── 配置实例 ──────────────────────────────────────────────
# 单例模式：所有模块共享同一个 Config 实例
_config: Optional[Config] = None


def init_config(config_path: str = "config/monument.json") -> Config:
    """初始化配置（main 入口调用）；允许外部注入"""
    global _config
    if _config is None:
        _config = Config(config_path, auto_reload=True)
    return _config


def get_config() -> Config:
    """获取配置实例，首次调用时自动初始化"""
    global _config
    if _config is None:
        _config = init_config()
    return _config


# ─── 数据类型 ──────────────────────────────────────────────

@dataclass
class MonumentEntry:
    """丰碑条目——可磨损/可加固的丰碑数据"""
    id: str
    title: str
    content: str
    author: str
    score: float = 1.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_reinforced_at: Optional[datetime] = None

    # 统计
    lifetime_references: int = 0
    lifetime_edits: int = 0
    lifetime_reviews: int = 0

    # 磨损历史快照（用于审计）
    _score_history: List[Tuple[datetime, float, str]] = field(default_factory=list)

    def _record_score(self, new_score: float, reason: str) -> None:
        self._score_history.append((datetime.now(timezone.utc), new_score, reason))

    def current_status(self) -> str:
        """基于当前分数返回状态"""
        return check_threshold(self.score)


# ─── 磨损函数 ──────────────────────────────────────────────

def apply_erosion(entry: MonumentEntry, days: int) -> float:
    """应用自然磨损，返回新分数

    磨损逻辑：
    - 每天衰减 base_rate
    - 低于 ACCELERATION_THRESHOLD 时加速（乘以 acceleration_factor）
    分数永不小于 0。
    """
    if days <= 0:
        return entry.score

    cfg = get_config()
    base_rate = cfg.get("erosion.base_rate")
    accel_threshold = cfg.get("erosion.acceleration_threshold")
    accel_factor = cfg.get("erosion.acceleration_factor")

    rate = base_rate
    if entry.score < accel_threshold:
        rate *= accel_factor

    # 复合衰减: new = old * (1 - rate)^days
    new_score = entry.score * ((1.0 - rate) ** days)
    new_score = max(new_score, 0.0)

    entry.score = new_score
    entry._record_score(new_score, f"erosion_{days}d_rate{rate:.4f}")
    logger.debug("磨损: id=%s days=%d rate=%.4f score=%.4f",
                 entry.id, days, rate, new_score)
    return new_score


def reinforce(entry: MonumentEntry, amount: float) -> float:
    """加固丰碑，返回新分数

    加固增量有上限，防止刷分：
    - 每次加固不超过 single_cap
    - 加固后分数不超过 score_max
    - 高分时效益递减
    """
    if amount <= 0:
        return entry.score

    cfg = get_config()
    single_cap = cfg.get("reinforce.single_cap")
    score_max = cfg.get("erosion.score_max")
    dampening_start = cfg.get("reinforce.dampening_start")
    dampening_min = cfg.get("reinforce.dampening_min")

    # 限制单次加固上限
    clamped = min(amount, single_cap)

    # 高分时加固效益递减
    if entry.score > dampening_start:
        dampening = 1.0 - (entry.score - dampening_start) * 2.0
        dampening = max(dampening, dampening_min)
        clamped *= dampening

    new_score = min(entry.score + clamped, score_max)
    entry.score = new_score
    entry.last_reinforced_at = datetime.now(timezone.utc)
    entry._record_score(new_score, f"reinforce_{amount:.4f}")
    logger.debug("加固: id=%s amount=%.4f clamped=%.4f score=%.4f",
                 entry.id, amount, clamped, new_score)
    return new_score


def reinforce_by_action(entry: MonumentEntry, action: str) -> float:
    """按操作类型加固，返回新分数"""
    cfg = get_config()
    amounts = {
        "reference": cfg.get("reinforce.by_reference"),
        "suggestion": cfg.get("reinforce.by_suggestion"),
        "review": cfg.get("reinforce.by_review"),
        "edit": cfg.get("reinforce.by_edit"),
    }
    amount = amounts.get(action, 0.0)
    if amount == 0.0:
        logger.warning("未知加固操作: %s", action)
        return entry.score

    # 更新统计数据
    if action == "reference":
        entry.lifetime_references += 1
    elif action == "review":
        entry.lifetime_reviews += 1
    elif action == "edit":
        entry.lifetime_edits += 1
    # suggestion 目前不计入统计

    return reinforce(entry, amount)


def check_threshold(score: float) -> str:
    """检查分数阈值，返回状态

    Returns:
        "normal"      — score > thresholds.normal
        "warning"     — thresholds.warning < score <= thresholds.normal
        "endangered"  — thresholds.endangered < score <= thresholds.warning
        "archived"    — score <= thresholds.archived
    """
    cfg = get_config()
    normal = cfg.get("thresholds.normal")
    warning = cfg.get("thresholds.warning")
    endangered = cfg.get("thresholds.endangered")
    archived = cfg.get("thresholds.archived")

    if score > normal:
        return "normal"
    if score > warning:
        return "warning"
    if score > endangered:
        return "endangered"
    return "archived"


def simulate_daily_cycle(entries: List[MonumentEntry],
                         days: int = 1) -> List[str]:
    """模拟多天自然磨损循环，返回触发"archived"状态的条目 ID 列表"""
    archived: List[str] = []
    for _ in range(days):
        for entry in entries:
            apply_erosion(entry, 1)
            if check_threshold(entry.score) == "archived":
                archived.append(entry.id)
    return archived

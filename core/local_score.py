"""
本地统计 — 简化的本地评分统计模块

原积分账本功能已废弃（见 ARCHITECTURE.md v1），
降级为简单的本地统计聚合器。

改造说明（v3.7.0）：
- 废弃 ScoreAccount / ScoreTransaction / ScoreSource 数据结构
- 移除对 score_repo 的依赖
- 所有数值从 config/monument.json 读取
- 保留 compute_health_score 作为纯计算函数
- 保留本地统计聚合能力（用于日志/监控）
"""

import logging
from typing import Dict, Optional

from core.config_loader import Config

logger = logging.getLogger(__name__)

# ── 配置实例（共享 singleton） ─────────────────────────────
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


# ─── 本地统计聚合器 ─────────────────────────────────────────

class LocalScoreBook:
    """
    本地统计聚合器（原积分账本降级版）

    废弃功能：
    - ScoreAccount / ScoreTransaction（改用 SQLite 持久化）
    - add_xuanjian_score / add_goal_tree_score / add_scheduler_score
    - 积分持久化（移交给 score_repo 全权处理）

    保留功能：
    - compute_health_score: 计算三维加权健康贡献分
    - 本地统计聚合（内存缓存，用于日志/监控）
    """

    def __init__(self, config: Optional[Config] = None):
        """
        Args:
            config: Config 实例（依赖注入）
                    不提供时自动从 singleton 读取
        """
        self._config = config or get_config()
        self._stats_cache: Dict[str, Dict] = {}
        logger.info("LocalScoreBook 初始化（简化模式）：所有数值从配置读取")

    # ─── 三维加权健康分 ──────────────────────────────────────

    def compute_health_score(
        self,
        xuanjian_score: float,
        goal_tree_score: float,
        scheduler_score: float,
        weights: Optional[Dict[str, float]] = None
    ) -> float:
        """
        计算三维加权健康贡献分。

        公式：
            健康分 = α×玄鉴分 + β×目的树分 + γ×调度器分
                   = erosion.xxx（权重从配置读取）

        Args:
            xuanjian_score:   玄鉴评分（质量）
            goal_tree_score:  目的树评分（方向）
            scheduler_score:  调度器评分（纪律）
            weights:          自定义权重 {"xuanjian": α, "goal_tree": β, "scheduler": γ}

        Returns:
            float: 加权健康分（按配置精度四舍五入）
        """
        if weights is None:
            # 权重从配置读取
            weights = {
                "xuanjian": self._config.get("scoring.weight_quality", 0.4),
                "goal_tree": self._config.get("scoring.weight_direction", 0.3),
                "scheduler": self._config.get("scoring.weight_discipline", 0.3),
            }

        health_score = (
            weights["xuanjian"] * xuanjian_score +
            weights["goal_tree"] * goal_tree_score +
            weights["scheduler"] * scheduler_score
        )

        # 精度从配置读取
        precision = int(self._config.get("display.precision", 4))
        return round(health_score, precision)

    # ─── 本地统计 ────────────────────────────────────────────

    def update_stats(self, ai_id: str, key: str, value: float) -> None:
        """
        更新本地统计缓存（内存级，不落盘）

        Args:
            ai_id: AI 标识
            key:   统计键
            value: 统计值
        """
        if ai_id not in self._stats_cache:
            self._stats_cache[ai_id] = {}
        self._stats_cache[ai_id][key] = value
        logger.debug("本地统计更新: ai=%s key=%s value=%s", ai_id, key, value)

    def get_stats(self, ai_id: str) -> Dict:
        """
        获取 AI 的本地统计快照

        Args:
            ai_id: AI 标识

        Returns:
            Dict: 统计字典（空 dict = 无记录）
        """
        return self._stats_cache.get(ai_id, {})

    def clear_stats(self, ai_id: Optional[str] = None) -> None:
        """
        清除统计缓存

        Args:
            ai_id: AI 标识（None=全部清除）
        """
        if ai_id:
            self._stats_cache.pop(ai_id, None)
        else:
            self._stats_cache.clear()

    @property
    def total_ais_tracked(self) -> int:
        return len(self._stats_cache)

    def __repr__(self) -> str:
        return (
            f"LocalScoreBook(config={self._config.config_path}, "
            f"tracked_ais={self.total_ais_tracked})"
        )

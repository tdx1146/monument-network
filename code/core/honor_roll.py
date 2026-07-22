"""
Honor Roll — 功绩榜

聚合丰碑的编辑/评审/建议数据，按贡献度排序展示。
支持：
- 编辑者排行榜（按 edits 和 score 综合）
- 评审者排行榜（按 reviews 和 score 综合）
- Markdown 功绩榜渲染（用于展示/通知）
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── 排行榜参数 ───────────────────────────────────────────

EDITOR_WEIGHT_COUNT = 0.6     # 编辑次数权重
EDITOR_WEIGHT_SCORE = 0.4     # 编辑分数权重

REVIEWER_WEIGHT_COUNT = 0.5   # 评审次数权重
REVIEWER_WEIGHT_SCORE = 0.5   # 评审分数权重

# SVG/ASCII 奖杯标记
TROPHY = {
    1: "🏆",
    2: "🥈",
    3: "🥉",
}


@dataclass
class EditorRecord:
    """编辑者记录"""
    editor_id: str
    editor_name: str
    edit_count: int
    total_score: float  # 编辑的丰碑平均分数
    last_edit_at: Optional[datetime] = None


@dataclass
class ReviewerRecord:
    """评审者记录"""
    reviewer_id: str
    reviewer_name: str
    review_count: int
    total_score: float  # 评审的丰碑平均分数
    last_review_at: Optional[datetime] = None


class HonorRoll:
    """功绩榜——聚合贡献数据并排序"""

    def __init__(self) -> None:
        self._editors: Dict[str, EditorRecord] = {}
        self._reviewers: Dict[str, ReviewerRecord] = {}

    # ─── 编辑者管理 ─────────────────────────────────────

    def record_edit(self, editor_id: str, editor_name: str,
                    monument_score: float) -> None:
        """记录一次编辑"""
        if editor_id not in self._editors:
            self._editors[editor_id] = EditorRecord(
                editor_id=editor_id,
                editor_name=editor_name,
                edit_count=0,
                total_score=0.0,
            )
        record = self._editors[editor_id]
        record.editor_name = editor_name
        # 滚动平均: 保持 total_score 为累计总分
        record.total_score += monument_score
        record.edit_count += 1
        record.last_edit_at = datetime.now(timezone.utc)
        logger.debug("记录编辑: id=%s name=%s count=%d",
                     editor_id, editor_name, record.edit_count)

    def record_review(self, reviewer_id: str, reviewer_name: str,
                      monument_score: float) -> None:
        """记录一次评审"""
        if reviewer_id not in self._reviewers:
            self._reviewers[reviewer_id] = ReviewerRecord(
                reviewer_id=reviewer_id,
                reviewer_name=reviewer_name,
                review_count=0,
                total_score=0.0,
            )
        record = self._reviewers[reviewer_id]
        record.reviewer_name = reviewer_name
        record.total_score += monument_score
        record.review_count += 1
        record.last_review_at = datetime.now(timezone.utc)
        logger.debug("记录评审: id=%s name=%s count=%d",
                     reviewer_id, reviewer_name, record.review_count)

    # ─── 查询 ────────────────────────────────────────────

    def get_editor_rankings(self, limit: int = 10) -> List[Dict]:
        """获取编辑者排行榜（按综合得分排序）

        综合 = edit_count * EDITOR_WEIGHT_COUNT
             + avg_score * EDITOR_WEIGHT_SCORE
        """
        scored: List[tuple[float, EditorRecord]] = []
        for record in self._editors.values():
            avg_score = record.total_score / max(record.edit_count, 1)
            composite = (
                record.edit_count * EDITOR_WEIGHT_COUNT
                + avg_score * EDITOR_WEIGHT_SCORE
            )
            scored.append((composite, record))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "rank": i + 1,
                "id": rec.editor_id,
                "name": rec.editor_name,
                "edit_count": rec.edit_count,
                "avg_score": round(rec.total_score / max(rec.edit_count, 1), 4),
                "composite": round(composite, 4),
            }
            for i, (composite, rec) in enumerate(scored[:limit])
        ]

    def get_reviewer_rankings(self, limit: int = 10) -> List[Dict]:
        """获取评审者排行榜"""
        scored: List[tuple[float, ReviewerRecord]] = []
        for record in self._reviewers.values():
            avg_score = record.total_score / max(record.review_count, 1)
            composite = (
                record.review_count * REVIEWER_WEIGHT_COUNT
                + avg_score * REVIEWER_WEIGHT_SCORE
            )
            scored.append((composite, record))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "rank": i + 1,
                "id": rec.reviewer_id,
                "name": rec.reviewer_name,
                "review_count": rec.review_count,
                "avg_score": round(rec.total_score / max(rec.review_count, 1), 4),
                "composite": round(composite, 4),
            }
            for i, (composite, rec) in enumerate(scored[:limit])
        ]

    def render_honor_roll(self, editor_limit: int = 5,
                          reviewer_limit: int = 5) -> str:
        """渲染功绩榜 Markdown"""
        lines: List[str] = [
            "# 🏛 丰碑功绩榜",
            "",
            f"> 更新时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
        ]

        # 编辑者榜
        lines.append("## ✏️ 编辑者排行榜")
        lines.append("")
        editors = self.get_editor_rankings(limit=editor_limit)
        if not editors:
            lines.append("_暂无编辑记录_")
        else:
            lines.append("| 排名 | 编辑者 | 编辑次数 | 平均分 |")
            lines.append("|------|--------|----------|--------|")
            for e in editors:
                trophy = TROPHY.get(e["rank"], f"{e['rank']}.")
                lines.append(f"| {trophy} {e['name']} | {e['name']} "
                             f"| {e['edit_count']} | {e['avg_score']} |")

        lines.append("")

        # 评审者榜
        lines.append("## 🔍 评审者排行榜")
        lines.append("")
        reviewers = self.get_reviewer_rankings(limit=reviewer_limit)
        if not reviewers:
            lines.append("_暂无评审记录_")
        else:
            lines.append("| 排名 | 评审者 | 评审次数 | 平均分 |")
            lines.append("|------|--------|----------|--------|")
            for r in reviewers:
                trophy = TROPHY.get(r["rank"], f"{r['rank']}.")
                lines.append(f"| {trophy} {r['name']} | {r['name']} "
                             f"| {r['review_count']} | {r['avg_score']} |")

        lines.append("")
        lines.append("---")
        lines.append("_轻如烟 · 丰碑网络 自动生成_")

        return "\n".join(lines)

    @property
    def total_editors(self) -> int:
        return len(self._editors)

    @property
    def total_reviewers(self) -> int:
        return len(self._reviewers)

    def clear(self) -> None:
        """清空所有数据（测试用途）"""
        self._editors.clear()
        self._reviewers.clear()

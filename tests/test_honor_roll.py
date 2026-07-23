"""
测试：功绩榜 (Honor Roll)
"""

import pytest

from core.honor_roll import HonorRoll


class TestHonorRoll:

    def test_init_empty(self):
        hr = HonorRoll()
        assert hr.total_editors == 0
        assert hr.total_reviewers == 0
        assert hr.get_editor_rankings() == []
        assert hr.get_reviewer_rankings() == []

    # ─── 编辑者测试 ─────────────────────────────────────────

    def test_record_edit(self):
        hr = HonorRoll()
        hr.record_edit("editor-1", "小A", 0.85)
        rankings = hr.get_editor_rankings()
        assert len(rankings) == 1
        assert rankings[0]["name"] == "小A"
        assert rankings[0]["edit_count"] == 1

    def test_record_multiple_edits(self):
        hr = HonorRoll()
        hr.record_edit("editor-1", "小A", 0.85)
        hr.record_edit("editor-1", "小A", 0.92)
        rankings = hr.get_editor_rankings()
        assert rankings[0]["edit_count"] == 2
        assert rankings[0]["avg_score"] == pytest.approx(0.885, 0.01)

    def test_multiple_editors_ranking(self):
        hr = HonorRoll()
        hr.record_edit("e1", "Alice", 0.9)
        hr.record_edit("e1", "Alice", 0.8)
        hr.record_edit("e2", "Bob", 0.95)
        rankings = hr.get_editor_rankings(limit=2)
        # Alice 2 次编辑 vs Bob 1 次，Alice 应排前面
        assert rankings[0]["id"] == "e1"
        assert rankings[1]["id"] == "e2"

    def test_editor_ranking_limit(self):
        hr = HonorRoll()
        for i in range(5):
            hr.record_edit(f"e{i}", f"Editor{i}", 0.8)
        assert len(hr.get_editor_rankings(limit=3)) == 3

    # ─── 评审者测试 ─────────────────────────────────────────

    def test_record_review(self):
        hr = HonorRoll()
        hr.record_review("r-1", "评审者A", 0.75)
        rankings = hr.get_reviewer_rankings()
        assert len(rankings) == 1
        assert rankings[0]["name"] == "评审者A"
        assert rankings[0]["review_count"] == 1

    def test_reviewer_ranking_by_composite(self):
        hr = HonorRoll()
        hr.record_review("r1", "ReviewerA", 0.9)
        hr.record_review("r1", "ReviewerA", 0.8)
        hr.record_review("r2", "ReviewerB", 0.95)
        rankings = hr.get_reviewer_rankings()
        # r1 2 次 vs r2 1 次
        assert rankings[0]["id"] == "r1"
        assert rankings[1]["id"] == "r2"

    def test_reviewer_ranking_limit(self):
        hr = HonorRoll()
        for i in range(5):
            hr.record_review(f"r{i}", f"Reviewer{i}", 0.8)
        assert len(hr.get_reviewer_rankings(limit=2)) == 2

    # ─── Empty rendering ─────────────────────────────────────

    def test_render_empty(self):
        hr = HonorRoll()
        md = hr.render_honor_roll()
        assert "暂无编辑记录" in md
        assert "暂无评审记录" in md

    def test_render_with_data(self):
        hr = HonorRoll()
        hr.record_edit("e1", "Alice", 0.9)
        hr.record_edit("e2", "Bob", 0.8)
        hr.record_review("r1", "Charlie", 0.85)

        md = hr.render_honor_roll(editor_limit=5, reviewer_limit=5)
        assert "# 🏛 丰碑功绩榜" in md
        assert "Alice" in md
        assert "Bob" in md
        assert "Charlie" in md
        assert "🏆" in md  # 第一名奖杯
        assert "暂无" not in md  # 数据都有了

    def test_render_markdown_structure(self):
        hr = HonorRoll()
        hr.record_edit("e1", "Alice", 0.9)
        md = hr.render_honor_roll()
        assert md.startswith("#")
        assert "| 排名 | 编辑者 | 编辑次数 | 平均分 |" in md
        assert "---" in md
        assert "_轻如烟 · 丰碑网络 自动生成_" in md

    # ─── Clear ───────────────────────────────────────────────

    def test_clear(self):
        hr = HonorRoll()
        hr.record_edit("e1", "Alice", 0.9)
        hr.record_review("r1", "Bob", 0.8)
        assert hr.total_editors == 1
        assert hr.total_reviewers == 1
        hr.clear()
        assert hr.total_editors == 0
        assert hr.total_reviewers == 0

    # ─── 独立记录 ─────────────────────────────────────────

    def test_separate_stats(self):
        """编辑和评审应独立统计"""
        hr = HonorRoll()
        hr.record_edit("user-1", "Alice", 0.8)
        hr.record_review("user-1", "Alice", 0.9)
        # 同一个 ID 在不同榜单上
        editors = hr.get_editor_rankings()
        reviewers = hr.get_reviewer_rankings()
        assert len(editors) == 1
        assert len(reviewers) == 1
        assert editors[0]["id"] == "user-1"
        assert reviewers[0]["id"] == "user-1"

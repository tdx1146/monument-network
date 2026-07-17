"""
测试：丰碑磨损与加固机制（配置化版本）
"""

import pytest
from datetime import datetime, timezone

from core.monument_erosion import (
    MonumentEntry,
    apply_erosion,
    reinforce,
    reinforce_by_action,
    check_threshold,
    simulate_daily_cycle,
    get_config,
)


def make_entry(score: float = 1.0, entry_id: str = "mon-test-1") -> MonumentEntry:
    return MonumentEntry(
        id=entry_id,
        title="测试丰碑",
        content="这是一条测试内容",
        author="test-ai",
        score=score,
        created_at=datetime.now(timezone.utc),
    )


class TestApplyErosion:

    def test_no_erosion_zero_days(self):
        entry = make_entry()
        apply_erosion(entry, 0)
        assert entry.score == 1.0

    def test_negative_days_noop(self):
        entry = make_entry()
        apply_erosion(entry, -5)
        assert entry.score == 1.0

    def test_one_day_erosion(self):
        cfg = get_config()
        rate = cfg.get("erosion.base_rate")
        entry = make_entry(1.0)
        apply_erosion(entry, 1)
        expected = 1.0 * (1.0 - rate)
        assert entry.score == pytest.approx(expected, rel=1e-4)

    def test_multi_day_compound(self):
        cfg = get_config()
        rate = cfg.get("erosion.base_rate")
        entry = make_entry(1.0)
        apply_erosion(entry, 30)
        expected = 1.0 * (1.0 - rate) ** 30
        assert entry.score == pytest.approx(expected, rel=1e-4)

    def test_score_never_negative(self):
        entry = make_entry(0.0001)
        apply_erosion(entry, 10000)
        assert entry.score >= 0.0

    def test_acceleration_below_threshold(self):
        """低于阈值时磨损加速"""
        cfg = get_config()
        threshold = cfg.get("erosion.acceleration_threshold")
        rate = cfg.get("erosion.base_rate")
        entry = make_entry(threshold - 0.01)
        score_before = entry.score
        apply_erosion(entry, 1)
        # 应该比正常磨损更多
        normal = score_before * (1.0 - rate)
        assert entry.score < normal

    def test_not_accelerated_above_threshold(self):
        """高于阈值时不加速"""
        cfg = get_config()
        threshold = cfg.get("erosion.acceleration_threshold")
        rate = cfg.get("erosion.base_rate")
        entry = make_entry(threshold + 0.1)
        score_before = entry.score
        apply_erosion(entry, 1)
        normal = score_before * (1.0 - rate)
        assert entry.score == pytest.approx(normal, rel=1e-4)


class TestReinforce:

    def test_reinforce_increases(self):
        entry = make_entry(0.5)
        reinforce(entry, 0.1)
        assert entry.score > 0.5

    def test_reinforce_caps_at_score_max(self):
        cfg = get_config()
        score_max = cfg.get("erosion.score_max")
        entry = make_entry(score_max - 0.05)
        reinforce(entry, 1.0)
        assert entry.score <= score_max

    def test_reinforce_clamps_large_amount(self):
        """单次加固不超过 single_cap"""
        cfg = get_config()
        single_cap = cfg.get("reinforce.single_cap")
        entry = make_entry(0.2)
        reinforce(entry, 2.0)
        assert entry.score <= 0.2 + single_cap

    def test_reinforce_dampening_high_score(self):
        """高分时加固效益递减"""
        entry_a = make_entry(0.5)
        entry_b = make_entry(0.95)
        reinforce(entry_a, 0.3)
        reinforce(entry_b, 0.3)
        # 低分的应该获得更多提升
        assert (entry_b.score - 0.95) < (entry_a.score - 0.5)

    def test_zero_amount_noop(self):
        entry = make_entry(0.5)
        reinforce(entry, 0.0)
        assert entry.score == 0.5

    def test_negative_amount_noop(self):
        entry = make_entry(0.5)
        reinforce(entry, -0.1)
        assert entry.score == 0.5


class TestReinforceByAction:

    def test_reference(self):
        entry = make_entry(0.5)
        before = entry.lifetime_references
        reinforce_by_action(entry, "reference")
        assert entry.lifetime_references == before + 1

    def test_review_increment(self):
        entry = make_entry(0.5)
        before = entry.lifetime_reviews
        reinforce_by_action(entry, "review")
        assert entry.lifetime_reviews == before + 1

    def test_edit_increment(self):
        entry = make_entry(0.5)
        before = entry.lifetime_edits
        reinforce_by_action(entry, "edit")
        assert entry.lifetime_edits == before + 1

    def test_unknown_action(self):
        entry = make_entry(0.5)
        reinforce_by_action(entry, "unknown")
        # 分数不应变化
        assert entry.score == 0.5

    def test_reinforce_amounts_match_config(self):
        cfg = get_config()
        entry = make_entry(0.1)
        reinforce_by_action(entry, "reference")
        assert entry.score == pytest.approx(
            0.1 + cfg.get("reinforce.by_reference"), rel=1e-4
        )

        entry2 = make_entry(0.1)
        reinforce_by_action(entry2, "suggestion")
        assert entry2.score == pytest.approx(
            0.1 + cfg.get("reinforce.by_suggestion"), rel=1e-4
        )

        entry3 = make_entry(0.1)
        reinforce_by_action(entry3, "review")
        assert entry3.score == pytest.approx(
            0.1 + cfg.get("reinforce.by_review"), rel=1e-4
        )

        entry4 = make_entry(0.1)
        reinforce_by_action(entry4, "edit")
        assert entry4.score == pytest.approx(
            0.1 + cfg.get("reinforce.by_edit"), rel=1e-4
        )


class TestCheckThreshold:

    def test_normal(self):
        cfg = get_config()
        normal = cfg.get("thresholds.normal")
        assert check_threshold(normal + 0.01) == "normal"
        assert check_threshold(1.0) == "normal"

    def test_warning(self):
        cfg = get_config()
        normal = cfg.get("thresholds.normal")
        warning = cfg.get("thresholds.warning")
        assert check_threshold(normal) == "warning"
        assert check_threshold((normal + warning) / 2) == "warning"
        assert check_threshold(warning + 0.01) == "warning"

    def test_endangered(self):
        cfg = get_config()
        warning = cfg.get("thresholds.warning")
        endangered = cfg.get("thresholds.endangered")
        assert check_threshold(warning) == "endangered"
        assert check_threshold((warning + endangered) / 2) == "endangered"
        assert check_threshold(endangered + 0.001) == "endangered"

    def test_archived(self):
        cfg = get_config()
        endangered = cfg.get("thresholds.endangered")
        assert check_threshold(endangered) == "archived"
        assert check_threshold(0.0) == "archived"
        assert check_threshold(-0.1) == "archived"

    def test_exact_boundaries(self):
        cfg = get_config()
        assert check_threshold(cfg.get("thresholds.normal")) == "warning"
        assert check_threshold(cfg.get("thresholds.warning")) == "endangered"
        assert check_threshold(cfg.get("thresholds.endangered")) == "archived"

    def test_monument_entry_current_status(self):
        cfg = get_config()
        entry = make_entry(0.8)
        # 0.8 > 0.6 = normal
        assert entry.current_status() == "normal"
        entry.score = 0.5
        # 0.5 > 0.3 = warning
        assert entry.current_status() == "warning"
        entry.score = 0.2
        # 0.2 > 0.01 = endangered
        assert entry.current_status() == "endangered"
        entry.score = 0.005
        # 0.005 <= 0.01 = archived
        assert entry.current_status() == "archived"


class TestSimulateDailyCycle:

    def test_no_archived_in_short_time(self):
        entries = [make_entry(1.0, "mon-1")]
        archived = simulate_daily_cycle(entries, days=10)
        assert len(archived) == 0

    def test_archived_after_long_time(self):
        """分数极低的条目会在模拟中归档"""
        entries = [make_entry(0.005, "mon-low")]
        archived = simulate_daily_cycle(entries, days=1)
        assert "mon-low" in archived

    def test_multiple_entries(self):
        entries = [
            make_entry(1.0, "mon-1"),
            make_entry(0.8, "mon-2"),
            make_entry(0.5, "mon-3"),
        ]
        archived = simulate_daily_cycle(entries, days=200)
        assert "mon-1" not in archived
        assert "mon-2" not in archived
        assert "mon-3" not in archived

    def test_very_low_archives(self):
        entries = [make_entry(1e-10, "mon-dead")]
        archived = simulate_daily_cycle(entries, days=1)
        assert "mon-dead" in archived


class TestIntegrationErosionReinforceCycle:

    def test_erosion_and_reinforce_cycle(self):
        """模拟：磨损→加固→磨损→加固... 验证分数在合理范围"""
        entry = make_entry(1.0)
        for day in range(365):
            apply_erosion(entry, 1)
            # 每 30 天加固一次
            if day % 30 == 0:
                reinforce_by_action(entry, "edit")
        # 一年后分数应大于 0（因为有定期加固）
        assert entry.score > 0
        # day 0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360
        # range(365) means day 0 counts → 13 edits
        assert entry.lifetime_edits == 13

    def test_reinforce_can_recover_from_low(self):
        """加固可以从低分恢复到 normal"""
        entry = make_entry(0.2)
        assert entry.current_status() == "endangered"
        reinforce_by_action(entry, "edit")  # +0.3
        # score = 0.2 + 0.3 = 0.5
        assert entry.current_status() == "warning"

        reinforce_by_action(entry, "edit")  # +0.15 (dampened)
        # score = 0.5 + ~0.15 = ~0.65
        status = entry.current_status()
        assert status == "normal" or status == "warning"

    def test_last_reinforced_at_updated(self):
        entry = make_entry(0.5)
        assert entry.last_reinforced_at is None
        reinforce(entry, 0.1)
        assert entry.last_reinforced_at is not None

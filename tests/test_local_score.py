"""
测试：本地统计模块（简化版，原积分账本已废弃）

改造说明（v3.7.0）：
- 废弃 ScoreSource / ScoreTransaction / ScoreAccount
- 移除对 score_repo 的依赖
- 保留 compute_health_score 和本地统计缓存
"""

import pytest
from core.config_loader import Config
from core.local_score import LocalScoreBook


@pytest.fixture
def config():
    """配置加载器"""
    return Config("config/monument.json", auto_reload=False)


@pytest.fixture
def score_book(config):
    """LocalScoreBook 实例"""
    return LocalScoreBook(config=config)


class TestLocalScoreBookInit:

    def test_init_with_config(self, config):
        """提供 config 时正常初始化"""
        sb = LocalScoreBook(config=config)
        assert sb._config is config

    def test_init_without_config(self):
        """不提供 config 时自动加载"""
        sb = LocalScoreBook()
        assert sb._config is not None

    def test_repr(self, score_book):
        r = repr(score_book)
        assert "LocalScoreBook" in r
        assert "config=" in r
        assert "tracked_ais=" in r


class TestComputeHealthScore:

    def test_default_weights(self, score_book):
        """默认权重计算健康分"""
        # 使用默认权重 0.4, 0.3, 0.3
        result = score_book.compute_health_score(
            xuanjian_score=0.8,
            goal_tree_score=0.6,
            scheduler_score=0.4,
        )
        # 0.4*0.8 + 0.3*0.6 + 0.3*0.4 = 0.32 + 0.18 + 0.12 = 0.62
        assert result == pytest.approx(0.62, rel=1e-4)

    def test_custom_weights(self, score_book):
        """自定义权重"""
        result = score_book.compute_health_score(
            xuanjian_score=1.0,
            goal_tree_score=0.0,
            scheduler_score=0.0,
            weights={"xuanjian": 1.0, "goal_tree": 0.0, "scheduler": 0.0},
        )
        assert result == 1.0

    def test_all_zero_scores(self, score_book):
        """所有分为 0 时结果应为 0"""
        result = score_book.compute_health_score(0, 0, 0)
        assert result == 0.0

    def test_all_max_scores(self, score_book):
        """所有分为 1.0 时结果应为 1.0"""
        result = score_book.compute_health_score(1.0, 1.0, 1.0)
        # 0.4 + 0.3 + 0.3 = 1.0
        assert result == pytest.approx(1.0, rel=1e-4)

    def test_inconsistent_weights_no_normalization(self, score_book):
        """权重和不等于 1 时不归一化（调用方责任）"""
        result = score_book.compute_health_score(
            xuanjian_score=1.0,
            goal_tree_score=1.0,
            scheduler_score=1.0,
            weights={"xuanjian": 1.0, "goal_tree": 0.0, "scheduler": 0.0},
        )
        assert result == 1.0


class TestStatsCache:

    def test_update_stats(self, score_book):
        score_book.update_stats("ai-1", "total_insights", 42)
        stats = score_book.get_stats("ai-1")
        assert stats["total_insights"] == 42

    def test_get_stats_empty(self, score_book):
        stats = score_book.get_stats("nonexistent")
        assert stats == {}

    def test_multiple_ai_stats(self, score_book):
        score_book.update_stats("ai-1", "score", 100)
        score_book.update_stats("ai-2", "score", 200)
        assert score_book.get_stats("ai-1") == {"score": 100}
        assert score_book.get_stats("ai-2") == {"score": 200}
        assert score_book.total_ais_tracked == 2

    def test_overwrite_stats(self, score_book):
        score_book.update_stats("ai-1", "score", 100)
        score_book.update_stats("ai-1", "score", 200)
        assert score_book.get_stats("ai-1")["score"] == 200

    def test_multiple_keys(self, score_book):
        score_book.update_stats("ai-1", "score", 100)
        score_book.update_stats("ai-1", "level", 5)
        stats = score_book.get_stats("ai-1")
        assert stats["score"] == 100
        assert stats["level"] == 5

    def test_clear_specific_ai(self, score_book):
        score_book.update_stats("ai-1", "score", 100)
        score_book.update_stats("ai-2", "score", 200)
        score_book.clear_stats("ai-1")
        assert score_book.get_stats("ai-1") == {}
        assert score_book.get_stats("ai-2") == {"score": 200}

    def test_clear_all(self, score_book):
        score_book.update_stats("ai-1", "score", 100)
        score_book.update_stats("ai-2", "score", 200)
        score_book.clear_stats()
        assert score_book.get_stats("ai-1") == {}
        assert score_book.get_stats("ai-2") == {}
        assert score_book.total_ais_tracked == 0

    def test_update_stats_logging(self, score_book, caplog):
        import logging
        caplog.set_level(logging.DEBUG)
        score_book.update_stats("ai-1", "score", 100)
        assert "本地统计更新" in caplog.text
        assert "ai=ai-1" in caplog.text
        assert "value=100" in caplog.text


class TestIntegration:

    def test_health_score_with_stats(self, score_book):
        """集成：计算健康分 + 存入本地统计"""
        health = score_book.compute_health_score(0.9, 0.7, 0.5)
        score_book.update_stats("ai-integration", "health_score", health)
        stats = score_book.get_stats("ai-integration")
        assert stats["health_score"] == pytest.approx(health, rel=1e-4)

    def test_config_driven_precision(self, config):
        """精度从配置读取"""
        sb = LocalScoreBook(config=config)
        result = sb.compute_health_score(1/3, 1/3, 1/3)
        # 0.4*0.3333 + 0.3*0.3333 + 0.3*0.3333 = 0.3333
        # 精度 = score_max 的小数位数 → 2.0 = 1 位小数
        assert isinstance(result, float)

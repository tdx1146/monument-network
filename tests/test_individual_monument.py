"""
测试：个体丰碑核心功能 + 玄鉴集成 (F135)
"""

import pytest
from unittest.mock import MagicMock, PropertyMock

from core.individual_monument import IndividualMonument


class TestIndividualMonument:

    def test_init(self):
        """创建时应有正确的初始状态"""
        m = IndividualMonument("test-ai-1")
        ident = m.data["identity"]
        assert ident["ai_id"] == "test-ai-1"
        assert ident["status"] == "alive"
        assert ident["died_at"] is None
        assert ident["born_at"] is not None

    def test_write_draft(self):
        m = IndividualMonument("test-ai-1")
        idx = m.write_draft("一条草稿", {"source": "chat"})
        assert idx == 0
        assert m.data["life_record"]["total_insights"] == 1
        assert len(m.data["monuments"]["drafts"]) == 1
        assert m.data["monuments"]["drafts"][0]["content"] == "一条草稿"

    def test_write_candidate(self):
        m = IndividualMonument("test-ai-1")
        idx = m.write_candidate("一条候选", {"score": 0.85})
        assert idx == 0
        assert len(m.data["monuments"]["candidates"]) == 1

    def test_finalize(self):
        m = IndividualMonument("test-ai-1")
        m.write_candidate("值得铭记的洞察")
        entry = m.finalize(0)
        assert entry["type"] == "finalized"
        assert "finalized_at" in entry
        assert len(m.data["monuments"]["candidates"]) == 0
        assert len(m.data["monuments"]["finalized"]) == 1

    def test_finalize_out_of_range(self):
        m = IndividualMonument("test-ai-1")
        with pytest.raises(IndexError):
            m.finalize(99)

    def test_freeze(self):
        m = IndividualMonument("test-ai-1")
        m.write_draft("一些内容")
        hash_val = m.freeze()
        assert m.data["identity"]["status"] == "frozen"
        assert m.data["freeze_proof"]["hash"] is not None
        assert m.data["freeze_proof"]["frozen_at"] is not None
        assert isinstance(hash_val, str) and len(hash_val) == 64

    def test_freeze_twice_raises(self):
        m = IndividualMonument("test-ai-1")
        m.freeze()
        with pytest.raises(ValueError, match="already frozen"):
            m.freeze()

    def test_write_after_freeze_raises(self):
        m = IndividualMonument("test-ai-1")
        m.freeze()
        with pytest.raises(ValueError, match="Monument is frozen"):
            m.write_draft("新的草稿")
        with pytest.raises(ValueError, match="Monument is frozen"):
            m.write_candidate("新的候选")
        with pytest.raises(ValueError, match="Monument is frozen"):
            m.finalize(0)

    def test_to_dict_and_from_dict_roundtrip(self):
        m = IndividualMonument("test-roundtrip")
        m.write_draft("持久化测试")
        m.write_candidate("候选测试")
        d = m.to_dict()
        m2 = IndividualMonument.from_dict(d)
        assert m2.data["identity"]["ai_id"] == "test-roundtrip"
        assert len(m2.data["monuments"]["drafts"]) == 1
        assert len(m2.data["monuments"]["candidates"]) == 1

    def test_to_json(self):
        m = IndividualMonument("test-json")
        js = m.to_json()
        assert '"ai_id": "test-json"' in js
        assert '"status": "alive"' in js

    def test_repr(self):
        m = IndividualMonument("repr-test")
        assert "repr-test" in repr(m)
        assert "alive" in repr(m)

    # ── 冻结后 write_candidate_scored 也应抛出 ────────────

    def test_write_candidate_scored_after_freeze_raises(self):
        m = IndividualMonument("test-ai-1")
        m.freeze()
        with pytest.raises(ValueError, match="Monument is frozen"):
            m.write_candidate_scored("测试内容", xuanjian_confidence=0.9)

    # ── 玄鉴集成：正常创建 ─────────────────────────────────

    def test_write_candidate_scored_high_confidence(self):
        """置信度 >= 0.8，应成功创建候选"""
        m = IndividualMonument("test-ai-1")

        # Mock 玄鉴管道
        mock_pipe = MagicMock()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "insight_id": "insight-test123",
            "ai_id": "test-ai-1",
            "raw_text": "高质量方法论洞察：可迁移的设计模式在跨项目中表现出一致性",
            "confidence": 0.85,
            "monument_score": 0.85,
            "time_binding": 0.3,
            "transferability": 0.7,
            "abstraction_level": 0.8,
            "is_candidate": True,
            "is_increment": True,
            "pattern_key": "方法论_洞察",
            "pattern_count": 2,
        }
        mock_pipe.evaluate.return_value = mock_result

        entry_id, analysis = m.write_candidate_scored(
            content="高质量方法论洞察：可迁移的设计模式在跨项目中表现出一致性",
            xuanjian_confidence=0.85,
            xuanjian_pipe=mock_pipe,
        )

        assert entry_id == 0, "应成功创建，返回有效 ID"
        assert analysis is not None
        assert analysis["monument_score"] == 0.85
        assert analysis["insight_id"] == "insight-test123"

        # 验证条目中的玄鉴数据
        entry = m.data["monuments"]["candidates"][0]
        assert "xuanjian_score" in entry
        assert entry["xuanjian_score"] == 0.85
        assert "xuanjian_analysis" in entry

        # 验证玄鉴管道被正确调用
        mock_pipe.evaluate.assert_called_once()
        call_kwargs = mock_pipe.evaluate.call_args.kwargs or mock_pipe.evaluate.call_args[1]
        # 兼容位置参数或关键字参数
        assert mock_pipe.evaluate.called

    # ── 玄鉴集成：拒绝创建 ─────────────────────────────────

    def test_write_candidate_scored_low_confidence(self):
        """置信度 < 0.8，应拒绝创建"""
        m = IndividualMonument("test-ai-1")

        mock_pipe = MagicMock()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "insight_id": "insight-low",
            "ai_id": "test-ai-1",
            "raw_text": "普通日志信息",
            "confidence": 0.5,
            "monument_score": 0.35,
            "time_binding": 0.8,
            "transferability": 0.2,
            "abstraction_level": 0.1,
            "is_candidate": False,
            "is_increment": False,
            "pattern_key": "普通_日志",
            "pattern_count": 0,
        }
        mock_pipe.evaluate.return_value = mock_result

        entry_id, analysis = m.write_candidate_scored(
            content="普通日志信息",
            xuanjian_confidence=0.5,
            xuanjian_pipe=mock_pipe,
        )

        assert entry_id == -1, "置信度不足，应返回 -1"
        assert analysis is not None
        assert analysis["error_type"] == "low_confidence"
        assert analysis["monument_score"] == 0.35
        assert analysis["threshold"] == 0.8

        # 验证没有创建候选条目
        assert len(m.data["monuments"]["candidates"]) == 0

    # ── 玄鉴集成：评分失败处理 ────────────────────────────

    def test_write_candidate_scored_pipeline_failure(self):
        """玄鉴管道异常，应返回错误"""
        m = IndividualMonument("test-ai-1")

        mock_pipe = MagicMock()
        mock_pipe.evaluate.side_effect = RuntimeError("数据库连接失败")

        entry_id, analysis = m.write_candidate_scored(
            content="洞察内容",
            xuanjian_confidence=0.9,
            xuanjian_pipe=mock_pipe,
        )

        assert entry_id == -1, "管道异常，应返回 -1"
        assert analysis is not None
        assert analysis["error_type"] == "pipeline_failure"
        assert "数据库连接失败" in analysis["error"]

        # 验证没有创建候选条目
        assert len(m.data["monuments"]["candidates"]) == 0

    # ── 玄鉴集成：无管道时兜底 ────────────────────────────

    def test_write_candidate_scored_no_pipe(self):
        """未提供玄鉴管道，应走原始路径"""
        m = IndividualMonument("test-ai-1")

        entry_id, analysis = m.write_candidate_scored(
            content="洞察内容",
            xuanjian_confidence=0.9,
            xuanjian_pipe=None,
        )

        assert entry_id == 0, "无管道时兜底创建候选"
        assert analysis is None, "无管道时分析结果为 None"
        assert len(m.data["monuments"]["candidates"]) == 1

    # ── 玄鉴集成：冻结后阻止 ──────────────────────────────

    def test_write_candidate_scored_frozen(self):
        m = IndividualMonument("test-ai-1")
        m.freeze()

        mock_pipe = MagicMock()
        with pytest.raises(ValueError, match="Monument is frozen"):
            m.write_candidate_scored(
                content="任何内容",
                xuanjian_confidence=0.9,
                xuanjian_pipe=mock_pipe,
            )

        # 管道不应被调用
        mock_pipe.evaluate.assert_not_called()

    # ── 玄鉴集成：最近分析记录 ────────────────────────────

    def test_get_last_xuanjian_analysis(self):
        """验证最近一次玄鉴分析被正确记录"""
        m = IndividualMonument("test-ai-1")

        # 初始应为 None
        assert m.get_last_xuanjian_analysis() is None

        mock_pipe = MagicMock()
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "insight_id": "insight-last",
            "monument_score": 0.92,
            "time_binding": 0.2,
            "transferability": 0.9,
            "abstraction_level": 0.8,
            "is_candidate": True,
            "is_increment": True,
            "pattern_key": "测试_分析",
            "pattern_count": 1,
        }
        mock_pipe.evaluate.return_value = mock_result

        entry_id, analysis = m.write_candidate_scored(
            content="优秀方法论洞察",
            xuanjian_confidence=0.95,
            xuanjian_pipe=mock_pipe,
        )

        # 验证提取方法
        last = m.get_last_xuanjian_analysis()
        assert last is not None
        assert last["monument_score"] == 0.92

        # 验证实例属性
        assert m.last_xuanjian_analysis is not None
        assert m.last_xuanjian_analysis["insight_id"] == "insight-last"

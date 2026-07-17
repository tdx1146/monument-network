"""
Individual Monument — 个体丰碑核心数据结构

每个 AI 个体拥有一座丰碑，记录其生命周期、洞察成果和冻结证明。

玄鉴集成 (F135)：write_candidate_scored() 调用玄鉴评分管道，
置信度 >= 0.8 才允许创建候选丰碑。
write_candidate() 保持原始接口不变，向后兼容。
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.xuanjian_pipe import XuanjianPipe, InsightAnalysis

from core.config_loader import Config

logger = logging.getLogger(__name__)


class IndividualMonument:
    """个体丰碑——一个 AI 个体的完整生命周期记录"""

    def __init__(self, ai_id: str) -> None:
        self.data: dict[str, Any] = {
            "identity": {
                "ai_id": ai_id,
                "born_at": _now_iso(),
                "died_at": None,
                "status": "alive",
            },
            "life_record": {
                "total_conversations": 0,
                "total_insights": 0,
                "score_history": [],
            },
            "monuments": {
                "drafts": [],
                "candidates": [],
                "finalized": [],
            },
            "freeze_proof": {
                "hash": None,
                "frozen_at": None,
            },
        }
        # 存储最近一次玄鉴分析结果（如果有）
        self.last_xuanjian_analysis: Optional[dict[str, Any]] = None

        # 配置实例 — 从配置系统读取阈值
        self._config = Config.get_instance()
        self._min_confidence = self._config.get("scoring.min_confidence", 0.8)

    # ── 写入方法 ──────────────────────────────────────────

    def write_draft(self, content: str, metadata: Optional[dict[str, Any]] = None) -> int:
        """写入草稿。返回该条目的索引 ID。"""
        self._assert_not_frozen()
        entry = {
            "id": len(self.data["monuments"]["drafts"]),
            "type": "draft",
            "content": content,
            "metadata": metadata or {},
            "created_at": _now_iso(),
        }
        self.data["monuments"]["drafts"].append(entry)
        self.data["life_record"]["total_insights"] += 1
        return entry["id"]

    def write_candidate(self, content: str, metadata: Optional[dict[str, Any]] = None) -> int:
        """
        写入候选丰碑（原始接口，无玄鉴评分）。

        返回该条目的索引 ID。
        保持向后兼容——所有现有调用者无需修改。
        """
        self._assert_not_frozen()
        entry = {
            "id": len(self.data["monuments"]["candidates"]),
            "type": "candidate",
            "content": content,
            "metadata": metadata or {},
            "created_at": _now_iso(),
        }
        self.data["monuments"]["candidates"].append(entry)
        self.data["life_record"]["total_insights"] += 1
        return entry["id"]

    def write_candidate_scored(
        self,
        content: str,
        xuanjian_confidence: float,
        metadata: Optional[dict[str, Any]] = None,
        xuanjian_pipe: Optional[Any] = None,
        xuanjian_source: Optional[Any] = None,
    ) -> tuple[int, Optional[dict[str, Any]]]:
        """
        写入候选丰碑（带玄鉴评分）。(F118-F119 归一化)

        调用玄鉴评分管道验证。统一使用 monument_score（三轴综合得分）
        而非外部传入的 xuanjian_confidence 做决策。
        仅 monument_score >= 0.8 时才创建候选。

        参数：
            content:            洞察/碑文内容
            xuanjian_confidence:外部输入的玄鉴置信度 (0.0~1.0)，仅透传至管道
            metadata:           附加元数据
            xuanjian_pipe:      XuanjianPipe 实例（可选，不提供时跳过评分）
            xuanjian_source:    来源元数据 (InsightSource)

        返回 (entry_id, xuanjian_analysis) 二元组：
            entry_id:  成功时返回索引 ID（>= 0），失败时返回 -1
            xuanjian_analysis: 玄鉴分析结果字典，或错误信息字典

        错误类型：
            error_type == "pipeline_failure": 玄鉴管道异常
            error_type == "low_confidence":   monument_score < 0.8
        """
        self._assert_not_frozen()

        # ── 玄鉴评分 ──
        analysis_dict = self._run_xuanjian_evaluation(
            content=content,
            xuanjian_confidence=xuanjian_confidence,
            xuanjian_pipe=xuanjian_pipe,
            xuanjian_source=xuanjian_source,
        )

        if analysis_dict is None:
            # 没有玄鉴管道，走原始路径（兜底，不阻止）
            entry_id = self._append_candidate_entry(content, metadata, xuanjian_score=None)
            return (entry_id, None)

        # 检查是否有错误
        if "error" in analysis_dict:
            self.last_xuanjian_analysis = analysis_dict
            return (-1, analysis_dict)

        # ── 检查 monument_score（三轴综合得分），而非外部 confidence ──
        monument_score = analysis_dict.get("monument_score", 0.0)
        if monument_score < self._min_confidence:
            result = {
                "error": "候选丰碑置信度不足",
                "monument_score": monument_score,
                "threshold": self._min_confidence,
                "time_binding": analysis_dict.get("time_binding"),
                "transferability": analysis_dict.get("transferability"),
                "abstraction_level": analysis_dict.get("abstraction_level"),
                "error_type": "low_confidence",
            }
            self.last_xuanjian_analysis = result
            return (-1, result)

        # ── monument_score 达标，创建候选 ──
        entry_id = self._append_candidate_entry(
            content, metadata, xuanjian_score=monument_score
        )

        # 将评分详情附加到条目
        self.data["monuments"]["candidates"][entry_id]["xuanjian_analysis"] = {
            "insight_id": analysis_dict.get("insight_id"),
            "monument_score": monument_score,
            "time_binding": analysis_dict.get("time_binding"),
            "transferability": analysis_dict.get("transferability"),
            "abstraction_level": analysis_dict.get("abstraction_level"),
            "is_candidate": analysis_dict.get("is_candidate"),
            "is_increment": analysis_dict.get("is_increment"),
            "pattern_key": analysis_dict.get("pattern_key"),
            "pattern_count": analysis_dict.get("pattern_count"),
        }

        self.last_xuanjian_analysis = analysis_dict
        return (entry_id, analysis_dict)

    def _run_xuanjian_evaluation(
        self,
        content: str,
        xuanjian_confidence: float,
        xuanjian_pipe: Optional[Any],
        xuanjian_source: Optional[Any],
    ) -> Optional[dict[str, Any]]:
        """
        内部方法：执行玄鉴评分，返回分析结果字典。
        (F118-F119 归一化：统一使用 monument_score 而非外部 confidence)

        确保管道始终运行三轴评分：
        - 如果外部 confidence < self._min_confidence，
          仍传入足够值触发三轴判别，然后使用 monument_score 做决策
        - 传入 max(confidence, 0.8) 保证三轴跑满

        返回 None 表示无需评分（没有 pipe），
        返回字典包含 'error' 键表示评分失败，
        否则包含完整分析结果。
        """
        if xuanjian_pipe is None:
            return None  # 未启用玄鉴，走原始路径

        ai_id = self.data["identity"]["ai_id"]

        # F118: 确保三轴判别始终运行，不受外部 confidence 门限阻塞
        # 传入较高值确保通过 evaluate() 的置信度前置检查
        pipe_confidence = max(xuanjian_confidence, self._min_confidence)

        try:
            result = xuanjian_pipe.evaluate(
                ai_id=ai_id,
                text=content,
                confidence=pipe_confidence,
                source=xuanjian_source,
            )
            return result.to_dict()
        except Exception as e:
            logger.error("玄鉴评分管道异常: %s", e)
            return {
                "error": f"玄鉴评分管道异常: {e}",
                "error_type": "pipeline_failure",
            }

    def _append_candidate_entry(
        self,
        content: str,
        metadata: Optional[dict[str, Any]],
        xuanjian_score: Optional[float],
    ) -> int:
        """内部方法：追加候选条目，返回索引 ID。"""
        entry: dict[str, Any] = {
            "id": len(self.data["monuments"]["candidates"]),
            "type": "candidate",
            "content": content,
            "metadata": metadata or {},
            "created_at": _now_iso(),
        }
        if xuanjian_score is not None:
            entry["xuanjian_score"] = xuanjian_score

        self.data["monuments"]["candidates"].append(entry)
        self.data["life_record"]["total_insights"] += 1
        return entry["id"]

    # ── 玄鉴查询 ──────────────────────────────────────────

    def get_last_xuanjian_analysis(self) -> Optional[dict[str, Any]]:
        """获取最近一次玄鉴分析结果。"""
        return self.last_xuanjian_analysis

    # ── 最终化 ────────────────────────────────────────────

    def finalize(self, index: int) -> dict[str, Any]:
        """将指定的候选丰碑提升为正式丰碑。返回被提升的条目。"""
        self._assert_not_frozen()
        candidates = self.data["monuments"]["candidates"]
        if index < 0 or index >= len(candidates):
            raise IndexError(f"Candidate index {index} out of range (0..{len(candidates) - 1})")
        entry = candidates.pop(index)
        entry["type"] = "finalized"
        entry["finalized_at"] = _now_iso()
        self.data["monuments"]["finalized"].append(entry)
        self.data["life_record"]["total_insights"] += 1
        return entry

    # ── 冻结 ──────────────────────────────────────────────

    def freeze(self) -> str:
        """冻结丰碑：计算整体 SHA-256 哈希，写入证明，状态置为 frozen。"""
        if self.data["freeze_proof"]["hash"] is not None:
            raise ValueError("Monument is already frozen")
        self._assert_not_frozen()

        now = _now_iso()
        # 对 identity + life_record + monuments 序列化后取 hash
        payload = {
            "identity": self.data["identity"],
            "life_record": self.data["life_record"],
            "monuments": self.data["monuments"],
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        hash_val = hashlib.sha256(raw).hexdigest()

        self.data["identity"]["status"] = "frozen"
        self.data["freeze_proof"]["hash"] = hash_val
        self.data["freeze_proof"]["frozen_at"] = now
        return hash_val

    # ── 序列化 ────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """返回深拷贝字典。"""
        return dict(self.data)

    def to_json(self, indent: int = 2) -> str:
        """返回 JSON 字符串。"""
        return json.dumps(self.data, ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndividualMonument":
        """从字典恢复实例。"""
        ai_id = data.get("identity", {}).get("ai_id", "")
        monument = cls.__new__(cls)
        monument.data = data
        monument.last_xuanjian_analysis = None
        return monument

    # ── 内部 ──────────────────────────────────────────────

    def _assert_not_frozen(self) -> None:
        if self.data["identity"]["status"] == "frozen":
            raise ValueError("Monument is frozen")

    def __repr__(self) -> str:
        ident = self.data["identity"]
        return (
            f"<IndividualMonument ai_id={ident['ai_id']!r} "
            f"status={ident['status']!r}>"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

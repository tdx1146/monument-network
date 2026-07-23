"""
Xuanjian Pipe — 玄鉴评分管道 (F118-F119 归一化)

三轴判别算法：
  轴 1：时间绑定度（time_binding）
    - 无条件贡献：(1.0 - time_binding) * 0.3
    - 低绑定（<= 0.5）时贡献大，高绑定（> 0.5）时贡献小但不为零
  轴 2：可迁移性（transferability）
    - 无条件贡献：transferability * 0.4
    - 高迁移（>= 0.5）时贡献大，低迁移时贡献小但不为零
  轴 3：抽象层级（abstraction_level）
    - 无条件贡献：abstraction_level * 0.3
    - 高层级（>= 0.5）时贡献大，低层级时贡献小但不为零

综合置信度 = 轴1贡献 + 轴2贡献 + 轴3贡献
  - 默认文本（无关键词）得分约 0.30
  - >= 0.8 → 触发丰碑候选

触发流程：
  1. 玄鉴输出置信度 >= XUANJIAN_MIN_CONFIDENCE
  2. 三轴判别算法计算
  3. 综合置信度 >= 0.8 → 创建候选
  4. 写入 candidates/ 目录
  5. 模式匹配 >= CANDIDATE_THRESHOLD_COUNT → 额外标记
"""

import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import (
    XUANJIAN_MIN_CONFIDENCE,
    CANDIDATE_THRESHOLD_COUNT,
    TIME_BINDING_WEIGHT,
    TRANSFERABILITY_WEIGHT,
    ABSTRACTION_WEIGHT,
    CANDIDATES_DIR,
)

from db.xuanjian_repo import XuanjianRepository


# ─── 数据结构 ──────────────────────────────────────────


@dataclass
class InsightSource:
    """洞察来源元数据"""
    source_type: str = "manual"  # "digestion_cycle" | "daily_note" | "self_pulse" | "manual"
    session_id: str = ""
    conversation_id: str = ""


@dataclass
class ThreeAxisScore:
    """三轴判别结果"""
    time_binding: float = 0.0          # 时间绑定度 0.0~1.0（低=方法论洞见）
    transferability: float = 0.0       # 可迁移性 0.0~1.0（高=方法论洞见）
    abstraction_level: float = 0.0     # 抽象层级 0.0~1.0（高=方法论洞见）

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class InsightAnalysis:
    """一次完整的玄鉴分析结果"""
    insight_id: str = field(default_factory=lambda: f"insight-{uuid.uuid4().hex[:12]}")
    ai_id: str = ""

    # 原始玄鉴输出
    raw_text: str = ""
    confidence: float = 0.0            # 0.0 ~ 1.0

    # 三轴判别
    time_binding: float = 0.0          # 时间绑定度 0.0~1.0
    transferability: float = 0.0       # 可迁移性 0.0~1.0
    abstraction_level: float = 0.0     # 抽象层级 0.0~1.0

    # 综合得分
    monument_score: float = 0.0        # 综合得分 0.0~1.0

    # 决策
    is_candidate: bool = False         # 是否触发候选
    is_increment: bool = False         # 是否仅+1积分（>=0.8但非候选）

    # 模式匹配
    pattern_key: str = ""              # 同类模式摘要
    pattern_count: int = 0             # 当前同类模式出现次数

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── 管道实现 ──────────────────────────────────────────


class XuanjianPipe:
    """玄鉴评分管道——接收入站玄鉴结果，执行三轴判别，触发候选"""

    def __init__(self) -> None:
        self.repo = XuanjianRepository()
        self.repo.ensure_table()

    # ── 公开接口 ────────────────────────────────────────

    def evaluate(
        self,
        ai_id: str,
        text: str,
        confidence: float,
        source: Optional[InsightSource] = None,
    ) -> InsightAnalysis:
        """
        全流程：置信度校验 → 三轴判别 → 置信度计算 → 候选触发

        参数：
            ai_id:       AI 标识
            text:        玄鉴输出的原始文本
            confidence:  玄鉴输出置信度（0.0~1.0）
            source:      来源元数据（可选）

        返回：
            InsightAnalysis 完整分析结果
        """
        # Step 1: 置信度校验
        if confidence < XUANJIAN_MIN_CONFIDENCE:
            # 低于阈值，不执行判别
            return InsightAnalysis(
                ai_id=ai_id,
                raw_text=text,
                confidence=confidence,
            )

        # Step 2: 三轴判别
        axes = self.compute_three_axis(text)
        time_binding = axes["time_binding"]
        transferability = axes["transferability"]
        abstraction_level = axes["abstraction_level"]

        # Step 3: 计算综合置信度
        monument_score = self.compute_confidence(time_binding, transferability, abstraction_level)

        # Step 4: 提取模式键
        pattern_key = self._extract_pattern_key(text)

        # Step 5: 查询同类模式出现次数
        pattern_count = self.repo.count_by_pattern(pattern_key)

        # Step 6: 构建分析结果
        analysis = InsightAnalysis(
            ai_id=ai_id,
            raw_text=text,
            confidence=confidence,
            time_binding=time_binding,
            transferability=transferability,
            abstraction_level=abstraction_level,
            monument_score=monument_score,
            is_candidate=False,
            is_increment=False,
            pattern_key=pattern_key,
            pattern_count=pattern_count,
        )

        # Step 7: 持久化评估记录
        evaluation_id = self.repo.create_evaluation(
            ai_id=ai_id,
            time_binding=time_binding,
            transferability=transferability,
            abstraction_level=abstraction_level,
            confidence=monument_score,
            pattern_key=pattern_key,
        )

        # Step 8: 判断是否触发候选
        if monument_score >= XUANJIAN_MIN_CONFIDENCE:
            analysis = self._trigger_candidate(analysis, evaluation_id)

        return analysis

    # ── 三轴判别 ────────────────────────────────────────

    @staticmethod
    def compute_three_axis(text: str) -> dict[str, float]:
        """
        三轴判别算法。
        对文本进行启发式打分，返回三轴得分字典。

        轴 1：时间绑定度（time_binding）
            - 低（< 0.5）= 方法论洞见良好
            - 关键词：通用规律、趋势、模式、始终、每次
        轴 2：可迁移性（transferability）
            - 高（>= 0.5）= 方法论洞见良好
            - 关键词：可迁移、适用、复用、通用、类似场景
        轴 3：抽象层级（abstraction_level）
            - 高（>= 0.5）= 方法论洞见良好
            - 关键词：抽象、模型、原则、架构、范式
        """
        # --- 轴 1：时间绑定度 ---
        # 低时间绑定 = 高阶信号，所以 time_binding 值越小越好
        # 注意：中文文本不支持 \b 单词边界，使用 re.escape 或直接匹配
        time_low_keywords = [
            '始终', '每次', '长期', '持续',
            '规律', '趋势', '模式', '周期',
            '反复', '一直',
            '通用', '原则',
        ]
        time_high_keywords = [
            '此时', '当前', '今天', '临时',
            '近期', '刚才',
        ]

        low_hits = sum(1 for kw in time_low_keywords if kw in text)
        high_hits = sum(1 for kw in time_high_keywords if kw in text)

        # 默认 0.7（中等偏高时间绑定）
        time_binding = 0.7
        # 每命中一个"低绑定"关键词，降低 0.12，最低 0.1
        time_binding = max(0.1, time_binding - low_hits * 0.12)
        # 每命中一个"高绑定"关键词，增加 0.15，最高 1.0
        time_binding = min(1.0, time_binding + high_hits * 0.15)

        # --- 轴 2：可迁移性 ---
        transfer_keywords = [
            '可迁移', '适用', '复用', '通用',
            '类似场景', '类比', '推广', '移植',
            '通用化', '复用性', '跨领域', '跨系统',
            '举一反三', '触类旁通',
        ]
        transfer_hits = sum(1 for kw in transfer_keywords if kw in text)

        # 默认 0.3（中等偏低）
        transferability = 0.3
        # 每命中一个可迁移关键词，增加 0.17，最高 1.0
        transferability = min(1.0, transferability + transfer_hits * 0.17)

        # --- 轴 3：抽象层级 ---
        abstract_keywords = [
            '抽象', '模型', '原则', '架构',
            '范式', '框架', '本质',
            '理论', '概念', '元认知',
            '通用原理', '底层逻辑', '核心机制',
        ]
        abstract_hits = sum(1 for kw in abstract_keywords if kw in text)

        # 默认 0.3（中等偏低）
        abstraction_level = 0.3
        # 每命中一个抽象关键词，增加 0.14，最高 1.0
        abstraction_level = min(1.0, abstraction_level + abstract_hits * 0.14)

        return {
            "time_binding": round(time_binding, 4),
            "transferability": round(transferability, 4),
            "abstraction_level": round(abstraction_level, 4),
        }

    # ── 置信度计算 ──────────────────────────────────────

    @staticmethod
    def compute_confidence(
        time_binding: float,
        transferability: float,
        abstraction_level: float,
    ) -> float:
        """
        综合置信度计算（F118 归一化版本）。

        三轴无条件贡献，去掉条件门限：
        - 轴 1（时间绑定度）：低绑定时为方法论洞见加分
            contribution = (1.0 - time_binding) * weight
            时间绑定度越高（=越临时化），扣分越多，保留负激励
        - 轴 2（可迁移性）：高迁移时为方法论洞见加分
            contribution = transferability * weight
        - 轴 3（抽象层级）：高层级时为方法论洞见加分
            contribution = abstraction_level * weight

        默认文本（无关键词）得分约为 0.30。

        权重比例：轴1:轴2:轴3 = TIME_BINDING_WEIGHT : TRANSFERABILITY_WEIGHT : ABSTRACTION_WEIGHT
            = 0.3 : 0.4 : 0.3
        """
        # 轴 1：时间绑定度 — 无条件贡献，低绑定得分高
        axis1_contrib = (1.0 - time_binding) * TIME_BINDING_WEIGHT

        # 轴 2：可迁移性 — 无条件贡献，高迁移得分高
        axis2_contrib = transferability * TRANSFERABILITY_WEIGHT

        # 轴 3：抽象层级 — 无条件贡献，高层级得分高
        axis3_contrib = abstraction_level * ABSTRACTION_WEIGHT

        confidence = axis1_contrib + axis2_contrib + axis3_contrib
        return round(min(1.0, confidence), 4)

    # ── 候选触发 ────────────────────────────────────────

    def _trigger_candidate(self, analysis: InsightAnalysis, evaluation_id: int) -> InsightAnalysis:
        """
        候选触发逻辑（内部方法）：
        1. monument_score >= XUANJIAN_MIN_CONFIDENCE
        2. 写入 candidates/ 目录
        3. 持久化标记
        4. 模式计数 >= CANDIDATE_THRESHOLD_COUNT → 额外标记
        """
        if analysis.monument_score < XUANJIAN_MIN_CONFIDENCE:
            return analysis

        now = _now_iso()
        analysis.is_candidate = True
        analysis.is_increment = True

        # 构造候选碑文内容
        candidate_data = {
            "insight_id": analysis.insight_id,
            "ai_id": analysis.ai_id,
            "monument_score": analysis.monument_score,
            "time_binding": analysis.time_binding,
            "transferability": analysis.transferability,
            "abstraction_level": analysis.abstraction_level,
            "pattern_key": analysis.pattern_key,
            "created_at": now,
            "raw_text_snippet": analysis.raw_text[:500] if len(analysis.raw_text) > 500 else analysis.raw_text,
        }

        # 写入 candidates/ 目录
        candidates_dir = Path(CANDIDATES_DIR)
        candidates_dir.mkdir(parents=True, exist_ok=True)
        candidate_filename = f"candidate-{analysis.insight_id}.json"
        candidate_path = candidates_dir / candidate_filename
        with open(str(candidate_path), "w", encoding="utf-8") as f:
            json.dump(candidate_data, f, ensure_ascii=False, indent=2)

        # 持久化标记（使用 evaluation_id，而非 insight_id）
        self.repo.mark_candidate(evaluation_id, now)

        # 模式检测：如果同一模式出现 >= 阈值次数，标记
        if analysis.pattern_count >= CANDIDATE_THRESHOLD_COUNT:
            analysis.is_candidate = True
            pattern_filename = f"pattern-{analysis.pattern_key}-{analysis.insight_id}.json"
            pattern_path = candidates_dir / pattern_filename
            with open(str(pattern_path), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "type": "pattern_trigger",
                        "pattern_key": analysis.pattern_key,
                        "pattern_count": analysis.pattern_count,
                        "triggered_at": now,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

        return analysis

    # ── 查询 ────────────────────────────────────────────

    def get_candidates(self, ai_id: str) -> list[dict[str, Any]]:
        """
        查询指定 AI 的高置信度评估列表。
        """
        return self.repo.get_by_ai_id(ai_id)

    def get_high_confidence(self) -> list[dict[str, Any]]:
        """
        查询全系统高置信度评估。
        """
        return self.repo.list_high_confidence(threshold=XUANJIAN_MIN_CONFIDENCE)

    # ── 内部方法 ───────────────────────────────────────

    @staticmethod
    def _extract_pattern_key(text: str) -> str:
        """
        从文本中提取模式键。
        取前 10 个非停用词，用下划线连接，构成低碰撞 key。
        """
        # 简单的中文分词：按标点符号拆分，取单词
        # 停用词
        stop_words = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
            "它", "们", "那", "什么", "怎么", "为什么", "因为", "所以",
            "但", "是", "可以", "能", "被", "把", "让", "给", "对", "从",
            "之", "以", "而", "与", "且", "或", "如果", "虽然", "然而",
        }

        # 去除标点，提取中文字符词
        # 用正则分词：按标点和空白分割
        tokens = re.split(r'[\s,，。！？、；：""''（）【】《》\[\]{}<>/\\|·…—\-+*=@#$%^&()]+', text)
        tokens = [t.strip() for t in tokens if t.strip() and len(t.strip()) >= 2 and t.strip() not in stop_words]

        # 取前 5 个有效词
        key_tokens = tokens[:5]
        if not key_tokens:
            return "generic"

        return "_".join(key_tokens)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

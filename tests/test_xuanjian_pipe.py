#!/usr/bin/env python3
"""
玄鉴评分管道测试

测试覆盖：
1. 三轴计算：各类文本的启发式打分
2. 置信度计算：权重算法
3. 阈值判断：monument_score >= 0.8
4. 候选触发：完整流程
5. 持久化：仓库读写
"""

import json
import sys
import tempfile
from pathlib import Path

# 确保 code/ 在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.xuanjian_pipe import XuanjianPipe, InsightAnalysis
from db.xuanjian_repo import XuanjianRepository
from db.database import init_db, close_db, get_connection


errors = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        errors.append(f"FAIL: {name} — {detail}")
        print(f"  ✗ {name}")
    else:
        print(f"  ✓ {name}")


# ==============================================================
# 测试辅助：清理 xuanjian 表
# ==============================================================

def _clean_xuanjian_table():
    conn = get_connection()
    conn.execute("DELETE FROM xuanjian_evaluations")
    conn.commit()


# ==============================================================
# 1. XuanjianRepository 测试
# ==============================================================

print("\n=== XuanjianRepository ===")

init_db()
repo = XuanjianRepository()
repo.ensure_table()
_clean_xuanjian_table()

# 写入评估
eid = repo.create_evaluation(
    ai_id="test-ai-1",
    time_binding=0.2,
    transferability=0.8,
    abstraction_level=0.7,
    confidence=0.85,
    pattern_key="方法论_备份",
)
check("create_evaluation returns int", isinstance(eid, int))

# 按 ai_id 查询
rows = repo.get_by_ai_id("test-ai-1")
check("get_by_ai_id returns list", len(rows) >= 1)
check("get_by_ai_id confidence match", rows[0]["confidence"] == 0.85)
check("get_by_ai_id time_binding match", rows[0]["time_binding"] == 0.2)

# 查询高置信度
high = repo.list_high_confidence(threshold=0.8)
check("list_high_confidence includes test-ai-1", any(r["ai_id"] == "test-ai-1" for r in high))

# 写入更低置信度的评估，确认高置信度查询正确
repo.create_evaluation(
    ai_id="test-ai-2",
    time_binding=0.8,
    transferability=0.2,
    abstraction_level=0.3,
    confidence=0.3,
)
high2 = repo.list_high_confidence(threshold=0.8)
check("list_high_confidence filters low confidence", all(r["confidence"] >= 0.8 for r in high2))

# 模式计数
cnt = repo.count_by_pattern("方法论_备份")
check("count_by_pattern for existing", cnt >= 1)

cnt2 = repo.count_by_pattern("nonexistent_pattern")
check("count_by_pattern for missing", cnt2 == 0)

# 标记候选
now = "2026-07-11T23:40:00"
ok = repo.mark_candidate(eid, now)
check("mark_candidate returns True", ok is True)
marked = repo.get_by_ai_id("test-ai-1")
check("mark_candidate persisted", marked[0]["is_candidate"] == 1)


# ==============================================================
# 2. 三轴判别测试
# ==============================================================

print("\n=== Three Axis Scoring ===")

pipe = XuanjianPipe()

# --- 2a: 方法论级别文本（低时间绑定、高可迁移、高抽象） ---
methodology_text = (
    "可迁移性高的通用架构模式始终适用于类似场景。"
    "抽象模型能跨领域复用，底层逻辑一致。"
    "这个原则的本质是复用性，可通过抽象范式推广。"
)
axes = pipe.compute_three_axis(methodology_text)
check("methodology: time_binding <= 0.5",
      axes["time_binding"] <= 0.5,
      f"got {axes['time_binding']}")
check("methodology: transferability >= 0.5",
      axes["transferability"] >= 0.5,
      f"got {axes['transferability']}")
check("methodology: abstraction_level >= 0.5",
      axes["abstraction_level"] >= 0.5,
      f"got {axes['abstraction_level']}")

# --- 2b: 临时性文本（高时间绑定、低可迁移、低抽象） ---
temp_text = "今天这个问题需要临时解决，刚才我已经试过一种方法。"
axes2 = pipe.compute_three_axis(temp_text)
check("temporal: time_binding >= 0.5",
      axes2["time_binding"] >= 0.5,
      f"got {axes2['time_binding']}")
check("temporal: transferability < 0.5",
      axes2["transferability"] < 0.5,
      f"got {axes2['transferability']}")

# --- 2c: 日常对话文本（中等绑定、低可迁移、低抽象） ---
daily_text = "我今天吃了早饭就去上班了，中午休息了一下。"
axes3 = pipe.compute_three_axis(daily_text)
check("daily: default time_binding ~0.7",
      abs(axes3["time_binding"] - 0.7) < 0.2,
      f"got {axes3['time_binding']}")
check("daily: transferability low",
      axes3["transferability"] < 0.5,
      f"got {axes3['transferability']}")
check("daily: abstraction_level low",
      axes3["abstraction_level"] < 0.5,
      f"got {axes3['abstraction_level']}")


# ==============================================================
# 3. 置信度计算测试
# ==============================================================

print("\n=== Confidence Computation ===")

# 三轴都"好"：低绑定(0.1) + 高迁移(0.9) + 高层级(0.9)
c1 = pipe.compute_confidence(0.1, 0.9, 0.9)
check("conf: good >= 0.8",
      c1 >= 0.8,
      f"got {c1}")
# 轴1贡献 = (1.0-0.1)*0.3 = 0.27
# 轴2贡献 = 0.9*0.4 = 0.36
# 轴3贡献 = 0.9*0.3 = 0.27
# 总和 = 0.90
expected_c1 = round(0.27 + 0.36 + 0.27, 4)
check("conf: good expected value",
      abs(c1 - expected_c1) < 0.001,
      f"expected {expected_c1}, got {c1}")

# 三轴都"差"：高绑定(0.8) + 低迁移(0.2) + 低层级(0.3)
c2 = pipe.compute_confidence(0.8, 0.2, 0.3)
# (F118 归一化后无条件贡献，去掉条件门限)
# 轴1: (1.0-0.8)*0.3 = 0.06
# 轴2: 0.2*0.4 = 0.08
# 轴3: 0.3*0.3 = 0.09
# 总和 = 0.23
check("conf: bad defaults to ~0.23 (no conditional gate)",
      abs(c2 - 0.23) < 0.01,
      f"got {c2}")

# 边界情况：time_binding 刚好 0.5
c3 = pipe.compute_confidence(0.5, 0.5, 0.5)
# 轴1: (1.0-0.5)*0.3 = 0.15
# 轴2: 0.5*0.4 = 0.20
# 轴3: 0.5*0.3 = 0.15
# 总和 = 0.50
check("conf: boundary 0.5",
      abs(c3 - 0.50) < 0.001,
      f"expected 0.50, got {c3}")

# 默认文本（无任何关键词，默认三轴 0.7/0.3/0.3）
# (1-0.7)*0.3 + 0.3*0.4 + 0.3*0.3 = 0.09+0.12+0.09 = 0.30
c4 = pipe.compute_confidence(0.7, 0.3, 0.3)
check("conf: default ~0.30",
      abs(c4 - 0.30) < 0.01,
      f"expected ~0.30, got {c4}")

# 最高分：time_binding=0.0, transferability=1.0, abstraction_level=1.0
c5 = pipe.compute_confidence(0.0, 1.0, 1.0)
check("conf: max = 1.0",
      abs(c5 - 1.0) < 0.01,
      f"expected 1.0, got {c5}")

# 零分边界：time_binding=1.0, transferability=0.0, abstraction_level=0.0
c6 = pipe.compute_confidence(1.0, 0.0, 0.0)
check("conf: zero = 0.0",
      abs(c6 - 0.0) < 0.01,
      f"expected 0.0, got {c6}")


# ==============================================================
# 4. 完整评估流程测试
# ==============================================================

print("\n=== Full Evaluation Flow ===")

_clean_xuanjian_table()

# --- 4a: 高置信度方法论文本 → 触发候选 ---
result = pipe.evaluate(
    ai_id="test-ai-3",
    text=(
        "采用抽象架构模式可跨领域复用，"
        "底层原则适用于各种场景。"
        "模型的核心思想是可迁移，通用性强的概念能持续使用。"
    ),
    confidence=0.85,
)
check("eval: returns InsightAnalysis", isinstance(result, InsightAnalysis))
check("eval: ai_id preserved", result.ai_id == "test-ai-3")
check("eval: confidence >= 0.8", result.monument_score >= 0.8)
check("eval: is_candidate True", result.is_candidate is True)

# 验证持久化
rows4 = repo.get_by_ai_id("test-ai-3")
check("eval: persisted to DB", len(rows4) >= 1)

# 验证候选文件
candidates_dir = Path(__file__).resolve().parent.parent.parent / "candidates"
candidate_files = list(candidates_dir.glob(f"candidate-{result.insight_id}.json"))
check("eval: candidate file created", len(candidate_files) >= 1)

if candidate_files:
    with open(candidate_files[0], encoding="utf-8") as f:
        cand_data = json.load(f)
    check("eval: candidate ai_id match", cand_data["ai_id"] == "test-ai-3")
    check("eval: candidate insight_id match", cand_data["insight_id"] == result.insight_id)

# --- 4b: 低置信度文本 → 不触发 ---
result2 = pipe.evaluate(
    ai_id="test-ai-4",
    text="我今天去了公园散步。",
    confidence=0.3,
)
check("eval low: confidence < 0.8", result2.confidence == 0.3)
check("eval low: monument_score 0", result2.monument_score == 0.0)
check("eval low: not candidate", result2.is_candidate is False)

# --- 4c: 置信度够但三轴不够 → 不触发 ---
# 模拟高 confidence 但内容无方法论价值的文本
result3 = pipe.evaluate(
    ai_id="test-ai-5",
    text="当前这个问题需要今天临时处理，刚才我已经做完了。",
    confidence=0.85,
)
check("eval high conf low method: not candidate",
      result3.is_candidate is False,
      f"got monument_score={result3.monument_score}")
# 这种纯临时性文本的三轴得分应该很低
check("eval high conf low method: low monument_score",
      result3.monument_score < 0.8,
      f"got {result3.monument_score}")

# --- 4d: 高置信度 + 三轴好 → 候选 ---
result4 = pipe.evaluate(
    ai_id="test-ai-6",
    text=(
        "通用模型的复用原则始终跨领域适用，"
        "抽象架构模式处处可复用。"
        "复用性强的底层逻辑跨系统推广，这个核心机制遵循既定规律。"
    ),
    confidence=0.95,
)
check("eval high both: is_candidate", result4.is_candidate is True)
check("eval high both: monument_score >= 0.8",
      result4.monument_score >= 0.8,
      f"got {result4.monument_score}")


# ==============================================================
# 5. 模式匹配测试
# ==============================================================

print("\n=== Pattern Matching ===")

# 多个同模式评估
for i in range(5):
    pipe.evaluate(
        ai_id="pattern-test-ai",
        text="这个抽象模型的原则通用性高，可迁移到类似场景中。",
        confidence=0.9,
    )

pattern_count = repo.count_by_pattern("这个抽象模型的原则通用性高_可迁移到类似场景中")
check("pattern: count >= 5", pattern_count >= 5,
      f"got {pattern_count}")


# ==============================================================
# 6. 边界情况测试
# ==============================================================

print("\n=== Edge Cases ===")

# 空文本
empty_result = pipe.evaluate(
    ai_id="empty-test",
    text="",
    confidence=0.9,
)
check("empty text: monument_score computed, likely low",
      empty_result.monument_score >= 0.0)

# 置信度恰好等于阈值
exact_result = pipe.evaluate(
    ai_id="exact-test",
    text="通用模型的核心架构原则可跨领域复用。",
    confidence=0.8,
)
check("exact threshold: confidence preserved",
      exact_result.confidence == 0.8)


# ==============================================================
# Summary
# ==============================================================

close_db()

print(f"\n{'='*40}")
if errors:
    print(f"FAILURES: {len(errors)}")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED ✓")

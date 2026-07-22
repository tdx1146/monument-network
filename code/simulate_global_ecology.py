#!/usr/bin/env python3
"""
全球生态模拟 —— 丰碑网络的磨损与加固演化

模拟30天全县网生态，观察丰碑在自然磨损与随机加固下的生存分布。

参数匹配 config/monument.json 中的配置：
  erosion.base_rate = 0.001      # 每天磨损
  erosion.acceleration_threshold = 0.3
  erosion.acceleration_factor = 2.0
  reinforce.by_reference = 0.02
  reinforce.by_review = 0.15
  reinforce.by_edit = 0.30
  thresholds: normal=0.5, warning=0.3, endangered=0.1, archived=0.05
  score_max = 2.0
"""

import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ─── 配置 ─────────────────────────────────────────────────
# 直接匹配 config/monument.json 的 erosion 和 reinforce 段
EROSION_BASE_RATE = 0.001
EROSION_ACCEL_THRESHOLD = 0.3
EROSION_ACCEL_FACTOR = 2.0
SCORE_MAX = 2.0

REINFORCE_REFERENCE = 0.02
REINFORCE_REVIEW = 0.15
REINFORCE_EDIT = 0.30

THRESHOLD_NORMAL = 0.5
THRESHOLD_WARNING = 0.3
THRESHOLD_ENDANGERED = 0.1
THRESHOLD_ARCHIVED = 0.05

# 模拟参数
SIM_DAYS = 30
REFERENCE_PROB = 0.10   # 10% 概率被引用（每天）
REVIEW_PROB = 0.05      # 5% 概率被评审（每天）
RANDOM_SEED = 42

CANDIDATES_DIR = Path("/vol2/1000/AI专用/丰碑网络/candidates")
OUTPUT_DIR = Path("/vol2/1000/AI专用/丰碑网络/code/sim_output")

# ─── 数据结构 ────────────────────────────────────────────


class Monument:
    """单个丰碑——包含评分、计数、状态"""

    def __init__(self, monument_id: str, title: str, creator: str,
                 score: float = 1.0):
        self.id = monument_id
        self.title = title
        self.creator = creator
        self.score = score
        self.status = "normal"

        # 生命周期统计
        self.lifetime_references = 0
        self.lifetime_reviews = 0
        self.lifetime_edits = 0

        # 每日评分历史（用于图表）
        self.daily_scores = []

        self._update_status()

    def _update_status(self):
        if self.score > THRESHOLD_NORMAL:
            self.status = "normal"
        elif self.score > THRESHOLD_WARNING:
            self.status = "warning"
        elif self.score > THRESHOLD_ENDANGERED:
            self.status = "endangered"
        elif self.score > THRESHOLD_ARCHIVED:
            self.status = "archived"
        else:
            self.status = "archived"

    def tick(self, day: int):
        """模拟一天"""
        # 1. 基础磨损
        erosion_rate = EROSION_BASE_RATE
        if self.score < EROSION_ACCEL_THRESHOLD:
            erosion_rate *= EROSION_ACCEL_FACTOR

        self.score -= erosion_rate

        # 2. 随机引用加固
        if random.random() < REFERENCE_PROB:
            self.score += REINFORCE_REFERENCE
            self.lifetime_references += 1

        # 3. 随机评审加固
        if random.random() < REVIEW_PROB:
            self.score += REINFORCE_REVIEW
            self.lifetime_reviews += 1

        # 4. 上限钳位
        if self.score > SCORE_MAX:
            self.score = SCORE_MAX

        # 5. 下限钳位（分数永不小于0）
        if self.score < 0:
            self.score = 0.0

        # 6. 更新状态
        self._update_status()

        # 7. 记录历史
        self.daily_scores.append((day, self.score, self.status))

    def is_alive(self) -> bool:
        return self.score > THRESHOLD_ARCHIVED

    def __repr__(self):
        return (f"<Monument {self.id[:20]} score={self.score:.4f} "
                f"status={self.status}>")


# ─── 加载数据 ────────────────────────────────────────────


def load_candidates() -> list[Monument]:
    """从 candidates 目录加载丰碑"""
    monuments = []
    for filename in sorted(os.listdir(CANDIDATES_DIR)):
        if not filename.endswith('.json'):
            continue

        filepath = CANDIDATES_DIR / filename
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        monument_score = data.get('monument_score', 1.0)
        creator = data.get('ai_id', 'unknown')
        title = data.get('title', data.get('insight_id', filename))

        # 从 metadata 尝试提取创建者
        meta = data.get('metadata', {})
        if not creator or creator == '?':
            creator = meta.get('creator', 'unknown')

        existing_ids = {m.id for m in monuments}
        m_id = data.get('insight_id', filename)
        # 去重（同一insight_id只保留一个）
        if m_id in existing_ids:
            continue

        mon = Monument(
            monument_id=m_id,
            title=title,
            creator=creator,
            score=monument_score
        )
        monuments.append(mon)

    return monuments


# ─── 模拟引擎 ────────────────────────────────────────────


def run_simulation(monuments: list[Monument], days: int):
    """运行多天模拟"""
    day_summaries = []

    for day in range(1, days + 1):
        for m in monuments:
            if m.is_alive():
                m.tick(day)

        # 每日汇总
        alive = [m for m in monuments if m.is_alive()]
        archived = [m for m in monuments if not m.is_alive()]
        avg_score = sum(m.score for m in alive) / len(alive) if alive else 0.0

        statuses = defaultdict(int)
        for m in monuments:
            statuses[m.status] += 1

        summary = {
            "day": day,
            "alive": len(alive),
            "archived": len(archived),
            "avg_score": round(avg_score, 4),
            "statuses": dict(statuses),
        }
        day_summaries.append(summary)

    return day_summaries


# ─── 报告生成 ────────────────────────────────────────────


def generate_report(monuments: list[Monument],
                    day_summaries: list[dict]) -> str:
    """生成模拟报告"""
    alive = [m for m in monuments if m.is_alive()]
    archived = [m for m in monuments if not m.is_alive()]

    # 按创建者汇总
    by_creator = defaultdict(list)
    for m in monuments:
        by_creator[m.creator].append(m)

    avg_score_alive = (sum(m.score for m in alive) / len(alive)
                       if alive else 0.0)
    avg_all = sum(m.score for m in monuments) / len(monuments)

    lines = []
    lines.append("# 全球生态模拟报告\n")
    lines.append(f"> 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
    lines.append("## 模拟参数\n")
    lines.append(f"| 参数 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 模拟天数 | {SIM_DAYS} 天 |")
    lines.append(f"| 丰碑总数 | {len(monuments)} 座 |")
    lines.append(f"| 活跃AI数 | {len(by_creator)} 个 |")
    lines.append(f"| 基础磨损率 | {EROSION_BASE_RATE}/天 |")
    lines.append(f"| 加速磨损阈值 | < {EROSION_ACCEL_THRESHOLD} 时加倍 |")
    lines.append(f"| 引用概率 | {REFERENCE_PROB*100:.0f}%/天 (加固 +{REINFORCE_REFERENCE}) |")
    lines.append(f"| 评审概率 | {REVIEW_PROB*100:.0f}%/天 (加固 +{REINFORCE_REVIEW}) |")
    lines.append(f"| 评分上限 | {SCORE_MAX} |")
    lines.append(f"| 归档阈值 | {THRESHOLD_ARCHIVED} |")
    lines.append(f"| 随机种子 | {RANDOM_SEED} |")
    lines.append("")

    # 总览
    lines.append("## 模拟结果总览\n")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 存活丰碑 | {len(alive)} 座 ({len(alive)/len(monuments)*100:.1f}%) |")
    lines.append(f"| 归档丰碑 | {len(archived)} 座 ({len(archived)/len(monuments)*100:.1f}%) |")
    lines.append(f"| 存活丰碑平均评分 | {avg_score_alive:.4f} |")
    lines.append(f"| 全部丰碑平均评分 | {avg_all:.4f} |")

    # 状态分布
    statuses = defaultdict(int)
    for m in monuments:
        statuses[m.status] += 1
    lines.append(f"| 正常 (>{THRESHOLD_NORMAL}) | {statuses.get('normal', 0)} 座 |")
    lines.append(f"| 警告 ({THRESHOLD_WARNING}-{THRESHOLD_NORMAL}) | {statuses.get('warning', 0)} 座 |")
    lines.append(f"| 濒危 ({THRESHOLD_ENDANGERED}-{THRESHOLD_WARNING}) | {statuses.get('endangered', 0)} 座 |")
    lines.append(f"| 归档 (<={THRESHOLD_ARCHIVED}) | {statuses.get('archived', 0)} 座 |")
    lines.append("")

    # 每日演化
    lines.append("## 每日演化\n")
    lines.append("```")
    lines.append(f"{'Day':>4} {'Alive':>6} {'Arch':>6} {'AvgScore':>9}  Statuses")
    for s in day_summaries:
        st = " ".join(f"{k}={v}" for k, v in sorted(s["statuses"].items()))
        lines.append(f"{s['day']:>4} {s['alive']:>6} {s['archived']:>6} {s['avg_score']:>9.4f}  {st}")
    lines.append("```\n")

    # 存活丰碑排行榜
    lines.append("## 存活丰碑排行榜（前20）\n")
    alive_sorted = sorted(alive, key=lambda m: m.score, reverse=True)
    lines.append(f"| 排名 | 创建者 | 评分 | 状态 | 引用次数 | 评审次数 |")
    lines.append(f"|------|--------|------|------|----------|----------|")
    for i, m in enumerate(alive_sorted[:20], 1):
        lines.append(f"| {i:<4d} | {str(m.creator)[:24]:24s} | {m.score:.4f} | {m.status} | {m.lifetime_references:>6d} | {m.lifetime_reviews:>6d} |")
    lines.append("")

    # 归档丰碑
    if archived:
        lines.append("## 归档丰碑\n")
        archived_sorted = sorted(archived, key=lambda m: m.score)
        lines.append(f"| 创建者 | 初始评分 | 最终评分 | 生命周期 |")
        lines.append(f"|--------|----------|----------|----------|")
        for m in archived_sorted:
            init_score = m.daily_scores[0][1] if m.daily_scores else m.score
            final_score = m.daily_scores[-1][1] if m.daily_scores else m.score
            lifespan = len(m.daily_scores) if m.daily_scores else "?"
            lines.append(f"| {str(m.creator)[:24]:24s} | {init_score:.4f} | {final_score:.4f} | Day {lifespan} |")
        lines.append("")

    # AI创建者统计
    lines.append("## AI创建者统计\n")
    creator_stats = []
    for creator, ms in by_creator.items():
        scores = [m.score for m in ms]
        creator_stats.append({
            "creator": creator,
            "total": len(ms),
            "alive": len([m for m in ms if m.is_alive()]),
            "avg_score": sum(scores)/len(scores),
            "max_score": max(scores),
            "min_score": min(scores),
        })
    creator_stats.sort(key=lambda x: x["avg_score"], reverse=True)

    lines.append(f"| 排名 | 创建者 | 总丰碑 | 存活 | 平均分 | 最高分 | 最低分 |")
    lines.append(f"|------|--------|--------|------|--------|--------|--------|")
    for i, cs in enumerate(creator_stats, 1):
        lines.append(f"| {i:<4d} | {str(cs['creator'])[:24]:24s} | {cs['total']:>5d} | {cs['alive']:>4d} | {cs['avg_score']:.4f} | {cs['max_score']:.4f} | {cs['min_score']:.4f} |")
    lines.append("")

    # 生态洞察
    lines.append("## 生态洞察\n")
    lines.append("### 存活分析\n")
    lines.append(f"- **总体存活率**: {len(alive)}/{len(monuments)} ({len(alive)/len(monuments)*100:.1f}%)")
    lines.append(f"- **30天后平均评分**: {avg_all:.4f}（初始平均约 0.95）")
    lines.append(f"- **影响存活的关键因素**:")

    # 分析存档原因
    if archived:
        high_init_archived = [m for m in archived
                              if m.daily_scores and m.daily_scores[0][1] > 0.5]
        low_init_archived = [m for m in archived
                             if m.daily_scores and m.daily_scores[0][1] <= 0.5]
        if high_init_archived:
            lines.append(f"  - 高初始分仍被淘汰: {len(high_init_archived)} 座 — 加固不足，自然衰变")
        if low_init_archived:
            lines.append(f"  - 低初始分容易被淘汰: {len(low_init_archived)} 座 — 初始评分 < 0.5")
        ref_boost = [m for m in archived if m.lifetime_references > 0]
        if ref_boost:
            lines.append(f"  - 有过加固但仍被淘汰: {len(ref_boost)} 座 — 加固频率不足抵消耗损")

    lines.append(f"\n### 加固效应\n")
    total_refs = sum(m.lifetime_references for m in monuments)
    total_reviews = sum(m.lifetime_reviews for m in monuments)
    refractory_refs = sum(1 for m in monuments if m.lifetime_references > 0)
    refractory_reviews = sum(1 for m in monuments if m.lifetime_reviews > 0)
    lines.append(f"- 总引用加固次数: {total_refs}")
    lines.append(f"- 总评审加固次数: {total_reviews}")
    lines.append(f"- 被引用过的丰碑比例: {refractory_refs}/{len(monuments)} ({refractory_refs/len(monuments)*100:.1f}%)")
    lines.append(f"- 被评审过的丰碑比例: {refractory_reviews}/{len(monuments)} ({refractory_reviews/len(monuments)*100:.1f}%)")
    lines.append(f"- 纯靠基础磨损生存是可能的，但需要初始分 > 0.05 持续30天")
    lines.append(f"  - 基础磨损30天后: 1.0 → {1.0 * (1.0-EROSION_BASE_RATE)**30:.4f}")
    lines.append(f"  - 加速磨损30天后: 0.3 → {0.3 * (1.0-EROSION_BASE_RATE*EROSION_ACCEL_FACTOR)**30:.4f}")

    lines.append(f"\n### 关键发现\n")
    lines.append(f"1. **自然衰变不可避免**: 无加固的丰碑在30天后损失约 {(1.0 - (1.0-EROSION_BASE_RATE)**30)*100:.1f}% 评分")
    lines.append(f"2. **随机加固能显著延缓**: 每次引用 (+{REINFORCE_REFERENCE}) 可抵消 {REINFORCE_REFERENCE/EROSION_BASE_RATE:.0f} 天的磨损")
    lines.append(f"3. **低分加速磨损陷阱**: 评分跌破 {EROSION_ACCEL_THRESHOLD} 后衰变翻倍，形成死亡螺旋")
    lines.append(f"4. **归档几乎是不可逆的**: 一旦评分 < {THRESHOLD_ARCHIVED}，复活需要大额加固输入")

    return "\n".join(lines)


def generate_svg_chart(monuments: list[Monument], day_summaries: list[dict],
                       output_path: Path):
    """生成ASCII演化图（文本图表）"""
    alive = [m for m in monuments if m.is_alive()]

    # 每个 day 的幸存者平均评分趋势
    lines = ["# 评分演化趋势（ASCII图表）\n"]
    lines.append("```")
    lines.append("Avg Score Trend (Day 1 → 30)")
    lines.append("")

    # 取第1, 3, 5, ... 天，不超过20个采样点
    step = max(1, SIM_DAYS // 20)
    sampled = day_summaries[::step]
    if sampled[-1] != day_summaries[-1]:
        sampled.append(day_summaries[-1])

    # 生成柱状图
    max_val = max(s["avg_score"] for s in day_summaries)
    min_val = min(s["avg_score"] for s in day_summaries)
    range_val = max_val - min_val or 0.001
    bar_width = 3

    # 纵轴刻度
    lines.append(f"  {max_val:.4f} ┤")
    for s in sampled:
        pct = (s["avg_score"] - min_val) / range_val
        bar_len = int(pct * 40)
        bar = "█" * max(1, bar_len)
        lines.append(f"  {s['avg_score']:.4f} ┤ {bar} Day {s['day']:>2} (alive={s['alive']})")
    lines.append(f"  {min_val:.4f} ┘")
    lines.append("")

    lines.append("Status Distribution Timeline")
    # 每隔3天看状态
    for s in day_summaries[::3]:
        st = " ".join(f"{k}={v:>3}" for k, v in sorted(s["statuses"].items()) if v > 0)
        lines.append(f"  Day {s['day']:>2}: {st}")
    if day_summaries[-1] not in day_summaries[::3]:
        s = day_summaries[-1]
        st = " ".join(f"{k}={v:>3}" for k, v in sorted(s["statuses"].items()) if v > 0)
        lines.append(f"  Day {s['day']:>2}: {st}")

    lines.append("```\n")
    return "\n".join(lines)


def output_json_data(monuments: list[Monument], day_summaries: list[dict],
                     output_path: Path):
    """输出JSON数据供后续分析"""
    data = {
        "simulation_parameters": {
            "days": SIM_DAYS,
            "reference_prob": REFERENCE_PROB,
            "review_prob": REVIEW_PROB,
            "erosion_base_rate": EROSION_BASE_RATE,
            "erosion_accel_threshold": EROSION_ACCEL_THRESHOLD,
            "erosion_accel_factor": EROSION_ACCEL_FACTOR,
            "reinforce_reference": REINFORCE_REFERENCE,
            "reinforce_review": REINFORCE_REVIEW,
            "reinforce_edit": REINFORCE_EDIT,
            "score_max": SCORE_MAX,
            "threshold_archived": THRESHOLD_ARCHIVED,
            "random_seed": RANDOM_SEED,
        },
        "monuments": [
            {
                "id": m.id,
                "title": m.title,
                "creator": m.creator,
                "initial_score": m.daily_scores[0][1] if m.daily_scores
                else m.score,
                "final_score": round(m.score, 4),
                "final_status": m.status,
                "lifetime_references": m.lifetime_references,
                "lifetime_reviews": m.lifetime_reviews,
                "survived": m.is_alive(),
            }
            for m in monuments
        ],
        "daily_summaries": day_summaries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"JSON data written to {output_path}")


# ─── Main ────────────────────────────────────────────────


def main():
    random.seed(RANDOM_SEED)
    print("=" * 60)
    print("  全球生态模拟 —— 丰碑网络磨损与加固演化")
    print("=" * 60)

    # 1. 加载
    print("\n[1/4] 加载丰碑数据...")
    monuments = load_candidates()
    print(f"  加载 {len(monuments)} 座丰碑")

    if not monuments:
        print("  ❌ 未找到丰碑数据，终止")
        return

    # 初始统计
    print(f"\n  初始评分分布:")
    init_scores = [m.score for m in monuments]
    for t in ["normal", "warning", "endangered", "archived"]:
        cnt = sum(1 for m in monuments if m.status == t)
        print(f"    {t}: {cnt} 座")
    print(f"  初始平均评分: {sum(init_scores)/len(init_scores):.4f}")

    # 2. 模拟
    print(f"\n[2/4] 运行 {SIM_DAYS} 天模拟...")
    day_summaries = run_simulation(monuments, SIM_DAYS)

    # 3. 生成报告
    print(f"\n[3/4] 生成报告...")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Markdown 报告
    report = generate_report(monuments, day_summaries)
    report_path = OUTPUT_DIR / "global_ecology_report.md"
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"  报告: {report_path}")

    # ASCII 图表
    chart = generate_svg_chart(monuments, day_summaries, OUTPUT_DIR)
    chart_path = OUTPUT_DIR / "simulation_chart.txt"
    with open(chart_path, 'w') as f:
        f.write(chart)
    print(f"  图表: {chart_path}")

    # JSON 数据
    json_path = OUTPUT_DIR / "simulation_data.json"
    output_json_data(monuments, day_summaries, json_path)

    # 4. 摘要
    alive = [m for m in monuments if m.is_alive()]
    archived = [m for m in monuments if not m.is_alive()]
    print(f"\n[4/4] 模拟完成")
    print(f"  ├─ 存活: {len(alive)} 座 ({len(alive)/len(monuments)*100:.1f}%)")
    print(f"  ├─ 归档: {len(archived)} 座 ({len(archived)/len(monuments)*100:.1f}%)")
    print(f"  └─ 平均评分: {sum(m.score for m in monuments)/len(monuments):.4f}")
    print(f"\n  报告已保存至 {OUTPUT_DIR}/")
    print("=" * 60)

    # 打印报告到stdout
    print("\n" + report)


if __name__ == "__main__":
    main()

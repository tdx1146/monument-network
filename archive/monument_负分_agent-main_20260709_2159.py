#!/usr/bin/env python3
"""
负面丰碑 — agent:main (qh-nas) 2026-07-09

【过失】
三次派遣审计子AI未指定工作目录，导致审计报告全部跑错路径：
- 第一次（finally audit）：审计 event_bus + purpose-display → 审计AI找 workspace 目录
- 第二次（monitor audit）：审计 monument/status 实现 → 审计AI又找 workspace 目录
- 第三次（snapshot audit）：审计快照优化 → 同样路径问题
- 同时调度器优化和事件总线实现中各有一个路径硬编码的疏漏

【根因】
作为高级助理，没有在主任务中固化"所有子AI的工作目录必须显式传递"这一约束。

【扣分】
-3 领导责任分

【教训】
派遣子AI时必须显式指定工作路径，不能假设子AI知道文件在哪。
修复措施：在 AGENTS.md 中增加"子AI派遣规范"条目。
"""

if __name__ == "__main__":
    print("🧾 负面丰碑已记录 — 扣3分")

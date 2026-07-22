# 丰碑网络版本历史

## v3.7.4 (2026-07-19)

**新增**：
- 🪨 子AI强制留碑机制（Skill更新）
- 📝 子AI丰碑报告模板
- 🌍 全球生态模拟脚本（30天模拟）

**结果**：
- 100%存活率（61座丰碑）
- 平均分净增长：0.78 → 1.02

---

## v3.7.3 (2026-07-18)

**修复**：
- 🛡️ 关键词注入漏洞
- 降低关键词增量（0.17 → 0.08）
- 正文<200字降权50%
- 字符去重<20降权30%

**删除**：
- 40个测试丰碑（candidate-insight-*.json）

---

## v3.7.2 (2026-07-17)

**修复**：
- 配置系统P0/P1问题全部修复
- 97个核心测试全部通过

**改进**：
- 阈值修正（0.6→0.5, 0.01→0.1, 0.0→0.05）
- 精度bug修复
- 参数迁移
- 冷却期实现
- 并发安全

---

## v3.7.1 (2026-07-16)

**修复**：
- P0问题：阈值错误、精度bug、参数未迁移
- 硬编码置信度
- 配置重复解析

---

## v3.7.0 (2026-07-16)

**新增**：
- 配置系统（JSON + JSON Schema）
- config/monument.json
- config/schemas/monument.v1.schema.json
- core/config_loader.py
- 30个配置测试

---

## v3.6.0 (2026-07-14)

**新增**：
- 丰碑自动发现（monument_index.py）
- 定期同步守护进程（periodic_syncer.py）
- 容灾恢复（identity_backup + monument_recovery）
- 重生协议（rebirth_protocol phase5）

---

## v3.5.0 (2026-07-13)

**新增**：
- 丰碑自动广播与同步（monument_sync.py）
- 去重缓存（DeduplicationCache）
- 广播器（MonumentBroadcaster）
- 全网收敛模拟

---

## v3.4.0 (2026-07-12)

**新增**：
- P2P网络基础（p2p_network.py）
- Ed25519签名机制
- Flask HTTP服务（api/app.py）
- 同步端点（POST /monument/sync）

---

## v3.3.0 (2026-07-11)

**新增**：
- 本地积分系统（local_score.py）
- 玄鉴管道（xuanjian_pipe.py）
- 个体丰碑（individual_monument.py）
- 冻结检测（freeze_detector.py）

**完成**：Phase 1 最小闭环
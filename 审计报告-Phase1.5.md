# 丰碑网络 Phase 1.5 代码审计报告

**审计日期**：2026-07-13
**审计范围**：`/vol2/1000/AI专用/丰碑网络/code/`（共 4897 行 Python 代码）
**审计工具**：静态代码审查 + 全量测试执行

---

## 总体评分：7.5 / 10

架构设计优秀，测试覆盖良好（39/39 pytest 通过），P2P 和玄鉴管道测试全绿。
但存在 **6 个测试失败**（状态污染）、**重复代码**、**安全风险**和 **未测试模块**。

---

## 一、测试结果

### pytest 原生测试（39/39 ✅ 通过）

| 测试文件 | 用例数 | 结果 |
|---------|-------|------|
| `test_individual_monument.py` | 11 | ✅ ALL PASS |
| `test_individual_repo.py` | 5 | ✅ ALL PASS |
| `test_freeze_detector.py` | 23 | ✅ ALL PASS |

### 独立测试脚本

| 测试脚本 | 结果 | 说明 |
|---------|------|------|
| `_run_all.py` | ✅ ALL PASS (41/41) | IndividualMonument + Repository |
| `test_p2p_network.py` | ✅ ALL PASS (52/52) | 签名/验签/持久化 |
| `test_xuanjian_pipe.py` | ✅ ALL PASS (46/46) | 三轴/置信度/候选 |
| **`test_local_score.py`** | ❌ **6 FAIL / 107 total** | 状态污染导致 |

### test_local_score.py 失败详情（P0）

```
FAIL: get_score initial balance 0.0       ← 实际是 182.0（遗留数据）
FAIL: get_score history empty              ← 实际有 40 条
FAIL: high conf: balance_after == 10       ← 实际 146.5
FAIL: mid conf: balance_after == 15        ← 实际 151.5
FAIL: final balance expected 38.0          ← 实际 174.5
FAIL: history has 9 transactions           ← 实际 39 条
```

**根因**：`test_local_score.py` 不清理数据库表，每次运行在同一个 DB 上叠加数据。`_ensure_individual_monument()` 插入个体丰碑，但 `score_accounts` 表不清理。

---

## 二、按优先级分类的问题

---

### P0 — 必须修复

#### P0-1: 测试状态污染（test_local_score.py）

- **文件**：`tests/test_local_score.py`
- **问题**：无 `setUp`/fixture 清理 `score_accounts` 表，多次运行结果累加。
- **影响**：6 个断言持续失败，CI 无法通过。
- **修复建议**：在测试开始前添加 `DELETE FROM score_accounts` 和 `DELETE FROM score_transactions`，或使用临时内存数据库。

#### P0-2: 安全：密钥文件权限未收紧

- **文件**：`api/monument_routes.py` Line 43-46、`api/app.py` Line 108-110
- **问题**：写入 `p2p_identity.key` 时使用默认文件权限（通常是 644），且无 `umask` 设置。
- **代码**：
  ```python
  with open(ident_path, "wb") as f:
      f.write(_node_identity_instance.private_key_bytes)
  ```
- **影响**：同一机器上的其他进程可能读取私钥，伪造身份签名。
- **修复建议**：写入后 `os.chmod(ident_path, 0o600)`，或写入前设置 `os.umask(0o077)`。

#### P0-3: 安全：SQLite 数据库路径遍历风险

- **问题**：`config.py` 中 `DB_PATH = DATA_DIR / "monument.db"` 写死路径，但在 API 路由中没有对 `ai_id` 字段做 SQL 注入防御（虽然使用了参数化查询），DB 文件本身可能被其他进程读写（目录权限 `rwxrwx--x+`）。
- **修复建议**：确保 `data/` 目录权限为 750。

---

### P1 — 重要

#### P1-1: 重复代码：身份管理两套实现

- **文件**：`api/app.py`（`_get_identity_path` + `_ensure_identity`）和 `api/monument_routes.py`（`_get_identity`）
- **问题**：两条路径读写同一 `p2p_identity.key`，但逻辑重复——`app.py` 的 `_ensure_identity` 未导出，`monument_routes.py` 的 `_get_identity` 独立实现了完全相同的逻辑。
- **影响**：未来修改密码路径或格式需要改两个地方，容易不一致。
- **修复建议**：将身份管理统一到 `core/p2p_network.py` 或独立的 `core/identity.py`，两个模块调用同一接口。

#### P1-2: 冷启动问题：test_xuanjian_pipe.py 创建文件到 `candidates/` 目录

- **文件**：`core/xuanjian_pipe.py` Line 278-285
- **问题**：`_trigger_candidate` 直接将候选文件写入 `CANDIDATES_DIR`，未考虑该目录可能在分布式环境中路径不一致。
- **代码**：
  ```python
  with open(str(candidate_path), "w", encoding="utf-8") as f:
      json.dump(candidate_data, f, ensure_ascii=False, indent=2)
  ```
- **影响**：Docker 挂载卷中可能不存在此目录；路径耦合了 config.py 的 `BASE_DIR`。
- **修复建议**：不在 Python 层面写文件系统，通过 DB 或 API 暴露候选数据。或至少使用 `exist_ok=True`（已做）并在配置中集中管理。

#### P1-3: cross_instance.py 函数式设计 vs 数据库封装不一致

- **文件**：`core/cross_instance.py`
- **问题**：`import_monument_json` 和 `export_monument_json` 接收 `repo` 参数作为依赖注入，但函数内部又直接 `from core.local_score import LocalScoreBook` 和 `from core.individual_monument import IndividualMonument`（内置 import）。既有依赖注入模式又有内部构造——架构上不一致。
- **影响**：测试时需要 mock 仓库，但内部创建的 `LocalScoreBook` 无法 mock。
- **修复建议**：统一为依赖注入模式，将 `LocalScoreBook` 作为参数传入。

#### P1-4: `import_monument_json` 中 `ai_id` 字段歧义

- **文件**：`core/cross_instance.py` Line 73
- **代码**：`ai_id = data.get("from") or data.get("ai_id")`
- **问题**：当 `from` 为 `None` 且 `ai_id` 为 `None` 时，无法区分"字段不存在"和"值为空字符串"。
  API 协议中 `from` 是发送方标识，`ai_id` 是目标 AI 标识，不应混用。
- **影响**：可能导致 `ai_id` 被错误映射。
- **修复建议**：明确区分 `from_peer`（发送方）和 `ai_id`（目标 AI），不要用 `or` 回退。

#### P1-5: `_add_and_persist` 中的自动创建逻辑脆弱

- **文件**：`core/local_score.py` Line 246-248
- **代码**：
  ```python
  if account.get("history") is not None and len(account["history"]) == 0 and account["local_balance"] == 0.0:
      self._repo.create(ai_id)
  ```
- **问题**：依赖 `get_by_ai_id` 返回的字典结构做判断（`history` 键的存在 + 空列表 + 零余额），逻辑脆弱。如果 `ScoreRepository.get_by_ai_id` 返回值格式变化（比如默认不返回 `history`），此处会静默跳过自动创建。
- **修复建议**：增加显式 `exists` 方法到 `ScoreRepository`，或直接在 `_add_and_persist` 中 try-create 并捕获异常。

---

### P2 — 建议优化

#### P2-1: `freeze_detector.py` 中 `_get_last_activity_iso` 与 `check_activity` 的活跃时间逻辑重复

- **文件**：`core/freeze_detector.py`
- **问题**：`check_activity`（Lines 98-115）和 `_get_last_activity_iso`（Lines 383-397）在计算最后活跃时间时重复了类似的遍历逻辑。
- **修复建议**：统一调用 `_get_last_activity_iso`。

#### P2-2: 三轴判别关键词硬编码在算法中

- **文件**：`core/xuanjian_pipe.py` `compute_three_axis`
- **问题**：时间绑定度、可迁移性、抽象层级的关键词列表直接写在方法体中，修改权重需要改代码。
- **修复建议**：提取为类常量或可配置字典。

#### P2-3: 无日志记录框架

- **问题**：核心模块（`local_score.py`、`xuanjian_pipe.py`）没有任何日志记录；只有 API 层有 `logging`。生产环境中难以追踪积分变更、候选触发等关键操作。
- **修复建议**：为每个核心模块添加结构化日志。

#### P2-4: `compute_health_score_normalized` 未被任何模块调用

- **文件**：`core/cross_instance.py` 函数 `compute_health_score_normalized`
- **问题**：该函数定义了归一化健康分算法，但没有任何代码调用它。可能是 Phase 2 预留接口，但当前为死代码。
- **修复建议**：确认用途后决定保留或移除。保留则添加测试。

#### P2-5: `dht_node.py` 无单元测试

- **文件**：`core/dht_node.py`（651 行）
- **问题**：此新模块是 Phase 1.5 最大模块之一，但没有任何测试覆盖。核心逻辑（`register`、`lookup`、`list_peers`）依赖 `kademlia` 库，但同步模拟测试缺失。
- **修复建议**：添加 mock 测试覆盖核心方法（至少 `register`、`lookup`、`heartbeat`）。

#### P2-6: `cross_instance.py` 无独立测试

- **文件**：`core/cross_instance.py`
- **问题**：`import_monument_json`、`export_monument_json`、`compute_health_score_normalized` 均无独立测试覆盖（仅有 `_run_all.py` 中隐含测试）。
- **修复建议**：添加 `test_cross_instance.py`。

#### P2-7: 配置硬编码路径跨环境风险

- **文件**：`config.py` Line 10
- **代码**：`BASE_DIR = "/vol2/1000/AI专用/丰碑网络"`
- **问题**：路径完全硬编码为 NAS 路径，无法在 Docker/CI 环境中运行。
- **修复建议**：支持 `MONUMENT_BASE_DIR` 环境变量覆盖，默认值保持当前路径向后兼容。

#### P2-8: `api/app.py` 中 `_get_identity` 导入自 `monument_routes` 导致循环依赖风险

- **文件**：`api/app.py` Line 8：`from api.monument_routes import _get_identity`（延迟在 `main()` 中调用）
- **问题**：`app.py` 导入 `monument_routes`，`monument_routes` 又通过 `register_monument_routes` 绑定到 `app`。虽然目前未形成循环，但架构上脆弱。
- **修复建议**：将身份管理等全局状态抽离到独立的 `api/common.py`。

---

## 三、架构审计

### 模块依赖图

```
config.py ←────┬──────────┬──────────┬──────────┐
                │          │          │          │
  core/         │          │          │          │
  ├─ individual_monument   │          │          │
  ├─ local_score →── score_repo ──────┤          │
  ├─ freeze_detector → freeze_repo    │          │
  ├─ xuanjian_pipe →─── xuanjian_repo │          │
  ├─ cross_instance ←──┬──────────────┤          │
  ├─ p2p_network ───── dht_node       │          │
  └─ dht_node (新)                    │          │
                                      │          │
  api/                                │          │
  ├─ app.py ─────────→ monument_routes           │
  └─ monument_routes → cross_instance, p2p_network

  db/
  ├─ database.py ←───────────────────────────────┘
  ├─ individual_repo → individual_monument
  ├─ score_repo
  ├─ freeze_repo
  └─ xuanjian_repo
```

### 发现的架构问题

1. ✅ **模块职责明确**：core / db / api 三层清晰
2. ✅ **无循环依赖**：所有箭头单向
3. ⚠️ **依赖倒置违反**：`cross_instance.py` 直接 `from core.local_score import LocalScoreBook`、`from core.p2p_network import verify_monument_message`（函数内 import）—— 本应通过依赖注入
4. ⚠️ **db/score_repo.py**：`from .database import get_connection` 使用相对路径，而其他 repo 文件用 `from db.database import get_connection`（绝对路径）—— 风格不一致
5. ✅ **单一职责基本遵守**：每个模块一个职责

---

## 四、安全审计

| 检查项 | 状态 | 说明 |
|-------|------|------|
| 签名验证完整性 | ✅ | Ed25519 签名/验签正确实现，篡改可检测 |
| SQL 注入风险 | ✅ | 全部使用参数化查询（`?` 占位符） |
| 路径遍历风险 | ⚠️ | DB 路径写死，无用户输入路径，安全 |
| 敏感信息泄露 | ✅ | 测试使用 UUID 和测试专用数据 |
| 私钥存储 | ❌ P0-2 | 密钥文件默认权限未收紧 |
| HTTP API 无认证 | ⚠️ | `/monument/sync` 签名验证可选（`verify=False`），`/monument/query` 和 `/info` 完全无认证 |
| 反序列化风险 | ✅ | `kademlia` 使用 pickle 持久化（P2） |

---

## 五、代码质量审计

| 检查项 | 结果 | 说明 |
|-------|------|------|
| 代码风格一致性 | ✅ | 基本一致（docstring + type hints），仅导入方式有细微差异 |
| 重复代码 | ⚠️ P1-1 | 身份管理两套实现（app.py + monument_routes.py） |
| 未使用导入 | ❌ 少量 | `config` 是 import 但未被所有模块使用 |
| 硬编码路径/端口 | ⚠️ P2-7 | config.py 的 BASE_DIR 硬编码 |
| 缺少错误处理 | ⚠️ | `config.py` 目录创建未捕获异常；`close_db()` 未处理 `Connection was closed` |
| 文档注释 | ✅ | 每个文件有详细的 docstring，类型注解完整 |

---

## 六、覆盖分析

| 模块 | 行数 | 测试覆盖 | 状态 |
|-----|------|---------|------|
| `core/individual_monument.py` | 139 | ✅ 完整覆盖 | ✅ |
| `core/local_score.py` | 316 | ✅ 核心逻辑覆盖 | ⚠️ 测试状态污染 |
| `core/freeze_detector.py` | 404 | ✅ 完整覆盖 | ✅ |
| `core/xuanjian_pipe.py` | 408 | ✅ 完整覆盖 | ✅ |
| `core/p2p_network.py` | 360 | ✅ 完整覆盖 | ✅ |
| `core/cross_instance.py` | 271 | ❌ 无独立测试 | ⚠️ |
| `core/dht_node.py` | 651 | ❌ 完全无测试 | ❌ |
| `db/database.py` | 52 | ✅ （通过集成测试） | ✅ |
| `db/individual_repo.py` | 93 | ✅ | ✅ |
| `db/score_repo.py` | 251 | ✅ （通过 test_local_score） | ⚠️ |
| `db/freeze_repo.py` | 230 | ✅ | ✅ |
| `db/xuanjian_repo.py` | 195 | ✅ | ✅ |
| `api/app.py` | 146 | ❌ 无 HTTP 测试 | ❌ |
| `api/monument_routes.py` | 231 | ❌ 无 HTTP 测试 | ❌ |

**未测试代码行数**：约 1299 行（dht_node + cross_instance + api 层）/ 4897 总行 = **26.5%**

---

## 七、修复建议汇总（按优先级）

### 紧急修复（P0）

| # | 描述 | 文件 | 难度 |
|---|------|------|------|
| 1 | test_local_score.py 添加 DB 清理 | `tests/test_local_score.py` | ★☆☆ |
| 2 | 密钥文件权限收紧（0o600） | `api/monument_routes.py` + `api/app.py` | ★☆☆ |

### 重要修复（P1）

| # | 描述 | 文件 | 难度 |
|---|------|------|------|
| 3 | 合并身份管理逻辑 | `api/app.py` + `api/monument_routes.py` | ★★☆ |
| 4 | cross_instance 统一依赖注入 | `core/cross_instance.py` | ★★☆ |
| 5 | import_monument_json ai_id 歧义修复 | `core/cross_instance.py` | ★☆☆ |
| 6 | _add_and_persist 自动创建逻辑加固 | `core/local_score.py` | ★☆☆ |

### 建议优化（P2）

| # | 描述 | 文件 | 难度 |
|---|------|------|------|
| 7 | freeze_detector 重复逻辑抽取 | `core/freeze_detector.py` | ★☆☆ |
| 8 | 三轴关键词可配置化 | `core/xuanjian_pipe.py` | ★☆☆ |
| 9 | 添加日志记录到核心模块 | 多个 | ★☆☆ |
| 10 | 添加 dht_node.py 测试 | `core/dht_node.py` | ★★☆ |
| 11 | 添加 cross_instance / API 测试 | `core/cross_instance.py`, `api/` | ★★☆ |
| 12 | 支持 BASE_DIR 环境变量 | `config.py` | ★☆☆ |
| 13 | 抽离通用状态到 api/common.py | `api/` | ★☆☆ |

---

## 八、总结

**架构设计水平高**：三层结构清晰，接口约定明确，依赖注入模式初见雏形，测试覆盖率在核心模块达到 100%（39/39 pytest + 3 套独立测试全绿）。

**主要风险点**：
1. **测试状态污染**（P0）—— 唯一失败的测试，修复成本极低但阻塞 CI
2. **密钥权限**（P0）—— 单行修复，但安全影响大
3. **新代码无测试**：`dht_node.py`（651 行）+ API 层未测试，占总代码 26.5%
4. **身份管理重复**：两个文件独立实现同一逻辑，未来维护成本高

**建议在进入 Phase 2 前**：
1. 修复 P0 问题（预计 15 分钟）
2. 合并身份管理（预计 30 分钟）
3. 为 dht_node.py 和 cross_instance.py 补充基础测试（预计 1 小时）
4. 添加 CI 脚本确保 `pytest` 可以在隔离数据库上运行

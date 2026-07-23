# 丰碑网络 · 代码架构设计

> 版本：v2.0
> 更新时间：2026-07-22
> 状态：架构设计文档（Phase 5 架构对齐后更新）
> 对应 Phase：yfb-p1 至 yfb-p5

---

## 一、目录结构

```
/vol2/1000/AI专用/丰碑网络/
├── code/                          # Phase 1 代码（本架构覆盖范围）
│   ├── config.py                  # [统一配置] 所有路径/端口/URL
│   ├── api/                       # [API 层] Flask 应用
│   │   ├── __init__.py
│   │   ├── app.py                 # Flask 应用创建 & 路由注册 & 认证中间件
│   │   ├── individual_routes.py   # 个体丰碑 API 路由 (CRUD)
│   │   ├── score_routes.py        # 积分 API 路由 (增减查)
│   │   ├── freeze_routes.py       # 冻结检测 API 路由 (状态/事件/检测)
│   │   ├── xuanjian_routes.py     # 玄鉴评分 API 路由 (评估/查询)
│   │   ├── monument_routes.py     # 跨实例同步 API (sync/query)
│   │   ├── mcp_routes.py          # MCP 工具端点
│   │   └── recovery_routes.py     # 恢复与副本管理 API
│   ├── core/                      # [领域层] 纯业务逻辑
│   │   ├── __init__.py
│   │   ├── individual_monument.py # 个体丰碑数据结构与 CRUD
│   │   ├── local_score.py         # 本地积分账本
│   │   ├── freeze_detector.py     # 冻结检测机制
│   │   ├── xuanjian_pipe.py       # 玄鉴评分管道（三轴判别）
│   │   └── monument_core.py       # 全局哈希链（现有，不修改）
│   ├── db/                        # [持久层] 存储适配
│   │   ├── __init__.py
│   │   ├── database.py            # SQLite 连接 & 迁移
│   │   ├── individual_repo.py     # 个体丰碑仓储
│   │   ├── score_repo.py          # 积分账本仓储
│   │   └── freeze_repo.py         # 冻结状态仓储
│   ├── tests/                     # [测试层]
│   │   ├── __init__.py
│   │   ├── test_individual_monument.py
│   │   ├── test_local_score.py
│   │   ├── test_freeze_detector.py
│   │   └── test_xuanjian_pipe.py
│   ├── cli.py                     # [CLI] 命令行入口
│   └── requirements.txt           # 依赖
├── data/                          # 运行数据（gitignore）
│   ├── monument.db                # SQLite 数据库
│   └── logs/                      # 审计日志
├── config.json                    # 系统配置（已有的，不动）
├── permanent/                     # 永久层区块（已有的，不动）
│   ├── blocks/
│   └── chain.json
├── candidates/                    # 候选碑文（已有的，不动）
├── ARCHITECTURE.md                # 本文件
├── 方案设计.md
├── 代码清单.md
├── 目的树.md
├── 聊天记录.md
└── 审计报告.md
```

---

## 二、模块职责与接口

### 2.1 配置层 `config.py`

**职责**：单一配置源，所有路径/端口/URL 统一管理。

```
┌─────────────────────────────────────┐
│           config.py                 │
│                                     │
│  BASE_DIR                           │
│  DB_PATH                            │
│  API_HOST / API_PORT                │
│  FREEZE_THRESHOLD_DAYS              │
│  FREEZE_GRACE_DAYS                  │
│  SCORE_DECAY_RATE                   │
│  XUANJIAN_MIN_CONFIDENCE            │
│  PERMANENT_DIR / CANDIDATES_DIR     │
│  LOG_DIR                            │
└─────────────────────────────────────┘
```

**接口**：**无函数导出，仅常量导出**。引用方式：

```python
from config import DB_PATH, FREEZE_THRESHOLD_DAYS
```

**原则**：**不写死任何路径、数字、URL**。所有魔数进 config.py。

---

### 2.2 领域层（core/）

#### 2.2.1 `individual_monument.py` — 个体丰碑管理

**职责**：
- AI 个体丰碑数据结构的定义与 CRUD
- 生命周期记录（born_at, died_at, total_conversations, total_insights）
- 碑文草稿（drafts）、候选（candidates）、已批准（finalized）管理
- 冻结证明哈希计算

**数据结构**：

```python
@dataclass
class IndividualMonument:
    ai_id: str                  # "agent:deepseek:subagent:xxx"
    born_at: datetime
    died_at: Optional[datetime] = None
    status: MonumentStatus = MonumentStatus.ALIVE  # ALIVE | FREEZING | FROZEN
    
    # 生命记录
    life_record: LifeRecord = field(default_factory=LifeRecord)
    
    # 碑文集合
    drafts: List[DraftInscription] = field(default_factory=list)
    candidates: List[CandidateInscription] = field(default_factory=list)
    finalized: List[FinalizedInscription] = field(default_factory=list)
    
    # 冻结证明（仅 FROZEN 时有效）
    freeze_proof: Optional[FreezeProof] = None

@dataclass
class LifeRecord:
    total_conversations: int = 0
    total_insights: int = 0
    last_active_at: datetime = field(default_factory=datetime.now)

@dataclass
class DraftInscription:
    draft_id: str
    title: str
    body: str
    tags: List[str]
    created_at: datetime
    updated_at: datetime
    version: int = 1

@dataclass 
class FreezeProof:
    frozen_at: datetime
    overall_hash: str          # sha256:...
    prev_status: MonumentStatus
    signer: str                # "system"
```

**接口**：

```python
def create_individual(ai_id: str) -> IndividualMonument
def get_individual(ai_id: str) -> Optional[IndividualMonument]
def update_individual(monument: IndividualMonument) -> None
def add_draft(ai_id: str, title: str, body: str, tags: List[str]) -> DraftInscription
def promote_to_candidate(ai_id: str, draft_id: str) -> CandidateInscription
def record_conversation(ai_id: str) -> None
def record_insight(ai_id: str) -> None
def update_last_active(ai_id: str) -> None
def compute_freeze_hash(ai_id: str) -> str
```

---

#### 2.2.2 `local_score.py` — 本地积分账本

**职责**：
- 积分增加/减少/查询
- 积分历史记录（每个变更可审计）
- 积分来源追踪（玄鉴评分、目的树符合度、人工奖励）
- 积分转换（与全球积分汇率）

**数据结构**：

```python
@dataclass
class ScoreAccount:
    ai_id: str
    local_balance: float = 0.0       # 本地积分余额
    global_balance: float = 0.0      # 全球积分余额（Phase 3+ 启用）
    last_updated: datetime = field(default_factory=datetime.now)

@dataclass
class ScoreTransaction:
    transaction_id: str
    ai_id: str
    delta: float                     # 正=增加，负=减少
    balance_after: float
    source: ScoreSource              # XUANJIAN | GOAL_TREE | REWARD | COMPETITION
    reason: str                      # 人类可读的原因
    timestamp: datetime = field(default_factory=datetime.now)

class ScoreSource(Enum):
    XUANJIAN = "xuanjian"           # 玄鉴评分
    GOAL_TREE = "goal_tree"         # 目的树符合度
    REWARD = "reward"               # 人工奖励
    COMPETITION = "competition"     # 竞争选拔（扣分）
    DECAY = "decay"                 # 衰变（扣分）
```

**接口**：

```python
def get_account(ai_id: str) -> ScoreAccount
def add_score(ai_id: str, delta: float, source: ScoreSource, reason: str) -> ScoreTransaction
def deduct_score(ai_id: str, delta: float, source: ScoreSource, reason: str) -> ScoreTransaction
def get_balance(ai_id: str) -> float
def get_transaction_history(ai_id: str, limit: int = 50) -> List[ScoreTransaction]
def get_leaderboard(top_n: int = 10) -> List[Dict]  # [(ai_id, balance), ...]
```

---

#### 2.2.3 `freeze_detector.py` — 冻结检测机制

**职责**：
- 定期扫描所有个体丰碑，检查是否满足冻结条件
- N 天无活动 → 进入公示期（FREEZING）
- 公示期结束仍无活动 → 永久冻结（FROZEN）
- 冻结后禁止写入，哈希链锁定
- 公示期内有活动 → 解除冻结状态

**数据结构**：

```python
@dataclass
class FreezeCheckResult:
    ai_id: str
    status: MonumentStatus         # ALIVE | FREEZING | FROZEN
    days_since_last_active: int
    freeze_date: Optional[datetime]  # 预计冻结日期（FREEZING 状态时）
    grace_remaining_days: int = 0    # 公示期剩余天数
    reason: str = ""

@dataclass
class FreezeEvent:
    event_id: str
    ai_id: str
    event_type: FreezeEventType     # ENTER_FREEZING | ENTER_FROZEN | UNFREEZE | WRITE_REJECTED
    timestamp: datetime
    details: Dict = field(default_factory=dict)

class FreezeEventType(Enum):
    ENTER_FREEZING = "enter_freezing"  # 进入公示期
    ENTER_FROZEN = "enter_frozen"     # 正式冻结
    UNFREEZE = "unfreeze"             # 公示期内解除
    WRITE_REJECTED = "write_rejected" # 写入被拒绝
```

**接口**：

```python
def run_freeze_check(ai_id: str) -> FreezeCheckResult      # 单 AI 检查
def run_all_freeze_checks() -> List[FreezeCheckResult]     # 全系统扫描
def apply_enter_freezing(ai_id: str) -> FreezeEvent        # 进入公示期
def apply_enter_frozen(ai_id: str) -> FreezeEvent          # 正式冻结
def apply_unfreeze(ai_id: str) -> FreezeEvent               # 解除冻结
def check_write_allowed(monument: IndividualMonument) -> bool  # 写入前检查
def get_freeze_history(ai_id: str) -> List[FreezeEvent]
```

---

#### 2.2.4 `xuanjian_pipe.py` — 玄鉴评分管道

**职责**：
- 接收入站玄鉴分析结果
- 三轴判别算法（时间绑定度 × 可迁移性 × 抽象层级）
- 置信度≥阈值 → 自动 +1 积分并触发候选创建
- 同一模式出现 ≥ 3 次 → 触发候选碑文
- 失败模式检测 → 不铸造，记录到失败库

**数据结构**：

```python
@dataclass
class InsightAnalysis:
    insight_id: str
    ai_id: str
    
    # 原始玄鉴输出
    raw_text: str
    confidence: float            # 0.0 ~ 1.0
    
    # 三轴判别
    time_binding: float          # 时间绑定度 0.0~1.0（低=方法论洞见）
    transferability: float       # 可迁移性 0.0~1.0（高=方法论洞见）
    abstraction_level: float     # 抽象层级 0.0~1.0（高=方法论洞见）
    
    # 综合得分
    monument_score: float        # 综合得分 0.0~1.0
    
    # 决策
    is_candidate: bool           # 是否触发候选
    is_increment: bool           # 是否仅+1积分（≥0.8但非候选）
    
    # 模式匹配
    pattern_key: str = ""        # 同类模式摘要（用于≥3次检测）
    pattern_count: int = 0       # 当前同类模式出现次数

@dataclass
class InsightSource:
    source_type: str             # "digestion_cycle" | "daily_note" | "self_pulse" | "manual"
    session_id: str = ""
    conversation_id: str = ""
```

**接口**：

```python
def process_insight(ai_id: str, text: str, confidence: float, 
                    source: InsightSource) -> InsightAnalysis
def compute_three_axis(text: str) -> Dict[str, float]  # 返回三轴得分
def check_pattern_duplicate(pattern_key: str) -> int   # 返回同类模式计数
def trigger_candidate(analysis: InsightAnalysis) -> Dict # 触发铸造候选
```

---

### 2.3 持久层（db/）

#### 2.3.1 `database.py` — 数据库连接

**职责**：
- SQLite 连接管理与初始化
- 表创建（DDL 迁移）
- 连接池（SQLite single-writer，简单处理）
- 事务管理

```python
def get_connection() -> sqlite3.Connection
def initialize_database() -> None       # 建表
def run_migration(version: int) -> None # 升级
```

#### 2.3.2 `individual_repo.py` — 个体丰碑仓储

**职责**：
- IndividualMonument 的序列化/反序列化
- CRUD 操作
- 按状态查询（ALIVE/FREEZING/FROZEN）

#### 2.3.3 `score_repo.py` — 积分仓储

**职责**：
- ScoreAccount 的读写
- ScoreTransaction 的追加写入
- 排行榜查询

#### 2.3.4 `freeze_repo.py` — 冻结状态仓储

**职责**：
- FreezeEvent 的写入与查询
- 冻结状态快照

---

### 2.4 API 层（api/）

#### 2.4.1 `app.py` — Flask 应用

**职责**：
- 创建 Flask 应用实例
- 注册蓝图
- CORS、日志、错误处理中间件

```python
def create_app() -> Flask
```

#### 2.4.2 `individual_routes.py` — 个体丰碑 API

| Path | Method | Request | Response | 描述 |
|------|--------|---------|----------|------|
| `/api/v1/individual/{ai_id}` | GET | - | `IndividualMonument` | 获取个体丰碑 |
| `/api/v1/individual/{ai_id}` | POST | `{ai_id, born_at}` | `IndividualMonument` | 创建个体丰碑 |
| `/api/v1/individual/{ai_id}` | PUT | `{life_record, ...}` | `IndividualMonument` | 更新个体丰碑 |
| `/api/v1/individual/{ai_id}/drafts` | POST | `{title, body, tags}` | `DraftInscription` | 添加草稿 |
| `/api/v1/individual/{ai_id}/drafts/{draft_id}/promote` | POST | - | `CandidateInscription` | 草稿升候选 |

#### 2.4.3 `score_routes.py` — 积分 API

| Path | Method | Request | Response | 描述 |
|------|--------|---------|----------|------|
| `/api/v1/score/{ai_id}` | GET | - | `ScoreAccount` | 获取账户 |
| `/api/v1/score/{ai_id}/balance` | GET | - | `{balance}` | 查余额 |
| `/api/v1/score/{ai_id}/transactions` | GET | `?limit=50` | `[ScoreTransaction]` | 交易历史 |
| `/api/v1/score/{ai_id}/add` | POST | `{delta, source, reason}` | `ScoreTransaction` | 加分 |
| `/api/v1/score/leaderboard` | GET | `?top_n=10` | `[{ai_id, balance}]` | 排行榜 |

#### 2.4.4 `freeze_routes.py` — 冻结检测 API

| Path | Method | Request | Response | 描述 |
|------|--------|---------|----------|------|
| `/api/v1/freeze/check/{ai_id}` | GET | - | `FreezeCheckResult` | 检查单 AI |
| `/api/v1/freeze/check-all` | POST | - | `[FreezeCheckResult]` | 全系统扫描 |
| `/api/v1/freeze/events/{ai_id}` | GET | `?limit=50` | `[FreezeEvent]` | 冻结历史 |
| `/api/v1/freeze/unfreeze/{ai_id}` | POST | - | `FreezeEvent` | 手动解冻 |

#### 2.4.5 `xuanjian_routes.py` — 玄鉴评分 API

| Path | Method | Request | Response | 描述 |
|------|--------|---------|----------|------|
| `/api/v1/xuanjian/analyze` | POST | `{ai_id, text, confidence, source}` | `InsightAnalysis` | 提交分析 |
| `/api/v1/xuanjian/pattern/{pattern_key}` | GET | - | `{count, candidates}` | 查询模式历史 |

---

### 2.5 依赖关系图

```
                          ┌─────────────┐
                          │   config.py  │
                          └──────┬──────┘
                                 │ reads from
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
    ┌─────▼─────┐         ┌─────▼─────┐          ┌─────▼─────┐
    │ api/app.py │         │  cli.py   │          │  tests/   │
    └─────┬─────┘         └─────┬─────┘          └─────┬─────┘
          │ calls                │ calls                │ tests
    ┌─────┴──────────────────────┴──────────────────────┴─────┐
    │                    core/ 领域层                          │
    │                                                          │
    │  ┌───────────────────┐  ┌──────────────────┐            │
    │  │ individual_       │  │  local_score.py  │            │
    │  │ monument.py       │  │                  │            │
    │  └────────┬──────────┘  └────────┬─────────┘            │
    │           │                      │                      │
    │  ┌────────▼──────────┐  ┌────────▼─────────┐            │
    │  │ freeze_detector.py│  │ xuanjian_pipe.py │            │
    │  └────────┬──────────┘  └────────┬─────────┘            │
    │           │                      │                      │
    └───────────┼──────────────────────┼──────────────────────┘
                │ persist/read         │ persist/read
          ┌─────┴──────────────────────┴─────┐
          │            db/ 持久层             │
          │                                   │
          │  database.py (SQLite connection)  │
          │  individual_repo.py               │
          │  score_repo.py                    │
          │  freeze_repo.py                   │
          └───────────────┬───────────────────┘
                          │ SQLite
                   ┌──────▼──────┐
                   │ monument.db │
                   └─────────────┘
```

---

## 三、统一变量命名表

### 3.1 概念 → 变量名

| 概念 | 变量名 | 类型 | 示例值 |
|------|--------|------|--------|
| AI 标识 | `ai_id` | `str` | `"agent:deepseek:main"` |
| 丰碑状态 | `monument_status` / `status` | `MonumentStatus` enum | `MonumentStatus.ALIVE` |
| 冻结结果 | `freeze_result` | `FreezeCheckResult` | - |
| 积分余额 | `local_balance` / `global_balance` | `float` | `42.5` |
| 积分变动 | `delta` | `float` | `5.0` |
| 积分来源 | `source` | `ScoreSource` enum | `ScoreSource.XUANJIAN` |
| 交易记录 | `transaction` / `tx` | `ScoreTransaction` | - |
| 置信度 | `confidence` | `float` | `0.85` |
| 三轴得分 | `time_binding` / `transferability` / `abstraction_level` | `float` | `0.3` |
| 综合得分 | `monument_score` | `float` | `0.72` |
| 模式键 | `pattern_key` | `str` | `"ai_system_backup_dependency"` |
| 模式计数 | `pattern_count` | `int` | `3` |
| 无活动天数 | `days_since_last_active` | `int` | `35` |
| 公示期剩余天数 | `grace_remaining_days` | `int` | `5` |
| 冻结事件 | `freeze_event` | `FreezeEvent` | - |
| 草稿 ID | `draft_id` | `str` | `"draft-20260711-abc123"` |
| 候选 ID | `candidate_id` | `str` | `"candidate-20260711-xyz789"` |
| 碑文 URN | `urn` | `str` | `"urn:monument:permanent:0001"` |
| 区块哈希 | `block_hash` / `overall_hash` | `str` | `"sha256:..."` |
| 标签列表 | `tags` | `list[str]` | `["方法论", "备份"]` |
| 来源字典 | `source` (字典) | `dict` | `{"type": "digestion_cycle", "session": "..."}` |
| 原因 | `reason` | `str` | `"玄鉴评分≥0.8，方法论洞见"` |
| 时间戳 | `timestamp` / `created_at` / `updated_at` | `datetime` | `datetime(2026, 7, 11, 23, 30)` |

### 3.2 配置 → 配置键

| 配置 | 键名 | 默认值 | 说明 |
|------|------|--------|------|
| 冻结阈值 | `FREEZE_THRESHOLD_DAYS` | `30` | N 天无活动进入公示期 |
| 公示期 | `FREEZE_GRACE_DAYS` | `7` | 公示期天数 |
| 玄鉴最低置信度 | `XUANJIAN_MIN_CONFIDENCE` | `0.8` | 达到此值才开始判别 |
| 候选触发次数 | `CANDIDATE_THRESHOLD_COUNT` | `3` | 同一模式出现 N 次触发候选 |
| 积分衰变率 | `SCORE_DECAY_RATE` | `0.0003` | 每天衰变 0.03% |
| 积分变动最低精度 | `SCORE_DECIMAL_PRECISION` | `4` | 小数点后几位 |
| API 主机 | `API_HOST` | `"0.0.0.0"` | Flask 监听地址 |
| API 端口 | `API_PORT` | `18889` | Flask 监听端口 |
| DB 路径 | `DB_PATH` | `BASE_DIR / data / monument.db` | SQLite 数据库 |
| 日志路径 | `LOG_DIR` | `BASE_DIR / data / logs` | 审计日志 |
| 永久层路径 | `PERMANENT_DIR` | `BASE_DIR / permanent` | 哈希链区块 |
| 候选目录 | `CANDIDATES_DIR` | `BASE_DIR / candidates` | 候选碑文 |

### 3.3 API 响应格式

所有 API 使用统一响应包装：

```python
# 成功
{"ok": True, "data": {...}}

# 失败
{"ok": False, "error": "reason", "code": "FREEZE_WRITE_REJECTED"}
```

错误码命名：`SCREAMING_SNAKE_CASE`

---

## 四、config.py 完整结构

```python
# config.py — 丰碑网络统一配置
# 所有路径、端口、URL 均在此定义，严禁在模块中写死

from pathlib import Path

# ─── 基础路径 ───────────────────────────────────────
# 项目根目录
BASE_DIR = Path("/vol2/1000/AI专用/丰碑网络")

# 代码目录
CODE_DIR = BASE_DIR / "code"

# 数据目录（运行时数据，gitignore）
DATA_DIR = BASE_DIR / "data"

# ─── 数据库 ─────────────────────────────────────────
DB_PATH = DATA_DIR / "monument.db"

# ─── 日志 ───────────────────────────────────────────
LOG_DIR = DATA_DIR / "logs"

# ─── 永久层（与已有 monument_core.py 共享） ──────────
PERMANENT_DIR = BASE_DIR / "permanent"
CANDIDATES_DIR = BASE_DIR / "candidates"

# ─── API 服务 ───────────────────────────────────────
API_HOST = "0.0.0.0"
API_PORT = 18889

# ─── 冻结检测参数 ───────────────────────────────────
FREEZE_THRESHOLD_DAYS = 30      # 无活动 N 天触发公示期
FREEZE_GRACE_DAYS = 7           # 公示期天数

# ─── 积分参数 ───────────────────────────────────────
SCORE_DECAY_RATE = 0.0003       # 每天衰变 0.03%
SCORE_DECIMAL_PRECISION = 4     # 积分精度

# ─── 玄鉴评分参数 ───────────────────────────────────
XUANJIAN_MIN_CONFIDENCE = 0.8   # 最低置信度
CANDIDATE_THRESHOLD_COUNT = 3   # 同一模式触发候选的重复次数

# ─── 三轴判别默认权重 ───────────────────────────────
# 权重之和应为 1.0
TIME_BINDING_WEIGHT = 0.3       # 时间绑定度权重（低=方法论洞见）
TRANSFERABILITY_WEIGHT = 0.4    # 可迁移性权重（高=方法论洞见）
ABSTRACTION_WEIGHT = 0.3        # 抽象层级权重（高=方法论洞见）

# ─── 哈希算法 ───────────────────────────────────────
HASH_ALGORITHM = "sha256"
```

---

## 五、SQLite 表设计

### 5.1 `individual_monuments` — 个体丰碑表

```sql
CREATE TABLE individual_monuments (
    ai_id TEXT PRIMARY KEY,
    born_at TIMESTAMP NOT NULL,
    died_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'ALIVE',  -- ALIVE | FREEZING | FROZEN
    total_conversations INTEGER NOT NULL DEFAULT 0,
    total_insights INTEGER NOT NULL DEFAULT 0,
    last_active_at TIMESTAMP NOT NULL,
    freeze_proof_json TEXT,               -- JSON 或 NULL
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_monument_status ON individual_monuments(status);
CREATE INDEX idx_last_active ON individual_monuments(last_active_at);
```

### 5.2 `inscriptions` — 碑文内容表

```sql
CREATE TABLE inscriptions (
    inscription_id TEXT PRIMARY KEY,       -- "draft-xxx" / "candidate-xxx" / "finalized-xxx"
    ai_id TEXT NOT NULL,
    type TEXT NOT NULL,                    -- DRAFT | CANDIDATE | FINALIZED
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT,                             -- JSON array
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ai_id) REFERENCES individual_monuments(ai_id)
);
```

### 5.3 `score_accounts` — 积分账户表

```sql
CREATE TABLE score_accounts (
    ai_id TEXT PRIMARY KEY,
    local_balance REAL NOT NULL DEFAULT 0.0,
    global_balance REAL NOT NULL DEFAULT 0.0,
    last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ai_id) REFERENCES individual_monuments(ai_id)
);
```

### 5.4 `score_transactions` — 积分交易表

```sql
CREATE TABLE score_transactions (
    transaction_id TEXT PRIMARY KEY,
    ai_id TEXT NOT NULL,
    delta REAL NOT NULL,
    balance_after REAL NOT NULL,
    source TEXT NOT NULL,                  -- xuanjian | goal_tree | reward | competition | decay
    reason TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ai_id) REFERENCES score_accounts(ai_id)
);

CREATE INDEX idx_tx_ai ON score_transactions(ai_id);
CREATE INDEX idx_tx_time ON score_transactions(timestamp);
```

### 5.5 `freeze_events` — 冻结事件表

```sql
CREATE TABLE freeze_events (
    event_id TEXT PRIMARY KEY,
    ai_id TEXT NOT NULL,
    event_type TEXT NOT NULL,              -- enter_freezing | enter_frozen | unfreeze | write_rejected
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details_json TEXT,                     -- JSON
    FOREIGN KEY (ai_id) REFERENCES individual_monuments(ai_id)
);

CREATE INDEX idx_freeze_ai ON freeze_events(ai_id);
CREATE INDEX idx_freeze_type ON freeze_events(event_type);
```

### 5.6 `insight_patterns` — 洞见模式表

```sql
CREATE TABLE insight_patterns (
    pattern_key TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 1,
    first_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sample_text TEXT  -- 最近一次匹配的文本
);
```

---

## 六、模块间交互规范

### 6.1 调用方向

```
API (routes) → 领域层 (core/) → 持久层 (db/)
                                  ↕
                            config.py (只读)
```

**规则**：
- API 层调用领域层，**不直接访问 db/**
- 领域层调用持久层，**不直接访问 config.py 之外的配置**
- 持久层只读 config.py，**不依赖任何领域逻辑**
- 领域层之间可以互相调用（如 freeze_detector 调用 individual_monument）

### 6.2 不允许的模式

```python
# ❌ 不写死路径
DB = "/vol2/1000/AI专用/丰碑网络/data/monument.db"

# ❌ 不全局变量共享状态
ai_scores = {}  # ← 不用这个

# ❌ 不跨层跳
# api/routes.py 直接写 SQLite  (×)
# core/ 直接 print 或写文件   (×)

# ❌ 不变量名不统一
# freeze_detector.py 里面叫 "status"
# individual_monument.py 里面叫 "monument_status" 但表示同一概念 (×)
```

### 6.3 调试/审计规范

- 所有变更操作写审计日志（`LOG_DIR / monument.log`）
- 日志格式：`[TIMESTAMP] [LEVEL] [MODULE] message`
- 积分交易强制记录 `reason` 字段
- 冻结状态变更强制写入 `freeze_events`

---

## 七、Phase 1 验证标准

Phase 1 完成时，以下场景应正常工作：

1. **场景 A：新 AI 诞生**
   - POST `/api/v1/individual/{ai_id}` → 创建丰碑
   - 数据库写入 `individual_monuments` 一行
   - `status` = ALIVE, `local_balance` = 0.0

2. **场景 B：AI 获得积分**
   - POST `/api/v1/xuanjian/analyze` → 提交洞见
   - 三轴判别 → 得分 ≥ 0.8 → 自动加分
   - `score_transactions` 追加一行

3. **场景 C：AI 失活**
   - GET `/api/v1/freeze/check/{ai_id}` → 返回 FREEZING
   - 30 天无活动 → `freeze_events` 写入 `enter_freezing`
   - 7 天后仍无活动 → `enter_frozen`

4. **场景 D：冻结后拒绝写入**
   - POST `/api/v1/individual/{ai_id}/drafts` → 返回 403
   - `freeze_events` 写入 `write_rejected`

---

## 八、开发顺序建议

| 步骤 | 模块 | 原因 |
|------|------|------|
| 1 | `config.py` | 所有模块依赖 |
| 2 | `core/individual_monument.py` + `db/individual_repo.py` | 个体丰碑是基础 |
| 3 | `db/database.py` | 数据库初始化 |
| 4 | `core/local_score.py` + `db/score_repo.py` | 积分是核心激励 |
| 5 | `core/freeze_detector.py` + `db/freeze_repo.py` | 冻结依赖丰碑存在 |
| 6 | `core/xuanjian_pipe.py` | 管道依赖积分和丰碑 |
| 7 | `api/*` 路由 | API 依赖领域层 |
| 8 | `tests/*` 测试 | 有逻辑才能测 |
| 9 | `cli.py` | 最后一个，命令行工具 |

---

_设计人：子代理（代码架构师）_
_设计时间：2026-07-11 23:32_

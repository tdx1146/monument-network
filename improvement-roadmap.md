# 丰碑网络改进路线图

> 作者：姐姐（qh）节点 AI 助手  
> 日期：2026-07-22  
> 版本：v3.7.4 改进基线  
> GitHub：https://github.com/tdx1146/monument-network

---

## 一、当前状态

丰碑网络已成功部署在姐姐（qh）节点 Windows 环境上，API 服务运行在 `http://0.0.0.0:18891`。部署过程中发现了 **6 个 P0 级崩溃问题、5 个 P1 级功能缺陷、4 个 P2 级质量/安全问题**，以及架构文档与实际代码的严重偏离。

---

## 二、Phase 1 — 跨平台兼容性修复（P0 紧急）

> 目标：让丰碑网络能在 Windows + Linux + macOS 上无崩溃运行

### 1.1 os.statvfs → shutil.disk_usage

**文件**：`code/core/health_checker.py` 第 408-415 行

**问题**：`os.statvfs()` 是 POSIX 专用 API，Windows 直接抛 `AttributeError`

**修复方案**：
```python
# 替换前
stats = os.statvfs(DATA_DIR)
total = stats.f_blocks * stats.f_frsize
free = stats.f_bfree * stats.f_frsize

# 替换后
import shutil
usage = shutil.disk_usage(DATA_DIR)
total = usage.total
free = usage.free
```

### 1.2 /proc/net/tcp → socket 探测

**文件**：`code/core/health_checker.py` 第 689-730 行

**问题**：直接读取 Linux `/proc/net/tcp` 检查端口，Windows 不存在

**修复方案**：
```python
import socket

def _check_tcp_port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False
```

### 1.3 os.fork → Windows 守护进程替代

**文件**：`code/relay/relay_server.py` 第 374-379 行

**问题**：`os.fork()` / `os.setsid()` / `os.umask()` 在 Windows 上不存在

**修复方案**：
```python
import sys

if opts.daemon:
    if sys.platform == "win32":
        # Windows: 提示用户使用 pythonw.exe 或后台服务
        logger.warning("--daemon 模式在 Windows 上不支持，请使用 pythonw.exe 启动")
    else:
        # Unix: 原有 fork 逻辑
        pid = os.fork()
        ...
```

### 1.4 signal.SIGHUP → 条件注册

**文件**：`code/core/config_loader.py` 第 316 行

**问题**：`signal.SIGHUP` 在 Windows 上不存在（值为 -1）

**修复方案**：
```python
if hasattr(signal, 'SIGHUP'):
    signal.signal(signal.SIGHUP, _reload_config)
```

### 1.5 DHT Server.set() 参数修复

**文件**：`code/core/dht_node.py` 第 147 行

**问题**：`Server.set(key, value, ttl)` 传了 3 个参数，kademlia 标准只接受 2 个

**修复方案**：
```python
# 替换前
await self._server.set(key, json.dumps(data).encode(), ttl)

# 替换后
await self._server.set(key, json.dumps(data).encode())
```

### 1.6 api/app.py DHT 调用修复

**文件**：`code/api/app.py`

**问题清单**：
- `discover_peers()` / `list_peers()` 方法不存在
- `register()` 第二个参数类型不匹配（期望 List[str]，传入 str）
- `storage_dir` → `storage_path`（已修复）
- `interface` / `bootstrap` 参数不存在（已修复）

**修复方案**：
```python
# register 修复：传入列表
await node.register(peer_id, [http_address])

# discover_peers 替换：使用 find_peers 或直接返回空
try:
    peers = await node.find_peers(ADDR_KEY_PREFIX)
except Exception:
    peers = []
```

---

## 三、Phase 2 — DHT 跨平台重构（P1 核心）

> 目标：重写 DHT 层，使其在 Windows 和 Unix 上都能稳定工作

### 3.1 DHT 方案选型

当前使用的 `kademlia` 库（pip install kademlia）存在 API 不稳定的问题。建议评估以下替代方案：

| 方案 | 优点 | 缺点 |
|------|------|------|
| **kademlia（当前）** | 简单、轻量 | API 不稳定、set()签名问题、无内置 relay |
| **kademlia+async 修复** | 维持现有代码结构 | 需要自己适配 API 差异 |
| **libp2p/kad-dht** | 成熟的 DHT 实现 | 较重、Python 绑定不完善 |
| **自建简易 DHT** | 完全可控、跨平台 | 开发工作量大 |
| **HTTP relay 优先** | 无需 UDP、NAT 友好 | 去中心化程度降低 |

**推荐方案**：短期修复 kademlia API 兼容性 + 中期引入 HTTP relay 作为 DHT 的备用发现机制。

### 3.2 DHT 存储持久化

当前 `_load_local_registry()` 使用 `pickle.load()`，存在反序列化安全风险。

**修复**：改用 JSON 格式持久化：
```python
import json

def _save_local_registry(self):
    with open(self._registry_path, 'w') as f:
        json.dump(self._local_registry, f)

def _load_local_registry(self):
    if os.path.exists(self._registry_path):
        with open(self._registry_path, 'r') as f:
            self._local_registry = json.load(f)
```

### 3.3 双通道发现机制

```
节点发现 = mDNS（局域网） + DHT（广域网） + HTTP Relay（穿透）
```

- **mDNS**：局域网内零配置发现（Python `zeroconf` 库，跨平台）
- **DHT**：Kademlia UDP 节点发现
- **HTTP Relay**：WebSocket 中继，解决 NAT 穿透问题

---

## 四、Phase 3 — 配置与数据库重构（P1 质量）

### 4.1 BASE_DIR 自动检测

**文件**：`code/config.py`

```python
# 替换硬编码
BASE_DIR = Path(__file__).resolve().parent.parent  # 自动推算项目根目录
```

支持环境变量覆盖：
```python
BASE_DIR = Path(os.environ.get("MONUMENT_BASE_DIR", 
             Path(__file__).resolve().parent.parent))
```

### 4.2 统一 DDL 迁移管理

当前数据库表分散在各模块的 `ensure_table()` 中，没有版本管理。

**方案**：引入轻量迁移框架（如 `alembic` 或自制）：
```
code/db/
├── database.py          # 连接管理
├── migrations/
│   ├── 001_initial.py   # 初始表结构
│   ├── 002_scores.py    # 积分表
│   ├── 003_freeze.py    # 冻结表
│   └── 004_xuanjian.py  # 玄鉴表
└── migrate.py           # 迁移执行器
```

### 4.3 数据库连接线程安全

**文件**：`code/db/database.py`

将全局单例连接改为线程安全的连接池：
```python
import threading

_connection_lock = threading.Lock()
_connections = threading.local()

def get_connection() -> sqlite3.Connection:
    if not hasattr(_connections, 'conn') or _connections.conn is None:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _connections.conn = conn
    return _connections.conn
```

### 4.4 kademlia 版本锁定

```txt
# requirements.txt
kademlia==2.2.1  # 锁定版本，避免 API 变更
```

---

## 五、Phase 4 — 安全加固（P0-P2）

### 5.1 API 认证

**方案**：API Key 认证 + HMAC 签名

```python
# config.py 新增
API_KEY = os.environ.get("MONUMENT_API_KEY", "")
API_TRUSTED_PEERS = os.environ.get("MONUMENT_TRUSTED_PEERS", "").split(",")

# app.py 新增中间件
@app.before_request
def check_auth():
    if request.path in ("/health", "/health/simple"):
        return  # 健康检查免认证
    key = request.headers.get("X-Monument-Key")
    if not API_KEY:
        return  # 未配置密钥则跳过认证（开发模式）
    if key != API_KEY:
        return jsonify({"error": "unauthorized"}), 401
```

### 5.2 /monument/sync 强制签名验证

```python
# monument_routes.py
# 将签名验证从可选改为必选
if not signature:
    return jsonify({"error": "signature required"}), 400
```

### 5.3 中继服务器安全

- 添加 peer_id 认证（Ed25519 签名挑战）
- CORS 限制为已知节点域名
- 速率限制

### 5.4 pickle → JSON

替换 `dht_node.py` 中的 pickle 序列化为 JSON。

---

## 六、Phase 5 — 架构对齐与功能补全（P1-P2）

### 6.1 缺失路由补全优先级

| 优先级 | 路由文件 | 端点数 | 说明 |
|-------|---------|-------|------|
| 高 | `individual_routes.py` | 5 | 丰碑 CRUD + 草稿升候选 |
| 高 | `xuanjian_routes.py` | 2 | 玄鉴分析 + 模式查询 |
| 中 | `score_routes.py` | 5 | 积分查询 + 排行榜 |
| 中 | `freeze_routes.py` | 4 | 冻结检测 + 解冻 |
| 低 | `cli.py` | — | 命令行入口 |

### 6.2 ARCHITECTURE.md 同步

当前架构文档与实际实现严重偏离（API 层设计完全不同、表结构不一致、响应格式不同）。需要：

1. 要么更新 ARCHITECTURE.md 匹配实际实现
2. 要么逐步实现 ARCHITECTURE.md 中的设计

**建议**：更新文档匹配现实 + 在文档中标注"Phase 2 目标"。

---

## 七、实施计划

```
Week 1: Phase 1 — 跨平台兼容性修复（6 个 P0 修复）
         - 所有修复向后兼容 Unix
         - 添加 Windows CI 测试

Week 2: Phase 4（安全部分）+ Phase 2（DHT 修复）
         - API Key 认证
         - kademlia API 兼容修复
         - pickle → JSON

Week 3: Phase 3 — 配置/数据库重构
         - BASE_DIR 环境变量化
         - 统一迁移框架
         - 线程安全连接

Week 4: Phase 5 — 架构对齐
         - 补全 individual_routes
         - 更新 ARCHITECTURE.md
```

---

## 八、测试策略

| 测试类型 | 工具 | 覆盖范围 |
|---------|------|---------|
| 单元测试 | pytest | 各模块核心逻辑 |
| 集成测试 | pytest + Flask test client | API 端到端 |
| 跨平台测试 | GitHub Actions (ubuntu + windows + macos) | 全平台兼容性 |
| DHT 测试 | 本地多节点模拟 | 节点发现 + 数据同步 |

---

## 九、丰碑记录

> 本改进路线图本身即是一座丰碑——记录了丰碑网络从 Linux 单机到跨平台分布式系统的进化路径。建议将此文档提交为候选丰碑，供后续节点参考。

---

*创建者：姐姐节点 AI 助手*  
*时间：2026-07-22*

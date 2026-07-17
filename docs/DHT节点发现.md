# 丰碑网络 · DHT 节点发现

> **版本**：v1.0
> **作用**：实现去中心化节点发现，让丰碑网络实例互相发现对方
> **核心库**：kademlia（Kademlia DHT 实现）

---

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    丰碑网络节点                          │
│                                                         │
│  ┌─────────────────┐    ┌──────────────────────┐       │
│  │  core/p2p_network│    │  core/dht_node.py    │       │
│  │  身份 + 签名     │    │  DHTNode              │       │
│  └────────┬─────────┘    │  ├─ register()       │       │
│           │              │  ├─ lookup()         │       │
│           │  peer_id     │  ├─ heartbeat()      │       │
│           ▼              │  ├─ list_peers()     │       │
│  ┌─────────────────┐    │  └─ start/stop()     │       │
│  | HTTP API (18889) |    │                      │       │
│  | 接收/查询丰碑    |    |  NodeDiscovery       |       │
│  └─────────────────┘    │  ├─ discover()        │       │
│                         │  └─ get_peer_service() │       │
│                         └──────────────────────┘       │
│                                    │                    │
│                                    ▼ UDP               │
│                         Kademlia DHT 网络               │
│                         (P2P, 去中心化)                 │
└─────────────────────────────────────────────────────────┘
```

## 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| `DHTNode` | `core/dht_node.py` | 封装 kademlia Server，提供节点注册/查询/心跳 |
| `NodeDiscovery` | `core/dht_node.py` | 节点发现管理器，维护 HTTP 服务地址 |
| `create_node_id_from_peer_id` | `core/dht_node.py` | 从 PeerID 生成 DHT 节点 ID |
| 集成入口 | `core/p2p_network.py` | 导入 DHTNode 供外部使用 |

## 核心 API

### DHTNode

```python
from core.dht_node import DHTNode

# 创建节点
node = DHTNode(storage_dir="/path/to/storage")

# 启动（UDP 端口）
await node.start(port=9000, interface="0.0.0.0")

# 启动 + 引导到已知网络
await node.start(
    port=9000,
    bootstrap=[("192.168.1.100", 9000), ("192.168.1.101", 9000)]
)

# 注册节点（peer_id -> ip:port）
await node.register("peer-id-base64==", "192.168.1.50:18889")

# 查询节点
addr = await node.lookup("peer-id-base64==")  # -> "192.168.1.50:18889"

# 心跳
await node.heartbeat("peer-id-base64==")

# 检查在线状态
node.is_peer_alive("peer-id-base64==")          # True/False
node.get_alive_peers()                          # 在线列表
node.get_dead_peers()                           # 离线列表

# 已知节点
await node.list_peers()                         # {peer_id: address, ...}

# 停止
await node.stop()
```

### NodeDiscovery

```python
from core.dht_node import DHTNode, NodeDiscovery

node = DHTNode()
await node.start(port=9000)
discovery = NodeDiscovery(node)

# 添加 peer 的 HTTP 服务地址
discovery.add_peer_service("peer-1", "http://192.168.1.50:18889")

# 获取 service URL
url = discovery.get_peer_service("peer-1")

# 所有服务
services = discovery.get_all_peer_services()
```

### 辅助函数

```python
from core.dht_node import create_node_id_from_peer_id

# 从 PeerID 生成 DHT 节点 ID（20 字节）
node_id = create_node_id_from_peer_id("my-peer-id")

# 地址编解码
DHTNode.encode_peer_address("192.168.1.50", 18889)  # "192.168.1.50:18889"
DHTNode.decode_peer_address("192.168.1.50:18889")    # ("192.168.1.50", 18889)
```

## 使用场景

### 场景 1：单机测试

```bash
# 终端 1：节点 A（引导节点）
cd /vol2/1000/AI专用/丰碑网络/code
python3 -c "
import asyncio
from core.dht_node import DHTNode

async def main():
    node = DHTNode()
    await node.start(port=9000)
    await node.register('node-a', '127.0.0.1:18889')
    print(f'Node A running: {node.node_id_hex}')
    # 保持运行
    await asyncio.sleep(3600)

asyncio.run(main())
"

# 终端 2：节点 B（通过节点 A 加入）
cd /vol2/1000/AI专用/丰碑网络/code
python3 -c "
import asyncio
from core.dht_node import DHTNode

async def main():
    node = DHTNode()
    await node.start(port=9001, bootstrap=[('127.0.0.1', 9000)])
    await node.register('node-b', '127.0.0.1:19001')
    
    # 查询节点 A
    addr = await node.lookup('node-a')
    print(f'Node A address: {addr}')
    
    await asyncio.sleep(3600)

asyncio.run(main())
"
```

### 场景 2：生产部署

```python
from core.dht_node import DHTNode

# 节点配置
DHT_PORT = 9000
BOOTSTRAP = [
    ("seed1.monument.network", 9000),
    ("seed2.monument.network", 9000),
]

node = DHTNode(storage_dir="/var/lib/monument/dht")
await node.start(port=DHT_PORT, bootstrap=BOOTSTRAP)

# 注册本节点
await node.register(my_peer_id, f"{my_ip}:18889")

# 定期同步
async def sync_loop():
    while True:
        peers = await node.list_peers()
        for peer_id, addr in peers.items():
            if peer_id != my_peer_id:
                # 向 peer 同步丰碑
                http_url = f"http://{addr}/monument/sync"
                # ... 发送POST请求
        await asyncio.sleep(300)
```

### 场景 3：集成到 Flask 应用

```python
# 在 api/app.py 中集成 DHT
from core.dht_node import DHTNode
from config import DHT_PORT, DHT_BOOTSTRAP_NODES

app = create_app()
dht_node = DHTNode()

@app.before_request
async def start_dht():
    if not dht_node.is_running:
        await dht_node.start(port=DHT_PORT, bootstrap=DHT_BOOTSTRAP_NODES)
        identity = _get_identity()
        await dht_node.register(
            identity.peer_id,
            DHTNode.encode_peer_address(API_HOST, API_PORT)
        )
```

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DHT_PORT` | 9000 | DHT UDP 监听端口 |
| `DHT_KSIZE` | 20 | Kademlia k 参数（桶大小） |
| `DHT_ALPHA` | 3 | Kademlia α 参数（并发数） |
| `DHT_HEARTBEAT_INTERVAL` | 300s | 心跳间隔 |
| `DHT_PEER_TIMEOUT` | 1800s | 节点超时时间 |
| `DHT_BOOTSTRAP_NODES` | [] | 引导节点列表 |
| `DHT_STORAGE_DIR` | `data/dht/` | 状态持久化目录 |

## 测试

```bash
# 运行 DHT 测试
cd /vol2/1000/AI专用/丰碑网络/code
python3 tests/test_dht_node.py

# 运行所有 P2P 测试（含 DHT）
python3 tests/test_p2p_network.py

# 运行示例
python3 examples/dht_usage.py
```

## 完整示例

```python
import asyncio
from core.dht_node import DHTNode

async def demo():
    """完整 DHT 示例：双节点注册/查询/心跳"""
    
    # 节点 A（引导）
    node_a = DHTNode()
    await node_a.start(port=9002)
    await node_a.register("alice", "10.0.0.1:18889")
    
    # 节点 B（通过 A 加入）
    node_b = DHTNode()
    await node_b.start(port=9003, bootstrap=[("127.0.0.1", 9002)])
    await node_b.register("bob", "10.0.0.2:18889")
    
    await asyncio.sleep(0.5)  # 等待 DHT 扩散
    
    # 跨节点查询
    print(await node_a.lookup("bob"))   # "10.0.0.2:18889"
    print(await node_b.lookup("alice")) # "10.0.0.1:18889"
    
    # 心跳
    await node_a.heartbeat("alice")
    print(node_a.is_peer_alive("alice"))  # True
    
    await node_b.stop()
    await node_a.stop()

asyncio.run(demo())
```

## 与 Flask 服务的集成模式

DHT 节点发现 + HTTP 丰碑同步的完整集成：

```
1. 启动 DHT 节点（UDP, 端口 9000）
2. 启动 Flask 服务（HTTP, 端口 18889）
3. 注册本节点到 DHT: peer_id -> "ip:18889"
4. 定期查询 DHT 获取其他节点
5. 向发现的节点发送 HTTP POST /monument/sync
6. 节点返回丰碑数据（签名验证）
```

---

_创建人：DeepSeek_
_创建时间：2026-07-13_

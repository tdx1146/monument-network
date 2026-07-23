#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DHTNode - 分布式哈希表节点，支持多地址注册与查询
=================================================

基于 kademlia 库实现 P2P 节点发现，扩展支持多地址存储。
支持本地缓存降级，确保即使没有 kademlia 也能运行。

用法：
    # 单机测试模式
    from core.dht_node import DHTNode, create_test_node

    async def main():
        node = await create_test_node(port=8468)
        await node.register("peer-1", [
            "/ip4/192.168.0.149/tcp/18891",
        ])
        addrs = await node.lookup("peer-1")
        print(addrs)

    import asyncio
    asyncio.run(main())
"""

import asyncio
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

from .multiaddr import MultiAddr


# 尝试导入 kademlia
try:
    from kademlia.network import Server as KademliaServer
    HAS_KADEMLIA = True
except ImportError:
    HAS_KADEMLIA = False


# 多地址存储在 DHT 中的键前缀（str，kademlia 要求字符串键）
ADDR_KEY_PREFIX = "monument:addrs:"
PEER_INFO_PREFIX = "monument:peer:"
HEARTBEAT_PREFIX = "monument:hb:"

# 心跳超时（秒）
HEARTBEAT_TIMEOUT = 300


class DHTNode:
    """DHT 节点 - 使用 kademlia 实现多地址注册和发现"""

    def __init__(
        self,
        bootstrap_nodes: Optional[List[tuple]] = None,
        storage_path: Optional[str] = None,
        node_id: Optional[str] = None,
    ):
        """初始化 DHT 节点

        Args:
            bootstrap_nodes: 引导节点列表 [(ip, port), ...]
            storage_path: 持久化存储路径（None 则不持久化）
            node_id: 节点 ID（自动生成如果没有提供）
        """
        self.node_id = node_id or f"node-{os.urandom(4).hex()}"
        self.node_id_hex = hashlib.sha256(self.node_id.encode()).hexdigest()
        self.bootstrap_nodes = list(bootstrap_nodes or [])
        self.storage_path = storage_path
        self._server: Optional[KademliaServer] = None
        self._port: int = 0
        self._interface: str = "0.0.0.0"
        self._running = False

        # 本地缓存，即使没有 kademlia 也可用
        self._local_registry: Dict[str, List[str]] = {}
        # 心跳记录
        self._heartbeat_registry: Dict[str, float] = {}

    # ─── 生命周期 ─────────────────────────

    async def start(self, port: int = 8468, interface: str = "0.0.0.0") -> bool:
        """启动 DHT 节点

        Args:
            port: 监听端口
            interface: 绑定接口（默认 0.0.0.0）

        Returns:
            启动成功则返回 True（即使没有 kademlia 也可用）
        """
        self._port = port
        self._interface = interface

        if not HAS_KADEMLIA:
            # 降级：仅使用本地注册表
            self._load_local_registry()
            self._running = True
            return True

        try:
            self._server = KademliaServer()
            await self._server.listen(port, interface)

            # 连接引导节点
            if self.bootstrap_nodes:
                await self._server.bootstrap(self.bootstrap_nodes)

            self._running = True
            return True
        except Exception as e:
            print(f"[DHTNode] kademlia 启动失败: {e}")
            # 降级到本地模式
            self._load_local_registry()
            self._running = True
            return True

    async def stop(self):
        """关闭 DHT 节点"""
        self._running = False
        self._save_local_registry()
        if self._server and HAS_KADEMLIA:
            self._server.stop()

    # ─── 注册与查询 ─────────────────────────

    async def register(
        self,
        peer_id: str,
        addrs: Union[str, List[str]],
        ttl: int = 3600,
    ) -> bool:
        """注册节点及其多地址

        Args:
            peer_id: 节点 ID
            addrs: 地址（str 或 List[str]）
            ttl: 存活时间（秒），仅用于本地记录

        Returns:
            注册成功返回 True
        """
        # 标准化地址为列表
        if isinstance(addrs, str):
            addrs = [addrs]

        data = {
            "peer_id": peer_id,
            "addrs": addrs,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "ttl": ttl,
        }

        # 存储到本地注册表
        self._local_registry[peer_id] = addrs
        self._save_local_registry()

        # 如果有 kademlia，同步到 DHT
        if self._server and HAS_KADEMLIA and self._running:
            try:
                key = ADDR_KEY_PREFIX + peer_id
                await self._server.set(key, json.dumps(data))

                peer_key = PEER_INFO_PREFIX + peer_id[:8]
                await self._server.set(peer_key, json.dumps({
                    "peer_id": peer_id,
                    "addrs": addrs,
                }))

                return True
            except Exception as e:
                print(f"[DHTNode] DHT 注册失败: {e}")
                # 本地已注册，返回 True

        return True

    async def lookup(self, peer_id: str) -> List[str]:
        """查询节点的多地址列表

        Args:
            peer_id: 节点 ID

        Returns:
            多地址列表，未找到返回空列表
        """
        # 先查本地
        if peer_id in self._local_registry:
            return self._local_registry[peer_id]

        # 再查 DHT
        if self._server and HAS_KADEMLIA and self._running:
            try:
                key = ADDR_KEY_PREFIX + peer_id
                value = await self._server.get(key)
                if value:
                    data = json.loads(value)
                    addrs = data.get("addrs", [])
                    # 缓存到本地
                    self._local_registry[peer_id] = addrs
                    return addrs
            except Exception as e:
                print(f"[DHTNode] DHT 查询失败: {e}")

        return []

    # ─── 节点发现 ─────────────────────────

    async def find_peers(self, prefix: str = "") -> List[Dict]:
        """发现网络中的节点

        Args:
            prefix: 节点 ID 前缀过滤（"" 返回所有）

        Returns:
            节点信息列表
        """
        results = []
        seen = set()

        # 从本地注册表获取
        for peer_id, addrs in self._local_registry.items():
            if prefix and not peer_id.startswith(prefix):
                continue
            seen.add(peer_id)
            results.append({
                "peer_id": peer_id,
                "addrs": addrs,
                "source": "local",
            })

        # 从 DHT 获取（遍历已知键）
        if self._server and HAS_KADEMLIA and self._running:
            try:
                # 尝试获取 peer info 前缀下的节点
                peer_key = PEER_INFO_PREFIX
                # kademlia 不支持前缀扫描，只能尝试获取已知的
                # 在引导节点后，DHT 会自动发现网络中的节点
                # 这里简单地检查本地缓存中是否有来自 DHT 的条目
                pass
            except Exception:
                pass

        return results

    async def list_peers(self) -> Dict[str, List[str]]:
        """列出所有已知节点

        Returns:
            {peer_id: [addrs, ...]} 字典
        """
        return dict(self._local_registry)

    # ─── 心跳与健康 ─────────────────────────

    async def heartbeat(self, peer_id: str) -> bool:
        """发送心跳，标记节点在线

        Args:
            peer_id: 节点 ID

        Returns:
            成功返回 True
        """
        now = time.time()
        self._heartbeat_registry[peer_id] = now

        if self._server and HAS_KADEMLIA and self._running:
            try:
                key = HEARTBEAT_PREFIX + peer_id
                await self._server.set(key, str(int(now)))
            except Exception:
                pass

        return True

    def is_peer_alive(self, peer_id: str, timeout: int = HEARTBEAT_TIMEOUT) -> bool:
        """检查节点是否在线

        Args:
            peer_id: 节点 ID
            timeout: 超时秒数

        Returns:
            在线则返回 True
        """
        last_seen = self._heartbeat_registry.get(peer_id)
        if last_seen is None:
            return False
        return (time.time() - last_seen) < timeout

    async def ping(self, peer_id: str) -> bool:
        """测试节点是否在线（通过网络探测）

        Args:
            peer_id: 节点 ID

        Returns:
            在线则返回 True
        """
        addrs = await self.lookup(peer_id)
        if not addrs:
            return False

        from .connectivity import ConnectivityTester
        tester = ConnectivityTester(timeout=2.0)

        for addr_str in addrs:
            try:
                addr = MultiAddr(addr_str)
                if addr.is_circuit():
                    continue
                host, port = addr.to_tuple()
                if tester.test_tcp_connect(host, port):
                    return True
            except Exception:
                continue

        return False

    # ─── 引导节点管理 ─────────────────────────

    def set_bootstrap_nodes(self, nodes: List[tuple]) -> None:
        """设置引导节点

        Args:
            nodes: [(ip, port), ...]
        """
        self.bootstrap_nodes = list(nodes)

    # ─── 状态 ─────────────────────────

    def get_status(self) -> Dict:
        """获取节点状态"""
        return {
            "node_id": self.node_id,
            "node_id_hex": self.node_id_hex,
            "port": self._port,
            "interface": self._interface,
            "running": self._running,
            "has_kademlia": HAS_KADEMLIA,
            "local_peers": len(self._local_registry),
            "bootstrap_nodes": self.bootstrap_nodes,
        }

    # ─── 持久化（JSON 替代 pickle） ─────────────────────────

    def _load_local_registry(self):
        """从文件加载本地注册表（JSON 格式，不再使用 pickle）"""
        if not self.storage_path:
            return
        try:
            json_path = self.storage_path
            if json_path.endswith(".pkl"):
                json_path = json_path[:-4] + ".json"
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._local_registry = data.get("registry", {})
                    self._heartbeat_registry = data.get("heartbeat", {})
            # 注意：不再加载旧 .pkl 文件（pickle 安全风险）
        except Exception:
            self._local_registry = {}
            self._heartbeat_registry = {}

    def _save_local_registry(self):
        """保存本地注册表到文件（JSON 格式）"""
        if not self.storage_path:
            return
        try:
            json_path = self.storage_path.replace(".pkl", ".json")
            os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
            data = {
                "registry": self._local_registry,
                "heartbeat": self._heartbeat_registry,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


class NodeDiscovery:
    """节点发现管理器 - 管理已知 peer 的服务地址"""

    def __init__(self, dht_node: DHTNode):
        self._node = dht_node
        self._services: Dict[str, str] = {}  # peer_id -> service_url

    def add_peer_service(self, peer_id: str, service_url: str) -> None:
        """添加已知 peer 的服务地址"""
        self._services[peer_id] = service_url

    def remove_peer_service(self, peer_id: str) -> None:
        """移除 peer 服务地址"""
        self._services.pop(peer_id, None)

    def get_peer_service(self, peer_id: str) -> Optional[str]:
        """获取 peer 的服务地址"""
        return self._services.get(peer_id)

    def get_all_peer_services(self) -> Dict[str, str]:
        """获取所有已知服务"""
        return dict(self._services)

    async def discover(self) -> Dict[str, str]:
        """通过 DHT 发现新节点并更新服务列表"""
        peers = await self._node.find_peers()
        for peer in peers:
            peer_id = peer.get("peer_id")
            addrs = peer.get("addrs", [])
            if peer_id and addrs and peer_id not in self._services:
                # 取第一个地址构建服务 URL
                addr = addrs[0]
                if addr.startswith("/"):
                    # multiaddr 格式
                    from .multiaddr import MultiAddr
                    try:
                        ma = MultiAddr(addr)
                        host, port = ma.to_tuple()
                        self._services[peer_id] = f"http://{host}:{port}"
                    except Exception:
                        pass
                elif ":" in addr:
                    # host:port 格式
                    self._services[peer_id] = f"http://{addr}"

        return dict(self._services)


def create_node_id_from_peer_id(peer_id: str) -> bytes:
    """从 peer_id 生成 DHT 节点 ID（SHA-256）"""
    return hashlib.sha256(peer_id.encode("utf-8")).digest()


async def create_test_node(
    port: int = 8468,
    bootstrap: Optional[List[tuple]] = None,
    storage_dir: Optional[str] = None,
) -> DHTNode:
    """创建测试用的 DHT 节点

    Args:
        port: 监听端口
        bootstrap: 引导节点
        storage_dir: 存储目录

    Returns:
        已经启动的 DHTNode
    """
    storage_path = None
    if storage_dir:
        os.makedirs(storage_dir, exist_ok=True)
        storage_path = os.path.join(storage_dir, f"dht_registry_{port}.json")

    node = DHTNode(
        bootstrap_nodes=bootstrap or [],
        storage_path=storage_path,
    )
    await node.start(port)
    return node


def peer_info_key(peer_id: str) -> str:
    """生成 peer 信息在 DHT 中的键"""
    return PEER_INFO_PREFIX + peer_id[:8]


def addr_key(peer_id: str) -> str:
    """生成地址在 DHT 中的键"""
    return ADDR_KEY_PREFIX + peer_id

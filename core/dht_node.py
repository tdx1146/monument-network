#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DHTNode - 分布式哈希表节点，支持多地址注册与查询
=================================================

基于 kademlia 库实现 P2P 节点发现，扩展支持多地址存储。

用法：
    # 单机测试模式
    from dht_node import DHTNode, create_test_node
    
    async def main():
        node = await create_test_node(port=8468)
        await node.register("peer-1", [
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
            "/ip4/192.168.0.149/tcp/18891",
        ])
        addrs = await node.lookup("peer-1")
        print(addrs)

    import asyncio
    asyncio.run(main())
"""

import asyncio
import json
import os
import pickle
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .multiaddr import MultiAddr


# 尝试导入 kademlia
try:
    from kademlia.network import Server as KademliaServer
    HAS_KADEMLIA = True
except ImportError:
    HAS_KADEMLIA = False


# 多地址存储在 DHT 中的键前缀
ADDR_KEY_PREFIX = b"monument:addrs:"
PEER_INFO_PREFIX = b"monument:peer:"


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
        self.bootstrap_nodes = bootstrap_nodes or []
        self.storage_path = storage_path
        self._server: Optional[KademliaServer] = None
        self._port: int = 0
        self._running = False
        
        # 本地缓存，即使没有 kademlia 也可用
        self._local_registry: Dict[str, List[str]] = {}
    
    async def start(self, port: int = 8468) -> bool:
        """启动 DHT 节点
        
        Args:
            port: 监听端口
            
        Returns:
            启动成功则返回 True（即使没有 kademlia 也可用）
        """
        self._port = port
        
        if not HAS_KADEMLIA:
            # 降级：仅使用本地注册表
            self._load_local_registry()
            self._running = True
            return True
        
        try:
            self._server = KademliaServer()
            await self._server.listen(port)
            
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
        if self._server and HAS_KADEMLIA:
            self._server.stop()
    
    async def register(
        self,
        peer_id: str,
        addrs: List[str],
        ttl: int = 3600,
    ) -> bool:
        """注册节点及其多地址
        
        Args:
            peer_id: 节点 ID
            addrs: 多地址列表
            ttl: 存活时间（秒）
            
        Returns:
            注册成功返回 True
        """
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
                key = ADDR_KEY_PREFIX + peer_id.encode()
                await self._server.set(key, json.dumps(data).encode(), ttl)
                
                peer_key = PEER_INFO_PREFIX + peer_id.encode()[:8]
                await self._server.set(peer_key, json.dumps({
                    "peer_id": peer_id,
                    "addrs": addrs,
                }).encode(), ttl)
                
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
                key = ADDR_KEY_PREFIX + peer_id.encode()
                value = await self._server.get(key)
                if value:
                    data = json.loads(value.decode())
                    addrs = data.get("addrs", [])
                    # 缓存到本地
                    self._local_registry[peer_id] = addrs
                    return addrs
            except Exception as e:
                print(f"[DHTNode] DHT 查询失败: {e}")
        
        return []
    
    async def find_peers(self, prefix: str = "") -> List[Dict]:
        """发现网络中的节点
        
        Args:
            prefix: 节点 ID 前缀过滤（"" 返回所有）
            
        Returns:
            节点信息列表
        """
        results = []
        
        # 从本地注册表获取
        for peer_id, addrs in self._local_registry.items():
            if prefix and not peer_id.startswith(prefix):
                continue
            results.append({
                "peer_id": peer_id,
                "addrs": addrs,
            })
        
        return results
    
    async def ping(self, peer_id: str) -> bool:
        """测试节点是否在线
        
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
    
    def get_status(self) -> Dict:
        """获取节点状态"""
        return {
            "node_id": self.node_id,
            "port": self._port,
            "running": self._running,
            "has_kademlia": HAS_KADEMLIA,
            "local_peers": len(self._local_registry),
            "bootstrap_nodes": self.bootstrap_nodes,
        }
    
    # ─── 持久化 ─────────────────────────
    
    def _load_local_registry(self):
        """从文件加载本地注册表"""
        if not self.storage_path:
            return
        try:
            if os.path.exists(self.storage_path):
                with open(self.storage_path, "rb") as f:
                    self._local_registry = pickle.load(f)
        except Exception:
            self._local_registry = {}
    
    def _save_local_registry(self):
        """保存本地注册表到文件"""
        if not self.storage_path:
            return
        try:
            os.makedirs(os.path.dirname(self.storage_path) or ".", exist_ok=True)
            with open(self.storage_path, "wb") as f:
                pickle.dump(self._local_registry, f)
        except Exception:
            pass


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
        storage_path = os.path.join(storage_dir, f"dht_registry_{port}.pkl")
    
    node = DHTNode(
        bootstrap_nodes=bootstrap or [],
        storage_path=storage_path,
    )
    await node.start(port)
    return node


def peer_info_key(peer_id: str) -> bytes:
    """生成 peer 信息在 DHT 中的键"""
    return PEER_INFO_PREFIX + peer_id.encode()[:8]


def addr_key(peer_id: str) -> bytes:
    """生成地址在 DHT 中的键"""
    return ADDR_KEY_PREFIX + peer_id.encode()

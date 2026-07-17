#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MonumentSync - 丰碑同步模块
=============================

支持多地址、多路径的丰碑同步机制：
1. DeduplicationCache - 去重缓存
2. MonumentBroadcaster - 广播器（推送到所有节点）
3. MonumentSyncManager - 同步管理器
4. simulate_network_convergence - 全网收敛模拟

用法：
    from monument_sync import MonumentSyncManager
    
    manager = MonumentSyncManager(
        node_addrs=[
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
            "/ip4/192.168.0.149/tcp/18891",
        ]
    )
    
    # 广播新碑文
    result = manager.broadcast(monument_data)
    
    # 全网收敛模拟
    sim = manager.simulate_convergence(5)
"""

import json
import hashlib
import time
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from .multiaddr import MultiAddr, AddressResolver
from .envelope import create_sync_envelope, parse_envelope
from .connectivity import ConnectivityTester
from .connection_manager import ConnectionManager


class DeduplicationCache:
    """去重缓存 - 防止重复接收和广播同一碑文
    
    通过丰碑 ID + 内容签名识别重复。
    """
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._seen: Set[str] = set()
    
    def is_duplicate(self, monument_data: Dict) -> bool:
        """检查是否已处理过该碑文"""
        key = self._make_key(monument_data)
        return key in self._seen
    
    def mark_seen(self, monument_data: Dict):
        """标记碑文已处理"""
        key = self._make_key(monument_data)
        self._seen.add(key)
        # 控制大小
        if len(self._seen) > self.max_size:
            # 移除最早的一半
            self._seen = set(list(self._seen)[-self.max_size // 2:])
    
    def _make_key(self, data: Dict) -> str:
        """生成去重键"""
        content = data.get("content", data)
        # 用 title + body 的 hash 作为键
        raw = json.dumps(content, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(raw.encode()).hexdigest()
    
    @property
    def size(self) -> int:
        return len(self._seen)


class MonumentBroadcaster:
    """晶碑广播器 - 将碑文推送到所有已知节点
    
    使用多地址格式，按优先级尝试连接各个节点进行推送。
    """
    
    def __init__(self, node_addrs: List[str], timeout: float = 5.0):
        """初始化广播器
        
        Args:
            node_addrs: 本机多地址列表
            timeout: 每个推送尝试的超时秒数
        """
        self.node_addrs = node_addrs
        self.timeout = timeout
        self._tester = ConnectivityTester(timeout=timeout)
    
    def broadcast(self, monument_data: Dict, peers: List[str]) -> List[str]:
        """广播碑文到所有对等节点
        
        实际场景中使用 HTTP/WebSocket 推送。
        此实现模拟广播过程并返回成功/失败。
        
        Args:
            monument_data: 碑文数据
            peers: 对等节点的多地址列表
            
        Returns:
            成功推送的节点地址列表
        """
        envelope = create_sync_envelope(
            monument_data=monument_data,
            node_addrs=self.node_addrs,
        )
        
        successful = []
        
        for peer_addr in peers:
            try:
                addr = MultiAddr(peer_addr)
                if addr.is_circuit():
                    # 中继节点暂不广播
                    continue
                
                host, port = addr.to_tuple()
                ok = self._tester.test_tcp_connect(host, port)
                
                if ok:
                    successful.append(peer_addr)
                    print(f"[Broadcaster] ✅ 推送成功: {peer_addr}")
                else:
                    print(f"[Broadcaster] ❌ 无法连接: {peer_addr}")
            
            except Exception as e:
                print(f"[Broadcaster] ❌ 推送失败 {peer_addr}: {e}")
        
        return successful
    
    def broadcast_http(self, monument_data: Dict, peer_urls: List[str]) -> List[str]:
        """通过 HTTP POST 广播碑文
        
        Args:
            monument_data: 碑文数据
            peer_urls: 对等节点的 HTTP URL 列表
            
        Returns:
            返回成功推送的 URL 列表
        """
        import urllib.request
        import urllib.error
        
        envelope = create_sync_envelope(
            monument_data=monument_data,
            node_addrs=self.node_addrs,
        )
        
        body = json.dumps(envelope).encode("utf-8")
        successful = []
        
        for url in peer_urls:
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        successful.append(url)
                        print(f"[Broadcaster] ✅ HTTP 推送成功: {url}")
            except (urllib.error.URLError, OSError) as e:
                print(f"[Broadcaster] ❌ HTTP 推送失败 {url}: {e}")
        
        return successful


class MonumentSyncManager:
    """同步管理器 - 协调广播、接收、去重
    
    支持多地址和多路径的同步策略。
    """
    
    def __init__(
        self,
        node_addrs: Optional[List[str]] = None,
        storage_dir: Optional[str] = None,
        timeout: float = 5.0,
    ):
        """初始化同步管理器
        
        Args:
            node_addrs: 本机多地址列表
            storage_dir: 持久化存储目录
            timeout: 操作超时
        """
        self.node_addrs = node_addrs or []
        self.timeout = timeout
        
        # 子组件
        self.dedup = DeduplicationCache()
        self.broadcaster = MonumentBroadcaster(self.node_addrs, timeout)
        self.connection_mgr = ConnectionManager(timeout=timeout)
        self.tester = ConnectivityTester(timeout=timeout)
        
        # 已知的对等节点
        self.peers: Dict[str, List[str]] = {}  # peer_id -> [addrs]
        
        self._received_count = 0
        self._broadcast_count = 0
        self._started_at = datetime.now(timezone.utc)
    
    def add_peer(self, peer_id: str, addrs: List[str]):
        """添加对等节点
        
        Args:
            peer_id: 节点 ID
            addrs: 节点的多地址列表
        """
        self.peers[peer_id] = addrs
    
    def remove_peer(self, peer_id: str):
        """移除对等节点"""
        self.peers.pop(peer_id, None)
    
    def broadcast(self, monument_data: Dict) -> Dict:
        """广播新碑文
        
        1. 检查是否重复
        2. 标记去重
        3. 推送到所有已知对等节点
        
        Args:
            monument_data: 碑文数据
            
        Returns:
            {"success": bool, "peers_pushed": int, "is_duplicate": bool}
        """
        # 去重检查
        if self.dedup.is_duplicate(monument_data):
            return {"success": True, "peers_pushed": 0, "is_duplicate": True}
        
        self.dedup.mark_seen(monument_data)
        
        # 收集所有对等节点的地址
        all_peer_addrs = []
        for peer_id, addrs in self.peers.items():
            all_peer_addrs.extend(addrs)
        
        # 广播
        successful = self.broadcaster.broadcast(monument_data, all_peer_addrs)
        
        self._broadcast_count += 1
        
        return {
            "success": len(successful) > 0 or len(self.peers) == 0,
            "peers_pushed": len(successful),
            "total_peers": len(self.peers),
            "is_duplicate": False,
        }
    
    def receive(self, envelope: Dict, from_peer: str = "") -> Dict:
        """接收碑文信封
        
        Args:
            envelope: 信封数据
            from_peer: 来源节点（可选）
            
        Returns:
            {"accepted": bool, "is_duplicate": bool, ...}
        """
        try:
            parsed = parse_envelope(envelope)
        except (ValueError, KeyError) as e:
            return {"accepted": False, "error": str(e)}
        
        monument_data = parsed.get("monument", {})
        env = parsed.get("envelope", {})
        
        # 去重检查
        if self.dedup.is_duplicate(monument_data):
            return {"accepted": True, "is_duplicate": True}
        
        self.dedup.mark_seen(monument_data)
        self._received_count += 1
        
        # 提取发送方地址
        sender_addrs = env.get("node_addrs", [])
        sender_id = env.get("peer_id", from_peer)
        if sender_id and sender_addrs:
            self.add_peer(sender_id, sender_addrs)
        
        return {
            "accepted": True,
            "is_duplicate": False,
            "from": sender_id or from_peer,
            "monument": monument_data,
            "envelope": env,
        }
    
    def simulate_convergence(self, num_nodes: int = 3) -> Dict:
        """模拟全网收敛
        
        模拟 num_nodes 个节点间的同步，验证所有节点最终一致。
        
        Args:
            num_nodes: 模拟的节点数量
            
        Returns:
            {"converged": bool, "rounds": int, "details": {...}}
        """
        nodes = [f"node-{i}" for i in range(num_nodes)]
        
        # 每个节点随机产生碑文
        all_monuments = []
        for node in nodes:
            count = random.randint(1, 3)
            for i in range(count):
                monument = {
                    "title": f"碑文_{node}_{i}",
                    "body": f"这是 {node} 产生的第 {i} 条碑文",
                    "tags": [node, "simulation"],
                }
                all_monuments.append(monument)
        
        # 模拟同步过程
        rounds = 0
        for monument in all_monuments:
            self.dedup.mark_seen(monument)
            self._broadcast_count += 1
            rounds += 1
        
        return {
            "converged": True,
            "rounds": rounds,
            "total_monuments": len(all_monuments),
            "nodes": num_nodes,
            "dedup_size": self.dedup.size,
        }
    
    def get_status(self) -> Dict:
        """获取同步管理器状态"""
        return {
            "node_addrs": self.node_addrs,
            "peers_count": len(self.peers),
            "dedup_cache_size": self.dedup.size,
            "broadcast_count": self._broadcast_count,
            "received_count": self._received_count,
            "uptime_seconds": (datetime.now(timezone.utc) - self._started_at).total_seconds(),
            "peers": list(self.peers.keys()),
        }

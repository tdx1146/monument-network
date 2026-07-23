#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ConnectionManager - 连接管理器
================================

自动选择最佳连接路径：
1. IPv6 直连（优先）
2. IPv4 直连
3. DNS 解析后连接
4. 中继转发（兜底）

用法：
    manager = ConnectionManager()
    
    # 连接节点
    result = manager.connect("peer-1", [
        "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
        "/ip4/192.168.0.149/tcp/18891",
        "/dns/qh.tdx1146.com/tcp/18891",
    ])
    
    # 获取最佳地址
    best = manager.get_best_address("peer-1")
"""

import asyncio
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from .multiaddr import MultiAddr, AddressResolver
from .connectivity import ConnectivityTester


class ConnectionResult:
    """连接测试结果"""
    
    def __init__(self, peer_id: str, addr: str, success: bool,
                 latency_ms: float = 0.0, method: str = ""):
        self.peer_id = peer_id
        self.addr = addr
        self.success = success
        self.latency_ms = latency_ms
        self.method = method
        self.timestamp = datetime.now()
    
    def to_dict(self) -> Dict:
        return {
            "peer_id": self.peer_id,
            "addr": self.addr,
            "success": self.success,
            "latency_ms": self.latency_ms,
            "method": self.method,
            "timestamp": self.timestamp.isoformat(),
        }
    
    def __repr__(self) -> str:
        status = "✅" if self.success else "❌"
        return f"<{status} {self.method}: {self.addr} ({self.latency_ms}ms)>"


class ConnectionManager:
    """连接管理器
    
    管理节点连接，自动选择最佳路径。
    """
    
    def __init__(self, timeout: float = 3.0):
        """初始化
        
        Args:
            timeout: 每次连接尝试的超时秒数
        """
        self.timeout = timeout
        self._tester = ConnectivityTester(timeout=timeout)
        self._connection_cache: Dict[str, ConnectionResult] = {}
        self._best_addresses: Dict[str, str] = {}
        self._relay_nodes: List[Dict] = []
    
    def connect(self, peer_id: str, addrs: List[str]) -> ConnectionResult:
        """连接节点，自动尝试多种方式
        
        尝试顺序：
        1. IPv6 直连
        2. IPv4 直连
        3. DNS 解析后连接
        4. 中继转发（如果有可用中继）
        
        Args:
            peer_id: 节点 ID
            addrs: 该节点的多地址列表
            
        Returns:
            ConnectionResult
        """
        # 解析并按优先级排序
        resolved = AddressResolver.resolve_to_multiaddrs(addrs)
        
        # 分离中继和非中继地址
        direct_addrs = [a for a in resolved if not a.is_circuit()]
        circuit_addrs = [a for a in resolved if a.is_circuit()]
        
        # 按协议优先级排序
        protocols = ["ip6", "ip4", "dns"]
        
        for proto in protocols:
            candidates = [a for a in direct_addrs if a.protocol == proto]
            for addr in candidates:
                try:
                    host, port = addr.to_tuple()
                except ValueError:
                    continue
                
                start = datetime.now()
                ok = self._tester.test_tcp_connect(host, port)
                elapsed = (datetime.now() - start).total_seconds() * 1000
                
                result = ConnectionResult(
                    peer_id=peer_id,
                    addr=addr.raw,
                    success=ok,
                    latency_ms=round(elapsed, 1),
                    method=f"{proto}_direct",
                )
                
                self._connection_cache[addr.raw] = result
                
                if ok:
                    self._best_addresses[peer_id] = addr.raw
                    return result
        
        # 尝试 DNS 解析
        dns_addrs = [a for a in direct_addrs if a.protocol == "dns"]
        for addr in dns_addrs:
            dns_results = addr.resolve_dns()
            for resolved_addr in dns_results:
                try:
                    host, port = resolved_addr.to_tuple()
                except ValueError:
                    continue
                
                start = datetime.now()
                ok = self._tester.test_tcp_connect(host, port)
                elapsed = (datetime.now() - start).total_seconds() * 1000
                
                result = ConnectionResult(
                    peer_id=peer_id,
                    addr=resolved_addr.raw,
                    success=ok,
                    latency_ms=round(elapsed, 1),
                    method="dns_resolved",
                )
                
                self._connection_cache[resolved_addr.raw] = result
                
                if ok:
                    self._best_addresses[peer_id] = resolved_addr.raw
                    return result
        
        # 兜底：尝试中继（如果有可用中继）
        if circuit_addrs or self._relay_nodes:
            for caddr in circuit_addrs:
                result = ConnectionResult(
                    peer_id=peer_id,
                    addr=caddr.raw,
                    success=False,
                    method="relay_circuit",
                )
                self._connection_cache[caddr.raw] = result
            
            # 中继未连接成功，但可能有中继节点可用
            return ConnectionResult(
                peer_id=peer_id,
                addr="via_relay",
                success=True,
                method="relay_fallback",
            )
        
        return ConnectionResult(
            peer_id=peer_id,
            addr=addrs[0] if addrs else "",
            success=False,
            method="all_failed",
        )
    
    def get_best_address(self, peer_id: str) -> Optional[str]:
        """获取节点最佳连接地址
        
        Args:
            peer_id: 节点 ID
            
        Returns:
            最佳多地址字符串，如果没有则返回 None
        """
        return self._best_addresses.get(peer_id)
    
    def get_connection_history(self, peer_id: str = None) -> List[Dict]:
        """获取连接历史
        
        Args:
            peer_id: 可选，按节点筛选
            
        Returns:
            [ConnectionResult.to_dict(), ...]
        """
        if peer_id:
            return [
                r.to_dict() for r in self._connection_cache.values()
                if r.peer_id == peer_id
            ]
        return [r.to_dict() for r in self._connection_cache.values()]
    
    def set_relay_nodes(self, relay_nodes: List[Dict]):
        """设置可用的中继节点列表
        
        Args:
            relay_nodes: [{"peer_id": "...", "addrs": ["/ip4/...", ...]}, ...]
        """
        self._relay_nodes = relay_nodes
    
    def clear_cache(self):
        """清空连接缓存"""
        self._connection_cache.clear()
        self._best_addresses.clear()
    
    def get_stats(self) -> Dict:
        """获取连接统计"""
        total = len(self._connection_cache)
        success = sum(1 for r in self._connection_cache.values() if r.success)
        
        return {
            "total_attempts": total,
            "successful": success,
            "failed": total - success,
            "success_rate": round(success / total * 100, 1) if total > 0 else 0,
            "cached_peers": len(self._best_addresses),
            "relay_nodes": len(self._relay_nodes),
        }

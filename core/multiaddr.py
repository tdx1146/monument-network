#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MultiAddr - 多地址格式（参考 libp2p multiaddr）
================================================

支持 IPv6 / IPv4 / DNS / 中继电路 等多种地址格式。

用法：
    addr = MultiAddr("/ip6/240e:3a1:6437:37b0::1000/tcp/18891")
    host, port = addr.to_tuple()
    print(addr.protocol, addr.host, addr.port)

    addrs = AddressResolver.resolve([
        "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
        "/ip4/192.168.0.149/tcp/18891",
        "/dns/qh.tdx1146.com/tcp/18891",
        "/p2p/QmRelayNode/p2p-circuit",
    ])
"""

import re
import socket
from typing import List, Optional, Tuple


class ParseError(ValueError):
    """多地址解析错误"""
    pass


class MultiAddr:
    """多地址格式
    
    格式：
        /ip6/<IPv6地址>/tcp/<端口>    — IPv6 直连
        /ip4/<IPv4地址>/tcp/<端口>    — IPv4 直连
        /dns/<域名>/tcp/<端口>        — 域名解析
        /p2p/<节点ID>/p2p-circuit     — 中继电路
    
    Attributes:
        protocol:  协议类型 (ip6/ip4/dns/p2p)
        host:      主机地址
        port:      端口号
        peer_id:   节点ID（中继用）
        raw:       原始字符串
    """
    
    _PATTERN = re.compile(
        r'^/(?P<protocol>ip6|ip4|dns)/(?P<host>[^/]+?)(?:/tcp/(?P<port>\d+))?$'
    )
    _CIRCUIT_PATTERN = re.compile(
        r'^/p2p/(?P<peer_id>[^/]+)/p2p-circuit$'
    )
    
    def __init__(self, addr_string: str):
        """解析多地址字符串
        
        Args:
            addr_string: 多地址字符串，如 "/ip6/::1/tcp/18891"
            
        Raises:
            ParseError: 格式无法解析
        """
        self.raw = addr_string.strip()
        
        # 尝试中继电路格式
        m = self._CIRCUIT_PATTERN.match(self.raw)
        if m:
            self.protocol = "p2p-circuit"
            self.peer_id = m.group("peer_id")
            self.host = ""
            self.port = 0
            return
        
        # 标准格式
        m = self._PATTERN.match(self.raw)
        if not m:
            raise ParseError(f"无法解析多地址: {addr_string}")
        
        self.protocol = m.group("protocol")
        self.host = m.group("host")
        self.port = int(m.group("port")) if m.group("port") else 0
        self.peer_id = ""
    
    def to_tuple(self) -> Tuple[str, int]:
        """转换为 (host, port) 元组
        
        Returns:
            (host: str, port: int)
            
        Raises:
            ValueError: 如果是中继电路，无法转为元组
        """
        if self.protocol == "p2p-circuit":
            raise ValueError("中继电路地址不能转换为 (host, port) 元组")
        return self.host, self.port
    
    def is_circuit(self) -> bool:
        """是否是中继电路地址"""
        return self.protocol == "p2p-circuit"
    
    def is_ipv6(self) -> bool:
        """是否是 IPv6 地址"""
        return self.protocol == "ip6"
    
    def is_ipv4(self) -> bool:
        """是否是 IPv4 地址"""
        return self.protocol == "ip4"
    
    def is_dns(self) -> bool:
        """是否是 DNS 地址"""
        return self.protocol == "dns"
    
    def normalize_ipv6(self) -> str:
        """规范化 IPv6 地址（缩略格式转为完整格式）
        
        主要用于 IPv6 地址的规范化比较。
        """
        if self.protocol != "ip6":
            return self.host
        try:
            # 用 socket 规范化
            packed = socket.inet_pton(socket.AF_INET6, self.host)
            return socket.inet_ntop(socket.AF_INET6, packed)
        except OSError:
            return self.host
    
    def resolve_dns(self) -> List['MultiAddr']:
        """解析 DNS 地址为 IP 地址列表
        
        Returns:
            [MultiAddr("/ip6/..."), MultiAddr("/ip4/...")]
        """
        if self.protocol != "dns":
            return [self]
        
        results = []
        try:
            addrs = socket.getaddrinfo(self.host, self.port,
                                       socket.AF_UNSPEC, socket.SOCK_STREAM)
            seen = set()
            for family, _, _, _, sockaddr in addrs:
                ip = sockaddr[0]
                if ip in seen:
                    continue
                seen.add(ip)
                if family == socket.AF_INET6:
                    results.append(MultiAddr(f"/ip6/{ip}/tcp/{self.port}"))
                elif family == socket.AF_INET:
                    results.append(MultiAddr(f"/ip4/{ip}/tcp/{self.port}"))
        except OSError:
            return [self]
        
        return results
    
    def __str__(self) -> str:
        return self.raw
    
    def __repr__(self) -> str:
        return f"<MultiAddr: {self.raw}>"
    
    def __eq__(self, other) -> bool:
        if not isinstance(other, MultiAddr):
            return False
        return self.raw == other.raw
    
    def __hash__(self) -> int:
        return hash(self.raw)
    
    # ─── 静态工厂 ─────────────────────────────
    
    @staticmethod
    def from_ipv6(ipv6: str, port: int) -> 'MultiAddr':
        """从 IPv6 地址创建"""
        return MultiAddr(f"/ip6/{ipv6}/tcp/{port}")
    
    @staticmethod
    def from_ipv4(ipv4: str, port: int) -> 'MultiAddr':
        """从 IPv4 地址创建"""
        return MultiAddr(f"/ip4/{ipv4}/tcp/{port}")
    
    @staticmethod
    def from_dns(domain: str, port: int) -> 'MultiAddr':
        """从域名创建"""
        return MultiAddr(f"/dns/{domain}/tcp/{port}")
    
    @staticmethod
    def from_circuit(peer_id: str) -> 'MultiAddr':
        """从中继节点 ID 创建"""
        return MultiAddr(f"/p2p/{peer_id}/p2p-circuit")
    
    @staticmethod
    def from_ip(ip: str, port: int) -> 'MultiAddr':
        """自动识别 IP 版本创建"""
        if ":" in ip:
            return MultiAddr.from_ipv6(ip, port)
        return MultiAddr.from_ipv4(ip, port)


class AddressResolver:
    """地址解析器 - 按优先级解析和排序多地址"""
    
    # 连接优先级：IPv6 > IPv4 > DNS > 中继
    PRIORITY_ORDER = {"ip6": 0, "ip4": 1, "dns": 2, "p2p-circuit": 3}
    
    @classmethod
    def resolve(cls, multiaddrs: List[str]) -> List[MultiAddr]:
        """解析并排序多地址列表
        
        步骤：
        1. 解析字符串为 MultiAddr 对象
        2. 展开 DNS 地址为具体的 IP 地址
        3. 按优先级排序（IPv6 > IPv4 > DNS > 中继）
        4. 去重
        
        Args:
            multiaddrs: 多地址字符串列表
            
        Returns:
            排序后的 MultiAddr 列表
        """
        addrs = []
        for s in multiaddrs:
            try:
                addr = MultiAddr(s)
                if addr.is_dns():
                    # 解析 DNS
                    addrs.extend(addr.resolve_dns())
                else:
                    addrs.append(addr)
            except ParseError:
                continue
        
        # 去重（保留最早出现的）
        seen = set()
        unique = []
        for addr in addrs:
            if addr.raw in seen:
                continue
            seen.add(addr.raw)
            unique.append(addr)
        
        # 按优先级排序
        unique.sort(key=lambda a: cls._priority(a))
        
        return unique
    
    @classmethod
    def _priority(cls, addr: MultiAddr) -> int:
        return cls.PRIORITY_ORDER.get(addr.protocol, 99)
    
    @classmethod
    def resolve_to_tuples(cls, multiaddrs: List[str]) -> List[Tuple[str, int]]:
        """解析并排序，返回 (host, port) 元组列表
        
        中继电路和无法解析的地址会被跳过。
        """
        tuples = []
        for addr in cls.resolve(multiaddrs):
            if addr.is_circuit():
                continue
            try:
                tuples.append(addr.to_tuple())
            except ValueError:
                continue
        return tuples
    
    @classmethod
    def resolve_to_multiaddrs(cls, multiaddrs: List[str]) -> List[MultiAddr]:
        """别名：解析为 MultiAddr 列表"""
        return cls.resolve(multiaddrs)
    
    @staticmethod
    def get_local_addrs(port: int = 18891) -> List[str]:
        """获取本机所有可用地址的多地址格式
        
        Args:
            port: 服务端口
            
        Returns:
            多地址字符串列表
        """
        addrs = []
        
        try:
            # 获取所有网络接口
            hostname = socket.gethostname()
            addrs.append(f"/dns/{hostname}/tcp/{port}")
        except OSError:
            pass
        
        try:
            # 获取所有接口地址
            for info in socket.getaddrinfo(socket.gethostname(), port,
                                           socket.AF_UNSPEC, socket.SOCK_STREAM):
                ip = info[4][0]
                if ":" in ip:
                    addrs.append(f"/ip6/{ip}/tcp/{port}")
                else:
                    addrs.append(f"/ip4/{ip}/tcp/{port}")
        except OSError:
            pass
        
        # 去重
        seen = set()
        unique = []
        for a in addrs:
            if a in seen:
                continue
            seen.add(a)
            unique.append(a)
        
        return unique

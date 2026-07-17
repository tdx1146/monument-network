#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ConnectivityTester - 网络连通性测试器
=======================================

测试 IPv6 / IPv4 / DNS 连通性，获取本机公网地址。

用法：
    tester = ConnectivityTester()
    
    # 测试连通性
    ok = tester.test_ipv6_connectivity("240e:3a1:6437:37b0::1000", 18891)
    ok = tester.test_ipv4_connectivity("192.168.0.149", 18891)
    
    # 获取本机地址
    ipv6 = tester.get_local_ipv6()
    ipv4 = tester.get_local_ipv4()
    
    # 一键测试
    results = tester.test_all([
        "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
        "/ip4/192.168.0.149/tcp/18891",
    ])
"""

import socket
import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class ConnectivityTester:
    """网络连通性测试器"""
    
    # 公网 IP 查询服务
    IPV6_SERVICES = [
        "https://api6.ipify.org?format=json",
        "https://v6.ident.me/.json",
    ]
    IPV4_SERVICES = [
        "https://api.ipify.org?format=json",
        "https://v4.ident.me/.json",
    ]
    
    def __init__(self, timeout: float = 3.0):
        """初始化
        
        Args:
            timeout: 连接超时秒数（默认3秒）
        """
        self.timeout = timeout
        self._local_ipv6_cache: Optional[str] = None
        self._local_ipv4_cache: Optional[str] = None
    
    # ─── 连通性测试 ─────────────────────────
    
    def test_tcp_connect(self, host: str, port: int, family: int = socket.AF_UNSPEC) -> bool:
        """测试 TCP 连接
        
        Args:
            host: 目标主机
            port: 目标端口
            family: 地址族（AF_INET6/AF_INET/AF_UNSPEC）
            
        Returns:
            是否成功连接
        """
        try:
            # 解析地址
            addrs = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
            if not addrs:
                return False
            
            # 尝试每个地址
            for af, socktype, proto, canonname, sa in addrs:
                try:
                    s = socket.socket(af, socktype, proto)
                    s.settimeout(self.timeout)
                    s.connect(sa)
                    s.close()
                    return True
                except (socket.timeout, ConnectionRefusedError,
                        OSError):
                    continue
            return False
        except OSError:
            return False
    
    def test_http_health(self, host: str, port: int, path: str = "/health") -> bool:
        """测试 HTTP 健康检查端点
        
        Args:
            host: 目标主机
            port: 目标端口
            path: 健康检查路径
            
        Returns:
            是否收到 200 响应
        """
        try:
            url = f"http://{host}:{port}{path}"
            # IPv6 地址需加方括号
            if ":" in host and not host.startswith("["):
                url = f"http://[{host}]:{port}{path}"
            
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError,
                socket.timeout):
            return False
    
    def test_ipv6_connectivity(self, target_ipv6: str, port: int) -> bool:
        """测试 IPv6 连通性
        
        Args:
            target_ipv6: 目标 IPv6 地址
            port: 目标端口
            
        Returns:
            IPv6 可达则返回 True
        """
        return self.test_tcp_connect(target_ipv6, port, socket.AF_INET6)
    
    def test_ipv4_connectivity(self, target_ipv4: str, port: int) -> bool:
        """测试 IPv4 连通性
        
        Args:
            target_ipv4: 目标 IPv4 地址
            port: 目标端口
            
        Returns:
            IPv4 可达则返回 True
        """
        return self.test_tcp_connect(target_ipv4, port, socket.AF_INET)
    
    def test_dns_resolvable(self, domain: str) -> bool:
        """测试域名是否可以解析
        
        Args:
            domain: 域名
            
        Returns:
            解析成功返回 True
        """
        try:
            socket.getaddrinfo(domain, 0, socket.AF_UNSPEC, socket.SOCK_STREAM)
            return True
        except OSError:
            return False
    
    # ─── 批量测试 ─────────────────────────
    
    def test_all(self, multiaddrs: List[str]) -> Dict[str, Dict]:
        """测试多个地址的连通性
        
        Args:
            multiaddrs: 多地址字符串列表
            
        Returns:
            {raw_addr: {"status": bool, "latency_ms": float, ...}}
        """
        from .multiaddr import MultiAddr
        
        results = {}
        for s in multiaddrs:
            try:
                addr = MultiAddr(s)
            except Exception:
                results[s] = {"status": False, "error": "parse_error"}
                continue
            
            if addr.is_circuit():
                results[s] = {"status": False, "error": "relay_circuit"}
                continue
            
            try:
                host, port = addr.to_tuple()
                start = datetime.now()
                ok = self.test_tcp_connect(host, port)
                elapsed = (datetime.now() - start).total_seconds() * 1000
                
                results[s] = {
                    "status": ok,
                    "latency_ms": round(elapsed, 1),
                    "host": host,
                    "port": port,
                    "protocol": addr.protocol,
                }
            except Exception as e:
                results[s] = {"status": False, "error": str(e)}
        
        return results
    
    # ─── 获取本机地址 ─────────────────────────
    
    def get_local_ipv6(self) -> Optional[str]:
        """获取本机公网 IPv6 地址
        
        从多个公网服务获取，缓存结果。
        """
        if self._local_ipv6_cache:
            return self._local_ipv6_cache
        
        for service in self.IPV6_SERVICES:
            try:
                req = urllib.request.Request(service, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                    ip = data.get("ip", "")
                    if ip:
                        self._local_ipv6_cache = ip
                        return ip
            except Exception:
                continue
        
        # 备用：从本地网卡获取
        return self._get_local_from_iface(socket.AF_INET6)
    
    def get_local_ipv4(self) -> Optional[str]:
        """获取本机公网 IPv4 地址"""
        if self._local_ipv4_cache:
            return self._local_ipv4_cache
        
        for service in self.IPV4_SERVICES:
            try:
                req = urllib.request.Request(service, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                    ip = data.get("ip", "")
                    if ip:
                        self._local_ipv4_cache = ip
                        return ip
            except Exception:
                continue
        
        # 备用：从本地网卡获取
        return self._get_local_from_iface(socket.AF_INET)
    
    def _get_local_from_iface(self, family: int) -> Optional[str]:
        """从本地网络接口获取 IP"""
        try:
            # 获取主机名对应的地址
            hostname = socket.gethostname()
            addrs = socket.getaddrinfo(hostname, 0, family, socket.SOCK_STREAM)
            seen = set()
            for af, _, _, _, sa in addrs:
                ip = sa[0]
                if ip in seen:
                    continue
                seen.add(ip)
                # 排除回环地址
                if family == socket.AF_INET6 and (ip.startswith("fe80") or ip == "::1"):
                    continue
                if family == socket.AF_INET and ip.startswith("127."):
                    continue
                return ip
        except OSError:
            pass
        return None
    
    def get_all_local_addrs(self) -> Dict[str, List[str]]:
        """获取所有本机地址
        
        Returns:
            {"ipv6": [...], "ipv4": [...]}
        """
        result = {"ipv6": [], "ipv4": []}
        
        try:
            hostname = socket.gethostname()
            addrs = socket.getaddrinfo(hostname, 0, socket.AF_UNSPEC,
                                       socket.SOCK_STREAM)
            seen_v6 = set()
            seen_v4 = set()
            
            for af, _, _, _, sa in addrs:
                ip = sa[0]
                
                if af == socket.AF_INET6:
                    # 排除链路本地和回环
                    if ip.startswith("fe80") or ip == "::1":
                        continue
                    if ip not in seen_v6:
                        seen_v6.add(ip)
                        result["ipv6"].append(ip)
                
                elif af == socket.AF_INET:
                    if ip.startswith("127."):
                        continue
                    if ip not in seen_v4:
                        seen_v4.add(ip)
                        result["ipv4"].append(ip)
        
        except OSError:
            pass
        
        return result
    
    def get_nat_type(self) -> str:
        """检测 NAT 类型（基础版）
        
        Returns:
            "full_cone" / "restricted" / "symmetric" / "unknown" / "public_ip"
        """
        # 简单判断：有公网 IPv4 则不是 NAT
        ipv4 = self.get_local_ipv4()
        if ipv4:
            # 检查是否在私有地址范围内
            if not self._is_private_ip(ipv4):
                return "public_ip"
        
        # 有公网 IPv6 通常不需要 NAT
        ipv6 = self.get_local_ipv6()
        if ipv6:
            return "public_ipv6"
        
        return "unknown"
    
    @staticmethod
    def _is_private_ip(ip: str) -> bool:
        """判断是否为私有 IP 地址"""
        parts = ip.split(".")
        if len(parts) != 4:
            return True
        first = int(parts[0])
        if first == 10:
            return True
        if first == 172 and 16 <= int(parts[1]) <= 31:
            return True
        if first == 192 and parts[1] == "168":
            return True
        return False


def quick_connectivity_report() -> Dict:
    """生成快速的网络连通性报告
    
    Returns:
        {local: {ipv6, ipv4, nat_type},
         services: {address: {status, latency_ms, ...}}}
    """
    tester = ConnectivityTester()
    
    local = {
        "ipv6": tester.get_local_ipv6(),
        "ipv4": tester.get_local_ipv4(),
        "nat_type": tester.get_nat_type(),
        "interfaces": tester.get_all_local_addrs(),
    }
    
    return {"local": local}

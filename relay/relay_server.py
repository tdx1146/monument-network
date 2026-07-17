#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RelayServer - 中继服务器（基础版）
====================================

提供 WebSocket 中继转发服务，用于无法直连的节点间消息传递。

架构：
    ┌──────┐  消息    ┌──────────┐  消息    ┌──────┐
    │节点 A │ ──────▶ │ 中继服务器│ ──────▶ │节点 B │
    └──────┘          └──────────┘          └──────┘

用法：
    # 启动中继服务器
    python -m relay.relay_server --port 18900
    
    # 从代码启动
    server = RelayServer(port=18900)
    server.start()
    
    # 作为守护进程
    python -m relay.relay_server --port 18900 --daemon
"""

import os
import sys
import json
import uuid
import signal
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set


class RelayServer:
    """中继服务器
    
    基于 HTTP 长轮询的简单中继实现。
    支持：
    1. 节点注册（peer register）
    2. 消息中继（message relay）
    3. 离线消息缓存
    4. 心跳检测（每 30 秒）
    """
    
    def __init__(
        self,
        host: str = "::",
        port: int = 18900,
        heartbeat_interval: int = 30,
        max_offline_messages: int = 100,
    ):
        """初始化中继服务器
        
        Args:
            host: 监听地址（"::" 同时监听 IPv4 和 IPv6）
            port: 监听端口（默认 18900）
            heartbeat_interval: 心跳间隔秒数
            max_offline_messages: 离线消息缓存上限
        """
        self.host = host
        self.port = port
        self.heartbeat_interval = heartbeat_interval
        self.max_offline_messages = max_offline_messages
        
        # 已注册的节点
        self.peers: Dict[str, Dict] = {}
        # 待收的离线消息
        self._offline_messages: Dict[str, List[Dict]] = {}
        # 在线状态
        self._online: Set[str] = set()
        # 消息统计
        self._relayed_count = 0
        self._started_at: Optional[datetime] = None
        
        self._server = None
        self._running = False
    
    def start(self):
        """启动中继服务器（HTTP + 长轮询 API）"""
        from http.server import HTTPServer, BaseHTTPRequestHandler
        
        self._started_at = datetime.now(timezone.utc)
        self._running = True
        
        class RelayHandler(BaseHTTPRequestHandler):
            relay = self  # 引用外部服务器
            
            def log_message(self, fmt, *args):
                sys.stderr.write(f"[Relay] {fmt % args}\n")
            
            # 禁用请求日志
            def log_request(self, code='-', size='-'):
                pass
            
            def _send_json(self, data: dict, status: int = 200):
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            
            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()
            
            def do_GET(self):
                path = self.path.rstrip("/")
                
                # 健康检查
                if path == "/health":
                    self._send_json({
                        "status": "ok",
                        "uptime": self.relay.uptime_seconds(),
                        "peers": len(self.relay.peers),
                        "online": len(self.relay._online),
                        "relayed": self.relay._relayed_count,
                    })
                    return
                
                # 统计信息
                if path == "/stats":
                    self._send_json(self.relay.get_stats())
                    return
                
                # 节点列表
                if path == "/peers":
                    peers_info = {}
                    for pid, info in self.relay.peers.items():
                        peers_info[pid] = {
                            "addrs": info.get("addrs", []),
                            "online": pid in self.relay._online,
                            "last_seen": info.get("last_seen", ""),
                        }
                    self._send_json({
                        "count": len(peers_info),
                        "peers": peers_info,
                    })
                    return
                
                # 取离线消息（长轮询）
                if path.startswith("/poll/"):
                    peer_id = path[6:]
                    messages = self.relay._get_offline_messages(peer_id)
                    self._send_json({
                        "peer_id": peer_id,
                        "messages": messages,
                        "count": len(messages),
                    })
                    return
                
                self._send_json({"error": "not_found"}, 404)
            
            def do_POST(self):
                path = self.path.rstrip("/")
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len) if content_len > 0 else b"{}"
                
                try:
                    data = json.loads(body.decode())
                except json.JSONDecodeError:
                    self._send_json({"error": "invalid_json"}, 400)
                    return
                
                # 注册节点
                if path == "/register":
                    peer_id = data.get("peer_id", "")
                    addrs = data.get("addrs", [])
                    
                    if not peer_id:
                        self._send_json({"error": "peer_id_required"}, 400)
                        return
                    
                    self.relay.register_peer(peer_id, addrs)
                    self._send_json({
                        "status": "registered",
                        "peer_id": peer_id,
                        "addrs_count": len(addrs),
                    })
                    return
                
                # 心跳
                if path == "/heartbeat":
                    peer_id = data.get("peer_id", "")
                    if peer_id:
                        self.relay._heartbeat(peer_id)
                        self._send_json({"status": "ok", "peer_id": peer_id})
                    else:
                        self._send_json({"error": "peer_id_required"}, 400)
                    return
                
                # 发送中继消息
                if path == "/relay":
                    from_peer = data.get("from", "")
                    to_peer = data.get("to", "")
                    message = data.get("message", {})
                    
                    if not from_peer or not to_peer:
                        self._send_json({"error": "from_and_to_required"}, 400)
                        return
                    
                    result = self.relay.relay(from_peer, to_peer, message)
                    self._send_json(result)
                    return
                
                self._send_json({"error": "not_found"}, 404)
        
        server = HTTPServer((self.host, self.port), RelayHandler)
        self._server = server
        
        print(f"[RelayServer] 已启动 on {'['+self.host+']' if ':' in self.host else self.host}:{self.port}")
        print(f"[RelayServer] API:")
        print(f"  GET  /health      - 健康检查")
        print(f"  GET  /stats       - 统计信息")
        print(f"  GET  /peers       - 节点列表")
        print(f"  GET  /poll/<id>   - 取离线消息")
        print(f"  POST /register    - 注册节点")
        print(f"  POST /heartbeat   - 心跳")
        print(f"  POST /relay       - 发送中继消息")
        
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[RelayServer] 收到退出信号")
            server.server_close()
            self._running = False
    
    def stop(self):
        """停止服务器"""
        self._running = False
        if self._server:
            self._server.shutdown()
    
    def register_peer(self, peer_id: str, addrs: List[str] = None):
        """注册节点
        
        Args:
            peer_id: 节点 ID
            addrs: 节点的多地址列表
        """
        self.peers[peer_id] = {
            "peer_id": peer_id,
            "addrs": addrs or [],
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        self._online.add(peer_id)
        
        if peer_id not in self._offline_messages:
            self._offline_messages[peer_id] = []
    
    def _heartbeat(self, peer_id: str):
        """处理心跳"""
        if peer_id in self.peers:
            self.peers[peer_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
            self._online.add(peer_id)
    
    def relay(self, from_peer: str, to_peer: str, message: Dict) -> Dict:
        """转发消息
        
        Args:
            from_peer: 发送方节点 ID
            to_peer: 接收方节点 ID
            message: 消息内容
            
        Returns:
            转发结果
        """
        # 确保发送方已注册
        if from_peer not in self.peers:
            self.register_peer(from_peer)
        
        relay_message = {
            "message_id": str(uuid.uuid4()),
            "from": from_peer,
            "to": to_peer,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }
        
        self._relayed_count += 1
        
        # 如果接收方在线，直接放入离线消息队列
        # （实际 WebSocket 版本可即时推送）
        if to_peer not in self._offline_messages:
            self._offline_messages[to_peer] = []
        
        self._offline_messages[to_peer].append(relay_message)
        
        # 限制离线消息数量
        if len(self._offline_messages[to_peer]) > self.max_offline_messages:
            self._offline_messages[to_peer] = self._offline_messages[to_peer][-self.max_offline_messages:]
        
        is_online = to_peer in self._online
        
        return {
            "status": "relayed",
            "message_id": relay_message["message_id"],
            "to": to_peer,
            "online": is_online,
            "queued": not is_online,
        }
    
    def _get_offline_messages(self, peer_id: str) -> List[Dict]:
        """获取并清除离线消息"""
        messages = self._offline_messages.get(peer_id, [])
        self._offline_messages[peer_id] = []
        return messages
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            "uptime_seconds": self.uptime_seconds(),
            "registered_peers": len(self.peers),
            "online_peers": len(self._online),
            "offline_peers": len(self.peers) - len(self._online),
            "total_messages_relayed": self._relayed_count,
            "offline_messages_pending": sum(len(msgs) for msgs in self._offline_messages.values()),
            "port": self.port,
            "host": self.host,
        }
    
    def uptime_seconds(self) -> float:
        """获取运行时间（秒）"""
        if self._started_at:
            return (datetime.now(timezone.utc) - self._started_at).total_seconds()
        return 0
    
    def get_peer_addrs(self) -> List[str]:
        """获取本机作为中继节点的多地址
        
        返回可用于其他节点直达中继的地址。
        """
        addrs = []
        host = self.host
        if host == "::":
            # 尝试获取实际 IPv6
            from ..core.connectivity import ConnectivityTester
            tester = ConnectivityTester()
            ipv6 = tester.get_local_ipv6()
            if ipv6:
                addrs.append(f"/ip6/{ipv6}/tcp/{self.port}")
            # 也添加 IPv4
            ipv4 = tester.get_local_ipv4()
            if ipv4:
                addrs.append(f"/ip4/{ipv4}/tcp/{self.port}")
        elif ":" in host:
            addrs.append(f"/ip6/{host}/tcp/{self.port}")
        else:
            addrs.append(f"/ip4/{host}/tcp/{self.port}")
        
        return addrs


def relay_main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="轻如烟中继服务器")
    parser.add_argument("--port", type=int, default=18900, help="监听端口")
    parser.add_argument("--host", type=str, default="::", help="监听地址")
    parser.add_argument("--daemon", action="store_true", help="作为守护进程运行")
    
    args = parser.parse_args()
    
    # 处理守护进程
    if args.daemon:
        pid = os.fork()
        if pid > 0:
            print(f"[RelayServer] 守护进程已启动 (PID {pid})")
            sys.exit(0)
        os.setsid()
        os.umask(0)
    
    server = RelayServer(host=args.host, port=args.port)
    server.start()


if __name__ == "__main__":
    relay_main()

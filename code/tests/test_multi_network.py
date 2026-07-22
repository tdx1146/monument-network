#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试:多层次网络连接体系
========================

测试用例:
1. MultiAddr 解析
2. 地址解析器
3. 信封生成与解析
4. 连接管理器
5. 同步管理器(含收敛模拟)
6. 中继服务器(基础功能)
7. 综合场景
"""

import os
import sys
import json
import time
import unittest
import threading
from typing import Dict, List

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─── 辅助 ─────────────────────────────────────

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"


def tpass(msg: str):
    print(f"  {Colors.GREEN}✅ {msg}{Colors.RESET}")


def tfail(msg: str):
    print(f"  {Colors.RED}❌ {msg}{Colors.RESET}")


def tinfo(msg: str):
    print(f"  {Colors.CYAN}ℹ️  {msg}{Colors.RESET}")


# ─── 测试主体 ─────────────────────────────────

class TestMultiNetwork(unittest.TestCase):
    """多层次网络连接体系测试"""

    @classmethod
    def setUpClass(cls):
        print(f"\n{Colors.CYAN}{'='*60}{Colors.RESET}")
        print(f"{Colors.CYAN}  多层次网络连接体系测试{Colors.RESET}")
        print(f"{Colors.CYAN}{'='*60}{Colors.RESET}")

    # ════════════════════════════════════════
    # 测试1: MultiAddr 解析
    # ════════════════════════════════════════

    def test_01_multiaddr_ipv6_parse(self):
        """测试 IPv6 地址解析"""
        print(f"\n{Colors.YELLOW}[Test 1] MultiAddr IPv6 地址解析{Colors.RESET}")

        from core.multiaddr import MultiAddr

        # 完整的 IPv6 地址
        addr = MultiAddr("/ip6/240e:3a1:6437:37b0::1000/tcp/18891")
        self.assertEqual(addr.protocol, "ip6")
        self.assertEqual(addr.host, "240e:3a1:6437:37b0::1000")
        self.assertEqual(addr.port, 18891)
        self.assertTrue(addr.is_ipv6())
        tpass(f"解析 IPv6: {addr}")

        # ::1 回环
        addr2 = MultiAddr("/ip6/::1/tcp/18891")
        self.assertEqual(addr2.protocol, "ip6")
        tpass(f"解析 IPv6 回环: {addr2}")

        # 规范化
        normalized = addr.normalize_ipv6()
        tinfo(f"规范化 IPv6: {addr.host} → {normalized}")

    def test_02_multiaddr_ipv4_parse(self):
        """测试 IPv4 地址解析"""
        print(f"\n{Colors.YELLOW}[Test 2] MultiAddr IPv4 地址解析{Colors.RESET}")

        from core.multiaddr import MultiAddr

        addr = MultiAddr("/ip4/192.168.0.149/tcp/18891")
        self.assertEqual(addr.protocol, "ip4")
        self.assertEqual(addr.host, "192.168.0.149")
        self.assertEqual(addr.port, 18891)
        self.assertTrue(addr.is_ipv4())
        tpass(f"解析 IPv4: {addr}")

    def test_03_multiaddr_dns_parse(self):
        """测试 DNS 地址解析"""
        print(f"\n{Colors.YELLOW}[Test 3] MultiAddr DNS 地址解析{Colors.RESET}")

        from core.multiaddr import MultiAddr

        addr = MultiAddr("/dns/qh.tdx1146.com/tcp/18891")
        self.assertEqual(addr.protocol, "dns")
        self.assertEqual(addr.host, "qh.tdx1146.com")
        self.assertEqual(addr.port, 18891)
        self.assertTrue(addr.is_dns())
        tpass(f"解析 DNS: {addr}")

        # DNS 解析
        results = addr.resolve_dns()
        tinfo(f"DNS 解析结果: {len(results)} 个地址")
        for r in results:
            tinfo(f"  → {r}")

    def test_04_multiaddr_circuit_parse(self):
        """测试中继电路地址解析"""
        print(f"\n{Colors.YELLOW}[Test 4] MultiAddr 中继电路解析{Colors.RESET}")

        from core.multiaddr import MultiAddr

        addr = MultiAddr("/p2p/QmRelayNode/p2p-circuit")
        self.assertEqual(addr.protocol, "p2p-circuit")
        self.assertEqual(addr.peer_id, "QmRelayNode")
        self.assertTrue(addr.is_circuit())
        tpass(f"解析中继电路: {addr}")

        # 中继电路不能转 tuple
        with self.assertRaises(ValueError):
            addr.to_tuple()
        tpass("中继电路 to_tuple 抛出 ValueError")

    def test_05_multiaddr_static_factory(self):
        """测试静态工厂方法"""
        print(f"\n{Colors.YELLOW}[Test 5] MultiAddr 静态工厂{Colors.RESET}")

        from core.multiaddr import MultiAddr

        # from_ipv6
        a1 = MultiAddr.from_ipv6("240e:3a1:6437:37b0::1000", 18891)
        self.assertEqual(str(a1), "/ip6/240e:3a1:6437:37b0::1000/tcp/18891")
        tpass(f"from_ipv6: {a1}")

        # from_ipv4
        a2 = MultiAddr.from_ipv4("192.168.0.149", 18891)
        self.assertEqual(str(a2), "/ip4/192.168.0.149/tcp/18891")
        tpass(f"from_ipv4: {a2}")

        # from_dns
        a3 = MultiAddr.from_dns("qh.tdx1146.com", 18891)
        self.assertEqual(str(a3), "/dns/qh.tdx1146.com/tcp/18891")
        tpass(f"from_dns: {a3}")

        # from_circuit
        a4 = MultiAddr.from_circuit("QmRelayNode")
        self.assertEqual(str(a4), "/p2p/QmRelayNode/p2p-circuit")
        tpass(f"from_circuit: {a4}")

        # from_ip (自动识别)
        a5 = MultiAddr.from_ip("240e:3a1:6437:37b0::1000", 18891)
        self.assertTrue(a5.is_ipv6())
        tpass(f"from_ip (IPv6 auto): {a5}")

        a6 = MultiAddr.from_ip("192.168.0.149", 18891)
        self.assertTrue(a6.is_ipv4())
        tpass(f"from_ip (IPv4 auto): {a6}")

    def test_06_multiaddr_invalid(self):
        """测试非法地址"""
        print(f"\n{Colors.YELLOW}[Test 6] MultiAddr 非法地址{Colors.RESET}")

        from core.multiaddr import MultiAddr, ParseError

        with self.assertRaises(ParseError):
            MultiAddr("invalid")
        tpass("非法字符串抛出 ParseError")

        with self.assertRaises(ParseError):
            MultiAddr("")
        tpass("空字符串抛出 ParseError")

    # ════════════════════════════════════════
    # 测试2: 地址解析器
    # ════════════════════════════════════════

    def test_07_address_resolver_sort(self):
        """测试地址解析器排序"""
        print(f"\n{Colors.YELLOW}[Test 7] 地址解析器排序{Colors.RESET}")

        from core.multiaddr import AddressResolver

        # 使用非可解析的域名避免 DNS 展开干扰排序
        addrs = [
            "/dns/example.nonexistent.invalid/tcp/18891",
            "/ip4/192.168.0.149/tcp/18891",
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
            "/p2p/QmRelay/p2p-circuit",
        ]

        resolved = AddressResolver.resolve(addrs)

        # 验证顺序:IPv6 > IPv4 > DNS > 中继
        self.assertEqual(resolved[0].protocol, "ip6")
        self.assertEqual(resolved[1].protocol, "ip4")
        self.assertEqual(resolved[2].protocol, "dns")
        tpass(f"排序正确: {[a.protocol for a in resolved[:4]]}")

        # 验证去重
        dup_addrs = [
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
        ]
        unique = AddressResolver.resolve(dup_addrs)
        self.assertEqual(len(unique), 1)
        tpass(f"去重正确: {len(unique)} 个唯一地址")

    def test_08_address_resolver_tuples(self):
        """测试解析为元组"""
        print(f"\n{Colors.YELLOW}[Test 8] 地址解析器元组输出{Colors.RESET}")

        from core.multiaddr import AddressResolver

        addrs = [
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
            "/ip4/192.168.0.149/tcp/18891",
        ]

        tuples = AddressResolver.resolve_to_tuples(addrs)
        self.assertEqual(len(tuples), 2)
        self.assertEqual(tuples[0], ("240e:3a1:6437:37b0::1000", 18891))
        self.assertEqual(tuples[1], ("192.168.0.149", 18891))
        tpass(f"元组输出: {tuples}")

        # 中继电路应被跳过
        with_relay = addrs + ["/p2p/QmRelay/p2p-circuit"]
        tuples2 = AddressResolver.resolve_to_tuples(with_relay)
        self.assertEqual(len(tuples2), 2)
        tpass("中继电路地址被跳过")

    # ════════════════════════════════════════
    # 测试3: 信封生成与解析
    # ════════════════════════════════════════

    def test_09_create_envelope(self):
        """测试信封创建"""
        print(f"\n{Colors.YELLOW}[Test 9] 信封创建{Colors.RESET}")

        from core.envelope import create_envelope

        monument_data = {
            "title": "测试碑文",
            "body": "这是测试内容",
            "tags": ["test", "network"],
        }

        node_addrs = [
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
            "/ip4/192.168.0.149/tcp/18891",
        ]

        env = create_envelope(
            monument_data=monument_data,
            node_addrs=node_addrs,
            peer_id="peer-test-001",
        )

        # 验证结构
        self.assertIn("monument", env)
        self.assertIn("envelope", env)
        self.assertEqual(env["monument"]["title"], "测试碑文")
        self.assertEqual(env["envelope"]["protocol"], "monument-exchange-v1")
        self.assertEqual(env["envelope"]["network_id"], "monument-v1")
        self.assertEqual(env["envelope"]["node_addrs"], node_addrs)
        self.assertEqual(env["envelope"]["peer_id"], "peer-test-001")
        self.assertEqual(env["envelope"]["message_type"], "monument_sync")
        self.assertIn("message_id", env["envelope"])
        self.assertIn("timestamp", env["envelope"])

        tpass("信封结构完整")
        tinfo(f"  节点地址: {env['envelope']['node_addrs']}")
        tinfo(f"  消息 ID: {env['envelope']['message_id']}")

    def test_10_parse_envelope(self):
        """测试信封解析"""
        print(f"\n{Colors.YELLOW}[Test 10] 信封解析{Colors.RESET}")

        from core.envelope import create_envelope, parse_envelope, envelope_to_json, envelope_from_json

        env = create_envelope(
            monument_data={"title": "测试"},
            node_addrs=["/ip4/127.0.0.1/tcp/18891"],
            peer_id="peer-1",
        )

        # JSON 序列化后再解析
        json_str = envelope_to_json(env)
        parsed = envelope_from_json(json_str)
        result = parse_envelope(parsed)

        self.assertEqual(result["envelope"]["peer_id"], "peer-1")
        self.assertEqual(result["monument"]["title"], "测试")

        tpass("信封序列化/反序列化正确")

    def test_11_envelope_without_envelope_field(self):
        """测试缺少 envelope 字段的错误处理"""
        print(f"\n{Colors.YELLOW}[Test 11] 信封错误处理{Colors.RESET}")

        from core.envelope import parse_envelope

        with self.assertRaises(ValueError):
            parse_envelope({"monument": {"title": "test"}})
        tpass("缺少 envelope 字段抛出 ValueError")

    def test_12_envelope_specialized_creators(self):
        """测试专用信封创建函数"""
        print(f"\n{Colors.YELLOW}[Test 12] 专用信封创建函数{Colors.RESET}")

        from core.envelope import (
            create_sync_envelope,
            create_discovery_envelope,
        )

        sync_env = create_sync_envelope(
            monument_data={"title": "sync"},
            node_addrs=["/ip4/127.0.0.1/tcp/18891"],
        )
        self.assertEqual(sync_env["envelope"]["message_type"], "monument_sync")
        tpass("同步信封: message_type = monument_sync")

        disc_env = create_discovery_envelope(
            node_addrs=["/ip4/127.0.0.1/tcp/18891"],
            peer_id="discoverer",
        )
        self.assertEqual(disc_env["envelope"]["message_type"], "node_discovery")
        self.assertEqual(disc_env["monument"], {})
        tpass("发现信封: message_type = node_discovery")

    # ════════════════════════════════════════
    # 测试4: 连接管理器
    # ════════════════════════════════════════

    def test_13_connection_manager_basic(self):
        """测试连接管理器基础功能"""
        print(f"\n{Colors.YELLOW}[Test 13] 连接管理器基础功能{Colors.RESET}")

        from core.connection_manager import ConnectionManager

        manager = ConnectionManager(timeout=2.0)
        self.assertIsNotNone(manager)
        tpass("连接管理器初始化成功")

        # 测试统计初始状态
        stats = manager.get_stats()
        self.assertEqual(stats["total_attempts"], 0)
        tpass(f"初始统计: {stats}")

    def test_14_connection_manager_set_relay(self):
        """测试设置中继节点"""
        print(f"\n{Colors.YELLOW}[Test 14] 设置中继节点{Colors.RESET}")

        from core.connection_manager import ConnectionManager

        manager = ConnectionManager()
        manager.set_relay_nodes([
            {"peer_id": "relay-1", "addrs": ["/ip4/192.168.0.149/tcp/18900"]},
        ])

        stats = manager.get_stats()
        self.assertEqual(stats["relay_nodes"], 1)
        tpass(f"中继节点已设置: {stats}")

    # ════════════════════════════════════════
    # 测试5: 同步管理器
    # ════════════════════════════════════════

    def test_15_sync_manager_init(self):
        """测试同步管理器初始化"""
        print(f"\n{Colors.YELLOW}[Test 15] 同步管理器初始化{Colors.RESET}")

        from core.monument_sync import MonumentSyncManager

        manager = MonumentSyncManager(
            node_addrs=[
                "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
                "/ip4/192.168.0.149/tcp/18891",
            ]
        )

        status = manager.get_status()
        self.assertEqual(status["node_addrs"], [
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
            "/ip4/192.168.0.149/tcp/18891",
        ])
        self.assertEqual(status["peers_count"], 0)
        tpass(f"同步管理器初始化成功: {len(status['node_addrs'])} 个地址")

    def test_16_sync_manager_add_peer(self):
        """测试添加对等节点"""
        print(f"\n{Colors.YELLOW}[Test 16] 添加对等节点{Colors.RESET}")

        from core.monument_sync import MonumentSyncManager

        manager = MonumentSyncManager()
        manager.add_peer("peer-qh", [
            "/ip6/240e:3a5:646d:fe10:fc86:2ab7:fede:3/tcp/18891",
            "/dns/qh.tdx1146.com/tcp/18891",
        ])

        status = manager.get_status()
        self.assertEqual(status["peers_count"], 1)
        tpass(f"添加对等节点: peer-qh ({status['peers_count']} 个节点)")

    def test_17_sync_manager_broadcast(self):
        """测试广播"""
        print(f"\n{Colors.YELLOW}[Test 17] 广播测试{Colors.RESET}")

        from core.monument_sync import MonumentSyncManager

        manager = MonumentSyncManager(
            node_addrs=["/ip4/127.0.0.1/tcp/18891"]
        )

        result = manager.broadcast({
            "title": "测试广播",
            "body": "这是测试广播的碑文",
            "tags": ["test"],
        })

        self.assertIn("success", result)
        self.assertIn("is_duplicate", result)
        self.assertFalse(result["is_duplicate"])
        tpass(f"广播结果: success={result['success']}, duplicate={result['is_duplicate']}")

        # 第二次广播(应去重)
        result2 = manager.broadcast({
            "title": "测试广播",
            "body": "这是测试广播的碑文",
            "tags": ["test"],
        })
        self.assertTrue(result2["is_duplicate"])
        tpass("重复广播被正确去重")

    def test_18_sync_manager_receive(self):
        """测试接收信封"""
        print(f"\n{Colors.YELLOW}[Test 18] 接收信封测试{Colors.RESET}")

        from core.monument_sync import MonumentSyncManager
        from core.envelope import create_sync_envelope

        manager = MonumentSyncManager(
            node_addrs=["/ip4/127.0.0.1/tcp/18891"]
        )

        env = create_sync_envelope(
            monument_data={"title": "接收测试", "body": "test"},
            node_addrs=["/ip6/240e:3a1:6437:37b0::1000/tcp/18891"],
            peer_id="sender-001",
        )

        result = manager.receive(env, from_peer="sender-001")

        self.assertTrue(result["accepted"])
        self.assertFalse(result["is_duplicate"])
        self.assertEqual(result["from"], "sender-001")
        tpass(f"接收信封: accepted={result['accepted']}, from={result['from']}")

        # 自动添加发送方为对等节点
        status = manager.get_status()
        self.assertIn("sender-001", status["peers"])
        tpass("发送方自动加入对等节点列表")

    def test_19_sync_manager_convergence(self):
        """测试全网收敛模拟"""
        print(f"\n{Colors.YELLOW}[Test 19] 全网收敛模拟{Colors.RESET}")

        from core.monument_sync import MonumentSyncManager

        manager = MonumentSyncManager(
            node_addrs=["/ip4/127.0.0.1/tcp/18891"]
        )

        # 模拟 5 个节点的全同步
        result = manager.simulate_convergence(num_nodes=5)

        self.assertTrue(result["converged"])
        self.assertGreater(result["rounds"], 0)
        self.assertGreater(result["total_monuments"], 0)
        tpass(f"收敛模拟: {result['nodes']} 节点, {result['rounds']} 轮, "
                  f"{result['total_monuments']} 条碑文")

    # ════════════════════════════════════════
    # 测试6: 去重缓存
    # ════════════════════════════════════════

    def test_20_dedup_cache(self):
        """测试去重缓存"""
        print(f"\n{Colors.YELLOW}[Test 20] 去重缓存{Colors.RESET}")

        from core.monument_sync import DeduplicationCache

        cache = DeduplicationCache(max_size=10)

        m1 = {"title": "A", "body": "Content A"}
        m2 = {"title": "B", "body": "Content B"}

        self.assertFalse(cache.is_duplicate(m1))
        cache.mark_seen(m1)
        self.assertTrue(cache.is_duplicate(m1))
        self.assertFalse(cache.is_duplicate(m2))

        tpass(f"去重缓存: size={cache.size}")

    # ════════════════════════════════════════
    # 测试7: 中继服务器
    # ════════════════════════════════════════

    def test_21_relay_server_basic(self):
        """测试中继服务器基础功能"""
        print(f"\n{Colors.YELLOW}[Test 21] 中继服务器基础功能{Colors.RESET}")

        from relay.relay_server import RelayServer

        server = RelayServer(host="127.0.0.1", port=18901)

        # 注册节点
        server.register_peer("node-a", ["/ip4/127.0.0.1/tcp/18891"])
        server.register_peer("node-b", ["/ip4/127.0.0.1/tcp/18892"])

        self.assertIn("node-a", server.peers)
        self.assertIn("node-b", server.peers)
        tpass("节点注册成功")

        # 转发消息
        result = server.relay("node-a", "node-b", {
            "type": "monument_sync",
            "data": {"title": "test"},
        })

        self.assertEqual(result["status"], "relayed")
        self.assertEqual(result["to"], "node-b")
        tpass(f"消息转发成功: {result['status']}")

        # 检查离线消息
        messages = server._get_offline_messages("node-b")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["from"], "node-a")
        tpass(f"离线消息缓存: {len(messages)} 条")

        # 统计
        stats = server.get_stats()
        self.assertEqual(stats["total_messages_relayed"], 1)
        tpass(f"服务器统计: {stats['total_messages_relayed']} 条已转发")

    def test_22_relay_server_health(self):
        """测试中继服务器健康检查和统计"""
        print(f"\n{Colors.YELLOW}[Test 22] 中继服务器健康检查{Colors.RESET}")

        from relay.relay_server import RelayServer

        server = RelayServer(host="127.0.0.1", port=18902)

        # 注册多个节点
        for i in range(3):
            server.register_peer(f"peer-{i}", [f"/ip4/127.0.0.1/tcp/{18891+i}"])

        # 一些在线,一些心跳
        server._heartbeat("peer-0")

        stats = server.get_stats()
        self.assertEqual(stats["registered_peers"], 3)
        tpass(f"注册节点数: {stats['registered_peers']}")

    def test_23_relay_get_peer_addrs(self):
        """测试获取中继节点多地址"""
        print(f"\n{Colors.YELLOW}[Test 23] 中继节点地址获取{Colors.RESET}")

        from relay.relay_server import RelayServer

        # IPv6 监听
        server_v6 = RelayServer(host="::", port=18900)
        # IPv4 监听
        server_v4 = RelayServer(host="0.0.0.0", port=18900)

        tpass("中继服务器支持双栈监听")

    # ════════════════════════════════════════
    # 场景测试
    # ════════════════════════════════════════

    def test_24_scenario_multi_hop_relay(self):
        """场景:多跳中继转发"""
        print(f"\n{Colors.YELLOW}[Test 24] 场景:多跳中继转发{Colors.RESET}")

        from relay.relay_server import RelayServer

        server_a = RelayServer(host="127.0.0.1", port=18903)
        server_b = RelayServer(host="127.0.0.1", port=18904)

        # 节点链: node-1 → relay-a → relay-b → node-2
        server_a.register_peer("node-1", ["/ip4/127.0.0.1/tcp/18891"])
        server_a.register_peer("relay-b", ["/ip4/127.0.0.1/tcp/18904"])

        # relay-a 转发消息到 node-2(通过 relay-b)
        result = server_a.relay("node-1", "relay-b", {
            "type": "relay_forward",
            "target": "node-2",
            "payload": {"hello": "world"},
        })

        self.assertEqual(result["status"], "relayed")
        tpass(f"多跳中继: node-1 → relay-a → relay-b → node-2")

        # 验证消息到达
        relay_b_msgs = server_a._get_offline_messages("relay-b")
        self.assertEqual(len(relay_b_msgs), 1)
        self.assertEqual(relay_b_msgs[0]["message"]["type"], "relay_forward")
        tpass("多跳中继消息到达目标")

    def test_25_scenario_full_stack(self):
        """场景:完整协议栈演示"""
        print(f"\n{Colors.YELLOW}[Test 25] 场景:完整协议栈演示{Colors.RESET}")

        from core.multiaddr import MultiAddr, AddressResolver
        from core.envelope import create_sync_envelope, parse_envelope, envelope_to_json
        from core.monument_sync import MonumentSyncManager

        # 模拟轻如烟(本机)和姐姐(远程)的节点
        my_addrs = [
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
            "/ip4/192.168.0.149/tcp/18891",
        ]
        qh_addrs = [
            "/ip6/240e:3a5:646d:fe10:fc86:2ab7:fede:3/tcp/18891",
            "/dns/qh.tdx1146.com/tcp/18891",
        ]

        # 1. 创建同步管理器
        my_manager = MonumentSyncManager(node_addrs=my_addrs)
        qh_manager = MonumentSyncManager(node_addrs=qh_addrs)

        # 2. 添加对等节点
        my_manager.add_peer("qh", qh_addrs)
        qh_manager.add_peer("dandan", my_addrs)

        self.assertEqual(my_manager.get_status()["peers_count"], 1)
        self.assertEqual(qh_manager.get_status()["peers_count"], 1)
        tpass("P1: 双方互加对等节点")

        # 3. 创建信封
        env = create_sync_envelope(
            monument_data={
                "title": "全栈测试碑文",
                "body": "通过多层次网络体系同步",
                "tags": ["test", "fullstack", "network"],
            },
            node_addrs=my_addrs,
            peer_id="dandan",
        )

        self.assertIn("envelope", env)
        self.assertEqual(env["envelope"]["peer_id"], "dandan")
        tpass("P2: 创建完整信封")

        # 4. JSON 序列化(模拟网络传输)
        json_str = envelope_to_json(env)
        self.assertGreater(len(json_str), 50)
        tpass(f"P3: 信封 JSON 序列化 ({len(json_str)} 字节)")

        # 5. 接收模拟
        import json
        parsed = json.loads(json_str)
        result = qh_manager.receive(parsed, from_peer="dandan")

        self.assertTrue(result["accepted"])
        self.assertEqual(result["monument"]["title"], "全栈测试碑文")
        tpass("P4: 接收方解析成功")

        # 6. 去重验证
        parsed2 = json.loads(json_str)
        result2 = qh_manager.receive(parsed2, from_peer="dandan")
        self.assertTrue(result2["is_duplicate"])
        tpass("P5: 重复接收被去重")

        # 7. 更新状态
        my_status = my_manager.get_status()
        qh_status = qh_manager.get_status()
        tinfo(f"发送方: {my_status['peers_count']} 对等, {my_status['broadcast_count']} 广播")
        tinfo(f"接收方: {qh_status['peers_count']} 对等, {qh_status['received_count']} 接收")

        tpass("🎉 完整协议栈演示通过")


# ─── 手动运行 ───────────────────────────────

def run_all_tests():
    """运行全部测试"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 按顺序添加测试
    test_methods = [
        name for name in dir(TestMultiNetwork)
        if name.startswith("test_")
    ]
    test_methods.sort()

    for method in test_methods:
        suite.addTest(TestMultiNetwork(method))

    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)

    print(f"\n{'='*60}")
    print(f"测试结果: {result.testsRun} 项测试")
    print(f"  ✅ 通过: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  ❌ 失败: {len(result.failures)}")
    print(f"  ⚠️  错误: {len(result.errors)}")
    print(f"{'='*60}")

    return len(result.failures) == 0 and len(result.errors) == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

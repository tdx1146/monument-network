#!/usr/bin/env python3
"""
DHT 节点发现单元测试。

测试内容：
  1. DHTNode 创建与配置
  2. 节点注册与查询（本地测试模式）
  3. 心跳检测
  4. 节点离线处理
  5. 地址编解码
  6. 节点 ID 生成
  7. 引导节点管理
  8. 多节点发现（通过两个实例通信）
  9. 状态持久化
  10. NodeDiscovery 管理器

运行方式：
    cd /vol2/1000/AI专用/丰碑网络/code && python3 tests/test_dht_node.py

注意：需要 kademlia 库和 rpcudp 库。
      UDF 测试需要可用端口（默认 9000-9002）。
"""

import sys
import os
import json
import tempfile
import time
import asyncio

# 确保 code/ 在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.dht_node import (
    DHTNode,
    NodeDiscovery,
    create_node_id_from_peer_id,
    KEY_PREFIX_PEER,
    KEY_PREFIX_HEARTBEAT,
    DEFAULT_TTL,
    PEER_TIMEOUT,
)

errors = []


def check(name: str, cond: bool, detail: str = ""):
    if not cond:
        errors.append(f"FAIL: {name} — {detail}")
        print(f"  ✗ {name}")
    else:
        print(f"  ✓ {name}")


# ─── 辅助函数 ─────────────────────────────────────────────

def find_free_port(start=9000, count=3):
    """找一组可用端口。"""
    import socket
    ports = []
    for p in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                ports.append(p)
                if len(ports) >= count:
                    break
            except OSError:
                continue
    return ports


async def run_dht_test():
    """运行所有 DHT 异步测试。"""
    
    # ─── 1. DHTNode 创建与配置 ──────────────────────────────
    print("\n=== 1. DHTNode 创建与配置 ===")
    
    node = DHTNode()
    check("DHTNode 创建", node is not None)
    check("node_id 20 字节", len(node.node_id) == 20)
    check("node_id_hex 40 字符", len(node.node_id_hex) == 40)
    check("初始未运行", node.is_running is False)
    check("空 peer 列表", len(node.known_peers) == 0)
    
    # 使用存储目录
    with tempfile.TemporaryDirectory() as tmpdir:
        node_with_storage = DHTNode(storage_dir=tmpdir)
        check("带存储目录创建", node_with_storage is not None)
    
    # ─── 2. 地址编解码 ───────────────────────────────────────
    print("\n=== 2. 地址编解码 ===")
    
    addr_ipv4 = DHTNode.encode_peer_address("192.168.1.50", 18889)
    check("IPv4 编码", addr_ipv4 == "192.168.1.50:18889")
    
    ip, port = DHTNode.decode_peer_address(addr_ipv4)
    check("IPv4 解码 IP", ip == "192.168.1.50")
    check("IPv4 解码端口", port == 18889)
    
    addr_ipv6 = DHTNode.encode_peer_address("::1", 18889)
    check("IPv6 编码", addr_ipv6 == "[::1]:18889")
    
    ip6, port6 = DHTNode.decode_peer_address(addr_ipv6)
    check("IPv6 解码 IP", ip6 == "::1")
    check("IPv6 解码端口", port6 == 18889)
    
    # ─── 3. 节点 ID 生成 ─────────────────────────────────────
    print("\n=== 3. 节点 ID 生成 ===")
    
    peer_id = "test-peer-id-base64=="
    node_id = create_node_id_from_peer_id(peer_id)
    check("节点 ID 20 字节", len(node_id) == 20)
    check("相同输入生成相同节点 ID", node_id == create_node_id_from_peer_id(peer_id))
    check("不同输入生成不同节点 ID", node_id != create_node_id_from_peer_id("different-id"))
    
    # ─── 4. 引导节点管理 ─────────────────────────────────────
    print("\n=== 4. 引导节点管理 ===")
    
    bootstrap_nodes = [("192.168.1.100", 9000), ("192.168.1.101", 9000)]
    node.set_bootstrap_nodes(bootstrap_nodes)
    retrieved = node.get_bootstrap_nodes()
    check("引导节点设置", retrieved == bootstrap_nodes)
    check("引导节点复制", retrieved is not bootstrap_nodes)  # 应该返回副本
    
    # ─── 5. 节点注册和查询（内存模式） ─────────────────────
    print("\n=== 5. 基础方法测试 ===")
    
    # 在没有启动时验证边界情况
    peer_id_a = "peer-a-test-123"
    peer_id_b = "peer-b-test-456"
    
    # 未启动时注册应返回 False
    result = await node.register(peer_id_a, "192.168.1.50:18889")
    check("未启动注册返回 False", result is False)
    
    # lookup 未启动应返回 None
    addr = await node.lookup(peer_id_a)
    check("未启动查询返回 None", addr is None)
    
    # ─── 6. 双节点 DHT 测试 ────────────────────────────────
    print("\n=== 6. 双节点 DHT 注册/查询 ===")
    
    ports = find_free_port(19000, 2)
    if len(ports) < 2:
        print("  ⚠ 端口不足，跳过真实 DHT 测试")
    else:
        port_a, port_b = ports
        
        # 启动节点 A
        node_a = DHTNode()
        await node_a.start(port=port_a, interface="127.0.0.1")
        check("节点 A 已启动", node_a.is_running)
        
        # 启动节点 B（引导到 A）
        node_b = DHTNode()
        await node_b.start(
            port=port_b,
            interface="127.0.0.1",
            bootstrap=[("127.0.0.1", port_a)],
        )
        check("节点 B 已启动", node_b.is_running)
        
        # 节点 B 注册数据
        test_peer = "test-node-via-dht"
        test_addr = "192.168.1.50:18889"
        
        result = await node_b.register(test_peer, test_addr)
        check("DHT 注册成功", result is True)
        
        # 等待 DHT 扩散
        await asyncio.sleep(0.5)
        
        # 节点 A 查询 B 注册的节点
        lookup_result = await node_a.lookup(test_peer)
        check(f"DHT 查询成功: {lookup_result}", lookup_result == test_addr)
        
        # 查询另一个不存在的节点
        not_found = await node_a.lookup("nonexistent-peer")
        check("查询不存在的节点返回 None", not_found is None)
        
        # 节点 B 注册自己的信息
        await node_b.register(peer_id_a, "10.0.0.1:18889")
        
        await asyncio.sleep(0.3)
        
        result = await node_a.lookup(peer_id_a)
        check("跨节点查找成功", result == "10.0.0.1:18889")
        
        # 本地缓存测试
        cached = await node_a.lookup(peer_id_a)
        check("缓存查找成功", cached == "10.0.0.1:18889")
        
        # list_peers
        peers = await node_a.list_peers()
        check("list_peers 包含缓存 node", len(peers) >= 1)
        
        # 清理
        await node_b.stop()
        await node_a.stop()
        check("节点 A 已停止", not node_a.is_running)
        check("节点 B 已停止", not node_b.is_running)
    
    # ─── 7. 心跳检测 ────────────────────────────────────────
    print("\n=== 7. 心跳检测 ===")
    
    ports = find_free_port(19100, 1)
    if ports:
        port = ports[0]
        
        hb_node = DHTNode()
        await hb_node.start(port=port, interface="127.0.0.1")
        
        hb_peer = "heartbeat-test-peer"
        
        # 发送心跳
        result = await hb_node.heartbeat(hb_peer)
        check("心跳发送成功", result is True)
        
        # 检查是否在线（刚发送应该在线）
        alive = hb_node.is_peer_alive(hb_peer, timeout=60)
        check("心跳后节点在线", alive is True)
        
        # 记录原始时间戳再验证（心跳刚发送，时间戳 = now）
        # is_peer_alive 用（now - timestamp）< timeout 判断
        # 设置 timeout=0 确保之前的时间戳过期
        ts_before = hb_node._known_peers.get(hb_peer, 0)
        check("心跳时间戳已记录", ts_before > 0)
        
        # timeout=0：任何心跳都过期
        dead = hb_node.is_peer_alive(hb_peer, timeout=0)
        check("timeout=0 时节点离线", dead is False)
        
        # 超大 timeout
        alive = hb_node.is_peer_alive(hb_peer, timeout=999999)
        check("超大 timeout 节点在线", alive is True)
        
        # 获取在线列表（超大 timeout）
        alive_list = hb_node.get_alive_peers(timeout=999999)
        check("在线列表包含心跳节点", hb_peer in alive_list)
        
        # 获取离线列表（timeout=0）
        dead_list = hb_node.get_dead_peers(timeout=0)
        check("离线列表包含心跳节点（timeout=0）", hb_peer in dead_list)
        
        # get_peer_info（只注册地址后才有完整信息）
        await hb_node.register(hb_peer, "10.0.0.2:18889")
        info = hb_node.get_peer_info(hb_peer)
        if info:
            check("peer_info 包含 peer_id", info["peer_id"] == hb_peer)
            check("peer_info 包含 address", info["address"] == "10.0.0.2:18889")
            check("peer_info 包含 is_alive", "is_alive" in info)
        
        await hb_node.stop()
    
    # ─── 8. 状态持久化 ──────────────────────────────────────
    print("\n=== 8. 状态持久化 ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        persist_node = DHTNode(storage_dir=tmpdir)
        check("持久化目录创建", os.path.exists(tmpdir))
        
        # 手动添加一些数据
        persist_node._local_peers["p1"] = "addr1"
        persist_node._local_peers["p2"] = "addr2"
        persist_node._known_peers["p1"] = 1000.0
        
        # 保存状态
        persist_node._save_state()
        state_file = os.path.join(tmpdir, "dht_state.pkl")
        check("状态文件已创建", os.path.exists(state_file))
        
        # 加载到新节点
        new_node = DHTNode(storage_dir=tmpdir)
        new_node._load_state()
        loaded_peers = new_node._local_peers
        check("加载后 peer 数量正确", len(loaded_peers) == 2)
        check("加载后 p1 地址", loaded_peers.get("p1") == "addr1")
        check("加载后 p2 地址", loaded_peers.get("p2") == "addr2")
        check("加载后 known_peers", new_node._known_peers.get("p1") == 1000.0)
    
    # ─── 9. NodeDiscovery 管理器 ──────────────────────────
    print("\n=== 9. NodeDiscovery 管理器 ===")
    
    disc_node = DHTNode()
    discovery = NodeDiscovery(disc_node)
    
    check("NodeDiscovery 创建", discovery is not None)
    
    # 添加 peer 服务
    discovery.add_peer_service("peer-1", "http://192.168.1.50:18889")
    discovery.add_peer_service("peer-2", "http://192.168.1.51:18889")
    
    svc = discovery.get_peer_service("peer-1")
    check("获取 service URL", svc == "http://192.168.1.50:18889")
    
    not_found = discovery.get_peer_service("nonexistent")
    check("不存在的 service 返回 None", not_found is None)
    
    all_svcs = discovery.get_all_peer_services()
    check("所有 service 数量", len(all_svcs) == 2)
    
    # 发现（空 DHT 时，无新节点）
    await asyncio.sleep(0.1)  # 即使没启动，discover 也能处理
    try:
        result = await discovery.discover()
        # 可能返回空或处理异常
        check("discover 不抛出异常", True)
    except Exception as e:
        check(f"discover 异常: {e}", False)
    
    # ─── 10. 边界情况 ──────────────────────────────────────
    print("\n=== 10. 边界情况 ===")
    
    # 重复 stop
    small_node = DHTNode()
    # 还没 start，stop 应该安全
    try:
        await small_node.stop()
        check("未启动的 stop 安全", True)
    except Exception as e:
        check(f"未启动的 stop 异常: {e}", False)
    
    # 重复 stop（已启动）
    ports = find_free_port(19200, 1)
    if ports:
        bounded_node = DHTNode()
        await bounded_node.start(port=ports[0], interface="127.0.0.1")
        await bounded_node.stop()
        await bounded_node.stop()  # 重复 stop
        check("重复 stop 安全", True)
    
    # 清理
    disc_node._running = False
    if disc_node._heartbeat_task:
        disc_node._heartbeat_task.cancel()
    
    await node.stop()
    await node_with_storage.stop()
    
    print(f"\n{'='*50}")


# ─── 入口 ─────────────────────────────────────────────────

def main():
    """入口函数，运行所有异步测试。"""
    print("=" * 50)
    print("  DHT 节点发现测试")
    print("=" * 50)
    
    asyncio.run(run_dht_test())
    
    if errors:
        print(f"\n  ❌ FAILURES: {len(errors)}")
        for e in errors:
            print(f"     {e}")
        sys.exit(1)
    else:
        print("\n  ✅ ALL DHT TESTS PASSED")
        print("=" * 50)


if __name__ == "__main__":
    main()

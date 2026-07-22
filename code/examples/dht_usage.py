#!/usr/bin/env python3
"""
丰碑网络 DHT 节点发现 —— 使用示例

本示例展示：
  1. 如何启动 DHT 节点
  2. 如何注册节点信息
  3. 如何查询其他节点
  4. 心跳检测
  5. 多节点发现

运行方式：
    python3 examples/dht_usage.py

注意：此示例需要两个可用 UDP 端口（默认 9000-9001）。
      引导节点地址需要根据实际网络环境修改。
"""

import sys
import os
import asyncio

# 确保 code/ 在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.dht_node import (
    DHTNode,
    NodeDiscovery,
    create_node_id_from_peer_id,
)


async def example_basic():
    """示例 1：基础用法 —— 单节点注册和查询。"""
    print("\n" + "=" * 60)
    print("  示例 1：单节点基础用法（无网络）")
    print("=" * 60)

    # 创建 DHT 节点
    node = DHTNode()
    print(f"  节点 ID: {node.node_id_hex[:16]}...")

    # 启动（不引导到任何节点，形成孤立网络）
    await node.start(port=9000, interface="127.0.0.1")

    # 注册一个节点
    peer_id = "my-peer-id-base64=="
    success = await node.register(peer_id, "192.168.1.50:18889")
    print(f"  注册节点: {'成功' if success else '失败'}")

    # 查询（本地缓存）
    addr = await node.lookup(peer_id)
    print(f"  查询节点: {peer_id[:16]}... -> {addr}")

    # 心跳
    await node.heartbeat(peer_id)
    print(f"  节点在线: {node.is_peer_alive(peer_id)}")

    await node.stop()
    print("  节点已停止")


async def example_multi_node():
    """示例 2：多节点发现。"""
    print("\n" + "=" * 60)
    print("  示例 2：双节点 DHT 通信")
    print("=" * 60)

    # 节点 A（引导节点）
    node_a = DHTNode()
    await node_a.start(port=9002, interface="127.0.0.1")
    print(f"  节点 A: {node_a.node_id_hex[:16]}... (引导节点)")

    # 注册节点的 HTTP 服务地址
    await node_a.register("node-a-peer", "192.168.1.1:18889")

    # 节点 B（通过 A 加入网络）
    node_b = DHTNode()
    node_b.set_bootstrap_nodes([("127.0.0.1", 9002)])
    await node_b.start(port=9003, interface="127.0.0.1")
    print(f"  节点 B: {node_b.node_id_hex[:16]}... (通过 A 加入)")

    # 节点 B 注册自己的信息
    await node_b.register("node-b-peer", "192.168.1.2:18889")

    # 等待 DHT 扩散
    await asyncio.sleep(0.5)

    # 节点 A 查询节点 B 的信息（本地缓存）
    addr = await node_a.lookup("node-b-peer")
    print(f"  节点 A 查询 node-b-peer: {addr}")

    # 节点 B 查询节点 A 的信息（本地缓存）
    addr = await node_b.lookup("node-a-peer")
    print(f"  节点 B 查询 node-a-peer: {addr}")

    # 列出所有已知 peer
    peers_a = await node_a.list_peers()
    print(f"  节点 A 已知 peers: {list(peers_a.keys())}")

    peers_b = await node_b.list_peers()
    print(f"  节点 B 已知 peers: {list(peers_b.keys())}")

    await node_b.stop()
    await node_a.stop()
    print("  所有节点已停止")


async def example_discovery():
    """示例 3：节点发现管理器。"""
    print("\n" + "=" * 60)
    print("  示例 3：节点发现管理器")
    print("=" * 60)

    node = DHTNode()
    await node.start(port=9004, interface="127.0.0.1")
    print(f"  节点已启动: {node.node_id_hex[:16]}...")

    # 创建发现管理器
    discovery = NodeDiscovery(node)

    # 添加已知的 peer 服务
    discovery.add_peer_service("peer-1", "http://192.168.1.50:18889")
    discovery.add_peer_service("peer-2", "http://192.168.1.51:18889")

    # 注册节点到 DHT
    await node.register("peer-1", "192.168.1.50:18889")
    await node.register("peer-2", "192.168.1.51:18889")

    # 列出所有服务
    services = discovery.get_all_peer_services()
    print(f"  已知服务数: {len(services)}")
    for pid, url in services.items():
        print(f"    {pid[:16]}... -> {url}")

    await node.stop()
    print("  节点已停止")


async def example_bootstrap():
    """示例 4：引导节点和缓存状态。"""
    print("\n" + "=" * 60)
    print("  示例 4：引导节点管理与状态持久化")
    print("=" * 60)

    import tempfile

    # 使用临时目录存储状态
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建带持久化的节点
        node = DHTNode()
        node.storage_path = os.path.join(tmpdir, "dht_registry.json")

        # 设置引导节点
        bootstrap_nodes = [
            ("192.168.1.100", 9000),
            ("192.168.1.101", 9000),
        ]
        node.set_bootstrap_nodes(bootstrap_nodes)
        print(f"  引导节点: {len(bootstrap_nodes)} 个")
        for ip, port in bootstrap_nodes:
            print(f"    {ip}:{port}")

        # 启动并注册一些节点
        await node.start(port=9005, interface="127.0.0.1")

        test_peers = [
            ("alice-peer", "10.0.0.1:18889"),
            ("bob-peer", "10.0.0.2:18889"),
            ("charlie-peer", "10.0.0.3:18889"),
        ]
        for pid, addr in test_peers:
            await node.register(pid, addr)

        print(f"  已注册 {len(test_peers)} 个节点")

        # 节点 ID 一致性示例
        peer_id = "my-ed25519-public-key-base64"
        dht_node_id = create_node_id_from_peer_id(peer_id)
        print(f"  PeerID -> DHT NodeID: {dht_node_id.hex()[:20]}...")

        await node.stop()
        print("  节点已停止（状态已保存）")

    print("  临时目录已清理")


async def main():
    """运行所有示例。"""
    print("=" * 60)
    print("  丰碑网络 DHT 节点发现 — 使用示例")
    print("=" * 60)

    await example_basic()
    await example_multi_node()
    await example_discovery()
    await example_bootstrap()

    print("\n" + "=" * 60)
    print("  所有示例完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

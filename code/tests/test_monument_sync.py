"""
测试丰碑自动广播与同步
"""

import sys
import os
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.monument_sync import (
    DeduplicationCache,
    MonumentBroadcaster,
    MonumentSyncManager,
    simulate_network_convergence
)
from core.p2p_network import P2PIdentity
from db.individual_repo import IndividualRepository
from config import DB_PATH
import sqlite3


class TestDeduplicationCache(unittest.TestCase):
    """测试去重缓存"""
    
    def test_add_new_entry(self):
        """测试添加新条目"""
        cache = DeduplicationCache()
        result = cache.add("monument-1", "sig-1")
        self.assertTrue(result)
    
    def test_add_duplicate(self):
        """测试添加重复条目"""
        cache = DeduplicationCache()
        cache.add("monument-1", "sig-1")
        result = cache.add("monument-1", "sig-1")
        self.assertFalse(result)
    
    def test_contains(self):
        """测试包含检查"""
        cache = DeduplicationCache()
        cache.add("monument-1", "sig-1")
        self.assertTrue(cache.contains("monument-1", "sig-1"))
        self.assertFalse(cache.contains("monument-1", "sig-2"))
    
    def test_clear(self):
        """测试清空"""
        cache = DeduplicationCache()
        cache.add("monument-1", "sig-1")
        cache.clear()
        self.assertFalse(cache.contains("monument-1", "sig-1"))
    
    def test_max_size_eviction(self):
        """测试缓存满时淘汰"""
        cache = DeduplicationCache(max_size=100)
        
        # 添加超过容量的条目
        for i in range(150):
            cache.add(f"monument-{i}", f"sig-{i}")
        
        # 缓存应该被清空一半
        self.assertTrue(len(cache._cache) <= 100)


class TestMonumentBroadcaster(unittest.TestCase):
    """测试丰碑广播器"""
    
    def setUp(self):
        self.identity = P2PIdentity()
    
    def test_add_peer(self):
        """测试添加节点"""
        broadcaster = MonumentBroadcaster(self.identity)
        broadcaster.add_peer("peer-1", "192.168.1.1:18889")
        self.assertIn("peer-1", broadcaster.known_peers)
    
    def test_remove_peer(self):
        """测试移除节点"""
        broadcaster = MonumentBroadcaster(self.identity)
        broadcaster.add_peer("peer-1", "192.168.1.1:18889")
        broadcaster.remove_peer("peer-1")
        self.assertNotIn("peer-1", broadcaster.known_peers)
    
    def test_broadcast_no_peers(self):
        """测试无节点时广播"""
        broadcaster = MonumentBroadcaster(self.identity)
        monument_data = {"test": "data"}
        results = broadcaster.broadcast(monument_data)
        self.assertEqual(results, {})
    
    def test_broadcast_with_exclude(self):
        """测试排除节点"""
        broadcaster = MonumentBroadcaster(self.identity)
        broadcaster.add_peer("peer-1", "192.168.1.1:18889")
        broadcaster.add_peer("peer-2", "192.168.1.2:18889")
        
        # 排除 peer-1
        monument_data = {"test": "data"}
        results = broadcaster.broadcast(
            monument_data,
            exclude_peers={"peer-1"}
        )
        
        # 只有 peer-2 会被尝试
        self.assertIn("peer-2", results)
        self.assertNotIn("peer-1", results)


class TestMonumentSyncManager(unittest.TestCase):
    """测试丰碑同步管理器"""
    
    def setUp(self):
        # 使用主数据库（简化测试）
        self.repo = IndividualRepository()
        self.identity = P2PIdentity()
        self.manager = MonumentSyncManager(
            self.identity,
            self.repo,
            max_cache_size=100
        )
    
    def test_on_new_monument_no_peers(self):
        """测试无节点时产生新丰碑"""
        monument = {"id": "test-sync-1", "content": "测试丰碑"}
        results = self.manager.on_new_monument("test-sync-ai", monument)
        self.assertEqual(results, {})
    
    def test_on_receive_invalid_signature(self):
        """测试接收无效签名丰碑"""
        signed_data = {
            "protocol": "monument-exchange-v1",
            "from_peer": "attacker",
            "ai_id": "test-ai",
            "monuments": [{"id": "test-1", "content": "fake"}],
            "signature": "invalid-signature"
        }
        
        success, message = self.manager.on_receive_monument(signed_data)
        self.assertFalse(success)
        self.assertIn("签名验证失败", message)
    
    def test_deduplication(self):
        """测试去重"""
        # 创建有效签名
        identity2 = P2PIdentity()
        monument_data = {
            "protocol": "monument-exchange-v1",
            "from_peer": identity2.peer_id,
            "ai_id": "test-dedup-ai",
            "monuments": [{"id": "test-dedup-1", "content": "测试去重"}],
            "timestamp": "2026-07-13T00:00:00Z"
        }
        
        # 手动签名
        from core.p2p_network import sign_monument_message
        signed_data = sign_monument_message(monument_data, identity2)
        
        # 第一次接收
        success1, msg1 = self.manager.on_receive_monument(signed_data)
        self.assertTrue(success1, msg1)
        
        # 第二次接收（去重）
        success2, msg2 = self.manager.on_receive_monument(signed_data)
        self.assertFalse(success2)
        self.assertIn("去重", msg2)


class TestNetworkConvergence(unittest.TestCase):
    """测试全网收敛"""
    
    def test_single_node(self):
        """测试单节点"""
        repo = IndividualRepository()
        identity = P2PIdentity()
        manager = MonumentSyncManager(identity, repo)
        
        monument = {"id": "test-conv-1", "content": "测试收敛"}
        result = simulate_network_convergence([manager], monument)
        
        self.assertEqual(result["total_nodes"], 1)
        self.assertEqual(result["converged_nodes"], 1)
        self.assertEqual(result["convergence_rate"], 1.0)
    
    def test_empty_nodes(self):
        """测试空节点列表"""
        monument = {"id": "test-conv-2", "content": "测试"}
        result = simulate_network_convergence([], monument)
        
        self.assertEqual(result["total_nodes"], 0)
        self.assertEqual(result["converged_nodes"], 0)


if __name__ == "__main__":
    unittest.main()
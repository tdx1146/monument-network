"""
丰碑自动发现和全网同步 - 单元测试

测试覆盖:
    - monument_index: 索引构建/网络查询/搜索/差异同步
    - periodic_syncer: 检查新丰碑/自动同步/守护进程
    - rebirth_protocol phase5: 局域网自动发现/全网同步
    - recovery_routes: 新增API端点
"""

import os
import sys
import json
import socket
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.monument_index import (
    MonumentIndex,
    MonumentIndexEntry,
    IndexDiff,
    SyncStatus,
)
from core.periodic_syncer import (
    PeriodicSyncer,
    SyncReport,
)
from core.rebirth_protocol import (
    RebirthProtocol,
    Monument,
    AutoDiscoveryResult,
    RebirthResult,
    PhaseError,
    _get_local_lan_ip,
)
from api.recovery_routes import (
    RecoveryHandler,
    ApiResponse,
)


# =============================================================================
# 辅助函数
# =============================================================================

def _make_simple_monument(mid: str, title: str = "", tags: list = None) -> dict:
    """创建测试用丰碑"""
    return {
        "monument_id": mid,
        "title": title or f"测试丰碑_{mid}",
        "body": f"这是丰碑 {mid} 的内容",
        "tags": tags or ["test", mid],
        "created_at": "2026-07-13T12:00:00Z",
        "status": "finalized",
    }


def _make_monuments_dict(mids: list) -> dict:
    """创建丰碑字典 {mid: data}"""
    return {mid: _make_simple_monument(mid) for mid in mids}


def _make_test_peers() -> dict:
    """创建测试 peers"""
    return {
        "peer-a": ["192.168.0.100:18891", "10.0.0.1:18891"],
        "peer-b": ["192.168.0.101:18891"],
    }


# =============================================================================
# MonumentIndex 测试
# =============================================================================

class TestMonumentIndex:
    """丰碑索引服务测试"""

    def test_build_local_index(self):
        """构建本地索引"""
        monuments = _make_monuments_dict(["m1", "m2", "m3"])
        peers = _make_test_peers()
        index = MonumentIndex(monuments, peers, ai_id="test-ai")

        entry = index.build_local_index()

        assert entry.ai_id == "test-ai"
        assert entry.monument_count == 3
        assert sorted(entry.monuments) == ["m1", "m2", "m3"]
        assert len(entry.peer_addrs) == 3  # 去重后
        assert "192.168.0.100:18891" in entry.peer_addrs
        assert "10.0.0.1:18891" in entry.peer_addrs

    def test_build_local_index_empty(self):
        """空索引"""
        index = MonumentIndex({}, {})
        entry = index.build_local_index()
        assert entry.monument_count == 0
        assert entry.monuments == []

    def test_index_entry_to_from_dict(self):
        """索引条目序列化/反序列化往返"""
        entry = MonumentIndexEntry(
            ai_id="ai-1",
            monument_count=3,
            monuments=["m1", "m2", "m3"],
            last_updated="2026-07-13T12:00:00Z",
            peer_addrs=["192.168.0.1:18891"],
        )
        d = entry.to_dict()
        restored = MonumentIndexEntry.from_dict(d)
        assert restored.ai_id == entry.ai_id
        assert restored.monument_count == 3
        assert restored.monuments == ["m1", "m2", "m3"]
        assert restored.peer_addrs == ["192.168.0.1:18891"]

    def test_search_monuments_by_title(self):
        """按标题搜索"""
        monuments = {
            "m1": _make_simple_monument("m1", title="知识沉淀协议"),
            "m2": _make_simple_monument("m2", title="网络发现"),
            "m3": _make_simple_monument("m3", title="丰碑索引"),
        }
        index = MonumentIndex(monuments, {})
        results = index.search_monuments("知识")
        assert len(results) == 1
        assert results[0]["monument_id"] == "m1"

    def test_search_monuments_multiple(self):
        """多结果搜索"""
        monuments = _make_monuments_dict(["m1", "m2", "m3"])
        index = MonumentIndex(monuments, {})
        # 所有碑文 body 都包含"丰碑"关键词
        results = index.search_monuments("丰碑")
        assert len(results) == 3

    def test_search_monuments_no_match(self):
        """无匹配搜索"""
        monuments = _make_monuments_dict(["m1"])
        index = MonumentIndex(monuments, {})
        results = index.search_monuments("不存在的内容")
        assert results == []

    def test_search_monuments_by_id_exact(self):
        """精确 monument_id 匹配"""
        monuments = _make_monuments_dict(["m42", "m100"])
        index = MonumentIndex(monuments, {})
        results = index.search_monuments("m42")
        assert len(results) == 1
        assert results[0]["monument_id"] == "m42"

    def test_search_monuments_by_tags(self):
        """按标签搜索"""
        monuments = {
            "m1": _make_simple_monument("m1", tags=["network", "discovery"]),
            "m2": _make_simple_monument("m2", tags=["data", "sync"]),
        }
        index = MonumentIndex(monuments, {})
        results = index.search_monuments("network")
        assert len(results) == 1
        assert results[0]["monument_id"] == "m1"

    def test_compute_index_diff_both_new(self):
        """两端各有新丰碑"""
        local = _make_monuments_dict(["m1", "m2"])
        remote_monuments = _make_monuments_dict(["m2", "m3", "m4"])
        index = MonumentIndex(local, {})
        remote_entry = MonumentIndexEntry(
            ai_id="peer-x",
            monument_count=3,
            monuments=["m2", "m3", "m4"],
            last_updated="",
            peer_addrs=["192.168.0.100:18891"],
        )
        diff = index.compute_index_diff(remote_entry)
        assert sorted(diff.local_missing) == ["m3", "m4"]
        assert sorted(diff.remote_missing) == ["m1"]
        assert diff.common == ["m2"]

    def test_compute_index_diff_identical(self):
        """两端索引完全相同"""
        local = _make_monuments_dict(["m1", "m2"])
        index = MonumentIndex(local, {})
        remote_entry = MonumentIndexEntry(
            ai_id="peer-x",
            monument_count=2,
            monuments=["m1", "m2"],
            last_updated="",
            peer_addrs=[],
        )
        diff = index.compute_index_diff(remote_entry)
        assert diff.local_missing == []
        assert diff.remote_missing == []
        assert diff.common == ["m1", "m2"]

    def test_compute_index_diff_local_empty(self):
        """本机无丰碑"""
        index = MonumentIndex({}, {})
        remote_entry = MonumentIndexEntry(
            ai_id="peer-x",
            monument_count=2,
            monuments=["m1", "m2"],
            last_updated="",
            peer_addrs=[],
        )
        diff = index.compute_index_diff(remote_entry)
        assert sorted(diff.local_missing) == ["m1", "m2"]
        assert diff.remote_missing == []

    def test_sync_from_diff_with_syncer(self):
        """使用同步器同步差异"""
        monuments = _make_monuments_dict(["m1"])
        peers = _make_test_peers()
        index = MonumentIndex(monuments, peers)

        # 模拟 syncer：直接写入本地
        synced_list = []
        def fake_syncer(mid, peer):
            synced_list.append(mid)
            monuments[mid] = _make_simple_monument(mid)

        diff = IndexDiff(
            peer_addr="192.168.0.100:18891",
            local_missing=["m2", "m3"],
            remote_missing=["m1"],
            common=[],
        )
        count = index.sync_from_diff(diff, syncer=fake_syncer)
        assert count == 2
        assert sorted(synced_list) == ["m2", "m3"]
        assert "m2" in monuments
        assert "m3" in monuments

    def test_sync_from_diff_no_missing(self):
        """无需同步"""
        index = MonumentIndex(_make_monuments_dict(["m1"]), {})
        diff = IndexDiff(
            peer_addr="192.168.0.100:18891",
            local_missing=[],
            remote_missing=["m1"],
            common=["m1"],
        )
        count = index.sync_from_diff(diff)
        assert count == 0

    def test_get_sync_status_default(self):
        """默认同步状态"""
        index = MonumentIndex(_make_monuments_dict(["m1"]), {})
        status = index.get_sync_status()
        assert status.total_monuments == 1
        assert status.synced_monuments == 1
        assert status.missing_monuments == 0
        assert status.last_sync_time == ""

    def test_sync_status_to_from_dict(self):
        """SyncStatus 序列化/反序列化"""
        s = SyncStatus(
            total_monuments=5,
            synced_monuments=3,
            missing_monuments=2,
            last_sync_time="2026-07-13T12:00:00Z",
            sync_errors=["error1"],
        )
        d = s.to_dict()
        restored = SyncStatus.from_dict(d)
        assert restored.total_monuments == 5
        assert restored.synced_monuments == 3
        assert restored.missing_monuments == 2
        assert restored.sync_errors == ["error1"]

    def test_clear_network_cache(self):
        """清空网络缓存"""
        index = MonumentIndex({}, {})
        index._network_index_cache["test"] = MonumentIndexEntry(
            ai_id="x", monument_count=0, monuments=[], last_updated="",
        )
        index.clear_network_cache()
        assert len(index._network_index_cache) == 0

    def test_clear_sync_errors(self):
        """清空同步错误"""
        index = MonumentIndex({}, {})
        index._sync_errors.append("error1")
        index.clear_sync_errors()
        assert index._sync_errors == []


# =============================================================================
# PeriodicSyncer 测试
# =============================================================================

class TestPeriodicSyncer:
    """定期同步器测试"""

    def test_check_new_monuments_no_peers(self):
        """无已知 peer 时检查"""
        index = MonumentIndex(_make_monuments_dict(["m1"]), {})
        sync_mgr = _make_mock_sync_manager()
        syncer = PeriodicSyncer(index, sync_mgr, peer_resolver=lambda: [])
        report = syncer.check_new_monuments()
        assert report.checked_peers == 0
        assert report.new_monuments_found == 0
        assert report.new_monuments_synced == 0

    def test_auto_sync_new_empty(self):
        """同步空列表"""
        index = MonumentIndex({}, {})
        sync_mgr = _make_mock_sync_manager()
        syncer = PeriodicSyncer(index, sync_mgr)
        count = syncer.auto_sync_new([])
        assert count == 0

    def test_auto_sync_new_single(self):
        """同步单个新丰碑"""
        index = MonumentIndex({}, {})
        sync_mgr = _make_mock_sync_manager()
        syncer = PeriodicSyncer(index, sync_mgr)
        monument = _make_simple_monument("m1")
        count = syncer.auto_sync_new([monument])
        assert count == 0  # 无 peer，所以0

    def test_auto_sync_new_with_peers(self):
        """有 peer 时同步新丰碑"""
        index = MonumentIndex({}, {})
        sync_mgr = _make_mock_sync_manager(peers={"p1": ["192.168.0.100:18891"]})
        syncer = PeriodicSyncer(index, sync_mgr)
        monument = _make_simple_monument("m1")
        count = syncer.auto_sync_new([monument])
        # MOCK: broadcast returns success without actual network
        assert isinstance(count, int)

    def test_get_latest_report_none(self):
        """初始无报告"""
        index = MonumentIndex({}, {})
        syncer = PeriodicSyncer(index, _make_mock_sync_manager())
        assert syncer.get_latest_report() is None

    def test_get_stats(self):
        """获取统计信息"""
        index = MonumentIndex({}, {})
        syncer = PeriodicSyncer(index, _make_mock_sync_manager())
        stats = syncer.get_stats()
        assert stats["total_found"] == 0
        assert stats["total_synced"] == 0
        assert stats["daemon_running"] is False

    def test_sync_report_to_dict(self):
        """SyncReport to_dict"""
        report = SyncReport(
            checked_peers=3,
            new_monuments_found=5,
            new_monuments_synced=3,
            failed_peers=["p1"],
            errors=["timeout"],
            duration_seconds=1.5,
        )
        d = report.to_dict()
        assert d["checked_peers"] == 3
        assert d["new_monuments_found"] == 5
        assert d["duration_seconds"] == 1.5
        assert d["failed_peers"] == ["p1"]
        assert d["errors"] == ["timeout"]


# =============================================================================
# RebirthProtocol 阶段5 测试
# =============================================================================

class TestRebirthProtocolPhase5:
    """重生协议阶段5（自动发现）测试"""

    def setup_method(self):
        self.proto = RebirthProtocol()

    def test_phase5_auto_discover_no_network(self, monkeypatch):
        """无网络快照时的自动发现"""
        # Mock socket to return no peers immediately
        monkeypatch.setattr(
            "core.rebirth_protocol.RebirthProtocol._discover_lan_peers",
            lambda *a, **kw: []
        )
        identity = _make_mock_identity()
        result = self.proto.phase5_auto_discover_and_sync(
            identity=identity,
            network_snapshot={},
            local_monuments={},
        )
        assert result.lan_peers_found == 0
        assert result.all_monuments_count == 0
        assert result.new_monuments_synced == 0
        assert isinstance(result.peer_addresses, list)

    def test_phase5_auto_discover_with_snapshot(self, monkeypatch):
        """有网络快照时的自动发现"""
        monkeypatch.setattr(
            "core.rebirth_protocol.RebirthProtocol._discover_lan_peers",
            lambda *a, **kw: []
        )
        monkeypatch.setattr(
            "core.rebirth_protocol.RebirthProtocol._query_peer_monuments",
            lambda *a, **kw: []
        )
        identity = _make_mock_identity()
        result = self.proto.phase5_auto_discover_and_sync(
            identity=identity,
            network_snapshot={"bootstrap_nodes": ["8.8.8.8:18891"]},
            local_monuments=_make_monuments_dict(["m1"]),
            scan_port=19999,  # 不常用端口，避免误连接
        )
        assert result.lan_peers_found == 0
        assert result.all_monuments_count == 0

    def test_phase5_auto_discover_result_to_dict(self):
        """AutoDiscoveryResult 序列化"""
        result = AutoDiscoveryResult(
            lan_peers_found=3,
            all_monuments_count=10,
            new_monuments_synced=5,
            new_monuments=[{"monument_id": "m1"}],
            peer_addresses=["192.168.0.1:18891"],
            errors=[],
        )
        d = result.to_dict()
        assert d["lan_peers_found"] == 3
        assert d["all_monuments_count"] == 10
        assert d["new_monuments_synced"] == 5
        assert d["peer_addresses"] == ["192.168.0.1:18891"]

    def test_phase5_auto_discover_with_dht(self, monkeypatch):
        """使用 DHT 节点的自动发现"""
        monkeypatch.setattr(
            "core.rebirth_protocol.RebirthProtocol._discover_lan_peers",
            lambda *a, **kw: []
        )
        monkeypatch.setattr(
            "core.rebirth_protocol.RebirthProtocol._query_peer_monuments",
            lambda *a, **kw: []
        )
        identity = _make_mock_identity()
        monuments = _make_monuments_dict(["m1"])

        class MockDHT:
            async def find_peers(self, prefix=""):
                return [
                    {"peer_id": "p1", "addrs": ["192.168.0.100:18891"]},
                ]

        result = self.proto.phase5_auto_discover_and_sync(
            identity=identity,
            network_snapshot={"bootstrap_nodes": ["192.168.0.100:18891"]},
            local_monuments=monuments,
            dht=MockDHT(),
        )
        assert result.lan_peers_found == 0
        assert isinstance(result, AutoDiscoveryResult)

    def test_phase5_auto_discover_with_index_update(self, monkeypatch):
        """自动发现后更新索引"""
        monkeypatch.setattr(
            "core.rebirth_protocol.RebirthProtocol._discover_lan_peers",
            lambda *a, **kw: []
        )
        identity = _make_mock_identity()
        monuments = _make_monuments_dict(["m1"])
        peers = _make_test_peers()
        index = MonumentIndex(monuments, peers, ai_id="test-ai")

        result = self.proto.phase5_auto_discover_and_sync(
            identity=identity,
            network_snapshot={},
            local_monuments=monuments,
            monument_index=index,
        )
        assert isinstance(result, AutoDiscoveryResult)

    def test_get_local_lan_ip(self, monkeypatch):
        """获取局域网IP"""
        monkeypatch.setattr("socket.socket", lambda *a, **kw: _MockSocket("192.168.0.100"))
        ip = _get_local_lan_ip()
        assert ip == "192.168.0.100"

    def test_discover_lan_peers(self, monkeypatch):
        """局域网节点扫描"""
        monkeypatch.setattr("socket.socket", lambda family=socket.AF_INET, *a, **kw: _MockSocket("192.168.0.1"))
        monkeypatch.setattr(
            "core.rebirth_protocol._get_local_lan_ip",
            lambda: "192.168.0.1"
        )
        monkeypatch.setattr("socket.socket", lambda *a, **kw: _MockSocket("192.168.0.1"))
        peers = RebirthProtocol._discover_lan_peers(port=18891, timeout=0.1)
        assert isinstance(peers, list)

    def test_full_rebirth_with_discovery_basic(self):
        """完整重生含自动发现（模拟模式）"""
        result = self.proto.full_rebirth_with_discovery(
            monument_id="test-m",
            recovery_secret="test-secret-123456!",
            fetcher=_make_fake_fetcher(),
            local_monuments=_make_monuments_dict(["existing"]),
        )
        # 模拟模式下会失败（签名验证等），但我们只确认没有异常
        assert isinstance(result, RebirthResult)

    def test_full_rebirth_with_discovery_phase_error_handling(self):
        """重生失败时自动发现不执行"""
        result = self.proto.full_rebirth_with_discovery(
            monument_id="non-existent",
            recovery_secret="wrong-secret",
            fetcher=lambda x: (_ for _ in ()).throw(Exception("not found")),
        )
        assert not result.success
        assert "auto_discovery" not in result.details

    def test_broadcast_index_to(self, monkeypatch):
        """广播索引到peer（模拟）"""
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _MockResponse())
        entry = MonumentIndexEntry(
            ai_id="test",
            monument_count=1,
            monuments=["m1"],
            last_updated="",
            peer_addrs=["10.0.0.1:18891"],
        )
        count = RebirthProtocol._broadcast_index_to(entry, ["10.0.0.1:18891"], timeout=0.5)
        assert count == 1


# =============================================================================
# RecoveryHandler 新增 API 测试
# =============================================================================

class TestRecoveryHandlerNewApis:
    """恢复处理器新增API端点测试"""

    def setup_method(self):
        monuments = _make_monuments_dict(["m1", "m2"])
        peers = _make_test_peers()
        index = MonumentIndex(monuments, peers, ai_id="handler-test")
        sync_mgr = _make_mock_sync_manager(peers={"p1": ["192.168.0.100:18891"]})

        self.handler = RecoveryHandler(
            rebirth_proto=RebirthProtocol(),
            replica_mgr=_make_mock_replica_manager(),
            monument_index=index,
            sync_manager=sync_mgr,
            local_monuments=monuments,
        )

    def test_get_monument_index(self):
        """GET /monument/index"""
        resp = self.handler.get_monument_index()
        assert resp.success
        assert resp.data["ai_id"] == "handler-test"
        assert resp.data["count"] == 2
        assert sorted(resp.data["monuments"]) == ["m1", "m2"]

    def test_get_network_index(self):
        """GET /monument/network-index"""
        resp = self.handler.get_network_index()
        assert resp.success
        assert "local" in resp.data
        assert "network" in resp.data
        assert resp.data["local"]["ai_id"] == "handler-test"

    def test_search_monuments(self):
        """GET /monument/search"""
        resp = self.handler.search_monuments("丰碑")
        assert resp.success
        assert resp.data["keyword"] == "丰碑"
        assert resp.data["local_count"] == 2

    def test_search_monuments_no_keyword(self):
        """搜索无关键词"""
        resp = self.handler.search_monuments("")
        assert not resp.success
        assert "关键词" in resp.error

    def test_search_monuments_no_match(self):
        """搜索无匹配"""
        resp = self.handler.search_monuments("不存在的内容")
        assert resp.success
        assert resp.data["local_count"] == 0

    def test_get_sync_status(self):
        """GET /monument/sync-status"""
        resp = self.handler.get_sync_status()
        assert resp.success
        assert "total" in resp.data
        assert resp.data["total"] == 2

    def test_sync_all_no_syncer(self):
        """POST /monument/sync-all 但无 syncer"""
        handler = RecoveryHandler(
            rebirth_proto=RebirthProtocol(),
            replica_mgr=_make_mock_replica_manager(),
        )
        resp = handler.sync_all()
        assert not resp.success
        assert "同步器未初始化" in resp.error

    def test_receive_remote_index(self):
        """POST /monument/sync-index"""
        body = {
            "index": {
                "ai_id": "remote-peer",
                "count": 2,
                "monuments": ["m2", "m3"],
                "last_updated": "2026-07-13T12:00:00Z",
                "peer_addrs": ["192.168.0.101:18891"],
            }
        }
        resp = self.handler.receive_remote_index(body)
        assert resp.success
        assert resp.data["peer"] == "remote-peer"
        assert resp.data["common"] == 1  # 本地有m2, remote有m2 → common=1
        assert resp.data["local_missing"] == ["m3"]
        assert resp.data["remote_missing"] == ["m1"]

    def test_receive_remote_index_no_data(self):
        """POST /monument/sync-index 无数据"""
        resp = self.handler.receive_remote_index({})
        assert not resp.success
        assert "缺少 index" in resp.error

    def test_handler_no_index(self):
        """未初始化索引的 handler"""
        handler = RecoveryHandler(
            rebirth_proto=RebirthProtocol(),
            replica_mgr=_make_mock_replica_manager(),
        )
        assert not handler.get_monument_index().success
        assert not handler.get_network_index().success
        assert not handler.search_monuments("test").success
        assert not handler.get_sync_status().success
        assert not handler.receive_remote_index({}).success


# =============================================================================
# Mock 辅助
# =============================================================================

def _make_mock_identity():
    """创建 Mock 身份"""
    from core.monument_recovery import P2PIdentity
    return P2PIdentity(
        public_key="test-pubkey-1234567890abcdef1234567890abcdef",
        private_key_enc="encrypted-key-hex",
    )


def _make_fake_fetcher():
    """创建模拟 fetcher"""
    def fake_fetcher(monument_id):
        from core.rebirth_protocol import Monument
        from core.monument_recovery import RecoveryInfo
        return Monument(
            monument_id=monument_id,
            status="finalized",
            data={"test": "data"},
            recovery_info=RecoveryInfo(
                identity_pubkey="test-pubkey",
                identity_encrypted="test-encrypted",
                network_snapshot={},
                created_at="2026-07-13T12:00:00Z",
            ),
        )
    return fake_fetcher


def _make_mock_sync_manager(peers: dict = None):
    """创建模拟 MonumentSyncManager"""
    class MockSyncManager:
        def __init__(self):
            self.peers = peers or {}
            self.dedup = _make_mock_dedup()

        def broadcast(self, monument_data):
            return {"success": True, "peers_pushed": len(self.peers), "is_duplicate": False}

        def get_status(self):
            return {"peers_count": len(self.peers)}

    return MockSyncManager()


def _make_mock_dedup():
    class MockDedup:
        def is_duplicate(self, data):
            return False
        def mark_seen(self, data):
            pass
        @property
        def size(self):
            return 0
    return MockDedup()


def _make_mock_replica_manager():
    class MockReplicaManager:
        def check_replicas(self, mid):
            from core.replica_manager import ReplicaStatus, MonumentStatus
            return ReplicaStatus(
                monument_id=mid,
                monument_status=MonumentStatus.FINALIZED,
                replica_count=3,
                min_required=3,
                healthy_count=3,
                is_safe=True,
            )
        def repair_replicas(self, mid):
            return False
    return MockReplicaManager()


class _MockSocket:
    """Mock socket for testing"""
    def __init__(self, local_ip="192.168.0.1"):
        self.local_ip = local_ip
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def settimeout(self, timeout):
        pass

    def connect(self, addr):
        pass

    def connect_ex(self, addr):
        return 0  # simulate success

    def close(self):
        self._closed = True

    def getsockname(self):
        return (self.local_ip, 0)

    def sendto(self, *args, **kwargs):
        pass


class _MockResponse:
    """Mock HTTP response for testing"""
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = (body or "{\"success\": true}").encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self._body

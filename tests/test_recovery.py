"""
丰碑网络容灾恢复 - 单元测试

测试覆盖:
    - identity_backup: 加密/解密/错误密钥/格式校验
    - monument_recovery: 创建/序列化/解密身份
    - replica_manager: 副本存储/心跳/检查/修复
    - rebirth_protocol: 5阶段流程/完整重生/错误路径
    - recovery_routes: API端点/恢复/副本管理
"""

import os
import sys
import time
import base64
import json

# 确保能找到code包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.identity_backup import (
    IdentityBackup,
    IdentityBackupError,
    DecryptionError,
)
from core.monument_recovery import (
    RecoveryInfo,
    P2PIdentity,
    DecryptionError as RecoveryDecryptionError,
)
from core.replica_manager import (
    ReplicaManager,
    ReplicaManagerError,
    ReplicaHealth,
    MonumentStatus,
)
from core.rebirth_protocol import (
    RebirthProtocol,
    Monument,
    PhaseError,
    RebirthResult,
)
from api.recovery_routes import (
    RecoveryHandler,
    ApiResponse,
)


# =============================================================================
# 辅助函数
# =============================================================================

def _make_private_key(size: int = 32) -> bytes:
    """生成测试用私钥"""
    return bytes(range(size))


def _make_network_snapshot() -> dict:
    return {
        "bootstrap_nodes": ["n1:8080", "n2:8080"],
        "known_nodes": {"n3": "zone-a", "n4": "zone-b"},
    }


# =============================================================================
# identity_backup 测试
# =============================================================================

class TestIdentityBackup:
    """身份密钥加密存储测试"""

    def test_encrypt_decrypt_roundtrip(self):
        """加密和解密往返正常"""
        sk = _make_private_key()
        secret = "my-strong-secret-1234!"
        encrypted = IdentityBackup.encrypt_private_key(sk, secret)
        assert isinstance(encrypted, str)
        assert len(encrypted) > 0

        decrypted = IdentityBackup.decrypt_private_key(encrypted, secret)
        assert decrypted == sk

    def test_encrypt_different_outputs(self):
        """每次加密输出不同（随机salt和nonce）"""
        sk = _make_private_key()
        secret = "test-secret-1234567!"
        e1 = IdentityBackup.encrypt_private_key(sk, secret)
        e2 = IdentityBackup.encrypt_private_key(sk, secret)
        assert e1 != e2

    def test_decrypt_wrong_secret(self):
        """错误密钥解密失败"""
        sk = _make_private_key()
        secret = "correct-secret-1234!"
        encrypted = IdentityBackup.encrypt_private_key(sk, secret)
        with pytest.raises(DecryptionError):
            IdentityBackup.decrypt_private_key(encrypted, "wrong-secret-123456!")

    def test_decrypt_corrupted_data(self):
        """损坏的密文解密失败"""
        with pytest.raises(DecryptionError):
            IdentityBackup.decrypt_private_key("!!!invalid-base64!!!", "test-secret-1234567!")

    def test_short_secret_raises_error(self):
        """短密码抛出异常"""
        sk = _make_private_key()
        with pytest.raises(IdentityBackupError):
            IdentityBackup.encrypt_private_key(sk, "short")

    def test_decrypt_empty_data(self):
        """空密文解密失败"""
        with pytest.raises(DecryptionError):
            IdentityBackup.decrypt_private_key("", "test-secret-1234567!")

    def test_multiple_keys_different_secrets(self):
        """不同密钥用不同密码加密"""
        sk1 = _make_private_key()
        sk2 = bytes(range(32, 64))
        secret1 = "secret-one-1234567!"
        secret2 = "secret-two-1234567!"

        e1 = IdentityBackup.encrypt_private_key(sk1, secret1)
        e2 = IdentityBackup.encrypt_private_key(sk2, secret2)

        assert IdentityBackup.decrypt_private_key(e1, secret1) == sk1
        assert IdentityBackup.decrypt_private_key(e2, secret2) == sk2
        with pytest.raises(DecryptionError):
            IdentityBackup.decrypt_private_key(e1, secret2)


# =============================================================================
# monument_recovery 测试
# =============================================================================

class TestMonumentRecovery:
    """丰碑恢复信息测试"""

    def setup_method(self):
        self.sk = _make_private_key()
        self.pubkey = "test-public-key-base64"
        self.secret = "my-strong-secret-123!"
        self.network = _make_network_snapshot()

    def test_create_recovery_info(self):
        """创建恢复信息"""
        info = RecoveryInfo.create(self.pubkey, self.sk, self.secret, self.network)
        assert info.identity_pubkey == self.pubkey
        assert len(info.identity_encrypted) > 0
        assert info.network_snapshot == self.network
        assert info.recovery_version == 1

    def test_create_with_timestamp(self):
        """指定时间戳创建"""
        info = RecoveryInfo.create(
            self.pubkey, self.sk, self.secret, self.network,
            created_at="2026-01-01T00:00:00+00:00",
        )
        assert info.created_at == "2026-01-01T00:00:00+00:00"

    def test_decrypt_identity(self):
        """解密身份"""
        info = RecoveryInfo.create(self.pubkey, self.sk, self.secret, self.network)
        identity = info.decrypt_identity(self.secret)
        assert isinstance(identity, P2PIdentity)
        assert identity.public_key == self.pubkey
        # private_key_enc 应该是hex格式的私钥
        assert len(identity.private_key_enc) == self.sk.hex().__len__()

    def test_decrypt_wrong_secret(self):
        """错误密钥解密"""
        info = RecoveryInfo.create(self.pubkey, self.sk, self.secret, self.network)
        with pytest.raises(RecoveryDecryptionError):
            info.decrypt_identity("wrong-secret-1234567!")

    def test_to_dict_and_from_dict(self):
        """序列化和反序列化"""
        info = RecoveryInfo.create(self.pubkey, self.sk, self.secret, self.network)
        d = info.to_dict()
        assert isinstance(d, dict)
        assert d["identity_pubkey"] == self.pubkey
        assert d["identity_encrypted"] == info.identity_encrypted

        info2 = RecoveryInfo.from_dict(d)
        assert info2.identity_pubkey == info.identity_pubkey
        assert info2.identity_encrypted == info.identity_encrypted
        assert info2.network_snapshot == info.network_snapshot
        assert info2.created_at == info.created_at

    def test_json_serializable(self):
        """可以序列化为JSON"""
        info = RecoveryInfo.create(self.pubkey, self.sk, self.secret, self.network)
        json_str = json.dumps(info.to_dict())
        parsed = json.loads(json_str)
        assert parsed["identity_pubkey"] == self.pubkey
        assert parsed["identity_encrypted"] == info.identity_encrypted

    def test_from_dict_minimal(self):
        """反序列化最小数据"""
        info = RecoveryInfo.from_dict({
            "identity_pubkey": "pk",
            "identity_encrypted": "enc",
        })
        assert info.identity_pubkey == "pk"
        assert info.identity_encrypted == "enc"
        assert info.network_snapshot == {}
        assert info.recovery_version == 1


# =============================================================================
# replica_manager 测试
# =============================================================================

class TestReplicaManager:
    """多副本管理测试"""

    def setup_method(self):
        self.monuments = {
            "m-final": {"status": "finalized", "data": b"final"},
            "m-pending": {"status": "pending", "data": b"pending"},
        }
        self.nodes = {
            "n1": {"network_zone": "zone-a", "alive": True},
            "n2": {"network_zone": "zone-b", "alive": True},
            "n3": {"network_zone": "zone-c", "alive": True},
            "n4": {"network_zone": "zone-a", "alive": False},
            "n5": {"network_zone": "zone-b", "alive": True},
        }
        self.mgr = ReplicaManager(self.monuments, self.nodes)

    def test_store_replica(self):
        """存储副本"""
        assert self.mgr.store_replica("m-final", "n1") is True

    def test_store_replica_unknown_node(self):
        """未知节点存储失败"""
        with pytest.raises(ReplicaManagerError):
            self.mgr.store_replica("m-final", "unknown-node")

    def test_store_same_replica_twice(self):
        """重复存储同一副本（覆盖式）"""
        self.mgr.store_replica("m-final", "n1")
        self.mgr.store_replica("m-final", "n1")  # 应该成功（更新）

    def test_heartbeat(self):
        """心跳更新"""
        self.mgr.store_replica("m-final", "n1")
        self.mgr.heartbeat("m-final", "n1")  # 不应抛出异常

    def test_heartbeat_unknown_replica(self):
        """未知副本的心跳失败"""
        self.mgr.store_replica("m-final", "n1")
        with pytest.raises(ReplicaManagerError):
            self.mgr.heartbeat("m-final", "n2")  # n2没有副本

    def test_check_replicas_healthy(self):
        """健康副本检查"""
        self.mgr.store_replica("m-final", "n1")
        self.mgr.store_replica("m-final", "n2")
        self.mgr.store_replica("m-final", "n3")
        status = self.mgr.check_replicas("m-final")
        assert status.replica_count == 3
        assert status.healthy_count == 3
        assert status.is_safe is True

    def test_check_replicas_insufficient(self):
        """副本不足"""
        status = self.mgr.check_replicas("m-pending")
        assert status.replica_count == 0
        assert status.is_safe is False

    def test_check_replicas_finalized_requires_three(self):
        """finalized丰碑需要至少3副本"""
        self.mgr.store_replica("m-final", "n1")
        self.mgr.store_replica("m-final", "n2")
        status = self.mgr.check_replicas("m-final")
        assert status.replica_count == 2
        assert status.is_safe is False
        assert status.min_required == 3

    def test_check_replicas_unknown_monument(self):
        """不存在的丰碑检查失败"""
        with pytest.raises(ReplicaManagerError):
            self.mgr.check_replicas("unknown")

    def test_distribute_replicas(self):
        """自动分布副本到不同zone"""
        self.mgr.store_replica("m-final", "n1")  # zone-a已有1个
        count = self.mgr.distribute_replicas("m-final", min_replicas=3)
        assert count >= 2
        status = self.mgr.check_replicas("m-final")
        assert status.replica_count >= 3

    def test_repair_replicas(self):
        """副本修复"""
        self.mgr.store_replica("m-final", "n1")
        self.mgr.store_replica("m-final", "n2")
        # 只有2个副本，需要修复到3个
        repaired = self.mgr.repair_replicas("m-final")
        assert repaired is True
        status = self.mgr.check_replicas("m-final")
        assert status.is_safe is True
        assert status.healthy_count >= 3

    def test_repair_already_safe(self):
        """已达安全水平的副本无需修复"""
        self.mgr.store_replica("m-final", "n1")
        self.mgr.store_replica("m-final", "n2")
        self.mgr.store_replica("m-final", "n3")
        repaired = self.mgr.repair_replicas("m-final")
        assert repaired is False

    def test_monument_status_enum(self):
        """丰碑状态枚举"""
        assert MonumentStatus("pending") == MonumentStatus.PENDING
        assert MonumentStatus("finalized") == MonumentStatus.FINALIZED

    def test_replica_health_enum(self):
        """副本健康枚举"""
        assert ReplicaHealth("healthy") == ReplicaHealth.HEALTHY
        assert ReplicaHealth("stale") == ReplicaHealth.STALE
        assert ReplicaHealth("lost") == ReplicaHealth.LOST


# =============================================================================
# rebirth_protocol 测试
# =============================================================================

class TestRebirthProtocol:
    """重生协议测试"""

    def setup_method(self):
        self.sk = _make_private_key()
        self.secret = "my-strong-secret-123!"
        self.network = _make_network_snapshot()
        self.recovery = RecoveryInfo.create(
            "test-pubkey", self.sk, self.secret, self.network
        )
        self.monument = Monument(
            monument_id="m1",
            status="finalized",
            data={"version": 1},
            signature="valid-sig",
            recovery_info=self.recovery,
            created_at="2026-07-13T00:00:00Z",
        )
        self.proto = RebirthProtocol()

    def _fetcher(self, monument_id: str) -> Monument:
        return self.monument

    def _announcer(self, pubkey: str, monument_id: str) -> bool:
        return True

    def _syncer(self, monument: Monument) -> bool:
        return True

    def test_phase1_fetch(self):
        """阶段1: 获取丰碑"""
        monument = self.proto.phase1_fetch_monument(
            "m1", self._fetcher, verify_signature=False
        )
        assert monument.monument_id == "m1"
        assert monument.status == "finalized"

    def test_phase2_decrypt(self):
        """阶段2: 解密身份"""
        identity = self.proto.phase2_decrypt_identity(
            self.monument, self.secret
        )
        assert identity.public_key == "test-pubkey"
        assert len(identity.private_key_enc) > 0

    def test_phase2_wrong_secret(self):
        """阶段2: 错误密钥"""
        with pytest.raises(PhaseError) as exc:
            self.proto.phase2_decrypt_identity(self.monument, "wrong-secret-1234567!")
        assert "解密身份失败" in str(exc.value)

    def test_phase2_no_recovery_info(self):
        """阶段2: 缺少recovery_info"""
        bad_monument = Monument(
            monument_id="m2", status="finalized", data={}
        )
        with pytest.raises(PhaseError) as exc:
            self.proto.phase2_decrypt_identity(bad_monument, self.secret)
        assert "缺少recovery_info" in str(exc.value)

    def test_phase3_connect(self):
        """阶段3: 连接网络"""
        identity = self.proto.phase2_decrypt_identity(self.monument, self.secret)
        conn = self.proto.phase3_connect_network(identity, self.monument)
        assert conn["connected"] is True
        assert len(conn["bootstrap_nodes"]) == 2
        assert conn["identity"] == "test-pubkey"

    def test_phase4_announce(self):
        """阶段4: 宣告重生"""
        conn = {"identity": "test-pubkey", "bootstrap_nodes": ["n1:8080"], "connected": True}
        result = self.proto.phase4_announce_rebirth(conn, "m1", self._announcer)
        assert result is True

    def test_phase5_sync(self):
        """阶段5: 同步数据"""
        result = self.proto.phase5_sync_data(self.monument, self._syncer)
        assert result is True

    def test_full_rebirth(self):
        """完整重生流程"""
        result = self.proto.full_rebirth(
            monument_id="m1",
            recovery_secret=self.secret,
            fetcher=self._fetcher,
            announcer=self._announcer,
            syncer=self._syncer,
        )
        assert result.success is True
        assert result.phases_completed == 5
        assert result.identity is not None
        assert result.identity.public_key == "test-pubkey"

    def test_full_rebirth_wrong_secret(self):
        """完整重生流程-错误密钥"""
        result = self.proto.full_rebirth(
            monument_id="m1",
            recovery_secret="wrong-secret-1234567!",
            fetcher=self._fetcher,
            announcer=self._announcer,
            syncer=self._syncer,
        )
        assert result.success is False
        assert result.phases_completed == 1  # 阶段1完成，阶段2失败

    def test_full_rebirth_no_monument(self):
        """完整重生流程-丰碑不存在"""
        def bad_fetcher(mid):
            raise FileNotFoundError(f"丰碑不存在: {mid}")

        result = self.proto.full_rebirth(
            monument_id="ghost",
            recovery_secret=self.secret,
            fetcher=bad_fetcher,
        )
        assert result.success is False
        assert "获取丰碑失败" in (result.error or "")

    def test_phase_callback(self):
        """阶段回调触发"""
        phases = []
        self.proto.on_phase(lambda p, a, d: phases.append((p, a)))

        self.proto.full_rebirth(
            monument_id="m1",
            recovery_secret=self.secret,
            fetcher=self._fetcher,
        )

        # 应该有10次回调（5阶段 * 2动作）
        assert len(phases) >= 8
        # 检查阶段顺序
        phase_nums = [p for p, a in phases]
        assert 1 in phase_nums
        assert 2 in phase_nums
        assert 3 in phase_nums


# =============================================================================
# recovery_routes 测试
# =============================================================================

class TestRecoveryRoutes:
    """API端点测试"""

    def setup_method(self):
        self.sk = _make_private_key()
        self.proto = RebirthProtocol()
        self.monuments = {"m1": {"status": "finalized", "data": b"test"}}
        self.nodes = {
            "n1": {"network_zone": "zone-a", "alive": True},
            "n2": {"network_zone": "zone-b", "alive": True},
            "n3": {"network_zone": "zone-c", "alive": True},
        }
        self.rmgr = ReplicaManager(self.monuments, self.nodes)
        self.handler = RecoveryHandler(self.proto, self.rmgr)

    def test_create_recovery(self):
        """POST /recovery/create"""
        r = self.handler.create_recovery(
            monument_id="m1",
            identity_pubkey="test-pubkey",
            private_key_bytes=self.sk,
            recovery_secret="my-strong-secret-123!",
            network_snapshot=_make_network_snapshot(),
        )
        assert r.success is True
        assert r.data["monument_id"] == "m1"
        assert r.data["identity_pubkey"] == "test-pubkey"

    def test_get_recovery_status(self):
        """GET /recovery/status/:id"""
        # 先创建
        self.handler.create_recovery(
            "m1", "test-pubkey", self.sk, "my-strong-secret-123!", {}
        )
        r = self.handler.get_recovery_status("rec-m1")
        assert r.success is True
        assert r.data["monument_id"] == "m1"

    def test_get_recovery_status_not_found(self):
        """GET /recovery/status/:id - 不存在"""
        r = self.handler.get_recovery_status("rec-nonexistent")
        assert r.success is False
        assert "不存在" in (r.error or "")

    def test_check_replicas(self):
        """GET /replica/check/:id"""
        self.rmgr.store_replica("m1", "n1")
        self.rmgr.store_replica("m1", "n2")
        self.rmgr.store_replica("m1", "n3")
        r = self.handler.check_replicas("m1")
        assert r.success is True
        assert r.data["is_safe"] is True
        assert r.data["healthy_count"] == 3

    def test_check_replicas_unsafe(self):
        """GET /replica/check/:id - 副本不足"""
        r = self.handler.check_replicas("m1")
        assert r.success is True
        assert r.data["is_safe"] is False

    def test_repair_replicas(self):
        """POST /replica/repair/:id"""
        # finalized丰碑需要3个副本，只有1个
        self.rmgr.store_replica("m1", "n1")
        r = self.handler.repair_replicas("m1")
        assert r.success is True
        assert r.data["repaired"] is True
        assert r.data["is_safe"] is True

    def test_restore(self):
        """POST /recovery/restore"""
        network = _make_network_snapshot()
        recovery = RecoveryInfo.create("test-pubkey", self.sk, "my-strong-secret-123!", network)
        monument = Monument(
            monument_id="m1", status="finalized", data={}, recovery_info=recovery
        )

        def fetcher(mid):
            return monument

        r = self.handler.restore("m1", "my-strong-secret-123!", fetcher)
        assert r.success is True
        assert r.data["phases_completed"] == 5

    def test_restore_wrong_secret(self):
        """POST /recovery/restore - 错误密钥"""
        network = _make_network_snapshot()
        recovery = RecoveryInfo.create("test-pubkey", self.sk, "my-strong-secret-123!", network)
        monument = Monument(
            monument_id="m1", status="finalized", data={}, recovery_info=recovery
        )

        def fetcher(mid):
            return monument

        r = self.handler.restore("m1", "wrong-secret-1234567!", fetcher)
        assert r.success is False
        assert "解密身份失败" in (r.error or "")

    def test_api_response_format(self):
        """API响应格式"""
        from api.recovery_routes import _ok, _err

        ok_resp = _ok({"key": "value"})
        assert ok_resp.success is True
        assert ok_resp.data == {"key": "value"}
        assert ok_resp.error is None

        err_resp = _err("something went wrong")
        assert err_resp.success is False
        assert err_resp.error == "something went wrong"
        assert err_resp.data is None

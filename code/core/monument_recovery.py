"""
丰碑增强格式 - 恢复信息 (monument_recovery.py)

为丰碑添加RecoveryInfo，支持身份密钥的加密存储和恢复。
RecoveryInfo嵌入丰碑，节点可通过recovery_secret重建身份。

用法:
    info = RecoveryInfo.create(identity, secret, network_snapshot)
    monument.recovery_info = info
    ...
    recovered = info.decrypt_identity(secret)
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .identity_backup import IdentityBackup

# 导出异常类型
from .identity_backup import IdentityBackupError, DecryptionError


@dataclass
class P2PIdentity:
    """P2P网络身份标识"""
    public_key: str       # Ed25519公钥 (base64)
    private_key_enc: str  # 加密后的私钥 (临时占位，但RecoveryInfo不存这个)


@dataclass
class RecoveryInfo:
    """丰碑恢复信息 - 嵌入丰碑用于节点重生"""

    # 身份密钥
    identity_pubkey: str          # Ed25519公钥（明文，base64）
    identity_encrypted: str       # 用recovery_secret加密的私钥 (base64)

    # 网络快照
    network_snapshot: dict        # 引导节点列表、已知节点等

    # 元数据
    created_at: str               # ISO 8601 UTC时间戳
    recovery_version: int = 1     # 恢复格式版本

    def to_dict(self) -> dict:
        """转为字典（JSON序列化用）"""
        return {
            "identity_pubkey": self.identity_pubkey,
            "identity_encrypted": self.identity_encrypted,
            "network_snapshot": self.network_snapshot,
            "created_at": self.created_at,
            "recovery_version": self.recovery_version,
        }

    @staticmethod
    def from_dict(data: dict) -> "RecoveryInfo":
        """从字典重建"""
        return RecoveryInfo(
            identity_pubkey=data["identity_pubkey"],
            identity_encrypted=data["identity_encrypted"],
            network_snapshot=data.get("network_snapshot", {}),
            created_at=data.get("created_at", ""),
            recovery_version=data.get("recovery_version", 1),
        )

    @staticmethod
    def create(
        identity_pubkey: str,
        private_key_bytes: bytes,
        recovery_secret: str,
        network: dict,
        created_at: Optional[str] = None,
    ) -> "RecoveryInfo":
        """
        创建恢复信息

        参数:
            identity_pubkey: Ed25519公钥 (base64)
            private_key_bytes: 原始私钥字节
            recovery_secret: 恢复密码（≥16字符）
            network: 网络快照字典
            created_at: 时间戳（默认当前UTC）
        """
        encrypted = IdentityBackup.encrypt_private_key(
            private_key_bytes, recovery_secret
        )

        if created_at is None:
            created_at = datetime.now(timezone.utc).isoformat()

        return RecoveryInfo(
            identity_pubkey=identity_pubkey,
            identity_encrypted=encrypted,
            network_snapshot=network,
            created_at=created_at,
        )

    def decrypt_identity(self, recovery_secret: str) -> P2PIdentity:
        """
        用recovery_secret解密身份

        返回:
            P2PIdentity对象

        抛出:
            DecryptionError: secret错误或数据损坏
        """
        private_key_bytes = IdentityBackup.decrypt_private_key(
            self.identity_encrypted, recovery_secret
        )
        return P2PIdentity(
            public_key=self.identity_pubkey,
            private_key_enc=private_key_bytes.hex(),
        )

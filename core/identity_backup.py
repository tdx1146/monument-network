"""
身份密钥加密存储 (identity_backup.py)

使用AES-256-GCM加密Ed25519私钥，密钥通过PBKDF2-HMAC-SHA256从secret派生。
支持标准备份格式，包含salt和nonce，用于丰碑恢复场景。

用法:
    encrypted = IdentityBackup.encrypt_private_key(private_key, secret)
    decrypted = IdentityBackup.decrypt_private_key(encrypted, secret)
"""

import base64
import os
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# PBKDF2参数 - 可通过环境变量覆盖（测试时降低迭代次数加快速度）
_PBKDF2_ITERATIONS = int(os.environ.get("PBKDF2_ITERATIONS", "600000"))
_SALT_LENGTH = 16
_KEY_LENGTH = 32  # AES-256


class IdentityBackupError(Exception):
    """身份备份相关错误的基类"""


class DecryptionError(IdentityBackupError):
    """解密失败（密钥错误或数据损坏）"""


class IdentityBackup:
    """身份密钥备份与恢复"""

    @staticmethod
    def encrypt_private_key(private_key: bytes, secret: str) -> str:
        """用secret加密Ed25519私钥，返回Base64编码的密文"""
        if len(secret) < 16:
            raise IdentityBackupError("recovery_secret 长度不能少于16字符")

        # 1. 派生密钥
        salt = os.urandom(_SALT_LENGTH)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=_KEY_LENGTH,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        )
        key = kdf.derive(secret.encode("utf-8"))

        # 2. AES-256-GCM加密
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)  # GCM推荐nonce长度
        ciphertext = aesgcm.encrypt(nonce, private_key, None)

        # 3. 打包: salt(16) + nonce(12) + ciphertext
        payload = salt + nonce + ciphertext
        return base64.b64encode(payload).decode("ascii")

    @staticmethod
    def _derive_key(secret: str, salt: bytes) -> bytes:
        """PBKDF2密钥派生"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=_KEY_LENGTH,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
        )
        return kdf.derive(secret.encode("utf-8"))

    @staticmethod
    def decrypt_private_key(encrypted: str, secret: str) -> bytes:
        """解密Base64编码的密文"""
        try:
            payload = base64.b64decode(encrypted)
        except (ValueError, base64.binascii.Error) as e:
            raise DecryptionError(f"Base64解码失败: {e}")
        if len(payload) < _SALT_LENGTH + 12 + 1:
            raise DecryptionError("密文数据不完整")
        salt, nonce, ct = payload[:_SALT_LENGTH], payload[_SALT_LENGTH:_SALT_LENGTH+12], payload[_SALT_LENGTH+12:]
        key = IdentityBackup._derive_key(secret, salt)
        try:
            return AESGCM(key).decrypt(nonce, ct, None)
        except Exception:
            raise DecryptionError("解密失败: secret错误或数据损坏")

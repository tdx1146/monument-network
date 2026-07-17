"""
P2P 丰碑网络 —— 身份、签名、节点发现、消息传输

模块职责：
  - Ed25519 密钥对管理（身份）
  - 丰碑消息签名/验签
  - DHT 节点发现（Phase 2）
  - HTTP 消息传输（Phase 2）

协议版本：monument-exchange-v1
"""

import json
import hashlib
import base64
import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from config import HASH_ALGORITHM


# ─── 身份系统 ──────────────────────────────────────────────

class P2PIdentity:
    """
    P2P 身份管理 —— Ed25519 密钥对
    
    身份格式：
        peer_id = Base58Encode(public_key)
        所有消息用私钥签名，接收方用公钥验签
    """
    
    def __init__(self, private_key: Optional[bytes] = None):
        """
        初始化身份。
        
        Args:
            private_key: Ed25519 私钥（32字节）。如果为 None，则自动生成。
        """
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            raise ImportError(
                "需要安装 cryptography 库: pip install cryptography"
            )
        
        if private_key:
            self._private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
                private_key
            )
        else:
            self._private_key = ed25519.Ed25519PrivateKey.generate()
        
        self._public_key = self._private_key.public_key()
        self._peer_id = self._encode_peer_id(self._public_key)
    
    @staticmethod
    def _encode_peer_id(public_key) -> str:
        """将公钥编码为 PeerID（Base58）。"""
        # 简化版：用 Base64 编码
        # cryptography 版本兼容：_raw_public_bytes（旧版）vs public_bytes_raw（新版）
        if hasattr(public_key, 'public_bytes_raw'):
            pub_bytes = public_key.public_bytes_raw()
        else:
            pub_bytes = public_key._raw_public_bytes()
        return base64.b64encode(pub_bytes).decode('ascii')
    
    @property
    def peer_id(self) -> str:
        """获取 PeerID。"""
        return self._peer_id
    
    @property
    def public_key_bytes(self) -> bytes:
        """获取公钥字节。"""
        if hasattr(self._public_key, 'public_bytes_raw'):
            return self._public_key.public_bytes_raw()
        return self._public_key._raw_public_bytes()
    
    @property
    def private_key_bytes(self) -> bytes:
        """获取私钥字节（用于持久化）。"""
        if hasattr(self._private_key, 'private_bytes_raw'):
            return self._private_key.private_bytes_raw()
        from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
        return self._private_key.private_bytes(
            encoding=Encoding.Raw,
            format=PrivateFormat.Raw,
            encryption_algorithm=NoEncryption(),
        )
    
    def sign(self, message: bytes) -> bytes:
        """
        对消息签名。
        
        Args:
            message: 原始消息字节
        
        Returns:
            签名字节（64字节）
        """
        return self._private_key.sign(message)
    
    @staticmethod
    def verify(peer_id: str, message: bytes, signature: bytes) -> bool:
        """
        验证签名。
        
        Args:
            peer_id:  发送方 PeerID
            message:  原始消息字节
            signature: 签名字节
        
        Returns:
            bool: 验证是否通过
        """
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
            
            # 解码 PeerID 为公钥
            pub_bytes = base64.b64decode(peer_id.encode('ascii'))
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
            
            public_key.verify(signature, message)
            return True
        except Exception:
            return False


# ─── 消息签名 ──────────────────────────────────────────────

def sign_monument_message(
    monument_data: Dict[str, Any],
    identity: P2PIdentity
) -> Dict[str, Any]:
    """
    对丰碑消息签名。
    
    Args:
        monument_data: 丰碑数据字典
        identity:      P2P 身份
    
    Returns:
        添加了签名的丰碑数据
    """
    # 复制数据，避免修改原对象
    signed_data = monument_data.copy()
    
    # 添加时间戳（如果没有）
    if "timestamp" not in signed_data:
        signed_data["timestamp"] = datetime.now().isoformat()
    
    # 添加发送方 PeerID
    signed_data["from_peer"] = identity.peer_id
    
    # 计算消息哈希
    message_bytes = json.dumps(
        signed_data, sort_keys=True, ensure_ascii=False
    ).encode('utf-8')
    
    # 签名
    signature = identity.sign(message_bytes)
    signed_data["signature"] = base64.b64encode(signature).decode('ascii')
    
    return signed_data


def verify_monument_message(
    signed_data: Dict[str, Any]
) -> Tuple[bool, str]:
    """
    验证丰碑消息签名。
    
    验证流程：
      1. 提取 signature 字段
      2. 重新计算消息哈希（不含 signature）
      3. 用 from_peer 的公钥验证签名
      4. 返回验证结果
    
    注意：此函数不会修改传入的字典。
    
    Args:
        signed_data: 签名后的丰碑数据
    
    Returns:
        (验证结果, 错误消息)
    """
    # 检查必需字段
    if "from_peer" not in signed_data:
        return False, "缺少 from_peer 字段"
    if "signature" not in signed_data:
        return False, "缺少 signature 字段"
    
    # 提取签名（不修改原字典）
    signature_b64 = signed_data.get("signature")
    try:
        signature = base64.b64decode(signature_b64.encode('ascii'))
    except Exception as e:
        return False, f"签名 Base64 解码失败: {e}"
    
    # 重新计算消息哈希（不含 signature 字段）
    data_no_sig = {k: v for k, v in signed_data.items() if k != "signature"}
    message_bytes = json.dumps(
        data_no_sig, sort_keys=True, ensure_ascii=False
    ).encode('utf-8')
    
    # 验证签名
    peer_id = signed_data["from_peer"]
    is_valid = P2PIdentity.verify(peer_id, message_bytes, signature)
    
    if is_valid:
        return True, "签名验证通过"
    else:
        return False, "签名验证失败"


# ─── 节点发现（DHT）──────────────────────────────────

# DHTNode 完整实现在 core/dht_node.py 中
# 此处仅为向后兼容提供别名
from core.dht_node import DHTNode


# ─── HTTP 消息传输 ──────────────────────────────────────────

def create_sync_message(
    monument_data: Dict[str, Any],
    identity: P2PIdentity
) -> str:
    """
    创建同步消息（JSON 字符串）。
    
    Args:
        monument_data: 丰碑数据
        identity:      发送方身份
    
    Returns:
        签名后的 JSON 字符串
    """
    signed = sign_monument_message(monument_data, identity)
    return json.dumps(signed, indent=2, ensure_ascii=False)


def parse_sync_message(
    json_str: str,
    verify: bool = True
) -> Tuple[bool, Dict[str, Any], str]:
    """
    解析同步消息。
    
    Args:
        json_str: JSON 字符串
        verify:   是否验证签名
    
    Returns:
        (验证结果, 丰碑数据, 错误消息)
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return False, {}, f"JSON 解析失败: {e}"
    
    if verify:
        is_valid, error_msg = verify_monument_message(data)
        if not is_valid:
            return False, {}, error_msg
    
    return True, data, "成功"


# ─── 辅助函数 ─────────────────────────────────────────────

def generate_identity_keypair() -> Tuple[bytes, bytes]:
    """
    生成新的密钥对。
    
    Returns:
        (私钥字节, 公钥字节)
    """
    identity = P2PIdentity()
    return identity.private_key_bytes, identity.public_key_bytes


def save_identity_to_file(identity: P2PIdentity, path: str) -> None:
    """
    持久化身份到文件。
    
    Args:
        identity: P2P 身份
        path:     文件路径
    """
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(identity.private_key_bytes)


def load_identity_from_file(path: str) -> Optional[P2PIdentity]:
    """
    从文件加载身份。
    
    Args:
        path: 私钥文件路径
    
    Returns:
        P2PIdentity 实例，文件不存在时返回 None
    """
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        priv_key = f.read()
    return P2PIdentity(private_key=priv_key)


def verify_monument_json(json_str: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    验证签名后的 JSON 字符串。
    
    Args:
        json_str: JSON 字符串（包含 signature 字段）
    
    Returns:
        (是否通过, 解析后的数据字典, 消息)
        如果验证失败，数据字典为 None
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return False, None, f"JSON 解析失败: {e}"
    
    if "signature" not in data:
        return False, data, "缺少 signature 字段"
    
    is_valid, msg = verify_monument_message(data)
    if not is_valid:
        return False, None, msg
    
    return True, data, "签名验证通过"
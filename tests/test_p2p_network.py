#!/usr/bin/env python3
"""
P2P 网络单元测试 —— 签名验证 + 消息完整性

测试内容：
  1. P2P 身份生成（密钥对创建、PeerID 编码）
  2. 签名与验签（sign/verify）
  3. 签名字段完整（sign_monument_message）
  4. 验签正确性（verify_monument_message）
  5. 篡改检测（修改内容后验签失败）
  6. 身份持久化（save/load identity）
  7. JSON 验证函数（verify_monument_json）
  8. 无签名消息拒绝
  9. 多字段消息签名
  10. 消息完整性与 cross_instance 兼容

运行方式：
    cd /vol2/1000/AI专用/丰碑网络/code && python3 tests/test_p2p_network.py
"""

import sys
import os
import json
import tempfile

# 确保 code/ 在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.p2p_network import (
    P2PIdentity,
    sign_monument_message,
    verify_monument_message,
    generate_identity_keypair,
    save_identity_to_file,
    load_identity_from_file,
    verify_monument_json,
    create_sync_message,
    parse_sync_message,
)

errors = []


def check(name: str, cond: bool, detail: str = ""):
    if not cond:
        errors.append(f"FAIL: {name} — {detail}")
        print(f"  ✗ {name}")
    else:
        print(f"  ✓ {name}")


# ─── 1. 身份生成 ──────────────────────────────────────

print("\n=== 1. P2P 身份生成 ===")

identity_a = P2PIdentity()
check("P2PIdentity 创建", identity_a is not None)
check("peer_id 非空", len(identity_a.peer_id) > 0)
check("public_key_bytes 长度=32", len(identity_a.public_key_bytes) == 32)
check("private_key_bytes 长度=32", len(identity_a.private_key_bytes) == 32)

# 验证同一个实例的 peer_id 和公钥对应
pub_bytes_a = identity_a.public_key_bytes
import base64
expected_peer_id = base64.b64encode(pub_bytes_a).decode('ascii')
check("peer_id 是公钥的 Base64 编码", identity_a.peer_id == expected_peer_id)

# 两个不同的身份应该有不同的 peer_id
identity_b = P2PIdentity()
check("不同身份不同 peer_id", identity_a.peer_id != identity_b.peer_id)

# ─── 2. 签名与验签 ────────────────────────────────────

print("\n=== 2. Ed25519 签名与验签 ===")

message = b"Hello, Monument Network!"
signature = identity_a.sign(message)
check("签名长度=64", len(signature) == 64)

# 正确验证
is_valid = P2PIdentity.verify(identity_a.peer_id, message, signature)
check("正确签名验证通过", is_valid is True)

# 错误签名验证（用 identity_b 的签名尝试用 identity_a 验证）
wrong_sig = identity_b.sign(message)
is_valid = P2PIdentity.verify(identity_a.peer_id, message, wrong_sig)
check("错误签名验证不通过", is_valid is False)

# 篡改消息
tampered = b"Hello, Modified Message!"
is_valid = P2PIdentity.verify(identity_a.peer_id, tampered, signature)
check("篡改消息验证不通过", is_valid is False)

# ─── 3. sign_monument_message ─────────────────────────

print("\n=== 3. 丰碑消息签名 ===")

monument_data = {
    "protocol": "monument-exchange-v1",
    "ai_id": "test-ai",
    "monuments": [
        {"id": 0, "content": "test monument", "created_at": "2026-07-12T19:00:00Z"}
    ],
}

signed = sign_monument_message(monument_data, identity_a)
check("签名后包含 from_peer", "from_peer" in signed)
check("from_peer 正确", signed["from_peer"] == identity_a.peer_id)
check("签名后包含 signature", "signature" in signed)
check("签名后包含 timestamp", "timestamp" in signed)
check("signature 是字符串", isinstance(signed["signature"], str))
check("原始协议字段保留", signed["protocol"] == "monument-exchange-v1")
check("原始 ai_id 保留", signed["ai_id"] == "test-ai")

# signature 应该是一个 base64 字符串
try:
    sig_bytes = base64.b64decode(signed["signature"])
    check("签名可解码", len(sig_bytes) == 64)
except Exception as e:
    check("签名可解码", False, str(e))

# ─── 4. verify_monument_message ───────────────────────

print("\n=== 4. 验签正确性 ===")

# 对已签名消息验签
is_valid, msg = verify_monument_message(signed)
check(f"验签通过 ({msg})", is_valid is True)

# 验证调用后未修改原字典
check("验签后 from_peer 依然存在", "from_peer" in signed)
check("验签后 signature 依然存在", "signature" in signed)

# ─── 5. 篡改检测 ──────────────────────────────────────

print("\n=== 5. 篡改检测 ===")

tampered_signed = signed.copy()
tampered_signed["ai_id"] = "evil-ai"
is_valid, msg = verify_monument_message(tampered_signed)
check(f"篡改内容验签失败 ({msg})", is_valid is False)

# 篡改 from_peer
tampered_peer = signed.copy()
tampered_peer["from_peer"] = identity_b.peer_id
is_valid, msg = verify_monument_message(tampered_peer)
check(f"篡改 from_peer 验签失败 ({msg})", is_valid is False)

# ─── 6. 缺少字段检测 ──────────────────────────────────

print("\n=== 6. 缺少字段检测 ===")

no_peer = {"protocol": "test", "signature": "abc"}
is_valid, msg = verify_monument_message(no_peer)
check("缺少 from_peer 返回错误", is_valid is False and "from_peer" in msg)

no_sig = {"protocol": "test", "from_peer": "abc"}
is_valid, msg = verify_monument_message(no_sig)
check("缺少 signature 返回错误", is_valid is False and "signature" in msg)

# ─── 7. 身份持久化 ────────────────────────────────────

print("\n=== 7. 身份持久化 ===")

with tempfile.NamedTemporaryFile(delete=False, suffix=".key") as f:
    key_path = f.name

try:
    save_identity_to_file(identity_a, key_path)
    check("身份已保存到文件", os.path.exists(key_path))
    
    loaded = load_identity_from_file(key_path)
    check("身份可加载", loaded is not None)
    if loaded:
        check("加载后 peer_id 一致", loaded.peer_id == identity_a.peer_id)
        check("加载后公钥一致", loaded.public_key_bytes == identity_a.public_key_bytes)
        check("加载后私钥一致", loaded.private_key_bytes == identity_a.private_key_bytes)
        
        # 用加载的身份签名并验证
        msg = b"persistence test"
        sig = loaded.sign(msg)
        check("加载身份可签名", len(sig) == 64)
        is_valid = P2PIdentity.verify(loaded.peer_id, msg, sig)
        check("加载身份的签名可验证", is_valid is True)
finally:
    os.unlink(key_path)

# ─── 8. verify_monument_json ─────────────────────────

print("\n=== 8. verify_monument_json ===")

# 验证 JSON 字符串
signed_json = json.dumps(signed, ensure_ascii=False)
is_valid, data, msg = verify_monument_json(signed_json)
check(f"JSON 验证通过 ({msg})", is_valid is True)
if data:
    check("JSON 验证后数据完整", data["ai_id"] == "test-ai")

# 验证无签名的 JSON
no_sig_data = {"protocol": "test", "ai_id": "no-sig"}
no_sig_json = json.dumps(no_sig_data, ensure_ascii=False)
is_valid, data, msg = verify_monument_json(no_sig_json)
check("无签名 JSON 返回 False", is_valid is False)

# 验证篡改的 JSON
tampered_dict = signed.copy()
tampered_dict["monuments"][0]["content"] = "tampered"
tampered_json = json.dumps(tampered_dict, ensure_ascii=False)
is_valid, _, msg = verify_monument_json(tampered_json)
check("篡改 JSON 验证不通过", is_valid is False)

# ─── 9. create_sync_message / parse_sync_message ───────

print("\n=== 9. 同步消息工具函数 ===")

sync_json = create_sync_message(monument_data, identity_a)
check("create_sync_message 返回字符串", isinstance(sync_json, str))
check("create_sync_message 是有效 JSON", json.loads(sync_json))

is_valid, parsed_data, msg = parse_sync_message(sync_json)
check(f"parse_sync_message 验证通过 ({msg})", is_valid is True)
if parsed_data:
    check("解析后 ai_id 正确", parsed_data["ai_id"] == "test-ai")

# 不验证签名
is_valid, parsed_data, msg = parse_sync_message(sync_json, verify=False)
check("parse_sync_message verify=False 通过", is_valid is True)

# 验证无效 JSON
is_valid, _, msg = parse_sync_message("{invalid json", verify=False)
check("无效 JSON 返回错误", is_valid is False and "JSON 解析失败" in msg)

# ─── 10. 空消息验证 ─────────────────────────────────────

print("\n=== 10. 边情况测试 ===")

# 空消息
empty_dict = {}
is_valid, msg = verify_monument_message(empty_dict)
check("空字典验签失败", is_valid is False)

# 只有 from_peer 无 signature
only_peer = {"from_peer": identity_a.peer_id}
is_valid, msg = verify_monument_message(only_peer)
check("无签名验签失败", is_valid is False and "signature" in msg)

# 空签名
empty_sig = {"from_peer": identity_a.peer_id, "signature": ""}
is_valid, msg = verify_monument_message(empty_sig)
check("空签名验签失败", is_valid is False)

# ─── 11. generate_identity_keypair ──────────────────────

print("\n=== 11. generate_identity_keypair ===")

priv, pub = generate_identity_keypair()
check("生成私钥 32 字节", len(priv) == 32)
check("生成公钥 32 字节", len(pub) == 32)

# 用生成的密钥创建身份
identity_c = P2PIdentity(private_key=priv)
check("从字节创建身份成功", identity_c.peer_id is not None)
check("公钥匹配", identity_c.public_key_bytes == pub)


# ─── 12. 复杂消息（多字段排序一致性） ───────────────────

print("\n=== 12. 多字段消息签名 ===")

complex_data = {
    "protocol": "monument-exchange-v1",
    "from": "qingruyan",
    "ai_id": "multi-field-test",
    "timestamp": "2026-07-12T19:00:00Z",
    "monuments": [
        {
            "id": 0,
            "content": "deep insight about life",
            "metadata": {
                "source": "chat",
                "score": 0.95,
                "tags": ["philosophy", "technology"]
            },
            "created_at": "2026-07-12T18:00:00Z"
        }
    ],
    "identity": {
        "born_at": "2026-01-01T00:00:00Z",
        "status": "alive"
    },
    "score_dimensions": {
        "health_score": 0.75,
        "xuanjian_count": 5,
        "goal_tree_aligned": 3,
        "goal_tree_diverged": 1
    }
}

signed_complex = sign_monument_message(complex_data, identity_a)
check("复杂消息签名完整", "signature" in signed_complex)
check("复杂消息 from_peer 正确", signed_complex["from_peer"] == identity_a.peer_id)

# 验证 ID 不变
check("复杂消息 ai_id 不变", signed_complex["ai_id"] == "multi-field-test")
check("复杂消息 monuments 不变", signed_complex["monuments"][0]["id"] == 0)

is_valid, msg = verify_monument_message(signed_complex)
check(f"复杂消息验签通过 ({msg})", is_valid is True)

# 修改复杂消息的任一字段都会失败
import copy
tampered_complex = copy.deepcopy(signed_complex)
tampered_complex["score_dimensions"]["health_score"] = 1.0
is_valid, msg = verify_monument_message(tampered_complex)
check(f"复杂消息篡改验签不通过 ({msg})", is_valid is False)

# 修改嵌套数组
tampered_nested = copy.deepcopy(signed_complex)
tampered_nested["monuments"][0]["metadata"]["tags"].append("evil")
is_valid, msg = verify_monument_message(tampered_nested)
check(f"嵌套字段篡改验签不通过 ({msg})", is_valid is False)


# ─── 汇总 ─────────────────────────────────────────────

print(f"\n{'='*50}")
if errors:
    print(f"  ❌ FAILURES: {len(errors)}")
    for e in errors:
        print(f"     {e}")
    sys.exit(1)
else:
    print("  ✅ ALL P2P TESTS PASSED")
    print(f"{'='*50}")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Envelope - 增强的信封格式，支持多地址和节点信息
=================================================

信封是节点间消息交换的容器，现在扩展支持：
1. 多地址列表（IPv6/IPv4/DNS/中继）
2. 中继节点列表
3. 协议版本协商
4. 网络 ID 标识

用法：
    env = create_envelope(
        monument_data={"title": "新碑文", ...},
        node_addrs=[
            "/ip6/240e:3a1:6437:37b0::1000/tcp/18891",
            "/ip4/192.168.0.149/tcp/18891",
        ]
    )
    
    # 解析信封
    parsed = parse_envelope(envelope_json)
    addrs = parsed["envelope"]["node_addrs"]
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# 协议常量
ENVELOPE_PROTOCOL = "monument-exchange-v1"
ENVELOPE_NETWORK_ID = "monument-v1"
ENVELOPE_MIN_VERSION = "v1.4.0"


def create_envelope(
    monument_data: Dict[str, Any],
    node_addrs: List[str],
    *,
    relay_nodes: Optional[List[Dict]] = None,
    peer_id: Optional[str] = None,
    message_type: str = "monument_sync",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """创建增强信封
    
    Args:
        monument_data: 碑文数据（有 content 字段的 dict）
        node_addrs: 发送方的多地址列表
        relay_nodes: 可选的中继节点列表，格式：
            [{"peer_id": "Qm...", "addrs": ["/ip4/.../tcp/18900"]}]
        peer_id: 发送方节点 ID
        message_type: 消息类型（默认 monument_sync）
        extra: 额外字段
        
    Returns:
        信封 dict，格式：
        {
            "monument": { ... },
            "envelope": {
                "protocol": "monument-exchange-v1",
                "network_id": "monument-v1",
                "node_addrs": ["/ip6/...", ...],
                "relay_nodes": [...],
                "min_version": "v1.4.0",
                "message_id": "...",
                "timestamp": "...",
                "peer_id": "...",
                "message_type": "...",
            }
        }
    """
    envelope = {
        "protocol": ENVELOPE_PROTOCOL,
        "network_id": ENVELOPE_NETWORK_ID,
        "node_addrs": node_addrs,
        "relay_nodes": relay_nodes or [],
        "min_version": ENVELOPE_MIN_VERSION,
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "peer_id": peer_id or f"peer-{uuid.uuid4().hex[:8]}",
        "message_type": message_type,
    }
    
    if extra:
        envelope.update(extra)
    
    return {
        "monument": monument_data,
        "envelope": envelope,
    }


def parse_envelope(envelope_data: Dict[str, Any]) -> Dict[str, Any]:
    """解析信封，验证协议版本
    
    Args:
        envelope_data: 信封 dict（通常来自 JSON 反序列化）
        
    Returns:
        解析后的信封
        
    Raises:
        ValueError: 协议版本不匹配
        KeyError: 缺少必要字段
    """
    if "envelope" not in envelope_data:
        raise ValueError("缺少 envelope 字段")
    
    env = envelope_data["envelope"]
    
    # 验证协议
    if env.get("protocol") != ENVELOPE_PROTOCOL:
        raise ValueError(
            f"协议不匹配: 期望 {ENVELOPE_PROTOCOL}, 实际 {env.get('protocol')}"
        )
    
    # 验证网络 ID
    if env.get("network_id") != ENVELOPE_NETWORK_ID:
        # 非致命，仅警告
        pass
    
    return envelope_data


def create_sync_envelope(
    monument_data: Dict[str, Any],
    node_addrs: List[str],
    relay_nodes: Optional[List[Dict]] = None,
    peer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """创建同步专用信封（monument_sync 类型）"""
    return create_envelope(
        monument_data=monument_data,
        node_addrs=node_addrs,
        relay_nodes=relay_nodes,
        peer_id=peer_id,
        message_type="monument_sync",
    )


def create_discovery_envelope(
    node_addrs: List[str],
    relay_nodes: Optional[List[Dict]] = None,
    peer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """创建节点发现专用信封（discovery 类型）"""
    return create_envelope(
        monument_data={},
        node_addrs=node_addrs,
        relay_nodes=relay_nodes,
        peer_id=peer_id,
        message_type="node_discovery",
    )


def envelope_to_json(envelope: Dict[str, Any], indent: int = 2) -> str:
    """信封序列化为 JSON 字符串"""
    return json.dumps(envelope, indent=indent, ensure_ascii=False)


def envelope_from_json(json_str: str) -> Dict[str, Any]:
    """从 JSON 字符串解析信封"""
    return json.loads(json_str)

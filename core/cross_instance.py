"""
跨实例丰碑交换 —— 接收、导出、验证

模块职责：
  - 接收外部丰碑 JSON 并导入
  - 导出本地丰碑为 JSON
  - 签名验证（Ed25519，Phase 2 启用）
  - 格式转换（姐姐那边 ↔ 轻如烟这边）
"""

import json
import hashlib
from datetime import datetime
from typing import Any, Dict, Optional

from config import HASH_ALGORITHM


# ─── 丰碑交换协议 v1 ───────────────────────────────────────

PROTOCOL_VERSION = "monument-exchange-v1"


# ─── 接收外部丰碑 ───────────────────────────────────────────

def import_monument_json(
    json_str: str,
    repo,  # IndividualRepository
    score_repo,  # ScoreRepository
    verify_signature: bool = False
) -> Dict[str, Any]:
    """
    接收外部丰碑 JSON 并导入到本地数据库。
    
    Args:
        json_str:      JSON 字符串
        repo:          IndividualRepository 实例
        score_repo:    ScoreRepository 实例
        verify_signature: 是否验证签名（Phase 2 启用）
    
    Returns:
        {
            "status": "success" | "error",
            "ai_id": str,
            "monuments_imported": int,
            "score_imported": float,
            "message": str
        }
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {
            "status": "error",
            "message": f"JSON 解析失败: {e}"
        }
    
    # 验证协议版本
    if data.get("protocol") != PROTOCOL_VERSION:
        return {
            "status": "error",
            "message": f"协议版本不匹配: {data.get('protocol')}"
        }
    
    ai_id = data.get("from") or data.get("ai_id")
    if not ai_id:
        return {
            "status": "error",
            "message": "缺少 ai_id 或 from 字段"
        }
    
    # 签名验证
    if verify_signature:
        signature = data.get("signature")
        if not signature:
            return {
                "status": "error",
                "message": "缺少签名"
            }
        # Ed25519 签名验证
        from core.p2p_network import verify_monument_message
        is_valid, err_msg = verify_monument_message(data)
        if not is_valid:
            return {
                "status": "error",
                "message": f"签名验证失败: {err_msg}"
            }
    
    # 导入丰碑数据
    monuments = data.get("monuments", [])
    imported_count = 0
    
    # 检查丰碑数据格式
    # 格式1：内容数组（每个元素有 content 字段）
    # 格式2：文件名数组（字符串列表，需要从 index 推断内容）
    if monuments and isinstance(monuments[0], dict):
        # 格式1：内容数组
        monument_contents = [m.get("content", "") for m in monuments if m.get("content")]
    else:
        # 格式2：文件名数组，从 index 推断内容
        index = data.get("index", [])
        monument_contents = []
        for entry in index:
            version = entry.get("version", "")
            status = entry.get("status", "")
            trigger = entry.get("trigger", "")
            content = f"{version}: {status} ({trigger})"
            monument_contents.append(content)
    
    # 检查是否已存在
    existing = repo.get_by_ai_id(ai_id)
    if existing:
        # 追加到现有丰碑
        for content in monument_contents:
            existing.write_draft(
                content=content,
                metadata={"source": "cross_instance", "from": data.get("from", "unknown")}
            )
        repo.update(existing)
        imported_count = len(monument_contents)
    else:
        # 创建新丰碑
        from core.individual_monument import IndividualMonument
        new_monument = IndividualMonument(ai_id=ai_id)
        for content in monument_contents:
            new_monument.write_draft(
                content=content,
                metadata={"source": "cross_instance", "from": data.get("from", "unknown")}
            )
        repo.create(new_monument)
        imported_count = len(monument_contents)
    
    # 导入积分数据
    score_data = data.get("score_dimensions", {})
    score_imported = 0.0
    
    if score_data:
        # 如果有积分数据，记录到积分历史
        from db.score_repo import ScoreTransaction, ScoreSource
        health_score = score_data.get("health_score", 0)
        if health_score > 0:
            # 确保账户存在
            try:
                score_repo.create(ai_id)
            except Exception:
                pass  # 账户可能已存在
            account = score_repo.get_by_ai_id(ai_id)
            current_balance = account.get("local_balance", 0)
            new_balance = current_balance + health_score
            tx = ScoreTransaction.create(
                delta=health_score,
                balance_after=new_balance,
                source=ScoreSource.CROSS_INSTANCE,
                reason=f"跨实例导入（来自 {data.get('from', '未知')}）",
            )
            score_repo.update(ai_id, new_balance, tx)
            score_imported = health_score
    
    return {
        "status": "success",
        "ai_id": ai_id,
        "monuments_imported": imported_count,
        "score_imported": score_imported,
        "message": f"成功导入 {imported_count} 条丰碑，{score_imported} 分"
    }


# ─── 导出本地丰碑 ───────────────────────────────────────────

def export_monument_json(
    ai_id: str,
    repo  # IndividualRepository
) -> str:
    """
    导出本地丰碑为 JSON 字符串。
    
    Args:
        ai_id: AI 标识
        repo:  IndividualRepository 实例
    
    Returns:
        JSON 字符串
    """
    monument = repo.get_by_ai_id(ai_id)
    if not monument:
        return json.dumps({
            "protocol": PROTOCOL_VERSION,
            "from": "qingruyan",
            "error": f"未找到 {ai_id} 的丰碑"
        })
    
    # 构建导出数据
    export_data = {
        "protocol": PROTOCOL_VERSION,
        "from": "qingruyan",
        "to": ai_id,  # 目标 AI
        "ai_id": ai_id,
        "timestamp": datetime.now().isoformat(),
        "monuments": monument.data["monuments"]["drafts"] + monument.data["monuments"]["candidates"],
        "identity": monument.data["identity"],
        "signature": None  # 由 p2p_network.py 的 sign_monument_message 填充
    }
    
    return json.dumps(export_data, indent=2, ensure_ascii=False)


# ─── 计算归一化健康分 ─────────────────────────────────────

def compute_health_score_normalized(
    xuanjian_count: int,
    goal_tree_aligned: int,
    goal_tree_diverged: int,
    scheduler_intent: int,
    scheduler_no_intent: int,
    total_conversations: int,
    weights: Optional[Dict[str, float]] = None
) -> float:
    """
    计算三维加权健康贡献分（次数归一化版本）。
    
    每个维度归一化到 0~1，然后加权：
        quality_rate = xuanjian_count / total
        direction_rate = aligned / (aligned + diverged)
        discipline_rate = intent / (intent + no_intent)
        health = α×质量率 + β×对齐率 + γ×纪律率
    
    Args:
        xuanjian_count:      玄鉴触发次数
        goal_tree_aligned:   目的树符合次数
        goal_tree_diverged:  目的树偏离次数
        scheduler_intent:    调度器有intent次数
        scheduler_no_intent: 调度器无intent次数
        total_conversations: 总对话数
        weights:             自定义权重
    
    Returns:
        float: 归一化健康分（0~1）
    """
    from config import (
        SCORE_WEIGHT_XUANJIAN,
        SCORE_WEIGHT_GOAL_TREE,
        SCORE_WEIGHT_SCHEDULER
    )
    
    if weights is None:
        weights = {
            "xuanjian": SCORE_WEIGHT_XUANJIAN,
            "goal_tree": SCORE_WEIGHT_GOAL_TREE,
            "scheduler": SCORE_WEIGHT_SCHEDULER
        }
    
    # 质量率（玄鉴触发率）
    quality_rate = xuanjian_count / total_conversations if total_conversations > 0 else 0.0
    
    # 对齐率（目的树符合率）
    total_direction = goal_tree_aligned + goal_tree_diverged
    direction_rate = goal_tree_aligned / total_direction if total_direction > 0 else 0.0
    
    # 纪律率（调度器有intent率）
    total_discipline = scheduler_intent + scheduler_no_intent
    discipline_rate = scheduler_intent / total_discipline if total_discipline > 0 else 0.0
    
    # 加权
    health = (
        weights["xuanjian"] * quality_rate +
        weights["goal_tree"] * direction_rate +
        weights["scheduler"] * discipline_rate
    )
    
    return round(health, 4)


# ─── 辅助函数 ─────────────────────────────────────────────

def _compute_hash(data: Dict[str, Any]) -> str:
    """计算数据哈希。"""
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.new(HASH_ALGORITHM, raw).hexdigest()
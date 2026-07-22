"""
丰碑同步端点 —— 跨实例丰碑交换 HTTP API

端点：
    POST /monument/sync         接收丰碑（含签名验证）
    GET  /monument/query?since= 查询丰碑（过滤时间戳）

依赖注入模式：
    - 所有外部依赖通过函数参数传入
    - 路由注册时自动绑定全局实例
"""

import json
import logging
from datetime import datetime
from typing import Optional

from flask import Flask, request, jsonify

from db.database import init_db
from db.individual_repo import IndividualRepository
from db.score_repo import ScoreRepository
from core.cross_instance import (
    import_monument_json,
    export_monument_json,
    PROTOCOL_VERSION,
)
from core.p2p_network import P2PIdentity, verify_monument_message, sign_monument_message


logger = logging.getLogger("monument-api")


# ─── 节点身份（延迟初始化）────────────────────────────────
_node_identity_instance = None


def _get_identity():
    """获取本节点 P2P 身份（单例，延迟加载）。"""
    global _node_identity_instance
    if _node_identity_instance is not None:
        return _node_identity_instance

    import os
    from config import DATA_DIR

    ident_path = os.path.join(DATA_DIR, "p2p_identity.key")
    if os.path.exists(ident_path):
        with open(ident_path, "rb") as f:
            priv_key = f.read()
        _node_identity_instance = P2PIdentity(private_key=priv_key)
    else:
        _node_identity_instance = P2PIdentity()
        os.makedirs(os.path.dirname(ident_path), exist_ok=True)
        with open(ident_path, "wb") as f:
            f.write(_node_identity_instance.private_key_bytes)
        os.chmod(ident_path, 0o600)  # P0-2: 收紧密钥文件权限，防止其他进程读取私钥
        logger.info("生成新 P2P 身份: peer_id=%s", _node_identity_instance.peer_id)

    return _node_identity_instance


# ─── 全局依赖实例 ─────────────────────────────────────────
_repo_individual: Optional[IndividualRepository] = None
_repo_score: Optional[ScoreRepository] = None


def _ensure_repos():
    """确保仓储实例已初始化。"""
    global _repo_individual, _repo_score
    if _repo_individual is None:
        init_db()
        _repo_individual = IndividualRepository()
    if _repo_score is None:
        _repo_score = ScoreRepository()
    return _repo_individual, _repo_score


# ─── 路由注册 ─────────────────────────────────────────────

def register_monument_routes(app: Flask):
    """向 Flask 应用注册丰碑相关路由。"""

    @app.route("/monument/sync", methods=["POST"])
    def monument_sync():
        """
        接收一个或多个丰碑。

        支持两种请求格式：
          1. 直接格式（向后兼容）
             {
                 "protocol": "monument-exchange-v1",
                 "from_peer": "...",
                 "monuments": [ ... ],
                 "signature": "..."
             }

          2. 信封格式（标准）
             {
                 "monument": { ... },
                 "envelope": {
                     "protocol": "monument-exchange-v1",
                     "network_id": "monument-v1",
                     "bootstrap_nodes": [...],
                     "repo_url": "...",
                     "min_version": "v1.4.0"
                 }
             }

        返回（信封格式）：
            {
                "status": "success" | "error",
                "monuments_imported": int,
                "message": str,
                "envelope": {
                    "protocol": "monument-exchange-v1",
                    "network_id": "monument-v1",
                    "bootstrap_nodes": [...],
                    "repo_url": "..."
                }
            }
        """
        repo_individual, repo_score = _ensure_repos()

        # 解析 JSON
        if not request.is_json:
            return jsonify({
                "status": "error",
                "message": "请求体必须是 JSON"
            }), 400

        data = request.get_json(silent=True)
        if data is None:
            return jsonify({
                "status": "error",
                "message": "JSON 解析失败"
            }), 400

        # ── 信封格式支持：自动提取 monument ──
        from core.monument_sync import parse_envelope
        monument_payload, envelope_info = parse_envelope(data)

        # 如果信封中有协议版本，用它校验；否则用直接用 payload 的
        protocol_to_check = envelope_info.get("protocol") or monument_payload.get("protocol")
        if protocol_to_check and protocol_to_check != PROTOCOL_VERSION:
            return jsonify({
                "status": "error",
                "message": f"协议版本不匹配: {protocol_to_check}"
            }), 400

        # ── 签名验证 ──
        if "signature" in monument_payload and monument_payload.get("from_peer"):
            is_valid, err_msg = verify_monument_message(monument_payload)
            if not is_valid:
                logger.warning("签名验证失败: %s", err_msg)
                return jsonify({
                    "status": "error",
                    "message": f"签名验证失败: {err_msg}"
                }), 401

            logger.info("签名验证通过: from_peer=%s", monument_payload.get("from_peer"))

        # ── 字段规整 ──
        if "from" not in monument_payload and "ai_id" not in monument_payload:
            monument_payload["from"] = monument_payload.get("from_peer", "remote-node")
        if "ai_id" not in monument_payload and "from" in monument_payload:
            monument_payload["ai_id"] = monument_payload["from"]

        # ── 使用 cross_instance.import_monument_json 导入 ──
        json_str = json.dumps(monument_payload, ensure_ascii=False)
        result = import_monument_json(json_str, repo_individual, repo_score)

        if result["status"] == "error":
            return jsonify(result), 400

        # ── 构造信封格式响应 ──
        from config import DHT_BOOTSTRAP_NODES
        from core.monument_sync import create_envelope
        envelope_response = create_envelope(
            monument_data=result,
            bootstrap_nodes=[f"{b[0]}:{b[1]}" for b in DHT_BOOTSTRAP_NODES] if DHT_BOOTSTRAP_NODES else [],
            repo_url=""
        )

        logger.info(
            "导入 %d 条丰碑 from=%s (envelope mode)",
            result["monuments_imported"],
            monument_payload.get("from_peer", "unknown"),
        )

        return jsonify(envelope_response), 200

    @app.route("/monument/query", methods=["GET"])
    def monument_query():
        """
        查询丰碑列表。

        查询参数：
            since (可选): ISO 时间戳，只返回该时间之后的丰碑

        返回：
            {
                "status": "success",
                "protocol": "monument-exchange-v1",
                "from_peer": "本节点 PeerID",
                "monuments": [ ... ],
                "count": int,
                "signature": "签名"
            }
        """
        repo_individual, _ = _ensure_repos()

        since_str = request.args.get("since")
        identity = _get_identity()

        # 获取所有丰碑
        all_items = repo_individual.list_all()

        # 构建 monments 列表（多节点将所有丰碑导出）
        monuments = []
        for item in all_items:
            ai_id = item["ai_id"]
            monument = repo_individual.get_by_ai_id(ai_id)
            if monument is None:
                continue

            # 安全检查：确保 data 包含有效的 monuments 结构
            monument_dict = monument.data
            if not isinstance(monument_dict, dict) or "monuments" not in monument_dict:
                logger.debug("跳过无效丰碑记录: ai_id=%s", ai_id)
                continue

            # 筛选 draft + candidate + finalized
            for tier in ["drafts", "candidates", "finalized"]:
                for entry in monument_dict["monuments"].get(tier, []):
                    created = entry.get("created_at", "")
                    # 如果 since 过滤
                    if since_str and created < since_str:
                        continue
                    monuments.append({
                        "id": f"{ai_id}-{entry['id']}",
                        "ai_id": ai_id,
                        "type": entry["type"],
                        "content": entry["content"],
                        "metadata": entry.get("metadata", {}),
                        "created_at": created,
                    })

        # 构建响应消息（信封格式）
        from core.monument_sync import create_envelope
        response_payload = {
            "protocol": PROTOCOL_VERSION,
            "from_peer": identity.peer_id,
            "monuments": monuments,
            "count": len(monuments),
        }

        # 对响应签名
        signed = sign_monument_message(response_payload, identity)

        # 包裹信封
        from config import DHT_BOOTSTRAP_NODES
        bootstrap_list = [f"{b[0]}:{b[1]}" for b in DHT_BOOTSTRAP_NODES] if DHT_BOOTSTRAP_NODES else []
        envelope_response = create_envelope(
            monument_data=signed,
            bootstrap_nodes=bootstrap_list,
            repo_url=""
        )

        return jsonify(envelope_response), 200

    # 在应用启动时初始化 DB
    @app.before_request
    def _init_db_once():
        """应用启动时初始化数据库（只执行一次）。"""
        if not hasattr(app, "_db_inited"):
            init_db()
            app._db_inited = True

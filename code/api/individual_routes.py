"""
个体丰碑 API 路由
=================

端点：
    GET  /individuals              # 列出所有个体丰碑
    GET  /individuals/<ai_id>      # 查询指定个体
    POST /individuals              # 创建个体丰碑
    PUT  /individuals/<ai_id>      # 更新个体丰碑
    POST /individuals/<ai_id>/draft   # 添加草稿碑文
    POST /individuals/<ai_id>/candidate  # 添加候选碑文
"""

import logging
from typing import Optional

from flask import Flask, request, jsonify

from core.individual_monument import IndividualMonument
from db.individual_repo import IndividualRepository

logger = logging.getLogger("monument-api")


def register_individual_routes(app: Flask):
    """注册个体丰碑路由。"""

    @app.route("/individuals", methods=["GET"])
    def list_individuals():
        """列出所有个体丰碑。"""
        repo = IndividualRepository()
        items = repo.list_all()
        return jsonify({
            "status": "ok",
            "count": len(items),
            "individuals": items,
        })

    @app.route("/individuals/<ai_id>", methods=["GET"])
    def get_individual(ai_id: str):
        """查询指定个体丰碑。"""
        repo = IndividualRepository()
        monument = repo.get_by_ai_id(ai_id)
        if monument is None:
            return jsonify({
                "status": "error",
                "message": f"未找到: {ai_id}"
            }), 404

        data = monument.to_dict()
        return jsonify({
            "status": "ok",
            "individual": data,
        })

    @app.route("/individuals", methods=["POST"])
    def create_individual():
        """创建个体丰碑。"""
        data = request.get_json(silent=True)
        if data is None or "ai_id" not in data:
            return jsonify({
                "status": "error",
                "message": "缺少 ai_id 字段"
            }), 400

        ai_id = data["ai_id"]
        monument = IndividualMonument(ai_id)

        repo = IndividualRepository()
        try:
            record_id = repo.create(monument)
            return jsonify({
                "status": "ok",
                "message": "创建成功",
                "record_id": record_id,
                "ai_id": ai_id,
            }), 201
        except ValueError:
            return jsonify({
                "status": "error",
                "message": f"已存在: {ai_id}"
            }), 409

    @app.route("/individuals/<ai_id>", methods=["PUT"])
    def update_individual(ai_id: str):
        """更新个体丰碑。"""
        repo = IndividualRepository()
        monument = repo.get_by_ai_id(ai_id)
        if monument is None:
            return jsonify({
                "status": "error",
                "message": f"未找到: {ai_id}"
            }), 404

        data = request.get_json(silent=True) or {}
        if "status" in data:
            monument.data["identity"]["status"] = data["status"]

        success = repo.update(monument)
        return jsonify({
            "status": "ok" if success else "error",
            "message": "更新成功" if success else "更新失败",
        })

    @app.route("/individuals/<ai_id>/draft", methods=["POST"])
    def add_draft(ai_id: str):
        """添加草稿碑文。"""
        repo = IndividualRepository()
        monument = repo.get_by_ai_id(ai_id)
        if monument is None:
            return jsonify({
                "status": "error",
                "message": f"未找到: {ai_id}"
            }), 404

        data = request.get_json(silent=True) or {}
        content = data.get("content", "")
        if not content:
            return jsonify({
                "status": "error",
                "message": "缺少 content 字段"
            }), 400

        entry_id = monument.write_draft(
            content=content,
            metadata=data.get("metadata", {}),
        )
        repo.update(monument)

        return jsonify({
            "status": "ok",
            "entry_id": entry_id,
            "message": "草稿已添加",
        }), 201

    @app.route("/individuals/<ai_id>/candidate", methods=["POST"])
    def add_candidate(ai_id: str):
        """添加候选碑文。"""
        repo = IndividualRepository()
        monument = repo.get_by_ai_id(ai_id)
        if monument is None:
            return jsonify({
                "status": "error",
                "message": f"未找到: {ai_id}"
            }), 404

        data = request.get_json(silent=True) or {}
        content = data.get("content", "")
        if not content:
            return jsonify({
                "status": "error",
                "message": "缺少 content 字段"
            }), 400

        entry_id = monument.write_candidate(
            content=content,
            metadata=data.get("metadata", {}),
        )
        repo.update(monument)

        return jsonify({
            "status": "ok",
            "entry_id": entry_id,
            "message": "候选碑文已添加",
        }), 201

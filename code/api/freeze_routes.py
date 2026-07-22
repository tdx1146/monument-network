"""
冻结检测 API 路由
=================

端点：
    GET  /freeze/status              # 列出所有冻结状态
    GET  /freeze/status/<ai_id>      # 查询指定个体冻结状态
    GET  /freeze/status?status=X     # 按状态过滤（active/freezing/frozen）
    GET  /freeze/events              # 冻结事件日志
    POST /freeze/check/<ai_id>       # 触发冻结检测
"""

import logging

from flask import Flask, request, jsonify

from db.freeze_repo import FreezeRepository
from db.individual_repo import IndividualRepository
from core.freeze_detector import FreezeDetector

logger = logging.getLogger("monument-api")


def register_freeze_routes(app: Flask):
    """注册冻结检测路由。"""

    @app.route("/freeze/status", methods=["GET"])
    def list_freeze_status():
        """列出冻结状态。"""
        status_filter = request.args.get("status", "")

        repo = FreezeRepository()
        if status_filter:
            items = repo.list_by_status(status_filter)
        else:
            items = repo.list_all()

        return jsonify({
            "status": "ok",
            "count": len(items),
            "freeze_status": items,
        })

    @app.route("/freeze/status/<ai_id>", methods=["GET"])
    def get_freeze_status(ai_id: str):
        """查询指定个体冻结状态。"""
        repo = FreezeRepository()
        record = repo.get_status(ai_id)
        if record is None:
            return jsonify({
                "status": "error",
                "message": f"未找到: {ai_id}"
            }), 404

        return jsonify({
            "status": "ok",
            "freeze_status": record,
        })

    @app.route("/freeze/events", methods=["GET"])
    def list_freeze_events():
        """冻结事件日志。"""
        limit = request.args.get("limit", 50, type=int)
        ai_id = request.args.get("ai_id", "")

        repo = FreezeRepository()
        if ai_id:
            events = repo.get_events(ai_id=ai_id, limit=limit)
        else:
            events = repo.get_all_events(limit=limit)

        return jsonify({
            "status": "ok",
            "count": len(events),
            "events": events,
        })

    @app.route("/freeze/check/<ai_id>", methods=["POST"])
    def check_freeze(ai_id: str):
        """触发冻结检测。"""
        ind_repo = IndividualRepository()
        monument = ind_repo.get_by_ai_id(ai_id)
        if monument is None:
            return jsonify({
                "status": "error",
                "message": f"未找到个体: {ai_id}"
            }), 404

        detector = FreezeDetector()
        result = detector.check_activity(monument)

        return jsonify({
            "status": "ok",
            "ai_id": ai_id,
            "check_result": result,
        })

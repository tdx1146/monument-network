"""
玄鉴评分 API 路由
=================

端点：
    POST /xuanjian/evaluate        # 运行玄鉴评分
    GET  /xuanjian/evaluations     # 查询评估记录
    GET  /xuanjian/evaluations/<ai_id>  # 查询指定个体评估记录
    GET  /xuanjian/high_confidence # 高置信度评估
"""

import logging

from flask import Flask, request, jsonify

from core.xuanjian_pipe import XuanjianPipe, InsightSource
from db.xuanjian_repo import XuanjianRepository

logger = logging.getLogger("monument-api")


def register_xuanjian_routes(app: Flask):
    """注册玄鉴评分路由。"""

    @app.route("/xuanjian/evaluate", methods=["POST"])
    def evaluate_insight():
        """运行玄鉴评分。"""
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({
                "status": "error",
                "message": "JSON 解析失败"
            }), 400

        ai_id = data.get("ai_id", "")
        text = data.get("text", "")
        confidence = data.get("confidence", 0.5)
        source_type = data.get("source_type", "manual")

        if not ai_id or not text:
            return jsonify({
                "status": "error",
                "message": "ai_id 和 text 为必填字段"
            }), 400

        pipe = XuanjianPipe()
        source = InsightSource(source_type=source_type)
        result = pipe.evaluate(
            ai_id=ai_id,
            text=text,
            confidence=confidence,
            source=source,
        )

        return jsonify({
            "status": "ok",
            "evaluation": {
                "insight_id": result.insight_id,
                "ai_id": ai_id,
                "monument_score": result.monument_score,
                "time_binding": result.time_binding,
                "transferability": result.transferability,
                "abstraction_level": result.abstraction_level,
                "is_candidate": result.is_candidate,
                "is_increment": result.is_increment,
                "pattern_key": result.pattern_key,
            },
        })

    @app.route("/xuanjian/evaluations", methods=["GET"])
    def list_evaluations():
        """查询最近的评估记录。"""
        limit = request.args.get("limit", 20, type=int)
        repo = XuanjianRepository()
        items = repo.get_recent(limit=limit)

        return jsonify({
            "status": "ok",
            "count": len(items),
            "evaluations": items,
        })

    @app.route("/xuanjian/evaluations/<ai_id>", methods=["GET"])
    def get_evaluations_by_ai(ai_id: str):
        """查询指定个体的评估记录。"""
        limit = request.args.get("limit", 50, type=int)
        repo = XuanjianRepository()
        items = repo.get_by_ai_id(ai_id, limit=limit)

        return jsonify({
            "status": "ok",
            "ai_id": ai_id,
            "count": len(items),
            "evaluations": items,
        })

    @app.route("/xuanjian/high_confidence", methods=["GET"])
    def list_high_confidence():
        """高置信度评估。"""
        threshold = request.args.get("threshold", 0.8, type=float)
        limit = request.args.get("limit", 20, type=int)
        repo = XuanjianRepository()
        items = repo.list_high_confidence(threshold=threshold, limit=limit)

        return jsonify({
            "status": "ok",
            "threshold": threshold,
            "count": len(items),
            "evaluations": items,
        })

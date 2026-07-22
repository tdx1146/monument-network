"""
积分账本 API 路由
=================

端点：
    GET  /scores              # 列出积分账户（Top N）
    GET  /scores/<ai_id>      # 查询指定个体积分
    POST /scores/<ai_id>      # 创建积分账户
    POST /scores/<ai_id>/add  # 增加积分
    POST /scores/<ai_id>/deduct  # 扣除积分
"""

import logging

from flask import Flask, request, jsonify

from db.score_repo import ScoreRepository

logger = logging.getLogger("monument-api")


def register_score_routes(app: Flask):
    """注册积分路由。"""

    @app.route("/scores", methods=["GET"])
    def list_scores():
        """列出积分账户。"""
        top_n = request.args.get("top_n", 10, type=int)
        repo = ScoreRepository()
        items = repo.list_all(top_n=top_n)
        return jsonify({
            "status": "ok",
            "count": len(items),
            "scores": items,
        })

    @app.route("/scores/<ai_id>", methods=["GET"])
    def get_score(ai_id: str):
        """查询指定个体积分。"""
        repo = ScoreRepository()
        account = repo.get_by_ai_id(ai_id)
        if account is None:
            return jsonify({
                "status": "error",
                "message": f"未找到: {ai_id}"
            }), 404

        return jsonify({
            "status": "ok",
            "account": account,
        })

    @app.route("/scores/<ai_id>", methods=["POST"])
    def create_score(ai_id: str):
        """创建积分账户。"""
        repo = ScoreRepository()
        try:
            account = repo.create(ai_id)
            return jsonify({
                "status": "ok",
                "message": "积分账户已创建",
                "account": account,
            }), 201
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": str(e),
            }), 409

    @app.route("/scores/<ai_id>/add", methods=["POST"])
    def add_score(ai_id: str):
        """增加积分。"""
        data = request.get_json(silent=True) or {}
        amount = data.get("amount", 0)
        reason = data.get("reason", "")

        if not isinstance(amount, (int, float)) or amount <= 0:
            return jsonify({
                "status": "error",
                "message": "amount 必须为正数"
            }), 400

        repo = ScoreRepository()
        account = repo.get_by_ai_id(ai_id)
        if account is None:
            account = repo.create(ai_id)

        new_balance = account.get("local_balance", 0) + amount
        repo.update(ai_id, new_balance, type("Tx", (), {
            "amount": amount,
            "reason": reason,
            "tx_type": "credit",
        })())

        return jsonify({
            "status": "ok",
            "ai_id": ai_id,
            "new_balance": new_balance,
            "message": f"积分 +{amount}",
        })

    @app.route("/scores/<ai_id>/deduct", methods=["POST"])
    def deduct_score(ai_id: str):
        """扣除积分。"""
        data = request.get_json(silent=True) or {}
        amount = data.get("amount", 0)
        reason = data.get("reason", "")

        if not isinstance(amount, (int, float)) or amount <= 0:
            return jsonify({
                "status": "error",
                "message": "amount 必须为正数"
            }), 400

        repo = ScoreRepository()
        account = repo.get_by_ai_id(ai_id)
        if account is None:
            return jsonify({
                "status": "error",
                "message": f"未找到: {ai_id}"
            }), 404

        current = account.get("local_balance", 0)
        if current < amount:
            return jsonify({
                "status": "error",
                "message": f"余额不足: {current} < {amount}"
            }), 400

        new_balance = current - amount
        repo.update(ai_id, new_balance, type("Tx", (), {
            "amount": -amount,
            "reason": reason,
            "tx_type": "debit",
        })())

        return jsonify({
            "status": "ok",
            "ai_id": ai_id,
            "new_balance": new_balance,
            "message": f"积分 -{amount}",
        })

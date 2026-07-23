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
        if not repo.account_exists(ai_id):
            return jsonify({
                "status": "error",
                "message": f"未找到: {ai_id}"
            }), 404

        account = repo.get_by_ai_id(ai_id)
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
        """增加积分（原子操作，无竞态条件）。"""
        data = request.get_json(silent=True) or {}
        amount = data.get("amount", 0)
        reason = data.get("reason", "")

        if not isinstance(amount, (int, float)) or amount <= 0:
            return jsonify({
                "status": "error",
                "message": "amount 必须为正数"
            }), 400

        repo = ScoreRepository()
        # 无条件确保账户存在（INSERT OR IGNORE，幂等）
        import sqlite3 as _sqlite3
        try:
            repo.create(ai_id)
        except _sqlite3.IntegrityError:
            # 外键约束失败：个体丰碑不存在
            return jsonify({
                "status": "error",
                "message": f"个体丰碑不存在，无法创建积分账户: {ai_id}"
            }), 404
        except Exception:
            pass  # 账户已存在

        # 原子操作：read-modify-write 在单个事务中完成
        from db.score_repo import ScoreSource
        try:
            result = repo.add_balance(
            ai_id, delta=amount,
            source=ScoreSource.MANUAL,
            reason=reason or "manual credit",
        )

        except _sqlite3.OperationalError as e:
            return jsonify({
                "status": "error",
                "message": "系统繁忙，请重试",
                "detail": str(e),
            }), 503

        if result is None:
            return jsonify({
                "status": "error",
                "message": f"账户创建失败: {ai_id}"
            }), 500

        return jsonify({
            "status": "ok",
            "ai_id": ai_id,
            "old_balance": result["old_balance"],
            "new_balance": result["new_balance"],
            "transaction_id": result["transaction_id"],
            "message": f"积分 +{amount}",
        })

    @app.route("/scores/<ai_id>/deduct", methods=["POST"])
    def deduct_score(ai_id: str):
        """扣除积分（原子操作，余额检查在事务内完成）。"""
        data = request.get_json(silent=True) or {}
        amount = data.get("amount", 0)
        reason = data.get("reason", "")

        if not isinstance(amount, (int, float)) or amount <= 0:
            return jsonify({
                "status": "error",
                "message": "amount 必须为正数"
            }), 400

        repo = ScoreRepository()
        if not repo.account_exists(ai_id):
            return jsonify({
                "status": "error",
                "message": f"未找到: {ai_id}"
            }), 404

        # 原子操作：余额检查 + 扣除在单个事务中完成
        from db.score_repo import ScoreSource
        import sqlite3 as _sqlite3
        try:
            result = repo.add_balance(
                ai_id, delta=-amount,
                source=ScoreSource.PENALTY,
                reason=reason or "manual debit",
            )
        except _sqlite3.OperationalError as e:
            return jsonify({
                "status": "error",
                "message": "系统繁忙，请重试",
                "detail": str(e),
            }), 503
        except ValueError as e:
            return jsonify({
                "status": "error",
                "message": str(e),
            }), 400

        if result is None:
            return jsonify({
                "status": "error",
                "message": f"账户不存在: {ai_id}"
            }), 404

        return jsonify({
            "status": "ok",
            "ai_id": ai_id,
            "old_balance": result["old_balance"],
            "new_balance": result["new_balance"],
            "transaction_id": result["transaction_id"],
            "message": f"积分 -{amount}",
        })

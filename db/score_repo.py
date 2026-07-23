"""
积分账本仓储 —— 积分账户与交易的持久化

表设计（ARCHITECTURE.md §5.3 & §5.4）：
  - score_accounts:    积分账户表（ai_id, local_balance, global_balance, last_updated）
  - score_transactions: 积分交易表（transaction_id, ai_id, delta, balance_after, source, reason, timestamp）

职责（ARCHITECTURE.md §2.3.3）：
  - ScoreAccount 的读写
  - ScoreTransaction 的追加写入
  - 排行榜查询
"""

import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

import uuid
from dataclasses import dataclass, field
from enum import Enum

from config import SCORE_DECIMAL_PRECISION

from .database import get_connection


class ScoreSource(Enum):
    """积分来源枚举"""
    XUANJIAN = "xuanjian"
    GOAL_TREE = "goal_tree"
    SCHEDULER = "scheduler"
    REWARD = "reward"
    CROSS_INSTANCE = "cross_instance"
    MANUAL = "manual"
    PENALTY = "penalty"


@dataclass
class ScoreTransaction:
    """积分交易记录"""
    transaction_id: str = field(default_factory=lambda: f"tx-{uuid.uuid4().hex[:12]}")
    delta: float = 0.0
    balance_after: float = 0.0
    source: ScoreSource = ScoreSource.MANUAL
    reason: str = ""

    @classmethod
    def create(cls, delta: float, balance_after: float,
               source: ScoreSource = ScoreSource.MANUAL,
               reason: str = "") -> "ScoreTransaction":
        """快捷创建方法"""
        return cls(
            transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
            delta=delta,
            balance_after=balance_after,
            source=source,
            reason=reason,
        )


class ScoreRepository:
    """积分账本仓储"""

    # ─── 建表 ─────────────────────────────────────────────────

    @staticmethod
    def create_table() -> None:
        """创建积分相关表（幂等）。"""
        conn = get_connection()

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS score_accounts (
                ai_id          TEXT PRIMARY KEY,
                local_balance  REAL NOT NULL DEFAULT 0.0,
                global_balance REAL NOT NULL DEFAULT 0.0,
                last_updated   TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (ai_id) REFERENCES individual_monuments(ai_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS score_transactions (
                transaction_id TEXT PRIMARY KEY,
                ai_id          TEXT NOT NULL,
                delta          REAL NOT NULL,
                balance_after  REAL NOT NULL,
                source         TEXT NOT NULL,
                reason         TEXT NOT NULL,
                timestamp      TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (ai_id) REFERENCES score_accounts(ai_id)
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tx_ai
            ON score_transactions(ai_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tx_time
            ON score_transactions(timestamp)
            """
        )

        conn.commit()

    # ─── CRUD：账户 ───────────────────────────────────────────

    def create(self, ai_id: str) -> Dict:
        """
        创建积分账户（初始余额 0.0）。

        Args:
            ai_id: AI 标识

        Returns:
            Dict: {"ai_id": str, "local_balance": 0.0, ...}
                  若账户已存在则返回现有账户

        Raises:
            sqlite3.IntegrityError: ai_id 违反外键约束（个体丰碑不存在）
        """
        conn = get_connection()
        now = datetime.now().isoformat()

        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO score_accounts (ai_id, local_balance, global_balance, last_updated)
                VALUES (?, 0.0, 0.0, ?)
                """,
                (ai_id, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise

        return self.get_by_ai_id(ai_id)

    def account_exists(self, ai_id: str) -> bool:
        """高效检查账户是否存在（避免 list_all 全表扫描）。"""
        conn = get_connection()
        row = conn.execute(
            "SELECT 1 FROM score_accounts WHERE ai_id = ?",
            (ai_id,),
        ).fetchone()
        return row is not None

    def get_by_ai_id(self, ai_id: str) -> Dict:
        """
        获取积分账户及交易历史。

        Args:
            ai_id: AI 标识

        Returns:
            Dict: {
                "ai_id": str,
                "local_balance": float,
                "global_balance": float,
                "last_updated": str,
                "history": List[Dict]
            }
            不存在时返回默认空账户（local_balance=0.0, history=[]）
        """
        conn = get_connection()

        row = conn.execute(
            "SELECT ai_id, local_balance, global_balance, last_updated FROM score_accounts WHERE ai_id = ?",
            (ai_id,),
        ).fetchone()

        if row is None:
            return {
                "ai_id": ai_id,
                "local_balance": 0.0,
                "global_balance": 0.0,
                "last_updated": datetime.now().isoformat(),
                "history": [],
            }

        history = self._get_history(conn, ai_id)

        return {
            "ai_id": row["ai_id"],
            "local_balance": round(row["local_balance"], SCORE_DECIMAL_PRECISION),
            "global_balance": round(row["global_balance"], SCORE_DECIMAL_PRECISION),
            "last_updated": row["last_updated"],
            "history": history,
        }

    def add_balance(self, ai_id: str, delta: float,
                     source: ScoreSource = ScoreSource.MANUAL,
                     reason: str = "") -> dict:
        """
        原子地增加/减少积分（read-modify-write 在单个事务中完成）。
        使用 BEGIN IMMEDIATE 获取写锁，防止并发竞态。

        Args:
            ai_id:  AI 标识
            delta:  变化量（正数=增加，负数=减少）
            source: 积分来源
            reason: 原因描述

        Returns:
            dict: {"ai_id", "old_balance", "new_balance", "transaction_id"}
                  若账户不存在返回 None

        Raises:
            ValueError: 扣除后余额为负
        """
        conn = get_connection()
        now = datetime.now().isoformat()

        try:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                "SELECT local_balance FROM score_accounts WHERE ai_id = ?",
                (ai_id,),
            ).fetchone()

            if row is None:
                conn.execute("ROLLBACK")
                return None

            old_balance = row["local_balance"]
            new_balance = old_balance + delta

            if new_balance < 0:
                raise ValueError(
                    f"余额不足: {old_balance} + {delta} = {new_balance} < 0"
                )

            tx = ScoreTransaction.create(
                delta=delta,
                balance_after=new_balance,
                source=source,
                reason=reason,
            )

            conn.execute(
                "UPDATE score_accounts SET local_balance = ?, last_updated = ? WHERE ai_id = ?",
                (new_balance, now, ai_id),
            )
            conn.execute(
                """INSERT INTO score_transactions (transaction_id, ai_id, delta, balance_after, source, reason, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tx.transaction_id, ai_id, tx.delta, tx.balance_after,
                 tx.source.value, tx.reason, now),
            )

            conn.commit()

            return {
                "ai_id": ai_id,
                "old_balance": round(old_balance, SCORE_DECIMAL_PRECISION),
                "new_balance": round(new_balance, SCORE_DECIMAL_PRECISION),
                "transaction_id": tx.transaction_id,
            }
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def update(self, ai_id: str, new_balance: float, tx: object) -> None:
        """
        更新账户余额并追加交易记录（事务安全：失败时自动回滚）。

        典型调用路径（ARCHITECTURE.md §6.1）：
          core/local_score.py → db/score_repo.py

        Args:
            ai_id:       AI 标识
            new_balance: 更新后的余额
            tx:          ScoreTransaction 对象（至少包含以下属性：
                         transaction_id, delta, balance_after, source, reason）
        """
        conn = get_connection()
        now = datetime.now().isoformat()

        try:
            conn.execute(
                "UPDATE score_accounts SET local_balance = ?, last_updated = ? WHERE ai_id = ?",
                (new_balance, now, ai_id),
            )

            conn.execute(
                """
                INSERT INTO score_transactions (transaction_id, ai_id, delta, balance_after, source, reason, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx.transaction_id,
                    ai_id,
                    tx.delta,
                    tx.balance_after,
                    tx.source.value,
                    tx.reason,
                    now,
                ),
            )

            conn.commit()
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def list_all(self, top_n: int = 10) -> List[Dict]:
        """
        获取积分排行榜（按余额降序排列）。

        Args:
            top_n: 返回前 N 名

        Returns:
            List[Dict]: [{"ai_id": str, "local_balance": float, "global_balance": float, "last_updated": str}, ...]
        """
        conn = get_connection()

        rows = conn.execute(
            """
            SELECT ai_id, local_balance, global_balance, last_updated
            FROM score_accounts
            ORDER BY local_balance DESC
            LIMIT ?
            """,
            (top_n,),
        ).fetchall()

        return [
            {
                "ai_id": row["ai_id"],
                "local_balance": round(row["local_balance"], SCORE_DECIMAL_PRECISION),
                "global_balance": round(row["global_balance"], SCORE_DECIMAL_PRECISION),
                "last_updated": row["last_updated"],
            }
            for row in rows
        ]

    # ─── 内部方法 ─────────────────────────────────────────────

    @staticmethod
    def _get_history(conn: sqlite3.Connection, ai_id: str, limit: int = 50) -> List[Dict]:
        """获取指定 AI 的交易历史（按时间倒序）。"""
        rows = conn.execute(
            """
            SELECT transaction_id, ai_id, delta, balance_after, source, reason, timestamp
            FROM score_transactions
            WHERE ai_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (ai_id, limit),
        ).fetchall()

        return [
            {
                "transaction_id": row["transaction_id"],
                "ai_id": row["ai_id"],
                "delta": row["delta"],
                "balance_after": round(row["balance_after"], SCORE_DECIMAL_PRECISION),
                "source": row["source"],
                "reason": row["reason"],
                "timestamp": row["timestamp"],
            }
            for row in rows
        ]

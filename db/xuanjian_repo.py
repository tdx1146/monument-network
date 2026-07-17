"""
Xuanjian Repository — 玄鉴评估结果的持久化仓库

表：xuanjian_evaluations
- id          INTEGER PRIMARY KEY AUTOINCREMENT
- ai_id       TEXT    NOT NULL
- time_binding      REAL NOT NULL    -- 时间绑定度 0.0~1.0
- transferability   REAL NOT NULL    -- 可迁移性 0.0~1.0
- abstraction_level REAL NOT NULL    -- 抽象层级 0.0~1.0
- confidence        REAL NOT NULL    -- 综合置信度 0.0~1.0
- is_candidate      INTEGER NOT NULL DEFAULT 0  -- 是否触发候选
- pattern_key       TEXT NOT NULL DEFAULT ''
- triggered_at      TEXT             -- ISO timestamp，候选触发时间
- created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from db.database import get_connection


class XuanjianRepository:
    """玄鉴评估仓库——CRUD 操作"""

    # ── 建表 ─────────────────────────────────────────────

    @staticmethod
    def ensure_table() -> None:
        """确保 xuanjian_evaluations 表存在（幂等）。"""
        conn = get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS xuanjian_evaluations (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ai_id             TEXT    NOT NULL,
                time_binding      REAL    NOT NULL,
                transferability   REAL    NOT NULL,
                abstraction_level REAL    NOT NULL,
                confidence        REAL    NOT NULL,
                is_candidate      INTEGER NOT NULL DEFAULT 0,
                pattern_key       TEXT    NOT NULL DEFAULT '',
                triggered_at      TEXT,
                created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_xuanjian_ai_id
            ON xuanjian_evaluations(ai_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_xuanjian_confidence
            ON xuanjian_evaluations(confidence)
            """
        )
        conn.commit()

    # ── Create ───────────────────────────────────────────

    @staticmethod
    def create_evaluation(
        ai_id: str,
        time_binding: float,
        transferability: float,
        abstraction_level: float,
        confidence: float,
        is_candidate: bool = False,
        pattern_key: str = "",
        triggered_at: Optional[str] = None,
    ) -> int:
        """
        写入一条玄鉴评估记录。
        返回自增 id。
        """
        conn = get_connection()
        cursor = conn.execute(
            """
            INSERT INTO xuanjian_evaluations
                (ai_id, time_binding, transferability, abstraction_level,
                 confidence, is_candidate, pattern_key, triggered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ai_id,
                time_binding,
                transferability,
                abstraction_level,
                confidence,
                1 if is_candidate else 0,
                pattern_key,
                triggered_at,
            ),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    # ── Read ─────────────────────────────────────────────

    @staticmethod
    def get_by_ai_id(ai_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """
        查询指定 AI 的所有评估记录，按 id 降序。
        返回包含所有字段的字典列表。
        """
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, ai_id, time_binding, transferability, abstraction_level,
                   confidence, is_candidate, pattern_key, triggered_at, created_at
            FROM xuanjian_evaluations
            WHERE ai_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (ai_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def list_high_confidence(threshold: float = 0.8, limit: int = 20) -> list[dict[str, Any]]:
        """
        列出综合置信度 >= threshold 的评估记录，按置信度降序。
        """
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, ai_id, time_binding, transferability, abstraction_level,
                   confidence, is_candidate, pattern_key, triggered_at, created_at
            FROM xuanjian_evaluations
            WHERE confidence >= ?
            ORDER BY confidence DESC
            LIMIT ?
            """,
            (threshold, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_recent(limit: int = 10) -> list[dict[str, Any]]:
        """
        查询最近的评估记录，按 id 降序。
        """
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, ai_id, time_binding, transferability, abstraction_level,
                   confidence, is_candidate, pattern_key, triggered_at, created_at
            FROM xuanjian_evaluations
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def count_by_pattern(pattern_key: str) -> int:
        """
        查询同一 pattern_key 的评估出现次数。
        """
        conn = get_connection()
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM xuanjian_evaluations
            WHERE pattern_key = ?
            """,
            (pattern_key,),
        ).fetchone()
        return row["cnt"] if row else 0

    # ── Update ───────────────────────────────────────────

    @staticmethod
    def mark_candidate(evaluation_id: int, triggered_at: str) -> bool:
        """
        将指定评估标记为候选触发。
        返回是否找到并更新。
        """
        conn = get_connection()
        cursor = conn.execute(
            """
            UPDATE xuanjian_evaluations
            SET is_candidate = 1, triggered_at = ?
            WHERE id = ?
            """,
            (triggered_at, evaluation_id),
        )
        conn.commit()
        return cursor.rowcount > 0

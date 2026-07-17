"""
Individual Repository — 个体丰碑的持久化仓库
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from core.individual_monument import IndividualMonument
from db.database import get_connection


class IndividualRepository:
    """个体丰碑仓库——CRUD 操作"""

    # ── Create ────────────────────────────────────────────

    @staticmethod
    def create(monument: IndividualMonument) -> int:
        """
        存入新丰碑。返回记录 id。
        如果 ai_id 已存在则抛出 ValueError。
        """
        conn = get_connection()
        ai_id = monument.data["identity"]["ai_id"]
        data_json = monument.to_json()
        now = _now_iso()
        try:
            cursor = conn.execute(
                """
                INSERT INTO individual_monuments (ai_id, data_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (ai_id, data_json, now, now),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]
        except Exception as exc:
            raise ValueError(f"ai_id '{ai_id}' already exists") from exc

    # ── Read ──────────────────────────────────────────────

    @staticmethod
    def get_by_ai_id(ai_id: str) -> Optional[IndividualMonument]:
        """按 ai_id 查询丰碑，不存在时返回 None。"""
        conn = get_connection()
        row = conn.execute(
            "SELECT data_json FROM individual_monuments WHERE ai_id = ?",
            (ai_id,),
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["data_json"])
        return IndividualMonument.from_dict(data)

    # ── Update ────────────────────────────────────────────

    @staticmethod
    def update(monument: IndividualMonument) -> bool:
        """更新丰碑数据。返回是否找到并更新了记录。"""
        conn = get_connection()
        ai_id = monument.data["identity"]["ai_id"]
        data_json = monument.to_json()
        now = _now_iso()
        cursor = conn.execute(
            """
            UPDATE individual_monuments
            SET data_json = ?, updated_at = ?
            WHERE ai_id = ?
            """,
            (data_json, now, ai_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    # ── List ──────────────────────────────────────────────

    @staticmethod
    def list_all() -> list[dict[str, Any]]:
        """列出所有丰碑的概要信息（不含完整 data_json）。"""
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, ai_id, created_at, updated_at
            FROM individual_monuments
            ORDER BY id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

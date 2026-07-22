"""
Freeze Repository — 冻结状态仓储

管理 freeze_status 表和 freeze_events 表的 CRUD 操作，
为 FreezeDetector 提供持久化支持。

表结构：
  freeze_status:  ai_id, status, last_activity_at, pending_since, frozen_at
  freeze_events:  event_id, ai_id, event_type, timestamp, details_json
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from db.database import get_connection


class FreezeRepository:
    """冻结状态仓储——冻结状态与事件的持久化"""

    # ── DDL ───────────────────────────────────────────────

    DDL_STATEMENTS = [
        """
        CREATE TABLE IF NOT EXISTS freeze_status (
            ai_id           TEXT PRIMARY KEY,
            status          TEXT NOT NULL DEFAULT 'active',
            last_activity_at TEXT NOT NULL,
            pending_since   TEXT,
            frozen_at       TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_freeze_status_status
        ON freeze_status(status)
        """,
        """
        CREATE TABLE IF NOT EXISTS freeze_events (
            event_id    TEXT PRIMARY KEY,
            ai_id       TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            details_json TEXT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_freeze_events_ai
        ON freeze_events(ai_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_freeze_events_type
        ON freeze_events(event_type)
        """,
    ]

    @classmethod
    def ensure_tables(cls) -> None:
        """确保冻结相关表已创建（幂等）。"""
        conn = get_connection()
        for ddl in cls.DDL_STATEMENTS:
            conn.execute(ddl)
        conn.commit()

    # ── Status CRUD ───────────────────────────────────────

    @staticmethod
    def upsert_status(
        ai_id: str,
        status: str,
        last_activity_at: str,
        pending_since: Optional[str] = None,
        frozen_at: Optional[str] = None,
    ) -> None:
        """创建或更新冻结状态记录。"""
        conn = get_connection()
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO freeze_status (ai_id, status, last_activity_at, pending_since, frozen_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ai_id) DO UPDATE SET
                status          = excluded.status,
                last_activity_at = excluded.last_activity_at,
                pending_since   = excluded.pending_since,
                frozen_at       = excluded.frozen_at,
                updated_at      = excluded.updated_at
            """,
            (ai_id, status, last_activity_at, pending_since, frozen_at, now),
        )
        conn.commit()

    @staticmethod
    def get_status(ai_id: str) -> Optional[dict[str, Any]]:
        """查询单个 AI 的冻结状态，不存在时返回 None。"""
        conn = get_connection()
        row = conn.execute(
            """
            SELECT ai_id, status, last_activity_at, pending_since, frozen_at,
                   created_at, updated_at
            FROM freeze_status
            WHERE ai_id = ?
            """,
            (ai_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def list_by_status(status: str) -> list[dict[str, Any]]:
        """按状态列出所有记录。"""
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT ai_id, status, last_activity_at, pending_since, frozen_at,
                   created_at, updated_at
            FROM freeze_status
            WHERE status = ?
            ORDER BY updated_at ASC
            """,
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def list_all() -> list[dict[str, Any]]:
        """列出所有冻结状态记录。"""
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT ai_id, status, last_activity_at, pending_since, frozen_at,
                   created_at, updated_at
            FROM freeze_status
            ORDER BY updated_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Event CRUD ───────────────────────────────────────

    @staticmethod
    def write_event(
        ai_id: str,
        event_type: str,
        details: Optional[dict[str, Any]] = None,
    ) -> str:
        """写入一条冻结事件记录。返回 event_id。"""
        conn = get_connection()
        event_id = str(uuid.uuid4())
        ts = _now_iso()
        details_json = json.dumps(details, ensure_ascii=False) if details else None
        conn.execute(
            """
            INSERT INTO freeze_events (event_id, ai_id, event_type, timestamp, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, ai_id, event_type, ts, details_json),
        )
        conn.commit()
        return event_id

    @staticmethod
    def get_events(
        ai_id: str,
        limit: int = 50,
        event_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """查询某个 AI 的冻结事件历史。"""
        conn = get_connection()
        if event_type:
            rows = conn.execute(
                """
                SELECT event_id, ai_id, event_type, timestamp, details_json
                FROM freeze_events
                WHERE ai_id = ? AND event_type = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (ai_id, event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT event_id, ai_id, event_type, timestamp, details_json
                FROM freeze_events
                WHERE ai_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (ai_id, limit),
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            if d["details_json"]:
                d["details"] = json.loads(d["details_json"])
            del d["details_json"]
            results.append(d)
        return results

    @staticmethod
    def get_all_events(limit: int = 100) -> list[dict[str, Any]]:
        """查询所有冻结事件（全局）。"""
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT event_id, ai_id, event_type, timestamp, details_json
            FROM freeze_events
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d["details_json"]:
                d["details"] = json.loads(d["details_json"])
            del d["details_json"]
            results.append(d)
        return results


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

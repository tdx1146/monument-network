"""
数据库连接和表管理
"""

import sqlite3
from typing import Optional

from config import DB_PATH


_connection: Optional[sqlite3.Connection] = None


def get_connection() -> sqlite3.Connection:
    """获取或创建 SQLite 连接。"""
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(DB_PATH)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
    return _connection


def init_db() -> None:
    """初始化数据库表结构。如果表已存在则不重复创建。"""
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS individual_monuments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ai_id       TEXT    NOT NULL UNIQUE,
            data_json   TEXT    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_individual_monuments_ai_id
        ON individual_monuments(ai_id)
        """
    )
    conn.commit()


def close_db() -> None:
    """关闭数据库连接。"""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None

"""
数据库连接和表管理
===================
- 线程安全的连接池（threading.local）
- 统一的 DDL 迁移管理
- WAL 模式 + 外键约束
"""

import logging
import sqlite3
import threading
from typing import Optional

from config import DB_PATH

logger = logging.getLogger(__name__)

# 线程本地存储：每个线程独立连接
_thread_local = threading.local()

# 迁移版本表名
_MIGRATION_TABLE = "_migrations"


def get_connection() -> sqlite3.Connection:
    """获取当前线程的 SQLite 连接（线程安全）。

    每个线程自动获取独立连接，避免 Flask 多线程 + 后台线程
    同时访问时的竞争条件。
    """
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _thread_local.conn = conn
    return conn


def new_connection() -> sqlite3.Connection:
    """创建独立的数据库连接（用于跨线程场景）。

    返回新连接，不缓存到线程本地存储。
    适用于 HealthChecker 等需要独立连接的模块。
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def close_db() -> None:
    """关闭当前线程的数据库连接。"""
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        conn.close()
        _thread_local.conn = None


# ─── 迁移管理 ────────────────────────────────────────────

def _ensure_migration_table() -> None:
    """创建迁移追踪表。"""
    conn = get_connection()
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_MIGRATION_TABLE} (
            name        TEXT PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _migration_applied(name: str) -> bool:
    """检查迁移是否已执行。"""
    conn = get_connection()
    row = conn.execute(
        f"SELECT 1 FROM {_MIGRATION_TABLE} WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


def _record_migration(name: str) -> None:
    """记录迁移已执行。"""
    conn = get_connection()
    conn.execute(
        f"INSERT OR IGNORE INTO {_MIGRATION_TABLE} (name) VALUES (?)",
        (name,)
    )
    conn.commit()


def init_db() -> None:
    """初始化所有数据库表结构（统一入口）。

    按依赖顺序执行各模块的 DDL。
    如果表已存在则跳过（幂等）。
    """
    # 先创建迁移表
    _ensure_migration_table()

    # 迁移 001：个体丰碑表
    _migrate_001_individual_monuments()

    # 迁移 002：积分账户 + 交易表
    _migrate_002_scores()

    # 迁移 003：冻结状态 + 事件表
    _migrate_003_freeze()

    # 迁移 004：玄鉴评估表
    _migrate_004_xuanjian()

    logger.info("数据库初始化完成: %s", DB_PATH)


# ─── 各迁移步骤 ──────────────────────────────────────────

def _migrate_001_individual_monuments() -> None:
    """迁移 001：创建 individual_monuments 表。"""
    name = "001_individual_monuments"
    if _migration_applied(name):
        return

    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS individual_monuments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ai_id       TEXT    NOT NULL UNIQUE,
            data_json   TEXT    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_individual_monuments_ai_id
        ON individual_monuments(ai_id)
    """)
    conn.commit()
    _record_migration(name)
    logger.info("迁移 %s 完成", name)


def _migrate_002_scores() -> None:
    """迁移 002：创建积分账户和交易表。"""
    name = "002_scores"
    if _migration_applied(name):
        return

    from db.score_repo import ScoreRepository
    ScoreRepository.create_table()
    _record_migration(name)
    logger.info("迁移 %s 完成", name)


def _migrate_003_freeze() -> None:
    """迁移 003：创建冻结状态和事件表。"""
    name = "003_freeze"
    if _migration_applied(name):
        return

    from db.freeze_repo import FreezeRepository
    FreezeRepository.ensure_tables()
    _record_migration(name)
    logger.info("迁移 %s 完成", name)


def _migrate_004_xuanjian() -> None:
    """迁移 004：创建玄鉴评估表。"""
    name = "004_xuanjian"
    if _migration_applied(name):
        return

    from db.xuanjian_repo import XuanjianRepository
    XuanjianRepository.ensure_table()
    _record_migration(name)
    logger.info("迁移 %s 完成", name)
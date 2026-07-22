"""
丰碑网络健康检查器 (health_checker.py)
=========================================

检查系统各模块健康状态：数据库、积分账本、DHT 同步、磁盘空间、
丰碑完整性、玄鉴系统等。

用法：
    from core.health_checker import HealthChecker

    checker = HealthChecker()
    result = checker.run_all()

输出格式：
    {
        "status": "healthy" | "degraded" | "unhealthy",
        "checks": {
            "database": {"status": "ok", "details": "..."},
            "balance":  {"status": "ok", "details": "积分: 100"},
            ...
        },
        "timestamp": "2026-07-16T00:45:00Z",
        "uptime_seconds": 86400
    }

状态定义：
    - healthy:   所有检查通过
    - degraded:  部分检查警告（磁盘空间不足、同步延迟）
    - unhealthy: 关键检查失败（数据库不可访问）
"""

import os
import time
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import DATA_DIR, DB_PATH, DHT_PORT

logger = logging.getLogger("monument-health")


# ─── 单次检查结果 ──────────────────────────────────────


class CheckResult:
    """单次检查的结果容器"""

    STATUS_OK = "ok"
    STATUS_WARNING = "warning"
    STATUS_ERROR = "error"

    def __init__(self, name: str, status: str = STATUS_OK, details: str = ""):
        self.name = name
        self.status = status
        self.details = details

    @property
    def is_ok(self) -> bool:
        return self.status == self.STATUS_OK

    @property
    def is_warning(self) -> bool:
        return self.status == self.STATUS_WARNING

    @property
    def is_error(self) -> bool:
        return self.status == self.STATUS_ERROR

    def to_dict(self) -> Dict[str, str]:
        return {"status": self.status, "details": self.details}

    def __repr__(self) -> str:
        return f"<{self.name}: {self.status} — {self.details}>"


# ─── 健康检查器 ─────────────────────────────────────────


class HealthChecker:
    """
    丰碑系统健康检查器。

    每个检查方法都是独立的，异常不会影响其他检查。
    检查超时控制：单个检查最长 5 秒。
    """

    def __init__(
        self,
        db_path: str = str(DB_PATH),
        data_dir: str = str(DATA_DIR),
        dht_port: int = DHT_PORT,
    ):
        self.db_path = db_path
        self.data_dir = data_dir
        self.dht_port = dht_port
        self._start_time = time.time()
        self._CACHE_DURATION = 10  # 数据文件列表缓存 10 秒内不重复读

    @staticmethod
    def _new_connection(db_path: str) -> sqlite3.Connection:
        """
        创建新的线程安全 SQLite 连接。
        每个检查方法在独立线程中运行，不能共享全局 _connection。
        """
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ─── 入口 ────────────────────────────────────────────

    def run_all(
        self,
        check_database: bool = True,
        check_balance: bool = True,
        check_sync: bool = True,
        check_disk: bool = True,
        check_monuments: bool = True,
        check_xuanjian: bool = True,
    ) -> Dict[str, Any]:
        """
        执行所有启用的健康检查，汇总为综合结果。

        参数：
            check_*: 控制哪些检查启用。默认全部启用。

        返回：
            综合健康报告字典
        """
        checks: Dict[str, CheckResult] = {}

        # 按关键程度排序：数据库→积分→磁盘→DHT 同步→丰碑完整性→玄鉴
        if check_database:
            checks["database"] = self._timed_check(self.check_database)

        if check_balance:
            checks["balance"] = self._timed_check(self.check_balance)

        if check_disk:
            checks["disk"] = self._timed_check(self.check_disk)

        if check_sync:
            checks["sync"] = self._timed_check(self.check_sync)

        if check_monuments:
            checks["monuments"] = self._timed_check(self.check_monuments)

        if check_xuanjian:
            checks["xuanjian"] = self._timed_check(self.check_xuanjian)

        # 综合评估
        overall = self._evaluate_overall(checks)

        return {
            "status": overall,
            "checks": {k: v.to_dict() for k, v in checks.items()},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": int(time.time() - self._start_time),
        }

    # ─── 各检查方法 ──────────────────────────────────────

    def check_database(self) -> CheckResult:
        """
        检查 1：数据库连接与表结构。

        检查：
          - SQLite 文件是否存在
          - 能否建立连接并执行查询
          - 关键表是否存在：individual_monuments, score_accounts, xuanjian_evaluations
          - 检查表的行数（空表也算健康，但记录）
        """
        # 1a. 数据库文件存在
        if not os.path.isfile(self.db_path):
            return CheckResult(
                "database", CheckResult.STATUS_ERROR,
                f"数据库文件不存在: {self.db_path}",
            )

        # 1b. 连接测试
        try:
            conn = self._new_connection(self.db_path)
            conn.execute("SELECT 1")
        except sqlite3.Error as e:
            return CheckResult(
                "database", CheckResult.STATUS_ERROR,
                f"数据库连接失败: {e}",
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # 1c. 关键表存在性检查（新建连接）
        required_tables = [
            "individual_monuments",
            "score_accounts",
            "score_transactions",
            "xuanjian_evaluations",
            "freeze_status",
            "freeze_events",
        ]
        try:
            conn = self._new_connection(self.db_path)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            existing_tables = {row["name"] for row in cursor.fetchall()}
        except sqlite3.Error as e:
            return CheckResult(
                "database", CheckResult.STATUS_ERROR,
                f"查询表结构失败: {e}",
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

        missing = [t for t in required_tables if t not in existing_tables]
        if missing:
            details = f"缺失表: {', '.join(missing)}；已有表: {', '.join(sorted(existing_tables))}"
            return CheckResult("database", CheckResult.STATUS_WARNING, details)

        # 1d. 各表行数
        row_counts = {}
        try:
            conn = self._new_connection(self.db_path)
            for table in required_tables:
                cursor = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
                row_counts[table] = cursor.fetchone()["cnt"]
        except sqlite3.Error:
            pass  # 非关键，不中断
        finally:
            try:
                conn.close()
            except Exception:
                pass

        details = f"连接正常 | 表数: {len(existing_tables)}"
        if row_counts:
            counts_str = ", ".join(f"{k}={v}" for k, v in row_counts.items())
            details += f" | {counts_str}"

        return CheckResult("database", CheckResult.STATUS_OK, details)

    def check_balance(self) -> CheckResult:
        """
        检查 2：积分账本。

        检查：
          - score_accounts 表是否存在
          - 总积分余额
          - AI 账户数量
          - 是否有足够积分（> 0 表示系统活跃）
        """
        try:
            conn = self._new_connection(self.db_path)
        except sqlite3.Error as e:
            return CheckResult(
                "balance", CheckResult.STATUS_ERROR,
                f"数据库不可用: {e}",
            )

        try:
            # 检查表是否存在
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='score_accounts'"
            )
            if cursor.fetchone() is None:
                return CheckResult(
                    "balance", CheckResult.STATUS_WARNING,
                    "积分账本表（score_accounts）未创建",
                )

            cursor = conn.execute(
                "SELECT COUNT(*) AS cnt, SUM(local_balance) AS total FROM score_accounts"
            )
            row = cursor.fetchone()
            account_count = row["cnt"]
            total_balance = round(row["total"] or 0.0, 4) if row["total"] else 0.0

            if account_count == 0:
                return CheckResult(
                    "balance", CheckResult.STATUS_WARNING,
                    "无积分账户（系统初始化阶段）",
                )

            # 检查排行榜前 3 名的账户
            top_accounts = ""
            cursor = conn.execute(
                "SELECT ai_id, local_balance FROM score_accounts "
                "ORDER BY local_balance DESC LIMIT 3"
            )
            top = [
                f"{r['ai_id'][:12]}={r['local_balance']}"
                for r in cursor.fetchall()
            ]
            if top:
                top_accounts = " | 前排: " + ", ".join(top)

        except sqlite3.Error as e:
            return CheckResult(
                "balance", CheckResult.STATUS_ERROR,
                f"查询积分表失败: {e}",
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if total_balance > 0:
            return CheckResult(
                "balance", CheckResult.STATUS_OK,
                f"积分总额: {total_balance}，账户数: {account_count}{top_accounts}",
            )
        else:
            return CheckResult(
                "balance", CheckResult.STATUS_WARNING,
                f"积分总额为零或为负，账户数: {account_count}{top_accounts}",
            )

    def check_sync(self) -> CheckResult:
        """
        检查 3：同步状态与 DHT。

        检查：
          - 能否导入 kademlia（表示可选 DHT 支持已安装）
          - 同步管理器状态（通过 periodic_syncer 推断）
          - 最近的同步报告（如果有）
          - DHT 端口是否可达（仅检查端口配置合理性）
        """
        details_parts = []
        warnings = []

        # 3a. kademlia 库可用性
        try:
            from kademlia.network import Server as KademliaServer  # noqa: F401
            details_parts.append("kademlia 库可用")
        except ImportError:
            details_parts.append("kademlia 库未安装（降级本地模式）")
            warnings.append("kademlia 可选")

        # 3b. 尝试访问 sync_manager 全局实例
        # 由于 sync_manager 在 app.py 作用域，我们检查 periodic_syncer 模块是否存在
        from core import periodic_syncer
        details_parts.append(f"同步模块版本: periodic_syncer v1")

        # 3c. 检查 DHT 存储目录
        from config import DHT_STORAGE_DIR
        dht_storage = str(DHT_STORAGE_DIR)
        if os.path.isdir(dht_storage):
            dht_files = os.listdir(dht_storage)
            details_parts.append(f"DHT 存储目录: {len(dht_files)} 个文件")
        else:
            details_parts.append("DHT 存储目录未创建")
            warnings.append("DHT 未启动或未持久化")

        # 3d. 检查是否有绑定 DHT 端口的进程（非 root 可以使用 netstat/ss）
        # 使用 ss 命令检查端口
        port_ok = self._check_port_listening(self.dht_port)
        if port_ok:
            details_parts.append(f"DHT 端口 {self.dht_port} 已监听")
        else:
            details_parts.append(f"DHT 端口 {self.dht_port} 未监听")
            warnings.append("DHT 节点未启动")

        # 3e. 检查应用 API 端口
        from config import API_PORT
        api_port_ok = self._check_port_listening(API_PORT)
        if api_port_ok:
            details_parts.append(f"API 端口 {API_PORT} 已监听")
        else:
            details_parts.append(f"API 端口 {API_PORT} 未监听")
            warnings.append("API 服务未启动")

        details = " | ".join(details_parts) if details_parts else "无同步信息"

        if warnings:
            status = CheckResult.STATUS_WARNING if len(warnings) <= 2 else CheckResult.STATUS_ERROR
            details += " | 警告: " + "; ".join(warnings)
            return CheckResult("sync", status, details)

        return CheckResult("sync", CheckResult.STATUS_OK, details)

    def check_disk(self) -> CheckResult:
        """
        检查 4：磁盘空间。

        检查：
          - 数据目录剩余空间
          - < 100MB 告警
          - < 10MB 错误（可能导致写入失败）
          - 数据目录大小
        """
        if not os.path.isdir(self.data_dir):
            return CheckResult(
                "disk", CheckResult.STATUS_ERROR,
                f"数据目录不存在: {self.data_dir}",
            )

        try:
            # 跨平台磁盘空间检查（shutil.disk_usage 支持 Windows/Linux/macOS）
            import shutil
            usage = shutil.disk_usage(self.data_dir)
            free_mb = usage.free / (1024 * 1024)
            total_mb = usage.total / (1024 * 1024)
            used_mb = (usage.total - usage.free) / (1024 * 1024)
            usage_pct = (used_mb / total_mb) * 100 if total_mb > 0 else 0
        except OSError as e:
            return CheckResult(
                "disk", CheckResult.STATUS_ERROR,
                f"检查磁盘空间失败: {e}",
            )

        # 数据目录自身占用
        dir_size_bytes = self._get_dir_size(self.data_dir)
        dir_size_mb = dir_size_bytes / (1024 * 1024)

        base = f"剩余: {free_mb:.0f}MB / {total_mb:.0f}MB ({usage_pct:.1f}% 已用)"
        details = f"{base} | 数据占用: {dir_size_mb:.1f}MB"

        if free_mb < 10:
            return CheckResult(
                "disk", CheckResult.STATUS_ERROR,
                f"{details} — 极低磁盘空间，写入风险",
            )
        elif free_mb < 100:
            return CheckResult(
                "disk", CheckResult.STATUS_WARNING,
                f"{details} — 剩余空间不足 100MB",
            )
        else:
            return CheckResult("disk", CheckResult.STATUS_OK, details)

    def check_monuments(self) -> CheckResult:
        """
        检查 5：丰碑完整性。

        检查：
          - individual_monuments 表中丰碑数量
          - drafts/candidates/finalized 子项数量
          - candidates/ 目录中的候选文件数
          - 各丰碑的基本数据完整性
        """
        details_parts = []
        warnings = []

        # 5a. 数据库丰碑数量
        try:
            conn = self._new_connection(self.db_path)
            cursor = conn.execute("SELECT COUNT(*) AS cnt FROM individual_monuments")
            monument_count = cursor.fetchone()["cnt"]
            details_parts.append(f"丰碑数: {monument_count}")
        except sqlite3.Error as e:
            return CheckResult(
                "monuments", CheckResult.STATUS_ERROR,
                f"查询丰碑表失败: {e}",
            )

        try:
            # 5b. 扫描每个丰碑的 draft/candidate/finalized 数量
            total_drafts = 0
            total_candidates = 0
            total_finalized = 0
            monument_errors = 0

            cursor = conn.execute(
                "SELECT ai_id, data_json FROM individual_monuments LIMIT 20"
            )
            for row in cursor.fetchall():
                try:
                    import json
                    data = json.loads(row["data_json"])
                    mon = data.get("monuments", {})
                    total_drafts += len(mon.get("drafts", []))
                    total_candidates += len(mon.get("candidates", []))
                    total_finalized += len(mon.get("finalized", []))
                except (json.JSONDecodeError, KeyError):
                    monument_errors += 1

            if monument_count > 0:
                details_parts.append(
                    f"drafts={total_drafts}, candidates={total_candidates}, "
                    f"finalized={total_finalized}"
                )

            if monument_errors > 0:
                warnings.append(f"{monument_errors} 个丰碑 JSON 解析失败")

            # 5d. 检查冻结状态分布
            cursor = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM freeze_status GROUP BY status"
            )
            statuses = {r["status"]: r["cnt"] for r in cursor.fetchall()}
            if statuses:
                status_str = ", ".join(f"{k}={v}" for k, v in statuses.items())
                details_parts.append(f"冻结: {status_str}")
        except sqlite3.Error:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # 5c. candidates 目录中的文件
        from config import CANDIDATES_DIR
        candidates_dir = str(CANDIDATES_DIR)
        if os.path.isdir(candidates_dir):
            candidate_files = [
                f for f in os.listdir(candidates_dir)
                if f.startswith("candidate-") and f.endswith(".json")
            ]
            pattern_files = [
                f for f in os.listdir(candidates_dir)
                if f.startswith("pattern-") and f.endswith(".json")
            ]
            if candidate_files:
                details_parts.append(f"候选文件: {len(candidate_files)}")
            if pattern_files:
                details_parts.append(f"模式触发: {len(pattern_files)}")

        details = " | ".join(details_parts) if details_parts else "无丰碑数据"

        if warnings:
            details += " | 警告: " + "; ".join(warnings)

        status = CheckResult.STATUS_WARNING if warnings else CheckResult.STATUS_OK
        return CheckResult("monuments", status, details)

    def check_xuanjian(self) -> CheckResult:
        """
        检查 6：玄鉴系统。

        检查：
          - xuanjian_evaluations 表是否存在
          - 总评估次数
          - 高置信度（>= 0.8）评估数
          - 最近一次评估时间
        """
        try:
            conn = self._new_connection(self.db_path)
        except sqlite3.Error as e:
            return CheckResult(
                "xuanjian", CheckResult.STATUS_ERROR,
                f"数据库不可用: {e}",
            )

        try:
            # 检查表是否存在
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='xuanjian_evaluations'"
            )
            if cursor.fetchone() is None:
                return CheckResult(
                    "xuanjian", CheckResult.STATUS_WARNING,
                    "玄鉴评估表（xuanjian_evaluations）未创建",
                )

            details_parts = []
            warnings = []

            cursor = conn.execute("SELECT COUNT(*) AS cnt FROM xuanjian_evaluations")
            total_evaluations = cursor.fetchone()["cnt"]
            details_parts.append(f"总评估: {total_evaluations}")

            # 高置信度评估数
            cursor = conn.execute(
                "SELECT COUNT(*) AS cnt FROM xuanjian_evaluations WHERE is_candidate=1"
            )
            candidate_count = cursor.fetchone()["cnt"]
            if candidate_count > 0:
                details_parts.append(f"已触发候选: {candidate_count}")

            # 最近评估时间
            cursor = conn.execute(
                "SELECT created_at FROM xuanjian_evaluations ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                details_parts.append(f"最近评估: {row['created_at']}")

            # 三轴平均分
            cursor = conn.execute(
                """
                SELECT AVG(time_binding) AS t, AVG(transferability) AS r,
                       AVG(abstraction_level) AS a, AVG(confidence) AS c
                FROM xuanjian_evaluations
                """
            )
            row = cursor.fetchone()
            if row and row["c"] is not None:
                details_parts.append(
                    f"三轴均值: tb={row['t']:.3f}, tr={row['r']:.3f}, "
                    f"ab={row['a']:.3f}, conf={row['c']:.3f}"
                )

            if total_evaluations == 0:
                warnings.append("尚无玄鉴评估记录（正常空系统）")

            details = " | ".join(details_parts) if details_parts else "空"
            if warnings:
                details += " | " + "; ".join(warnings)

            if total_evaluations == 0:
                status = CheckResult.STATUS_OK
                details += "（初始化阶段，无评估记录）"
            else:
                status = CheckResult.STATUS_OK

            return CheckResult("xuanjian", status, details)

        except sqlite3.Error as e:
            return CheckResult(
                "xuanjian", CheckResult.STATUS_ERROR,
                f"查询玄鉴表失败: {e}",
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ─── 辅助方法 ────────────────────────────────────────

    def _timed_check(self, check_fn, timeout: float = 5.0) -> CheckResult:
        """
        带超时的检查包装器。
        超时时返回 error 结果，不中断主流程。
        """
        import threading

        result_container = []
        exception_container = []

        def wrapper():
            try:
                result_container.append(check_fn())
            except Exception as e:
                exception_container.append(e)

        thread = threading.Thread(target=wrapper, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if exception_container:
            return CheckResult(
                check_fn.__name__.replace("check_", ""),
                CheckResult.STATUS_ERROR,
                f"异常: {exception_container[0]}",
            )

        if thread.is_alive():
            return CheckResult(
                check_fn.__name__.replace("check_", ""),
                CheckResult.STATUS_ERROR,
                "检查超时（> 5 秒）",
            )

        return result_container[0]

    def _evaluate_overall(self, checks: Dict[str, CheckResult]) -> str:
        """
        综合评估系统健康状态。

        规则：
          - 有任意 check 为 error → unhealthy
          - 有任意 check 为 warning → degraded
          - 全部 ok → healthy
        """
        has_error = any(v.is_error for v in checks.values())
        has_warning = any(v.is_warning for v in checks.values())

        if has_error:
            return "unhealthy"
        elif has_warning:
            return "degraded"
        else:
            return "healthy"

    def _check_port_listening(self, port: int) -> bool:
        """检查端口是否在监听（通过 /proc/net/tcp 和 /proc/net/tcp6）。"""
        try:
            return self._check_tcp_port(port)
        except Exception:
            return False

    @staticmethod
    def _check_tcp_port(port: int) -> bool:
        """跨平台端口监听检查（socket 探测，支持 Windows/Linux/macOS）。"""
        import socket

        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass

        # 回退：尝试 IPv6
        try:
            with socket.create_connection(("::1", port), timeout=1):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass

        return False

    @staticmethod
    def _get_dir_size(path: str) -> int:
        """递归计算目录大小（字节）。"""
        total = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
        except OSError:
            pass
        return total

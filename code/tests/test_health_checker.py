#!/usr/bin/env python3
"""
Health Checker 单元测试

测试覆盖：
  - CheckResult 基础功能
  - HealthChecker 各检查方法
  - 综合评估逻辑
  - 超时保护
  - 异常隔离
"""

import os
import sys
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

# ── 测试环境准备 ──────────────────────────────────────

# 将 code/ 目录加入 sys.path
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

# 使用临时数据库路径（避免影响实际数据）
_TEST_DIR = tempfile.mkdtemp(prefix="monument_health_test_")
_TEST_DB = os.path.join(_TEST_DIR, "test_monument.db")

# 临时覆盖配置
import config
_ORIG_DB_PATH = config.DB_PATH
_ORIG_DATA_DIR = config.DATA_DIR
config.DB_PATH = _TEST_DB
config.DATA_DIR = _TEST_DIR  # type: ignore[assignment]

# 重新导入会使用新配置
from db.database import init_db, get_connection, close_db
from db.score_repo import ScoreRepository
from db.freeze_repo import FreezeRepository
from db.xuanjian_repo import XuanjianRepository
from core.health_checker import HealthChecker, CheckResult

# 创建同步管理器需要的目录
os.makedirs(os.path.join(_TEST_DIR, "dht"), exist_ok=True)
from config import DHT_STORAGE_DIR, CANDIDATES_DIR
os.makedirs(str(DHT_STORAGE_DIR), exist_ok=True)
os.makedirs(str(CANDIDATES_DIR), exist_ok=True)


# ── 基础测试 ──────────────────────────────────────────


class TestCheckResult(unittest.TestCase):
    """CheckResult 基础功能测试"""

    def test_ok_default(self):
        r = CheckResult("test")
        self.assertEqual(r.status, "ok")
        self.assertTrue(r.is_ok)
        self.assertFalse(r.is_warning)
        self.assertFalse(r.is_error)

    def test_warning_status(self):
        r = CheckResult("test", CheckResult.STATUS_WARNING, "disk space low")
        self.assertEqual(r.status, "warning")
        self.assertTrue(r.is_warning)
        self.assertFalse(r.is_ok)
        self.assertFalse(r.is_error)
        self.assertEqual(r.details, "disk space low")

    def test_error_status(self):
        r = CheckResult("db", CheckResult.STATUS_ERROR, "connection failed")
        self.assertEqual(r.status, "error")
        self.assertTrue(r.is_error)
        self.assertFalse(r.is_ok)

    def test_to_dict(self):
        r = CheckResult("disk", CheckResult.STATUS_WARNING, "only 50MB")
        d = r.to_dict()
        self.assertEqual(d, {"status": "warning", "details": "only 50MB"})

    def test_repr(self):
        r = CheckResult("test", CheckResult.STATUS_OK, "all good")
        self.assertIn("test", repr(r))
        self.assertIn("ok", repr(r))


# ── HealthChecker 测试 ────────────────────────────────


class TestHealthCheckerDatabase(unittest.TestCase):
    """数据库检查测试"""

    @classmethod
    def setUpClass(cls):
        init_db()
        # 额外创建积分、冻结、玄鉴表
        ScoreRepository.create_table()
        FreezeRepository.ensure_tables()
        XuanjianRepository.ensure_table()
        cls.checker = HealthChecker(db_path=_TEST_DB, data_dir=_TEST_DIR)

    def test_database_with_tables(self):
        result = self.checker.check_database()
        self.assertEqual(result.status, "ok", msg=result.details)
        # 验证关键信息
        self.assertIn("连接正常", result.details)
        self.assertIn("individual_monuments", result.details)
        self.assertIn("score_accounts", result.details)
        self.assertIn("xuanjian_evaluations", result.details)

    def test_database_missing_file(self):
        checker = HealthChecker(
            db_path="/tmp/nonexistent/dead.db",
            data_dir=_TEST_DIR,
        )
        result = checker.check_database()
        self.assertEqual(result.status, "error")
        self.assertIn("不存在", result.details)

    def test_database_table_counts(self):
        """插入数据后检查行数是否能反映在输出中"""
        conn = get_connection()
        # 插入一条积分账户记录
        conn.execute(
            "INSERT OR IGNORE INTO score_accounts (ai_id, local_balance) VALUES (?, ?)",
            ("health-test-ai", 42.0),
        )
        conn.commit()
        result = self.checker.check_database()
        self.assertEqual(result.status, "ok", msg=result.details)
        # 应包含 score_accounts 表计数
        self.assertIn("score_accounts", result.details)


class TestHealthCheckerBalance(unittest.TestCase):
    """积分检查测试"""

    @classmethod
    def setUpClass(cls):
        from db.database import _connection
        if _connection is None:
            init_db()
        ScoreRepository.create_table()
        cls.checker = HealthChecker(db_path=_TEST_DB, data_dir=_TEST_DIR)

    def setUp(self):
        # 清理积分账户，避免测试间干扰
        conn = get_connection()
        conn.execute("DELETE FROM score_accounts")
        conn.commit()

    def test_balance_no_accounts(self):
        """无积分账户 → warning"""
        result = self.checker.check_balance()
        self.assertEqual(result.status, "warning")
        self.assertIn("无积分账户", result.details)

    def test_balance_positive(self):
        """有正积分 → ok"""
        conn = get_connection()
        conn.execute(
            "INSERT INTO score_accounts (ai_id, local_balance) VALUES ('ai-1', 100.0)"
        )
        conn.execute(
            "INSERT INTO score_accounts (ai_id, local_balance) VALUES ('ai-2', 50.0)"
        )
        conn.commit()

        result = self.checker.check_balance()
        self.assertEqual(result.status, "ok", msg=result.details)
        self.assertIn("积分总额: 150", result.details)
        self.assertIn("账户数: 2", result.details)

    def test_balance_zero_total(self):
        """总额为零 → warning"""
        conn = get_connection()
        conn.execute(
            "INSERT INTO score_accounts (ai_id, local_balance) VALUES ('ai-zero', 0.0)"
        )
        conn.commit()
        result = self.checker.check_balance()
        self.assertEqual(result.status, "warning")


class TestHealthCheckerDisk(unittest.TestCase):
    """磁盘检查测试"""

    @classmethod
    def setUpClass(cls):
        cls.checker = HealthChecker(db_path=_TEST_DB, data_dir=_TEST_DIR)

    def test_disk_ok(self):
        """正常目录应返回 ok"""
        result = self.checker.check_disk()
        self.assertIn(result.status, ["ok", "warning"])
        self.assertIn("剩余:", result.details)

    def test_disk_missing_dir(self):
        """目录不存在 → error"""
        checker = HealthChecker(data_dir="/tmp/nonexistent_dir_12345")
        result = checker.check_disk()
        self.assertEqual(result.status, "error")
        self.assertIn("不存在", result.details)


class TestHealthCheckerSync(unittest.TestCase):
    """同步状态检查测试"""

    @classmethod
    def setUpClass(cls):
        cls.checker = HealthChecker(db_path=_TEST_DB, data_dir=_TEST_DIR)

    def test_sync_module_available(self):
        """同步模块应可导入"""
        result = self.checker.check_sync()
        self.assertIn(result.status, ["ok", "warning"])
        self.assertIn("同步模块", result.details)
        # DHT 端口可能未监听，但这不是测试失败
        self.assertIn("DHT 端口", result.details)

    def test_sync_dht_dir(self):
        """DHT 存储目录创建后应有文件信息"""
        # 在 DHT 目录写入一个文件
        os.makedirs(str(DHT_STORAGE_DIR), exist_ok=True)
        marker = os.path.join(str(DHT_STORAGE_DIR), ".health_test")
        with open(marker, "w") as f:
            f.write("ok")
        try:
            result = self.checker.check_sync()
            self.assertIn("DHT 存储目录", result.details)
        finally:
            os.remove(marker)


class TestHealthCheckerMonuments(unittest.TestCase):
    """丰碑完整性检查测试"""

    @classmethod
    def setUpClass(cls):
        from db.database import _connection
        if _connection is None:
            init_db()
        cls.checker = HealthChecker(db_path=_TEST_DB, data_dir=_TEST_DIR)

    def setUp(self):
        conn = get_connection()
        conn.execute("DELETE FROM individual_monuments")
        conn.execute("DELETE FROM freeze_status")
        conn.commit()

    def test_monuments_empty(self):
        """无丰碑 → ok（丰碑数为 0）"""
        result = self.checker.check_monuments()
        self.assertEqual(result.status, "ok")
        # 丰碑数为 0 时仍输出

    def test_monuments_with_data(self):
        """有丰碑记录 → ok"""
        from core.individual_monument import IndividualMonument
        from db.individual_repo import IndividualRepository

        repo = IndividualRepository()
        m = IndividualMonument("health-mon-test")
        m.write_draft("draft content")
        m.write_candidate("candidate content")
        repo.create(m)

        result = self.checker.check_monuments()
        self.assertEqual(result.status, "ok", msg=result.details)
        self.assertIn("丰碑数: 1", result.details)
        self.assertIn("drafts=1", result.details)
        self.assertIn("candidates=1", result.details)

    def test_monuments_with_candidate_file(self):
        """有候选文件 → 反映在细节中"""
        # 写入一个候选文件（使用唯一名称）
        import uuid
        unique_name = f"candidate-{uuid.uuid4().hex}.json"
        candidate_path = os.path.join(str(CANDIDATES_DIR), unique_name)
        try:
            with open(candidate_path, "w") as f:
                json.dump({"test": True}, f)
            result = self.checker.check_monuments()
            # 使用严格前缀匹配
            self.assertIn("候选文件:", result.details, msg=result.details)
        finally:
            if os.path.exists(candidate_path):
                os.remove(candidate_path)


class TestHealthCheckerXuanjian(unittest.TestCase):
    """玄鉴系统检查测试"""

    @classmethod
    def setUpClass(cls):
        cls.checker = HealthChecker(db_path=_TEST_DB, data_dir=_TEST_DIR)

    def test_xuanjian_no_table(self):
        """没有表时返回 warning（通过手动删除表测试）"""
        conn = get_connection()
        # 备份当前数据，临时删除 xuanjian 表
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='xuanjian_evaluations'"
        )
        table_exists = cursor.fetchone() is not None
        try:
            # 临时重命名表，模拟缺失
            conn.execute("ALTER TABLE xuanjian_evaluations RENAME TO xuanjian_evaluations_bak")
            conn.commit()
            result = self.checker.check_xuanjian()
            self.assertEqual(result.status, "warning")
            self.assertIn("未创建", result.details)
        finally:
            if table_exists:
                conn.execute("DROP TABLE IF EXISTS xuanjian_evaluations")
                conn.execute("ALTER TABLE xuanjian_evaluations_bak RENAME TO xuanjian_evaluations")
                conn.commit()

    def test_xuanjian_with_data(self):
        """有评估记录 → ok"""
        from db.database import _connection
        if _connection is None:
            init_db()
        XuanjianRepository.ensure_table()

        repo = XuanjianRepository()
        repo.create_evaluation(
            ai_id="xuan-test",
            time_binding=0.3,
            transferability=0.7,
            abstraction_level=0.8,
            confidence=0.85,
            is_candidate=True,
            pattern_key="pattern_test",
        )
        repo.create_evaluation(
            ai_id="xuan-test",
            time_binding=0.6,
            transferability=0.4,
            abstraction_level=0.5,
            confidence=0.55,
        )

        result = self.checker.check_xuanjian()
        self.assertEqual(result.status, "ok", msg=result.details)
        self.assertIn("总评估: 2", result.details)
        self.assertIn("已触发候选: 1", result.details)
        self.assertIn("三轴均值", result.details)


class TestHealthCheckerComprehensive(unittest.TestCase):
    """综合评估测试"""

    @classmethod
    def setUpClass(cls):
        cls.checker = HealthChecker(db_path=_TEST_DB, data_dir=_TEST_DIR)

    def test_run_all_basic(self):
        """run_all 应返回完整报告"""
        report = self.checker.run_all()
        self.assertIn("status", report)
        self.assertIn("checks", report)
        self.assertIn("timestamp", report)
        self.assertIn("uptime_seconds", report)
        self.assertIsInstance(report["uptime_seconds"], int)

    def test_run_all_status_values(self):
        """status 必须是 healthy/degraded/unhealthy 之一"""
        report = self.checker.run_all()
        self.assertIn(report["status"], ["healthy", "degraded", "unhealthy"])

    def test_run_all_checks_dict(self):
        """checks 应包含所有检查项"""
        report = self.checker.run_all()
        expected_keys = {"database", "balance", "sync", "disk", "monuments", "xuanjian"}
        self.assertTrue(
            expected_keys.issubset(report["checks"].keys()),
            msg=f"缺少: {expected_keys - report['checks'].keys()}",
        )

    def test_run_all_selective(self):
        """只启用部分检查"""
        report = self.checker.run_all(
            check_database=True,
            check_balance=False,
            check_sync=False,
            check_disk=True,
            check_monuments=False,
            check_xuanjian=False,
        )
        self.assertIn("database", report["checks"])
        self.assertIn("disk", report["checks"])
        self.assertNotIn("balance", report["checks"])
        self.assertNotIn("sync", report["checks"])
        self.assertNotIn("monuments", report["checks"])
        self.assertNotIn("xuanjian", report["checks"])

    def test_timed_check_timeout(self):
        """超时处理：超时检查应返回 error"""
        def slow_check():
            import time
            time.sleep(10)
            return CheckResult("slow", CheckResult.STATUS_OK, "too late")

        result = self.checker._timed_check(slow_check, timeout=0.5)
        self.assertEqual(result.status, "error")
        self.assertIn("超时", result.details)

    def test_timed_check_exception(self):
        """异常隔离：异常检查应返回 error"""
        def broken_check():
            raise RuntimeError("Kaboom!")

        result = self.checker._timed_check(broken_check)
        self.assertEqual(result.status, "error")
        self.assertIn("异常", result.details)
        self.assertIn("Kaboom", result.details)

    def test_evaluate_overall(self):
        """综合评估逻辑"""
        all_ok = {
            "a": CheckResult("a", CheckResult.STATUS_OK, "ok"),
            "b": CheckResult("b", CheckResult.STATUS_OK, "ok"),
        }
        self.assertEqual(
            self.checker._evaluate_overall(all_ok), "healthy"
        )

        has_warning = {
            "a": CheckResult("a", CheckResult.STATUS_OK),
            "b": CheckResult("b", CheckResult.STATUS_WARNING),
        }
        self.assertEqual(
            self.checker._evaluate_overall(has_warning), "degraded"
        )

        has_error = {
            "a": CheckResult("a", CheckResult.STATUS_OK),
            "b": CheckResult("b", CheckResult.STATUS_ERROR),
        }
        self.assertEqual(
            self.checker._evaluate_overall(has_error), "unhealthy"
        )

        mixed = {
            "a": CheckResult("a", CheckResult.STATUS_OK),
            "b": CheckResult("b", CheckResult.STATUS_WARNING),
            "c": CheckResult("c", CheckResult.STATUS_ERROR),
        }
        # error 优先级高于 warning
        self.assertEqual(
            self.checker._evaluate_overall(mixed), "unhealthy"
        )


class TestHealthCheckerPortCheck(unittest.TestCase):
    """端口检查辅助方法测试"""

    @classmethod
    def setUpClass(cls):
        cls.checker = HealthChecker(db_path=_TEST_DB, data_dir=_TEST_DIR)

    def test_check_port_not_listening(self):
        """未监听端口应返回 False"""
        result = self.checker._check_port_listening(65535)
        self.assertFalse(result)

    def test_check_tcp_port_proc(self):
        """/proc/net/tcp 解析应稳定返回（不抛异常）"""
        result = self.checker._check_port_listening(self.checker.dht_port)
        # 只是验证不抛异常；结果取决于当前系统状态
        self.assertIsInstance(result, bool)


class TestHealthCheckerTimestamp(unittest.TestCase):
    """时间戳和 uptime 格式检查"""

    def test_timestamp_format(self):
        checker = HealthChecker(db_path=_TEST_DB, data_dir=_TEST_DIR)
        report = checker.run_all(check_balance=False, check_sync=False,
                                 check_monuments=False, check_xuanjian=False)
        from datetime import datetime
        # 解析 iso 时间戳
        parsed = datetime.fromisoformat(report["timestamp"])
        self.assertIsNotNone(parsed)
        self.assertTrue(report["uptime_seconds"] >= 0)


# ── 清理 ──────────────────────────────────────────────


def tearDownModule():
    """测试结束恢复配置并清理临时目录。"""
    close_db()
    config.DB_PATH = _ORIG_DB_PATH
    config.DATA_DIR = _ORIG_DATA_DIR

    import shutil
    try:
        shutil.rmtree(_TEST_DIR)
    except OSError:
        pass


# ── 入口 ──────────────────────────────────────────────


if __name__ == "__main__":
    unittest.main(verbosity=2)

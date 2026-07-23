"""
log_writer.py — 写入锁中间件（threading + fcntl 双互斥）

为 event_bus.jsonl 提供线程安全 + 进程安全的追加写入。
同一进程内 threading.Lock 互斥；跨进程用 fcntl.flock 互斥。
"""

import json
import os
import sys
import threading

# Windows 兼容：fcntl 仅在 Unix 上可用
if sys.platform == "win32":
    import msvcrt
    _has_fcntl = False
else:
    import fcntl
    _has_fcntl = True
from datetime import datetime, timezone, timedelta
from typing import Optional

__all__ = ["LogWriter"]

_BJT = timezone(timedelta(hours=8))


class LogWriter:
    """
    线程安全 + 进程安全的 JSONL 写入器

    用法:
        writer = LogWriter("/path/to/event_bus.jsonl")
        writer.write({
            "t": "2026-07-16T14:20:00+08:00",
            "event_type": "task_complete",
            "producer": "xuanjian_pipe",
            "result": "OK"
        })
    """

    def __init__(self, filepath: str):
        self._filepath = os.path.abspath(filepath)
        self._lock = threading.Lock()
        self._ensure_dir()

    def _ensure_dir(self):
        """确保文件所在目录存在"""
        os.makedirs(os.path.dirname(self._filepath), exist_ok=True)

    def _maybe_rotate(self):
        """检查文件大小，超过阈值时自动轮转"""
        try:
            from config import LOG_MAX_SIZE_MB, LOG_RETENTION_COUNT, EVENT_BUS_MAX_SIZE_MB
        except ImportError:
            LOG_MAX_SIZE_MB = 50
            LOG_RETENTION_COUNT = 5
            EVENT_BUS_MAX_SIZE_MB = 100

        if not os.path.exists(self._filepath):
            return

        # event_bus.jsonl 使用更大的阈值
        if "event_bus" in os.path.basename(self._filepath):
            max_bytes = EVENT_BUS_MAX_SIZE_MB * 1024 * 1024
        else:
            max_bytes = LOG_MAX_SIZE_MB * 1024 * 1024
        if os.path.getsize(self._filepath) < max_bytes:
            return

        # 轮转：file.jsonl -> file.jsonl.1 -> file.jsonl.2 -> ...
        for i in range(LOG_RETENTION_COUNT - 1, 0, -1):
            old_path = f"{self._filepath}.{i}"
            new_path = f"{self._filepath}.{i + 1}"
            if os.path.exists(old_path):
                if os.path.exists(new_path):
                    os.remove(new_path)
                os.replace(old_path, new_path)

        # 当前文件 -> .1
        rotated = f"{self._filepath}.1"
        if os.path.exists(rotated):
            os.remove(rotated)
        os.replace(self._filepath, rotated)

        # 删除超出保留数量的旧文件
        for i in range(LOG_RETENTION_COUNT + 1, LOG_RETENTION_COUNT + 5):
            old_path = f"{self._filepath}.{i}"
            if os.path.exists(old_path):
                os.remove(old_path)

    def _now_iso(self) -> str:
        """返回当前时间的 ISO 8601 字符串，含北京时区"""
        return datetime.now(_BJT).isoformat()

    def write(self, record: dict, validate: bool = True) -> dict:
        """
        追加写入一条 JSONL 记录

        参数:
            record:  事件字典，必须包含 event_type / producer / result
            validate: 是否进行基本字段校验（默认 True）

        返回:
            添加了 t（时间戳）后的完整字典

        异常:
            ValueError: 校验不通过时抛出
        """
        # 自动添加时间戳
        if "t" not in record:
            record["t"] = self._now_iso()

        # 基本校验
        if validate:
            required = ["event_type", "producer", "result"]
            for field in required:
                if field not in record:
                    raise ValueError(
                        f"缺少必填字段 '{field}'，记录: {record}"
                    )
            valid_results = {"OK", "FAIL", "WARN", "TIMEOUT"}
            if record["result"] not in valid_results:
                raise ValueError(
                    f"result 必须为 {valid_results}，实际: {record['result']}"
                )

        # 线程互斥
        with self._lock:
            # 写入前检查是否需要轮转
            self._maybe_rotate()
            with open(self._filepath, "a", encoding="utf-8") as f:
                if _has_fcntl:
                    # Unix: fcntl.flock 进程互斥
                    try:
                        fcntl.flock(f, fcntl.LOCK_EX)
                        line = json.dumps(record, ensure_ascii=False)
                        f.write(line + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                else:
                    # Windows: msvcrt.locking 文件锁（锁获取与写入分离，避免重复写入）
                    locked = False
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                        locked = True
                    except (OSError, IOError):
                        pass  # 锁定失败，仍尝试写入（append 模式对小写入是原子的）
                    try:
                        line = json.dumps(record, ensure_ascii=False)
                        f.write(line + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        if locked:
                            try:
                                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                            except (OSError, IOError):
                                pass

        return record

    def read_all(self) -> list:
        """
        读取所有记录（用于测试/审计）

        返回:
            解析后的 dict 列表（非 JSON 行跳过）
        """
        if not os.path.exists(self._filepath):
            return []

        records = []
        with open(self._filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records


def quick_test():
    """快速自测 log_writer 的核心功能"""
    import tempfile
    print("=" * 50)
    print("🧪 log_writer.py 快速自测")
    print("=" * 50)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        writer = LogWriter(tmp_path)

        # 写入测试记录
        rec = writer.write({
            "event_type": "task_complete",
            "producer": "test",
            "result": "OK",
            "detail": "快速自测"
        })
        assert "t" in rec, "写入后应自动添加时间戳"
        print(f"✅ 写入成功: {json.dumps(rec, ensure_ascii=False)}")

        # 验证内容
        all_recs = writer.read_all()
        assert len(all_recs) == 1, f"应读取到 1 条，实际 {len(all_recs)}"
        assert all_recs[0]["event_type"] == "task_complete"
        print("✅ 读取验证成功")

        # 验证校验
        try:
            writer.write({"event_type": "test"})
            print("❌ 校验：缺少字段应抛出 ValueError")
            return
        except ValueError:
            print("✅ 字段校验正常")

        # 验证 result 枚举
        try:
            writer.write({"event_type": "test", "producer": "test", "result": "INVALID"})
            print("❌ 校验：无效 result 应抛出 ValueError")
            return
        except ValueError:
            print("✅ result 枚举校验正常")

        print("=" * 50)
        print("✅ log_writer.py 测试通过")
        print("=" * 50)

    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    quick_test()

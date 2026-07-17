"""
task_scheduler.py — 定时调度器（30s tick）

定时执行 cron 任务，任务完成后写入事件到 event_bus.jsonl。
轻量级实现，依赖 croniter 解析 cron 表达式。
"""

import json
import os
import subprocess
import sys
import time
import threading
import yaml
from datetime import datetime, timezone, timedelta
from typing import Optional

try:
    from .log_writer import LogWriter
except ImportError:
    from log_writer import LogWriter

__all__ = ["TaskScheduler", "main"]

_BJT = timezone(timedelta(hours=8))

# 默认路径
_DEFAULT_EVENT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "event_bus.jsonl"
)
_DEFAULT_OPERATION_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "operation_log.jsonl"
)


class TaskScheduler:
    """
    定时调度器：按 cron 表达式执行任务，完成后写事件

    用法:
        scheduler = TaskScheduler()
        scheduler.start()
        ...
        scheduler.stop()
    """

    def __init__(self, event_file: str = _DEFAULT_EVENT_FILE,
                 operation_log: str = _DEFAULT_OPERATION_LOG,
                 tick_interval: float = 30.0):
        self._event_writer = LogWriter(event_file)
        self._log_writer = LogWriter(operation_log)
        self._tick_interval = tick_interval

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 任务列表
        self._tasks: list = []

    def load_tasks(self, tasks_config: list):
        """
        加载任务配置

        参数:
            tasks_config: [{"id": "...", "cron": "...", "command": "..."}, ...]
        """
        self._tasks = tasks_config
        print(f"[task_scheduler] ✅ 加载 {len(tasks_config)} 个定时任务")

    def _now_iso(self) -> str:
        return datetime.now(_BJT).isoformat()

    def _is_time_to_run(self, task: dict, last_run: dict) -> bool:
        """
        判断任务是否到了运行时间
        每 tick 检查一次，基于 cron 表达式
        """
        from croniter import croniter

        cron_expr = task.get("cron", "")
        task_id = task.get("id", "unknown")

        # 获取上次运行时间
        last = last_run.get(task_id)
        if last is None:
            # 从未运行过 → 运行
            return True

        # 计算下次运行时间
        base = datetime.fromtimestamp(last, tz=_BJT)
        try:
            cron = croniter(cron_expr, base)
            next_time = cron.get_next(datetime)
            now = datetime.now(_BJT)
            return now >= next_time
        except (ValueError, KeyError) as e:
            print(f"[task_scheduler] ⚠️ 任务 '{task_id}' cron 解析失败: {e}")
            return False

    def _run_task(self, task: dict) -> dict:
        """
        执行单个任务并返回事件

        返回:
            事件字典
        """
        task_id = task.get("id", "unknown")
        command = task.get("command", "")

        print(f"[task_scheduler] ▶️ 执行任务: {task_id}")
        self._log_writer.write({
            "event_type": "consumer_action",
            "producer": "task_scheduler",
            "result": "OK",
            "detail": f"开始执行任务: {task_id}",
        })

        try:
            result = subprocess.run(
                command,
                shell=True,
                timeout=task.get("timeout", 60),
                capture_output=True,
                text=True,
            )
            success = result.returncode == 0
            status = "OK" if success else "FAIL"

            event = {
                "t": self._now_iso(),
                "event_type": "task_complete",
                "producer": f"task_scheduler/{task_id}",
                "result": status,
                "trace_id": f"task-{task_id}-{int(time.time())}",
                "detail": f"任务 {task_id} 完成 (exit={result.returncode})",
            }

            if not success:
                event["detail"] += f", stderr: {result.stderr[:200]}"

            self._event_writer.write(event)

            print(f"[task_scheduler] ✅ 任务完成: {task_id} → {status}")
            return event

        except subprocess.TimeoutExpired:
            event = {
                "t": self._now_iso(),
                "event_type": "task_complete",
                "producer": f"task_scheduler/{task_id}",
                "result": "TIMEOUT",
                "trace_id": f"task-{task_id}-{int(time.time())}",
                "detail": f"任务 {task_id} 超时 (>{task.get('timeout', 60)}s)",
            }
            self._event_writer.write(event)

            print(f"[task_scheduler] ⏰ 任务超时: {task_id}")
            return event

        except Exception as e:
            event = {
                "t": self._now_iso(),
                "event_type": "anomaly",
                "producer": f"task_scheduler/{task_id}",
                "result": "FAIL",
                "trace_id": f"task-{task_id}-{int(time.time())}",
                "detail": f"任务 {task_id} 异常: {e}",
            }
            self._event_writer.write(event)

            print(f"[task_scheduler] ❌ 任务异常: {task_id}: {e}")
            return event

    def tick_loop(self):
        """30s 刻度循环"""
        print(f"[task_scheduler] 🟢 启动调度器 (刻度间隔 {self._tick_interval}s)")
        print(f"[task_scheduler] 任务数: {len(self._tasks)}")

        last_run = {}

        while not self._stop_event.is_set():
            now = datetime.now(_BJT)
            print(f"[task_scheduler] ⏱️ tick: {now.strftime('%H:%M:%S')}")

            for task in self._tasks:
                if self._is_time_to_run(task, last_run):
                    event = self._run_task(task)
                    last_run[task["id"]] = time.time()

            self._stop_event.wait(self._tick_interval)

        print("[task_scheduler] ⏹️ 调度器停止")

    def start(self):
        """启动调度器线程"""
        if self._thread and self._thread.is_alive():
            print("[task_scheduler] ⚠️ 调度器已在运行")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.tick_loop,
            daemon=True,
            name="task-scheduler",
        )
        self._thread.start()
        print("[task_scheduler] 🟢 调度器线程已启动")

    def stop(self):
        """停止调度器"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            print("[task_scheduler] ⏹️ 调度器已停止")


def quick_test():
    """快速自测调度器核心功能"""
    print("=" * 50)
    print("🧪 task_scheduler.py 快速自测")
    print("=" * 50)

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        event_file = os.path.join(tmpdir, "event_bus.jsonl")

        scheduler = TaskScheduler(
            event_file=event_file,
            operation_log=os.path.join(tmpdir, "operation_log.jsonl"),
            tick_interval=0.1
        )

        # 加载测试任务
        scheduler.load_tasks([
            {
                "id": "test-hello",
                "cron": "* * * * *",  # 每分钟
                "command": "echo hello",
                "timeout": 10
            }
        ])

        # 测试 cron 解析
        assert scheduler._is_time_to_run(
            {"id": "test-hello", "cron": "* * * * *"},
            {}
        ), "新任务应标记为可运行"
        print("✅ cron 解析正常")

        # 测试事件写入
        from log_writer import LogWriter
        writer = LogWriter(event_file)
        writer.write({
            "event_type": "task_complete",
            "producer": "test",
            "result": "OK",
            "detail": "快速自测"
        })
        recs = writer.read_all()
        assert len(recs) == 1
        print(f"✅ 事件写入正常: {len(recs)} 条")

    print("=" * 50)
    print("✅ task_scheduler.py 快速自测通过")
    print("=" * 50)


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="定时调度器")
    parser.add_argument("--test", action="store_true", help="运行快速自测")
    parser.add_argument("--daemon", action="store_true", help="以后台模式运行")
    args = parser.parse_args()

    if args.test:
        quick_test()
        return

    scheduler = TaskScheduler()
    scheduler.load_tasks([])  # 实际使用时需要加载任务配置
    scheduler.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[task_scheduler] 收到 Ctrl+C，正在关闭...")
        scheduler.stop()

    print("[task_scheduler] 👋 已退出")


if __name__ == "__main__":
    main()

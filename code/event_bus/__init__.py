"""
事件总线系统 v0.4.1 (同步自姐姐)

定时调度器 + 事件消费者 + 写入锁中间件
为丰碑系统提供事件驱动基础设施。

导出:
    LogWriter       — 线程/进程安全的 JSONL 写入器
    EventConsumer   — 事件消费者（3s 轮询规则匹配）
    TaskScheduler   — 定时调度器（30s tick）
"""

from .log_writer import LogWriter
from .event_consumer import EventConsumer
from .task_scheduler import TaskScheduler

__all__ = ["LogWriter", "EventConsumer", "TaskScheduler"]
__version__ = "0.4.1"

"""
定期同步器 (periodic_syncer.py)

管理丰碑的定期同步检查、新丰碑自动发现、全网同步。
基于已有 monument_sync 和 monument_index 构建，复用现有广播/索引逻辑。

用法:
    # 手动运行一次
    syncer = PeriodicSyncer(
        monument_index=index,
        sync_manager=manager,
    )
    report = syncer.check_new_monuments()

    # 启动守护进程
    syncer.start_sync_daemon(interval_minutes=30)
    # ... 应用退出时
    syncer.stop_sync_daemon()
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set
import json
import threading
import time


@dataclass
class SyncReport:
    """单次同步报告"""
    checked_peers: int
    new_monuments_found: int
    new_monuments_synced: int
    failed_peers: List[str]
    errors: List[str]
    duration_seconds: float
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checked_peers": self.checked_peers,
            "new_monuments_found": self.new_monuments_found,
            "new_monuments_synced": self.new_monuments_synced,
            "failed_peers": self.failed_peers,
            "errors": self.errors,
            "duration_seconds": round(self.duration_seconds, 2),
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat(),
        }


class PeriodicSyncer:
    """定期同步器

    定期检查网络中的新丰碑并自动同步。
    复用 monument_index 的查询和 monument_sync 的广播能力。
    """

    def __init__(
        self,
        monument_index: Any,
        sync_manager: Any,
        peer_resolver: Optional[Callable[[], List[str]]] = None,
        sync_callback: Optional[Callable[[str, str], bool]] = None,
    ):
        """
        参数:
            monument_index: MonumentIndex 实例
            sync_manager: MonumentSyncManager 实例（用于广播）
            peer_resolver: 获取当前 peer 地址列表的函数
            sync_callback: 同步单个丰碑的回调 (monument_id, from_peer) -> bool
        """
        self._index = monument_index
        self._sync_mgr = sync_manager
        self._resolve_peers = peer_resolver or (lambda: list(
            monument_index._network_index_cache.keys()
        ))
        self._sync_callback = sync_callback

        # 已检查过的 peer 地址及其最后检查时间
        # 不再永久跳过，而是定期重检
        self._known_peers: Dict[str, float] = {}  # {addr: last_check_timestamp}
        self._recheck_interval: int = 300  # 5 分钟后重检

        # 同步历史
        self._reports: List[SyncReport] = []
        self._total_found = 0
        self._total_synced = 0

        # 守护线程
        self._daemon_thread: Optional[threading.Thread] = None
        self._daemon_running = False
        self._daemon_interval = 30  # 默认30分钟

    # ─── 单次检查 ─────────────────────────

    def check_new_monuments(self, timeout: float = 5.0) -> SyncReport:
        """检查网络中的新丰碑

        遍历所有已知 peer，查询其索引并与本地索引比较差异，
        自动同步缺失的丰碑。

        参数:
            timeout: 每个 peer 请求的超时秒数

        Returns:
            SyncReport 报告
        """
        start_time = time.time()
        errors: List[str] = []
        failed_peers: List[str] = []

        # 获取当前已知 peer 地址
        peer_addrs = self._resolve_peers()
        if not peer_addrs:
            # 从 sync_manager 获取 peer 地址备用
            if hasattr(self._sync_mgr, "peers"):
                for addrs in self._sync_mgr.peers.values():
                    peer_addrs.extend(addrs)

        checked = 0
        new_total = 0
        synced_total = 0

        now = time.time()
        for addr in peer_addrs:
            last_check = self._known_peers.get(addr, 0)
            if now - last_check < self._recheck_interval:
                continue  # 跳过最近检查过的 peer，但不是永久跳过
            self._known_peers[addr] = now
            checked += 1

            try:
                # 查询远程索引
                remote = self._query_peer_index(addr, timeout)
                if remote is None:
                    continue

                # 计算差异
                diff = self._index.compute_index_diff(remote)

                if diff.local_missing:
                    new_total += len(diff.local_missing)
                    # 同步缺失的丰碑
                    syncer = self._make_syncer(addr)
                    synced = self._index.sync_from_diff(diff, syncer)
                    synced_total += synced

                    # 广播本地新丰碑到该 peer（双向同步）
                    if diff.remote_missing:
                        self._broadcast_local_new(diff.remote_missing, addr)

            except Exception as e:
                errors.append(f"[{addr}] 检查失败: {e}")
                failed_peers.append(addr)

        duration = time.time() - start_time
        report = SyncReport(
            checked_peers=checked,
            new_monuments_found=new_total,
            new_monuments_synced=synced_total,
            failed_peers=failed_peers,
            errors=errors,
            duration_seconds=duration,
        )
        self._reports.append(report)
        self._total_found += new_total
        self._total_synced += synced_total
        return report

    def auto_sync_new(self, new_monuments: List[Dict]) -> int:
        """自动同步新丰碑到所有 peer

        参数:
            new_monuments: 新创建的丰碑列表

        Returns:
            成功同步的 peer 数量
        """
        synced_count = 0
        for monument in new_monuments:
            result = self._sync_mgr.broadcast(monument)
            if result.get("success"):
                synced_count += result.get("peers_pushed", 0)
        return synced_count

    # ─── 守护进程 ─────────────────────────

    def start_sync_daemon(self, interval_minutes: int = 30):
        """启动同步守护进程

        在后台线程中定期执行 check_new_monuments。

        参数:
            interval_minutes: 检查间隔（分钟），默认30分钟
        """
        if self._daemon_running:
            print("[PeriodicSyncer] 守护进程已在运行")
            return

        self._daemon_interval = interval_minutes
        self._daemon_running = True
        self._daemon_thread = threading.Thread(
            target=self._daemon_loop,
            name="periodic-syncer",
            daemon=True,
        )
        self._daemon_thread.start()
        print(f"[PeriodicSyncer] ✅ 守护进程已启动，间隔 {interval_minutes} 分钟")

    def stop_sync_daemon(self):
        """停止同步守护进程"""
        self._daemon_running = False
        if self._daemon_thread:
            self._daemon_thread.join(timeout=5.0)
            self._daemon_thread = None
        print("[PeriodicSyncer] ⏹️ 守护进程已停止")

    def _daemon_loop(self):
        """守护进程主循环"""
        while self._daemon_running:
            try:
                report = self.check_new_monuments()
                if report.new_monuments_found > 0:
                    print(
                        f"[PeriodicSyncer] 🔍 发现 {report.new_monuments_found} 个新丰碑, "
                        f"同步 {report.new_monuments_synced} 个"
                    )
                else:
                    print("[PeriodicSyncer] ℹ️ 未发现新丰碑")
            except Exception as e:
                print(f"[PeriodicSyncer] ❌ 守护进程异常: {e}")

            # 等待指定间隔
            for _ in range(self._daemon_interval * 60):
                if not self._daemon_running:
                    break
                time.sleep(1)

    # ─── 内部方法 ─────────────────────────

    def _query_peer_index(self, addr: str, timeout: float) -> Any:
        """查询单个 peer 的索引

        返回 MonumentIndexEntry 或 None。
        """
        import urllib.request
        import urllib.error

        url = f"http://{addr}/monument/index"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
                if body.get("success"):
                    from .monument_index import MonumentIndexEntry
                    return MonumentIndexEntry.from_dict(body["data"])
        except (urllib.error.URLError, OSError, json.JSONDecodeError,
                KeyError):
            pass
        return None

    def _make_syncer(self, peer_addr: str) -> Callable:
        """创建同步回调函数"""
        def _fetch_and_store(monument_id: str, _from_addr: str):
            """从 peer 获取并存储单个丰碑"""
            import urllib.request
            import urllib.error

            url = f"http://{peer_addr}/monument/query/{monument_id}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode())
                if not data.get("success"):
                    raise RuntimeError(f"获取丰碑失败: {data.get('error', '')}")
                monument = data["data"].get("monument", {})
                self._index._monuments[monument_id] = monument

        if self._sync_callback is not None:
            return self._sync_callback
        return _fetch_and_store

    def _broadcast_local_new(self, remote_missing: List[str], peer_addr: str):
        """将本机独有的丰碑广播到远程 peer"""
        for mid in remote_missing:
            if mid in self._index._monuments:
                mon = self._index._monuments[mid]
                self._sync_mgr.broadcast(mon)

    # ─── 状态查询 ─────────────────────────

    def get_latest_report(self) -> Optional[SyncReport]:
        """获取最近一次同步报告"""
        if self._reports:
            return self._reports[-1]
        return None

    def get_all_reports(self) -> List[Dict[str, Any]]:
        """获取所有同步报告"""
        return [r.to_dict() for r in self._reports]

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_found": self._total_found,
            "total_synced": self._total_synced,
            "known_peers": len(self._known_peers),  # now tracks last-check time
            "checked_count": len(self._reports),
            "daemon_running": self._daemon_running,
            "daemon_interval_minutes": self._daemon_interval if self._daemon_running else 0,
            "last_check": self._reports[-1].to_dict() if self._reports else None,
        }

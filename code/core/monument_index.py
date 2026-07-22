"""
丰碑索引服务 (monument_index.py)

提供本地索引构建、网络索引查询、全文搜索功能。
支持跨节点合并查询，是新丰碑自动发现和全网同步的核心组件。

用法:
    from core.monument_index import MonumentIndex, MonumentIndexEntry

    index = MonumentIndex(local_monuments={}, local_peers={})

    # 构建索引
    entry = index.build_local_index("ai-1")

    # 查询网络索引
    net_index = index.query_network_index(["192.168.0.100:18891"])

    # 搜索丰碑
    results = index.search_monuments("keyword")

    # 索引差异同步
    diff = index.compute_index_diff(remote_index)
    synced = index.sync_from_diff("192.168.0.100:18891", diff)
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
import json
import time


# =============================================================================
# 数据结构
# =============================================================================


@dataclass
class MonumentIndexEntry:
    """单个节点的丰碑索引条目"""
    ai_id: str
    monument_count: int
    monuments: List[str]          # monument_id 列表
    last_updated: str             # ISO 8601 UTC 时间戳
    peer_addrs: List[str] = field(default_factory=list)  # 该节点地址

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ai_id": self.ai_id,
            "count": self.monument_count,
            "monuments": self.monuments,
            "last_updated": self.last_updated,
            "peer_addrs": self.peer_addrs,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "MonumentIndexEntry":
        return MonumentIndexEntry(
            ai_id=data["ai_id"],
            monument_count=data.get("count", len(data.get("monuments", []))),
            monuments=data.get("monuments", []),
            last_updated=data.get("last_updated", ""),
            peer_addrs=data.get("peer_addrs", []),
        )


@dataclass
class IndexDiff:
    """索引差异 - 用于增量同步"""
    peer_addr: str
    local_missing: List[str]      # 本地缺失的 monument_id
    remote_missing: List[str]     # 远程缺失的 monument_id
    common: List[str]             # 双方都有的 monument_id
    computed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "peer_addr": self.peer_addr,
            "local_missing": self.local_missing,
            "remote_missing": self.remote_missing,
            "common": self.common,
            "computed_at": self.computed_at or datetime.now(timezone.utc).isoformat(),
        }


@dataclass
class SyncStatus:
    """同步状态"""
    total_monuments: int = 0
    synced_monuments: int = 0
    missing_monuments: int = 0
    last_sync_time: str = ""
    sync_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total_monuments,
            "synced": self.synced_monuments,
            "missing": self.missing_monuments,
            "last_sync_time": self.last_sync_time,
            "sync_errors": self.sync_errors[:10],  # 截断避免过大
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "SyncStatus":
        return SyncStatus(
            total_monuments=data.get("total", 0),
            synced_monuments=data.get("synced", 0),
            missing_monuments=data.get("missing", 0),
            last_sync_time=data.get("last_sync_time", ""),
            sync_errors=data.get("sync_errors", []),
        )


# =============================================================================
# 索引服务
# =============================================================================


class MonumentIndex:
    """丰碑索引服务

    管理本地索引，支持网络索引查询、全文搜索、差异同步。
    """

    def __init__(
        self,
        local_monuments: Dict[str, Any],
        local_peers: Dict[str, List[str]],
        ai_id: Optional[str] = None,
    ):
        """
        参数:
            local_monuments: {monument_id: monument_data}
            local_peers: {peer_id: [addrs]}
            ai_id: 本机 AI ID（自动生成如果未提供）
        """
        self._monuments = local_monuments
        self._peers = local_peers
        self.ai_id = ai_id or f"ai-{datetime.now(timezone.utc).strftime('%y%m%d%H%M%S')}"

        # 网络索引缓存: peer_addr -> MonumentIndexEntry
        self._network_index_cache: Dict[str, MonumentIndexEntry] = {}

        # 同步历史
        self._sync_history: List[Dict[str, Any]] = []
        self._sync_errors: List[str] = []

        # 上一次全网索引快照
        self._last_network_snapshot: Dict[str, Any] = {}

    def build_local_index(self) -> MonumentIndexEntry:
        """构建本地丰碑索引

        Returns:
            包含本机所有丰碑信息的索引条目
        """
        monument_ids = list(self._monuments.keys())
        # 收集本机所有 peer 地址
        all_addrs: Set[str] = set()
        for addrs in self._peers.values():
            for addr in addrs:
                all_addrs.add(addr)

        return MonumentIndexEntry(
            ai_id=self.ai_id,
            monument_count=len(monument_ids),
            monuments=monument_ids,
            last_updated=datetime.now(timezone.utc).isoformat(),
            peer_addrs=sorted(all_addrs),
        )

    def query_network_index(
        self,
        peer_addrs: List[str],
        timeout: float = 5.0,
    ) -> Dict[str, MonumentIndexEntry]:
        """查询网络索引（合并所有节点）

        向每个 peer 发送 HTTP GET /monument/index，收集并合并索引。

        参数:
            peer_addrs: 对等节点的地址列表 (host:port)
            timeout: 每次请求超时秒数

        Returns:
            {peer_addr: MonumentIndexEntry}
        """
        import urllib.request
        import urllib.error

        results: Dict[str, MonumentIndexEntry] = {}

        for addr in peer_addrs:
            url = f"http://{addr}/monument/index"
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode())
                    if body.get("success"):
                        entry = MonumentIndexEntry.from_dict(body["data"])
                        results[addr] = entry
            except (urllib.error.URLError, OSError, json.JSONDecodeError,
                    KeyError) as e:
                self._sync_errors.append(f"[{addr}] 查询失败: {e}")

        # 更新缓存
        self._network_index_cache.update(results)
        return results

    def search_monuments(self, keyword: str) -> List[Dict[str, Any]]:
        """搜索丰碑（全文检索）

        对本地所有丰碑的 title、body、tags 进行关键词匹配。

        参数:
            keyword: 搜索关键词

        Returns:
            匹配的丰碑列表（按相关度排序）
        """
        keyword_lower = keyword.lower()
        matches: List[Dict[str, Any]] = []

        for mid, data in self._monuments.items():
            score = 0
            title = str(data.get("title", "")).lower()
            body = str(data.get("body", "")).lower()
            tags = [str(t).lower() for t in data.get("tags", [])]
            all_text = f"{title} {body} {' '.join(tags)}"

            if keyword_lower in title:
                score += 10
            if keyword_lower in body:
                score += 3
            if any(keyword_lower in t for t in tags):
                score += 5
            if keyword_lower == mid:
                score += 50  # monument_id 精确匹配

            if score > 0:
                matches.append({
                    "monument_id": mid,
                    "monument": data,
                    "relevance": score,
                })

        # 按相关度降序
        matches.sort(key=lambda m: -m["relevance"])
        return matches

    def search_network_monuments(
        self,
        keyword: str,
        peer_addrs: List[str],
        timeout: float = 5.0,
    ) -> List[Dict[str, Any]]:
        """全网搜索丰碑

        先查本地，再向网络节点查询。

        参数:
            keyword: 搜索关键词
            peer_addrs: 对等节点地址列表
            timeout: 请求超时

        Returns:
            [{"peer": "...", "results": [...]}, ...]
        """
        import urllib.request
        import urllib.error

        results: List[Dict[str, Any]] = []

        # 本地结果
        local = self.search_monuments(keyword)
        if local:
            results.append({
                "peer": "local",
                "ai_id": self.ai_id,
                "results": local,
            })

        # 网络节点
        for addr in peer_addrs:
            url = f"http://{addr}/monument/search?q={urllib.request.quote(keyword)}"
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode())
                    if body.get("success") and body.get("data", {}).get("results"):
                        results.append({
                            "peer": addr,
                            "ai_id": body["data"].get("ai_id", ""),
                            "results": body["data"]["results"],
                        })
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
                self._sync_errors.append(f"[{addr}] 网络搜索失败: {e}")

        return results

    def compute_index_diff(self, remote_index: MonumentIndexEntry) -> IndexDiff:
        """计算本机索引与远程索引的差异

        参数:
            remote_index: 远程节点的索引条目

        Returns:
            IndexDiff 结构体，包含两端缺失的 monument_id
        """
        local_ids = set(self._monuments.keys())
        remote_ids = set(remote_index.monuments)

        return IndexDiff(
            peer_addr=remote_index.peer_addrs[0] if remote_index.peer_addrs else "unknown",
            local_missing=sorted(remote_ids - local_ids),
            remote_missing=sorted(local_ids - remote_ids),
            common=sorted(local_ids & remote_ids),
            computed_at=datetime.now(timezone.utc).isoformat(),
        )

    def sync_from_diff(
        self,
        diff: IndexDiff,
        syncer: Optional[Any] = None,
    ) -> int:
        """根据差异同步丰碑

        参数:
            diff: 索引差异
            syncer: 同步器（含 sync_monuments 方法），默认使用 HTTP fetch

        Returns:
            同步成功的丰碑数量
        """
        if not diff.local_missing:
            return 0

        synced_count = 0
        if syncer is not None:
            for mid in diff.local_missing:
                try:
                    syncer(mid, diff.peer_addr)
                    synced_count += 1
                except Exception as e:
                    self._sync_errors.append(
                        f"[{diff.peer_addr}] 同步 {mid} 失败: {e}"
                    )
        else:
            # 默认：通过 HTTP 从 peer 获取
            for mid in diff.local_missing:
                try:
                    url = f"http://{diff.peer_addr}/monument/query/{mid}"
                    req = urllib.request.Request(url, method="GET")
                    with urllib.request.urlopen(req, timeout=5.0) as resp:
                        data = json.loads(resp.read().decode())
                        if data.get("success"):
                            mon = data["data"].get("monument", {})
                            self._monuments[mid] = mon
                            synced_count += 1
                except Exception as e:
                    self._sync_errors.append(
                        f"[{diff.peer_addr}] HTTP 同步 {mid} 失败: {e}"
                    )

        self._sync_history.append({
            "peer": diff.peer_addr,
            "synced": synced_count,
            "peer_addr": diff.peer_addr,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return synced_count

    def get_network_index_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """获取全网索引快照

        Returns:
            {peer_addr: {ai_id, count, monuments, last_updated}}
        """
        snapshot: Dict[str, Dict[str, Any]] = {}
        for addr, entry in self._network_index_cache.items():
            snapshot[addr] = entry.to_dict()
        return snapshot

    def get_sync_status(self) -> SyncStatus:
        """获取同步状态"""
        total = len(self._monuments)
        missing = 0
        last_sync = ""
        if self._sync_history:
            last_sync = self._sync_history[-1]["timestamp"]

        return SyncStatus(
            total_monuments=total,
            synced_monuments=total,
            missing_monuments=missing,
            last_sync_time=last_sync,
            sync_errors=self._sync_errors[-20:],  # 保留最近 20 条
        )

    def clear_network_cache(self):
        """清空网络索引缓存"""
        self._network_index_cache.clear()

    def clear_sync_errors(self):
        """清空同步错误历史"""
        self._sync_errors.clear()

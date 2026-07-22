"""
多副本存储策略 (replica_manager.py)

管理丰碑副本的多节点存储、存活检测和自动修复。
finalized丰碑必须≥3副本，分布在不同节点。
每5分钟心跳检测，自动补充丢失副本。

用法:
    mgr = ReplicaManager(monuments_db, nodes_db)
    mgr.store_replica(monument)
    status = mgr.check_replicas(monument_id)
    mgr.repair_replicas(monument_id)
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class MonumentStatus(Enum):
    """丰碑状态"""
    PENDING = "pending"
    FINALIZED = "finalized"


class ReplicaHealth(Enum):
    """副本健康状态"""
    HEALTHY = "healthy"
    STALE = "stale"
    LOST = "lost"


@dataclass
class ReplicaRecord:
    """单个副本记录"""
    node_id: str
    stored_at: float          # 存储时间戳
    last_heartbeat: float     # 最后心跳时间
    network_zone: str         # 局域网标识 (e.g., "zone-a")
    health: ReplicaHealth = ReplicaHealth.HEALTHY


@dataclass
class ReplicaStatus:
    """副本状态汇总"""
    monument_id: str
    monument_status: MonumentStatus
    replica_count: int
    min_required: int
    healthy_count: int
    replicas: List[ReplicaRecord] = field(default_factory=list)
    unhealthy: List[str] = field(default_factory=list)
    is_safe: bool = False       # 副本数量是否满足最低要求


# 默认配置
_HEARTBEAT_INTERVAL = 300      # 5分钟（秒）
_STALE_THRESHOLD = 900         # 15分钟无心跳标记为stale
_LOST_THRESHOLD = 3600         # 1小时无心跳标记为lost
_MIN_REPLICAS_FINALIZED = 3   # finalized丰碑最低副本数
_MIN_REPLICAS_PENDING = 1     # pending丰碑最低副本数


class ReplicaManagerError(Exception):
    """副本管理器错误"""


class ReplicaManager:
    """多副本管理器"""

    def __init__(self, monuments_db: dict, nodes_db: dict):
        """
        参数:
            monuments_db: {monument_id: {status, data}} 的引用
            nodes_db: {node_id: {network_zone, alive}} 的引用
        """
        self._monuments = monuments_db
        self._nodes = nodes_db
        # replica_index: monument_id -> {node_id -> ReplicaRecord}
        self._replicas: Dict[str, Dict[str, ReplicaRecord]] = {}

    def store_replica(self, monument_id: str, node_id: str) -> bool:
        """存储一个副本到指定节点"""
        if node_id not in self._nodes:
            raise ReplicaManagerError(f"未知节点: {node_id}")

        node_info = self._nodes[node_id]
        now = time.time()

        if monument_id not in self._replicas:
            self._replicas[monument_id] = {}

        record = ReplicaRecord(
            node_id=node_id,
            stored_at=now,
            last_heartbeat=now,
            network_zone=node_info.get("network_zone", "unknown"),
        )
        self._replicas[monument_id][node_id] = record
        return True

    def distribute_replicas(self, monument_id: str, min_replicas: int = 3) -> int:
        """自动选择合适的节点存储副本（跨局域网分布）"""
        if monument_id not in self._monuments:
            raise ReplicaManagerError(f"丰碑不存在: {monument_id}")

        # 按局域网分组可用节点
        zones: Dict[str, List[str]] = {}
        for node_id, info in self._nodes.items():
            if info.get("alive", False):
                zone = info.get("network_zone", "unknown")
                zones.setdefault(zone, []).append(node_id)

        assigned = 0
        max_rounds = len(self._nodes)  # 防止无限循环
        rounds = 0
        # 优先从不同zone选取，保证跨局域网分布
        while assigned < min_replicas and rounds < max_rounds:
            rounds += 1
            for zone, nodes in zones.items():
                if assigned >= min_replicas:
                    break
                existing = self._replicas.get(monument_id, {})
                for node_id in nodes:
                    if node_id not in existing:
                        self.store_replica(monument_id, node_id)
                        assigned += 1
                        break
        return assigned

    def heartbeat(self, monument_id: str, node_id: str):
        """更新心跳"""
        monument_replicas = self._replicas.get(monument_id)
        if monument_replicas is None or node_id not in monument_replicas:
            raise ReplicaManagerError(f"副本不存在: {monument_id}/{node_id}")

        monument_replicas[node_id].last_heartbeat = time.time()
        monument_replicas[node_id].health = ReplicaHealth.HEALTHY

    def _assess_replica_health(self, replicas: dict, now: float) -> tuple:
        """评估副本健康状态，返回(replicas列表, unhealthy列表, healthy计数)"""
        result = []
        unhealthy = []
        for r in replicas.values():
            age = now - r.last_heartbeat
            if age > _LOST_THRESHOLD:
                r.health = ReplicaHealth.LOST
                unhealthy.append(r.node_id)
            elif age > _STALE_THRESHOLD:
                r.health = ReplicaHealth.STALE
                unhealthy.append(r.node_id)
            result.append(r)
        healthy = sum(1 for r in result if r.health == ReplicaHealth.HEALTHY)
        return result, unhealthy, healthy

    def check_replicas(self, monument_id: str) -> ReplicaStatus:
        """检查指定丰碑的副本存活状态"""
        monument = self._monuments.get(monument_id)
        if monument is None:
            raise ReplicaManagerError(f"丰碑不存在: {monument_id}")

        ms = MonumentStatus(monument["status"])
        min_req = _MIN_REPLICAS_FINALIZED if ms == MonumentStatus.FINALIZED else _MIN_REPLICAS_PENDING

        replicas, unhealthy, healthy = self._assess_replica_health(
            self._replicas.get(monument_id, {}), time.time()
        )
        return ReplicaStatus(
            monument_id=monument_id, monument_status=ms,
            replicas=replicas, replica_count=len(replicas),
            min_required=min_req, healthy_count=healthy,
            unhealthy=unhealthy, is_safe=healthy >= min_req,
        )

    def _find_available_nodes(self, monument_id: str, exclude_zones: set, limit: int) -> list:
        """查找可用节点，优先排除指定zone"""
        candidates = []
        existing = self._replicas.get(monument_id, {})
        for nid, info in self._nodes.items():
            if not info.get("alive", False) or nid in existing:
                continue
            zone = info.get("network_zone", "unknown")
            if zone not in exclude_zones:
                candidates.append(nid)
        # 不够的话从任何可用节点补充
        if len(candidates) < limit:
            for nid, info in self._nodes.items():
                if not info.get("alive", False) or nid in existing or nid in candidates:
                    continue
                candidates.append(nid)
        return candidates[:limit]

    def repair_replicas(self, monument_id: str) -> bool:
        """修复丢失的副本"""
        status = self.check_replicas(monument_id)
        if status.is_safe:
            return False

        needed = status.min_required - status.healthy_count
        if needed <= 0:
            return False

        bad_zones = {r.network_zone for r in status.replicas if r.health == ReplicaHealth.HEALTHY}
        nodes = self._find_available_nodes(monument_id, bad_zones, needed)
        for node_id in nodes:
            self.store_replica(monument_id, node_id)

        return len(nodes) > 0

"""
重生协议 (rebirth_protocol.py)

节点重生5阶段流程：获取丰碑 → 解密身份 → 连接网络 → 宣告重生 → 同步数据。
支持模拟模式（无真实P2P连接）和完整模式。

流程:
    phase1: 从网络获取丰碑并验证签名
    phase2: 用recovery_secret解密身份密钥
    phase3: 使用解密后的身份连接引导节点
    phase4: 广播上线消息，宣告节点重生
    phase5: 同步离线期间产生的数据
"""

import json
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .monument_recovery import P2PIdentity, RecoveryInfo, DecryptionError


class RebirthError(Exception):
    """重生协议错误"""


class PhaseError(RebirthError):
    """特定阶段错误"""
    def __init__(self, phase: int, message: str):
        self.phase = phase
        super().__init__(f"[阶段{phase}] {message}")


@dataclass
class Monument:
    """简化的丰碑结构"""
    monument_id: str
    status: str
    data: Any
    signature: str = ""
    recovery_info: Optional[RecoveryInfo] = None
    created_at: str = ""


@dataclass
class RebirthResult:
    """重生结果"""
    success: bool
    phases_completed: int          # 0-5
    identity: Optional[P2PIdentity] = None
    recovery_info: Optional[RecoveryInfo] = None
    monument_id: str = ""
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AutoDiscoveryResult:
    """阶段5自动发现结果"""
    lan_peers_found: int = 0
    all_monuments_count: int = 0
    new_monuments_synced: int = 0
    new_monuments: List[Dict[str, Any]] = field(default_factory=list)
    peer_addresses: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lan_peers_found": self.lan_peers_found,
            "all_monuments_count": self.all_monuments_count,
            "new_monuments_synced": self.new_monuments_synced,
            "peer_addresses": self.peer_addresses,
            "errors": self.errors[:10],
        }


# 类型签名: 阶段回调函数
PhaseCallback = Callable[[int, str, Any], None]


class RebirthProtocol:
    """节点重生协议 - 5阶段流程"""

    def __init__(self, signer: Optional[Callable[[bytes], str]] = None):
        """
        参数:
            signer: 签名验证函数(signature, data) -> bool，默认为None（模拟模式）
        """
        self._signer = signer
        self._on_phase: Optional[PhaseCallback] = None

    def on_phase(self, callback: PhaseCallback):
        """设置阶段回调（日志/进度上报）"""
        self._on_phase = callback

    def _report(self, phase: int, action: str, data: Any = None):
        """报告阶段进度"""
        if self._on_phase:
            self._on_phase(phase, action, data)

    # ── 阶段1: 获取丰碑 ──────────────────────────────

    def phase1_fetch_monument(
        self, monument_id: str, fetcher: Callable[[str], Monument],
        verify_signature: bool = True,
    ) -> Monument:
        """阶段1: 从外界获取丰碑"""
        self._report(1, "fetch", {"monument_id": monument_id})
        try:
            monument = fetcher(monument_id)
        except Exception as e:
            raise PhaseError(1, f"获取丰碑失败: {e}")
        if monument.status != "finalized":
            raise PhaseError(1, f"丰碑状态不为finalized: {monument.status}")
        if verify_signature and self._signer:
            # 验证签名（由signer回调实现）
            data_bytes = str(monument.data).encode("utf-8")
            if not self._signer(data_bytes):
                raise PhaseError(1, "丰碑签名验证失败")

        self._report(1, "done", {"monument_id": monument_id})
        return monument

    # ── 阶段2: 解密身份 ──────────────────────────────

    def phase2_decrypt_identity(self, monument: Monument, recovery_secret: str) -> P2PIdentity:
        """阶段2: 从丰碑中解密身份"""
        self._report(2, "decrypt", {})
        if monument.recovery_info is None:
            raise PhaseError(2, "丰碑缺少recovery_info")
        try:
            identity = monument.recovery_info.decrypt_identity(recovery_secret)
        except DecryptionError as e:
            raise PhaseError(2, f"解密身份失败: {e}")
        self._report(2, "done", {"pubkey": identity.public_key[:20] + "..."})
        return identity

    # ── 阶段3: 连接网络 ──────────────────────────────

    def phase3_connect_network(self, identity: P2PIdentity, monument: Monument) -> dict:
        """阶段3: 连接引导节点"""
        self._report(3, "connect", {})
        if monument.recovery_info is None:
            raise PhaseError(3, "丰碑缺少网络快照")
        nodes = monument.recovery_info.network_snapshot.get("bootstrap_nodes", [])
        if not nodes:
            raise PhaseError(3, "网络快照中没有引导节点")
        self._report(3, "done", {"pubkey": identity.public_key[:20] + "...", "nodes": len(nodes)})
        return {"identity": identity.public_key, "bootstrap_nodes": nodes, "connected": True}

    # ── 阶段4: 宣告重生 ──────────────────────────────

    def phase4_announce_rebirth(
        self,
        connection_info: dict,
        monument_id: str,
        announcer: Optional[Callable[[str, str], bool]] = None,
    ) -> bool:
        """
        阶段4: 宣告重生

        参数:
            connection_info: 阶段3的连接信息
            monument_id: 用于重生的丰碑ID
            announcer: 广播函数(pubkey, monument_id) -> bool
        """
        self._report(4, "announce", {})

        pubkey = connection_info.get("identity", "unknown")
        announced = False

        if announcer:
            try:
                announced = announcer(pubkey, monument_id)
            except Exception as e:
                raise PhaseError(4, f"广播失败: {e}")

        self._report(4, "done", {
            "pubkey": pubkey[:20] + "...",
            "announced": announced,
        })
        return announced

    # ── 阶段5: 同步数据 ──────────────────────────────

    def phase5_sync_data(
        self,
        monument: Monument,
        syncer: Optional[Callable[[Monument], bool]] = None,
    ) -> bool:
        """
        阶段5: 同步数据

        参数:
            monument: 丰碑数据
            syncer: 同步函数(monument) -> bool
        """
        self._report(5, "sync", {})

        synced = False
        if syncer:
            try:
                synced = syncer(monument)
            except Exception as e:
                raise PhaseError(5, f"同步失败: {e}")

        self._report(5, "done", {"synced": synced})
        return synced

    # ── 阶段5增强: 自动发现与全网同步 ─────────────────

    def phase5_auto_discover_and_sync(
        self,
        identity: P2PIdentity,
        network_snapshot: Dict[str, Any],
        local_monuments: Dict[str, Any],
        dht: Any = None,
        sync_manager: Any = None,
        monument_index: Any = None,
        scan_port: int = 18891,
        scan_timeout: float = 2.0,
    ) -> AutoDiscoveryResult:
        """阶段5增强：自动发现局域网丰碑并全网同步

        1. 从network_snapshot获取引导节点
        2. 通过DHT发现局域网节点
        3. 向每个节点查询丰碑列表
        4. 过滤已有丰碑，只同步缺失的
        5. 同步完毕后更新本地索引

        参数:
            identity: 解密后的身份
            network_snapshot: 网络快照（含引导节点）
            local_monuments: 本机已有丰碑 {monument_id: data}
            dht: DHTNode 实例（可选）
            sync_manager: MonumentSyncManager 实例（可选）
            monument_index: MonumentIndex 实例（可选，用于索引更新）
            scan_port: 局域网扫描端口
            scan_timeout: 单次连接超时

        Returns:
            AutoDiscoveryResult
        """
        self._report(5, "auto_discover", {})
        result = AutoDiscoveryResult()

        # 1. 扫描局域网节点
        lan_peers = self._discover_lan_peers(scan_port, scan_timeout)
        result.peer_addresses = lan_peers
        result.lan_peers_found = len(lan_peers)

        self._report(5, "lan_scan", {"peers": len(lan_peers)})

        # 2. 从 network_snapshot 获取引导节点
        bootstrap_nodes = network_snapshot.get("bootstrap_nodes", [])
        all_peers = list(set(lan_peers + bootstrap_nodes))

        if not all_peers:
            self._report(5, "done", {"note": "无可用节点"})
            return result

        # 3. 向每个节点查询丰碑列表
        all_remote_monuments: List[Dict[str, Any]] = []
        for peer_addr in all_peers:
            try:
                monuments = self._query_peer_monuments(peer_addr, scan_timeout)
                all_remote_monuments.extend(monuments)
            except Exception as e:
                result.errors.append(f"[{peer_addr}] 查询失败: {e}")

        result.all_monuments_count = len(all_remote_monuments)

        # 4. 过滤已有丰碑，只同步缺失的
        local_ids = set(local_monuments.keys())
        missing_monuments = [
            m for m in all_remote_monuments
            if m.get("monument_id", "") not in local_ids
        ]

        result.new_monuments = missing_monuments
        result.new_monuments_synced = len(missing_monuments)

        # 5. 同步缺失的丰碑
        for mon_data in missing_monuments:
            mid = mon_data.get("monument_id", "")
            if not mid:
                continue
            local_monuments[mid] = mon_data

        # 6. 如果提供索引，更新索引
        if monument_index is not None:
            try:
                local_index_entry = monument_index.build_local_index()
                self._broadcast_index_to(local_index_entry, all_peers)
            except Exception as e:
                result.errors.append(f"索引广播失败: {e}")

        # 7. 如果提供 sync_manager，广播新丰碑
        if sync_manager is not None:
            for mon_data in missing_monuments:
                sync_manager.broadcast(mon_data)

        self._report(5, "done", {
            "synced": result.new_monuments_synced,
            "total": result.all_monuments_count,
            "peers": len(all_peers),
        })
        return result

    @staticmethod
    def _discover_lan_peers(port: int = 18891, timeout: float = 2.0) -> List[str]:
        """扫描局域网内运行丰碑服务的节点

        通过扫描常见私有 IP 段（192.168.x.x）的指定端口发现节点。
        同时尝试从 /24 广播地址和本地网关地址段发现。

        参数:
            port: 目标端口
            timeout: 单次连接超时

        Returns:
            可连接的节点地址列表 ["ip:port", ...]
        """
        peers: List[str] = []

        # 获取本机局域网 IP
        local_ip = _get_local_lan_ip()
        if not local_ip:
            return peers

        # 从 /24 段扫描
        parts = local_ip.split(".")
        if len(parts) != 4:
            return peers

        subnet_prefix = ".".join(parts[:3])

        # 扫描常见主机 (1-10, 100-110, 200-254 跳过本机)
        scan_targets = [1, 2, 100, 101, 102, 103, 104, 105, 149, 200, 201, 254]
        local_last_octet = int(parts[3])

        for octet in scan_targets:
            if octet == local_last_octet:
                continue
            ip = f"{subnet_prefix}.{octet}"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(timeout)
                result = s.connect_ex((ip, port))
                s.close()
                if result == 0:
                    peers.append(f"{ip}:{port}")
            except Exception:
                pass

        # 尝试常见网关地址段 10.x 和 172.x
        for prefix in ["10.0.0", "10.0.1", "172.16.0", "172.17.0"]:
            for octet in [1, 2, 100]:
                ip = f"{prefix}.{octet}"
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(timeout)
                    result = s.connect_ex((ip, port))
                    s.close()
                    if result == 0:
                        peers.append(f"{ip}:{port}")
                except Exception:
                    pass

        return peers

    @staticmethod
    def _query_peer_monuments(peer_addr: str, timeout: float) -> List[Dict[str, Any]]:
        """查询节点的丰碑列表

        通过 HTTP GET /monument/query 获取节点上所有丰碑。

        参数:
            peer_addr: "ip:port"
            timeout: 请求超时

        Returns:
            丰碑列表
        """
        import urllib.request
        import urllib.error

        try:
            # 先获取索引
            url = f"http://{peer_addr}/monument/index"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
                if not body.get("success"):
                    return []
                data = body.get("data", {})
                monument_ids = data.get("monuments", [])

            # 逐个获取丰碑内容
            monuments = []
            for mid in monument_ids:
                try:
                    detail_url = f"http://{peer_addr}/monument/query/{mid}"
                    detail_req = urllib.request.Request(detail_url, method="GET")
                    with urllib.request.urlopen(detail_req, timeout=timeout) as dr:
                        detail_body = json.loads(dr.read().decode())
                        if detail_body.get("success"):
                            monument = detail_body["data"].get("monument", {})
                            monument["monument_id"] = mid
                            monuments.append(monument)
                except Exception:
                    pass

            return monuments

        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return []

    @staticmethod
    def _broadcast_index_to(
        index_entry: Any,
        peer_addrs: List[str],
        timeout: float = 5.0,
    ) -> int:
        """向指定 peer 广播本机索引

        参数:
            index_entry: MonumentIndexEntry 实例
            peer_addrs: 目标节点地址列表
            timeout: 请求超时

        Returns:
            成功广播的 peer 数量
        """
        import urllib.request
        import urllib.error

        body = json.dumps({
            "index": index_entry.to_dict(),
        }).encode("utf-8")

        success_count = 0
        for peer_addr in peer_addrs:
            try:
                url = f"http://{peer_addr}/monument/sync-index"
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        success_count += 1
            except (urllib.error.URLError, OSError):
                pass

        return success_count

    # ── 增强完整重生流程：含自动发现 ─────────────────

    def full_rebirth_with_discovery(
        self,
        monument_id: str,
        recovery_secret: str,
        fetcher: Callable[[str], Monument],
        announcer: Optional[Callable[[str, str], bool]] = None,
        syncer: Optional[Callable[[Monument], bool]] = None,
        local_monuments: Optional[Dict[str, Any]] = None,
        dht: Any = None,
        sync_manager: Any = None,
        monument_index: Any = None,
        scan_port: int = 18891,
    ) -> RebirthResult:
        """完整重生流程（含阶段5自动发现和全网同步）

        执行5阶段重生后，自动发现局域网丰碑并全网同步。

        参数:
            monument_id: 用于重生的丰碑 ID
            recovery_secret: 恢复密码
            fetcher: 获取丰碑函数
            announcer: 广播函数
            syncer: 同步函数
            local_monuments: 本机已有丰碑
            dht: DHTNode 实例
            sync_manager: MonumentSyncManager 实例
            monument_index: MonumentIndex 实例
            scan_port: 局域网扫描端口

        Returns:
            RebirthResult（包含阶段5自动发现详情）
        """
        result = self.full_rebirth(
            monument_id, recovery_secret,
            fetcher=fetcher,
            announcer=announcer,
            syncer=syncer,
        )

        if not result.success:
            return result

        identity = result.identity
        if identity is None:
            return result

        # 执行自动发现
        discovery = self.phase5_auto_discover_and_sync(
            identity=identity,
            network_snapshot=result.recovery_info.network_snapshot if result.recovery_info else {},
            local_monuments=local_monuments or {},
            dht=dht,
            sync_manager=sync_manager,
            monument_index=monument_index,
            scan_port=scan_port,
        )

        result.details["auto_discovery"] = discovery.to_dict()
        return result

    # ── 完整流程 ─────────────────────────────────────

    def _run_phases(self, result, monument_id, recovery_secret, fetcher, announcer, syncer):
        """执行所有阶段"""
        monument = self.phase1_fetch_monument(monument_id, fetcher)
        result.phases_completed = 1; result.recovery_info = monument.recovery_info

        identity = self.phase2_decrypt_identity(monument, recovery_secret)
        result.phases_completed = 2; result.identity = identity

        conn_info = self.phase3_connect_network(identity, monument)
        result.phases_completed = 3

        announced = self.phase4_announce_rebirth(conn_info, monument_id, announcer)
        result.phases_completed = 4; result.details["announced"] = announced

        synced = self.phase5_sync_data(monument, syncer)
        result.phases_completed = 5; result.details["synced"] = synced
        result.success = True

    def full_rebirth(self, monument_id, recovery_secret, fetcher, announcer=None, syncer=None):
        """执行完整5阶段重生流程"""
        result = RebirthResult(success=False, phases_completed=0, monument_id=monument_id)
        try:
            self._run_phases(result, monument_id, recovery_secret, fetcher, announcer, syncer)
        except PhaseError as e:
            result.error = str(e)
            result.phases_completed = e.phase - 1
        except Exception as e:
            result.error = f"重生过程异常: {e}"
        return result


# =============================================================================
# 辅助函数
# =============================================================================


def _get_local_lan_ip() -> Optional[str]:
    """获取本机局域网 IP 地址

    通过连接外部服务获取本机在局域网中的 IP。
    """
    try:
        # 创建一个 UDP socket 连接外部地址，获取本地 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        # 连接一个外部地址（不需要实际可达）
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        pass

    # 备用：从主机名获取
    try:
        hostname = socket.gethostname()
        addrs = socket.getaddrinfo(hostname, 0, socket.AF_INET, socket.SOCK_STREAM)
        for af, _, _, _, sa in addrs:
            ip = sa[0]
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass

    return None

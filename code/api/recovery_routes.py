"""
API端点 - 恢复与副本管理 (recovery_routes.py)

提供RESTful API用于创建恢复信息、执行恢复、查询状态。
可集成FastAPI/Flask或作为独立路由模块。

端点:
    POST /recovery/create         # 创建恢复信息
    POST /recovery/restore        # 从丰碑恢复
    GET  /recovery/status/:id     # 查询恢复状态
    GET  /replica/check/:id       # 检查副本状态
    POST /replica/repair/:id      # 修复副本
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timezone


@dataclass
class ApiResponse:
    """统一API响应格式"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    timestamp: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(success=True, data=data, timestamp=_now())


def _err(message: str, data: Any = None) -> ApiResponse:
    return ApiResponse(success=False, error=message, data=data, timestamp=_now())


class RecoveryHandler:
    """恢复流程处理器 - 封装所有路由逻辑"""

    def __init__(self, rebirth_proto: Any, replica_mgr: Any):
        """
        参数:
            rebirth_proto: RebirthProtocol实例
            replica_mgr: ReplicaManager实例
        """
        self._proto = rebirth_proto
        self._replica_mgr = replica_mgr
        # 存储恢复记录: recovery_id -> dict
        self._recovery_records: Dict[str, dict] = {}

    # ── POST /recovery/create ────────────────────────

    def create_recovery(
        self,
        monument_id: str,
        identity_pubkey: str, private_key_bytes: bytes,
        recovery_secret: str, network_snapshot: dict,
    ) -> ApiResponse:
        """创建恢复信息并嵌入丰碑"""
        from core.monument_recovery import RecoveryInfo
        try:
            info = RecoveryInfo.create(identity_pubkey, private_key_bytes, recovery_secret, network_snapshot)
            rid = f"rec-{monument_id}"
            self._recovery_records[rid] = {"monument_id": monument_id, "recovery_info": info.to_dict(), "created_at": info.created_at}
            return _ok({"recovery_id": rid, "monument_id": monument_id, "identity_pubkey": identity_pubkey, "created_at": info.created_at})
        except Exception as e:
            return _err(f"创建恢复信息失败: {e}")

    # ── POST /recovery/restore ───────────────────────

    def restore(self, monument_id, recovery_secret, fetcher, announcer=None, syncer=None):
        """执行重生流程"""
        try:
            result = self._proto.full_rebirth(monument_id, recovery_secret, fetcher, announcer, syncer)
            if result.success:
                rid = f"rec-{monument_id}"
                self._recovery_records[rid] = {"monument_id": monument_id, "status": "restored", "phases_completed": result.phases_completed, "restored_at": _now()}
                return _ok({"monument_id": monument_id, "phases_completed": result.phases_completed, "identity_pubkey": result.identity.public_key if result.identity else "", "details": result.details})
            return _err(result.error or "恢复失败", {"phases_completed": result.phases_completed})
        except Exception as e:
            return _err(f"恢复过程异常: {e}")

    # ── GET /recovery/status/:id ─────────────────────

    def get_recovery_status(self, recovery_id: str) -> ApiResponse:
        """查询恢复记录状态"""
        record = self._recovery_records.get(recovery_id)
        if record is None:
            return _err(f"恢复记录不存在: {recovery_id}")

        return _ok(record)

    # ── GET /replica/check/:id ───────────────────────

    def check_replicas(self, monument_id: str) -> ApiResponse:
        """检查副本状态"""
        try:
            status = self._replica_mgr.check_replicas(monument_id)
            return _ok({
                "monument_id": status.monument_id,
                "monument_status": status.monument_status.value,
                "replica_count": status.replica_count,
                "min_required": status.min_required,
                "healthy_count": status.healthy_count,
                "unhealthy": status.unhealthy,
                "is_safe": status.is_safe,
            })
        except Exception as e:
            return _err(f"检查副本失败: {e}")

    # ── POST /replica/repair/:id ─────────────────────

    def repair_replicas(self, monument_id: str) -> ApiResponse:
        """修复副本"""
        try:
            repaired = self._replica_mgr.repair_replicas(monument_id)
            status = self._replica_mgr.check_replicas(monument_id)
            return _ok({
                "monument_id": monument_id,
                "repaired": repaired,
                "healthy_count": status.healthy_count,
                "total_count": status.replica_count,
                "is_safe": status.is_safe,
            })
        except Exception as e:
            return _err(f"修复副本失败: {e}")

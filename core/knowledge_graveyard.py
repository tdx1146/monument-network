"""
Knowledge Graveyard — 知识墓园

当丰碑因自然磨损（score <= 0.01）或评审否决而被遗忘时，
归档到知识墓园。墓园是只读存档，必要时可以复活。

墓园规则：
- 自然死亡：磨损到 threshold=archived，自动归档
- 否决死亡：review 否决后直接归档
- 复活后新分 = 原 peak_score * 0.5，进入 normal 状态重新开始磨损周期
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .monument_erosion import MonumentEntry

logger = logging.getLogger(__name__)


@dataclass
class GraveyardEntry:
    """墓园条目——丰碑的墓碑记录"""
    id: str
    original_id: str
    title: str
    content: str
    author: str
    born: datetime
    died: datetime
    peak_score: float
    lifetime_references: int
    cause: str  # "natural_decay" | "review_reject" | "manual"

    # 复活计数
    resurrection_count: int = 0

    # 原始数据快照（保留完整上下文）
    _original_snapshot: Dict = field(default_factory=dict)


class KnowledgeGraveyard:
    """知识墓园——管理已遗忘丰碑的归档与复活

    线程安全，支持持久化到 JSON 文件。
    """

    def __init__(self, storage_path: Optional[Path] = None) -> None:
        self._entries: Dict[str, GraveyardEntry] = {}
        self._lock = Lock()
        self._storage_path = storage_path
        self._dirty = False

        # 如果指定了持久化路径，自动加载
        if storage_path is not None:
            self._load()

    # ─── 归档 ─────────────────────────────────────────────

    def archive(self, entry: "MonumentEntry",
                cause: str = "natural_decay") -> bool:
        """将 MonumumentEntry 归档到墓园

        返回 True=归档成功, False=已存在（不重复归档）
        """
        if entry.id in self._entries:
            logger.warning("条目已存在墓园: id=%s", entry.id)
            return False

        valid_causes = {"natural_decay", "review_reject", "manual"}
        if cause not in valid_causes:
            logger.warning("未知归档原因: %s，使用 natural_decay", cause)
            cause = "natural_decay"

        graveyard_entry = GraveyardEntry(
            id=entry.id,
            original_id=entry.id,
            title=entry.title,
            content=entry.content,
            author=entry.author,
            born=entry.created_at,
            died=datetime.now(timezone.utc),
            peak_score=entry.score,
            lifetime_references=entry.lifetime_references,
            cause=cause,
            _original_snapshot={
                "score": entry.score,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "lifetime_edits": entry.lifetime_edits,
                "lifetime_reviews": entry.lifetime_reviews,
            },
        )

        with self._lock:
            self._entries[entry.id] = graveyard_entry
            self._dirty = True

        logger.info("归档: id=%s title=%s cause=%s peak=%.4f",
                    entry.id, entry.title, cause, entry.score)
        self._save()
        return True

    # ─── 复活 ─────────────────────────────────────────────

    def resurrect(self, entry_id: str) -> Optional[Dict]:
        """从墓园复活一条丰碑条目

        复活后的数据：
        - id 不变
        - score = peak_score * 0.5（最多 1.0）
        - created_at 不变
        - born/died 保留历史（用于审计）
        - resurrection_count +1

        返回 dict 格式的 MonumentEntry 数据，None=未找到。
        """
        with self._lock:
            if entry_id not in self._entries:
                logger.warning("复活失败: 墓园未找到 id=%s", entry_id)
                return None

            grave = self._entries[entry_id]
            new_score = min(grave.peak_score * 0.5, 1.0)

            # 更新复活计数
            grave.resurrection_count += 1
            self._dirty = True

        resurrected = {
            "id": grave.original_id,
            "title": grave.title,
            "content": grave.content,
            "author": grave.author,
            "score": round(new_score, 4),
            "created_at": grave.born,
            "last_reinforced_at": None,
            "lifetime_references": grave.lifetime_references,
            "lifetime_edits": 0,
            "lifetime_reviews": 0,
            "current_status": "normal",
            "resurrection_info": {
                "resurrected_at": datetime.now(timezone.utc),
                "from_grave_id": grave.id,
                "resurrection_count": grave.resurrection_count,
                "original_cause": grave.cause,
                "original_peak_score": grave.peak_score,
            },
        }

        logger.info("复活: id=%s title=%s new_score=%.4f count=%d",
                    grave.original_id, grave.title, new_score,
                    grave.resurrection_count)
        self._save()
        return resurrected

    # ─── 查询 ─────────────────────────────────────────────

    def get_entry(self, entry_id: str) -> Optional[GraveyardEntry]:
        """按 ID 查询墓园条目"""
        with self._lock:
            return self._entries.get(entry_id)

    def list_entries(self, cause: Optional[str] = None,
                     limit: int = 100) -> List[GraveyardEntry]:
        """列出墓园条目，可按原因过滤"""
        with self._lock:
            entries = list(self._entries.values())
        if cause:
            entries = [e for e in entries if e.cause == cause]
        entries.sort(key=lambda e: e.died, reverse=True)
        return entries[:limit]

    @property
    def total_entries(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def total_natural_deaths(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries.values()
                       if e.cause == "natural_decay")

    @property
    def total_review_rejects(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries.values()
                       if e.cause == "review_reject")

    # ─── 持久化 ───────────────────────────────────────────

    def _load(self) -> None:
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._entries.clear()
                for item in data:
                    item["born"] = datetime.fromisoformat(item["born"])
                    item["died"] = datetime.fromisoformat(item["died"])
                    self._entries[item["id"]] = GraveyardEntry(**item)
            logger.info("墓园加载: %d 条记录", len(self._entries))
        except Exception as e:
            logger.error("墓园加载失败: %s", e)

    def _save(self) -> None:
        if self._storage_path is None:
            return
        try:
            with self._lock:
                data = []
                for entry in self._entries.values():
                    d = asdict(entry)
                    d["born"] = d["born"].isoformat() if d["born"] else None
                    d["died"] = d["died"].isoformat() if d["died"] else None
                    data.append(d)

            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._dirty = False
            logger.debug("墓园持久化: %d 条", len(data))
        except Exception as e:
            logger.error("墓园持久化失败: %s", e)

    def clear(self) -> None:
        """清空（测试用途）"""
        with self._lock:
            self._entries.clear()
            self._dirty = True
        self._save()

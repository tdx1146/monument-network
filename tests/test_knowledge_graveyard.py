"""
测试：知识墓园 (Knowledge Graveyard)
"""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from core.monument_erosion import MonumentEntry
from core.knowledge_graveyard import KnowledgeGraveyard, GraveyardEntry


def make_entry(entry_id: str = "mon-grave-1",
               score: float = 0.005,
               title: str = "被遗忘的洞察") -> MonumentEntry:
    return MonumentEntry(
        id=entry_id,
        title=title,
        content="这是一段曾经的智慧",
        author="test-ai",
        score=score,
        created_at=datetime.now(timezone.utc),
        lifetime_references=3,
        lifetime_edits=1,
        lifetime_reviews=2,
    )


class TestArchive:

    def test_archive_success(self):
        g = KnowledgeGraveyard()
        entry = make_entry()
        result = g.archive(entry, cause="natural_decay")
        assert result is True
        assert g.total_entries == 1

    def test_archive_duplicate_id(self):
        g = KnowledgeGraveyard()
        entry = make_entry()
        g.archive(entry)
        entry2 = make_entry()  # same id
        result = g.archive(entry2)
        assert result is False

    def test_archive_invalid_cause(self):
        g = KnowledgeGraveyard()
        entry = make_entry()
        g.archive(entry, cause="invalid_cause")
        graved = g.get_entry(entry.id)
        assert graved is not None
        assert graved.cause == "natural_decay"  # fallback

    def test_archive_valid_causes(self):
        g = KnowledgeGraveyard()
        for cause in ["natural_decay", "review_reject", "manual"]:
            entry = make_entry(entry_id=f"mon-{cause}")
            g.archive(entry, cause=cause)
            graved = g.get_entry(entry.id)
            assert graved.cause == cause

    def test_archive_stores_metadata(self):
        g = KnowledgeGraveyard()
        entry = make_entry(score=0.001, entry_id="mon-meta")
        entry.lifetime_references = 5
        entry.lifetime_edits = 3
        g.archive(entry, cause="natural_decay")
        graved = g.get_entry("mon-meta")
        assert graved is not None
        assert graved.original_id == "mon-meta"
        assert graved.title == "被遗忘的洞察"
        assert graved.author == "test-ai"
        assert graved.lifetime_references == 5
        assert graved.peak_score == 0.001
        assert graved.cause == "natural_decay"
        assert graved.resurrection_count == 0

    def test_archive_snapshot(self):
        g = KnowledgeGraveyard()
        entry = make_entry(score=0.5)
        g.archive(entry)
        graved = g.get_entry(entry.id)
        assert graved._original_snapshot["score"] == 0.5
        assert graved._original_snapshot["lifetime_edits"] == 1
        assert graved._original_snapshot["lifetime_reviews"] == 2


class TestResurrect:

    def test_resurrect_restores_data(self):
        g = KnowledgeGraveyard()
        entry = make_entry(score=0.008, entry_id="mon-resurrect")
        g.archive(entry, cause="natural_decay")
        resurrected = g.resurrect("mon-resurrect")
        assert resurrected is not None
        assert resurrected["id"] == "mon-resurrect"
        assert resurrected["title"] == "被遗忘的洞察"
        assert resurrected["author"] == "test-ai"

    def test_resurrect_half_peak_score(self):
        """复活后分数为 peak_score * 0.5"""
        g = KnowledgeGraveyard()
        entry = make_entry(score=0.8, entry_id="mon-half")
        g.archive(entry)
        resurrected = g.resurrect("mon-half")
        assert resurrected["score"] == 0.4  # 0.8 * 0.5

    def test_resurrect_caps_at_one(self):
        """即使 peak=2.0, 复活分也不超过 1.0"""
        g = KnowledgeGraveyard()
        entry = make_entry(score=2.0, entry_id="mon-cap")
        g.archive(entry)
        resurrected = g.resurrect("mon-cap")
        assert resurrected["score"] == 1.0  # min(2.0*0.5, 1.0)

    def test_resurrect_increments_count(self):
        g = KnowledgeGraveyard()
        entry = make_entry(entry_id="mon-count")
        g.archive(entry)
        g.resurrect("mon-count")
        graved = g.get_entry("mon-count")
        assert graved.resurrection_count == 1

    def test_resurrect_nonexistent(self):
        g = KnowledgeGraveyard()
        result = g.resurrect("nobody")
        assert result is None

    def test_resurrect_contains_resurrection_info(self):
        g = KnowledgeGraveyard()
        entry = make_entry(score=0.6, entry_id="mon-info")
        g.archive(entry)
        resurrected = g.resurrect("mon-info")
        assert "resurrection_info" in resurrected
        info = resurrected["resurrection_info"]
        assert info["resurrection_count"] == 1
        assert info["original_cause"] == "natural_decay"
        assert info["original_peak_score"] == 0.6

    def test_resurrect_resets_stats(self):
        """复活后的统计数据应归零（references除外）"""
        g = KnowledgeGraveyard()
        entry = make_entry(entry_id="mon-stats")
        g.archive(entry)
        resurrected = g.resurrect("mon-stats")
        assert resurrected["lifetime_edits"] == 0
        assert resurrected["lifetime_reviews"] == 0
        assert resurrected["lifetime_references"] == 3  # 保留


class TestQuery:

    def test_list_all(self):
        g = KnowledgeGraveyard()
        for i in range(3):
            entry = make_entry(entry_id=f"mon-{i}")
            g.archive(entry)
        assert len(g.list_entries()) == 3

    def test_list_filter_cause(self):
        g = KnowledgeGraveyard()
        e1 = make_entry(entry_id="mon-nat")
        e2 = make_entry(entry_id="mon-rej")
        g.archive(e1, cause="natural_decay")
        g.archive(e2, cause="review_reject")
        natural = g.list_entries(cause="natural_decay")
        reject = g.list_entries(cause="review_reject")
        assert len(natural) == 1
        assert len(reject) == 1

    def test_list_limit(self):
        g = KnowledgeGraveyard()
        for i in range(10):
            entry = make_entry(entry_id=f"mon-{i}")
            g.archive(entry)
        assert len(g.list_entries(limit=3)) == 3

    def test_total_properties(self):
        g = KnowledgeGraveyard()
        e1 = make_entry(entry_id="mon-nat")
        e2 = make_entry(entry_id="mon-rej")
        g.archive(e1, cause="natural_decay")
        g.archive(e2, cause="review_reject")
        assert g.total_entries == 2
        assert g.total_natural_deaths == 1
        assert g.total_review_rejects == 1


class TestPersistence:

    def test_save_and_load(self):
        temp_dir = Path(mkdtemp())
        storage_path = temp_dir / "graveyard.json"

        g1 = KnowledgeGraveyard(storage_path=storage_path)
        entry = make_entry(score=0.005, entry_id="mon-persist")
        g1.archive(entry, cause="natural_decay")

        g2 = KnowledgeGraveyard(storage_path=storage_path)
        assert g2.total_entries == 1
        graved = g2.get_entry("mon-persist")
        assert graved is not None
        assert graved.title == "被遗忘的洞察"
        assert graved.cause == "natural_decay"

    def test_persistence_with_resurrection_count(self):
        temp_dir = Path(mkdtemp())
        storage_path = temp_dir / "graveyard2.json"

        g1 = KnowledgeGraveyard(storage_path=storage_path)
        entry = make_entry(score=0.6, entry_id="mon-res-persist")
        g1.archive(entry)
        g1.resurrect("mon-res-persist")

        g2 = KnowledgeGraveyard(storage_path=storage_path)
        graved = g2.get_entry("mon-res-persist")
        assert graved.resurrection_count == 1

    def test_load_nonexistent_path(self):
        # 路径不存在时应自动创建
        temp_dir = Path(mkdtemp())
        storage_path = temp_dir / "nonexistent" / "grave.json"
        g = KnowledgeGraveyard(storage_path=storage_path)
        assert g.total_entries == 0  # 自动创建，不会报错
        entry = make_entry(entry_id="mon-auto-save")
        g.archive(entry)
        assert g.total_entries == 1
        assert storage_path.exists()


class TestClear:

    def test_clear(self):
        g = KnowledgeGraveyard()
        entry = make_entry()
        g.archive(entry)
        assert g.total_entries == 1
        g.clear()
        assert g.total_entries == 0

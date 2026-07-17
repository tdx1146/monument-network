"""
测试：IndividualRepository 持久化
"""

import pytest

from core.individual_monument import IndividualMonument
from db.database import init_db, close_db, get_connection
from db.individual_repo import IndividualRepository


@pytest.fixture(autouse=True)
def db():
    """每个测试前重建表，保证隔离。"""
    init_db()
    conn = get_connection()
    conn.execute("DELETE FROM individual_monuments")
    conn.commit()
    yield
    close_db()


class TestIndividualRepository:

    def test_create_and_get(self):
        m = IndividualMonument("ai-1")
        m.write_draft("测试数据")
        repo = IndividualRepository()
        row_id = repo.create(m)
        assert row_id > 0

        loaded = repo.get_by_ai_id("ai-1")
        assert loaded is not None
        assert loaded.data["identity"]["ai_id"] == "ai-1"
        assert len(loaded.data["monuments"]["drafts"]) == 1

    def test_get_nonexistent(self):
        repo = IndividualRepository()
        assert repo.get_by_ai_id("nobody") is None

    def test_update(self):
        m = IndividualMonument("ai-update")
        repo = IndividualRepository()
        repo.create(m)

        m.write_draft("新草稿")
        ok = repo.update(m)
        assert ok is True

        loaded = repo.get_by_ai_id("ai-update")
        assert loaded is not None
        assert len(loaded.data["monuments"]["drafts"]) == 1

    def test_list_all(self):
        repo = IndividualRepository()
        for i in range(3):
            m = IndividualMonument(f"ai-{i}")
            m.write_draft(f"draft-{i}")
            repo.create(m)

        items = repo.list_all()
        assert len(items) == 3
        ai_ids = [item["ai_id"] for item in items]
        assert "ai-0" in ai_ids
        assert "ai-1" in ai_ids
        assert "ai-2" in ai_ids

    def test_create_duplicate_ai_id(self):
        m = IndividualMonument("dup")
        repo = IndividualRepository()
        repo.create(m)
        with pytest.raises(ValueError, match="already exists"):
            repo.create(IndividualMonument("dup"))

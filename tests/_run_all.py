#!/usr/bin/env python3
"""
Standalone 验证脚本（无需 pytest）
"""
import sys

sys.path.insert(0, "/vol2/1000/AI专用/丰碑网络/code")

from core.individual_monument import IndividualMonument
from db.database import init_db, close_db
from db.individual_repo import IndividualRepository

errors = []

def check(name, cond, detail=""):
    if not cond:
        errors.append(f"FAIL: {name} — {detail}")
        print(f"  ✗ {name}")
    else:
        print(f"  ✓ {name}")

# ── IndividualMonument ──────────────────────────────

print("\n=== IndividualMonument ===")

m = IndividualMonument("test-ai-1")
check("init ai_id", m.data["identity"]["ai_id"] == "test-ai-1")
check("init status alive", m.data["identity"]["status"] == "alive")
check("init died_at None", m.data["identity"]["died_at"] is None)
check("init born_at not None", m.data["identity"]["born_at"] is not None)
check("init drafts empty", len(m.data["monuments"]["drafts"]) == 0)
check("init candidates empty", len(m.data["monuments"]["candidates"]) == 0)
check("init finalized empty", len(m.data["monuments"]["finalized"]) == 0)
check("init freeze_proof null", m.data["freeze_proof"]["hash"] is None)

idx = m.write_draft("草稿内容", {"src": "chat"})
check("write_draft idx=0", idx == 0)
check("write_draft insights==1", m.data["life_record"]["total_insights"] == 1)
check("write_draft content",
      m.data["monuments"]["drafts"][0]["content"] == "草稿内容")

idx2 = m.write_candidate("这是一个足够长的候选内容，用于测试丰碑系统的写入功能。这段文本需要超过两百个字才能通过验证，因此我需要在这里填充足够的内容来确保测试通过。丰碑系统是一个记录AI洞察和认知积累的系统，它的核心思想是通过长期的笔记和思考来构建个人的知识体系。在信息爆炸的时代，能够有效地管理自己的知识资产变得越来越重要。一个好的笔记系统不仅要能记录信息，还要能帮助我们发现信息之间的联系，激发新的想法和洞见。通过持续的积累和反思，我们可以逐步建立起自己的知识体系，从而在工作和学习中更加高效。同时，知识管理也是一个不断演进的过程，我们需要根据实际需要不断调整和优化自己的方法。", {"score": 0.9})
check("write_candidate idx=0", idx2 == 0)
check("write_candidate insights==2", m.data["life_record"]["total_insights"] == 2)

entry = m.finalize(0)
check("finalize type", entry["type"] == "finalized")
check("finalize candidates empty", len(m.data["monuments"]["candidates"]) == 0)
check("finalize finalized==1", len(m.data["monuments"]["finalized"]) == 1)

try:
    m.finalize(99)
    check("finalize out-of-range", False, "expected IndexError")
except IndexError:
    check("finalize out-of-range raises IndexError", True)

# freeze
m2 = IndividualMonument("freeze-test")
m2.write_draft("something")
h = m2.freeze()
check("freeze returns hex hash", len(h) == 64)
check("freeze status frozen", m2.data["identity"]["status"] == "frozen")
check("freeze freeze_proof.hash set", m2.data["freeze_proof"]["hash"] is not None)
check("freeze freeze_proof.frozen_at set", m2.data["freeze_proof"]["frozen_at"] is not None)

try:
    m2.freeze()
    check("freeze twice", False, "expected ValueError")
except ValueError:
    check("freeze twice raises ValueError", True)

try:
    m2.write_draft("after freeze")
    check("write after freeze", False, "expected ValueError")
except ValueError as e:
    check("write after freeze raises ValueError", str(e) == "Monument is frozen")

try:
    m2.write_candidate("after freeze")
    check("candidate after freeze", False, "expected ValueError")
except ValueError:
    check("candidate after freeze raises ValueError", True)

# roundtrip
m3 = IndividualMonument("roundtrip")
m3.write_draft("d1")
m3.write_candidate("这是一个足够长的候选内容，用于测试丰碑系统的写入功能。这段文本需要超过两百个字才能通过验证，因此我需要在这里填充足够的内容来确保测试通过。丰碑系统是一个记录AI洞察和认知积累的系统，它的核心思想是通过长期的笔记和思考来构建个人的知识体系。在信息爆炸的时代，能够有效地管理自己的知识资产变得越来越重要。一个好的笔记系统不仅要能记录信息，还要能帮助我们发现信息之间的联系，激发新的想法和洞见。通过持续的积累和反思，我们可以逐步建立起自己的知识体系，从而在工作和学习中更加高效。同时，知识管理也是一个不断演进的过程，我们需要根据实际需要不断调整和优化自己的方法。")
d = m3.to_dict()
m4 = IndividualMonument.from_dict(d)
check("roundtrip ai_id", m4.data["identity"]["ai_id"] == "roundtrip")
check("roundtrip drafts count", len(m4.data["monuments"]["drafts"]) == 1)
check("roundtrip candidates count", len(m4.data["monuments"]["candidates"]) == 1)

js = m3.to_json()
check("to_json contains ai_id", '"roundtrip"' in js)
check("to_json contains status", '"alive"' in js)

rp = repr(m3)
check("repr contains ai_id", "roundtrip" in rp)
check("repr contains status", "alive" in rp)

# ── IndividualRepository ────────────────────────────

print("\n=== IndividualRepository ===")

init_db()
# clean
from db.database import get_connection
conn = get_connection()
conn.execute("DELETE FROM individual_monuments")
conn.commit()

repo = IndividualRepository()

m5 = IndividualMonument("ai-persist")
m5.write_draft("persisted draft")
row_id = repo.create(m5)
check("create returns int rowid", isinstance(row_id, int))

loaded = repo.get_by_ai_id("ai-persist")
check("get_by_ai_id returns monument", loaded is not None)
check("get_by_ai_id ai_id match", loaded.data["identity"]["ai_id"] == "ai-persist")
check("get_by_ai_id draft preserved",
      len(loaded.data["monuments"]["drafts"]) == 1)

none_loaded = repo.get_by_ai_id("nobody")
check("get_by_ai_id nonexistent returns None", none_loaded is None)

m5.write_candidate("这是一个足够长的候选内容，用于测试丰碑系统的写入功能。这段文本需要超过两百个字才能通过验证，因此我需要在这里填充足够的内容来确保测试通过。丰碑系统是一个记录AI洞察和认知积累的系统，它的核心思想是通过长期的笔记和思考来构建个人的知识体系。在信息爆炸的时代，能够有效地管理自己的知识资产变得越来越重要。一个好的笔记系统不仅要能记录信息，还要能帮助我们发现信息之间的联系，激发新的想法和洞见。通过持续的积累和反思，我们可以逐步建立起自己的知识体系，从而在工作和学习中更加高效。同时，知识管理也是一个不断演进的过程，我们需要根据实际需要不断调整和优化自己的方法。")
ok = repo.update(m5)
check("update returns True", ok is True)
reloaded = repo.get_by_ai_id("ai-persist")
check("update candidates persisted",
      len(reloaded.data["monuments"]["candidates"]) == 1)

try:
    repo.create(IndividualMonument("ai-persist"))
    check("create duplicate raises", False)
except ValueError as e:
    check("create duplicate raises ValueError", "already exists" in str(e))

# create more for list
for i in range(3):
    repo.create(IndividualMonument(f"list-ai-{i}"))
items = repo.list_all()
check("list_all includes original", any(it["ai_id"] == "ai-persist" for it in items))
check("list_all includes new items", any(it["ai_id"] == "list-ai-2" for it in items))

close_db()

# ── Summary ─────────────────────────────────────────

print(f"\n{'='*40}")
if errors:
    print(f"FAILURES: {len(errors)}")
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED ✓")

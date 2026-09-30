# -*- coding: utf-8 -*-
"""W-4 / M2：每 (vendor, tenant) 的 active 记忆容量上限（写入侧确定性淘汰）。

缺陷：`db.upsert_vendor_memory`（`rag.ingest_memory` / `rag.approve_pending_to_memory`
的落库链路）对同一供应商可**无限追加** active 记忆行。高分供应商会被噪声记忆淹没，
检索质量随使用下降。修复：写入新行后若 active 行数**超过**上限（settings 键
`memory_max_active_per_vendor`，缺省 200；0/负值 = 不限制），按确定性口径淘汰多余的
最低分/最旧行 —— 归档（status='archived'）而非物理删除，并级联删向量、留审计。

本用例覆盖 4 条判别式判据（缺一不可）：
(e1) 上限内不淘汰：active 数 <= 上限时，行数、状态、向量、无审计，全部不变；
(e2) 超限淘汰最低分且级联向量：淘汰的正是 decay_score 最低的那批（同分再按
     created_at/rowid 取最旧），被淘汰行归档 + 其向量被级联删；本次新写入的行不被
     自己淘汰；审计留痕（action=capacity_eviction）；
(e3) 上限=0 / 负值 = 不限制（向后兼容：回到「只追加、无边界」的旧行为）；
(e4) 幂等：内容幂等命中（复用既有行、不增行）不触发淘汰；达上限后的重复写入不产生
     额外归档。

全部离线假数据：每个用例独立临时 DB + 独立临时 RAG 目录，零外部调用、零 live 写入。
嵌入档锁定为 hash（demo/conftest.py 已设 RAG_EMBEDDING_PROVIDER=hash），纯本地。

反证（人工执行，见任务报告；仓库文件只读，不改动源码）：把 upsert 侧的淘汰调用去掉
（或猴补 `db._evict_vendor_memory_over_capacity` 为空操作）→ (e2) 的「超限后 active
数仍 <= 上限」断言必须翻红；把淘汰顺序反序（先淘汰最高分）→ (e2) 的「被淘汰的是
最低分那批」断言必须翻红。见 `_mutation_proof` 脚本（scratchpad，不落仓库）。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_capacity_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_capacity.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services import settings_service  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

_LIMIT_KEY = "memory_max_active_per_vendor"


def _fresh_db():
    """每个用例独立数据库文件 + 独立向量目录，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"cap_{uuid.uuid4().hex[:8]}.db")
    os.environ["RAG_DIR"] = os.path.join(_TMP, f"rag_{uuid.uuid4().hex[:8]}")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _set_limit(n):
    """写容量上限（settings 键，缺省 200）。"""
    settings_service.set_value(_LIMIT_KEY, n)


def _ingest(vendor, tenant, text, notes=""):
    """走生产写入链路落一条记忆（DB 行 + 向量），返回其 memory_id。

    内容幂等命中（复用既有行、不新增）时同样返回被复用的那一行的 id —— 幂等键是
    (vendor, tenant, status='active', notes, sample)，其中 sample 即 items_text。
    """
    rag.ingest_memory(vendor, text, notes=notes, tenant_id=tenant,
                      source_kind="manual")
    for r in db.list_vendor_memory(vendor, tenant_id=tenant):
        if r["notes"] == notes and r["sample"] == text:
            return r["memory_id"]
    raise AssertionError(f"ingest 后找不到对应的 active 行 notes={notes!r}")


def _all_rows(vendor, tenant):
    return db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)


def _active(vendor, tenant):
    return db.list_vendor_memory(vendor, tenant_id=tenant)


def _set_decay(vendor, tenant, memory_id, score):
    """把某行的 decay_score 设为指定值（构造可区分的淘汰序；仅测试用）。"""
    s = db.get_session()
    try:
        row = s.query(db._VendorMemoryRow).filter(
            db._VendorMemoryRow.memory_id == memory_id).first()
        assert row is not None, f"未找到 memory_id={memory_id}"
        row.decay_score = float(score)
        s.commit()
    finally:
        s.close()


def _status_of(vendor, tenant, memory_id):
    for r in _all_rows(vendor, tenant):
        if r["memory_id"] == memory_id:
            return r["status"]
    return None


def _vector_present(memory_id, tenant):
    return rag._memory_vectors_present(rag._store(tenant), memory_id)


def _eviction_audits():
    return [e for e in db.read_system_audit_log()
            if e.get("action") == "capacity_eviction"]


# -------------------------------------------------------------
# (e1) 上限内不淘汰
# -------------------------------------------------------------
def test_within_limit_does_not_evict():
    _fresh_db()
    vendor, tenant = "容量商戶A", "cap_t1"
    _set_limit(5)

    mids = [_ingest(vendor, tenant, f"菜心 {i}斤 @10", notes=f"n{i}")
            for i in range(5)]

    rows = _all_rows(vendor, tenant)
    assert len(rows) == 5, rows
    assert all(r["status"] == "active" for r in rows), rows
    assert {r["memory_id"] for r in rows} == set(mids), rows
    # 向量与行数一一对应，无级联删除
    assert all(_vector_present(m, tenant) for m in mids)
    # 未超限 → 不得留淘汰审计
    assert _eviction_audits() == [], _eviction_audits()


# -------------------------------------------------------------
# (e2) 超限淘汰最低分且级联向量
# -------------------------------------------------------------
def test_over_limit_evicts_lowest_score_and_cascades_vector():
    _fresh_db()
    vendor, tenant = "容量商戶B", "cap_t2"
    _set_limit(200)  # 先宽松，避免种子阶段就被淘汰

    # 5 条已存在记忆，给予可区分的 decay（插入序 = 从旧到新）
    mids = [_ingest(vendor, tenant, f"品項 {i} 單價 {i}", notes=f"seed{i}")
            for i in range(5)]
    decays = {mids[0]: 0.9, mids[1]: 0.2, mids[2]: 0.5,
              mids[3]: 0.8, mids[4]: 0.6}
    for mid, score in decays.items():
        _set_decay(vendor, tenant, mid, score)
    assert all(_vector_present(m, tenant) for m in mids)

    # 收紧上限到 3，再写第 6 条（新行 decay=0.7、created_at 最新）→ 5+1 超过 3，
    # 需淘汰 6-3=3 条：候选排除新行后，按 (decay ASC, created_at ASC, rowid ASC)
    # 取最低 3 条 = 0.2(mids[1]) / 0.5(mids[2]) / 0.6(mids[4])
    _set_limit(3)
    new_mid = _ingest(vendor, tenant, "新寫入 7斤 @10", notes="newest")

    active = _active(vendor, tenant)
    assert len(active) == 3, active
    expected_evicted = {mids[1], mids[2], mids[4]}
    active_ids = {r["memory_id"] for r in active}
    assert active_ids == ({new_mid, mids[0], mids[3]}), active_ids

    # 被淘汰的正是最低分那批，且已归档（非物理删除：历史仍可查）
    for m in expected_evicted:
        assert _status_of(vendor, tenant, m) == "archived", (m, _status_of(vendor, tenant, m))
    # 保留下来的（含本次新行）仍是 active
    for m in (new_mid, mids[0], mids[3]):
        assert _status_of(vendor, tenant, m) == "active", m

    # 本轮新写入的行绝不被自己淘汰
    assert new_mid in active_ids
    assert _vector_present(new_mid, tenant) is True

    # 级联向量：被淘汰行的向量被删，保留行的向量仍在
    for m in expected_evicted:
        assert _vector_present(m, tenant) is False, (
            "淘汰未级联删除向量：归档行仍在向量库（巡检会报 orphan）")
    for m in (mids[0], mids[3]):
        assert _vector_present(m, tenant) is True

    # 审计留痕：action=capacity_eviction，载荷含 vendor/tenant/被淘汰 memory_id
    audits = _eviction_audits()
    assert audits, "超限淘汰必须留审计"
    new_payload = audits[-1]["new"] if isinstance(audits[-1]["new"], dict) else {}
    old_payload = audits[-1]["old"] if isinstance(audits[-1]["old"], dict) else {}
    evicted_logged = set(old_payload.get("memory_ids", []))
    assert evicted_logged == expected_evicted, old_payload
    assert old_payload.get("vendor") == vendor and old_payload.get("tenant_id") == tenant
    assert new_payload.get("status") == "archived", new_payload


# -------------------------------------------------------------
# (e3) 上限=0 / 负值 = 不限制（向后兼容）
# -------------------------------------------------------------
def test_zero_limit_disables_capacity():
    _fresh_db()
    vendor, tenant = "容量商戶C", "cap_t3"
    _set_limit(0)

    mids = [_ingest(vendor, tenant, f"品項 {i}", notes=f"z{i}") for i in range(6)]
    rows = _all_rows(vendor, tenant)
    assert len(rows) == 6, rows
    assert all(r["status"] == "active" for r in rows), rows
    assert _eviction_audits() == []

    # 负值同款「不限制」
    _set_limit(-1)
    _ingest(vendor, tenant, "再寫一條 8斤", notes="neg")
    assert len(_all_rows(vendor, tenant)) == 7
    assert all(r["status"] == "active" for r in _all_rows(vendor, tenant))


# -------------------------------------------------------------
# (e4) 幂等：内容幂等命中不增行、不触发淘汰
# -------------------------------------------------------------
def test_idempotent_write_within_limit_does_not_evict():
    _fresh_db()
    vendor, tenant = "容量商戶D", "cap_t4"
    _set_limit(3)

    mids = [_ingest(vendor, tenant, f"品項 {i}", notes=f"d{i}") for i in range(3)]
    assert len(_all_rows(vendor, tenant)) == 3

    # 完全同 content+notes 的重复写入 → 复用既有行（不增行），不触发任何淘汰
    mid_again = _ingest(vendor, tenant, "品項 1", notes="d1")
    assert mid_again == mids[1], (mid_again, mids)
    rows = _all_rows(vendor, tenant)
    assert len(rows) == 3, rows
    assert all(r["status"] == "active" for r in rows), rows
    assert _eviction_audits() == [], _eviction_audits()

    # 达上限后继续重复写入，仍不产生额外归档
    _ingest(vendor, tenant, "品項 2", notes="d2")
    assert all(r["status"] == "active" for r in _all_rows(vendor, tenant))
    assert len(_all_rows(vendor, tenant)) == 3

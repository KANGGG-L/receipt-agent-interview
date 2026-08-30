# -*- coding: utf-8 -*-
"""Gap B1 + B4 记忆治理元数据 + 读取预算专项测试（T3，TDD 先于实现编写）。

覆盖：
1. 写入记忆带来源（source_kind）与版本（version），source_ref 可回溯触发 receipt_id
2. feedback 蒸馏路径：source_kind='feedback_distilled'，source_ref 为触发点踩
   的 receipt_id 列表 JSON；彻底替换 notes[-4000:] 累积拼接（按条存储）
3. 同 (vendor, tenant_id) 内容完全相同的 active 行不重复追加（幂等）
4. MemoryBudget：注入总 token 不超 facts_tokens=800
5. max_items=6 生效：写入 8 条只注入 6 条
6. 单条超 per_item_tokens=200 被截断
7. retrieve_context 命中后 hit_count 自增、last_hit_at 刷新
8. status='archived' 的记忆不参与注入（列语义闭环，淘汰动作归 T8）

token 口径：无 tokenizer 依赖，_approx_tokens = len(text)（1 字符按 1 token 保守计），
因此预算断言直接用 len(ctx)。
数据一律假数据（随机后缀的虚拟商户名，避免跨用例模糊匹配串扰）。
"""

import json
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_governance_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_governance.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import importlib  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402


def _fresh_db():
    """每个用例独立数据库文件 + 重置 Chroma 进程内缓存，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"gov_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _fake_vendor(prefix):
    return f"{prefix}{uuid.uuid4().hex[:6]}"


# -------------------------------------------------------------
# 1. 写入带来源与版本，source_ref 可回溯 receipt_id
# -------------------------------------------------------------
def test_approve_memory_carries_source_version_and_ref():
    _db = _fresh_db()
    vendor = _fake_vendor("審批商戶")
    rag.ingest_memory(vendor, "菜心 10斤 @8 = 80", notes="版式：手寫長單",
                      tenant_id="gov_t1", receipt_id=4321)

    rows = _db.list_vendor_memory(vendor, tenant_id="gov_t1")
    assert len(rows) == 1, "一次 approve 写入应恰好生成一条记忆"
    r = rows[0]
    assert r["source_kind"] == "approve"
    assert r["version"] == 1
    assert r["memory_id"], "memory_id 必须生成"
    ref = json.loads(r["source_ref"] or "[]")
    assert "4321" in [str(x) for x in ref], "source_ref 必须可回溯到触发 receipt_id"
    assert r["created_at"] and r["updated_at"]
    assert r["status"] == "active"
    assert r["hit_count"] == 0
    assert r["decay_score"] == 1.0

    # Chroma metadata 同步携带治理字段（回溯删除用）
    docs = rag._store("gov_t1").similarity_search(vendor, k=5)
    metas = [d.metadata for d in docs if d.metadata.get("vendor") == vendor]
    assert metas, "Chroma 中应能召回本次写入"
    assert all(m.get("memory_id") == r["memory_id"] for m in metas)
    assert all(m.get("created_at") for m in metas)


def test_manual_memory_without_receipt_is_marked_manual():
    _db = _fresh_db()
    vendor = _fake_vendor("手動商戶")
    rag.ingest_memory(vendor, "A 1件 @2 = 2", notes="手動備註", tenant_id="gov_t1b")
    r = _db.list_vendor_memory(vendor, tenant_id="gov_t1b")[0]
    assert r["source_kind"] == "manual"
    assert json.loads(r["source_ref"] or "[]") == []


def test_feedback_memory_source_ref_traces_receipt_ids():
    _db = _fresh_db()
    vendor = _fake_vendor("反饋商戶")
    content = rag.ingest_feedback_memory(
        vendor, "品名認錯要糾正", ["模糊"], "gov_t2",
        source_receipt_ids=["901", "902", "903"])
    assert "品名" in content

    # T8：蒸馏先入待确认队列（带触发来源），人工 approve 后才落记忆库
    pend = _db.list_pending_memory(tenant_id="gov_t2")
    assert len(pend) == 1
    assert json.loads(pend[0]["source_receipt_ids_json"]) == ["901", "902", "903"]
    assert _db.list_vendor_memory(vendor, tenant_id="gov_t2") == []

    rag.approve_pending_to_memory(
        vendor, content, tenant_id="gov_t2",
        source_receipt_ids=["901", "902", "903"])

    rows = _db.list_vendor_memory(vendor, tenant_id="gov_t2")
    assert len(rows) == 1, "一次蒸馏沉淀经 approve 后应恰好生成一条独立记忆"
    r = rows[0]
    assert r["source_kind"] == "feedback_distilled"
    assert json.loads(r["source_ref"]) == ["901", "902", "903"]
    assert "品名認錯要糾正" in (r["notes"] or "")


def test_duplicate_write_is_idempotent():
    _db = _fresh_db()
    vendor = _fake_vendor("冪等商戶")
    rag.ingest_memory(vendor, "A 1件 @2 = 2", notes="n1",
                      tenant_id="gov_t3", receipt_id=1)
    rag.ingest_memory(vendor, "A 1件 @2 = 2", notes="n1",
                      tenant_id="gov_t3", receipt_id=1)
    assert len(_db.list_vendor_memory(vendor, tenant_id="gov_t3")) == 1, \
        "同 (vendor, tenant) 内容完全相同的 active 行不应重复追加"


# -------------------------------------------------------------
# 3b. P3-3：去重命中时合并 source_ref（新 receipt_id 不丢失）+ 刷新 updated_at
# -------------------------------------------------------------
def test_dedup_hit_merges_source_ref_and_refreshes_updated_at():
    _db = _fresh_db()
    vendor = _fake_vendor("合併商戶")
    mid1, is_new1 = _db.upsert_vendor_memory(
        vendor, "同內容備註", "同樣本", tenant_id="gov_t3b",
        source_kind="approve", source_ref=json.dumps(["101"]))
    row0 = _db.list_vendor_memory(vendor, tenant_id="gov_t3b")[0]
    updated_before = row0["updated_at"]

    # 同 (vendor, tenant, notes, sample) 命中去重：不同 receipt_id 必须并入 source_ref
    mid2, is_new2 = _db.upsert_vendor_memory(
        vendor, "同內容備註", "同樣本", tenant_id="gov_t3b",
        source_kind="approve", source_ref=json.dumps(["102"]))
    assert is_new1 is True and is_new2 is False, "第二次写入应命中幂等去重"
    assert mid2 == mid1, "去重命中应复用既有 memory_id"

    rows = _db.list_vendor_memory(vendor, tenant_id="gov_t3b")
    assert len(rows) == 1, "去重命中不得新增行"
    r = rows[0]
    ref = json.loads(r["source_ref"] or "[]")
    assert ref == ["101", "102"], f"新 receipt_id 应并入既有 source_ref，实际 {ref}"
    assert r["updated_at"] >= updated_before, "去重命中应刷新 updated_at"
    assert r["version"] == 1, "version 保持 1（同行演进归 TODO(T8) 口径）"

    # 同一 receipt_id 重复触发：集合式去重，不重复追加
    _db.upsert_vendor_memory(vendor, "同內容備註", "同樣本", tenant_id="gov_t3b",
                             source_kind="approve", source_ref=json.dumps(["101"]))
    ref2 = json.loads(_db.list_vendor_memory(vendor, tenant_id="gov_t3b")[0]["source_ref"])
    assert ref2 == ["101", "102"], "重复 receipt_id 不得在 source_ref 中重复出现"


# -------------------------------------------------------------
# 4. 注入总 token 不超 facts_tokens=800
# -------------------------------------------------------------
def test_inject_total_tokens_within_facts_budget():
    _db = _fresh_db()
    vendor = _fake_vendor("預算商戶")
    for i in range(8):
        _db.upsert_vendor_memory(vendor, f"備註{i} " + "x" * 180, "",
                                 tenant_id="gov_t4", source_kind="manual")
    ctx = rag.retrieve_context(vendor, tenant_id="gov_t4")
    assert ctx.strip() != "", "有 active 记忆时注入不应为空"
    assert len(ctx) <= 800, f"注入总 token 应 <= facts_tokens=800，实际 {len(ctx)}"


# -------------------------------------------------------------
# 5. max_items=6 生效
# -------------------------------------------------------------
def test_max_items_six_cap():
    _db = _fresh_db()
    vendor = _fake_vendor("條數商戶")
    for i in range(8):
        _db.upsert_vendor_memory(vendor, f"MARKER_{i} " + "y" * 20, "",
                                 tenant_id="gov_t5", source_kind="manual")
    ctx = rag.retrieve_context(vendor, tenant_id="gov_t5")
    injected = [i for i in range(8) if f"MARKER_{i}" in ctx]
    assert len(injected) == 6, f"写入 8 条只应注入 6 条，实际 {len(injected)}: {injected}"


# -------------------------------------------------------------
# 6. 单条超 per_item_tokens 被截断
# -------------------------------------------------------------
def test_per_item_truncation():
    _db = _fresh_db()
    vendor = _fake_vendor("截斷商戶")
    _db.upsert_vendor_memory(vendor, "z" * 300, "",
                             tenant_id="gov_t6", source_kind="manual")

    # 自定义预算：单条 50，注入后总长（含标签）不得超 50
    budget = rag.MemoryBudget(facts_tokens=800, per_item_tokens=50, max_items=6)
    ctx = rag.retrieve_context(vendor, tenant_id="gov_t6", budget=budget)
    assert len(ctx) <= 50, f"单条应截断到 per_item_tokens=50，实际 {len(ctx)}"
    assert "z" * 300 not in ctx

    # 默认预算：单条 200 上限同样生效
    ctx2 = rag.retrieve_context(vendor, tenant_id="gov_t6")
    assert "z" * 201 not in ctx2, "默认预算下单条 200 token 截断失效"
    assert len(ctx2) <= 200


# -------------------------------------------------------------
# 7. 命中记账：hit_count 自增、last_hit_at 刷新
# -------------------------------------------------------------
def test_hit_count_and_last_hit_at_refreshed_on_retrieve():
    _db = _fresh_db()
    vendor = _fake_vendor("命中商戶")
    _db.upsert_vendor_memory(vendor, "命中備註", "命中樣本",
                             tenant_id="gov_t7", source_kind="manual")

    r0 = _db.list_vendor_memory(vendor, tenant_id="gov_t7")[0]
    assert r0["hit_count"] == 0 and not r0["last_hit_at"]

    ctx = rag.retrieve_context(vendor, tenant_id="gov_t7")
    assert "命中備註" in ctx, "命中内容应被注入"
    r1 = _db.list_vendor_memory(vendor, tenant_id="gov_t7")[0]
    assert r1["hit_count"] == 1, "首次命中后 hit_count 应为 1"
    assert r1["last_hit_at"], "首次命中后 last_hit_at 应刷新"

    rag.retrieve_context(vendor, tenant_id="gov_t7")
    r2 = _db.list_vendor_memory(vendor, tenant_id="gov_t7")[0]
    assert r2["hit_count"] == 2, "再次命中 hit_count 应累加到 2"
    assert r2["last_hit_at"] >= r1["last_hit_at"], "last_hit_at 应不早于上次"


# -------------------------------------------------------------
# 8. status='archived' 的记忆不参与注入
# -------------------------------------------------------------
def test_archived_memory_not_injected():
    _db = _fresh_db()
    vendor = _fake_vendor("歸檔商戶")
    mid, _is_new = _db.upsert_vendor_memory(vendor, "ARCHIVED_MARKER 應該不可見",
                                            "樣本", tenant_id="gov_t8",
                                            source_kind="manual")
    assert mid
    s = _db.get_session()
    try:
        row = s.query(_db._VendorMemoryRow).filter(
            _db._VendorMemoryRow.memory_id == mid).first()
        assert row is not None
        row.status = "archived"
        s.commit()
    finally:
        s.close()

    ctx = rag.retrieve_context(vendor, tenant_id="gov_t8")
    assert "ARCHIVED_MARKER" not in ctx, "archived 记忆不得注入"
    assert ctx.strip() == "", "唯一记忆归档后注入应为空"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

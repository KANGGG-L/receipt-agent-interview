# -*- coding: utf-8 -*-
"""T8（Gap D1 + B2）：记忆写入人工闸 + 非对称淘汰 生命周期测试。

覆盖：
1. 连续 3 次点踩 → 进入 pending_memory 待确认队列（而非直接生效）
2. 未 approve 的记忆不参与检索注入
3. approve 后生效（写入 vendor_memory + 向量库，可被检索）
4. reject 不注入
5. 非对称衰减数值断言：命中 +0.05 / 用户覆写 -0.12 / <=0.5 归档且停止注入
6. retrieve_context 只召回 status=active 且 decay_score > 归档阈值 的记忆

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用。
"""

import importlib
import json
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_lifecycle_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_memory.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

# 非对称衰减缺省口径（settings 缺省值，测试按数值断言）
HIT_BONUS = 0.05
OVERRIDE_PENALTY = 0.12
ARCHIVE_THRESHOLD = 0.5


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"mem_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _mk_receipt_with_items(_db, vendor):
    rid = _db.create_receipt(supplier_name=vendor, status="parsed")
    _db.set_receipt_items(rid, [{
        "name": "菜心", "raw_name": "菜心", "quantity": 2, "unit": "斤",
        "raw_unit": "斤", "unit_price": 10, "amount": 20,
        "sku_id": None, "cost_center_id": None, "confidence": 0.9,
        "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
        "price_diff_percent": 0.0, "unit_conversion_warning": "",
        "fuzzy_candidates": [], "entity_candidates": [],
    }])
    return rid


def _three_dislikes(client, _db, vendor, tenant_id="default"):
    """通过真实 API 连踩 3 次，返回最后一次的响应体。"""
    rids = [_mk_receipt_with_items(_db, vendor) for _ in range(3)]
    last = None
    for i, rid in enumerate(rids):
        resp = client.post(
            f"/api/receipt/{rid}/feedback",
            json={"like": -1, "comment": f"金額錯第{i}次", "item_index": 0},
            headers={"X-Role": "staff", "X-Tenant-Id": tenant_id},
        )
        assert resp.status_code == 200, resp.text
        last = resp.json()
    return last


# -------------------------------------------------------------
# 1-2. 三连踩 → pending；未 approve 不注入
# -------------------------------------------------------------
def test_three_dislikes_enqueue_pending_not_injected():
    _db = _fresh_db()
    vendor = "待確認商戶A"
    body = _three_dislikes(_client(), _db, vendor)
    assert body["distilled"] is True

    pend = _db.list_pending_memory(tenant_id="default")
    assert len(pend) == 1
    assert pend[0]["status"] == "pending"
    assert pend[0]["vendor"] == vendor
    assert pend[0]["source_kind"] == "feedback_distilled"
    # source_receipt_ids 可回溯
    refs = json.loads(pend[0]["source_receipt_ids_json"] or "[]")
    assert len(refs) == 3

    # 未 approve 不注入
    assert rag.retrieve_context(vendor, tenant_id="default").strip() == ""


# -------------------------------------------------------------
# 3. approve 后注入
# -------------------------------------------------------------
def test_approve_makes_memory_retrievable():
    _db = _fresh_db()
    client = _client()
    vendor = "批准商戶B"
    _three_dislikes(client, _db, vendor)
    pend = _db.list_pending_memory(tenant_id="default")
    pid = pend[0]["id"]

    resp = client.post(f"/api/memory/pending/{pid}/approve",
                       headers={"X-Role": "admin"})
    assert resp.status_code == 200, resp.text

    rows = _db.list_pending_memory(tenant_id="default")
    assert rows[0]["status"] == "approved"
    assert rows[0]["reviewed_by"]
    assert rows[0]["reviewed_at"]

    # approve 后经既有 upsert 链路落 vendor_memory（带治理元数据）
    mem = _db.list_vendor_memory(vendor, tenant_id="default")
    assert len(mem) == 1
    assert mem[0]["source_kind"] == "feedback_distilled"
    assert json.loads(mem[0]["source_ref"])  # 触发来源可回溯

    # 可被检索注入
    ctx = rag.retrieve_context(vendor, tenant_id="default")
    assert "糾偏" in ctx or "反馈" in ctx or vendor in ctx


# -------------------------------------------------------------
# 4. reject 不注入
# -------------------------------------------------------------
def test_reject_not_injected():
    _db = _fresh_db()
    client = _client()
    vendor = "拒絕商戶C"
    _three_dislikes(client, _db, vendor)
    pid = _db.list_pending_memory(tenant_id="default")[0]["id"]

    resp = client.post(f"/api/memory/pending/{pid}/reject",
                       headers={"X-Role": "admin"})
    assert resp.status_code == 200, resp.text

    rows = _db.list_pending_memory(tenant_id="default")
    assert rows[0]["status"] == "rejected"
    assert _db.list_vendor_memory(vendor, tenant_id="default") == []
    assert rag.retrieve_context(vendor, tenant_id="default").strip() == ""


# -------------------------------------------------------------
# 5. 非对称衰减：数值断言（+0.05 / -0.12 / 归档）
# -------------------------------------------------------------
def _approved_vendor_memory(_db, client, vendor):
    """造一条已 approve 的反馈记忆，返回 memory_id。"""
    _three_dislikes(client, _db, vendor)
    pid = _db.list_pending_memory(tenant_id="default")[0]["id"]
    resp = client.post(f"/api/memory/pending/{pid}/approve",
                       headers={"X-Role": "admin"})
    assert resp.status_code == 200, resp.text
    mem = _db.list_vendor_memory(vendor, tenant_id="default")
    assert len(mem) == 1
    return mem[0]["memory_id"]


def test_decay_hit_bonus_and_override_penalty():
    _db = _fresh_db()
    client = _client()
    vendor = "衰減商戶D"
    mid = _approved_vendor_memory(_db, client, vendor)

    # 初始 decay_score = 1.0
    mem = _db.list_vendor_memory(vendor, tenant_id="default")[0]
    assert abs(mem["decay_score"] - 1.0) < 1e-9

    # 用户覆写一次：-0.12 → 0.88
    _db.apply_vendor_memory_override_penalty(vendor, tenant_id="default")
    mem = _db.list_vendor_memory(vendor, tenant_id="default")[0]
    assert abs(mem["decay_score"] - (1.0 - OVERRIDE_PENALTY)) < 1e-9

    # 检索命中一次：+0.05 → 0.93
    ctx = rag.retrieve_context(vendor, tenant_id="default")
    assert ctx.strip() != ""
    mem = _db.list_vendor_memory(vendor, tenant_id="default")[0]
    assert abs(mem["decay_score"] - (1.0 - OVERRIDE_PENALTY + HIT_BONUS)) < 1e-9
    assert mem["hit_count"] >= 1


def test_override_penalty_archives_and_stops_injection():
    _db = _fresh_db()
    client = _client()
    vendor = "歸檔商戶E"
    _approved_vendor_memory(_db, client, vendor)

    # 连续覆写 5 次：1.0 - 5*0.12 = 0.40 <= 0.5 → archived
    for i in range(4):
        _db.apply_vendor_memory_override_penalty(vendor, tenant_id="default")
        mem = _db.list_vendor_memory(vendor, tenant_id="default")[0]
        assert mem["status"] == "active", f"第{i+1}次覆写后不应归档"
    _db.apply_vendor_memory_override_penalty(vendor, tenant_id="default")
    mem = _db.list_vendor_memory(vendor, tenant_id="default",
                                 include_archived=True)[0]
    assert mem["decay_score"] <= ARCHIVE_THRESHOLD
    assert mem["status"] == "archived"

    # 归档后停止注入
    assert rag.retrieve_context(vendor, tenant_id="default").strip() == ""


# -------------------------------------------------------------
# 6. retrieve_context 只召回 active 且 decay_score > 阈值
# -------------------------------------------------------------
def test_retrieve_context_filters_by_status_and_decay():
    _db = _fresh_db()
    client = _client()

    vendor_bad = "劣化商戶F"
    vendor_good = "健康商戶G"
    _approved_vendor_memory(_db, client, vendor_bad)
    _approved_vendor_memory(_db, client, vendor_good)

    # 把劣化商户的活跃记忆压到阈值以下（-0.12 * 5 = 0.40）
    for _ in range(5):
        _db.apply_vendor_memory_override_penalty(vendor_bad, tenant_id="default")

    # 劣化商户：无注入；健康商户：正常注入
    assert rag.retrieve_context(vendor_bad, tenant_id="default").strip() == ""
    assert rag.retrieve_context(vendor_good, tenant_id="default").strip() != ""


# -------------------------------------------------------------
# 管理端点权限与形态
# -------------------------------------------------------------
def test_pending_list_requires_admin():
    _db = _fresh_db()
    client = _client()
    _three_dislikes(client, _db, "權限商戶H")
    resp = client.get("/api/memory/pending", headers={"X-Role": "staff"})
    assert resp.status_code == 403
    resp = client.get("/api/memory/pending", headers={"X-Role": "admin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert len(body["data"]) == 1


def test_approve_requires_admin_and_404_on_unknown():
    _db = _fresh_db()
    client = _client()
    resp = client.post("/api/memory/pending/999999/approve",
                       headers={"X-Role": "admin"})
    assert resp.status_code == 404
    _three_dislikes(client, _db, "權限商戶I")
    pid = _db.list_pending_memory(tenant_id="default")[0]["id"]
    resp = client.post(f"/api/memory/pending/{pid}/approve",
                       headers={"X-Role": "staff"})
    assert resp.status_code == 403


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

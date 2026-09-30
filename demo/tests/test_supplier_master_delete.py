# -*- coding: utf-8 -*-
"""FP-2a：供应商主档物理删除（DELETE /api/suppliers/{id}，PDPO erasure）。

覆盖 SPEC §2.1 边界表：
(f2a-1) 有引用单据（含仅软删单据）→ 409 SUPPLIER_HAS_RECEIPTS，一切原样；
(f2a-2) 无单据 + 记忆（active/archived/异名锚点行）+ 候选（全 status）+ 别名 →
        200 且四类级联删净、向量消失、主档行消失、巡检 consistent:true；
(f2a-3) 无任何级联数据 → 200 全 0（幂等空删）；
(f2a-4) 重复删除 → 404；(f2a-5) 跨租户 → 404；(f2a-6) staff → 403；
(f2a-7) 记忆锚点 supplier_id 有值但 vendor 名与主档名不同 → 仍被删（OR 口径）；
(f2a-8) 审计 supplier_master_delete 一条，载荷含 4 个级联计数。

反证口径：把端点里的 `db.delete_pending_memory` / alias 级联 / 物理删主档任一去掉，
(f2a-2) 对应的「零残留」断言翻红；把引用守卫去掉，(f2a-1) 翻红（供应商行消失留下死链单据）。

全部离线：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 写入。
"""

import importlib
import os
import sys
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="supplier_master_delete_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_supplier_delete.db")
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


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"sup_{uuid.uuid4().hex[:8]}.db")
    os.environ["RAG_DIR"] = os.path.join(_TMP, f"rag_{uuid.uuid4().hex[:8]}")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _headers(role="owner", tenant="default"):
    return {"X-Role": role, "X-Tenant-Id": tenant}


def _seed_memory(_db, vendor, tenant_id="default", supplier_id=None,
                 status="active", sample=None):
    """落一条记忆行（不写向量也要断言 DB 删净；向量仅在 (f2a-2) 主场景补齐）。"""
    from langchain_core.documents import Document
    sample = sample or f"{vendor} 明细：菜心 2斤 @10 = 20"
    mid, _is_new = _db.upsert_vendor_memory(
        vendor, f"版式：手写单（{vendor}）", sample,
        tenant_id=tenant_id, source_kind="manual", supplier_id=supplier_id)
    if supplier_id is not None:
        # 锚点权威写入可能被 _memory_anchor_for_write 校验后保留；幂等补一次
        s = _db.get_session()
        try:
            row = s.query(_db._VendorMemoryRow).filter(
                _db._VendorMemoryRow.memory_id == mid).first()
            row.supplier_id = supplier_id
            s.commit()
        finally:
            s.close()
    if status != "active":
        s = _db.get_session()
        try:
            row = s.query(_db._VendorMemoryRow).filter(
                _db._VendorMemoryRow.memory_id == mid).first()
            row.status = status
            s.commit()
        finally:
            s.close()
    rag._store(tenant_id).add_documents([
        Document(page_content=f"版式：手写单（{vendor}）",
                 metadata={"vendor": vendor, "kind": "notes",
                           "tenant_id": tenant_id, "memory_id": mid}),
    ])
    return mid


def _vector_ids(tenant_id, mid):
    return rag._store(tenant_id)._collection.get(where={"memory_id": mid})["ids"]


def _supplier_counts(_db, supplier_id, tenant_id="default"):
    """四类级联对象 + 主档行的残留计数（全 0 = 删净）。"""
    s = _db.get_session()
    try:
        mem = s.query(_db._VendorMemoryRow).filter(
            _db._VendorMemoryRow.supplier_id == supplier_id).count()
        pend = s.query(_db._PendingMemoryRow).filter(
            _db._PendingMemoryRow.supplier_id == supplier_id).count()
        alias = s.query(_db._VendorSkuAliasRow).filter(
            _db._VendorSkuAliasRow.supplier_id == supplier_id).count()
        master = s.query(_db._SupplierRow).filter(
            _db._SupplierRow.id == supplier_id).count()
        return {"vendor_memory": mem, "pending": pend,
                "alias": alias, "master": master}
    finally:
        s.close()


def _audit_entries(_db, action):
    return [e for e in _db.read_system_audit_log()
            if str(e.get("action")) == action]


def _seed_full_cascade(_db, tenant_id="default"):
    """建一个无单据供应商 + 全套级联数据，返回 (supplier_id, memory_ids)。"""
    sup_id, err = _db.create_supplier("全級聯商戶A", tenant_id=tenant_id)
    assert err is None, err
    mids = [
        _seed_memory(_db, "全級聯商戶A", tenant_id=tenant_id),
        # 异名锚点行（f2a-7）：vendor 写法不同，但 supplier_id 锚在本档
        _seed_memory(_db, "全級聯商戶A的別名寫法", tenant_id=tenant_id,
                     supplier_id=sup_id),
    ]
    # 归档行：内容必须可区分（upsert 幂等键是 vendor+notes+sample，同文会并回首行）
    _seed_memory(_db, "全級聯商戶A", tenant_id=tenant_id, status="archived",
                 sample="全級聯商戶A 舊版明细：牛肉 3斤 @60 = 180")
    for st in ("pending", "approved", "rejected"):
        pid, _new = _db.enqueue_pending_memory(
            "全級聯商戶A", f"候選：{st}", tenant_id=tenant_id,
            supplier_id=sup_id)
        if st != "pending":
            assert _db.review_pending_memory(pid, st, "tester",
                                             tenant_id=tenant_id), pid
    # 异名锚点的候选行
    _db.enqueue_pending_memory("全級聯商戶A的別名寫法", "候選：別名錨點",
                               tenant_id=tenant_id, supplier_id=sup_id)
    sku_id, err = _db.create_sku("級聯SKU", tenant_id=tenant_id)
    assert err is None, err
    aid, aerr = _db.upsert_vendor_sku_alias(
        sup_id, sku_id, "黑牛", tenant_id=tenant_id)
    assert aerr is None, aerr
    return sup_id, mids


# -------------------------------------------------------------
# (f2a-1) 有引用单据 → 409，一切原样
# -------------------------------------------------------------
def test_delete_refused_when_receipts_reference_supplier():
    _db = _fresh_db()
    client = _client()
    sup_id, _err = _db.create_supplier("有單商戶B", tenant_id="default")
    rid = _db.create_receipt(supplier_name="有單商戶B", tenant_id="default")
    _db.update_receipt(rid, supplier_id=sup_id)
    # 软删单据同样计数（防「删档后恢复软删单」死链）
    rid2 = _db.create_receipt(supplier_name="有單商戶B", tenant_id="default")
    _db.update_receipt(rid2, supplier_id=sup_id)
    _db.soft_delete_receipt(rid2)
    _seed_memory(_db, "有單商戶B", supplier_id=sup_id)

    resp = client.delete(f"/api/suppliers/{sup_id}", headers=_headers())
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "SUPPLIER_HAS_RECEIPTS", body
    assert body["data"]["receipt_count"] == 2, body
    # 守卫拒绝：不做任何删除
    assert _supplier_counts(_db, sup_id) == {
        "vendor_memory": 1, "pending": 0, "alias": 0, "master": 1}
    # 留了拒绝审计（可观测），但没有删除审计
    assert _audit_entries(_db, "supplier_delete_refused")
    assert not _audit_entries(_db, "supplier_master_delete")


# -------------------------------------------------------------
# (f2a-2)+(f2a-7)+(f2a-8) 全级联删除成功
# -------------------------------------------------------------
def test_delete_without_receipts_cascades_everything():
    _db = _fresh_db()
    client = _client()
    sup_id, mids = _seed_full_cascade(_db)

    resp = client.delete(f"/api/suppliers/{sup_id}", headers=_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["supplier_id"] == sup_id
    assert data["vendor_memory_deleted"] == 3, data      # active×1 + 异名锚点 + archived
    assert data["vectors_deleted"] == 3, data
    assert data["pending_memory_deleted"] == 4, data     # 3 status + 异名锚点 1
    assert data["vendor_sku_alias_deleted"] == 1, data

    # 四类数据零残留 + 主档行消失
    assert _supplier_counts(_db, sup_id) == {
        "vendor_memory": 0, "pending": 0, "alias": 0, "master": 0}
    # 名字口径也删净（OR 的另一侧）
    assert _db.list_vendor_memory("全級聯商戶A", tenant_id="default",
                                  include_archived=True) == []
    assert _db.list_pending_memory(status="pending", tenant_id="default") == []
    for mid in mids:
        assert _vector_ids("default", mid) == [], mid
    # 审计 F1：删除后巡检仍一致（无孤儿向量/缺向量）
    report = _consistency()
    assert report["consistent"] is True, report
    # 审计一条，载荷含 4 个级联计数 + 删除前主档要点（手机号只记有无）
    audits = _audit_entries(_db, "supplier_master_delete")
    assert len(audits) == 1, audits
    old, new = audits[-1]["old"], audits[-1]["new"]
    assert old.get("name") == "全級聯商戶A" and old.get("has_contact_phone") is False, old
    for k in ("vendor_memory_deleted", "vectors_deleted",
              "pending_memory_deleted", "vendor_sku_alias_deleted"):
        assert k in new, new


def test_delete_cascade_removes_pending_by_name_for_rejected_rows():
    """按名删候选也覆盖 approved/rejected（erasure 覆盖全部 status 的再证）。"""
    _db = _fresh_db()
    client = _client()
    sup_id, _err = _db.create_supplier("候選商戶C", tenant_id="default")
    _db.enqueue_pending_memory("候選商戶C", "c1", tenant_id="default")
    pid, _n = _db.enqueue_pending_memory("候選商戶C", "c2", tenant_id="default")
    _db.review_pending_memory(pid, "rejected", "tester", tenant_id="default")

    resp = client.delete(f"/api/suppliers/{sup_id}", headers=_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["pending_memory_deleted"] == 2


# -------------------------------------------------------------
# (f2a-3) 幂等空删；(f2a-4) 重复删除；(f2a-5) 跨租户；(f2a-6) 权限
# -------------------------------------------------------------
def test_delete_supplier_without_any_cascade_data_returns_zero_counts():
    _db = _fresh_db()
    client = _client()
    sup_id, _err = _db.create_supplier("乾淨商戶D", tenant_id="default")

    resp = client.delete(f"/api/suppliers/{sup_id}", headers=_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["vendor_memory_deleted"] == 0
    assert data["vectors_deleted"] == 0
    assert data["pending_memory_deleted"] == 0
    assert data["vendor_sku_alias_deleted"] == 0


def test_delete_twice_second_returns_404():
    _db = _fresh_db()
    client = _client()
    sup_id, _err = _db.create_supplier("重刪商戶E", tenant_id="default")
    assert client.delete(f"/api/suppliers/{sup_id}",
                         headers=_headers()).status_code == 200
    resp = client.delete(f"/api/suppliers/{sup_id}", headers=_headers())
    assert resp.status_code == 404, resp.text


def test_delete_cross_tenant_returns_404():
    _db = _fresh_db()
    client = _client()
    sup_id, _err = _db.create_supplier("跨租商戶F", tenant_id="shopA")
    resp = client.delete(f"/api/suppliers/{sup_id}",
                         headers=_headers(tenant="default"))
    assert resp.status_code == 404, resp.text
    assert _supplier_counts(_db, sup_id)["master"] == 1


def test_delete_requires_owner_role():
    _db = _fresh_db()
    client = _client()
    sup_id, _err = _db.create_supplier("權限商戶G", tenant_id="default")
    resp = client.delete(f"/api/suppliers/{sup_id}", headers=_headers(role="staff"))
    assert resp.status_code == 403, resp.text
    assert _supplier_counts(_db, sup_id)["master"] == 1


# -------------------------------------------------------------
# db 层守卫的独立判据（不经端点）
# -------------------------------------------------------------
def test_db_delete_pending_memory_requires_selector():
    _db = _fresh_db()
    vendor = "守門候選商戶H"
    _db.enqueue_pending_memory(vendor, "c", tenant_id="default")
    # 两个选择器都空 → 拒绝（防误删整表）
    assert _db.delete_pending_memory(tenant_id="default") == 0
    assert len(_db.list_pending_memory(tenant_id="default")) == 1
    # 跨租户删不掉
    assert _db.delete_pending_memory(vendor=vendor, tenant_id="other") == 0
    assert _db.delete_pending_memory(vendor=vendor, tenant_id="default") == 1


def test_db_delete_vendor_memory_supports_supplier_anchor_or_filter():
    """OR 口径（含自动锚点现实）：按名删会命中锚在别档的同名行，按锚删会命中
    名字不同的锚定行 —— 两者并集才是"这个供应商的全部记忆"。"""
    _db = _fresh_db()
    sup_id, _err = _db.create_supplier("錨點商戶I", tenant_id="default")
    sup2_id, _err2 = _db.create_supplier("另一檔J", tenant_id="default")
    # 异名锚点行：vendor 与主档名不同，但 supplier_id 锚在 sup_id
    mid_anchor = _seed_memory(_db, "別名寫法I", supplier_id=sup_id)
    # 与主档同名的行，但锚点在另一档（upsert 传入权威锚点）
    mid_name = _seed_memory(_db, "錨點商戶I", supplier_id=sup2_id)
    # 锚点删除：只命中锚定行，同名异档行不动
    affected, mids = _db.delete_vendor_memory(supplier_id=sup_id,
                                              tenant_id="default")
    assert affected == 1 and mids == [mid_anchor], (affected, mids)
    assert len(_db.list_vendor_memory("錨點商戶I", tenant_id="default")) == 1
    # 名 + 锚点 OR：两侧命中取并集（同名异档行按名命中 + 异名锚点行按锚命中）
    mid_anchor2 = _seed_memory(_db, "別名寫法I-2", supplier_id=sup_id)
    affected, mids = _db.delete_vendor_memory(vendor="錨點商戶I",
                                              supplier_id=sup_id,
                                              tenant_id="default")
    assert affected == 2, (affected, mids)
    assert sorted(mids) == sorted([mid_name, mid_anchor2]), mids
    assert _db.list_vendor_memory("錨點商戶I", tenant_id="default",
                                  include_archived=True) == []
    assert _db.list_vendor_memory("別名寫法I-2", tenant_id="default",
                                  include_archived=True) == []


def test_db_delete_vendor_memory_dry_run_does_not_delete():
    _db = _fresh_db()
    vendor = "乾跑商戶J"
    _seed_memory(_db, vendor)
    affected, mids = _db.delete_vendor_memory(vendor=vendor, tenant_id="default",
                                              dry_run=True)
    assert affected == 0 and len(mids) == 1, (affected, mids)
    # 行仍在
    assert len(_db.list_vendor_memory(vendor, tenant_id="default")) == 1


def _consistency():
    from app.services.memory_consistency import check_memory_consistency
    return check_memory_consistency(tenant_id="default")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

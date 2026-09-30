# -*- coding: utf-8 -*-
"""P1-T1：记忆删除与向量级联 测试。

覆盖：
1. 删除单条：DB 行消失 + 该 memory_id 的向量块消失 + retrieve_context 不再注入
2. 按供应商批量：该供应商全部行与向量被删，别的供应商不受影响
3. 批量删除覆盖已归档行（归档不等于删除，删除语义必须一致）
4. 跨租户删除返回 404 且他租户的行/向量分毫未动
5. 非 admin 403；不存在/未知供应商 404
6. 向量库整目录不存在时不报错，且不会为了删除而把空库建出来
7. db.delete_vendor_memory 两个选择器都不给时拒绝执行（防误删整租户）

反向证明（人工执行，见任务报告）：把 memory_store 里级联删向量的那一步注释掉，
第 1/2/3 条用例里"向量无该 mid"的断言必须失败。

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
数据用 `_seed_memory` 直接落「DB 行 + 向量」这两张介质（与 approve 链路同一形态），
不经过识别/反馈 API —— 本用例只关心删除行为，避免与其它波次正在改的链路耦合。
"""

import importlib
import os
import sys
import tempfile
import uuid
from urllib.parse import quote

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_delete_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_memory_delete.db")
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
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"del_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _seed_memory(_db, vendor, tenant_id="default", sample=None):
    """落一条记忆：vendor_memory 行 + 同 memory_id 的两块向量（notes/sample）。

    刻意不用 API 造数据：删除行为与写入链路无关，直接构造两张介质的最小一致状态，
    用例才不会因别的波次正在改写入链路而假失败。
    """
    from langchain_core.documents import Document
    sample = sample or f"{vendor} 明细：菜心 2斤 @10 = 20"
    mid, _is_new = _db.upsert_vendor_memory(
        vendor, "版式：手写单", sample, tenant_id=tenant_id, source_kind="manual")
    rag._store(tenant_id).add_documents([
        Document(page_content="版式：手写单",
                 metadata={"vendor": vendor, "kind": "notes",
                           "tenant_id": tenant_id, "memory_id": mid}),
        Document(page_content=f"供应商 {vendor} 的已确认进货明细：\n{sample}",
                 metadata={"vendor": vendor, "kind": "sample",
                           "tenant_id": tenant_id, "memory_id": mid}),
    ])
    return mid


def _vector_ids(tenant_id, mid):
    """该 memory_id 在向量层剩余的分块 id 列表（空 = 已级联删净）。"""
    return rag._store(tenant_id)._collection.get(where={"memory_id": mid})["ids"]


def _admin(**extra):
    headers = {"X-Role": "admin"}
    headers.update(extra)
    return headers


# -------------------------------------------------------------
# 1. 删除单条：DB + 向量 + 检索注入 三处同时消失
# -------------------------------------------------------------
def test_delete_single_memory_removes_row_vectors_and_injection():
    _db = _fresh_db()
    client = _client()
    vendor = "刪除商戶A"
    mid = _seed_memory(_db, vendor)

    # 前置：行、向量、注入三者都在
    assert len(_db.list_vendor_memory(vendor, tenant_id="default")) == 1
    assert len(_vector_ids("default", mid)) == 2
    assert rag.retrieve_context(vendor, tenant_id="default").strip() != ""

    resp = client.delete(f"/api/memory/{mid}", headers=_admin())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["affected"] == 1
    assert data["memory_ids"] == [mid]
    assert data["vectors_deleted"] == 1

    # DB 行没了（含归档视图，确认是真删不是置 archived）
    assert _db.list_vendor_memory(vendor, tenant_id="default",
                                  include_archived=True) == []
    # 向量没了
    assert _vector_ids("default", mid) == []
    # 不再注入
    assert rag.retrieve_context(vendor, tenant_id="default").strip() == ""
    # 审计留痕
    logs = _db.read_system_audit_log()
    assert any(str(l.get("action")) == "vendor_memory_delete"
               and mid in str(l.get("field")) for l in logs), logs


# -------------------------------------------------------------
# 2. 按供应商批量删除：只删该供应商
# -------------------------------------------------------------
def test_delete_by_vendor_removes_only_that_vendor():
    _db = _fresh_db()
    client = _client()
    vendor = "批量商戶B"
    other = "別的商戶B"
    mid1 = _seed_memory(_db, vendor, sample="批量商戶B 明细一：菜心 2斤 @10 = 20")
    mid2 = _seed_memory(_db, vendor, sample="批量商戶B 明细二：牛肉 3斤 @60 = 180")
    mid_other = _seed_memory(_db, other)

    resp = client.delete(f"/api/memory/vendor/{quote(vendor)}", headers=_admin())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["affected"] == 2
    assert sorted(data["memory_ids"]) == sorted([mid1, mid2])

    assert _db.list_vendor_memory(vendor, tenant_id="default",
                                  include_archived=True) == []
    assert _vector_ids("default", mid1) == []
    assert _vector_ids("default", mid2) == []
    assert rag.retrieve_context(vendor, tenant_id="default").strip() == ""

    # 别的供应商毫发无损
    assert len(_db.list_vendor_memory(other, tenant_id="default")) == 1
    assert len(_vector_ids("default", mid_other)) == 2
    assert rag.retrieve_context(other, tenant_id="default").strip() != ""
    logs = _db.read_system_audit_log()
    assert any(str(l.get("action")) == "vendor_memory_delete_by_vendor"
               for l in logs), logs


# -------------------------------------------------------------
# 3. 批量删除覆盖已归档行
# -------------------------------------------------------------
def test_delete_by_vendor_includes_archived_rows():
    _db = _fresh_db()
    client = _client()
    vendor = "歸檔商戶C"
    mid = _seed_memory(_db, vendor)

    # 连续覆写：起始分 0.7（P1-T2 起不再恒 1.0），扣两次即 0.46 <= 0.5 → archived
    for _ in range(5):
        _db.apply_vendor_memory_override_penalty(vendor, tenant_id="default")
    assert _db.list_vendor_memory(vendor, tenant_id="default") == []  # 默认只列 active
    archived = _db.list_vendor_memory(vendor, tenant_id="default",
                                      include_archived=True)
    assert [r["status"] for r in archived] == ["archived"]
    assert archived[0]["memory_id"] == mid

    resp = client.delete(f"/api/memory/vendor/{quote(vendor)}", headers=_admin())
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["affected"] == 1
    assert _db.list_vendor_memory(vendor, tenant_id="default",
                                  include_archived=True) == []
    assert _vector_ids("default", mid) == []


# -------------------------------------------------------------
# 4. 跨租户 404 且他租户数据不动
# -------------------------------------------------------------
def test_delete_cross_tenant_returns_404_and_keeps_rows():
    _db = _fresh_db()
    client = _client()
    vendor = "跨租商戶D"
    mid = _seed_memory(_db, vendor, tenant_id="shopA")

    resp = client.delete(f"/api/memory/{mid}", headers=_admin(**{"X-Tenant-Id": "default"}))
    assert resp.status_code == 404, resp.text
    # 他租户的行与向量分毫未动
    assert len(_db.list_vendor_memory(vendor, tenant_id="shopA")) == 1
    assert len(_vector_ids("shopA", mid)) == 2

    # 本租户可以删
    resp = client.delete(f"/api/memory/{mid}",
                         headers=_admin(**{"X-Tenant-Id": "shopA"}))
    assert resp.status_code == 200, resp.text
    assert _vector_ids("shopA", mid) == []

    # 批量删除同样按租户隔离
    mid2 = _seed_memory(_db, vendor, tenant_id="shopA")
    resp = client.delete(f"/api/memory/vendor/{quote(vendor)}",
                         headers=_admin(**{"X-Tenant-Id": "default"}))
    assert resp.status_code == 404, resp.text
    assert len(_db.list_vendor_memory(vendor, tenant_id="shopA")) == 1
    assert len(_vector_ids("shopA", mid2)) == 2


# -------------------------------------------------------------
# 5. 权限与不存在
# -------------------------------------------------------------
def test_delete_requires_admin():
    _db = _fresh_db()
    client = _client()
    vendor = "權限商戶E"
    mid = _seed_memory(_db, vendor)

    resp = client.delete(f"/api/memory/{mid}", headers={"X-Role": "staff"})
    assert resp.status_code == 403
    resp = client.delete(f"/api/memory/vendor/{quote(vendor)}",
                         headers={"X-Role": "staff"})
    assert resp.status_code == 403
    # 两次越权都没删掉任何东西
    assert len(_db.list_vendor_memory(vendor, tenant_id="default")) == 1
    assert len(_vector_ids("default", mid)) == 2


def test_delete_unknown_returns_404():
    _db = _fresh_db()
    client = _client()
    assert client.delete("/api/memory/does-not-exist",
                         headers=_admin()).status_code == 404
    resp = client.delete(f"/api/memory/vendor/{quote('不存在的商戶F')}", headers=_admin())
    assert resp.status_code == 404
    # 404 不写审计（没有实际删除动作）
    logs = _db.read_system_audit_log()
    assert not any(str(l.get("action", "")).startswith("vendor_memory_delete")
                   for l in logs), logs


# -------------------------------------------------------------
# 6. 向量库整目录不存在：不报错，也不新建空库
# -------------------------------------------------------------
def test_delete_without_vector_store_does_not_fail():
    _db = _fresh_db()
    client = _client()
    vendor = "無向量商戶G"
    mid, _is_new = _db.upsert_vendor_memory(
        vendor, "版式：手写单", "明细：菜心 2斤 @10 = 20", tenant_id="default")

    # 模拟 D5 清退后 / 从未写过向量的情形：整目录不存在
    absent = os.path.join(_TMP, f"absent_rag_{uuid.uuid4().hex[:6]}")
    rag.PERSIST_DIR = absent
    rag._vs = None
    rag._vs_tenant = {}

    resp = client.delete(f"/api/memory/{mid}", headers=_admin())
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["affected"] == 1
    assert resp.json()["data"]["vectors_deleted"] == 0
    assert _db.list_vendor_memory(vendor, tenant_id="default") == []
    assert not os.path.exists(absent), "删一条不含向量的记忆不应把空向量库建出来"


# -------------------------------------------------------------
# 7. DB 层防误删：两个选择器都不给时拒绝
# -------------------------------------------------------------
def test_db_delete_requires_selector():
    _db = _fresh_db()
    vendor = "守門商戶H"
    _seed_memory(_db, vendor)
    assert _db.delete_vendor_memory(tenant_id="default") == (0, [])
    assert len(_db.list_vendor_memory(vendor, tenant_id="default")) == 1


def test_db_delete_requires_tenant_when_memory_is_in_other_tenant():
    """db 层的租户隔离（端点之外的第二道门）：跨租户删不掉，且不返回 memory_id。"""
    _db = _fresh_db()
    vendor = "租戶門商戶I"
    mid = _seed_memory(_db, vendor, tenant_id="shopB")
    assert _db.delete_vendor_memory(memory_id=mid, tenant_id="default") == (0, [])
    assert len(_db.list_vendor_memory(vendor, tenant_id="shopB")) == 1
    affected, mids = _db.delete_vendor_memory(memory_id=mid, tenant_id="shopB")
    assert affected == 1 and mids == [mid]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

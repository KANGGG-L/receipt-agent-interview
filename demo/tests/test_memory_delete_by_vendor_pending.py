# -*- coding: utf-8 -*-
"""FP-2b / FP-2c：按供应商删记忆覆盖 pending + 向量删失败不静默（vector-first）。

FP-2b（pending 覆盖）：
  - DELETE /api/memory/vendor/{v} 连 pending_memory 一并删（覆盖全部 status，
    approved/rejected 历史行也是该主体的个人数据）；响应 data 增加 pending_deleted；
  - affected==0 但 pending 有行 → 200（"记忆+候选"整体语义）；两者皆 0 才 404。

FP-2c（vector-first + 失败上抛）：
  - 删除路径重排为「先删向量（失败即抛 MemoryVectorDeleteError）→ 后删 DB 行」，
    失败态落在「DB 完好、可重试、无孤儿」；端点回 500 VECTOR_DELETE_FAILED，
    不写删除审计（业务未发生）；
  - 归档/取代/容量淘汰三条内部路径沿用缺省 on_error="degrade"：打桩向量删除失败后
    DB 状态迁移照常提交、不抛 500（0.2-4 红线：FP-2c 失败上抛只作用于删除路径）；
  - 向量库整目录不存在仍提前返回 0、不建空库（沿用 test_memory_delete.py 的口径）。

反证口径：把 delete_memory 的 on_error 改回 "degrade"（或 try/except 吞掉异常），
(fp2c-1)/(fp2c-2) 的 500 断言翻红；把 api_memory 的 pending 接线去掉，(fp2b-1) 翻红。

全部离线：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 写入。
数据用 `_seed_memory` 直接落「DB 行 + 向量」两介质（与 test_memory_delete.py 同款）。
"""

import importlib
import json
import os
import sys
import tempfile
import uuid
from urllib.parse import quote

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_vendor_pending_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_vendor_pending.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services import memory_store  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"vp_{uuid.uuid4().hex[:8]}.db")
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


def _seed_memory(_db, vendor, tenant_id="default", sample=None):
    """落一条记忆：vendor_memory 行 + 同 memory_id 的两块向量。"""
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
    return rag._store(tenant_id)._collection.get(where={"memory_id": mid})["ids"]


def _seed_pending(_db, vendor, tenant_id="default", status="pending"):
    """入队一条候选记忆并按需流转到指定 status，返回 pending 行 id。"""
    pid, _is_new = _db.enqueue_pending_memory(
        vendor, f"{vendor} 候选：{status}", tenant_id=tenant_id)
    if status != "pending":
        assert _db.review_pending_memory(pid, status, "tester",
                                         tenant_id=tenant_id), pid
    return pid


def _pending_count(_db, vendor, tenant_id="default", status=None):
    return len(_db.list_pending_memory(status=status or "pending",
                                       tenant_id=tenant_id))


def _admin(**extra):
    headers = {"X-Role": "admin"}
    headers.update(extra)
    return headers


def _audit_actions(_db):
    return [str(e.get("action")) for e in _db.read_system_audit_log()]


class _BoomCollection:
    """collection.delete 一律抛错的打桩（模拟向量库故障）。"""

    def delete(self, where=None):
        raise RuntimeError("boom: 向量库故障")


class _BoomStore:
    _collection = _BoomCollection()


def _arm_vector_failure(monkeypatch):
    """把 rag._store 打桩成「删除必失败」；须在**种子落库之后**调用（种子还要写向量），
    PERSIST_DIR 保持真实存在（防 delete_memory_vectors 提前 return 0 而打不中）。"""
    monkeypatch.setattr(rag, "_store", lambda tenant_id=None: _BoomStore())


# -------------------------------------------------------------
# FP-2b：按供应商删除覆盖 pending
# -------------------------------------------------------------
def test_delete_by_vendor_covers_pending_all_statuses():
    _db = _fresh_db()
    client = _client()
    vendor = "候選商戶A"
    mid = _seed_memory(_db, vendor)
    p_pending = _seed_pending(_db, vendor, status="pending")
    p_approved = _seed_pending(_db, vendor, status="approved")
    p_rejected = _seed_pending(_db, vendor, status="rejected")
    assert _pending_count(_db, vendor, status="pending") == 1

    resp = client.delete(f"/api/memory/vendor/{quote(vendor)}", headers=_admin())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["affected"] == 1 and data["memory_ids"] == [mid]
    assert data["pending_deleted"] == 3, data

    # DB 行、向量、pending（全部 status）删净
    assert _db.list_vendor_memory(vendor, tenant_id="default",
                                  include_archived=True) == []
    assert _vector_ids("default", mid) == []
    for st in ("pending", "approved", "rejected"):
        assert _pending_count(_db, vendor, status=st) == 0, st
    # 审计 new 载荷含 pending 计数
    logs = [e for e in _db.read_system_audit_log()
            if str(e.get("action")) == "vendor_memory_delete_by_vendor"]
    assert logs, _audit_actions(_db)
    new_val = logs[-1]["new"]
    payload = json.loads(new_val) if isinstance(new_val, str) else new_val
    assert payload.get("pending_deleted") == 3, payload


def test_delete_by_vendor_pending_only_returns_200():
    """affected==0 但有 pending 行 → 200（删的就是「记忆+候选」整体）。"""
    _db = _fresh_db()
    client = _client()
    vendor = "僅候選商戶B"
    _seed_pending(_db, vendor, status="pending")

    resp = client.delete(f"/api/memory/vendor/{quote(vendor)}", headers=_admin())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["affected"] == 0 and data["pending_deleted"] == 1, data
    assert _pending_count(_db, vendor) == 0


def test_delete_by_vendor_neither_memory_nor_pending_404():
    _db = _fresh_db()
    client = _client()
    resp = client.delete(f"/api/memory/vendor/{quote('不存在的商戶C')}",
                         headers=_admin())
    assert resp.status_code == 404, resp.text
    assert not any(a.startswith("vendor_memory_delete")
                   for a in _audit_actions(_db)), _audit_actions(_db)


def test_delete_by_vendor_pending_cross_tenant_untouched():
    _db = _fresh_db()
    client = _client()
    vendor = "跨租候選商戶D"
    _seed_pending(_db, vendor, tenant_id="shopA", status="pending")

    resp = client.delete(f"/api/memory/vendor/{quote(vendor)}",
                         headers=_admin(**{"X-Tenant-Id": "default"}))
    assert resp.status_code == 404, resp.text
    # 他租户的候选行原样
    assert _pending_count(_db, vendor, tenant_id="shopA") == 1


# -------------------------------------------------------------
# FP-2c：向量删失败不静默（500 VECTOR_DELETE_FAILED，DB 完好）
# -------------------------------------------------------------
def test_vector_failure_by_vendor_returns_500_and_db_intact(monkeypatch):
    _db = _fresh_db()
    client = _client()
    vendor = "向量故障商戶E"
    mid = _seed_memory(_db, vendor)
    _seed_pending(_db, vendor, status="pending")
    _arm_vector_failure(monkeypatch)

    resp = client.delete(f"/api/memory/vendor/{quote(vendor)}", headers=_admin())
    assert resp.status_code == 500, resp.text
    assert resp.json()["code"] == "VECTOR_DELETE_FAILED", resp.text

    # DB 行与候选均未删（vector-first：失败发生在任何 DB 写之前）
    assert len(_db.list_vendor_memory(vendor, tenant_id="default")) == 1
    assert _pending_count(_db, vendor) == 1
    # 无孤儿：行还在、向量也还在 → 巡检仍一致
    report = _consistency()
    assert report["consistent"] is True, report
    # 不写删除审计（业务未发生）；留 vector_delete_failed 供排查
    actions = _audit_actions(_db)
    assert "vendor_memory_delete_by_vendor" not in actions, actions
    assert "vector_delete_failed" in actions, actions


def test_vector_failure_single_delete_returns_500_and_db_intact(monkeypatch):
    _db = _fresh_db()
    client = _client()
    vendor = "向量故障商戶F"
    mid = _seed_memory(_db, vendor)
    _arm_vector_failure(monkeypatch)

    resp = client.delete(f"/api/memory/{mid}", headers=_admin())
    assert resp.status_code == 500, resp.text
    assert resp.json()["code"] == "VECTOR_DELETE_FAILED", resp.text
    assert len(_db.list_vendor_memory(vendor, tenant_id="default")) == 1
    assert _consistency()["consistent"] is True
    actions = _audit_actions(_db)
    assert "vendor_memory_delete" not in actions, actions
    assert "vector_delete_failed" in actions, actions


def test_degrade_paths_unchanged_under_vector_failure(monkeypatch):
    """归档 / 容量淘汰 / 缺省 delete_memory_vectors：向量失败仅降级，DB 照常提交。"""
    _db = _fresh_db()
    vendor, tenant = "降級商戶G", "default"
    mid = _seed_memory(_db, vendor, tenant_id=tenant)
    _arm_vector_failure(monkeypatch)

    # 缺省参数（on_error="degrade"）：不抛、返回 0（三个既有内部调用点行为不变）
    assert memory_store.delete_memory_vectors(tenant, [mid]) == 0

    # 归档路径（R11）：打桩下 status 迁移照常提交，不抛 500
    for _ in range(5):
        _db.apply_vendor_memory_override_penalty(vendor, tenant_id=tenant)
    rows = _db.list_vendor_memory(vendor, tenant_id=tenant,
                                  include_archived=True)
    assert [r["status"] for r in rows] == ["archived"], rows

    # 容量淘汰路径（W-4）：打桩下归档照常提交（留 3 条 active，上限 2，写第 3 条触发）。
    # 此处只落 DB 行（打桩后向量写不进，恰好同态：向量删除也失败但仅降级）。
    from app.services import settings_service
    settings_service.set_value("memory_max_active_per_vendor", 2)
    for i in range(3):
        _db.upsert_vendor_memory(vendor, f"版式：手写单 {i}",
                                 f"降級商戶G 明细{['一', '二', '三'][i]}",
                                 tenant_id=tenant, source_kind="manual")
    rows = _db.list_vendor_memory(vendor, tenant_id=tenant,
                                  include_archived=True)
    assert sum(1 for r in rows if r["status"] == "active") == 2, rows
    assert any(str(e.get("action")) == "capacity_eviction"
               for e in _db.read_system_audit_log())


def test_delete_without_vector_store_still_succeeds():
    """向量库整目录不存在：提前返回 0、不抛、不建空库（F2c-4，沿用 D5 口径）。"""
    _db = _fresh_db()
    client = _client()
    vendor = "無向量商戶H"
    mid, _is_new = _db.upsert_vendor_memory(
        vendor, "版式：手写单", "明细：菜心 2斤 @10 = 20", tenant_id="default")

    absent = os.path.join(_TMP, f"absent_rag_{uuid.uuid4().hex[:6]}")
    rag.PERSIST_DIR = absent
    rag._vs = None
    rag._vs_tenant = {}

    resp = client.delete(f"/api/memory/{mid}", headers=_admin())
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["vectors_deleted"] == 0
    assert _db.list_vendor_memory(vendor, tenant_id="default") == []
    assert not os.path.exists(absent), "删除不应把空向量库建出来"


def test_concurrent_delete_between_dryrun_and_real_gives_404(monkeypatch):
    """F2c-6：dry_run 取到 mids 后行被并发删掉 → 实删 affected=0 → 端点 404。"""
    _db = _fresh_db()
    vendor = "並發商戶I"
    mid = _seed_memory(_db, vendor)

    real_delete = db.delete_vendor_memory
    state = {"dry_ran": False}

    def _racing_delete(*args, **kwargs):
        if kwargs.get("dry_run"):
            state["dry_ran"] = True
            return real_delete(*args, **kwargs)
        # 模拟「dry_run 之后、实删之前」另一线程把行删掉了
        if state["dry_ran"]:
            real_delete(memory_id=mid, tenant_id=kwargs.get("tenant_id"))
        return real_delete(*args, **kwargs)

    monkeypatch.setattr(db, "delete_vendor_memory", _racing_delete)
    result = memory_store.delete_memory(vendor=vendor, tenant_id="default")
    assert result["affected"] == 0 and result["memory_ids"] == [mid], result

    client = _client()
    resp = client.delete(f"/api/memory/vendor/{quote(vendor)}", headers=_admin())
    assert resp.status_code == 404, resp.text


def _consistency():
    from app.services.memory_consistency import check_memory_consistency
    return check_memory_consistency(tenant_id="default")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

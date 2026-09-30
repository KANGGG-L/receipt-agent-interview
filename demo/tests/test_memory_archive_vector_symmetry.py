# -*- coding: utf-8 -*-
"""R11 / R13：状态迁移（归档 / 取代 / 恢复）必须与向量层对称。

背景（主 agent live 实测）：把 4 条低信息量事实 status 改为 archived 后，巡检**立刻**
报 `consistent: False` + `orphan_mids` 恰好那 4 个 —— 向量还在库里、行已非 active。
即 P1-T1 的「删除」有向量级联，而「归档」没有，于是每归档一次就在向量库留一份垃圾，
且**没有任何写入路径会自愈**（巡检能报出来，说明巡检是对的）。

R13 是同一不对称的第二例：**取代**（supersede，active -> superseded，由
`db.save_memory_fact` 在同键写入时触发）起初也只改 status、不动向量层。实测判据探针
（`/tmp/rev_supersede_probe2.py`）：旧行先有向量 → 同键再写 → 巡检报
`SUPERSEDE_LEAVES_ORPHAN_VECTOR`。

本用例覆盖任务书 6 条判定：
(a) active 记忆 + 向量 → 归档 → 向量被删、巡检 `consistent: True`、用其原文召不回；
(b) 再恢复为 active → 向量被**补回**（走 AK2 的「确保向量存在」单点实现）且可召回
    （向量腿单独也能召回；重复点恢复 → 409 且不重复写向量）；
(c) 删除路径仍只删一次（不因本改动被重复调用：用调用计数钉住）；
(d) 幂等：连续两次归档 → 第二次无异常、无重复删除（归档级联不再被触发）；
(e) 降级：向量库不可用时状态变更仍成功、且有 warning 留痕（不把归档/恢复整体弄失败）。

R13（与 R11 同族，放在同一文件里）：
(f) supersede（db 层，与判据探针同形）→ 旧行向量被级联删、巡检 `consistent: True`、
    `orphan_mids == []`；
(g) 生产链路 `rag.approve_pending_to_memory` 同键二次 approve → 旧向量被清、两侧一致
    （证明「接线真的被走到」，不是只有 db 层函数可用）；
(h) 链式取代的幂等：每条旧行恰好被删一次、已 superseded 的行不再被重复删、内容幂等
    命中时完全不触发级联；
(i) 降级：向量库不可用时 supersede 仍成功（取代关系已提交）+ warning 留痕。

反向证明（人工执行，见任务报告）：去掉归档侧的向量级联（`archive_memory_vectors` 不再
被调用）→ (a) 必须失败（巡检报 orphan_mids、向量仍在）。(c)/(d) 的调用计数同时会失败。
去掉 supersede 侧的级联（`db.save_memory_fact` 不再调用 `archive_memory_vectors`）→
(f)/(g)/(h) 必须失败。

全部离线假数据：每个用例独立临时 DB + 独立临时 RAG 目录，零外部调用、零 live 写入。
向量走默认嵌入档（hash char-ngram，纯本地、不发网络请求）。
"""

import importlib
import logging
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_archive_vector_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_archive_vector.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services import memory_consistency as mc  # noqa: E402
from app.services import memory_store  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}


def _fresh_db():
    """每个用例独立数据库文件 + 独立向量目录，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"av_{uuid.uuid4().hex[:8]}.db")
    os.environ["RAG_DIR"] = os.path.join(_TMP, f"rag_{uuid.uuid4().hex[:8]}")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _seed_memory_with_vector(_db, vendor, tenant_id, items_text="菜心 2斤 @10 = 20"):
    """落一条 active 记忆（DB 行 + 向量），返回 memory_id。

    走 `rag.ingest_memory`（生产写入链路）而不是只写 DB 行：本用例要验证的正是
    「向量层与状态迁移的对称」，前提必须真的有一枚向量。
    """
    rag.ingest_memory(vendor, items_text, notes=f"{vendor} 版式：手寫長單",
                      tenant_id=tenant_id, source_kind="manual")
    rows = _db.list_vendor_memory(vendor, tenant_id=tenant_id)
    assert rows, "前置失败：记忆未落库，后续断言无意义"
    return rows[0]["memory_id"]


def _vector_present(mid, tenant_id):
    return rag._memory_vectors_present(rag._store(tenant_id), mid)


def _archive(_db, vendor, tenant_id):
    """走真实的淘汰路径把记忆压到归档（不直接改列，保证测的是产品行为）。

    不假设具体扣分次数：步长是 settings 可调的，写死次数会在调参后变成假失败。
    """
    for _ in range(10):
        _db.apply_vendor_memory_override_penalty(vendor, tenant_id=tenant_id)
        row = _db.list_vendor_memory(vendor, tenant_id=tenant_id,
                                     include_archived=True)[0]
        if row["status"] == "archived":
            return True
    return False


def _consistency(tenant_id):
    return mc.check_memory_consistency(
        tenant_id=tenant_id, db_path=os.environ["DB_PATH"],
        rag_dir=os.environ["RAG_DIR"])


def _admin_headers(tenant_id):
    return {"X-Role": "admin", "X-Tenant-Id": tenant_id}


# -------------------------------------------------------------
# (a) 归档 → 向量被删 + 巡检一致 + 召不回（治理承诺）
# -------------------------------------------------------------
def test_archive_cascades_vector_delete_and_blocks_recall():
    _db = _fresh_db()
    vendor = "對稱商戶A"
    tenant = "av_t1"
    mid = _seed_memory_with_vector(_db, vendor, tenant)

    # 前置：向量在库、可召回、巡检一致
    assert _vector_present(mid, tenant) is True, "前置失败：种子记忆没有向量"
    assert "菜心" in rag.retrieve_context(vendor, tenant_id=tenant)
    assert _consistency(tenant)["consistent"] is True

    # 归档（真实淘汰路径）
    assert _archive(_db, vendor, tenant), "前置失败：记忆未被归档，后续断言无意义"

    # 向量被级联删掉：巡检不再报 orphan
    assert _vector_present(mid, tenant) is False, (
        "归档未级联删除向量：向量库里仍有该 memory_id 的向量块")
    report = _consistency(tenant)
    assert report["consistent"] is True, report
    assert report["orphan_mids"] == [], report["orphan_mids"]
    assert report["missing_vector_mids"] == [], report["missing_vector_mids"]

    # 治理承诺：归档后召不回（注入面已排除该行）
    assert rag.retrieve_context(vendor, tenant_id=tenant).strip() == ""


# -------------------------------------------------------------
# (b) 恢复 → 向量被补回（走单点「确保向量存在」）且可召回
# -------------------------------------------------------------
def test_restore_backfills_vector_and_recall_returns():
    _db = _fresh_db()
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app)
    vendor = "對稱商戶B"
    tenant = "av_t2"
    mid = _seed_memory_with_vector(_db, vendor, tenant)
    assert _archive(_db, vendor, tenant)
    assert _vector_present(mid, tenant) is False

    # 恢复走生产端点（api_memory 的 POST /api/memory/{memory_id}/revive）
    resp = client.post(f"/api/memory/{mid}/revive", headers=_admin_headers(tenant))
    assert resp.status_code == 200, resp.text

    # 向量被补回
    assert _vector_present(mid, tenant) is True, (
        "恢复未补写向量：DB 行已 active 而向量库无该 mid（巡检会报缺向量）")
    report = _consistency(tenant)
    assert report["consistent"] is True, report

    # 注入面恢复
    assert "菜心" in rag.retrieve_context(vendor, tenant_id=tenant)

    # 更强的判据：**向量腿**单独也能召回（否则"能召回"可能只是精确腿的功劳，
    # 补写向量的证据就不成立）。use_exact/use_keyword 是 _build_recall_pool 的
    # 观测开关，只被用例使用。
    rows = _db.list_vendor_memory(vendor, tenant_id=tenant)
    pool, stats = rag._build_recall_pool(
        vendor, tenant, rows, "菜心", 3, use_exact=False, use_keyword=False)
    assert stats["vector_hits"] >= 1, ("向量腿召回为 0，补写的向量未参与检索", stats)
    assert pool, pool

    # 幂等（另一方向）：再点一次恢复 → 409 已 active，且**不得重复写向量**
    before_ids = rag._store(tenant)._collection.get(where={"memory_id": mid})["ids"]
    resp2 = client.post(f"/api/memory/{mid}/revive", headers=_admin_headers(tenant))
    assert resp2.status_code == 409, resp2.text
    after_ids = rag._store(tenant)._collection.get(where={"memory_id": mid})["ids"]
    assert len(before_ids) == len(after_ids) == 1, (
        "重复恢复不得重复写向量", before_ids, after_ids)


# -------------------------------------------------------------
# (c) 删除路径仍只删一次（不被本改动重复调用）
# -------------------------------------------------------------
def test_delete_path_deletes_vector_exactly_once(monkeypatch):
    _db = _fresh_db()
    vendor = "對稱商戶C"
    tenant = "av_t3"
    mid = _seed_memory_with_vector(_db, vendor, tenant)
    assert _vector_present(mid, tenant) is True

    calls = []
    original = memory_store.delete_memory_vectors

    # FP-2c：删除路径以 on_error="raise" 调用级联，桩须透传该参（断言不变）
    def _counting(tenant_id, memory_ids, on_error="degrade"):
        calls.append((tenant_id, list(memory_ids)))
        return original(tenant_id, memory_ids, on_error=on_error)

    archive_calls = []
    monkeypatch.setattr(memory_store, "delete_memory_vectors", _counting)
    monkeypatch.setattr(
        memory_store, "archive_memory_vectors",
        lambda *a, **k: archive_calls.append((a, k)) or 0)

    result = memory_store.delete_memory(memory_id=mid, tenant_id=tenant)

    assert result["affected"] == 1
    assert result["memory_ids"] == [mid]
    assert result["vectors_deleted"] == 1
    assert len(calls) == 1, ("删除路径的向量级联被调用了 %d 次（应为 1 次）" % len(calls))
    assert calls[0] == (tenant, [mid])
    assert archive_calls == [], "删除路径不得触发归档级联"
    assert _vector_present(mid, tenant) is False
    assert _consistency(tenant)["consistent"] is True


# -------------------------------------------------------------
# (d) 幂等：连续两次归档 → 无异常、无重复删除
# -------------------------------------------------------------
def test_repeated_archive_is_idempotent(monkeypatch):
    _db = _fresh_db()
    vendor = "對稱商戶D"
    tenant = "av_t4"
    mid = _seed_memory_with_vector(_db, vendor, tenant)

    calls = []
    original = memory_store.archive_memory_vectors

    def _counting(tenant_id, memory_ids, vendor=None):
        calls.append((tenant_id, list(memory_ids)))
        return original(tenant_id, memory_ids, vendor=vendor)

    monkeypatch.setattr(memory_store, "archive_memory_vectors", _counting)

    assert _archive(_db, vendor, tenant)
    assert len(calls) == 1, ("首次归档应恰好触发一次级联，实际 %d 次" % len(calls))
    assert calls[0][1] == [mid]
    assert _vector_present(mid, tenant) is False

    # 已归档后再连续调用两次淘汰路径：无异常、且不再触发级联（幂等）
    for _ in range(2):
        assert _db.apply_vendor_memory_override_penalty(
            vendor, tenant_id=tenant) == 0
    assert len(calls) == 1, ("已归档行不应再次触发向量级联，实际 %d 次" % len(calls))
    assert _vector_present(mid, tenant) is False
    assert _consistency(tenant)["consistent"] is True


# -------------------------------------------------------------
# (e) 向量库不可用 → 状态变更仍成功 + warning 留痕（不整条失败）
# -------------------------------------------------------------
def test_archive_degrades_when_vector_store_unavailable(monkeypatch, caplog):
    _db = _fresh_db()
    vendor = "對稱商戶E"
    tenant = "av_t5"
    mid = _seed_memory_with_vector(_db, vendor, tenant)

    def _boom(*_a, **_k):
        raise RuntimeError("vector store unavailable (injected)")

    monkeypatch.setattr(rag, "_store", _boom)
    with caplog.at_level(logging.WARNING):
        assert _archive(_db, vendor, tenant), "向量库故障不得阻断归档（状态变更仍须成功）"

    row = _db.list_vendor_memory(vendor, tenant_id=tenant,
                                 include_archived=True)[0]
    assert row["status"] == "archived"
    assert row["memory_id"] == mid
    assert any("向量级联删除失败" in r.getMessage() for r in caplog.records), (
        "向量库不可用时必须留下 warning 痕迹", [r.getMessage() for r in caplog.records])


def test_restore_degrades_when_vector_store_unavailable(monkeypatch, caplog):
    _db = _fresh_db()
    vendor = "對稱商戶F"
    tenant = "av_t6"
    mid = _seed_memory_with_vector(_db, vendor, tenant)
    assert _archive(_db, vendor, tenant)
    assert _vector_present(mid, tenant) is False

    def _boom(*_a, **_k):
        raise RuntimeError("vector store unavailable (injected)")

    monkeypatch.setattr(rag, "_store", _boom)
    with caplog.at_level(logging.WARNING):
        result = _db.revive_vendor_memory(mid, tenant_id=tenant)

    # 状态变更成功（不因向量层故障失败），且有 warning 留痕
    assert result["revived"] == 1
    assert result["status"] == "active"
    row = _db.list_vendor_memory(vendor, tenant_id=tenant)[0]
    assert row["status"] == "active"
    assert any("degraded" in r.getMessage() or "failed" in r.getMessage()
               for r in caplog.records), (
        "向量库不可用时必须留下 warning 痕迹", [r.getMessage() for r in caplog.records])


# -------------------------------------------------------------
# R13：(f) 取代 → 向量被级联删 + 巡检一致（与审核判据探针同形）
# -------------------------------------------------------------
def test_supersede_cascades_vector_delete_and_keeps_consistency():
    """旧行先有向量 → 同键再写使其 superseded → 孤儿清零、巡检一致。"""
    _db = _fresh_db()
    vendor = "取代商戶A"
    tenant = "sv_t1"
    key = "alias:取代A"

    r1 = _db.save_memory_fact(tenant, vendor, key, notes="第一版", sample="s1")
    mid1 = str(r1["memory_id"])
    # 镜像生产链路：旧行（上一次 approve 落的）已经有向量
    assert rag.ensure_memory_vectors(tenant, mid1, vendor=vendor) is True, (
        "前置失败：旧行没有向量，后续断言无意义")
    assert _vector_present(mid1, tenant) is True
    assert _consistency(tenant)["consistent"] is True

    r2 = _db.save_memory_fact(tenant, vendor, key, notes="第二版", sample="s2")
    mid2 = str(r2["memory_id"])
    assert [str(x) for x in r2["superseded"]] == [mid1], r2

    # 级联删除：旧行的向量不再残留（R13 的核心判据）
    assert _vector_present(mid1, tenant) is False, (
        "supersede 未级联删除向量：旧 memory_id 的向量仍在库里（巡检会报 orphan）")
    # 新行补上向量（生产里由 approve 的向量写入链路完成）→ 两侧完全一致
    assert rag.ensure_memory_vectors(tenant, mid2, vendor=vendor) is True
    report = _consistency(tenant)
    assert report["orphan_mids"] == [], report["orphan_mids"]
    assert report["missing_vector_mids"] == [], report["missing_vector_mids"]
    assert report["consistent"] is True, report

    # 治理语义保持不变：旧行仍可查（历史），但默认读口径只剩新行
    rows = _db.list_vendor_memory(vendor, tenant_id=tenant)
    assert [r["memory_id"] for r in rows] == [mid2], rows
    all_rows = _db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)
    assert {r["memory_id"] for r in all_rows} == {mid1, mid2}


# -------------------------------------------------------------
# R13：(g) 生产链路 approve 同键二次写入 → 旧向量被清（证明接线真被走到）
# -------------------------------------------------------------
def test_approve_supersede_cascades_vector_via_production_path():
    """rag.approve_pending_to_memory 走 db.save_memory_fact 的结构化分支，
    第二次 approve 取代第一次 —— 旧向量必须被清掉（消费 saved['superseded']）。"""
    _db = _fresh_db()
    from app.services.memory_facts import KIND_ALIAS

    vendor = "取代商戶B"
    tenant = "sv_t2"
    key = "alias:取代B"
    common = dict(tenant_id=tenant, source_kind="extract",
                  supplier_id="91", fact_kind=KIND_ALIAS, fact_key=key,
                  written_by="tester@demo.hk")

    rag.approve_pending_to_memory(vendor, "第一版別名：B商號", fact_value="B商號", **common)
    rows = _db.list_vendor_memory(vendor, tenant_id=tenant)
    assert len(rows) == 1, rows
    mid1 = str(rows[0]["memory_id"])
    assert _vector_present(mid1, tenant) is True, (
        "前置失败：approve 未写向量（后续断言无意义）")
    assert _consistency(tenant)["consistent"] is True

    rag.approve_pending_to_memory(vendor, "第二版別名：B商號有限公司",
                                  fact_value="B商號有限公司", **common)

    assert _vector_present(mid1, tenant) is False, (
        "approve 的 supersede 分支未级联删向量（db.save_memory_fact 返回的 superseded "
        "必须被消费；本用例覆盖的正是这条生产链路）")
    rows = _db.list_vendor_memory(vendor, tenant_id=tenant)
    assert len(rows) == 1 and str(rows[0]["memory_id"]) != mid1, rows
    report = _consistency(tenant)
    assert report["orphan_mids"] == [], report["orphan_mids"]
    assert report["missing_vector_mids"] == [], report["missing_vector_mids"]
    assert report["consistent"] is True, report


# -------------------------------------------------------------
# R13：(h) 链式取代幂等 + 内容幂等命中不触发级联
# -------------------------------------------------------------
def test_supersede_cascade_deletes_each_row_exactly_once(monkeypatch):
    _db = _fresh_db()
    vendor = "取代商戶C"
    tenant = "sv_t3"
    key = "alias:取代C"

    calls = []
    original = memory_store.delete_memory_vectors

    def _counting(tenant_id, memory_ids):
        calls.append((tenant_id, list(memory_ids)))
        return original(tenant_id, memory_ids)

    monkeypatch.setattr(memory_store, "delete_memory_vectors", _counting)

    mids = []
    for i in range(3):
        r = _db.save_memory_fact(tenant, vendor, key, notes="第%d版" % (i + 1))
        mid = str(r["memory_id"])
        mids.append(mid)
        # 镜像生产：每一版落库后都有向量
        assert rag.ensure_memory_vectors(tenant, mid, vendor=vendor) is True

    # 每条旧行恰好被删一次，且删的是「上一次仍 active 的那条」
    assert calls == [(tenant, [mids[0]]), (tenant, [mids[1]])], calls
    assert _vector_present(mids[0], tenant) is False
    assert _vector_present(mids[1], tenant) is False
    assert _vector_present(mids[2], tenant) is True, "最新一条仍 active，向量不得被删"

    report = _consistency(tenant)
    assert report["orphan_mids"] == [], report["orphan_mids"]
    assert report["missing_vector_mids"] == [], report["missing_vector_mids"]
    assert report["consistent"] is True, report

    # 内容幂等命中（同 vendor/tenant/notes/sample）→ 复用既有行，不取代、不触发级联
    dup = _db.save_memory_fact(tenant, vendor, key, notes="第3版")
    assert dup["is_new"] is False, dup
    assert dup["superseded"] == [], dup
    assert len(calls) == 2, ("幂等命中不得再触发向量级联", calls)
    assert _vector_present(mids[2], tenant) is True
    assert _consistency(tenant)["consistent"] is True


# -------------------------------------------------------------
# R13：(i) 向量库不可用 → 取代仍成功（关系已提交）+ warning 留痕
# -------------------------------------------------------------
def test_supersede_degrades_when_vector_store_unavailable(monkeypatch, caplog):
    _db = _fresh_db()
    vendor = "取代商戶D"
    tenant = "sv_t4"
    key = "alias:取代D"

    r1 = _db.save_memory_fact(tenant, vendor, key, notes="第一版", sample="s1")
    mid1 = str(r1["memory_id"])
    assert rag.ensure_memory_vectors(tenant, mid1, vendor=vendor) is True

    def _boom(*_a, **_k):
        raise RuntimeError("vector store unavailable (injected)")

    monkeypatch.setattr(rag, "_store", _boom)
    with caplog.at_level(logging.WARNING):
        r2 = _db.save_memory_fact(tenant, vendor, key, notes="第二版", sample="s2")

    # 取代关系照常提交（不因向量层故障失败）
    assert [str(x) for x in r2["superseded"]] == [mid1], r2
    statuses = {r["memory_id"]: r["status"] for r in
                _db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)}
    assert statuses.get(mid1) == "superseded", statuses
    assert any("向量级联删除失败" in r.getMessage() for r in caplog.records), (
        "向量库不可用时必须留下 warning 痕迹", [r.getMessage() for r in caplog.records])

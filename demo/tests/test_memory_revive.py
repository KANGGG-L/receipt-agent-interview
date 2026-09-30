# -*- coding: utf-8 -*-
"""P1-T2：归档恢复通道 + 衰减双向生效 测试。

覆盖：
1. 归档记忆在归档期间不被注入；POST /api/memory/{memory_id}/revive 后 status 回
   active、decay_score 重置为起始分，并重新被 retrieve_context 注入
2. 未归档记忆恢复 → 409（目标明明可见，不伪装成 404），且分数/时间戳都不被重置
3. 跨租户恢复 → 404，且他租户的行分毫未动；本租户可正常恢复
4. 不存在 → 404 且不写审计
5. 权限：非 admin → 403，且什么都没改
6. 命中强化真正抬升 decay_score（严格大于命中前），hit_count 同步累加
7. 新记忆起始分 = memory_initial_decay_score 且严格低于上限 1.0、高于归档阈值

反向证明（人工执行，见任务报告）：把「新行起始分」改回 1.0，第 6 条用例的
"命中后严格上升"断言必须失败 —— 起始分与上限相同则 min(1.0, cur+bonus) 恒等，
这正是本任务要修的「衰减单向」缺陷。

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
记忆只落 DB 行（不经向量层）：本用例关心的是归档/恢复对注入面的影响，检索的精确
匹配通道只读 DB；同时避免与其它波次正在改的写入链路耦合。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_revive_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_memory_revive.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services import settings_service  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"revive_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _admin(**extra):
    headers = {"X-Role": "admin"}
    headers.update(extra)
    return headers


def _seed_memory(_db, vendor, tenant_id):
    """落一条 DB 记忆，返回 memory_id。"""
    mid, _is_new = _db.upsert_vendor_memory(
        vendor, "版式：手寫長單", f"{vendor} 明細：菜心 2斤 @10 = 20",
        tenant_id=tenant_id, source_kind="manual")
    return mid


def _archive(_db, vendor, tenant_id):
    """走真实的淘汰路径把记忆压到归档（不直接改列，保证测的是产品行为）。

    不假设具体扣分次数：步长是 settings 可调的，写死次数会在调参后变成假失败。
    返回是否确实归档，由调用方断言。
    """
    for _ in range(10):
        _db.apply_vendor_memory_override_penalty(vendor, tenant_id=tenant_id)
        row = _db.list_vendor_memory(vendor, tenant_id=tenant_id,
                                     include_archived=True)[0]
        if row["status"] == "archived":
            return True
    return False


def _initial_decay():
    return settings_service.get_float("memory_initial_decay_score")


# -------------------------------------------------------------
# 1. 归档 → 恢复 → 重新注入（本任务的核心交付）
# -------------------------------------------------------------
def test_archived_memory_revivable_and_injectable():
    _db = _fresh_db()
    client = _client()
    vendor = "恢復商戶A"
    tenant = "rv_t1"
    mid = _seed_memory(_db, vendor, tenant)

    # 前置：起始分高于归档阈值 → 正常注入
    assert "菜心" in rag.retrieve_context(vendor, tenant_id=tenant)

    # 归档（真实淘汰路径）→ 停止注入
    assert _archive(_db, vendor, tenant), "前置失败：记忆未被归档，后续断言无意义"
    assert rag.retrieve_context(vendor, tenant_id=tenant).strip() == ""

    # 恢复
    resp = client.post(f"/api/memory/{mid}/revive",
                       headers=_admin(**{"X-Tenant-Id": tenant}))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["revived"] == 1
    assert data["status"] == "active"

    row = _db.list_vendor_memory(vendor, tenant_id=tenant)[0]
    assert row["status"] == "active"
    assert abs(row["decay_score"] - _initial_decay()) < 1e-9, \
        f"恢复应把分数重置为起始分，实际 {row['decay_score']}"
    # 重置后的分数必须能过检索的衰减门（否则"恢复"只是换了个不可见状态）
    assert row["decay_score"] > _db.memory_archive_threshold()

    # 重新被注入
    ctx = rag.retrieve_context(vendor, tenant_id=tenant)
    assert "菜心" in ctx, f"恢复后应重新注入，实际 {ctx!r}"

    # 审计留痕（谁在什么时候把哪条记忆从归档拉回）
    logs = _db.read_system_audit_log()
    assert any(str(l.get("action")) == "vendor_memory_revive"
               and mid in str(l.get("field")) for l in logs), logs


# -------------------------------------------------------------
# 2. 未归档恢复 → 409 且不改分（恢复不是"重置分数"的万能入口）
# -------------------------------------------------------------
def test_revive_active_memory_returns_409_without_reset():
    _db = _fresh_db()
    client = _client()
    vendor = "未歸檔商戶B"
    tenant = "rv_t2"
    mid = _seed_memory(_db, vendor, tenant)
    before = _db.list_vendor_memory(vendor, tenant_id=tenant)[0]

    resp = client.post(f"/api/memory/{mid}/revive",
                       headers=_admin(**{"X-Tenant-Id": tenant}))
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "ALREADY_ACTIVE"

    after = _db.list_vendor_memory(vendor, tenant_id=tenant)[0]
    assert after["decay_score"] == before["decay_score"], "未归档的记忆不得被重置分数"
    assert after["updated_at"] == before["updated_at"], "未归档的记忆不该被写一次"
    logs = _db.read_system_audit_log()
    assert not any(str(l.get("action")) == "vendor_memory_revive" for l in logs), logs


# -------------------------------------------------------------
# 3. 跨租户 404 且他租户数据不动
# -------------------------------------------------------------
def test_revive_cross_tenant_returns_404_and_keeps_rows():
    _db = _fresh_db()
    client = _client()
    vendor = "跨租商戶C"
    tenant = "rv_shopA"
    mid = _seed_memory(_db, vendor, tenant)
    assert _archive(_db, vendor, tenant)

    resp = client.post(f"/api/memory/{mid}/revive",
                       headers=_admin(**{"X-Tenant-Id": "rv_default"}))
    assert resp.status_code == 404, resp.text
    row = _db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)[0]
    assert row["status"] == "archived", "跨租户请求不得改动他租户的记忆状态"
    assert row["decay_score"] <= _db.memory_archive_threshold()

    # 本租户可以恢复
    resp = client.post(f"/api/memory/{mid}/revive",
                       headers=_admin(**{"X-Tenant-Id": tenant}))
    assert resp.status_code == 200, resp.text
    assert _db.list_vendor_memory(vendor, tenant_id=tenant)[0]["status"] == "active"

    # db 层的租户隔离（端点之外的第二道门）
    assert _db.revive_vendor_memory(mid, tenant_id="rv_default")["found"] == 0


# -------------------------------------------------------------
# 4. 不存在 → 404 且不写审计
# -------------------------------------------------------------
def test_revive_unknown_returns_404_without_audit():
    _db = _fresh_db()
    client = _client()
    resp = client.post("/api/memory/does-not-exist/revive", headers=_admin())
    assert resp.status_code == 404, resp.text
    logs = _db.read_system_audit_log()
    assert not any(str(l.get("action")) == "vendor_memory_revive" for l in logs), logs


# -------------------------------------------------------------
# 5. 权限：非 admin 403 且什么都没改
# -------------------------------------------------------------
def test_revive_requires_admin():
    _db = _fresh_db()
    client = _client()
    vendor = "權限商戶D"
    tenant = "rv_t5"
    mid = _seed_memory(_db, vendor, tenant)
    assert _archive(_db, vendor, tenant)

    resp = client.post(f"/api/memory/{mid}/revive",
                       headers={"X-Role": "staff", "X-Tenant-Id": tenant})
    assert resp.status_code == 403
    row = _db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)[0]
    assert row["status"] == "archived", "越权请求不得恢复任何记忆"


# -------------------------------------------------------------
# 6. 命中强化真正抬升 decay_score（反证目标：起始分若=上限则本用例失败）
# -------------------------------------------------------------
def test_hit_bonus_actually_raises_decay_score():
    _db = _fresh_db()
    vendor = "抬分商戶E"
    tenant = "rv_t6"
    _seed_memory(_db, vendor, tenant)

    before = _db.list_vendor_memory(vendor, tenant_id=tenant)[0]
    assert rag.retrieve_context(vendor, tenant_id=tenant).strip() != ""

    hit1 = _db.list_vendor_memory(vendor, tenant_id=tenant)[0]
    assert hit1["decay_score"] > before["decay_score"], (
        f"命中强化未生效：{before['decay_score']} → {hit1['decay_score']}"
        "（起始分若与上限 1.0 相同，min(1.0, cur+bonus) 恒等）")
    assert hit1["hit_count"] == before["hit_count"] + 1

    # 未到上限前继续单调上升
    assert rag.retrieve_context(vendor, tenant_id=tenant).strip() != ""
    hit2 = _db.list_vendor_memory(vendor, tenant_id=tenant)[0]
    assert hit2["decay_score"] > hit1["decay_score"], \
        f"第二次命中应继续上升：{hit1['decay_score']} → {hit2['decay_score']}"
    assert hit2["hit_count"] == 2
    assert hit2["last_hit_at"]


# -------------------------------------------------------------
# 7. 起始分口径：低于上限、高于归档阈值
# -------------------------------------------------------------
def test_new_memory_starts_below_ceiling_and_above_threshold():
    _db = _fresh_db()
    vendor = "起始分商戶F"
    tenant = "rv_t7"
    initial = _initial_decay()
    assert 0.0 < initial < 1.0, "起始分必须低于上限 1.0，否则命中强化恒等"

    _seed_memory(_db, vendor, tenant)
    got = _db.list_vendor_memory(vendor, tenant_id=tenant)[0]["decay_score"]
    assert abs(got - initial) < 1e-9, \
        f"新行起始分应取 memory_initial_decay_score={initial}，实际 {got}"
    assert got > _db.memory_archive_threshold(), \
        "起始分必须高于归档阈值，否则新记忆写入即不可注入"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

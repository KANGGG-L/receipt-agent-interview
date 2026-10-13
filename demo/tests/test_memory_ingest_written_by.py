# -*- coding: utf-8 -*-
"""U1 审计链收口：approve_receipt 自由文本 ingest 行的 written_by 可追溯。

背景（E2E 断言 1.11 / 审计 A1 口径缺口）：approve_receipt 回写 VendorMemory 的
自由文本行（版式 + 已确认明细）经 `rag.ingest_memory` 落库，而该函数此前没有
`written_by` 形参，approve 调用点无法透传操作者 —— 该路径落库行 written_by 为空，
「每条生效记忆可追溯到写入者」在一条写入路径上有缺口。人工闸路径
（api_memory.approve_pending → approve_pending_to_memory，见
test_memory_written_by_audit.py）此前已接好，本文件补上另一条。

判别式判据：
1. 走真实 approve 端点（HTTP → api_receipts.approve_receipt → rag.ingest_memory →
   DB 行）：自由文本回写行 written_by 非空且**等于本次请求的操作者**（只断言"有值"
   不够：落了常量/兜底身份同样追溯不到是谁批的）；
2. 两个不同操作者各批一张单：各自的行记各自的写入者（排除"全局常量"假通过）；
3. 向后兼容：`rag.ingest_memory` 旧调用形态（不传 written_by）仍能写入、不报错，
   该列为空值（缺省不编造 "system" 之类的写入者）。

反证说明：去掉 api_receipts.approve_receipt 里的 `written_by=who` 透传 →
用例 1/2 必须失败；去掉 rag.ingest_memory 的 written_by 形参透传（经
_persist_memory_row 落列）→ 用例 1/2 失败且用例 3 的兼容分支回归。

全部离线假数据：每例独立临时 DB + 临时 RAG 目录，零外部调用、零 live 库/向量库写入。
"""

import importlib
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）。
# app.db 在 import 期执行 _make_engine() 的迁移块（ALTER），不带 DB_PATH 就会打到 live 库。
_TMP = tempfile.mkdtemp(prefix="memory_ingest_written_by_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_ingest_written_by.db")
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
    """每个用例独立数据库文件，避免跨用例串扰（与既有记忆用例同款隔离）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"iwb_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _raw_vendor_rows(path, vendor):
    """裸 SQL 读该供应商的全部记忆行：只有从库里读出来才算"真写入"
    （ORM 属性可能是假象）。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM vendor_memory WHERE vendor = ? ORDER BY created_at",
            (vendor,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _mk_parsed_receipt(_db, vendor):
    """造一张 parsed 单据（approve 端点接受的状态）带明细行。"""
    rid = _db.create_receipt_record(supplier_name=vendor, status="parsed",
                                    tenant_id="default")
    _db.update_receipt(rid, receipt_date="2026-08-24", total_amount=20)
    _db.set_receipt_items(rid, [{
        "name": "菜心", "raw_name": "菜心", "quantity": 2, "unit": "斤",
        "raw_unit": "斤", "unit_price": 10, "amount": 20,
        "sku_id": None, "cost_center_id": None, "confidence": 0.9,
        "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
        "price_diff_percent": 0.0, "unit_conversion_warning": "",
        "fuzzy_candidates": [], "entity_candidates": [],
    }])
    return rid


def _approve(_db, client, vendor, role="owner", forged_email="forged.operator@demo.hk"):
    """走真实 approve 端点。演示态身份只认角色，伪造的 X-Email 必须被忽略。"""
    rid = _mk_parsed_receipt(_db, vendor)
    row = _db.get_receipt_row(rid, tenant_id="default")
    resp = client.post(f"/api/receipt/{rid}/approve", json={"version": row.version},
                       headers={"X-Role": role, "X-Tenant-Id": "default",
                                "X-Email": forged_email})
    assert resp.status_code == 200, resp.text
    return rid, resp


# -------------------------------------------------------------
# 1. approve 自由文本回写行：written_by 非空且为本次操作者
# -------------------------------------------------------------
def test_approve_free_text_ingest_row_records_operator():
    """演示态操作者是角色邮箱 owner@demo.hk。伪造 X-Email 不得进入 written_by。

    旧契约把 X-Email: boss.a@demo.hk 当成操作者。phase1 spec 3.2 / 3.6 已锁定：
    AUTH_ENABLED=0 时邮箱固定为 {role}@demo.hk，X-Email 不得进入审计。
    """
    _db = _fresh_db()
    vendor = "審計鏈直批商號"
    forged = "boss.a@demo.hk"
    _approve(_db, _client(), vendor, role="owner", forged_email=forged)

    rows = _raw_vendor_rows(_db.DB_PATH, vendor)
    assert len(rows) == 1, f"一次 approve 应恰好回写一条记忆，实得 {len(rows)} 条"
    row = rows[0]
    assert row["source_kind"] == "approve", (
        f"本用例钉的是 approve 直写路径，实际 source_kind={row['source_kind']!r}")
    assert row["written_by"] == "owner@demo.hk", (
        f"written_by 必须是已解析的角色邮箱，实际 {row['written_by']!r}")
    assert row["written_by"] != forged, "伪造的 X-Email 不得写入审计列"


# -------------------------------------------------------------
# 2. 两个操作者各批一张：各自的行记各自的写入者（排除"全局常量"假通过）
# -------------------------------------------------------------
def test_written_by_tracks_the_actual_operator():
    """写入者随已解析角色变化，不随伪造邮箱变化。

    owner 与 admin 都能入账（require_role('owner')）。两人的伪造邮箱不同，
    落库邮箱必须分别是 owner@demo.hk 与 admin@demo.hk。两行若相同，说明写死了常量。
    """
    _db = _fresh_db()
    client = _client()
    seen = {}
    for role, forged in (("owner", "boss.one@demo.hk"), ("admin", "boss.two@demo.hk")):
        vendor = f"審計鏈操作者商號{role}"
        _approve(_db, client, vendor, role=role, forged_email=forged)
        rows = _raw_vendor_rows(_db.DB_PATH, vendor)
        assert len(rows) == 1
        seen[role] = rows[0]["written_by"]
        assert forged not in (rows[0]["written_by"] or ""), (role, rows[0]["written_by"])

    assert seen == {"owner": "owner@demo.hk", "admin": "admin@demo.hk"}, (
        f"写入者必须随已解析角色变化（实得 {seen!r}）：若两行同值，说明落的是常量而非操作者")


# -------------------------------------------------------------
# 3. 向后兼容：旧调用方（不传 written_by）仍能写入且该列为空
# -------------------------------------------------------------
def test_legacy_ingest_memory_call_without_written_by_unchanged():
    _db = _fresh_db()
    vendor = "審計鏈兼容商號"
    # 旧调用形态：不带 written_by（离线脚本 / 既有测试直调的样子）
    rag.ingest_memory(vendor, "- 菜心 2斤 @10 = 20", notes="版式：手写单",
                      tenant_id="default", receipt_id=999001)
    rows = _raw_vendor_rows(_db.DB_PATH, vendor)
    assert len(rows) == 1, "不传 written_by 的旧调用仍必须能正常落库"
    assert not rows[0]["written_by"], (
        f"不传 written_by 时该列必须为空（实际 {rows[0]['written_by']!r}）："
        "缺省不得编造写入者（与既有遗留路径落库形态一致）")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

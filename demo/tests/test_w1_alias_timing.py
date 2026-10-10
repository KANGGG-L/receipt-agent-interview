# -*- coding: utf-8 -*-
"""工作项③（收尾波次 SPEC_FINAL §3）：新商首单别名登记时序（E2E 注记①）。

病灶：`approve_receipt` 中 `apply_receipt_to_inventory`（别名登记前置）先于供应商
自动建档执行 → 新商首单 W1 入账期锚点解析落空 → 别名登记恒 NO_SUPPLIER。
修法（候选 B 最小实现）：把既有"供应商自动建档"块上移到 apply 之前 —— 重排不新增
建档时机（人工闸后动作只先建），`find_supplier_by_name` 幂等守卫原样保留，
`inventory.apply_receipt_to_inventory` 本体（含记忆先验档）零改动。

覆盖（每条都可"回滚实现即失败"）：
- W1 正例：新 vendor 首单（status=parsed、明细含未匹配品名）→ POST approve →
  `vendor_sku_alias` 出现该商作用域别名行（supplier_id 非空、source_ref=receipt:<rid>），
  suppliers 表出现该商，且无 NO_SUPPLIER skip 留痕；
- W2 幂等：同单二次 approve → 状态机拦截（非 200），别名行数不变；
- W3 真无锚语义保持：supplier_name 为空的单 approve → 不建档、无别名行；
- W4 单元级语义不变（防重排误伤）：直接调 `apply_receipt_to_inventory`（无档案）
  仍 NO_SUPPLIER 不建档案 —— 重排只发生在 approve 链路，apply 本体零改动。

FastAPI TestClient + tmp DB/RAG（模块级 env，逐用例独立库），零模型调用：approve
端点的 `_extract_and_enqueue_memory_facts` 在开关缺省关闭时即返回。
"""

import importlib
import logging
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="w1_alias_timing_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_w1.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services.inventory import apply_receipt_to_inventory  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

TENANT = "w1-tenant"
OWNER = {"X-Role": "owner", "X-Tenant-Id": TENANT}
VENDOR = "旺記新材料行"
NEW_ITEM = "寫法甲筍乾"


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个用例一个干净的库（模块级 DB_PATH 是给 import 期用的兜底）。"""
    old = db.DB_PATH
    path = os.path.join(_TMP, f"w1_{uuid.uuid4().hex[:8]}.db")
    os.environ["DB_PATH"] = path
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    try:
        yield db
    finally:
        os.environ["DB_PATH"] = old
        db.DB_PATH = old
        db._make_engine()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def _item(name):
    """与 save_parsed_data 落库同形的明细行（未匹配：sku_id=None、matched=0）。"""
    return {
        "name": name, "raw_name": name, "quantity": 1, "unit": "斤",
        "unit_price": 10.0, "amount": 10.0, "sku_id": None,
        "cost_center_id": None, "confidence": 0.9, "matched": 0,
        "price_anomaly": 0, "price_anomaly_direction": "", "price_diff_percent": 0.0,
        "unit_conversion_warning": "", "fuzzy_candidates": [], "entity_candidates": [],
    }


def _mk_receipt(supplier_name=VENDOR, tenant_id=TENANT, status="parsed"):
    rid = db.create_receipt(supplier_name=supplier_name, status=status,
                            tenant_id=tenant_id)
    db.set_receipt_items(rid, [_item(NEW_ITEM)])
    # AC-05-04 收紧后的 approve 契约：缺开单日期/金额会被 422 APPROVAL_BLOCKED
    # 硬拦（金额须与明细算术一致）。本文件测的是别名登记时序，须先过该门。
    db.update_receipt(rid, receipt_date="2026-08-24", total_amount=10.0)
    return rid


def _approve(client, rid):
    row = db.get_receipt_row(rid, tenant_id=TENANT)
    return client.post(f"/api/receipt/{rid}/approve", headers=OWNER,
                       json={"version": row.version})


def _alias_rows(tenant_id=TENANT):
    s = db.get_session()
    try:
        q = s.query(db._VendorSkuAliasRow)
        if tenant_id is not None:
            q = q.filter(db._VendorSkuAliasRow.tenant_id == tenant_id)
        return q.all()
    finally:
        s.close()


def _supplier_count(tenant_id=TENANT):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM suppliers WHERE tenant_id=?", (tenant_id,)
        ).fetchone()[0]
    finally:
        conn.close()


# -------------------------------------------------------------
# W1：新商首单 → 建档先于入账 → 别名登记有锚点
# -------------------------------------------------------------
def test_w1_first_order_registers_alias_after_reorder(client, caplog):
    assert db.find_supplier_by_name(VENDOR, tenant_id=TENANT) is None, \
        "夹具前提：新商无档案"
    rid = _mk_receipt()

    with caplog.at_level(logging.INFO):
        resp = _approve(client, rid)

    assert resp.status_code == 200, resp.text
    # suppliers 表出现该商（建档块在新位置生效）
    sid_row = db.find_supplier_by_name(VENDOR, tenant_id=TENANT)
    assert sid_row is not None, "approve 链必须完成供应商自动建档"
    # 别名行：该商作用域、supplier_id 非空、source_ref 可回溯本单
    rows = _alias_rows()
    assert len(rows) == 1, rows
    assert int(rows[0].supplier_id) == int(sid_row.id)
    assert rows[0].source_ref == "receipt:%s" % rid
    assert rows[0].raw_name == NEW_ITEM
    # 无 NO_SUPPLIER skip 留痕（别名登记一次成功，无"供应商档案未锚定"跳过）
    assert not any("NO_SUPPLIER" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


# -------------------------------------------------------------
# W2：幂等 —— 同单二次 approve 被状态机拦截，别名行数不变
# -------------------------------------------------------------
def test_w2_second_approve_blocked_alias_unchanged(client):
    rid = _mk_receipt()
    assert _approve(client, rid).status_code == 200
    before = len(_alias_rows())

    resp = _approve(client, rid)

    assert resp.status_code != 200, "二次 approve 必须被状态机拦截"
    # 2026-10-10 契约对齐：对已入账单据的二次 approve，状态机返回更精确的
    # ALREADY_APPROVED（原口径 INVALID_STATUS）。
    assert resp.json().get("code") == "ALREADY_APPROVED"
    assert len(_alias_rows()) == before, "状态机拦截后别名表不得新增"


# -------------------------------------------------------------
# W3：真无锚语义保持 —— supplier_name 为空 → approve 契约门 422 硬拦，不建档
# -------------------------------------------------------------
def test_w3_no_vendor_name_no_archive_no_alias(client):
    """2026-10-10 契约对齐：AC-05-04 收紧后，缺供应商名称在 approve 门就被
    422 APPROVAL_BLOCKED 硬拦（原口径为走完链路、靠 apply 层 NO_SUPPLIER 不建档）。
    「无 vendor 名不建档」的语义不变，只是拦截点前移到契约门。"""
    rid = _mk_receipt(supplier_name="")
    before = _supplier_count()

    resp = _approve(client, rid)

    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "APPROVAL_BLOCKED"
    assert _supplier_count() == before, "无 vendor 名不得凭空建档"
    assert _alias_rows() == [], "无 vendor 名不得登记别名"


# -------------------------------------------------------------
# W4：单元级语义不变 —— 直接调 apply（无档案）仍 NO_SUPPLIER 不建档
# -------------------------------------------------------------
def test_w4_apply_direct_without_archive_still_no_supplier(caplog):
    """重排只发生在 approve 链路；apply 本体（含记忆先验档）语义零变化。"""
    rid = _mk_receipt()
    row = db.get_receipt_row(rid, tenant_id=TENANT)
    assert row.supplier_id in (None, "") and not row.supplier_id

    with caplog.at_level(logging.INFO):
        apply_receipt_to_inventory(row, tenant_id=TENANT)

    assert db.find_supplier_by_name(VENDOR, tenant_id=TENANT) is None, \
        "apply 本体绝不建档"
    assert _alias_rows() == [], "无锚点时不登记别名（NO_SUPPLIER 语义保持）"
    assert any("NO_SUPPLIER" in r.getMessage() or "供应商档案未锚定" in r.getMessage()
               for r in caplog.records), \
        "NO_SUPPLIER 跳过留痕必须保留（可观测性不回退）"

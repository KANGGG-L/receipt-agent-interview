# -*- coding: utf-8 -*-
"""I02 capability 矩阵抽样：三身份 x 六域的真实请求断言（I02 规格 v1 §3）。

口径：AUTH_ENABLED=1 + IDENTITY_V2_ENABLED=1（本文件专测 v2 语义；开关关分支
由 test_i01_identity_migration.py 的 AC5 与 P01 批次回归覆盖）。
红线：全程沙箱 DB；供应商别名 Vendor_A/Vendor_B；无真实凭据。
"""

import os
import sys
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_i02_capability_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_i02.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ["IDENTITY_V2_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "i02-test-secret-not-the-default-value")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db, identity_store  # noqa: E402
from app.main import app  # noqa: E402

db._make_engine()

# 受控测试身份：built-in 三账号 + 无 membership 的第四账号
auth.ACCOUNTS["nomember@example.test"] = ("i02-task-only", "staff", "default")
identity_store.grant_tenant_membership("staff@demo.hk", "default", "staff")
identity_store.grant_tenant_membership("boss@demo.hk", "default", "owner")
identity_store.set_platform_role("admin@demo.hk")

# 种子数据（default 租户可列表；tenant_b 单据用于跨租户负向）
db.create_sku("Item_A", base_unit="kg", tenant_id="default")
_sup_id, _ = db.create_supplier("Vendor_A", tenant_id="default")
_rid_b = db.create_receipt(supplier_name="Vendor_B", status="parsed", tenant_id="tenant_b")

client = TestClient(app)


def _hdr(email, role, tenant="default"):
    return {"Authorization": "Bearer " + auth.issue_token(email, role, tenant)}


STAFF = _hdr("staff@demo.hk", "staff")
OWNER = _hdr("boss@demo.hk", "owner")
ADMIN = _hdr("admin@demo.hk", "admin")
NOMEMBER = _hdr("nomember@example.test", "staff")


def _get(path, hdr):
    return client.get(path, headers=hdr)


# -------------------------------------------------------------
# A. 无 membership：一切商户业务端点 403（含 staff 角色令牌）
# -------------------------------------------------------------
def test_a_no_membership_all_business_denied():
    for path in ("/api/receipts", "/api/inventory", "/api/suppliers",
                 "/api/dishes", "/api/payables", "/api/cost_report"):
        r = _get(path, NOMEMBER)
        assert r.status_code == 403, (path, r.status_code)
    r = client.post("/api/receipt/999999/approve", headers=NOMEMBER, json={"version": 1})
    assert r.status_code == 403, r.status_code


# -------------------------------------------------------------
# B. staff membership：staff 域通过、owner/admin 域 403
# -------------------------------------------------------------
def test_b_staff_domain_pass():
    for path in ("/api/receipts", "/api/inventory", "/api/inventory/logs",
                 "/api/suppliers", "/api/dishes", "/api/export/reports"):
        r = _get(path, STAFF)
        assert r.status_code == 200, (path, r.status_code)


def test_b_staff_owner_domain_denied():
    for path in ("/api/payables", "/api/cost_report", "/api/admin/funnel"):
        r = _get(path, STAFF)
        assert r.status_code == 403, (path, r.status_code)
    r = client.post("/api/receipt/999999/approve", headers=STAFF, json={"version": 1})
    assert r.status_code == 403, r.status_code
    r = client.delete("/api/suppliers/999999", headers=STAFF)
    assert r.status_code == 403, r.status_code


def test_b_staff_platform_domain_denied():
    for path in ("/api/admin/engine-config", "/api/memory/pending",
                 "/api/evalset/samples"):
        r = _get(path, STAFF)
        assert r.status_code == 403, (path, r.status_code)


# -------------------------------------------------------------
# C. owner membership：owner 域通过（RBAC 通过后到对象层 404）、平台域 403
# -------------------------------------------------------------
def test_c_owner_domain_pass():
    for path in ("/api/payables", "/api/cost_report", "/api/export/reports", "/api/dishes"):
        r = _get(path, OWNER)
        assert r.status_code == 200, (path, r.status_code)
    r = client.post("/api/receipt/999999/approve", headers=OWNER, json={"version": 1})
    assert r.status_code == 404, r.status_code  # RBAC 通过，单不存在
    r = client.delete("/api/suppliers/999999", headers=OWNER)
    assert r.status_code == 404, r.status_code  # RBAC 通过，供应商不存在


def test_c_owner_platform_domain_denied():
    for path in ("/api/admin/engine-config", "/api/memory/pending", "/api/evalset/samples"):
        r = _get(path, OWNER)
        assert r.status_code == 403, (path, r.status_code)


def test_c_owner_cross_tenant_isolated():
    # AUTH_ENABLED=1：租户只来自令牌（default）；tenant_b 单据不可见 → 404
    r = _get("/api/receipt/%d" % _rid_b, OWNER)
    assert r.status_code == 404, r.status_code


# -------------------------------------------------------------
# D. 平台 admin：平台域通过、商户业务域全部 403（D-02 核心翻转）
# -------------------------------------------------------------
def test_d_admin_platform_domain_pass():
    for path in ("/api/admin/engine-config", "/api/memory/pending"):
        r = _get(path, ADMIN)
        assert r.status_code == 200, (path, r.status_code)
    # evalset：RBAC 通过（403 才是失败信号）；404 为对象层语义（沙箱无数据集配置）
    r = _get("/api/evalset/samples", ADMIN)
    assert r.status_code in (200, 404), r.status_code
    assert r.status_code != 403


def test_d_admin_business_domain_denied():
    for path in ("/api/receipts", "/api/inventory", "/api/payables", "/api/cost_report"):
        r = _get(path, ADMIN)
        assert r.status_code == 403, (path, r.status_code)
    r = client.post("/api/receipt/999999/approve", headers=ADMIN, json={"version": 1})
    assert r.status_code == 403, r.status_code


# -------------------------------------------------------------
# E. 字段矩阵（服务端强制，非 UI 遮蔽）：staff 库存无经营价格字段
# -------------------------------------------------------------
def test_e_staff_inventory_financial_fields_absent():
    r = _get("/api/inventory", STAFF)
    assert r.status_code == 200, r.status_code
    rows = r.json()["data"]
    assert rows, "seeded sku must be listed"
    assert all(x["last_unit_price"] is None and x["vs_avg_pct"] is None for x in rows)


# -------------------------------------------------------------
# F. capability 映射单测（词汇冻结，S01/U03 下游引用）
# -------------------------------------------------------------
def test_f_capability_mapping():
    staff_caps = identity_store.capabilities_for("staff@demo.hk", "default")
    assert {"own_receipt_read", "own_receipt_edit", "inventory_read"} <= staff_caps
    assert "receipt_approve" not in staff_caps and "finance_read" not in staff_caps
    owner_caps = identity_store.capabilities_for("boss@demo.hk", "default")
    assert {"receipt_approve", "finance_read", "finance_operate",
            "supplier_credit_allocate", "memory_governance"} <= owner_caps
    admin_caps = identity_store.capabilities_for("admin@demo.hk", "default")
    assert admin_caps == set(identity_store.CAPABILITIES_PLATFORM)
    assert not (admin_caps & identity_store.CAPABILITIES_TENANT_OWNER)
    # 错租户查 capability：无该租户 membership → 空
    assert identity_store.capabilities_for("boss@demo.hk", "tenant_other") == set()

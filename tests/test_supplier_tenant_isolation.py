# -*- coding: utf-8 -*-
"""安全加固第二批 D16：payments / departments 租户隔离 + supplier_stats 归属校验。

覆盖：
1. 租户 A 的 owner 看不到 B 的付款（API + 数据访问层直调）；
2. 租户 A 的 staff 看不到 B 的部门（API + 数据访问层直调）；
3. 「不得停用最后一个启用部门」按租户独立判定；
4. list_payments / list_departments 不传租户时不过滤（向后兼容）；
5. supplier_stats 对跨租户 supplier_id 返回空统计（不拿别人的行）；
6. create_payment 回写收据付款状态限定同租户（跨租户单据不得被改写）。

数据一律假数据（供应商甲 / 乙部门，租户 tenantA / tenantB）。
"""

import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="supplier_tenant_isolation_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_supplier_tenant.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import importlib  # noqa: E402

import app.db as db  # noqa: E402

TENANT_A = "tenantA"
TENANT_B = "tenantB"

EMPTY_STATS = {"receipt_count": 0, "unpaid_credit_total": 0.0, "unpaid_credit_count": 0}


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"st_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


# -------------------------------------------------------------
# 1. 付款：租户隔离（数据访问层 + API）
# -------------------------------------------------------------
def test_list_payments_filters_by_tenant_direct():
    _db = _fresh_db()
    pid_a = _db.create_payment(supplier_id=1, amount=10.0, method="cash",
                               paid_at="2026-01-01", tenant_id=TENANT_A)
    pid_b = _db.create_payment(supplier_id=2, amount=20.0, method="cash",
                               paid_at="2026-01-02", tenant_id=TENANT_B)

    ids_a = {p["id"] for p in _db.list_payments(tenant_id=TENANT_A)}
    ids_b = {p["id"] for p in _db.list_payments(tenant_id=TENANT_B)}
    assert ids_a == {pid_a}, "tenantA 只应看到自己的付款"
    assert ids_b == {pid_b}, "tenantB 只应看到自己的付款"
    # 不传租户 = 不过滤（内部脚本/导出路径向后兼容）
    assert {pid_a, pid_b} <= {p["id"] for p in _db.list_payments()}


def test_owner_cannot_see_other_tenant_payments_via_api():
    _db = _fresh_db()
    pid_a = _db.create_payment(supplier_id=1, amount=10.0, method="cash",
                               paid_at="2026-01-01", tenant_id=TENANT_A)
    pid_b = _db.create_payment(supplier_id=2, amount=20.0, method="cash",
                               paid_at="2026-01-02", tenant_id=TENANT_B)

    client = _client()
    ha = {"X-Role": "owner", "X-Tenant-Id": TENANT_A}
    hb = {"X-Role": "owner", "X-Tenant-Id": TENANT_B}

    ids_a = {p["id"] for p in client.get("/api/payments", headers=ha).json()["data"]}
    ids_b = {p["id"] for p in client.get("/api/payments", headers=hb).json()["data"]}
    assert ids_a == {pid_a}, "tenantA 的 owner 列表不得含 tenantB 的付款"
    assert ids_b == {pid_b}

    # default 租户（无 header）看不到两个非 default 付款
    ids_default = {p["id"] for p in
                   client.get("/api/payments", headers={"X-Role": "owner"}).json()["data"]}
    assert pid_a not in ids_default and pid_b not in ids_default


def test_payments_supplier_filter_is_id_based_and_tenant_safe():
    """按 supplier_id 精确关联过滤（原按供应商名匹配属跨租户回退口径，已删除）。"""
    _db = _fresh_db()
    sup_a, err = _db.create_supplier("供应商甲", tenant_id=TENANT_A)
    assert err is None
    sup_b, err = _db.create_supplier("供应商乙", tenant_id=TENANT_B)
    assert err is None
    _db.create_payment(supplier_id=sup_a, amount=10.0, method="cash",
                       paid_at="2026-01-01", tenant_id=TENANT_A)
    _db.create_payment(supplier_id=sup_b, amount=20.0, method="cash",
                       paid_at="2026-01-02", tenant_id=TENANT_B)

    client = _client()
    ha = {"X-Role": "owner", "X-Tenant-Id": TENANT_A}
    one = client.get(f"/api/payments?supplier_id={sup_a}", headers=ha).json()["data"]
    assert len(one) == 1 and one[0]["supplier_id"] == sup_a
    # 跨租户 supplier_id：空列表（不退回全量）
    none = client.get(f"/api/payments?supplier_id={sup_b}", headers=ha).json()["data"]
    assert none == [], "跨租户 supplier_id 必须返回空列表"


# -------------------------------------------------------------
# 2. 部门：租户隔离（数据访问层 + API）
# -------------------------------------------------------------
def test_list_departments_filters_by_tenant_direct():
    _db = _fresh_db()
    dept_a = _db.create_department("甲部门", tenant_id=TENANT_A)
    dept_b = _db.create_department("乙部门", tenant_id=TENANT_B)

    ids_a = {d.id for d in _db.list_departments(tenant_id=TENANT_A)}
    ids_b = {d.id for d in _db.list_departments(tenant_id=TENANT_B)}
    assert ids_a == {dept_a}
    assert ids_b == {dept_b}
    # 不传租户 = 不过滤（向后兼容）
    assert {dept_a, dept_b} <= {d.id for d in _db.list_departments()}


def test_update_department_cross_tenant_treated_as_missing():
    _db = _fresh_db()
    dept_a = _db.create_department("甲部门", tenant_id=TENANT_A)

    # 跨租户更新：视为不存在（返回 None），且不得改写它租部门
    assert _db.update_department(dept_a, tenant_id=TENANT_B, name="篡改") is None
    row = [d for d in _db.list_departments(tenant_id=TENANT_A) if d.id == dept_a][0]
    assert row.name == "甲部门", "跨租户更新不得改写他租部门"

    # 同租户更新生效
    assert _db.update_department(dept_a, tenant_id=TENANT_A, name="甲部门改") is not None
    row2 = [d for d in _db.list_departments(tenant_id=TENANT_A) if d.id == dept_a][0]
    assert row2.name == "甲部门改"


def test_staff_cannot_see_other_tenant_departments_via_api():
    _db = _fresh_db()
    client = _client()
    hb_owner = {"X-Role": "owner", "X-Tenant-Id": TENANT_B}
    dept_b = client.post("/api/departments", json={"name": "乙部门"},
                         headers=hb_owner).json()["id"]

    ha_staff = {"X-Role": "staff", "X-Tenant-Id": TENANT_A}
    ids_a = {d["id"] for d in
             client.get("/api/departments", headers=ha_staff).json()["data"]}
    assert dept_b not in ids_a, "tenantA 的 staff 不得看到 tenantB 的部门"

    hb_staff = {"X-Role": "staff", "X-Tenant-Id": TENANT_B}
    ids_b = {d["id"] for d in
             client.get("/api/departments", headers=hb_staff).json()["data"]}
    assert dept_b in ids_b


# -------------------------------------------------------------
# 3. 「不得停用最后一个启用部门」按租户独立判定
# -------------------------------------------------------------
def test_last_active_department_guard_is_per_tenant():
    _db = _fresh_db()
    client = _client()
    ha = {"X-Role": "owner", "X-Tenant-Id": TENANT_A}
    hb = {"X-Role": "owner", "X-Tenant-Id": TENANT_B}

    # A：1 个部门；B：2 个部门
    dept_a = client.post("/api/departments", json={"name": "甲部门"},
                         headers=ha).json()["id"]
    dept_b1 = client.post("/api/departments", json={"name": "乙部门一"},
                          headers=hb).json()["id"]
    dept_b2 = client.post("/api/departments", json={"name": "乙部门二"},
                          headers=hb).json()["id"]

    # A 停用唯一部门 -> 拒绝（B 的 2 个启动部门不得计入 A 的计数）
    resp_a = client.delete(f"/api/departments/{dept_a}", headers=ha)
    assert resp_a.status_code == 200
    assert resp_a.json()["status"] == "error"
    assert "最后一个" in resp_a.json()["msg"]

    # B 停用其一 -> 成功（B 有 2 个启用部门）
    resp_b = client.delete(f"/api/departments/{dept_b1}", headers=hb)
    assert resp_b.json()["status"] == "success", resp_b.text

    # 校验台账：A 的部门仍启用；B 的 dept_b1 已停用、dept_b2 仍启用
    a_rows = {d["id"]: d["active"] for d in
              client.get("/api/departments", headers=ha).json()["data"]}
    assert a_rows[dept_a] == 1
    b_rows = {d["id"]: d["active"] for d in
              client.get("/api/departments", headers=hb).json()["data"]}
    assert b_rows[dept_b1] == 0 and b_rows[dept_b2] == 1

    # B 停用最后一个 -> 拒绝
    resp_b2 = client.delete(f"/api/departments/{dept_b2}", headers=hb)
    assert resp_b2.json()["status"] == "error"
    assert "最后一个" in resp_b2.json()["msg"]


# -------------------------------------------------------------
# 4. supplier_stats 跨租户归属校验（D16）
# -------------------------------------------------------------
def test_supplier_stats_cross_tenant_returns_empty():
    _db = _fresh_db()
    sup_a, err = _db.create_supplier("供应商甲", tenant_id=TENANT_A)
    assert err is None
    rid = _db.create_receipt_record(supplier_name="供应商甲", status="approved",
                                    settlement_type="credit", total_amount=100.0,
                                    tenant_id=TENANT_A)
    assert rid

    # 同租户：正常统计
    st_a = _db.supplier_stats(sup_a, tenant_id=TENANT_A)
    assert st_a["receipt_count"] == 1
    assert st_a["unpaid_credit_total"] == 100.0
    assert st_a["unpaid_credit_count"] == 1

    # 跨租户：空统计（拿不到别人的行）
    assert _db.supplier_stats(sup_a, tenant_id=TENANT_B) == EMPTY_STATS

    # 不存在的 supplier_id：同样空统计
    assert _db.supplier_stats(999999, tenant_id=TENANT_A) == EMPTY_STATS


# -------------------------------------------------------------
# 5. create_payment 回写收据付款状态限定同租户
# -------------------------------------------------------------
def test_create_payment_writeback_only_same_tenant():
    _db = _fresh_db()
    rid_b = _db.create_receipt(supplier_name="供应商乙", status="approved",
                               tenant_id=TENANT_B)
    # A 租户付款、linked 指向 B 租户单据：不得改写 B 租户单据
    _db.create_payment(supplier_id=1, amount=5.0, method="cash",
                       paid_at="2026-01-01", linked_receipt_ids=[rid_b],
                       tenant_id=TENANT_A)
    row_b = _db.get_receipt_row(rid_b)
    assert (row_b.paid_at or "") == "", "跨租户 linked 回写不得改写他租单据"

    # 同租户回写正常
    rid_a = _db.create_receipt(supplier_name="供应商甲", status="approved",
                               tenant_id=TENANT_A)
    _db.create_payment(supplier_id=1, amount=5.0, method="cash",
                       paid_at="2026-01-02", linked_receipt_ids=[rid_a],
                       tenant_id=TENANT_A)
    row_a = _db.get_receipt_row(rid_a)
    assert row_a.paid_at == "2026-01-02"
    assert row_a.paid_method == "cash"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

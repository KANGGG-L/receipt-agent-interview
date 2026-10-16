# -*- coding: utf-8 -*-
"""UAT-028: 停用供应商名称保留与部分更新字段独立性专项测试。

验收标准:
1. 供应商 Vendor_A 创建后改名为 Vendor_B，再 PATCH active=0 停用，
   随后以 includeInactive=1 查询时，name 仍为 Vendor_B，历史引用（收据、采购、外键关联）保持指向原记录且不变；
2. PATCH notes 只改 notes，不得影响 name 与 phone；
   对 name/phone 的「字段省略」与「显式传空/显式清空」两种情形必须分别处理并各自验证，
   不得因为部分 patch 语义把显式清空放宽为忽略，也不得让省略字段被写成空值；
3. 空 name 创建、显式无效更新在部分 patch 路径下仍必须被拒绝（不得变宽松）；
4. 跨 tenant 读取/写入应拒绝或不可见，对 B tenant baseline 前后无意外改动。
"""

import os
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from _b2_helpers import hdr, new_tenant, make_receipt, item


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_uat028_vendor_rename_deactivate_and_retain_name_and_references(client):
    """AC 1: Vendor_A 创建 -> 改名 Vendor_B -> 停用 active=0 -> include_inactive 查询 name 仍为 Vendor_B 且单据关联保持。"""
    tn = new_tenant()

    # 1. 创建供应商 Vendor_A
    r = client.post("/api/suppliers", headers=hdr(tn, "owner"), json={
        "name": "Vendor_A_Lifecycle",
        "contact_phone": "12345678",
        "notes": "Synthetic notes",
        "settlement_pref": "cash"
    })
    assert r.status_code == 200 and r.json().get("status") == "success", r.text
    sid = r.json()["id"]

    # 2. 建立关联单据，确认 supplier_id 指向 sid
    rid, _ = make_receipt(
        client, tn,
        items=[item("生菜", 10, "斤", 2.5)],
        total=25.0,
        supplier="Vendor_A_Lifecycle",
        supplier_id=sid
    )
    assert rid is not None

    # 3. 改名为 Vendor_B
    r_rename = client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={
        "name": "Vendor_B_Lifecycle"
    })
    assert r_rename.status_code == 200 and r_rename.json().get("status") == "success", r_rename.text

    # 4. PATCH active=0 停用
    r_deact = client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={
        "active": 0
    })
    assert r_deact.status_code == 200 and r_deact.json().get("status") == "success", r_deact.text

    # 5. 默认查询不带 include_inactive: 停用供应商不在列表中
    r_active = client.get("/api/suppliers", headers=hdr(tn, "owner"))
    assert r_active.status_code == 200
    active_ids = [x["id"] for x in r_active.json().get("data", [])]
    assert sid not in active_ids, "Deactivated supplier must not appear in active list"

    # 6. 带 include_inactive=1 查询: name 保持 Vendor_B_Lifecycle，且 receipt_count 保持为 1
    r_inc = client.get("/api/suppliers?include_inactive=1", headers=hdr(tn, "owner"))
    assert r_inc.status_code == 200
    rows = [x for x in r_inc.json().get("data", []) if x["id"] == sid]
    assert len(rows) == 1, "Deactivated supplier must be found when include_inactive=1"
    target = rows[0]
    assert target["name"] == "Vendor_B_Lifecycle", f"Expected Vendor_B_Lifecycle, got: {target['name']}"
    assert target["active"] == 0
    assert target["contact_phone"] == "12345678"
    assert target["receipt_count"] == 1, f"Expected 1 receipt reference, got: {target['receipt_count']}"

    # 7. 按新名称搜索停用供应商 (q=Vendor_B & include_inactive=1)
    r_search = client.get("/api/suppliers?q=Vendor_B&include_inactive=1", headers=hdr(tn, "owner"))
    assert r_search.status_code == 200
    search_ids = [x["id"] for x in r_search.json().get("data", [])]
    assert sid in search_ids, "Must be searchable by updated name Vendor_B"

    # 8. 底层数据库直接核对
    sup_row = db.get_supplier(sid, tenant_id=tn)
    assert sup_row is not None
    assert sup_row.name == "Vendor_B_Lifecycle"
    assert sup_row.active == 0
    rc = db.count_receipts_for_supplier(sid, tenant_id=tn)
    assert rc == 1


def test_uat028_patch_notes_does_not_affect_name_or_phone_omitted_vs_explicit_clear(client):
    """AC 2: PATCH notes 不影响 name 与 phone；省略字段保留原值，显式清空有效生效。"""
    tn = new_tenant()

    # 初始化供应商: name, phone, notes 均有值
    r = client.post("/api/suppliers", headers=hdr(tn, "owner"), json={
        "name": "Vendor_Field_Test",
        "contact_phone": "22334455",
        "notes": "Initial Notes",
        "settlement_pref": "cash"
    })
    assert r.status_code == 200 and r.json().get("status") == "success"
    sid = r.json()["id"]

    # 1. 字段省略: 仅 PATCH notes，name 与 contact_phone 省略
    r_notes = client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={
        "notes": "Updated Notes Only"
    })
    assert r_notes.status_code == 200 and r_notes.json().get("status") == "success"

    r_chk1 = client.get("/api/suppliers?include_inactive=1", headers=hdr(tn, "owner"))
    target1 = next(x for x in r_chk1.json()["data"] if x["id"] == sid)
    assert target1["name"] == "Vendor_Field_Test", "Omitted name must NOT be changed"
    assert target1["contact_phone"] == "22334455", "Omitted contact_phone must NOT be changed"
    assert target1["notes"] == "Updated Notes Only"

    # 2. 显式清空 phone: 传 contact_phone=""，name 与 notes 省略
    r_clr_phone = client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={
        "contact_phone": ""
    })
    assert r_clr_phone.status_code == 200 and r_clr_phone.json().get("status") == "success"

    r_chk2 = client.get("/api/suppliers?include_inactive=1", headers=hdr(tn, "owner"))
    target2 = next(x for x in r_chk2.json()["data"] if x["id"] == sid)
    assert target2["name"] == "Vendor_Field_Test", "Omitted name must NOT be changed"
    assert target2["contact_phone"] == "", "Explicitly cleared contact_phone must be empty string"
    assert target2["notes"] == "Updated Notes Only", "Omitted notes must NOT be changed"

    # 3. 显式清空 notes: 传 notes=""，name 省略
    r_clr_notes = client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={
        "notes": ""
    })
    assert r_clr_notes.status_code == 200 and r_clr_notes.json().get("status") == "success"

    r_chk3 = client.get("/api/suppliers?include_inactive=1", headers=hdr(tn, "owner"))
    target3 = next(x for x in r_chk3.json()["data"] if x["id"] == sid)
    assert target3["name"] == "Vendor_Field_Test"
    assert target3["contact_phone"] == ""
    assert target3["notes"] == "", "Explicitly cleared notes must be empty string"

    # 4. 显式通过 null 清空: 先设置 phone，再以 null 清空
    client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={"contact_phone": "66778899"})
    r_chk4 = client.get("/api/suppliers?include_inactive=1", headers=hdr(tn, "owner"))
    assert next(x for x in r_chk4.json()["data"] if x["id"] == sid)["contact_phone"] == "66778899"

    r_null_phone = client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={"contact_phone": None})
    assert r_null_phone.status_code == 200 and r_null_phone.json().get("status") == "success"

    r_chk5 = client.get("/api/suppliers?include_inactive=1", headers=hdr(tn, "owner"))
    target5 = next(x for x in r_chk5.json()["data"] if x["id"] == sid)
    assert target5["contact_phone"] == "", "Explicit null contact_phone must clear to empty string, not ignored"
    assert target5["name"] == "Vendor_Field_Test"


def test_uat028_empty_name_create_and_invalid_patch_rejected(client):
    """AC 3: 空 name 创建拒绝，显式空/无效 name 的 PATCH 必须拒绝且保留原名称。"""
    tn = new_tenant()

    # 1. 创建时空 name 拒绝
    r_empty_create = client.post("/api/suppliers", headers=hdr(tn, "owner"), json={"name": ""})
    assert r_empty_create.status_code in (400, 422) or r_empty_create.json().get("status") == "error"

    r_blank_create = client.post("/api/suppliers", headers=hdr(tn, "owner"), json={"name": "   "})
    assert r_blank_create.status_code in (400, 422) or r_blank_create.json().get("status") == "error"

    # 2. 创建有效供应商
    r_ok = client.post("/api/suppliers", headers=hdr(tn, "owner"), json={
        "name": "Vendor_Immutable_Name",
        "contact_phone": "99887766"
    })
    assert r_ok.status_code == 200 and r_ok.json().get("status") == "success"
    sid = r_ok.json()["id"]

    # 3. PATCH 时显式空串 "" 必须被拒绝
    r_empty_patch = client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={"name": ""})
    assert r_empty_patch.status_code in (400, 422) or r_empty_patch.json().get("status") == "error", \
        f"Empty name patch must be rejected, got: {r_empty_patch.status_code} {r_empty_patch.text}"

    # 核对原名未被清空
    sup = db.get_supplier(sid, tenant_id=tn)
    assert sup.name == "Vendor_Immutable_Name", f"Name was corrupted to: {repr(sup.name)}"

    # 4. PATCH 时显式纯空格 "   " 必须被拒绝
    r_blank_patch = client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={"name": "   "})
    assert r_blank_patch.status_code in (400, 422) or r_blank_patch.json().get("status") == "error", \
        f"Blank name patch must be rejected, got: {r_blank_patch.status_code} {r_blank_patch.text}"

    sup = db.get_supplier(sid, tenant_id=tn)
    assert sup.name == "Vendor_Immutable_Name"

    # 5. PATCH 时显式 null 必须被拒绝（不得把显式传空放宽为忽略或写成空）
    r_null_patch = client.patch(f"/api/suppliers/{sid}", headers=hdr(tn, "owner"), json={"name": None})
    assert r_null_patch.status_code in (400, 422) or r_null_patch.json().get("status") == "error", \
        f"Null name patch must be rejected, got: {r_null_patch.status_code} {r_null_patch.text}"

    sup = db.get_supplier(sid, tenant_id=tn)
    assert sup.name == "Vendor_Immutable_Name"


def test_uat028_tenant_isolation_and_b_baseline_unpolluted(client):
    """AC 4: 跨 tenant 读写拒绝与隔离，B 租户 baseline 无污染。"""
    tn_a = new_tenant()
    tn_b = new_tenant()

    # 租户 A 创建供应商
    ra = client.post("/api/suppliers", headers=hdr(tn_a, "owner"), json={"name": "Vendor_Only_In_A"})
    assert ra.status_code == 200 and ra.json().get("status") == "success"
    sid_a = ra.json()["id"]

    # 租户 B 创建供应商
    rb = client.post("/api/suppliers", headers=hdr(tn_b, "owner"), json={"name": "Vendor_Only_In_B"})
    assert rb.status_code == 200 and rb.json().get("status") == "success"
    sid_b = rb.json()["id"]

    # 租户 B 查询列表不可见 A 的供应商
    rb_list = client.get("/api/suppliers?include_inactive=1", headers=hdr(tn_b, "owner"))
    assert rb_list.status_code == 200
    b_ids = [x["id"] for x in rb_list.json().get("data", [])]
    assert sid_a not in b_ids, "Tenant A supplier must not be visible to Tenant B"
    assert sid_b in b_ids

    # 租户 B 试图修改租户 A 的供应商 -> 必须被拒绝 (404 或 error)
    r_hack = client.patch(f"/api/suppliers/{sid_a}", headers=hdr(tn_b, "owner"), json={
        "name": "Vendor_Hacked_By_B",
        "active": 0
    })
    assert r_hack.status_code in (403, 404) or r_hack.json().get("status") == "error", \
        f"Cross-tenant patch must be rejected, got: {r_hack.status_code} {r_hack.text}"

    # 租户 A 数据不受影响
    sup_a = db.get_supplier(sid_a, tenant_id=tn_a)
    assert sup_a is not None
    assert sup_a.name == "Vendor_Only_In_A"
    assert sup_a.active == 1

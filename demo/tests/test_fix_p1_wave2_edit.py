# -*- coding: utf-8 -*-
"""P1 Wave 2 记忆编辑与 Supersede 状态机测试套件 (T5 G2 D2)。

约束与红线：
1. 全局严禁使用 Emoji。统一采用 [OK], [FAIL], [INFO], [WARN]。
2. 严禁改动 live 数据库与 live 向量库，环境完全基于 /tmp 隔离沙箱。
3. 严格断言返回口径，杜绝 500 异常与越权泄露。
"""

import os
import sys
import tempfile
import uuid
import json
import pytest
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix="test_p1_w2_edit_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_p1_w2.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "0"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db
import app.services.rag as rag
import app.services.memory_store as memory_store
from fastapi.testclient import TestClient
from app.main import app as fastapi_app

rag.PERSIST_DIR = os.environ["RAG_DIR"]
client = TestClient(fastapi_app)

ADMIN_HEADERS = {"X-Role": "admin", "X-Tenant-Id": "default"}
STAFF_HEADERS = {"X-Role": "staff", "X-Tenant-Id": "default"}
OWNER_HEADERS = {"X-Role": "owner", "X-Tenant-Id": "default"}


def test_t5_patch_memory_supersede_success():
    """T5 (G2, D2已决): 记忆编辑成功触发 supersede，旧行失效指向新行，新行生效且初始分重置。"""
    vendor = "Vendor_Edit_Success"
    tenant = "default"

    # 1. 建立初始记忆
    old_mid, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="初始备注", sample="样本", tenant_id=tenant,
        fact_key="alias:原始品名", fact_row={"fact_value": "原始值", "fact_kind": "alias"}
    )

    # 2. 调用 PATCH /api/memory/{memory_id}
    res = client.patch(
        f"/api/memory/{old_mid}",
        headers=ADMIN_HEADERS,
        json={
            "fact_key": "alias:更新品名",
            "fact_value": "更新值",
            "notes": "更新备注说明"
        }
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    res_data = data["data"]
    assert res_data["action"] == "superseded"
    assert res_data["old_memory_id"] == old_mid
    new_mid = res_data["new_memory_id"]
    assert new_mid and new_mid != old_mid
    assert res_data["vendor"] == vendor
    assert res_data["status"] == "active"

    # 3. 校验旧行状态 (superseded + superseded_by = new_mid)
    old_row = db.get_vendor_memory_by_id(old_mid, tenant_id=tenant)
    assert old_row is not None
    assert old_row["status"] == "superseded"
    assert old_row["superseded_by"] == new_mid

    # 4. 校验新行状态 (active + decay_score=0.7 + version=1 + superseded_by=None)
    new_row = db.get_vendor_memory_by_id(new_mid, tenant_id=tenant)
    assert new_row is not None
    assert new_row["status"] == "active"
    assert new_row["superseded_by"] == ""
    assert new_row["fact_key"] == "alias:更新品名"
    assert new_row["fact_value"] == "更新值"
    assert new_row["notes"] == "更新备注说明"
    assert new_row["vendor"] == vendor
    assert new_row["tenant_id"] == tenant
    assert round(new_row["decay_score"], 2) == 0.70
    assert new_row["version"] == 1


def test_t5_patch_memory_cross_tenant_isolation_returns_404():
    """T5 跨租户隔离：编辑属于其他租户的记忆统一返回 404 (消除存在性 Oracle 探测)。"""
    vendor = "Vendor_Tenant_A"
    tenant_a = "tenant_alpha"
    tenant_b = "tenant_beta"

    # 在 tenant_alpha 下创建记忆
    mid_a, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="租户A记忆", sample="", tenant_id=tenant_a,
        fact_key="alias:商品A", fact_row={"fact_value": "标准A", "fact_kind": "alias"}
    )

    # 用 tenant_beta 身份发起编辑
    res = client.patch(
        f"/api/memory/{mid_a}",
        headers={"X-Role": "admin", "X-Tenant-Id": tenant_b},
        json={"fact_key": "alias:恶意窜改", "fact_value": "窜改值"}
    )
    assert res.status_code == 404
    data = res.json()
    assert data["code"] == "MEMORY_NOT_FOUND"

    # 验证 tenant_alpha 下的原始数据分毫未动
    row_a = db.get_vendor_memory_by_id(mid_a, tenant_id=tenant_a)
    assert row_a is not None
    assert row_a["status"] == "active"
    assert row_a["fact_key"] == "alias:商品A"


def test_t5_patch_memory_non_active_conflict_returns_409():
    """T5 状态机校验：仅 active 记忆允许编辑，已归档或已 superseded 记忆返回 409。"""
    vendor = "Vendor_Status_Conflict"
    tenant = "default"

    # 1. 测试归档记忆不可编辑
    mid_archived, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="归档记忆", sample="", tenant_id=tenant,
        fact_key="alias:归档品", fact_row={"fact_value": "旧值", "fact_kind": "alias"}
    )
    # 强制将状态置为 archived
    s = db.get_session()
    try:
        r = s.query(db._VendorMemoryRow).filter_by(memory_id=mid_archived).first()
        r.status = "archived"
        s.commit()
    finally:
        s.close()

    res_archived = client.patch(
        f"/api/memory/{mid_archived}",
        headers=ADMIN_HEADERS,
        json={"fact_value": "试图修改归档"}
    )
    assert res_archived.status_code == 409
    assert res_archived.json()["code"] == "MEMORY_NOT_ACTIVE"

    # 2. 测试已 superseded 记忆不可重复编辑
    mid_active, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="可编辑记忆", sample="", tenant_id=tenant,
        fact_key="alias:待替换", fact_row={"fact_value": "旧值", "fact_kind": "alias"}
    )
    # 第一次编辑成功
    res_first = client.patch(
        f"/api/memory/{mid_active}",
        headers=ADMIN_HEADERS,
        json={"fact_value": "新值1"}
    )
    assert res_first.status_code == 200

    # 对旧的 mid_active 再次编辑，必须返回 409
    res_second = client.patch(
        f"/api/memory/{mid_active}",
        headers=ADMIN_HEADERS,
        json={"fact_value": "新值2"}
    )
    assert res_second.status_code == 409
    assert res_second.json()["code"] == "MEMORY_NOT_ACTIVE"


def test_t5_patch_memory_rbac_restrictions():
    """T5 权限控制：仅 admin 角色可编辑，非 admin (staff, owner, 匿名) 返回 403 或 401。"""
    vendor = "Vendor_RBAC_Test"
    tenant = "default"

    mid, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="权限测试", sample="", tenant_id=tenant,
        fact_key="alias:白菜", fact_row={"fact_value": "大白菜", "fact_kind": "alias"}
    )

    # 1. staff 角色 -> 403
    res_staff = client.patch(
        f"/api/memory/{mid}",
        headers=STAFF_HEADERS,
        json={"fact_value": "店员篡改"}
    )
    assert res_staff.status_code == 403

    # 2. owner 角色 -> 403 (严格 require_admin)
    res_owner = client.patch(
        f"/api/memory/{mid}",
        headers=OWNER_HEADERS,
        json={"fact_value": "老板篡改"}
    )
    assert res_owner.status_code == 403

    # 3. 匿名无身份 -> 401
    res_anon = client.patch(
        f"/api/memory/{mid}",
        headers={"X-Tenant-Id": "default"},
        json={"fact_value": "匿名篡改"}
    )
    assert res_anon.status_code == 401


def test_t5_patch_memory_validation_oversized_and_empty():
    """T5 字段校验：空更新、空白 fact_key、超长字段统一拦截返回 422。"""
    vendor = "Vendor_Validation_Test"
    tenant = "default"

    mid, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="校验测试", sample="", tenant_id=tenant,
        fact_key="alias:西红柿", fact_row={"fact_value": "番茄", "fact_kind": "alias"}
    )

    # 1. 未提供任何字段 -> 422
    res_empty = client.patch(f"/api/memory/{mid}", headers=ADMIN_HEADERS, json={})
    assert res_empty.status_code == 422
    assert "未提供任何待更新字段" in res_empty.json()["msg"]

    # 2. 全为 None -> 422
    res_none = client.patch(
        f"/api/memory/{mid}",
        headers=ADMIN_HEADERS,
        json={"fact_key": None, "fact_value": None, "notes": None}
    )
    assert res_none.status_code == 422

    # 3. fact_key 纯空白 -> 422
    res_blank_key = client.patch(
        f"/api/memory/{mid}",
        headers=ADMIN_HEADERS,
        json={"fact_key": "   \t\n  "}
    )
    assert res_blank_key.status_code == 422
    assert "fact_key 不能为空" in res_blank_key.json()["msg"]

    # 4. fact_key 超长 (>200) -> 422
    res_long_key = client.patch(
        f"/api/memory/{mid}",
        headers=ADMIN_HEADERS,
        json={"fact_key": "K" * 201}
    )
    assert res_long_key.status_code == 422
    assert "fact_key 长度不能超过 200" in res_long_key.json()["msg"]

    # 5. fact_value 超长 (>1000) -> 422
    res_long_val = client.patch(
        f"/api/memory/{mid}",
        headers=ADMIN_HEADERS,
        json={"fact_value": "V" * 1001}
    )
    assert res_long_val.status_code == 422
    assert "fact_value 长度不能超过 1000" in res_long_val.json()["msg"]

    # 6. notes 超长 (>2000) -> 422
    res_long_notes = client.patch(
        f"/api/memory/{mid}",
        headers=ADMIN_HEADERS,
        json={"notes": "N" * 2001}
    )
    assert res_long_notes.status_code == 422
    assert "notes 长度不能超过 2000" in res_long_notes.json()["msg"]


def test_t5_patch_memory_disallowed_fields_tamper_resistant():
    """T5 篡改防护：payload 中注入 vendor / tenant_id / status 等字段被自动丢弃，保持不可变。"""
    vendor = "Vendor_Tamper_Protect"
    tenant = "default"

    mid, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="原备注", sample="原样本", tenant_id=tenant,
        fact_key="alias:正品", fact_row={"fact_value": "原值", "fact_kind": "alias"}
    )

    # 攻击者尝试通过 PATCH 修改 vendor, tenant_id, status, decay_score
    res = client.patch(
        f"/api/memory/{mid}",
        headers=ADMIN_HEADERS,
        json={
            "vendor": "Malicious_Vendor",
            "tenant_id": "malicious_tenant",
            "status": "archived",
            "decay_score": 0.1,
            "fact_value": "合规修改"
        }
    )
    assert res.status_code == 200
    new_mid = res.json()["data"]["new_memory_id"]

    new_row = db.get_vendor_memory_by_id(new_mid, tenant_id=tenant)
    assert new_row["vendor"] == vendor  # 保持原 vendor
    assert new_row["tenant_id"] == tenant  # 保持原 tenant
    assert new_row["status"] == "active"  # 保持 active
    assert round(new_row["decay_score"], 2) == 0.70  # 初始分 0.70
    assert new_row["fact_value"] == "合规修改"


def test_t5_patch_memory_vector_cascade_and_audit():
    """T5 向量级联与审计：对称执行 archive_memory_vectors (旧) 与 restore_memory_vectors (新)，写审计日志。"""
    vendor = "Vendor_Vector_Audit"
    tenant = "default"

    mid, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="向量测试", sample="", tenant_id=tenant,
        fact_key="alias:莲藕", fact_row={"fact_value": "洪湖莲藕", "fact_kind": "alias"}
    )

    with patch.object(memory_store, "archive_memory_vectors") as mock_archive, \
         patch.object(memory_store, "restore_memory_vectors") as mock_restore:
        res = client.patch(
            f"/api/memory/{mid}",
            headers=ADMIN_HEADERS,
            json={"fact_value": "九孔莲藕"}
        )
        assert res.status_code == 200
        new_mid = res.json()["data"]["new_memory_id"]

        # 断言旧向量归档调用
        mock_archive.assert_called_once_with(tenant, [mid], vendor=vendor, reason="superseded")
        # 断言新向量补写调用
        mock_restore.assert_called_once_with(tenant, new_mid, vendor=vendor)

    # 验证系统审计日志
    raw_audits = db.get_app_setting("system_audit_json", "[]")
    audits = json.loads(raw_audits)
    match_audit = [a for a in audits if a.get("action") == "vendor_memory_edit" and mid in a.get("field", "")]
    assert len(match_audit) > 0
    latest = match_audit[-1]
    assert mid in latest["field"]
    details = json.loads(latest["new"])
    assert details["new_memory_id"] == new_mid
    assert details["fact_value"] == "九孔莲藕"


def test_t5_patch_memory_partial_updates():
    """T5 部分更新：支持仅更新 fact_key, 仅更新 fact_value 或仅更新 notes。"""
    vendor = "Vendor_Partial_Update"
    tenant = "default"

    # 1. 仅更新 notes
    mid_1, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="原注1", sample="样1", tenant_id=tenant,
        fact_key="alias:K1", fact_row={"fact_value": "V1", "fact_kind": "alias"}
    )
    res_notes = client.patch(
        f"/api/memory/{mid_1}",
        headers=ADMIN_HEADERS,
        json={"notes": "仅更新的备注"}
    )
    assert res_notes.status_code == 200
    new_1 = res_notes.json()["data"]["new_memory_id"]
    row_1 = db.get_vendor_memory_by_id(new_1, tenant_id=tenant)
    assert row_1["notes"] == "仅更新的备注"
    assert row_1["fact_key"] == "alias:K1"
    assert row_1["fact_value"] == "V1"

    # 2. 仅更新 fact_key
    mid_2, _ = db.upsert_vendor_memory(
        vendor=vendor, notes="原注2", sample="样2", tenant_id=tenant,
        fact_key="alias:K2", fact_row={"fact_value": "V2", "fact_kind": "alias"}
    )
    res_key = client.patch(
        f"/api/memory/{mid_2}",
        headers=ADMIN_HEADERS,
        json={"fact_key": "alias:K2_NEW"}
    )
    assert res_key.status_code == 200
    new_2 = res_key.json()["data"]["new_memory_id"]
    row_2 = db.get_vendor_memory_by_id(new_2, tenant_id=tenant)
    assert row_2["fact_key"] == "alias:K2_NEW"
    assert row_2["fact_value"] == "V2"
    assert row_2["notes"] == "原注2"

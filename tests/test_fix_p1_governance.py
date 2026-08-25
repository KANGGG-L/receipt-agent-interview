# -*- coding: utf-8 -*-
"""
P1 治理残留修复回归测试集：
- D-P1-3 RAG data_only 调试开关（默认隐藏，显式开启可见）
- D-P1-4 划线 is_void 全链路贯穿（契约 → 算术门禁 → 持久化 → 详情）
- E-P1-1 引擎灰测 Tag 数据源（use_grey 持久化）
- E-P1-2 黄金样本 57 看板端点
- E-P1-3 p-value 卡片端点（阈值 alpha=0.05 + 低置信度标注）
- E-P1-4 403 人话统一（角色指引文案）
- F-P1-3 多币种（白名单校验 + 非法回退 HKD）
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))

os.environ.setdefault("DB_PATH", "/tmp/test_p1_governance.db")
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app import db as _db  # noqa: E402
from app.models import ReceiptData, ReceiptItem, DocForm  # noqa: E402
from app.services.receipt_utils import save_parsed_data  # noqa: E402

client = TestClient(app)


# -------------------------------------------------------------
# D-P1-3 RAG data_only 调试开关
# -------------------------------------------------------------
def test_rag_context_hidden_by_default_and_visible_with_data_only():
    rid = _db.create_receipt(supplier_name="RAG 开关测试", status="parsed")
    _db.update_receipt(rid, rag_context_json="供应商先验：菜心常以扎开单")

    data_default = client.get(f"/api/receipt/{rid}").json()["data"]
    assert "rag_context" not in data_default, "默认视图不得暴露 rag_context"

    data_debug = client.get(f"/api/receipt/{rid}?data_only=true").json()["data"]
    assert data_debug.get("rag_context") == "供应商先验：菜心常以扎开单"


def test_save_parsed_data_persists_rag_context():
    data = ReceiptData(
        doc_form=DocForm.THERMAL, vendor="RAG 持久化供应商", date="2026-08-21",
        items=[ReceiptItem(name="菜心", qty=10, unit="斤", unit_price=8.5, amount=85.0)],
        total=85.0, payment_marked=False, confidence=0.9,
    )
    rid = _db.create_receipt(status="uploaded")
    save_parsed_data(rid, data, {"use_grey": 0, "vendor_context": "先验片段Y", "raw": "", "audit_result": {}})
    row = _db.get_receipt_row(rid)
    assert row.rag_context_json == "先验片段Y"
    detail = client.get(f"/api/receipt/{rid}?data_only=true").json()["data"]
    assert detail["rag_context"] == "先验片段Y"


# -------------------------------------------------------------
# D-P1-4 划线 is_void 全链路贯穿
# -------------------------------------------------------------
def test_is_void_roundtrip_via_save_edited_and_detail():
    rid = _db.create_receipt(supplier_name="划线回归测试", status="parsed")
    resp = client.post("/api/save_edited", json={
        "receipt_id": rid, "supplier_name": "划线回归测试", "date": "2026-08-21",
        "total_amount": 85.0, "settlement_type": "cash", "version": 1,
        "items": [
            {"name": "菜心", "quantity": 10, "unit": "斤", "unit_price": 8.5, "amount": 85.0},
            {"name": "黄花鱼", "quantity": 5, "unit": "条", "unit_price": 30.0, "amount": 150.0, "is_void": True},
        ],
    })
    assert resp.status_code == 200, resp.text
    items = _db.get_receipt_items(rid)
    assert [i["is_void"] for i in items] == [False, True]
    detail = client.get(f"/api/receipt/{rid}").json()["data"]
    assert detail["items"][1]["is_void"] is True


def test_math_engine_excludes_void_rows_from_total():
    from app.services.math_engine import validate_and_report
    data = ReceiptData(
        doc_form=DocForm.PRINTED, vendor="算术门禁回归", date="2026-08-21",
        items=[
            ReceiptItem(name="菜心", qty=10, unit="斤", unit_price=8.5, amount=85.0),
            ReceiptItem(name="黄花鱼", qty=5, unit="条", unit_price=30.0, amount=150.0, is_void=True),
        ],
        total=85.0, payment_marked=False, confidence=0.95,
    )
    assert validate_and_report(data) == []


# -------------------------------------------------------------
# E-P1-1 引擎灰测 Tag 数据源
# -------------------------------------------------------------
def test_use_grey_persisted_for_grey_tag():
    data = ReceiptData(
        doc_form=DocForm.NCR_HAND, vendor="灰测标记供应商", date="2026-08-21",
        items=[ReceiptItem(name="生菜", qty=3, unit="斤", unit_price=6.0, amount=18.0)],
        total=18.0, payment_marked=False, confidence=0.9,
    )
    rid = _db.create_receipt(status="uploaded")
    save_parsed_data(rid, data, {"use_grey": 1, "raw": "", "audit_result": {}})
    row = _db.get_receipt_row(rid)
    assert row.use_grey == 1
    # 列表行与详情均携带 use_grey（前端 greyBadgeHtml / 标题徽标数据源）
    assert client.get(f"/api/receipt/{rid}").json()["data"]["use_grey"] == 1
    list_row = next(r for r in client.get("/api/receipts").json()["data"] if r["id"] == rid)
    assert list_row["use_grey"] == 1


# -------------------------------------------------------------
# E-P1-2 黄金样本 57 看板
# -------------------------------------------------------------
def test_golden_samples_board_endpoint_admin_only():
    resp = client.get("/api/admin/golden-samples", headers={"X-Role": "admin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["target_total"] == 57
    assert isinstance(body["coverage"], dict) and body["coverage"]
    assert isinstance(body["items"], list)


def test_golden_samples_board_requires_admin():
    resp = client.get("/api/admin/golden-samples", headers={"X-Role": "owner"})
    assert resp.status_code == 403
    assert "权限不足" in resp.json().get("detail", "")


# -------------------------------------------------------------
# E-P1-3 p-value 卡片
# -------------------------------------------------------------
def test_pvalue_card_404_for_missing_experiment():
    resp = client.get("/api/admin/experiments/99999/pvalue", headers={"X-Role": "admin"})
    assert resp.status_code == 404


def test_pvalue_card_returns_three_metric_cards_with_threshold_note():
    exp = _db.create_experiment(name="pvalue 回归实验", hypothesis="h")["id"]
    for i in range(35):
        _db.log_ai_decision(
            experiment_id=exp, grp="control" if i % 2 else "treatment",
            decision_type="extract", adopted=1 if i % 3 else 0, confidence=0.9)
    resp = client.get(f"/api/admin/experiments/{exp}/pvalue", headers={"X-Role": "admin"})
    assert resp.status_code == 200
    body = resp.json()
    metrics = {c["metric"] for c in body["cards"]}
    assert metrics == {"accuracy", "hallucination_rate", "edit_rate"}
    acc = next(c for c in body["cards"] if c["metric"] == "accuracy")
    assert acc["p_value"] is not None
    # 阈值说明必须显式携带 alpha=0.05 判定语义
    assert "0.05" in acc["note"]
    assert ("显著" in acc["note"]) or ("不显著" in acc["note"])
    # 小样本 → 低置信度标注
    assert body["low_confidence"] is True
    assert "低置信度" in acc["note"] or body["low_confidence"] is True


# -------------------------------------------------------------
# E-P1-4 403 人话统一
# -------------------------------------------------------------
def test_403_message_includes_role_guidance_for_owner_api():
    resp = client.get("/api/receipts/export", headers={"X-Role": "staff"})
    assert resp.status_code == 403
    detail = resp.json().get("detail", "")
    assert "权限不足" in detail
    assert "staff" in detail and "owner" in detail
    assert "切换角色" in detail or "联系管理员" in detail


def test_403_message_includes_role_guidance_for_admin_api():
    resp = client.get("/api/admin/engine-config", headers={"X-Role": "owner"})
    assert resp.status_code == 403
    detail = resp.json().get("detail", "")
    assert "权限不足" in detail and "admin" in detail


# -------------------------------------------------------------
# F-P1-3 多币种
# -------------------------------------------------------------
def test_currency_whitelist_accepts_valid_and_normalizes_case():
    rid = _db.create_receipt(supplier_name="币种测试", status="parsed")
    resp = client.post("/api/save_edited", json={
        "receipt_id": rid, "supplier_name": "币种测试", "date": "2026-08-21",
        "total_amount": 100.0, "settlement_type": "cash", "version": 1,
        "items": [{"name": "菜心", "quantity": 10, "unit": "斤", "unit_price": 10, "amount": 100}],
        "currency": "cny",
    })
    assert resp.status_code == 200
    assert _db.get_receipt_row(rid).currency == "CNY"


def test_currency_invalid_falls_back_to_hkd():
    rid = _db.create_receipt(supplier_name="币种回退测试", status="parsed")
    resp = client.post("/api/save_edited", json={
        "receipt_id": rid, "supplier_name": "币种回退测试", "date": "2026-08-21",
        "total_amount": 100.0, "settlement_type": "cash", "version": 1,
        "items": [{"name": "菜心", "quantity": 10, "unit": "斤", "unit_price": 10, "amount": 100}],
        "currency": "XXX",
    })
    assert resp.status_code == 200
    assert _db.get_receipt_row(rid).currency == "HKD"


def test_detail_and_list_carry_currency_field():
    rid = _db.create_receipt(supplier_name="币种字段测试", status="parsed")
    _db.update_receipt(rid, currency="USD")
    detail = client.get(f"/api/receipt/{rid}").json()["data"]
    assert detail["currency"] == "USD"
    list_row = next(r for r in client.get("/api/receipts").json()["data"] if r["id"] == rid)
    assert list_row["currency"] == "USD"

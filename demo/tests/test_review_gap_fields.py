# -*- coding: utf-8 -*-
"""店员复核界面四缺口修复回归测试（G1-G4，T5/T12 轮审核缺口）。

覆盖：
1. G1 费用抽屉：service_fee / tax_amount 经 /api/save_edited 提交 → 落库 → 回读
2. G2 adjustment_notes：手写注记 list[str] 提交 → 落库 → 回读（空行剔除）
3. G3 payment_evidence：契约 ReceiptData Optional 默认 ""；save_edited 提交落库回读
4. 缺省路径：不传新字段不 422，默认值落库不炸
5. F5 门禁联动：save_edited 后算术门禁用店员提交值（service_fee/tax_amount）重算
6. 前端静态断言：四个新控件 id 存在且位于复核区（Side-by-Side 表单卡内）

全部离线：零外部调用。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_gap_fields.db")
if os.path.exists("/tmp/receipt_demo_gap_fields.db"):
    os.remove("/tmp/receipt_demo_gap_fields.db")

import pytest

from app import db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_gap_fields.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _mk_receipt():
    rid = db.create_receipt(status="uploaded")
    db.update_receipt(rid, supplier_name="祥興食品", receipt_date="2026-08-01")
    return rid


_BASE_BODY = {
    "supplier_name": "祥興食品",
    "date": "2026-08-01",
    "total_amount": 65.0,
    "settlement_type": "cash",
    "items": [{"name": "菜心", "quantity": 5.0, "unit": "斤",
               "unit_price": 10.0, "amount": 50.0}],
    "version": 1,
    "source": "manual",
}


def _save(client, rid, **overrides):
    body = dict(_BASE_BODY)
    body["receipt_id"] = rid
    body.update(overrides)
    return client.post("/api/save_edited", headers={"X-Role": "staff"}, json=body)


def _detail(client, rid):
    resp = client.get("/api/receipt/%d" % rid, headers={"X-Role": "staff"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# -------------------------------------------------------------
# 契约层：payment_evidence Optional + 默认值，extra=forbid 语义不变
# -------------------------------------------------------------
def test_receiptdata_payment_evidence_optional_default():
    from app.models import ReceiptData
    data = ReceiptData(doc_form="printed_delivery_note", vendor="祥興",
                       date="2026-08-01",
                       items=[{"name": "菜心", "qty": 5, "unit": "斤",
                               "unit_price": 10, "amount": 50}],
                       total=50.0, payment_marked=False, confidence=0.9)
    assert data.payment_evidence == "", "契约缺省 payment_evidence 必须默认空串"
    assert data.service_fee == 0.0 and data.tax_amount == 0.0
    assert data.adjustment_notes == []


def test_receiptdata_extra_forbid_still_rejects_unknown_field():
    from pydantic import ValidationError
    from app.models import ReceiptData
    with pytest.raises(ValidationError):
        ReceiptData(doc_form="printed_delivery_note", vendor="祥興",
                    date="2026-08-01",
                    items=[{"name": "菜心", "qty": 5, "unit": "斤",
                            "unit_price": 10, "amount": 50}],
                    total=50.0, payment_marked=False, confidence=0.9,
                    not_in_schema="x")


def test_validate_contract_roundtrip_with_payment_evidence():
    from app.services.contract import validate_contract
    payload = {
        "doc_form": "printed_delivery_note", "vendor": "祥興",
        "date": "2026-08-01",
        "items": [{"name": "菜心", "qty": 5.0, "unit": "斤",
                   "unit_price": 10.0, "amount": 50.0}],
        "total": 50.0, "payment_marked": True,
        "payment_evidence": "红色印章", "confidence": 0.9,
    }
    data, err = validate_contract(payload)
    assert err is None, err
    assert data.payment_evidence == "红色印章"


# -------------------------------------------------------------
# save_edited 持久化：G1/G2/G3 提交 → 落库 → 回读
# -------------------------------------------------------------
def test_save_edited_persists_gap_fields(client):
    rid = _mk_receipt()
    resp = _save(client, rid,
                 service_fee=15.0, tax_amount=0.0,
                 adjustment_notes=["拒收 2 包", "  ", "短装 1 箱"],
                 payment_evidence="红色印章")
    assert resp.status_code == 200, resp.text
    data = _detail(client, rid)
    assert data["service_fee"] == 15.0
    assert data["tax_amount"] == 0.0
    assert data["adjustment_notes"] == ["拒收 2 包", "短装 1 箱"]
    assert data["payment_evidence"] == "红色印章"


def test_save_edited_defaults_without_new_fields(client):
    """缺省路径：不传新字段不 422，默认值落库不炸。"""
    rid = _mk_receipt()
    resp = _save(client, rid, total_amount=50.0)
    assert resp.status_code == 200, resp.text
    data = _detail(client, rid)
    assert data["service_fee"] == 0.0
    assert data["tax_amount"] == 0.0
    assert data["adjustment_notes"] == []
    assert data["payment_evidence"] == ""


def test_save_edited_overwrites_previous_values(client):
    rid = _mk_receipt()
    assert _save(client, rid, service_fee=15.0,
                 adjustment_notes=["拒收 2 包"],
                 payment_evidence="红色印章").status_code == 200
    # 第二次保存清空/修正：店员修正必须覆盖旧值而非只增不减
    assert _save(client, rid, version=2, total_amount=50.0,
                 service_fee=0.0, tax_amount=3.0,
                 adjustment_notes=[], payment_evidence="").status_code == 200
    data = _detail(client, rid)
    assert data["service_fee"] == 0.0
    assert data["tax_amount"] == 3.0
    assert data["adjustment_notes"] == []
    assert data["payment_evidence"] == ""


# -------------------------------------------------------------
# F5 门禁联动：save_edited 用店员提交值重算算术门禁
# -------------------------------------------------------------
def test_math_gate_recomputed_with_user_values_consistent(client):
    """明细 50 + 服务费 15 = 总额 65 → 用提交值重算后门禁通过（warnings 清空）。"""
    rid = _mk_receipt()
    resp = _save(client, rid, service_fee=15.0, tax_amount=0.0)
    assert resp.status_code == 200, resp.text
    data = _detail(client, rid)
    assert data["math_warnings"] == [], \
        "店员修正后的费用值必须进算术门禁重算（50+15=65 应通过）"


def test_math_gate_flags_mismatch_with_user_values(client):
    """总额与提交费用不符（漏加服务费）→ 门禁用提交值报出差额。"""
    rid = _mk_receipt()
    resp = _save(client, rid, total_amount=50.0, service_fee=15.0)
    assert resp.status_code == 200, resp.text
    data = _detail(client, rid)
    warnings = data["math_warnings"]
    assert warnings, "门禁必须用店员提交的 service_fee 重算并报不一致"
    assert any("服务费=15" in w for w in warnings), warnings


# -------------------------------------------------------------
# 前端静态断言：四个新控件 id 存在且在复核区
# -------------------------------------------------------------
def _index_html():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates", "index.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_four_new_controls_exist_in_review_area():
    html = _index_html()
    for ctrl_id in ("inpServiceFee", "inpTaxAmount",
                    "inpAdjustmentNotes", "inpPaymentEvidence"):
        assert 'id="%s"' % ctrl_id in html, "复核区缺少新控件 #%s" % ctrl_id
    # 位置：全部位于 Side-by-Side 复核表单卡内（供应商输入之后、明细表之前）
    anchor_supplier = html.index('id="inpSupplier"')
    anchor_table = html.index('id="itemTableBody"')
    for ctrl_id in ("inpServiceFee", "inpTaxAmount",
                    "inpAdjustmentNotes", "inpPaymentEvidence"):
        pos = html.index('id="%s"' % ctrl_id)
        assert anchor_supplier < pos < anchor_table, \
            "#%s 不在复核区（供应商与明细表之间）" % ctrl_id
    # 标签人话
    assert "服务费 (加一)" in html
    assert "税额/VAT" in html
    assert "手写注记" in html
    assert "付款证据" in html


def test_main_js_collects_new_fields():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "static", "js", "main.js")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    collect_start = src.index("function collectReviewFormData")
    collect_end = src.index("function buildSavePayloadFromData")
    collect_src = src[collect_start:collect_end]
    for key in ("service_fee", "tax_amount", "adjustment_notes", "payment_evidence",
                "inpServiceFee", "inpTaxAmount", "inpAdjustmentNotes",
                "inpPaymentEvidence"):
        assert key in collect_src, "collectReviewFormData 未采集 %s" % key

# -*- coding: utf-8 -*-
"""店员复核界面四缺口修复回归测试（G1-G4，T5/T12 轮审核缺口）。

覆盖：
1. G1 费用抽屉：service_fee / tax_amount 经 /api/save_edited 提交 → 落库 → 回读
2. G2 adjustment_notes：手写注记 list[str] 提交 → 落库 → 回读（空行剔除）
3. G3 payment_evidence：契约 ReceiptData Optional 默认 ""；save_edited 提交落库回读
4. 缺省路径：不传新字段不 422，默认值落库不炸
5. F5 门禁联动：save_edited 后算术门禁用店员提交值（service_fee/tax_amount）重算
6. 前端静态断言：附加费用「动态费用行」视图（添加按钮/行容器/占位文案/类型六项/
   删除归 0/类型去重提示），service_fee / tax_amount 经 collectFeeMap 采集

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
# 前端静态断言：附加费用改为「动态费用行」模式（按需添加，不再默认铺六宫格）
# -------------------------------------------------------------
def _index_html():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "templates", "index.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _main_js_src():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "static", "js", "main.js")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_dynamic_fee_rows_ui_mode():
    """动态费用行视图：添加按钮/行容器/占位提示存在且位于复核区，固定六宫格已移除。"""
    html = _index_html()
    # 动态模式三要素：占位提示、行容器、添加按钮
    assert 'id="feesRowsEmpty"' in html, "缺少费用占位提示 #feesRowsEmpty"
    assert 'id="feesRowsContainer"' in html, "缺少动态费用行容器 #feesRowsContainer"
    assert 'id="btnAddFeeRow"' in html, "缺少「+ 添加费用」按钮 #btnAddFeeRow"
    assert "无附加费用" in html, "占位提示必须是人话文案「无附加费用」"
    assert "+ 添加费用" in html
    # 位置：费用抽屉仍在 Side-by-Side 复核表单卡内（供应商输入之后、明细表之前）
    anchor_supplier = html.index('id="inpSupplier"')
    anchor_table = html.index('id="itemTableBody"')
    for mark in ('id="feesRowsEmpty"', 'id="feesRowsContainer"', 'id="btnAddFeeRow"'):
        pos = html.index(mark)
        assert anchor_supplier < pos < anchor_table, \
            "%s 不在复核区（供应商与明细表之间）" % mark
    # 旧固定六宫格输入框不复存在（默认不再铺 6 个空输入框）
    for legacy_id in ("inpDiscount", "inpDeliveryFee", "inpServiceFee",
                      "inpTaxAmount", "inpDeposit", "inpRounding"):
        assert 'id="%s"' % legacy_id not in html, \
            "固定六宫格输入 #%s 应由动态费用行取代" % legacy_id
    # 仍保留的手写注记 / 付款证据控件
    for ctrl_id in ("inpAdjustmentNotes", "inpPaymentEvidence"):
        assert 'id="%s"' % ctrl_id in html, "复核区缺少控件 #%s" % ctrl_id
    assert "手写注记" in html
    assert "付款证据" in html


def test_fee_type_options_match_contract_fields():
    """原因下拉六项与六个契约字段一一对应，带既有正负号 label。"""
    src = _main_js_src()
    start = src.index("const FEE_TYPES")
    end = src.index("];", start)
    fee_types_src = src[start:end]
    expectations = [
        ("discount_amount", "整单折扣/折让 (-)"),
        ("delivery_fee", "送货运费 (+)"),
        ("service_fee", "服务费 (加一) (+)"),
        ("tax_amount", "税额/VAT (+)"),
        ("deposit_amount", "胶筐押金 (+)"),
        ("rounding_adjustment", "尾数抹零 (-)"),
    ]
    for key, label in expectations:
        assert "key: '%s'" % key in fee_types_src, "FEE_TYPES 缺少契约字段 %s" % key
        assert "'%s'" % label in fee_types_src, "FEE_TYPES 缺少费用原因 label「%s」" % label


def test_main_js_collects_new_fields():
    src = _main_js_src()
    collect_start = src.index("function collectReviewFormData")
    collect_end = src.index("function buildSavePayloadFromData")
    collect_src = src[collect_start:collect_end]
    for key in ("service_fee", "tax_amount", "adjustment_notes", "payment_evidence",
                "collectFeeMap"):
        assert key in collect_src, "collectReviewFormData 未采集 %s" % key
    # collectFeeMap 必须映射回全部六个契约字段（未出现的类型 = 0）
    map_start = src.index("function collectFeeMap")
    map_end = src.index("function updateFeesSummaryBadge")
    map_src = src[map_start:map_end]
    for key in ("discount_amount", "delivery_fee", "service_fee",
                "tax_amount", "deposit_amount", "rounding_adjustment"):
        assert key in map_src, "collectFeeMap 未映射契约字段 %s" % key


def test_fee_row_delete_means_zero_semantics():
    """行删除 = 该项归 0：removeFeeRow 只做 remove + 重算，
    collectFeeMap 仅遍历现存行（未出现的类型缺省 0）——契约字段不会残留旧值。"""
    src = _main_js_src()
    rm_start = src.index("function removeFeeRow")
    rm_end = src.index("function updateFeesEmptyState")
    rm_src = src[rm_start:rm_end]
    assert "row.remove()" in rm_src, "删除费用行必须移除该行 DOM"
    assert "recalcTotalSum()" in rm_src, "删除费用行后必须联动重算总额"
    # 归 0 语义的关键：collectFeeMap 从零值起步，仅统计现存的费用行
    map_start = src.index("function collectFeeMap")
    map_end = src.index("function updateFeesSummaryBadge")
    map_src = src[map_start:map_end]
    assert "querySelectorAll('.fee-row')" in map_src, "collectFeeMap 必须遍历现存费用行"
    assert "discount_amount: 0.00" in map_src, "未出现的类型必须缺省归 0"


def test_fee_type_dedup_with_humanized_hint():
    """同一类型不可重复：重复选择时人话提示「该项已添加，已在上方标出」并聚焦已有行。"""
    src = _main_js_src()
    start = src.index("function onFeeTypeChange")
    end = src.index("function findFeeRowByType")
    dup_src = src[start:end]
    assert "该项已添加，已在上方标出" in dup_src, "重复选择费用类型必须有人话提示"
    assert "showToast" in dup_src, "提示必须经 toast 呈现"
    assert "focus()" in dup_src, "重复选择时必须聚焦已有行"


def test_fee_rows_render_and_autofocus():
    """打开单据：非零费用字段渲染为行（renderFeeRowsFromData）；
    点「+ 添加费用」新行自动聚焦原因下拉（addFeeRow）。"""
    src = _main_js_src()
    render_start = src.index("function renderFeeRowsFromData")
    render_end = src.index("function appendFeeRow")
    render_src = src[render_start:render_end]
    assert "container.innerHTML = ''" in render_src, "渲染前必须清空旧费用行"
    assert "appendFeeRow(" in render_src, "非零费用字段必须渲染为费用行"
    assert "updateFeesEmptyState()" in render_src, "渲染后必须刷新占位提示状态"
    add_start = src.index("function addFeeRow")
    add_end = src.index("function onFeeTypeChange")
    add_src = src[add_start:add_end]
    assert "focus()" in add_src, "「+ 添加费用」新增行必须自动聚焦原因下拉"

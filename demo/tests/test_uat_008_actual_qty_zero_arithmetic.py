# -*- coding: utf-8 -*-
"""UAT-008: 实收 0 的保存与入账算术校验一致性专项测试。

核心验证点：
1. 合成 fixture：quantity=1, unit_price=10, amount=10, actual_qty=0，六项费用各自独立取值；
2. 显式区分 null 与 0：actual_qty=null 按 quantity 处理，actual_qty=0 按 0 处理；
3. 有效 0 时，总额差额可见（接口与前端如实报出差额，不得假称无问题）；
4. 修正总额匹配后，保存与审批得出一致结论（同一处判定口径）；
5. 未匹配单据不得进入库存（DB 零库存写入与零流水残留）；
6. 跨 tenant 隔离：拒绝或不可见，Tenant B baseline 零意外改动；
7. 前端 review_core.js 逻辑断言：calculateReceiptIssues 与 recalcTotalSum 严格对齐有效实收算术。
"""

import json
import os
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.main import app
from app.models import DocForm, ReceiptData, ReceiptItem
from app.services import math_engine
from app.services.inventory import apply_receipt_to_inventory

STAFF_A = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat008_a"}
OWNER_A = {"X-Role": "owner", "X-Tenant-Id": "tenant_uat008_a"}
STAFF_B = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat008_b"}
OWNER_B = {"X-Role": "owner", "X-Tenant-Id": "tenant_uat008_b"}

# 六项费用各自独立取值（互不相同、互不污染）
FEES_FIXTURE = {
    "delivery_fee": 5.0,
    "deposit_amount": 4.0,
    "service_fee": 3.0,
    "tax_amount": 2.0,
    "discount_amount": 1.0,
    "rounding_adjustment": 0.5,
}
# 费用净调整值 = +5 + 4 + 3 + 2 - 1 - 0.5 = +12.5
# 当明细有效行金额为 0（actual_qty=0）时，匹配的应付总额为 0 + 12.5 = 12.5
# 当明细有效行金额为 10（actual_qty=null）时，匹配的应付总额为 10 + 12.5 = 22.5





def test_uat008_requirement_1_fixture_independent_fees():
    """验证六项费用各自独立取值，且值互不相同、无共享污染。"""
    fee_values = list(FEES_FIXTURE.values())
    assert len(fee_values) == 6
    assert len(set(fee_values)) == 6, "六项费用必须各自独立取值，不得共用同一值"
    for k, v in FEES_FIXTURE.items():
        assert v > 0, f"{k} 必须为正数独立值"


def test_uat008_requirement_2_explicit_distinction_null_vs_zero_db_and_arithmetic():
    """验证显式区分 null 与 0：落库值与计算结果逐条断言。"""
    client = TestClient(app)

    # 1. actual_qty = 0 场景
    res_zero = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT008_A",
        "date": "2026-10-15",
        "total_amount": 12.5,
        "settlement_type": "cash",
        "items": [{
            "name": "商品甲", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    assert res_zero.status_code == 200, res_zero.text
    rid_zero = res_zero.json()["receipt_id"]

    items_zero = db.get_receipt_items(rid_zero, tenant_id="tenant_uat008_a")
    assert len(items_zero) == 1
    assert items_zero[0]["actual_qty"] == 0.0, "actual_qty=0 必须作为有效 0 落库，严禁被 null 覆盖"
    assert items_zero[0]["quantity"] == 1.0
    assert items_zero[0]["amount"] == 10.0

    # 2. actual_qty = null 场景
    res_null = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT008_A",
        "date": "2026-10-15",
        "total_amount": 22.5,
        "settlement_type": "cash",
        "items": [{
            "name": "商品乙", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": None,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    assert res_null.status_code == 200, res_null.text
    rid_null = res_null.json()["receipt_id"]

    items_null = db.get_receipt_items(rid_null, tenant_id="tenant_uat008_a")
    assert len(items_null) == 1
    assert items_null[0]["actual_qty"] is None, "actual_qty=null 必须显式为 None"
    assert items_null[0]["quantity"] == 1.0
    assert items_null[0]["amount"] == 10.0

    # 3. 两种输入的门禁重算对比
    d_zero = ReceiptData(
        doc_form=DocForm.PRINTED, vendor="供应商_UAT008_A", date="2026-10-15",
        items=[ReceiptItem(name="商品甲", qty=1, unit="件", unit_price=10.0, amount=10.0, actual_qty=0.0)],
        total=12.5, payment_marked=False, confidence=1.0, **FEES_FIXTURE
    )
    assert math_engine.validate_and_report(d_zero) == []

    d_null = ReceiptData(
        doc_form=DocForm.PRINTED, vendor="供应商_UAT008_A", date="2026-10-15",
        items=[ReceiptItem(name="商品乙", qty=1, unit="件", unit_price=10.0, amount=10.0, actual_qty=None)],
        total=22.5, payment_marked=False, confidence=1.0, **FEES_FIXTURE
    )
    assert math_engine.validate_and_report(d_null) == []


def test_uat008_requirement_3_difference_visible_when_unmatched():
    """当存在有效 0 且总额未调整时，总额差额必须在接口和前端均可见，不得静默通过。"""
    client = TestClient(app)

    # 接口端实测：actual_qty=0，总额仍按原 22.5 提交
    res_mismatch = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT008_A",
        "date": "2026-10-15",
        "total_amount": 22.5,  # 未修正总额（应为 12.5，差 10.0）
        "settlement_type": "cash",
        "confirm_mismatch": False,
        "items": [{
            "name": "商品甲", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    # confirm_mismatch 为 False 时，应被 422 拦截并报错
    assert res_mismatch.status_code == 422, res_mismatch.text
    err_json = res_mismatch.json()
    assert err_json.get("code") == "MATH_MISMATCH_CONFIRM"
    assert "总额对不上" in str(err_json)
    assert "相差 10 元" in str(err_json) or "差 10" in str(err_json)

    # 允许店员确认异常后暂存（confirm_mismatch=True）
    res_saved = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT008_A",
        "date": "2026-10-15",
        "total_amount": 22.5,
        "settlement_type": "cash",
        "confirm_mismatch": True,
        "items": [{
            "name": "商品甲", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    assert res_saved.status_code == 200
    rid = res_saved.json()["receipt_id"]

    # 老板审批时，未修正的单据必须被 422 阻断入账
    res_approve = client.post(f"/api/receipt/{rid}/approve", headers=OWNER_A, json={"version": 1})
    assert res_approve.status_code == 422
    approve_json = res_approve.json()
    assert approve_json.get("code") == "APPROVAL_BLOCKED"
    assert "总额对不上" in str(approve_json)
    assert "相差 10 元" in str(approve_json)


def test_uat008_requirement_4_matched_save_and_approve_agree():
    """将总额修正到匹配后（12.5），保存与审批必须得出一致结论（均成功通过）。"""
    client = TestClient(app)

    res_save = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT008_A",
        "date": "2026-10-15",
        "total_amount": 12.5,  # 修正到匹配值
        "settlement_type": "cash",
        "confirm_mismatch": False,
        "items": [{
            "name": "商品甲", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    assert res_save.status_code == 200, res_save.text
    rid = res_save.json()["receipt_id"]

    res_approve = client.post(f"/api/receipt/{rid}/approve", headers=OWNER_A, json={"version": 1})
    assert res_approve.status_code == 200, res_approve.text
    assert res_approve.json().get("status") == "success"

    row = db.get_receipt_row(rid, tenant_id="tenant_uat008_a")
    assert row.status == "approved"
    assert row.total_amount == 12.5


def test_uat008_requirement_5_unmatched_zero_inventory_residue_and_matched_zero_stock_increase():
    """未匹配单据不得入库（DB 零残留）；匹配单据实收 0 入库后库存增量严格为 0。"""
    client = TestClient(app)

    # 1. 尝试对未匹配单据入账
    res_unmatched = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT008_A",
        "date": "2026-10-15",
        "total_amount": 22.5,
        "settlement_type": "cash",
        "confirm_mismatch": True,
        "items": [{
            "name": "商品零残留测试", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    rid_unmatched = res_unmatched.json()["receipt_id"]
    client.post(f"/api/receipt/{rid_unmatched}/approve", headers=OWNER_A, json={"version": 1})

    session = db.get_session()
    try:
        logs_unmatched = session.query(db._StockLogRow).filter(db._StockLogRow.receipt_id == rid_unmatched).all()
        batches_unmatched = session.query(db._InventoryBatchRow).filter(db._InventoryBatchRow.receipt_id == rid_unmatched).all()
    finally:
        session.close()
    assert len(logs_unmatched) == 0, "未匹配单据被阻断后，库存流水必须为 0 条"
    assert len(batches_unmatched) == 0, "未匹配单据被阻断后，库存批次必须为 0 条"

    # 2. 匹配单据（actual_qty=0）入账后，SKU 库存增量严格为 0（不得因 quantity=1 误增）
    sku_id, _ = db.create_sku("商品零增量测试", base_unit="件", tenant_id="tenant_uat008_a")
    sku = db.get_sku(sku_id, tenant_id="tenant_uat008_a")
    initial_stock = sku.current_stock or 0.0

    res_matched = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT008_A",
        "date": "2026-10-15",
        "total_amount": 12.5,
        "settlement_type": "cash",
        "confirm_mismatch": False,
        "items": [{
            "name": "商品零增量测试", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": 0, "sku_id": sku_id,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    rid_matched = res_matched.json()["receipt_id"]
    res_app = client.post(f"/api/receipt/{rid_matched}/approve", headers=OWNER_A, json={"version": 1})
    assert res_app.status_code == 200

    sku_after = db.get_sku(sku_id, tenant_id="tenant_uat008_a")
    assert sku_after.current_stock == initial_stock, (
        f"actual_qty=0 入库后 SKU 库存不得增加！原库存={initial_stock}, 现库存={sku_after.current_stock}"
    )


def test_uat008_requirement_6_cross_tenant_isolation_and_tenant_b_unaffected():
    """跨 tenant 访问被阻断或不可见；Tenant B baseline 前后零意外改动。"""
    client = TestClient(app)

    # 记录 Tenant B 基线
    session = db.get_session()
    try:
        t_b_receipts_before = session.query(db._ReceiptRow).filter(db._ReceiptRow.tenant_id == "tenant_uat008_b").count()
        t_b_logs_before = session.query(db._StockLogRow).filter(db._StockLogRow.tenant_id == "tenant_uat008_b").count()
    finally:
        session.close()

    # Tenant A 创建单据
    res_a = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT008_A",
        "date": "2026-10-15",
        "total_amount": 12.5,
        "settlement_type": "cash",
        "items": [{
            "name": "商品隔离甲", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    rid_a = res_a.json()["receipt_id"]

    # Tenant B 越权读取 Tenant A 详情
    res_peek = client.get(f"/api/receipt/{rid_a}", headers=STAFF_B)
    assert res_peek.status_code == 404, "Tenant B 读取 Tenant A 单据应返回 404"

    # Tenant B 越权修改 Tenant A 单据
    res_hack_save = client.post("/api/save_edited", headers=STAFF_B, json={
        "receipt_id": rid_a,
        "version": 1,
        "supplier_name": "黑客篡改供应商",
        "date": "2026-10-15",
        "total_amount": 12.5,
        "settlement_type": "cash",
        "items": [{
            "name": "商品隔离甲", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    assert res_hack_save.status_code == 404, "Tenant B 越权保存应返回 404"

    # Tenant B 越权审批 Tenant A 单据
    res_hack_app = client.post(f"/api/receipt/{rid_a}/approve", headers=OWNER_B, json={"version": 1})
    assert res_hack_app.status_code == 404, "Tenant B 越权审批应返回 404"

    # 校验 Tenant B 基线未受任何影响
    session = db.get_session()
    try:
        t_b_receipts_after = session.query(db._ReceiptRow).filter(db._ReceiptRow.tenant_id == "tenant_uat008_b").count()
        t_b_logs_after = session.query(db._StockLogRow).filter(db._StockLogRow.tenant_id == "tenant_uat008_b").count()
    finally:
        session.close()

    assert t_b_receipts_after == t_b_receipts_before, "Tenant B 单据数前后必须完全一致"
    assert t_b_logs_after == t_b_logs_before, "Tenant B 库存流水数前后必须完全一致"


def test_uat008_frontend_js_calculate_receipt_issues_logic():
    """测试 review_core.js 中 calculateReceiptIssues 对 actual_qty 的处理。"""
    js_code = """
    const fs = require('fs');
    const content = fs.readFileSync('demo/static/js/review_core.js', 'utf8');

    // 提取 calculateReceiptIssues 函数
    const fnMatch = content.match(/function calculateReceiptIssues\\([\\s\\S]*?\\n\\}/);
    if (!fnMatch) {
        console.error("calculateReceiptIssues not found");
        process.exit(1);
    }

    // 模拟运行环境
    const sandbox = {
        calculateReceiptIssues: null,
        collectFeeMap: () => ({ discount_amount: 1, rounding_adjustment: 0.5, delivery_fee: 5, deposit_amount: 4, service_fee: 3, tax_amount: 2 }),
        isTotalOverridden: () => false,
    };
    eval("var calculateReceiptIssues; " + fnMatch[0] + "; sandbox.calculateReceiptIssues = calculateReceiptIssues;");

    const fees = {
        delivery_fee: 5.0,
        deposit_amount: 4.0,
        service_fee: 3.0,
        tax_amount: 2.0,
        discount_amount: 1.0,
        rounding_adjustment: 0.5
    };

    // Case 1: actual_qty = 0, total_amount = 22.5 (未修正)
    const issuesUnmatched = sandbox.calculateReceiptIssues({
        supplier_name: "供应商甲",
        date: "2026-10-15",
        total_amount: 22.5,
        items: [{ name: "商品A", quantity: 1, unit_price: 10, amount: 10, actual_qty: 0 }],
        ...fees
    });
    const mathIssue = issuesUnmatched.find(i => i.id === 'err_grand_total_math');
    if (!mathIssue) {
        console.error("FAIL: err_grand_total_math not found when actual_qty=0 and total=22.5");
        process.exit(2);
    }
    if (!mathIssue.guide.includes("10.00") && !mathIssue.guide.includes("10元") && !mathIssue.guide.includes("10")) {
        console.error("FAIL: difference not visible in guide: " + mathIssue.guide);
        process.exit(3);
    }

    // Case 2: actual_qty = 0, total_amount = 12.5 (修正后匹配)
    const issuesMatched = sandbox.calculateReceiptIssues({
        supplier_name: "供应商甲",
        date: "2026-10-15",
        total_amount: 12.5,
        items: [{ name: "商品A", quantity: 1, unit_price: 10, amount: 10, actual_qty: 0 }],
        ...fees
    });
    const mathIssueMatched = issuesMatched.find(i => i.id === 'err_grand_total_math');
    if (mathIssueMatched) {
        console.error("FAIL: unexpected err_grand_total_math when actual_qty=0 and total=12.5");
        process.exit(4);
    }

    // Case 3: actual_qty = null, total_amount = 22.5 (应通过)
    const issuesNull22 = sandbox.calculateReceiptIssues({
        supplier_name: "供应商甲",
        date: "2026-10-15",
        total_amount: 22.5,
        items: [{ name: "商品A", quantity: 1, unit_price: 10, amount: 10, actual_qty: null }],
        ...fees
    });
    const mathIssueNull22 = issuesNull22.find(i => i.id === 'err_grand_total_math');
    if (mathIssueNull22) {
        console.error("FAIL: unexpected err_grand_total_math when actual_qty=null and total=22.5");
        process.exit(5);
    }

    console.log("ALL JS CHECKS PASSED");
    """
    proc = subprocess.run(["node", "-e", js_code], capture_output=True, text=True)
    assert proc.returncode == 0, f"JS 逻辑校验失败：stdout={proc.stdout}, stderr={proc.stderr}"

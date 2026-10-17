# -*- coding: utf-8 -*-
"""UAT-008（2026-10-17 复验补修）：实收 actual_qty 的归一口径在保存、门禁重算、落库、入账四处一致。

缺陷复现（HEAD b0e6b1e）：actual_qty 为 "" / 纯空白 / 非法字符串（"abc"、"nan"）时，
保存门禁 _math_gate 把它当「未填」（或构造失败吞成 []）按票面 10 元计而放行（200，无警告），
落库层 db.set_receipt_items 却经 money._to_decimal 存成 0.0；入账按 DB 的 0 重算 -> 422
APPROVAL_BLOCKED。同一张单「保存说没问题、入账说对不上」。

本文件断言：
1. money.optional_qty：None/""/空白 -> None；0、0.0、"0"、" 0 " -> 0.0；非法 -> ValueError；
2. 空白实收：保存 200 且落库 None，入账 200，库存按 quantity 入 1（null 按 quantity）；
3. 非法实收：保存 422 INVALID_ACTUAL_QTY，零写库（单据数前后一致）；
4. 有效 0 的四种表示：未修正总额 22.5 -> 保存 422 MATH_MISMATCH_CONFIRM 且差额 10 可见；
   修正到 12.5 -> 保存 200、入账 200、落库 0.0、库存增量 0；
5. 保存（confirm_mismatch=False）放行 <=> 入账放行（同口径矩阵）；
6. 详情接口 gate_issues 与入账结论一致；
7. 前端 calculateReceiptIssues 与后端同口径（空白不当 0，"0"/" 0 " 当 0）。
"""

import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app import money  # noqa: E402
from app.main import app  # noqa: E402

TENANT_A = "tenant_uat008n_a"
TENANT_B = "tenant_uat008n_b"
STAFF_A = {"X-Role": "staff", "X-Tenant-Id": TENANT_A}
OWNER_A = {"X-Role": "owner", "X-Tenant-Id": TENANT_A}

# 六项费用各自独立取值；净调整 = +5 +4 +3 +2 -1 -0.5 = +12.5
FEES = {
    "delivery_fee": 5.0,
    "deposit_amount": 4.0,
    "service_fee": 3.0,
    "tax_amount": 2.0,
    "discount_amount": 1.0,
    "rounding_adjustment": 0.5,
}
TOTAL_IF_ZERO = 12.5   # 有效行 0 + 12.5
TOTAL_IF_NULL = 22.5   # 有效行 10 + 12.5

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _payload(actual_qty, total, *, name="Item_UAT008", confirm=False, sku_id=None):
    item = {"name": name, "quantity": 1, "unit": "pcs",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": actual_qty}
    if sku_id is not None:
        item["sku_id"] = sku_id
    return {"supplier_name": "Vendor_A", "date": "2026-10-17", "total_amount": total,
            "settlement_type": "cash", "confirm_mismatch": confirm, "source": "manual",
            "items": [item], **FEES}


def _count_receipts(tenant):
    s = db.get_session()
    try:
        return s.query(db._ReceiptRow).filter(db._ReceiptRow.tenant_id == tenant).count()
    finally:
        s.close()


def _stock_logs(rid):
    s = db.get_session()
    try:
        return s.query(db._StockLogRow).filter(db._StockLogRow.receipt_id == rid).all()
    finally:
        s.close()


# ---------------------------------------------------------------- 1. helper
@pytest.mark.parametrize("raw,expected", [
    (None, None), ("", None), ("  ", None), ("\t", None),
    (0, 0.0), (0.0, 0.0), ("0", 0.0), (" 0 ", 0.0), ("0.0", 0.0),
    (1, 1.0), ("2.5", 2.5), (1.23456, 1.2346),
])
def test_optional_qty_contract(raw, expected):
    got = money.optional_qty(raw)
    assert got == expected
    if expected is None:
        assert got is None
    else:
        assert got is not None and isinstance(got, float)


@pytest.mark.parametrize("raw", ["abc", "nan", "inf", "-inf", True, False, [1], {"a": 1}])
def test_optional_qty_rejects_invalid_strict_and_lax_is_none(raw):
    with pytest.raises(ValueError):
        money.optional_qty(raw)
    assert money.optional_qty(raw, strict=False) is None


# ---------------------------------------------------------------- 2. blank = unfilled
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_actual_qty_save_and_approve_agree_and_stock_uses_quantity(blank):
    client = TestClient(app)
    sku_id, _ = db.create_sku("Item_UAT008_blank_%d" % len(blank), base_unit="pcs", tenant_id=TENANT_A)
    before = db.get_sku(sku_id, tenant_id=TENANT_A).current_stock or 0.0

    r = client.post("/api/save_edited", headers=STAFF_A,
                    json=_payload(blank, TOTAL_IF_NULL, name="Item_UAT008_blank_%d" % len(blank),
                                  sku_id=sku_id))
    assert r.status_code == 200, r.text
    rid = r.json()["receipt_id"]
    items = db.get_receipt_items(rid, tenant_id=TENANT_A)
    assert items[0]["actual_qty"] is None, "空白实收 = 未填，落库必须是 None，不能伪造成有效 0"

    ap = client.post(f"/api/receipt/{rid}/approve", headers=OWNER_A, json={"version": 1})
    assert ap.status_code == 200, ap.text  # 保存放行 => 入账也放行
    after = db.get_sku(sku_id, tenant_id=TENANT_A).current_stock or 0.0
    assert after - before == pytest.approx(1.0), "未填实收按 quantity=1 入库"


# ---------------------------------------------------------------- 3. invalid rejected
@pytest.mark.parametrize("bad", ["abc", "nan", "inf", True])
def test_invalid_actual_qty_rejected_without_db_write(bad):
    client = TestClient(app)
    n_before = _count_receipts(TENANT_A)
    r = client.post("/api/save_edited", headers=STAFF_A, json=_payload(bad, TOTAL_IF_NULL, confirm=True))
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "INVALID_ACTUAL_QTY"
    assert "第 1 行" in body["msg"]
    assert _count_receipts(TENANT_A) == n_before, "非法实收被拒绝时不得写库"


# ---------------------------------------------------------------- 4. effective zero variants
ZERO_VARIANTS = [0, 0.0, "0", " 0 "]


@pytest.mark.parametrize("zero", ZERO_VARIANTS)
def test_effective_zero_unmatched_difference_visible_and_blocked(zero):
    client = TestClient(app)
    r = client.post("/api/save_edited", headers=STAFF_A, json=_payload(zero, TOTAL_IF_NULL))
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "MATH_MISMATCH_CONFIRM"
    assert "总额对不上" in body["msg"] and "相差 10 元" in body["msg"]
    assert "应付 12.5 元" in body["msg"]

    # 店员确认后暂存：入账必须仍被拦，且差额可见；零库存流水
    r2 = client.post("/api/save_edited", headers=STAFF_A, json=_payload(zero, TOTAL_IF_NULL, confirm=True))
    assert r2.status_code == 200, r2.text
    rid = r2.json()["receipt_id"]
    assert db.get_receipt_items(rid, tenant_id=TENANT_A)[0]["actual_qty"] == 0.0

    detail = client.get(f"/api/receipt/{rid}", headers=STAFF_A)
    assert detail.status_code == 200
    blob = json.dumps(detail.json(), ensure_ascii=False)
    assert "TOTAL_MISMATCH" in blob, "详情接口必须同样报出总额差额，不能假称无问题"

    ap = client.post(f"/api/receipt/{rid}/approve", headers=OWNER_A, json={"version": 1})
    assert ap.status_code == 422
    assert ap.json()["code"] == "APPROVAL_BLOCKED"
    assert "相差 10 元" in ap.json()["msg"]
    assert _stock_logs(rid) == [], "未匹配不得入库存"


@pytest.mark.parametrize("zero", ZERO_VARIANTS)
def test_effective_zero_matched_save_approve_agree_and_zero_stock_delta(zero):
    client = TestClient(app)
    sname = "Item_UAT008_zero_%s" % repr(zero).replace(" ", "_").replace("'", "s")
    sku_id, _ = db.create_sku(sname, base_unit="pcs", tenant_id=TENANT_A)
    before = db.get_sku(sku_id, tenant_id=TENANT_A).current_stock or 0.0

    r = client.post("/api/save_edited", headers=STAFF_A,
                    json=_payload(zero, TOTAL_IF_ZERO, name=sname, sku_id=sku_id))
    assert r.status_code == 200, r.text
    rid = r.json()["receipt_id"]
    assert db.get_receipt_items(rid, tenant_id=TENANT_A)[0]["actual_qty"] == 0.0

    detail = client.get(f"/api/receipt/{rid}", headers=STAFF_A).json()
    assert "TOTAL_MISMATCH" not in json.dumps(detail, ensure_ascii=False)

    ap = client.post(f"/api/receipt/{rid}/approve", headers=OWNER_A, json={"version": 1})
    assert ap.status_code == 200, ap.text
    after = db.get_sku(sku_id, tenant_id=TENANT_A).current_stock or 0.0
    assert after == before, "实收 0 入账后库存增量必须为 0（不得按 quantity=1 误增）"
    assert db.get_receipt_row(rid, tenant_id=TENANT_A).status == "approved"


# ---------------------------------------------------------------- 5. agreement matrix
@pytest.mark.parametrize("actual,total", [
    (None, TOTAL_IF_NULL), (None, TOTAL_IF_ZERO),
    (0, TOTAL_IF_ZERO), (0, TOTAL_IF_NULL),
    ("", TOTAL_IF_NULL), ("", TOTAL_IF_ZERO),
    ("  ", TOTAL_IF_NULL), (" 0 ", TOTAL_IF_ZERO), ("0", TOTAL_IF_NULL),
])
def test_save_gate_and_approve_gate_reach_same_conclusion(actual, total):
    client = TestClient(app)
    strict = client.post("/api/save_edited", headers=STAFF_A, json=_payload(actual, total))
    save_ok = strict.status_code == 200
    if save_ok:
        rid = strict.json()["receipt_id"]
    else:
        assert strict.json()["code"] == "MATH_MISMATCH_CONFIRM", strict.text
        forced = client.post("/api/save_edited", headers=STAFF_A, json=_payload(actual, total, confirm=True))
        assert forced.status_code == 200, forced.text
        rid = forced.json()["receipt_id"]
    ap = client.post(f"/api/receipt/{rid}/approve", headers=OWNER_A, json={"version": 1})
    approve_ok = ap.status_code == 200
    assert save_ok == approve_ok, (
        "保存门禁与入账门禁结论不一致: actual=%r total=%r save=%s approve=%s %s"
        % (actual, total, strict.status_code, ap.status_code, ap.text))
    if not approve_ok:
        assert _stock_logs(rid) == []


# ---------------------------------------------------------------- 6. tenant B
def test_tenant_b_baseline_untouched_by_normalization_paths():
    client = TestClient(app)
    b_before = _count_receipts(TENANT_B)
    r = client.post("/api/save_edited", headers=STAFF_A, json=_payload("0", TOTAL_IF_ZERO))
    rid = r.json()["receipt_id"]
    assert client.get(f"/api/receipt/{rid}", headers={"X-Role": "staff", "X-Tenant-Id": TENANT_B}).status_code == 404
    assert client.post(f"/api/receipt/{rid}/approve", headers={"X-Role": "owner", "X-Tenant-Id": TENANT_B},
                       json={"version": 1}).status_code == 404
    assert _count_receipts(TENANT_B) == b_before


# ---------------------------------------------------------------- 7. frontend same contract
def test_frontend_calculate_receipt_issues_blank_vs_zero_contract():
    js = r"""
    const fs = require('fs');
    const src = fs.readFileSync('demo/static/js/review_core.js', 'utf8');
    const m = src.match(/function calculateReceiptIssues\([\s\S]*?\n\}/);
    if (!m) { console.error('calculateReceiptIssues not found'); process.exit(1); }
    eval('var calculateReceiptIssues; ' + m[0]);
    const fees = { delivery_fee: 5, deposit_amount: 4, service_fee: 3, tax_amount: 2,
                   discount_amount: 1, rounding_adjustment: 0.5 };
    function hasTotalIssue(actual, total) {
      const issues = calculateReceiptIssues(Object.assign({
        supplier_name: 'Vendor_A', date: '2026-10-17', total_amount: total,
        items: [{ name: 'Item', quantity: 1, unit_price: 10, amount: 10, actual_qty: actual }]
      }, fees));
      const it = issues.find(i => i.id === 'err_grand_total_math');
      return it ? it.guide : null;
    }
    const cases = [
      // [actual, total, expectIssue]
      [null, 22.5, false], [undefined, 22.5, false], ['', 22.5, false], ['   ', 22.5, false],
      [0, 22.5, true], ['0', 22.5, true], [' 0 ', 22.5, true], [0.0, 22.5, true],
      [0, 12.5, false], ['0', 12.5, false], [' 0 ', 12.5, false],
      ['   ', 12.5, true], [null, 12.5, true],
    ];
    const out = [];
    let bad = 0;
    for (const [a, t, exp] of cases) {
      const g = hasTotalIssue(a, t);
      const ok = (g !== null) === exp && (!exp || g.indexOf('10.00') >= 0);
      out.push({ actual: a === undefined ? 'undefined' : a, total: t, expectIssue: exp, guide: g, ok });
      if (!ok) bad++;
    }
    console.log(JSON.stringify(out));
    process.exit(bad ? 2 : 0);
    """
    proc = subprocess.run(["node", "-e", js], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, "stdout=%s stderr=%s" % (proc.stdout, proc.stderr)


# ---------------------------------------------------------------- 8. auto total single source
def test_frontend_auto_total_is_actual_aware_and_shared_with_override_hint():
    """recalcTotalSum 与 review_ux.itemsSumNow（总额手改提示）必须共用实收感知的同一实现。

    HEAD 时 itemsSumNow 只加 .inp-amount：实收 0、手填总额 22.5 时提示被隐藏且手改标记被清除，
    与问题摘要栏「相差 10」相反（真实浏览器复现见 artifacts/uat008-dev/logs/probe-ui-red-review_ux-HEAD.txt）。
    """
    core = open(os.path.join(REPO_ROOT, "demo/static/js/review_core.js"), encoding="utf-8").read()
    ux = open(os.path.join(REPO_ROOT, "demo/static/js/review_ux.js"), encoding="utf-8").read()
    assert "function computeItemsAutoTotal()" in core
    assert "window.computeItemsAutoTotal = computeItemsAutoTotal;" in core
    recalc = core[core.index("function recalcTotalSum()"):]
    recalc = recalc[:recalc.index("\n}\n")]
    assert "computeItemsAutoTotal()" in recalc
    sum_now = ux[ux.index("function itemsSumNow()"):]
    sum_now = sum_now[:sum_now.index("\n    }\n")]
    assert "root.computeItemsAutoTotal()" in sum_now

    js = r"""
    const fs = require('fs');
    const src = fs.readFileSync('demo/static/js/review_core.js', 'utf8');
    const m = src.match(/function computeItemsAutoTotal\(\)[\s\S]*?\n\}/);
    if (!m) { console.error('computeItemsAutoTotal not found'); process.exit(1); }
    function mkRow(actual) {
      const v = { '.inp-actual-qty': actual, '.inp-price': '10', '.inp-amount': '10.00', '.inp-qty': '1' };
      return { dataset: {}, querySelector: sel => (sel in v ? { value: v[sel] } : null) };
    }
    let rows = [];
    global.document = { getElementById: id => id === 'itemTableBody' ? { querySelectorAll: () => rows } : (id === 'inpDocForm' ? { value: 'printed' } : null) };
    global.currentReceiptData = {};
    global.collectFeeMap = () => ({ discount_amount: 1, rounding_adjustment: 0.5, delivery_fee: 5,
                                    deposit_amount: 4, service_fee: 3, tax_amount: 2 });
    eval('var computeItemsAutoTotal; ' + m[0]);
    const out = {};
    for (const a of ['', '  ', '0', ' 0 ', '1']) { rows = [mkRow(a)]; out[JSON.stringify(a)] = computeItemsAutoTotal(); }
    console.log(JSON.stringify(out));
    const exp = { '""': 22.5, '"  "': 22.5, '"0"': 12.5, '" 0 "': 12.5, '"1"': 22.5 };
    for (const k in exp) if (Math.abs(out[k] - exp[k]) > 1e-9) { console.error('mismatch ' + k); process.exit(2); }
    """
    proc = subprocess.run(["node", "-e", js], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, "stdout=%s stderr=%s" % (proc.stdout, proc.stderr)

# -*- coding: utf-8 -*-
"""F1a 复核主流程回归（真实 Chromium，真实服务；识别结果用路由替身制造确定的失败/成功）：
S3-02 / S3-04 / S3-05 / S3-07 / S3-10 / S3-11 / S3-19 / S3-20 / S3-24 / S2-12 / S1-P1-03 / S1-P1-05 /
S1-P1-11 / SKU 候选 / UNIT_CONVERSION_MISSING / S3-17。
"""
import json
import os
import re

import pytest

from _f1a_support import site, browser, make_page, make_image  # noqa: F401

JSON_CT = "application/json"


def _img(site):
    return make_image(os.path.join(site.workdir, "ok.jpg"), "receipt")


def _fail_job(page, msg):
    page.route("**/api/job/*", lambda r: r.fulfill(
        status=200, content_type=JSON_CT,
        body=json.dumps({"job_id": "x", "job_status": "error", "receipt_id": None, "error_msg": msg})))


def _upload_and_fail(page, site, msg):
    """走真实上传（拿到真单据 id），把任务结果替换成指定失败文案。"""
    def job(route):
        r = route.fetch()
        d = r.json()
        d["job_status"] = "error"
        d["error_msg"] = msg
        route.fulfill(response=r, json=d)
    page.route("**/api/job/*", job)
    page.set_input_files("#receiptFile", _img(site))


def _toasts(page):
    return page.eval_on_selector_all(".app-toast .app-toast-msg", "els => els.map(e => e.innerText)")


# ---------------------------------------------------------------- S3-02 / S3-04 识别失败
@pytest.mark.parametrize("role", ["staff", "owner"])
def test_s3_04_not_configured_converts_once_without_admin_jargon(make_page, site, role):
    pg = make_page(role, 1440, 900)
    page = pg.open()
    _upload_and_fail(page, site, "安全阻断: 非法外部 API Base URL: URL 不能为空")
    page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000)
    page.wait_for_timeout(500)
    toasts = _toasts(page)
    assert len(toasts) == 1, toasts
    for bad in ("管理后台", "引擎配置", "密钥", "安全阻断", "API", "繁忙", "稍后重试"):
        assert bad not in toasts[0], (bad, toasts[0])
    assert "手工" in toasts[0] and "原图" in toasts[0]
    assert not page.is_visible("#errorCard")
    assert not page.is_visible("#qualityWarningsBanner")
    assert page.get_attribute("#previewImg", "src"), "原图丢了"
    assert not page.is_visible("#manualEntryNoImage")
    assert page.is_visible("#manualEntryFormBadge") and not page.is_visible("#aiPrefillNoticeBadge")
    assert not pg.errors, pg.errors
    pg.shot("F1a-S3-04-%s-1440-not-configured.png" % role)


def test_s3_04_admin_sees_where_to_check_but_staff_does_not(make_page, site):
    pg = make_page("admin", 1440, 900)
    page = pg.open()
    _upload_and_fail(page, site, "鉴权失败: Incorrect API key provided 401 Unauthorized")
    page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000)
    t = _toasts(page)
    assert len(t) == 1 and "引擎配置" in t[0], t


def test_s3_04_busy_stays_on_error_card_with_retry_and_manual(make_page, site):
    pg = make_page("staff", 390, 844)
    page = pg.open()
    _upload_and_fail(page, site, "AI 服务繁忙 429 rate limit timeout")
    page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)
    text = page.inner_text("#errorCard")
    assert "不是照片的问题" in text and "重试" in text and "转手工" in text
    assert "管理后台" not in text and "密钥" not in text
    assert page.is_visible("#btnConvertManual") and page.is_enabled("#btnConvertManual")
    assert page.is_visible("#btnRetryNormal")
    assert _toasts(page) == []
    assert not page.is_visible("#qualityWarningsBanner")
    page.click("#btnConvertManual")
    page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000)
    assert page.get_attribute("#previewImg", "src")
    assert len(_toasts(page)) == 1
    pg.shot("F1a-S3-04-staff-390-busy-then-manual.png")


def test_s3_04_not_configured_card_when_unknown_has_no_retry(make_page, site):
    """未开通类原因不给「重试」（重试必然失败），只给转手工。"""
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    page.evaluate("showErrorCard('AI 引擎未配置 not configured', null, 'ENGINE_NOT_CONFIGURED')")
    assert page.is_visible("#errorCard")
    assert not page.is_visible("#btnRetryNormal")
    assert page.is_visible("#btnConvertManual")
    assert "管理后台" not in page.inner_text("#errorCard")


def test_s3_02_convert_failure_keeps_button_and_image_and_one_message(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    page.route("**/api/receipt/*/convert_manual", lambda r: r.fulfill(
        status=500, content_type=JSON_CT,
        body=json.dumps({"status": "error", "code": "INTERNAL", "msg": "Internal Server Error"})))
    _upload_and_fail(page, site, "AI 服务繁忙 timeout")
    page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)
    src_before = page.get_attribute("#previewImg", "src")
    assert src_before
    page.click("#btnConvertManual")
    page.wait_for_timeout(800)
    assert page.is_visible("#errorCard")
    assert page.is_visible("#btnConvertManual") and page.is_enabled("#btnConvertManual")
    assert _toasts(page) == [], "失败时只该在卡片里说明一次，不再另弹提示"
    card = page.inner_text("#errorCard")
    assert "Internal" not in card and "原图已保留" in card
    assert page.get_attribute("#previewImg", "src") == src_before
    page.unroute("**/api/receipt/*/convert_manual")
    page.click("#btnConvertManual")
    page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000)


def test_s3_02_archive_convert_uses_in_app_dialog_and_works_for_staff(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open("tab-archive")
    native = []
    page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))
    posted = []
    page.route("**/api/receipt/777/convert_manual", lambda r: (posted.append(1), r.fulfill(
        status=200, content_type=JSON_CT, body=json.dumps({"status": "success", "data": {}}))))
    page.evaluate("convertReceiptFromArchive(777)")
    page.wait_for_selector(".ui-dlg, .custom-modal-overlay", timeout=3000)
    dlg = page.inner_text(".ui-dlg, .custom-modal-overlay")
    assert "原图会保留" in dlg
    page.click(".ui-dlg button:has-text('转手工录入'), .custom-modal-overlay button:has-text('转手工录入')")
    page.wait_for_timeout(600)
    assert posted and not native


# ---------------------------------------------------------------- S3-11 / S3-24 手工单与标题
def test_s3_11_manual_entry_badge_and_no_image_text(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    page.click("#btnNewManualEntry")
    page.wait_for_selector("#prefillFormCard:not(.hide)")
    assert page.is_visible("#manualEntryFormBadge")
    assert page.inner_text("#manualEntryFormBadge").strip() == "手工录入"
    assert not page.is_visible("#aiPrefillNoticeBadge")
    assert "无原图（手工录入）" in page.inner_text("#manualEntryNoImage")
    page.evaluate("showToast('x')")
    assert "AI 预填" not in page.inner_text("#prefillFormCard .card-title")
    assert "AI 已完成解析" not in "".join(_toasts(page))


def test_s3_11_archive_detail_manual_receipt_shows_placeholder_not_broken_image(make_page, site):
    rid = site.make_receipt(doc_form="manual_entry")
    pg = make_page("owner", 1440, 900)
    page = pg.open("tab-archive")
    page.evaluate("id => loadReceiptDetail(id)", rid)
    page.wait_for_selector("#archiveDetailModal:not(.hide)", timeout=8000)
    assert page.is_visible("#archiveNoImage")
    assert "无原图（手工录入）" in page.inner_text("#archiveNoImage")
    assert not page.is_visible("#archivePreviewImg")
    pg.shot("F1a-S3-11-owner-1440-archive-manual.png")


def test_s3_24_title_has_single_badge_discard_is_text_button_payment_is_switch(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    page.click("#btnNewManualEntry")
    page.wait_for_selector("#prefillFormCard:not(.hide)")
    visible_badges = page.eval_on_selector_all(
        "#prefillFormCard .review-title-line .badge",
        "els => els.filter(e => e.offsetParent !== null).map(e => e.textContent.trim())")
    assert len(visible_badges) == 1, visible_badges
    assert page.evaluate("document.querySelector('#prefillFormCard .card-title').innerText").count("未保存") == 0
    discard = page.locator("#btnDiscardReceipt")
    assert "btn-text-danger" in discard.get_attribute("class")
    assert "btn-danger" not in discard.get_attribute("class").split()
    d_box = discard.bounding_box()
    others = [page.locator(s).bounding_box() for s in ("#btnAddRow", "#btnSaveReview", "#btnSaveAndNext")]
    assert all(d_box["x"] <= o["x"] for o in others if o), (d_box, others)
    sw = page.locator("#inpPaymentMark")
    assert sw.get_attribute("role") == "switch"
    assert sw.get_attribute("aria-checked") == "false"
    sw.click()
    assert sw.get_attribute("aria-checked") == "true"
    assert page.evaluate("getComputedStyle(document.querySelector('#inpPaymentMark')).backgroundColor") in (
        "rgba(0, 0, 0, 0)", "transparent")
    pg.shot("F1a-S3-24-staff-1440-review-header.png")


# ---------------------------------------------------------------- S3-05 / S3-19 门禁
def _fill_manual(page, supplier="测试菜行", qty="2", price="10", amount=None):
    page.click("#btnNewManualEntry")
    page.wait_for_selector("#prefillFormCard:not(.hide)")
    page.fill("#inpSupplier", supplier)
    page.fill("#inpDate", "2026-10-05")
    row = page.locator("#itemTableBody tr").first
    row.locator(".inp-name").fill("菜心")
    row.locator(".inp-qty").fill(qty)
    row.locator(".inp-qty").dispatch_event("input")
    row.locator(".inp-unit").fill("斤")
    row.locator(".inp-price").fill(price)
    row.locator(".inp-price").dispatch_event("input")
    if amount is not None:
        row.locator(".inp-amount").fill(amount)
        row.locator(".inp-amount").dispatch_event("input")


def test_s3_19_manual_total_defaults_to_items_sum_and_override_shows_difference(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    _fill_manual(page)
    assert page.input_value("#inpTotal") == "20.00"
    page.fill("#inpTotal", "25")
    page.wait_for_timeout(200)
    hint = page.inner_text("#totalOverrideHint")
    assert "相差 5.00 元" in hint and "20.00" in hint
    page.locator("#itemTableBody tr").first.locator(".inp-qty").fill("3")
    page.locator("#itemTableBody tr").first.locator(".inp-qty").dispatch_event("input")
    assert page.input_value("#inpTotal") == "25", "手改过的总额不能被明细合计悄悄盖掉"
    assert "相差 5.00 元" in page.inner_text("#totalOverrideHint") or "相差" in page.inner_text("#totalOverrideHint")
    page.click("#totalOverrideHint .link-btn")
    assert page.input_value("#inpTotal") == "30.00"
    assert not page.is_visible("#totalOverrideHint")


def test_s3_05_blocking_issue_really_blocks_save_and_jumps_to_field(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    saves = []
    page.on("request", lambda r: saves.append(r.url) if r.method == "POST" and "/api/save_edited" in r.url else None)
    _fill_manual(page)
    page.select_option("#inpSettlementType", "cash")
    page.fill("#inpTotal", "60")
    assert "必须修改" in page.inner_text("#issueBlockerCountBadge")
    page.click("#btnSaveReview")
    page.wait_for_timeout(600)
    assert not saves, "界面写着「必须修改」，保存却发出去了"
    t = " ".join(_toasts(page))
    assert "必须先改" in t and "保存并提交审核" in t
    assert page.evaluate("document.activeElement.id") == "inpTotal"
    page.fill("#inpTotal", "20")
    page.click("#btnSaveReview")
    page.wait_for_function("document.querySelector('#reviewSaveStatusBadge').textContent.includes('待老板审批')", timeout=8000)
    assert saves


def test_s3_05_manual_entry_must_choose_settlement(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    saves = []
    page.on("request", lambda r: saves.append(r.url) if r.method == "POST" and "/api/save_edited" in r.url else None)
    _fill_manual(page)
    assert page.input_value("#inpSettlementType") == ""
    page.click("#btnSaveReview")
    page.wait_for_timeout(500)
    assert not saves
    assert "结算方式" in " ".join(_toasts(page))


def test_s3_19_settlement_preselected_from_supplier_habit(make_page, site):
    code, ret = site.api("POST", "/api/suppliers", {"name": "挂账菜行", "settlement_pref": "credit"}, role="owner")
    assert code == 200, (code, ret)
    pg = make_page("owner", 1440, 900)
    page = pg.open()
    page.click("#btnNewManualEntry")
    page.wait_for_selector("#prefillFormCard:not(.hide)")
    page.fill("#inpSupplier", "挂账菜行")
    page.evaluate("availableSuppliers.length || fetch('/api/suppliers', {headers: buildAuthHeaders('/api/suppliers', null) || {}}).then(r => r.json()).then(j => { availableSuppliers = j.data; })")
    page.wait_for_timeout(500)
    page.evaluate("document.getElementById('inpSupplier').dispatchEvent(new Event('change', {bubbles: true}))")
    page.wait_for_timeout(200)
    assert page.input_value("#inpSettlementType") == "credit"


# ---------------------------------------------------------------- S3-20 / S1-P1-05 审批
def _open_in_workbench(page, rid):
    page.evaluate("id => openReceiptInWorkbench(id, true, true)", rid)
    page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=8000)
    page.wait_for_timeout(600)


def test_s3_20_approve_with_blocker_jumps_to_field_without_page_error(make_page, site):
    rid = site.make_receipt(total=50.0, items=[{"name": "菜心", "quantity": 2, "unit": "斤", "unit_price": 10, "amount": 20}])
    pg = make_page("owner", 1440, 900)
    page = pg.open()
    _open_in_workbench(page, rid)
    page.click("#btnApproveReceipt")
    page.wait_for_timeout(700)
    assert "必须先改" in " ".join(_toasts(page))
    assert page.evaluate("document.activeElement.id") == "inpTotal"
    assert not pg.errors, pg.errors


def test_s1_p1_05_approve_on_unreviewed_parsed_receipt_explains(make_page, site):
    rid = site.make_receipt()
    pg = make_page("owner", 1440, 900)
    page = pg.open()
    def detail(route):
        r = route.fetch()
        d = r.json()
        d["data"]["status"] = "parsed"
        route.fulfill(response=r, json=d)
    page.route(re.compile(r".*/api/receipt/%d(\?.*)?$" % rid), detail)
    _open_in_workbench(page, rid)
    approves = []
    page.on("request", lambda r: approves.append(1) if r.method == "POST" and r.url.endswith("/approve") else None)
    page.click("#btnApproveReceipt")
    page.wait_for_timeout(500)
    t = " ".join(_toasts(page))
    assert "还没有人核对过" in t and "保存并提交审核" in t and "审核入账" in t
    assert not approves


def test_s3_10_prompt_names_match_real_button_names(make_page, site):
    rid = site.make_receipt()
    pg = make_page("owner", 1440, 900)
    page = pg.open()
    _open_in_workbench(page, rid)
    save_label = page.inner_text("#btnSaveReview").strip()
    approve_label = page.inner_text("#btnApproveReceipt").strip()
    page.locator("#itemTableBody tr").first.locator(".inp-qty").fill("5")
    page.locator("#itemTableBody tr").first.locator(".inp-qty").dispatch_event("input")
    page.evaluate("markReviewDirty()")
    page.click("#btnApproveReceipt")
    page.wait_for_timeout(400)
    t = " ".join(_toasts(page))
    assert "「%s」" % save_label in t and "「%s」" % approve_label in t, (save_label, approve_label, t)
    assert "保存复核修改" not in t
    assert page.inner_text("#btnExecuteApproveConfirm", timeout=2000).strip() == approve_label


def test_s1_unit_conversion_missing_dialog_owner_jumps_to_settings_prefilled(make_page, site):
    rid = site.make_receipt()
    pg = make_page("owner", 1440, 900)
    page = pg.open()
    page.route("**/api/receipt/*/approve", lambda r: r.fulfill(
        status=422, content_type=JSON_CT,
        body=json.dumps({"status": "error", "code": "UNIT_CONVERSION_MISSING",
                         "msg": "「箱」和库存单位「斤」之间还没有换算关系，请老板到「设置」里添加换算，或把这一行的单位改成「斤」",
                         "data": {"pairs": [{"from_unit": "箱", "to_unit": "斤", "item_name": "菜心"}]}})))
    _open_in_workbench(page, rid)
    page.click("#btnApproveReceipt")
    page.wait_for_selector("#btnExecuteApproveConfirm", state="visible", timeout=4000)
    page.click("#btnExecuteApproveConfirm")
    page.wait_for_selector(".ui-dlg", timeout=4000)
    text = page.inner_text(".ui-dlg")
    assert "菜心" in text and "箱" in text and "斤" in text
    assert "UNIT_CONVERSION_MISSING" not in text
    assert not page.is_visible("#modalApproveConfirmDialog")
    pg.shot("F1a-UNIT-owner-1440-dialog.png")
    page.click(".ui-dlg button:has-text('去设置添加换算')")
    page.wait_for_selector("#tab-settings.active", timeout=4000)
    page.wait_for_selector("#stConvFrom", timeout=4000)
    page.wait_for_function("document.getElementById('stConvFrom').value === '箱'", timeout=4000)
    assert page.input_value("#stConvTo") == "斤"


def test_s1_unit_conversion_missing_dialog_staff_asks_owner(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    page.evaluate("""handleApproveError(422, {code: 'UNIT_CONVERSION_MISSING', msg: 'x',
        data: {pairs: [{from_unit: '箱', to_unit: '斤', item_name: '菜心'}]}}, {})""")
    page.wait_for_selector(".ui-dlg", timeout=3000)
    text = page.inner_text(".ui-dlg")
    assert "请老板先到「设置」里添加换算" in text
    assert "去设置" not in text


# ---------------------------------------------------------------- S2-12 门禁人话
def test_s2_12_gate_text_is_plain_language(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    samples = [
        "items.0.unit_price: 填写的内容格式不对 a valid number, unable to parse string as a number",
        "doc_form: Input should be 'printed_delivery_note', 'ncr_handwritten'",
        "第1行 菜心: 数量1.35×单价38.5=51.98，但小计=52.0，相差 -0.02",
        "明细合计100.0  应为 100.0，但单据写着90.0，相差 10.0",
        "安全阻断: Canary 令牌缺失",
        "算术门禁: 明细合计=100.0，预期总额=100.0，但总额=90.0（差-10.0）",
        "some weird english only message",
    ]
    out = page.evaluate("s => s.map(humanizeGateMsg)", samples)
    for o in out:
        for bad in ("items.", "a valid number", "安全阻断", "Canary", "unit_price", "doc_form", "Input should", "-0.0", "-10.0"):
            assert bad not in o, (bad, o)
        assert not re.search(r"[A-Za-z]{4,}", o), o
    assert "第 1 行 菜心：单据写 52 元，按数量单价应是 51.98 元，相差 0.02 元" == out[2]
    assert "相差 10 元" in out[3]


def test_s2_12_structured_gate_issues_render_template(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    got = page.evaluate("""TERMS.formatGateIssues([
        {code: 'LINE_AMOUNT_MISMATCH', row_index: 0, item_name: '菜心', field: 'amount', expected: 51.98, actual: 52, diff: 0.02, hint: 'x'},
        {code: 'TOTAL_MISMATCH', expected: 100, actual: 90, diff: -10, hint: 'y'},
        {code: 'CONTRACT_FIELD', row_index: 2, field: 'items.2.unit_price', hint: 'z'}])""")
    assert got[0] == "第 1 行 菜心：单据写 52 元，按数量单价应是 51.98 元，相差 0.02 元"
    assert got[1] == "明细加起来是 100 元，但整单总额写的是 90 元，相差 10 元"
    assert "第 3 行" in got[2] and "单价" in got[2] and "没读清" in got[2]


def test_s2_12_review_form_uses_gate_issues_over_legacy_warnings(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    page.evaluate("""renderEditForm({supplier_name: '甲', date: '2026-10-05', total_amount: 52, doc_form: 'printed_delivery_note',
        items: [{name: '菜心', quantity: 1.35, unit: '斤', unit_price: 38.5, amount: 52}],
        math_warnings: ['第1行 菜心: 数量1.35×单价38.5=51.98，但小计=52.0，相差 -0.02'],
        gate_issues: [{code: 'LINE_AMOUNT_MISMATCH', row_index: 0, item_name: '菜心', expected: 51.98, actual: 52, diff: 0.02}]})""")
    page.wait_for_timeout(300)
    t = " ".join(_toasts(page)) + page.inner_text("#alertBanner")
    assert "单据写 52 元，按数量单价应是 51.98 元，相差 0.02 元" in t


# ---------------------------------------------------------------- S1-P2-02 错误码
def test_s1_p2_02_error_codes_map_to_plain_language(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    cases = [
        [409, {"code": "VERSION_CONFLICT", "msg": "version conflict"}],
        [409, {"code": "INVALID_STATUS", "msg": "status='flagged' not allowed"}],
        [400, {"code": "VERSION_REQUIRED", "msg": "GET /api/receipt/{id} to refresh"}],
        [401, {"code": "UNAUTHORIZED", "msg": "Bearer token missing; X-Role header"}],
        [500, {"code": "SOMETHING_NEW", "msg": "magic bytes ... Truncated File Read"}],
        [400, {"msg": "Truncated File Read"}],
        [403, {"detail": "权限不足：当前角色为店员（staff），此操作需老板（owner）"}],
    ]
    out = page.evaluate("cs => cs.map(c => humanizeBackendError(c[0], c[1]))", cases)
    for o in out:
        assert o and not re.search(r"[A-Za-z]{3,}", o), o
        for bad in ("X-Role", "Bearer", "status=", "/api/", "magic bytes", "staff", "owner"):
            assert bad not in o
    assert page.evaluate("TERMS.humanizeCode('APPROVAL_BLOCKED', '缺少供应商名称，无法入账。')") == "缺少供应商名称，无法入账。"
    assert "没有成功" in page.evaluate("TERMS.humanizeCode('NEW_CODE', 'plain english')")


# ---------------------------------------------------------------- S3-07 / S1-P1-11 / S1-P1-03
def test_s3_07_flag_uses_in_app_dialog_with_reason(make_page, site):
    rid = site.make_receipt()
    pg = make_page("owner", 1440, 900)
    page = pg.open("tab-archive")
    native = []
    page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))
    body = {}
    page.route("**/api/receipt/%d/flag" % rid, lambda r: (body.update(json.loads(r.request.post_data or "{}")), r.fulfill(
        status=200, content_type=JSON_CT, body=json.dumps({"status": "success"}))))
    page.evaluate("id => { flagReceipt(id); }", rid)
    page.wait_for_selector(".ui-dlg", timeout=3000)
    page.fill(".ui-dlg input", "单价明显不对")
    page.click(".ui-dlg button:has-text('标记有问题')")
    page.wait_for_timeout(600)
    assert body.get("reason") == "单价明显不对"
    assert not native


def test_s3_07_discard_uses_in_app_dialog_and_owner_gets_undo(make_page, site):
    pg = make_page("owner", 1440, 900)
    page = pg.open()
    native = []
    page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))
    calls = []
    page.route("**/api/receipt/*/discard", lambda r: (calls.append("discard"), r.fulfill(
        status=200, content_type=JSON_CT, body=json.dumps({"status": "success"}))))
    page.route("**/api/trash/receipts/*/restore", lambda r: (calls.append("restore"), r.fulfill(
        status=200, content_type=JSON_CT, body=json.dumps({"status": "success", "receipt_id": 1}))))
    page.click("#btnNewManualEntry")
    page.wait_for_selector("#prefillFormCard:not(.hide)")
    page.evaluate("currentReceiptId = 4242")
    page.click("#btnDiscardReceipt")
    page.wait_for_selector(".ui-dlg", timeout=3000)
    dlg = page.inner_text(".ui-dlg")
    assert "撤销" in dlg and "管理后台" not in dlg
    page.click(".ui-dlg button:has-text('放弃这张单')")
    page.wait_for_selector(".app-toast-action", timeout=4000)
    assert page.inner_text(".app-toast-action").strip() == "撤销"
    page.click(".app-toast-action")
    page.wait_for_timeout(600)
    assert calls == ["discard", "restore"], calls
    assert not native


def test_s3_07_staff_discard_does_not_promise_undo(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    page.route("**/api/receipt/*/discard", lambda r: r.fulfill(
        status=200, content_type=JSON_CT, body=json.dumps({"status": "success"})))
    page.click("#btnNewManualEntry")
    page.wait_for_selector("#prefillFormCard:not(.hide)")
    page.evaluate("currentReceiptId = 4243")
    page.click("#btnDiscardReceipt")
    page.wait_for_selector(".ui-dlg", timeout=3000)
    dlg = page.inner_text(".ui-dlg")
    assert "没法自己找回" in dlg and "撤销" not in dlg.replace("没法自己找回", "")
    page.click(".ui-dlg button:has-text('放弃这张单')")
    page.wait_for_timeout(600)
    assert page.locator(".app-toast-action").count() == 0


def test_s1_p1_11_owner_sees_reverse_button_on_approved_and_posts_payload(make_page, site):
    rid = site.make_receipt()
    pg = make_page("owner", 1440, 900)
    page = pg.open("tab-archive")

    def detail(route):
        r = route.fetch()
        d = r.json()
        d["data"]["status"] = "approved"
        d["data"]["version"] = 7
        route.fulfill(response=r, json=d)
    page.route(re.compile(r".*/api/receipt/%d(\?.*)?$" % rid), detail)
    captured = {}
    page.route("**/api/receipt/%d/reverse" % rid, lambda r: (captured.update(json.loads(r.request.post_data)), r.fulfill(
        status=200, content_type=JSON_CT, body=json.dumps({"status": "success", "version": 8}))))
    page.evaluate("id => loadReceiptDetail(id)", rid)
    page.wait_for_selector("#archiveDetailModal:not(.hide)", timeout=8000)
    assert page.is_visible("#modalReverseBtn")
    assert not page.is_visible("#modalApproveBtn")
    pg.shot("F1a-S1-P1-11-owner-1440-reverse-button.png")
    page.click("#modalReverseBtn")
    page.wait_for_selector(".ui-dlg", timeout=3000)
    assert "冲销" in page.inner_text(".ui-dlg")
    page.click(".ui-dlg button:has-text('继续冲销')")
    page.wait_for_selector(".ui-dlg input", timeout=3000)
    page.click(".ui-dlg button:has-text('确认冲销')")
    assert page.inner_text(".ui-dlg .ui-dlg-err").strip() != ""
    page.fill(".ui-dlg input", "单价抄错了")
    page.click(".ui-dlg button:has-text('确认冲销')")
    page.wait_for_timeout(700)
    assert captured == {"version": 7, "reason": "单价抄错了"}, captured


def test_s1_p1_11_reverse_hidden_for_staff_and_for_unposted(make_page, site):
    rid = site.make_receipt()
    staff = make_page("staff", 1440, 900)
    page = staff.open("tab-archive")
    page.evaluate("id => loadReceiptDetail(id)", rid)
    page.wait_for_selector("#archiveDetailModal:not(.hide)", timeout=8000)
    assert not page.is_visible("#modalReverseBtn")
    owner = make_page("owner", 1440, 900)
    page2 = owner.open("tab-archive")
    page2.evaluate("id => loadReceiptDetail(id)", rid)
    page2.wait_for_selector("#archiveDetailModal:not(.hide)", timeout=8000)
    assert not page2.is_visible("#modalReverseBtn")
    assert page2.is_visible("#modalApproveBtn")


def test_s1_p1_03_flagged_receipt_staff_cannot_save_and_sees_why(make_page, site):
    rid = site.make_receipt()
    pg = make_page("staff", 1440, 900)
    page = pg.open()

    def detail(route):
        r = route.fetch()
        d = r.json()
        d["data"]["status"] = "flagged"
        route.fulfill(response=r, json=d)
    page.route(re.compile(r".*/api/receipt/%d(\?.*)?$" % rid), detail)
    page.evaluate("id => openReceiptInWorkbench(id, true, true)", rid)
    page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=8000)
    page.wait_for_timeout(500)
    assert page.is_disabled("#btnSaveReview") and page.is_disabled("#btnSaveAndNext")
    banner = page.inner_text("#flaggedLockBanner")
    assert "老板标记了有问题" in banner and "告诉老板" in banner
    pg.shot("F1a-S1-P1-03-staff-1440-flagged-lock.png")
    owner = make_page("owner", 1440, 900)
    page2 = owner.open()
    page2.route(re.compile(r".*/api/receipt/%d(\?.*)?$" % rid), detail)
    page2.evaluate("id => openReceiptInWorkbench(id, true, true)", rid)
    page2.wait_for_selector("#prefillFormCard:not(.hide)", timeout=8000)
    page2.wait_for_timeout(500)
    assert not page2.is_disabled("#btnSaveReview")


# ---------------------------------------------------------------- SKU 候选
def test_sku_candidates_confirm_and_none_of_them(make_page, site):
    rid = site.make_receipt(items=[
        {"name": "牛肉丸", "quantity": 1, "unit": "包", "unit_price": 10, "amount": 10},
        {"name": "鸡翼", "quantity": 1, "unit": "斤", "unit_price": 20, "amount": 20}])
    pg = make_page("owner", 1440, 900)
    page = pg.open()

    def detail(route):
        r = route.fetch()
        d = r.json()
        its = d["data"]["items"]
        its[0]["sku_id"] = None
        its[0]["sku_candidates"] = [{"sku_id": 3, "name": "牛肉", "unit": "斤", "reason": "名字相近", "unit_compatible": False}]
        its[1]["sku_id"] = None
        its[1]["sku_candidates"] = [{"sku_id": 9, "name": "鸡", "unit": "斤", "reason": "名字相近", "unit_compatible": True}]
        route.fulfill(response=r, json=d)
    page.route(re.compile(r".*/api/receipt/%d(\?.*)?$" % rid), detail)
    _open_in_workbench(page, rid)
    boxes = page.locator("#itemTableBody .sku-candidate-box:not(.hide)")
    assert boxes.count() == 2
    t0 = boxes.nth(0).inner_text()
    assert "可能是：牛肉" in t0 and "确认就是它" in t0 and "都不是，新建" in t0 and "没有换算关系" in t0
    boxes.nth(0).locator(".sku-cand-yes").click()
    boxes.nth(0).wait_for(state="visible")
    page.locator("#itemTableBody .sku-candidate-box").nth(1).locator(".sku-cand-no").click()
    sent = {}
    page.route("**/api/save_edited", lambda r: (sent.update(json.loads(r.request.post_data)), r.fulfill(
        status=200, content_type=JSON_CT, body=json.dumps({"status": "success", "receipt_id": rid, "version": 5}))))
    page.click("#btnSaveReview")
    page.wait_for_timeout(800)
    assert sent["items"][0].get("sku_id") == 3
    assert not sent["items"][1].get("sku_id")
    pg.shot("F1a-SKU-owner-1440-candidates.png")


# ---------------------------------------------------------------- 术语 S3-10 / 徽章 / logo
def test_s3_10_no_bracket_prefixes_or_english_status_or_mixed_credit_terms(make_page, site):
    for role in ("staff", "owner"):
        pg = make_page(role, 1440, 900)
        page = pg.open()
        text = page.evaluate("document.body.innerText")
        for bad in ("[DEMO MODE]", "[系统提示]", "[警告]", "[标记]", "[入账提示]", "确认入账并增加库存", "赊单", "赊账"):
            assert bad not in text, (role, bad)
        html = page.content()
        assert "确认入账并增加库存" not in html and "[入账提示]" not in html and "[无法入账]" not in html
    labels = page.evaluate("Object.values(RECEIPT_STATUSES).map(s => s.label + '|' + s.filter + '|' + s.title)")
    for l in labels:
        assert not re.search(r"[A-Za-z]{3,}", l), l
    assert page.evaluate("RECEIPT_STATUSES.edited.label") == page.evaluate("TERMS.status.edited.label") == "待老板审批"
    opts = page.eval_on_selector_all("#inpSettlementType option", "els => els.map(e => e.textContent)")
    assert any("挂账" in o for o in opts) and not any("月结" in o or "赊" in o for o in opts)


def test_s3_17_logo_is_small_file_and_referenced(make_page, site):
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    src = page.get_attribute(".brand-logo-img", "src")
    assert "logo_icon_64" in src
    size = page.evaluate("src => fetch(src).then(r => r.blob()).then(b => b.size)", src)
    assert size < 20000, size

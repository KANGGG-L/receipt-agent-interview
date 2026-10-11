# -*- coding: utf-8 -*-
"""F1b：归档对账（子 Tab、付款、对账、应付口径、回收站、供应商合并、付款提醒横幅）真实浏览器回归。"""

import datetime

import pytest

import _f1b_pw as h

sync_playwright = h.playwright_api.sync_playwright
TODAY = datetime.date.today()


def _d(days):
    return (TODAY + datetime.timedelta(days=days)).isoformat()


@pytest.fixture(scope="module")
def server():
    handle, data_dir = h.start_server()
    try:
        yield handle
    finally:
        h.stop_server(handle, data_dir)


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        yield p


def _page_errors(page):
    return [e for e in page.errors if e.startswith("pageerror")]


def _pane_visible(page, pane):
    return page.evaluate(
        "p => { const el = document.querySelector('#tab-archive [data-arc-pane=\"' + p + '\"]'); "
        "return !!el && el.offsetParent !== null; }", pane)


def test_archive_split_into_three_subtabs(server, pw):
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-archive")
        tabs = page.evaluate("() => Array.from(document.querySelectorAll('#arcSubTabs .arc-subtab')).map(b => b.textContent.trim())")
        assert [t[:2] for t in tabs] == ["单据", "供应", "应付"], tabs
        assert _pane_visible(page, "docs") and not _pane_visible(page, "suppliers") and not _pane_visible(page, "payables")
        page.click("#arcTab-suppliers")
        assert _pane_visible(page, "suppliers") and not _pane_visible(page, "docs")
        page.click("#arcTab-payables")
        assert _pane_visible(page, "payables") and not _pane_visible(page, "suppliers")
        heights = page.evaluate("() => Array.from(document.querySelectorAll('#arcSubTabs button')).map(b => b.getBoundingClientRect().height)")
        assert min(heights) >= 40
        h.shot(page, "archive-subtabs-desk")
        assert not _page_errors(page)
    finally:
        h.close(page)


def test_overdue_banner_red_dot_and_jump(server, pw):
    h.seed_receipt(supplier="逾期测试供应商", date=_d(-40), pay_date=_d(-10),
                   items=[{"name": "逾期菜心", "quantity": 10, "unit": "斤", "unit_price": 5, "amount": 50}])
    h.seed_receipt(supplier="逾期测试供应商", date=_d(-3), pay_date=_d(3),
                   items=[{"name": "快到期鸡蛋", "quantity": 3, "unit": "斤", "unit_price": 10, "amount": 30}])
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page)
        banner = page.locator("#payReminderBanner")
        banner.wait_for(state="visible")
        text = banner.inner_text()
        assert "1 张挂账单已逾期" in text and "1 张 7 天内到期" in text, text
        dot = page.locator("#navPayRedDot")
        assert dot.is_visible() and dot.inner_text() == "2"
        h.shot(page, "overdue-banner-desk")
        banner.click()
        page.wait_for_function("() => document.getElementById('tab-archive').classList.contains('active')")
        assert _pane_visible(page, "payables")
        rows = page.locator("#payablesBody tr")
        assert rows.count() == 2
        assert "逾期" in page.locator("#payablesBody").inner_text()
        assert "天到期" in page.locator("#payablesBody").inner_text()
        assert "2" in page.locator("#arcPayCount").inner_text()
        h.shot(page, "archive-payables-desk")
        assert not _page_errors(page)
    finally:
        h.close(page)

    mobile = h.new_page(pw, role="owner", viewport=h.MOBILE)
    try:
        h.open_app(mobile)
        mobile.locator("#payReminderBanner").wait_for(state="visible")
        box = mobile.locator("#payReminderBanner").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 391
        assert mobile.evaluate("() => document.documentElement.scrollWidth") <= 391
        h.shot(mobile, "overdue-banner-mobile")
    finally:
        h.close(mobile)


def test_supplier_table_and_payables_use_same_basis(server, pw):
    # 一张还没审批的挂账单：两处都不应计入；供应商表与应付清单的未付金额一致
    h.seed_receipt(supplier="口径供应商", date=_d(-5), approve=True, pay_date=_d(20),
                   items=[{"name": "口径菜", "quantity": 2, "unit": "斤", "unit_price": 20, "amount": 40}])
    h.seed_receipt(supplier="口径供应商", date=_d(-4), approve=False,
                   items=[{"name": "口径肉", "quantity": 1, "unit": "斤", "unit_price": 99, "amount": 99}])
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-archive")
        page.click("#arcTab-suppliers")
        row = page.locator("#supplierAdminBody tr", has_text="口径供应商")
        row.wait_for()
        assert "已入账未付挂账单" in page.inner_text("#supplierCard")
        assert "$40.00" in row.inner_text() and "1 张" in row.inner_text()
        page.click("#arcTab-payables")
        page.select_option("#finSupplier", label=page.locator("#finSupplier option", has_text="口径供应商").inner_text())
        page.wait_for_timeout(500)
        summary = page.inner_text("#financeSummary")
        assert "已入账未付总额" in summary and "$40.00" in summary and "（1 张）" in summary
        assert "只算已审批入账" in summary and "另有 1 张挂账单还没审批" in summary
        body = page.inner_text("#payablesBody")
        assert "99.00" not in body
    finally:
        h.close(page)


def test_register_payment_only_posted_unpaid_partial_and_status_labels(server, pw):
    r1 = h.seed_receipt(supplier="付款测试供应商", date=_d(-9), pay_date=_d(30),
                        items=[{"name": "付款菜", "quantity": 10, "unit": "斤", "unit_price": 5, "amount": 50}])
    r2 = h.seed_receipt(supplier="付款测试供应商", date=_d(-8), pay_date=_d(30),
                        items=[{"name": "付款鸡", "quantity": 3, "unit": "斤", "unit_price": 10, "amount": 30}])
    r_unposted = h.seed_receipt(supplier="付款测试供应商", date=_d(-7), approve=False,
                                items=[{"name": "付款未入账", "quantity": 1, "unit": "斤", "unit_price": 77, "amount": 77}])
    r_cash = h.seed_receipt(supplier="付款测试供应商", date=_d(-6), settlement="cash",
                            items=[{"name": "付款现金", "quantity": 1, "unit": "斤", "unit_price": 66, "amount": 66}])
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-archive")
        page.click("#arcTab-payables")
        label = page.locator("#finSupplier option", has_text="付款测试供应商").inner_text()
        page.select_option("#finSupplier", label=label)
        page.wait_for_timeout(500)
        page.click("#financeActions .btn-primary")
        page.wait_for_selector("#payReceiptBody .pay-amt-input")
        rows = page.locator("#payReceiptBody tr")
        assert rows.count() == 2, page.inner_text("#payReceiptBody")
        modal_text = page.inner_text("#paymentModal")
        assert "#%d" % r_unposted not in modal_text and "#%d" % r_cash not in modal_text
        assert "edited" not in modal_text and "approved" not in modal_text
        assert "已入账" in modal_text
        assert page.input_value("#payAmount") == "80.00"
        h.shot(page, "payment-modal-desk")

        # 校验：超过待付、空金额、未来日期
        first = page.locator("#payReceiptBody tr").first.locator(".pay-amt-input")
        first.fill("999")
        assert "不能超过" in page.inner_text("#payError")
        first.fill("")
        assert "大于 0" in page.inner_text("#payError")
        first.fill("20")
        page.locator("#payReceiptBody tr").nth(1).locator(".pay-receipt-check").uncheck()
        assert page.input_value("#payAmount") == "20.00"
        page.fill("#payDate", _d(5))
        page.click("#paymentModal .btn-primary")
        assert "不能晚于今天" in page.inner_text("#payError")
        page.fill("#payDate", _d(0))
        page.click("#paymentModal .btn-primary")
        page.wait_for_function("() => document.getElementById('paymentModal').classList.contains('hide')")
        st, body = h.api("GET", "/api/receipts")
        paid = {r["id"]: r for r in body["data"]}
        partial = [paid[i] for i in (r1, r2) if paid[i]["payment_status"] == "partial"]
        assert len(partial) == 1 and abs(partial[0]["paid_amount"] - 20) < 1e-6, [paid[i] for i in (r1, r2)]
        untouched = [paid[i] for i in (r1, r2) if paid[i]["payment_status"] != "partial"]
        assert untouched[0]["paid_amount"] == 0

        # 再开：已付/待付/状态
        page.click("#financeActions .btn-primary")
        page.wait_for_selector("#payReceiptBody .pay-amt-input")
        txt = page.inner_text("#payReceiptBody")
        assert "部分已付" in txt and "$20.00" in txt
        h.shot(page, "payment-modal-partial-desk")
        page.click("#paymentModal .btn-secondary")
        # 应付清单徽章
        assert "部分已付" in page.inner_text("#payablesBody")
        assert not _page_errors(page)
    finally:
        h.close(page)


def test_register_payment_modal_mobile(server, pw):
    page = h.new_page(pw, role="owner", viewport=h.MOBILE)
    try:
        h.open_app(page, "tab-archive")
        page.click("#arcTab-payables")
        label = page.locator("#finSupplier option", has_text="付款测试供应商").inner_text()
        page.select_option("#finSupplier", label=label)
        page.wait_for_timeout(500)
        page.click("#financeActions .btn-primary")
        page.wait_for_selector("#payReceiptBody .pay-amt-input")
        h.shot(page, "payment-modal-mobile")
        box = page.locator("#paymentModal .modal-card").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 391
    finally:
        h.close(page)


def test_reconciliation_flow_create_list_view_resolve_view(server, pw):
    h.seed_receipt(supplier="对账供应商", date=_d(-2), pay_date=_d(30),
                   items=[{"name": "对账菜", "quantity": 4, "unit": "斤", "unit_price": 10, "amount": 40}])
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-archive")
        page.click("#arcTab-payables")
        label = page.locator("#finSupplier option", has_text="对账供应商").inner_text()
        page.select_option("#finSupplier", label=label)
        page.wait_for_timeout(500)
        page.click("#financeActions .btn-secondary")
        page.wait_for_selector("#reconCreateModal:not(.hide)")
        page.click("#reconCreateModal .btn-primary")
        page.wait_for_selector("#reconTaskBody tr")
        first = page.locator("#reconTaskBody tr").first
        txt = first.inner_text()
        assert "NaN" not in txt and "undefined" not in txt
        assert first.locator("td").first.inner_text().startswith("#")
        # 对账页展示 period、已对上含义
        assert "~" in txt or "没限定期间" in txt
        assert "已对上" in page.inner_text("#reconTaskArea")
        first.locator("button").click()
        page.wait_for_selector("#reconLineBody tr")
        assert "加载对账详情失败" not in page.inner_text("body")
        note = page.inner_text("#reconDiffNote")
        assert "行" in note
        h.shot(page, "recon-detail-desk")
        # 处理差异：不用管 -> 已处理
        page.locator("#reconLineBody tr").first.locator("button", has_text="这笔不用管").click()
        page.wait_for_selector("#reconResolveModal:not(.hide)")
        page.fill("#reconResolveNote", "测试核销")
        page.click("#reconResolveSubmit")
        page.wait_for_function("() => document.getElementById('reconResolveModal').classList.contains('hide')")
        page.wait_for_function("() => document.getElementById('reconLineBody').textContent.includes('已处理')")
        # 确认对账完成：应用内确认框
        page.click("#btnConfirmRecon")
        page.wait_for_selector(".ui-dlg")
        assert "锁定" in page.inner_text(".ui-dlg")
        page.click(".ui-dlg-btn.primary")
        page.wait_for_function("() => document.getElementById('reconLineBody').textContent.includes('已锁定')")
        assert not _page_errors(page)
    finally:
        h.close(page)


def test_trash_list_restore_and_purge(server, pw):
    rid = h.seed_receipt(supplier="回收站供应商", date=_d(-1), approve=False,
                         items=[{"name": "回收站菜", "quantity": 1, "unit": "斤", "unit_price": 8, "amount": 8}])
    rid2 = h.seed_receipt(supplier="回收站供应商", date=_d(-1), approve=False,
                          items=[{"name": "回收站肉", "quantity": 1, "unit": "斤", "unit_price": 9, "amount": 9}])
    for r in (rid, rid2):
        st, body = h.api("POST", "/api/receipt/%d/discard" % r, role="owner")
        assert st == 200, body
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-archive")
        assert page.is_visible("#trashCard")
        page.click("#trashCard summary")
        page.wait_for_selector("#trashBody table")
        assert page.locator("#trashBody tbody tr").count() == 2
        h.shot(page, "trash-desk")
        page.locator("#trashBody tr", has_text="#%d" % rid).locator(".trash-restore").click()
        page.wait_for_function("() => !document.getElementById('trashBody').textContent.includes('#%d')" % rid)
        st, body = h.api("GET", "/api/receipt/%d" % rid)
        assert st == 200
        page.locator("#trashBody tr", has_text="#%d" % rid2).locator(".trash-purge").click()
        page.wait_for_selector(".ui-dlg")
        assert "无法找回" in page.inner_text(".ui-dlg")
        page.click(".ui-dlg-btn.danger")
        page.wait_for_selector("#trashBody >> text=回收站是空的")
        assert not _page_errors(page)
    finally:
        h.close(page)


def test_trash_hidden_for_staff(server, pw):
    page = h.new_page(pw, role="staff")
    try:
        h.open_app(page)
        assert not page.evaluate("() => { const c = document.getElementById('trashCard'); return !!c && c.offsetParent !== null; }")
        assert h.api("GET", "/api/trash/receipts", role="staff")[0] == 403
    finally:
        h.close(page)


def test_supplier_merge_entry_preview_and_confirm(server, pw):
    keep = h.seed_receipt(supplier="合并保留商行", date=_d(-3), pay_date=_d(30),
                          items=[{"name": "合并A", "quantity": 1, "unit": "斤", "unit_price": 10, "amount": 10}])
    drop = h.seed_receipt(supplier="合并重复商行", date=_d(-2), pay_date=_d(30),
                          items=[{"name": "合并B", "quantity": 2, "unit": "斤", "unit_price": 10, "amount": 20}])
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-archive")
        page.click("#arcTab-suppliers")
        assert page.is_visible("#btnOpenSupplierMerge")
        page.click("#btnOpenSupplierMerge")
        page.wait_for_selector("#mergeModal:not(.hide)")
        page.select_option("#mergeKeepSelect", label=page.locator("#mergeKeepSelect option", has_text="合并保留商行").inner_text())
        page.select_option("#mergeDropSelect", label=page.locator("#mergeDropSelect option", has_text="合并重复商行").inner_text())
        preview = page.inner_text("#mergePreview")
        assert "合并重复商行" in preview and "合并保留商行" in preview and "不能撤销" in preview and "$20.00" in preview
        h.shot(page, "supplier-merge-desk")
        # 保留与并入相同：拦截
        page.select_option("#mergeDropSelect", label=page.locator("#mergeDropSelect option", has_text="合并保留商行").inner_text())
        assert "同一家" in page.inner_text("#mergePreview")
        assert page.is_disabled("#mergeSubmitBtn")
        page.select_option("#mergeDropSelect", label=page.locator("#mergeDropSelect option", has_text="合并重复商行").inner_text())
        page.click("#mergeSubmitBtn")
        page.wait_for_selector(".ui-dlg")
        assert "不能撤销" in page.inner_text(".ui-dlg")
        page.click(".ui-dlg-btn.danger")
        page.wait_for_function("() => document.getElementById('mergeModal').classList.contains('hide')")
        st, body = h.api("GET", "/api/receipt/%d" % drop)
        assert (body.get("data") or body).get("supplier_name") == "合并保留商行"
        # 供应商行里的更多菜单也有入口
        page.wait_for_timeout(600)
        page.locator("#supplierAdminBody tr", has_text="合并保留商行").locator("button", has_text="更多").click()
        assert "合并重复供应商" in page.inner_text("#supMoreMenu")
        assert not _page_errors(page)
    finally:
        h.close(page)


def test_staff_sees_only_scan_and_inventory(server, pw):
    page = h.new_page(pw, role="staff")
    try:
        h.open_app(page)
        visible = page.evaluate(
            "() => Array.from(document.querySelectorAll('.sidebar-nav .sidebar-btn[data-target]'))"
            ".filter(b => b.offsetParent !== null).map(b => b.dataset.target)")
        assert visible == ["tab-scan", "tab-inventory"], visible
    finally:
        h.close(page)

# -*- coding: utf-8 -*-
"""F1b：库存页（操作列、表格宽度、价格无数据、超库存确认、术语）真实浏览器回归。"""

import datetime

import pytest

import _f1b_pw as h

sync_playwright = h.playwright_api.sync_playwright
TODAY = datetime.date.today()


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


def _sku(name):
    st, body = h.api("GET", "/api/inventory?q=" + name)
    rows = body.get("data") or []
    return next(r for r in rows if r["name"] == name)


def _page_errors(page):
    return [e for e in page.errors if e.startswith("pageerror")]


@pytest.fixture(scope="module")
def seeded(server):
    h.seed_receipt(supplier="库存供应商", date=(TODAY - datetime.timedelta(days=1)).isoformat(), settlement="cash",
                   items=[{"name": "库存测试鸡", "quantity": 21, "unit": "斤", "unit_price": 10, "amount": 210}])
    h.seed_receipt(supplier="库存供应商", date="2016-03-05", settlement="cash",
                   items=[{"name": "古早价格菜", "quantity": 5, "unit": "斤", "unit_price": 7, "amount": 35}])
    return True


def test_inventory_row_actions_and_table_fit(server, pw, seeded):
    for name, vp in (("desk", h.DESKTOP), ("mobile", h.MOBILE)):
        page = h.new_page(pw, role="owner", viewport=vp)
        try:
            h.open_app(page, "tab-inventory")
            page.wait_for_selector("#inventoryTableBody tr")
            m = page.evaluate("""() => {
                const t = document.getElementById('inventoryTable');
                const wrap = t.closest('.table-container');
                const btns = Array.from(document.querySelectorAll('#inventoryTableBody tr .inv-actions'))
                    .map(a => Array.from(a.querySelectorAll('button')).length);
                const bh = Array.from(document.querySelectorAll('#inventoryTableBody .inv-actions button'))
                    .map(b => b.getBoundingClientRect().height);
                return {tableW: t.scrollWidth, wrapW: wrap.clientWidth, wrapScroll: wrap.scrollWidth,
                        btns: btns, minBtn: Math.min.apply(null, bh), docW: document.documentElement.scrollWidth};
            }""")
            assert m["btns"] and max(m["btns"]) <= 3, m
            assert m["minBtn"] >= 40, m
            if name == "desk":
                assert m["wrapScroll"] <= m["wrapW"] + 1, m
            else:
                assert m["docW"] <= 391, m
            h.shot(page, "inventory-" + name)
            if name == "desk":
                page.locator("#inventoryTableBody tr", has_text="库存测试鸡").locator("button", has_text="更多").click()
                menu = page.inner_text("#invMoreMenu")
                for label in ("价格走势", "盘点", "编辑", "出库", "停用"):
                    assert label in menu, menu
                h.shot(page, "inventory-more-menu-desk")
            assert not _page_errors(page)
        finally:
            h.close(page)


def test_price_history_without_recent_data_shows_dash(server, pw, seeded):
    sku = _sku("古早价格菜")
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-inventory")
        page.wait_for_selector("#inventoryTableBody tr")
        page.evaluate("id => viewPriceHistory(id)", sku["id"])
        page.wait_for_selector("#priceHistoryMetrics .ph-metric")
        metrics = page.inner_text("#priceHistoryMetrics")
        assert "近 30 天无进货" in metrics
        avg_card = page.locator("#priceHistoryMetrics .ph-metric", has_text="30 日均价").inner_text()
        assert "—" in avg_card and "$0.00" not in avg_card
        vs_card = page.locator("#priceHistoryMetrics .ph-metric", has_text="较均价").inner_text()
        assert "—" in vs_card and "0.0%" not in vs_card
        table = page.inner_text("#priceHistoryTableBody")
        assert "receipt" not in table
        h.shot(page, "price-history-no-recent-desk")
    finally:
        h.close(page)


def test_sku_modal_has_no_english_jargon(server, pw, seeded):
    sku = _sku("库存测试鸡")
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-inventory")
        page.wait_for_selector("#inventoryTableBody tr")
        page.evaluate("id => openEditSkuModal(id)", sku["id"])
        txt = page.inner_text("#skuModal")
        assert "SKU" not in txt, txt
        page.evaluate("id => viewPriceHistory(id)", sku["id"])
        page.wait_for_selector("#priceHistoryMetrics .ph-metric")
        assert "SKU" not in page.inner_text("#priceHistoryModal")
        body_text = page.inner_text("#tab-inventory")
        assert "SKU" not in body_text
    finally:
        h.close(page)


def test_overdraw_requires_owner_reason_and_cancel_keeps_stock(server, pw, seeded):
    sku = _sku("库存测试鸡")
    assert sku["current_stock"] == 21
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-inventory")
        page.wait_for_selector("#inventoryTableBody tr")
        page.locator("#inventoryTableBody tr", has_text="库存测试鸡").locator("button", has_text="变动录入").click()
        page.wait_for_selector("#modalStockMovementDialog:not(.hide)")
        page.select_option("#stockMovementKind", "consume")
        page.fill("#stockMovementQty", "1000")
        page.click("#modalStockMovementDialog .btn-primary")
        page.wait_for_selector(".ui-dlg")
        dlg = page.inner_text(".ui-dlg")
        assert "库存只有 21 斤" in dlg and "1000" in dlg and "差 979" in dlg, dlg
        h.shot(page, "overdraw-confirm-desk")
        # 取消：库存不变
        page.click(".ui-dlg-btn:not(.primary)")
        page.wait_for_function("() => !document.querySelector('.ui-dlg')")
        assert _sku("库存测试鸡")["current_stock"] == 21
        # 不写原因不能继续
        page.click("#modalStockMovementDialog .btn-primary")
        page.wait_for_selector(".ui-dlg")
        page.click(".ui-dlg-btn.primary")
        assert "写明为什么" in page.inner_text(".ui-dlg-err")
        # 写原因后继续，库存变负且流水有原因
        page.fill(".ui-dlg input", "上周进货单还没录")
        page.click(".ui-dlg-btn.primary")
        page.wait_for_function("() => document.getElementById('modalStockMovementDialog').classList.contains('hide')")
        assert _sku("库存测试鸡")["current_stock"] == -979
        st, logs = h.api("GET", "/api/inventory/logs?kind=consume")
        assert any("上周进货单还没录" in (r.get("note") or "") for r in logs.get("data", [])), logs
        assert not _page_errors(page)
    finally:
        h.close(page)


def test_normal_consume_does_not_ask(server, pw, seeded):
    h.seed_receipt(supplier="库存供应商", date=TODAY.isoformat(), settlement="cash",
                   items=[{"name": "正常消耗菜", "quantity": 10, "unit": "斤", "unit_price": 3, "amount": 30}])
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-inventory")
        page.wait_for_selector("#inventoryTableBody tr")
        page.locator("#inventoryTableBody tr", has_text="正常消耗菜").locator("button", has_text="变动录入").click()
        page.wait_for_selector("#modalStockMovementDialog:not(.hide)")
        page.select_option("#stockMovementKind", "consume")
        page.fill("#stockMovementQty", "4")
        page.click("#modalStockMovementDialog .btn-primary")
        page.wait_for_function("() => document.getElementById('modalStockMovementDialog').classList.contains('hide')")
        assert not page.query_selector(".ui-dlg")
        assert _sku("正常消耗菜")["current_stock"] == 6
    finally:
        h.close(page)


def test_inventory_staff_view_is_read_only(server, pw, seeded):
    page = h.new_page(pw, role="staff")
    try:
        h.open_app(page, "tab-inventory")
        page.wait_for_selector("#inventoryTableBody tr")
        btns = page.evaluate("() => Array.from(document.querySelectorAll('#inventoryTableBody .inv-actions button')).map(b => b.textContent.trim())")
        assert btns and set(btns) == {"详情"}, btns
    finally:
        h.close(page)

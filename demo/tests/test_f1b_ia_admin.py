# -*- coding: utf-8 -*-
"""F1b：信息架构（今日待办、店员最小导航）、admin 页面可读性与手机溢出、/evalset 空状态与权限。"""

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


@pytest.fixture(scope="module")
def seeded(server):
    h.seed_receipt(supplier="待办供应商", date=_d(-30), pay_date=_d(-5),
                   items=[{"name": "待办菜", "quantity": 4, "unit": "斤", "unit_price": 10, "amount": 40}])
    h.seed_receipt(supplier="待办供应商", date=_d(-2), approve=False,
                   items=[{"name": "待审批肉", "quantity": 2, "unit": "斤", "unit_price": 25, "amount": 50}])
    h.seed_receipt(supplier="待办供应商", date=_d(-1), settlement="cash",
                   items=[{"name": "低库存菜", "quantity": 1, "unit": "斤", "unit_price": 6, "amount": 6}])
    st, body = h.api("GET", "/api/inventory?q=低库存菜")
    sku = body["data"][0]
    st, body = h.api("PATCH", "/api/inventory/skus/%d" % sku["id"], {"min_stock_alert": 50})
    assert st == 200, body
    return True


def test_owner_lands_on_today_and_blocks_link_through(server, pw, seeded):
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page)
        assert page.evaluate("() => document.querySelector('.tab-content.active').id") == "tab-today"
        page.wait_for_selector("#todayBody .today-card")
        cards = page.locator("#todayBody .today-card")
        assert cards.count() == 3
        text = page.inner_text("#todayBody")
        for t in ("待我审批", "挂账单：逾期 / 快到期", "低库存"):
            assert t in text, text
        nums = [c.locator(".today-num").inner_text().split()[0] for c in [cards.nth(i) for i in range(3)]]
        assert nums == ["1", "1", "1"], nums
        h.shot(page, "today-desk")

        page.locator("#todayBody [data-today=approve]").click()
        page.wait_for_function("() => document.getElementById('tab-scan').classList.contains('active')")
        assert page.evaluate("() => document.getElementById('queueTabApprove').classList.contains('active')")

        h.go_tab(page, "tab-today")
        page.locator("#todayBody [data-today=pay]").click()
        page.wait_for_function("() => document.getElementById('tab-archive').classList.contains('active')")
        assert page.evaluate("() => document.querySelector('#payablesCard').offsetParent !== null")

        h.go_tab(page, "tab-today")
        page.locator("#todayBody [data-today=low]").click()
        page.wait_for_function("() => document.getElementById('tab-inventory').classList.contains('active')")
        page.wait_for_function("() => document.getElementById('invFilterSelect').value === 'low'")
        assert not _page_errors(page)
    finally:
        h.close(page)

    mobile = h.new_page(pw, role="owner", viewport=h.MOBILE)
    try:
        h.open_app(mobile)
        mobile.wait_for_selector("#todayBody .today-card")
        assert mobile.evaluate("() => document.documentElement.scrollWidth") <= 391
        h.shot(mobile, "today-mobile")
    finally:
        h.close(mobile)


def test_navigation_by_role(server, pw, seeded):
    expect = {
        "staff": ["tab-scan", "tab-inventory"],
        "owner": ["tab-today", "tab-scan", "tab-inventory", "tab-dishes", "tab-archive", "tab-report",
                  "tab-export", "tab-settings"],
    }
    for role, tabs in expect.items():
        page = h.new_page(pw, role=role)
        try:
            h.open_app(page)
            visible = page.evaluate(
                "() => Array.from(document.querySelectorAll('.sidebar-nav .sidebar-btn[data-target]'))"
                ".filter(b => b.offsetParent !== null).map(b => b.dataset.target)")
            assert visible == tabs, (role, visible)
        finally:
            h.close(page)
    admin = h.new_page(pw, role="admin")
    try:
        h.open_app(admin)
        assert admin.evaluate("() => document.querySelector('.tab-content.active').id") == "tab-scan"
        visible = admin.evaluate(
            "() => Array.from(document.querySelectorAll('.sidebar-nav .sidebar-btn[data-target]'))"
            ".filter(b => b.offsetParent !== null).map(b => b.dataset.target)")
        assert "tab-engine" in visible and "tab-analytics" in visible and "tab-settings" in visible
    finally:
        h.close(admin)


def test_staff_forced_into_hidden_tab_is_bounced_back(server, pw, seeded):
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-dishes")
        page.select_option("#demoRoleSelect", "staff")
        page.wait_for_function("() => document.getElementById('tab-scan').classList.contains('active')")
    finally:
        h.close(page)


SMALL_FONT_JS = """
(tabId) => {
  const out = [];
  document.getElementById(tabId).querySelectorAll('*').forEach(el => {
    if (!el.offsetParent) return;
    let own = false;
    for (const c of el.childNodes) { if (c.nodeType === 3 && c.nodeValue.trim()) own = true; }
    if (!own) return;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs < 12) out.push(fs + ' ' + el.tagName + '.' + el.className);
  });
  return out;
}
"""

OVERFLOW_JS = """
() => {
  const vw = document.documentElement.clientWidth, res = [];
  document.querySelectorAll('#tab-engine *, #tab-analytics *').forEach(el => {
    if (!el.offsetParent) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.right <= vw + 1) return;
    let p = el.parentElement;
    while (p && p !== document.body) {
      const ov = getComputedStyle(p).overflowX;
      if (ov === 'auto' || ov === 'scroll') return;
      p = p.parentElement;
    }
    res.push(Math.round(r.right) + ' ' + el.tagName + '.' + String(el.className).slice(0, 40) + (el.id ? '#' + el.id : ''));
  });
  return res.slice(0, 10);
}
"""


def _analytics_segments(page):
    return page.evaluate("() => Array.from(document.querySelectorAll('.analytics-segment-btn')).map(b => b.id)")


def test_admin_pages_readable_and_human_labels(server, pw):
    page = h.new_page(pw, role="admin")
    try:
        h.open_app(page)
        h.go_tab(page, "tab-engine")
        for sub in ("01", "02", "03"):
            page.evaluate("s => switchEngineSubView(s, {scroll: false})", sub)
            page.wait_for_timeout(500)
            assert page.evaluate(SMALL_FONT_JS, "tab-engine") == [], sub
            body = page.inner_text("#tab-engine")
            for bad in ("GRAY", "DISABLED", "ENABLED", "EXPERIMENTS", "ACTIVE"):
                assert bad not in body, (sub, bad)
        page.evaluate("() => switchEngineSubView('02', {scroll: false})")
        page.wait_for_timeout(400)
        body = page.inner_text("#tab-engine")
        assert "灰度未开启" in body or "灰度放量中" in body
        h.go_tab(page, "tab-analytics")
        for seg in _analytics_segments(page):
            page.evaluate("i => document.getElementById(i).click()", seg)
            page.wait_for_timeout(500)
            assert page.evaluate(SMALL_FONT_JS, "tab-analytics") == [], seg
        h.shot(page, "admin-analytics-desk")
        assert not _page_errors(page)
    finally:
        h.close(page)


def test_admin_pages_no_horizontal_overflow_on_mobile(server, pw):
    page = h.new_page(pw, role="admin", viewport=h.MOBILE)
    try:
        h.open_app(page)
        h.go_tab(page, "tab-engine")
        for sub in ("01", "02", "03"):
            page.evaluate("s => switchEngineSubView(s, {scroll: false})", sub)
            page.wait_for_timeout(500)
            assert page.evaluate(OVERFLOW_JS) == [], sub
        h.shot(page, "admin-engine-mobile")
        h.go_tab(page, "tab-analytics")
        for seg in _analytics_segments(page):
            page.evaluate("i => document.getElementById(i).click()", seg)
            page.wait_for_timeout(500)
            assert page.evaluate(OVERFLOW_JS) == [], seg
        h.shot(page, "admin-analytics-mobile")
    finally:
        h.close(page)


def test_observability_success_rate_basis_is_explained(server, pw):
    page = h.new_page(pw, role="admin")
    try:
        h.open_app(page)
        h.go_tab(page, "tab-analytics")
        note = page.inner_text("#parsingKpiSuccessNote")
        assert "AI 自动识别" in note and "手工录入" in note and "不计入" in note
    finally:
        h.close(page)


def test_cost_report_states_basis(server, pw, seeded):
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-report")
        basis = page.inner_text("#costReportBasis")
        assert "已入账单据" in basis and "服务费" in basis and "税" in basis and "折扣" in basis
        page.wait_for_function("() => document.getElementById('costReportSummary').textContent.length > 0")
        assert "含服务费、税、折扣" in page.inner_text("#costReportSummary") or "暂无" in page.inner_text("#costReportSummary")
        h.shot(page, "cost-report-desk")
    finally:
        h.close(page)


def test_evalset_without_manifest_shows_empty_states_not_loading(server, pw):
    page = h.new_page(pw, role="admin")
    try:
        page.goto(h.BASE + "/evalset")
        page.wait_for_function("() => document.getElementById('progressText').textContent !== '加载中…'", timeout=15000)
        page.wait_for_timeout(800)
        text = page.inner_text("body")
        assert "加载中" not in text, text
        assert "还没有评测集" in text
        assert page.is_disabled("#batchBtn")
        h.shot(page, "evalset-empty-desk")
        mobile = page.context.new_page()
        mobile.set_viewport_size(h.MOBILE)
        mobile.goto(h.BASE + "/evalset")
        mobile.wait_for_timeout(1500)
        assert mobile.evaluate("() => document.documentElement.scrollWidth") <= 400
    finally:
        h.close(page)


@pytest.mark.parametrize("role", ["staff", "owner"])
def test_evalset_non_admin_sees_only_permission_notice(server, pw, role):
    page = h.new_page(pw, role=role)
    try:
        page.goto(h.BASE + "/evalset")
        page.wait_for_selector("[role=alert]")
        text = page.inner_text("body")
        assert "只有系统管理员能用" in text
        assert "左侧栏最上方" in text and "当前角色" in text
        assert "右上角" not in text
        assert page.query_selector("#sampleList") is None and page.query_selector("#reflowBody") is None
        link = page.locator("a", has_text="回到主界面")
        assert link.get_attribute("href") == "/"
        h.shot(page, "evalset-%s-desk" % role)
    finally:
        h.close(page)

# -*- coding: utf-8 -*-
"""F1b：设置页（单位 / 单位换算 / 汇率）真实浏览器回归。桌面 1440x900 与手机 390x844。"""

import pytest

import _f1b_pw as h

sync_playwright = h.playwright_api.sync_playwright


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


def _min_height(page, selector):
    return page.evaluate(
        "s => Array.from(document.querySelectorAll(s)).filter(e => e.offsetParent)"
        ".map(e => e.getBoundingClientRect().height)", selector)


def test_staff_has_no_settings_entry_and_api_403(server, pw):
    page = h.new_page(pw, role="staff")
    try:
        h.open_app(page)
        visible = page.evaluate(
            "() => { const b = document.getElementById('settingsNavBtn'); return !!b && b.offsetParent !== null; }")
        assert visible is False
        assert h.api("GET", "/api/settings/units", role="staff")[0] == 403
        assert h.api("GET", "/api/settings/fx_rates", role="staff")[0] == 403
    finally:
        h.close(page)


def test_owner_units_and_conversions_flow(server, pw):
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page)
        assert page.evaluate("() => document.getElementById('settingsNavBtn').offsetParent !== null")
        h.go_tab(page, "tab-settings")
        page.wait_for_selector("#stUnitsList table")
        assert page.locator("#stUnitsList").inner_text().count("系统自带") >= 5

        # 添加单位：空名字给人话提示
        page.click("#stUnitAdd")
        assert "请先填写单位名称" in page.inner_text("#stUnitMsg")
        page.fill("#stUnitName", "大桶")
        page.select_option("#stUnitKind", "count")
        page.click("#stUnitAdd")
        page.wait_for_selector("#stUnitsList >> text=大桶")
        # 重复添加：后端人话提示
        page.fill("#stUnitName", "大桶")
        page.click("#stUnitAdd")
        page.wait_for_function("() => document.getElementById('stUnitMsg').textContent.includes('已经有了')")

        # 换算校验
        page.fill("#stConvFrom", "箱")
        page.fill("#stConvTo", "箱")
        page.fill("#stConvFactor", "12")
        page.click("#stConvSave")
        assert "同一个" in page.inner_text("#stConvMsg")
        page.fill("#stConvTo", "瓶")
        page.fill("#stConvFactor", "-3")
        page.click("#stConvSave")
        assert "大于 0" in page.inner_text("#stConvMsg")
        page.fill("#stConvFactor", "abc")
        page.click("#stConvSave")
        assert "只能填数字" in page.inner_text("#stConvMsg")

        # 「1 箱 = 12 瓶」保存并显示反向推导
        page.fill("#stConvFactor", "12")
        page.wait_for_function("() => document.getElementById('stConvPreview').textContent.includes('反过来 1 瓶')")
        page.click("#stConvSave")
        page.wait_for_selector("#stConvList >> text=1 箱 = 12 瓶")
        row = page.locator("#stConvList tr", has_text="1 箱 = 12 瓶").inner_text()
        assert "反过来：1 瓶 = 0.0833333 箱" in row

        # 实时预览：10 斤 = 6.048 公斤
        page.fill("#stConvFrom", "斤")
        page.fill("#stConvTo", "公斤")
        page.fill("#stConvFactor", "0.6048")
        page.wait_for_function("() => document.getElementById('stConvPreview').textContent.includes('10 斤 = 6.048 公斤')")
        h.shot(page, "settings-desk")

        # 系统默认项标明，并允许覆盖
        seed_row = page.locator("#stConvList tr", has_text="1 斤 = 0.6048 公斤")
        assert "系统默认，可修改" in seed_row.inner_text()
        seed_row.locator(".st-edit-conv").click()
        page.fill("#stConvFactor", "0.5")
        page.click("#stConvSave")
        page.wait_for_selector("#stConvList >> text=1 斤 = 0.5 公斤")
        assert "本店设置" in page.locator("#stConvList tr", has_text="1 斤 = 0.5 公斤").inner_text()

        # 删除换算走应用内确认
        page.locator("#stConvList tr", has_text="1 箱 = 12 瓶").locator(".st-del-conv").click()
        page.wait_for_selector(".ui-dlg")
        assert "确定删除" in page.inner_text(".ui-dlg")
        page.click(".ui-dlg-btn.danger")
        page.wait_for_function("() => !document.getElementById('stConvList').textContent.includes('1 箱 = 12 瓶')")

        # 被 SKU 引用的单位不能删：人话提示
        st, _ = h.api("POST", "/api/inventory/skus", {"name": "设置页引用测试", "category": "其他", "base_unit": "大桶",
                                                      "min_stock_alert": 1})
        page.click("#stUnitsList tr:has-text('大桶') .st-del-unit")
        page.wait_for_selector(".ui-dlg")
        page.click(".ui-dlg-btn.danger")
        if st == 200:
            page.wait_for_selector(".ui-dlg:has-text('不能删除')")
            page.click(".ui-dlg-btn")

        # 按钮高度 >= 40px
        heights = _min_height(page, "#tab-settings .st-btn, #tab-settings .form-control")
        assert heights and min(heights) >= 40, heights
        assert not [e for e in page.errors if "pageerror" in e], page.errors
    finally:
        h.close(page)


def test_fx_rates_section(server, pw):
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-settings")
        page.wait_for_selector("#stFxBody .st-fx-in")
        assert "参考值，需老板确认" in page.inner_text("#stFxBody")
        page.fill("#stFxNewCode", "U1")
        page.fill("#stFxNewRate", "7.8")
        page.click("#stFxSave")
        assert "3 个英文字母" in page.inner_text("#stFxMsg")
        page.fill("#stFxNewCode", "ZZZ")
        page.fill("#stFxNewRate", "0")
        page.click("#stFxSave")
        assert "大于 0" in page.inner_text("#stFxMsg") or "汇率" in page.inner_text("#stFxMsg")
        page.fill("#stFxNewRate", "1.25")
        page.click("#stFxSave")
        page.wait_for_selector("#stFxBody label:has-text('1 ZZZ =')")
        st, body = h.api("GET", "/api/settings/fx_rates")
        assert any(r["currency"] == "ZZZ" and abs(r["rate"] - 1.25) < 1e-9 for r in body["data"]["rates"])
    finally:
        h.close(page)


def test_settings_mobile_no_overflow_and_reachable_via_more(server, pw):
    page = h.new_page(pw, role="owner", viewport=h.MOBILE)
    try:
        h.open_app(page, "tab-settings")
        page.wait_for_selector("#stUnitsList table")
        sw = page.evaluate("() => document.documentElement.scrollWidth")
        assert sw <= 391, sw
        # 设置入口登记在共用导航数据里，手机“更多”从这里取
        reg = page.evaluate("() => (window.NAV_ITEMS || []).filter(i => i.target === 'tab-settings')")
        assert reg and reg[0]["group"] == "more" and "staff" not in reg[0]["roles"]
        h.shot(page, "settings-mobile")
    finally:
        h.close(page)


def test_sku_unit_dropdown_uses_tenant_units(server, pw):
    h.api("POST", "/api/settings/units", {"name": "提篮", "kind": "count"})
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page, "tab-inventory")
        page.evaluate("() => openAddSkuModal()")
        page.wait_for_function("() => document.querySelectorAll('#skuUnitList option').length > 5")
        values = page.evaluate("() => Array.from(document.querySelectorAll('#skuUnitList option')).map(o => o.value)")
        assert "提篮" in values and "公斤" in values
        page.fill("#skuModalUnit", "全新单位乙")
        page.wait_for_function("() => document.getElementById('skuModalUnitHint').textContent.includes('设置')")
        assert "补上" in page.inner_text("#skuModalUnitHint")
    finally:
        h.close(page)


def test_unit_conversion_missing_guides_to_settings(server, pw):
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page)
        page.evaluate("""() => { window.handleSettingsBlock({code: 'UNIT_CONVERSION_MISSING',
            msg: '「箱」和库存单位「斤」之间还没有换算关系，请老板到「设置」里添加换算',
            data: {pairs: [{from_unit: '箱', to_unit: '斤', item_name: '白菜'}]}}); }""")
        page.wait_for_selector(".ui-dlg")
        page.click(".ui-dlg-btn.primary")
        page.wait_for_function("() => document.getElementById('stConvFrom') && document.getElementById('stConvFrom').value === '箱'")
        assert page.input_value("#stConvTo") == "斤"
    finally:
        h.close(page)

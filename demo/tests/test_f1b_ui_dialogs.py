# -*- coding: utf-8 -*-
"""F1b：应用内对话框（ui-dialog.js）与库存/归档/付款/供应商/部门/admin 区域不再使用浏览器原生弹窗。"""

import os
import re

import pytest

import _f1b_pw as h

sync_playwright = h.playwright_api.sync_playwright
STATIC = os.path.join(h._DEMO_DIR, "static", "js")

NATIVE_GUARD = """
window.__native = [];
window.confirm = function (m) { window.__native.push('confirm:' + m); return true; };
window.alert = function (m) { window.__native.push('alert:' + m); };
window.prompt = function (m) { window.__native.push('prompt:' + m); return 'x'; };
"""

MY_FUNCTIONS = [
    "addDepartment", "renameDepartment", "deactivateDepartment", "deduplicateSkus", "deleteSkuDirect",
    "toggleSkuActive", "deactivateSupplier", "confirmReconTask", "deleteVendorMemory", "toggleGoldenSample",
    "deleteSelectedPreset", "saveCurrentConfigAsPreset", "openPaymentModal", "submitPayment", "submitMerge",
    "submitStockMovement", "submitOutbound",
]


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


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def _function_body(src, name):
    m = re.search(r"\n(?:async )?function %s\(" % re.escape(name), src)
    assert m, name
    nxt = re.search(r"\n(?:async )?function \w+\(|\n// -{5,}", src[m.end():])
    return src[m.start(): m.end() + (nxt.start() if nxt else len(src))]


def test_no_native_dialogs_in_owned_areas_static():
    native = re.compile(r"(?<![\w.])(?:window\.)?(?:confirm|alert|prompt)\(")
    for fname in ("finance.js", "settings.js", "evalset.js", "eval_workbench.js", "export_center.js", "ui-dialog.js"):
        src = _read(fname)
        for m in native.finditer(src):
            line = src[:m.start()].count("\n") + 1
            raise AssertionError("%s:%d 仍在使用原生弹窗" % (fname, line))
    main = _read("main.js")
    for name in MY_FUNCTIONS:
        if re.search(r"\n(?:async )?function %s\(" % name, main):
            assert not native.search(_function_body(main, name)), name
    assert not re.search(r"(?<![\w.])alert\(", main), "main.js 仍有 alert("


def test_dialog_keyboard_focus_and_roles(server, pw):
    page = h.new_page(pw, role="owner")
    try:
        h.open_app(page)
        page.evaluate("() => { document.getElementById('demoRoleSelect').focus(); window.__r = null; "
                      "uiConfirm({title: '删除测试', message: '确定删除吗？', danger: true}).then(v => { window.__r = v; }); }")
        dlg = page.locator(".ui-dlg")
        dlg.wait_for()
        assert dlg.get_attribute("role") == "dialog" and dlg.get_attribute("aria-modal") == "true"
        # 危险操作默认聚焦“取消”
        assert page.evaluate("() => document.activeElement.textContent.trim()") == "取消"
        heights = page.evaluate("() => Array.from(document.querySelectorAll('.ui-dlg-btn')).map(b => b.getBoundingClientRect().height)")
        assert min(heights) >= 44
        # Tab 不会跑出对话框
        for _ in range(5):
            page.keyboard.press("Tab")
            assert page.evaluate("() => !!document.activeElement.closest('.ui-dlg')")
        page.keyboard.press("Escape")
        page.wait_for_function("() => window.__r === false")
        assert page.evaluate("() => document.activeElement.id") == "demoRoleSelect"

        page.evaluate("() => { window.__p = undefined; uiPrompt({title: '改名', label: '新名称', value: '旧名'}).then(v => { window.__p = v; }); }")
        page.wait_for_selector(".ui-dlg input")
        assert page.input_value(".ui-dlg input") == "旧名"
        page.fill(".ui-dlg input", "")
        page.keyboard.press("Enter")
        assert "不能空着" in page.inner_text(".ui-dlg-err")
        page.fill(".ui-dlg input", "新名")
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.__p === '新名'")
        page.evaluate("() => { window.__p = undefined; uiPrompt({title: '改名', label: '新名称'}).then(v => { window.__p = v; }); }")
        page.keyboard.press("Escape")
        page.wait_for_function("() => window.__p === null")
        h.shot(page, "ui-dialog-desk")
    finally:
        h.close(page)


def test_department_supplier_sku_flows_use_app_dialogs(server, pw):
    h.seed_receipt(supplier="弹窗供应商", settlement="cash",
                   items=[{"name": "弹窗测试菜", "quantity": 2, "unit": "斤", "unit_price": 5, "amount": 10}])
    st, body = h.api("POST", "/api/departments", {"name": "弹窗测试部"})
    page = h.new_page(pw, role="owner")
    page.add_init_script(NATIVE_GUARD)
    try:
        h.open_app(page, "tab-report")
        page.wait_for_function("() => typeof allDepartments !== 'undefined' && allDepartments.some(d => d.name === '弹窗测试部')")
        page.evaluate("() => { document.getElementById('deptAdminDetails').open = true; }")
        dept = next(d for d in page.evaluate("() => allDepartments") if d["name"] == "弹窗测试部")
        page.evaluate("id => { deactivateDepartment(id); }", dept["id"])
        page.wait_for_selector(".ui-dlg")
        assert "停用" in page.inner_text(".ui-dlg")
        page.click(".ui-dlg-btn.danger")
        page.wait_for_function("() => !document.querySelector('.ui-dlg')")
        assert page.evaluate("() => window.__native") == []

        h.go_tab(page, "tab-archive")
        page.click("#arcTab-suppliers")
        sup = page.locator("#supplierAdminBody tr", has_text="弹窗供应商")
        sup.wait_for()
        sup.locator("button", has_text="更多").click()
        page.click("#supMoreMenu >> text=停用")
        page.wait_for_selector(".ui-dlg")
        assert "停用这家供应商" in page.inner_text(".ui-dlg")
        page.click(".ui-dlg-btn.danger")
        page.wait_for_function("() => !document.querySelector('.ui-dlg')")

        h.go_tab(page, "tab-inventory")
        page.wait_for_selector("#inventoryTableBody tr")
        row = page.locator("#inventoryTableBody tr", has_text="弹窗测试菜")
        row.locator("button", has_text="更多").click()
        page.click("#invMoreMenu >> text=停用")
        page.wait_for_selector(".ui-dlg")
        assert "停用食材" in page.inner_text(".ui-dlg")
        page.click(".ui-dlg-btn.danger")
        page.wait_for_function("() => !document.querySelector('.ui-dlg')")
        assert page.evaluate("() => window.__native") == []
        assert not [e for e in page.errors if e.startswith("pageerror")]
    finally:
        h.close(page)

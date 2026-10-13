# -*- coding: utf-8 -*-
"""阻塞级回归（真实浏览器 + 真实服务）：空明细复核表单加一行后必须能保存。

缺陷（UAT 实测，命中「转手工补录」convert_manual 主推链路）：
  `renderEditForm` 在 `items.length === 0` 时往 `#itemTableBody` 写入一条
  **不含任何 input** 的占位行（`<tr class="warning-row">未检出单据明细项…</tr>`）；
  「+ 新增明细行」（`addEmptyRow` / `appendTableRow`）只追加、不清占位；采集侧
  `collectReviewFormData` 对每一行**无条件**取 `.inp-name.value` → 命中占位行抛
  `TypeError: Cannot read properties of null (reading 'value')`，异常冒泡出
  `submitSaveEdited`，于是「不发任何请求、界面无任何提示」，用户点「确认上传单据」
  后彻底卡死。

本用例在真实服务 + 真实 Chromium 上走该链路并守护修复：
  进入新建手工单（产品入口）→ 把复核表单渲染成「空明细占位行」态（与
  convert_manual / 空识别结果同款）→ 点「+ 新增明细行」→ 填值
  → 点「确认上传单据」→ 必须真的发出 `POST /api/save_edited` 且返回 status=success，
  且不得出现 pageerror / 未捕获异常。

数据面：被测服务由 `playwright_live_server` 自举，`DB_PATH` 指向 live 库的**只读拷贝**
（见 `_playwright_server.py`），本用例保存只写该副本。用例不上传任何文件，
不触碰 `demo/uploads/`。
"""
import os

import pytest
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

def _live_base_url():
    """夹具自举之后的地址。15010 被常驻服务占用时不会打到那台服务。"""
    import _playwright_server as _pw
    explicit = os.environ.get("PW_TEST_BASE_URL", os.environ.get("TEST_BASE_URL", "")).strip()
    return explicit or _pw.current_base_url()


SAVE_PATH = "/api/save_edited"


@pytest.mark.usefixtures("playwright_live_server")
def test_empty_items_review_form_can_save_after_adding_row():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            page_errors = []
            page.on("pageerror", lambda err: page_errors.append(str(err)))

            saved = []
            page.on(
                "response",
                lambda res: (
                    saved.append((res.status, res.url))
                    if res.request.method == "POST" and SAVE_PATH in res.url
                    else None
                ),
            )

            page.goto(_live_base_url())
            page.wait_for_selector(".app-wrapper", timeout=15000)
            page.evaluate("() => { if (typeof setRole === 'function') setRole('owner'); }")
            page.wait_for_timeout(300)

            # 1. 经产品入口进入新建手工单（卡片/左栏/状态全部由生产代码置好）
            page.locator("#btnNewManualEntry").click()
            page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=5000)

            # 2. 关键前置：把明细表渲染成「空明细占位行」态
            #    （convert_manual / 空识别结果走的就是 renderEditForm(items=[])）
            page.evaluate(
                """() => { renderEditForm({ receipt_id: null, items: [],
                                            quality_warnings: [], math_warnings: [] }); }"""
            )
            placeholder_rows = page.evaluate(
                "() => document.querySelectorAll('#itemTableBody tr.warning-row').length"
            )
            assert placeholder_rows == 1, (
                "前置不成立：空明细应渲染 1 条占位行（tr.warning-row），实际 %s" % placeholder_rows
            )
            assert page.evaluate(
                "() => document.querySelectorAll('#itemTableBody tr .inp-name').length"
            ) == 0, "前置不成立：占位行不应包含任何输入控件"

            # 3. 用户点「+ 新增明细行」补录一行
            page.locator("#btnAddRow").click()
            page.wait_for_function(
                "() => document.querySelectorAll('#itemTableBody tr.warning-row').length === 0",
                timeout=5000,
            )
            assert page.evaluate(
                "() => document.querySelectorAll('#itemTableBody tr .inp-name').length"
            ) == 1, "加行后应恰好有 1 个真实明细行"

            # 4. 填值（走真实 input 事件，让行内 recalc/埋点逻辑照常生效）
            page.fill("#inpSupplier", "E2E空明细回归供应商")
            page.fill("#inpDate", "2026-01-05")
            page.fill("#inpTotal", "100")
            # 结算方式必填（PRD M3 / D4，review_core.requireSettlementMarked）：
            # 未标记会被产品正确地阻断保存（toast 提示、不发请求）——2026-10-13 QA 三臂实验裁决
            page.select_option("#inpSettlementType", "cash")
            page.locator("#itemTableBody .inp-name").first.fill("走地鸡")
            page.locator("#itemTableBody .inp-unit").first.fill("kg")
            page.locator("#itemTableBody .inp-qty").first.fill("2")
            page.locator("#itemTableBody .inp-price").first.fill("50")
            page.locator("#itemTableBody .inp-amount").first.fill("100")

            # 5. 点「确认上传单据」——必须真的发出 POST /api/save_edited
            try:
                with page.expect_response(
                    lambda res: res.request.method == "POST" and SAVE_PATH in res.url,
                    timeout=15000,
                ) as resp_info:
                    page.locator("#btnSaveReview").click()
            except PlaywrightTimeoutError:
                pytest.fail(
                    "点击「确认上传单据」后 15s 内没有发出任何 POST %s 请求"
                    "（用户点保存无反应、界面无提示）。pageerror=%s" % (SAVE_PATH, page_errors)
                )

            response = resp_info.value
            body = response.json()
            assert response.status == 200, (response.status, body)
            assert body.get("status") == "success", body
            assert body.get("receipt_id"), body

            # 6. 全程不得有未捕获异常（缺陷期此处就是 TypeError: ... reading 'value'）
            assert page_errors == [], "保存过程中出现未捕获异常: %s" % page_errors
        finally:
            browser.close()

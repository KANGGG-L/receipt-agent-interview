# -*- coding: utf-8 -*-
"""灰测样本表（canary）渲染回归：不截断、且切子视图时不重复请求。

D24/D25（环境债治本）：本用例所需活体服务统一经 `playwright_live_server` 夹具自举
（见 demo/tests/conftest.py 与 `_playwright_server.py`）。服务必然经 `_playwright_serve.py`
启动器拉起并锁定域名 DNS 解析，且以 live 库的**只读拷贝**为 DB_PATH（拷贝方向是
live → 临时目录，live 库只被读取、绝不被写入），因此 `#adminGreySamplesBody` 能渲染出
>=100 条真实样本，用例可重复运行。

本用例此前自带一套 `ensure_server()`：直接 `-m uvicorn` 起服务、**未**锁定域名 DNS
解析。批量跑时它先于 `test_playwright_*` 起了一个未锁定服务，后续用例复用该服务，
使「创建 A/B 实验」这类要过 SSRF 校验的请求被 fail-closed 拒绝 —— 这正是「文件名顺序
决定谁先起服务」的顺序相关假失败根因。现统一走夹具的单一自举路径，不再各写一份。
"""

import os

import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("PW_TEST_BASE_URL", os.environ.get("TEST_BASE_URL", "http://127.0.0.1:15010"))
SCREENSHOT_DIR = os.environ.get("GUI_SCREENSHOT_DIR") or os.environ.get("ARTIFACTS_DIR") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../gui-test-screenshots"))
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def switch_demo_role(page, target_role):
    current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
    if current_role == target_role:
        return
    with page.expect_navigation(timeout=15000):
        page.select_option("#demoRoleSelect", target_role)
    page.wait_for_selector("#demoRoleSelect", timeout=10000)
    page.wait_for_timeout(400)


@pytest.mark.usefixtures("playwright_live_server")
def test_canary_table_no_truncation_and_no_duplicate_requests():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960})

        sample_requests = []
        page.on("request", lambda req: sample_requests.append(req.url) if "grey-test/samples" in req.url else None)

        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#demoRoleSelect", timeout=12000)
        switch_demo_role(page, "admin")

        # Step 1: Click analytics board (default subview: sec-telemetry)
        sample_requests.clear()
        page.locator("#analyticsBoardBtn").click()
        page.wait_for_selector("#tab-analytics", timeout=10000)
        page.wait_for_timeout(1000)

        # Assert 0 requests to samples when switching to tab-analytics (duplicate line 1372 removed, canary not eagerly loaded)
        assert len(sample_requests) == 0, (
            f"Expected 0 requests to samples on tab-analytics click when sec-telemetry is active, got {len(sample_requests)}: {sample_requests}"
        )

        # Step 2: Switch to Canary subview
        sample_requests.clear()
        page.locator("#btnSubViewCanary").click()
        page.wait_for_timeout(500)

        # Assert exactly 1 clean request when activating canary subview
        assert len(sample_requests) == 1, (
            f"Expected exactly 1 request on btnSubViewCanary click, got {len(sample_requests)}: {sample_requests}"
        )

        # Step 3: Wait for samples to render
        page.wait_for_selector("#adminGreySamplesBody tr td.cell-vendor", timeout=45000)
        page.wait_for_timeout(500)

        # Step 4: Verify limit notice
        notice = page.locator("#adminGreySampleLimitNotice")
        assert notice.is_visible(), "adminGreySampleLimitNotice should be visible"
        notice_text = notice.inner_text()
        # notice 形如「* 仅展示最新 100 条脱敏单据抽样观测记录，全量样本共 N 份」。
        # 展示上限 100 是固定口径；N 随库中单据数变化（页面按 deleted_at IS NULL 口径统计），
        # 因此断言语义（上限 + 全量数存在）而非写死数值：
        #   2026-09-20 清库前写死 1484 → 清库后假失败；改为直接 COUNT(*) 后又因软删口径
        #   与页面差 3 条再次假失败。此处改为格式断言，与数据量解耦。
        import re
        assert "100" in notice_text and re.search(r"全量样本共\s*\d+\s*份", notice_text), (
            f"Unexpected notice text: {notice_text}"
        )

        # Step 5: Verify rendered rows count capped at 100
        rows = page.locator("#adminGreySamplesBody tr")
        assert rows.count() == 100, f"Expected 100 rows, got {rows.count()}"

        # Step 6: Verify table layout and cell truncation (zero truncated cells!)
        truncation_check = page.evaluate("""() => {
            const table = document.getElementById('adminGreySamplesTable');
            if (!table) return [{ error: 'Table not found' }];
            const cells = table.querySelectorAll('tbody td');
            const truncated = [];
            cells.forEach(cell => {
                // Allow 1px browser subpixel difference
                if (cell.scrollWidth > cell.clientWidth + 1) {
                    truncated.push({
                        text: cell.innerText.trim(),
                        scrollWidth: cell.scrollWidth,
                        clientWidth: cell.clientWidth,
                        className: cell.className
                    });
                }
            });
            return truncated;
        }""")
        assert len(truncation_check) == 0, f"Found {len(truncation_check)} truncated cells: {truncation_check[:5]}"

        # Step 7: Verify specific problematic columns are fully visible
        first_vendor = page.locator("#adminGreySamplesBody tr:first-child td.cell-vendor").inner_text()
        assert "脱敏" in first_vendor, f"Expected vendor info in first row, got: {first_vendor}"

        # Step 8: Save screenshot
        shot_path = os.path.join(SCREENSHOT_DIR, "canary_table_fixed.png")
        page.screenshot(path=shot_path, full_page=False)
        print(f"Screenshot saved to {shot_path}")

        browser.close()


if __name__ == "__main__":
    test_canary_table_no_truncation_and_no_duplicate_requests()

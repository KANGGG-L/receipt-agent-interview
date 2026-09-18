# -*- coding: utf-8 -*-
import os
import sys
import time
import socket
import subprocess
from urllib.parse import urlparse
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DEMO_DIR = os.path.join(ROOT_DIR, "demo")
SCREENSHOT_DIR = os.path.join(DEMO_DIR, "gui-test-screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def is_service_reachable(timeout=1.0):
    parsed = urlparse(BASE_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_server():
    if is_service_reachable():
        return None
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "15010"],
        cwd=DEMO_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    start = time.time()
    while time.time() - start < 15:
        if is_service_reachable():
            return proc
        time.sleep(0.4)
    return proc


def switch_demo_role(page, target_role):
    current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
    if current_role == target_role:
        return
    with page.expect_navigation(timeout=15000):
        page.select_option("#demoRoleSelect", target_role)
    page.wait_for_selector("#demoRoleSelect", timeout=10000)
    page.wait_for_timeout(400)


def test_canary_table_no_truncation_and_no_duplicate_requests():
    server_proc = ensure_server()
    try:
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
            assert "100" in notice_text and "1484" in notice_text, (
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
    finally:
        if server_proc:
            server_proc.terminate()
            server_proc.wait()


if __name__ == "__main__":
    test_canary_table_no_truncation_and_no_duplicate_requests()

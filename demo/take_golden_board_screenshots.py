import os
import sys
import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010/"
SCREENSHOT_DIR = "demo/gui-test-screenshots"

def switch_demo_role(page, target_role):
    current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
    if current_role == target_role:
        return
    with page.expect_navigation(timeout=15000):
        page.select_option("#demoRoleSelect", target_role)
    page.wait_for_selector("#demoRoleSelect", timeout=10000)
    page.wait_for_timeout(400)

def main():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1050})
        page = context.new_page()

        print(f"Navigating to {BASE_URL}...")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#demoRoleSelect", timeout=12000)

        # Switch to admin
        print("Switching role to admin...")
        switch_demo_role(page, "admin")

        # Click AI 效果观测与评测中心
        print("Opening AI 效果观测与评测中心 (#analyticsBoardBtn)...")
        page.locator("#analyticsBoardBtn").click()
        page.wait_for_selector("#tab-analytics.active", timeout=10000)

        # Click subview 03: 基准资产与 GT 确权
        print("Clicking subview 03 (#btnSubViewEval)...")
        page.locator("#btnSubViewEval").click()
        page.wait_for_selector("#sec-eval", state="visible", timeout=5000)

        # Wait for golden board table rows to render
        print("Waiting for goldenBoardBody table rows...")
        page.wait_for_selector("#goldenBoardBody tr:not(:has(td[colspan]))", timeout=10000)
        page.wait_for_timeout(1000)

        # Screenshot 1: All
        shot_all = os.path.join(SCREENSHOT_DIR, "golden_board_redesign_all.png")
        card = page.locator("#sec-eval .card:has(#goldenBoardBody)")
        card.screenshot(path=shot_all)
        print(f"Saved: {shot_all}")

        # Screenshot 2: gt_confirmed
        print("Switching scope to gt_confirmed...")
        page.select_option("#goldenScopeSelect", "gt_confirmed")
        page.wait_for_selector("#goldenBoardBody tr:not(:has(td[colspan]))", timeout=10000)
        page.wait_for_timeout(500)
        shot_confirmed = os.path.join(SCREENSHOT_DIR, "golden_board_redesign_confirmed.png")
        card.screenshot(path=shot_confirmed)
        print(f"Saved: {shot_confirmed}")

        # Screenshot 3: gt_pending
        print("Switching scope to gt_pending...")
        page.select_option("#goldenScopeSelect", "gt_pending")
        page.wait_for_selector("#goldenBoardBody tr:not(:has(td[colspan]))", timeout=10000)
        page.wait_for_timeout(500)
        shot_pending = os.path.join(SCREENSHOT_DIR, "golden_board_redesign_pending.png")
        card.screenshot(path=shot_pending)
        print(f"Saved: {shot_pending}")

        # Screenshot 4: golden
        print("Switching scope to golden...")
        page.select_option("#goldenScopeSelect", "golden")
        page.wait_for_selector("#goldenBoardBody tr:not(:has(td[colspan]))", timeout=10000)
        page.wait_for_timeout(500)
        shot_golden = os.path.join(SCREENSHOT_DIR, "golden_board_redesign_golden.png")
        card.screenshot(path=shot_golden)
        print(f"Saved: {shot_golden}")

        # Full view screenshot
        shot_full = os.path.join(SCREENSHOT_DIR, "golden_board_redesign_full.png")
        page.screenshot(path=shot_full, full_page=False)
        print(f"Saved: {shot_full}")

        browser.close()
        print("Completed screenshot capture successfully.")

if __name__ == "__main__":
    main()

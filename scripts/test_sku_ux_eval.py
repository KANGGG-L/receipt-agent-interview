import os
import time
import json
from playwright.sync_api import sync_playwright, expect

SCREENSHOT_DIR = "/Users/ethan/Documents/GitHub/receipt-agent-interview/artifacts/sku_ux_eval"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
BASE_URL = "http://127.0.0.1:15010"

def run_eval():
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print("1. Opening demo page...")
        page.goto(BASE_URL)
        page.wait_for_timeout(1000)
        
        page.screenshot(path=f"{SCREENSHOT_DIR}/01_homepage.png")
        print("Homepage screenshot taken.")

        # Click "新建手工单" to enter form edit mode
        print("2. Entering Manual Entry Mode...")
        manual_btn = page.locator("#btnNewManualEntry")
        if manual_btn.is_visible():
            manual_btn.click()
        else:
            page.evaluate("startManualEntry();")
        
        page.wait_for_selector("#itemTableBody tr", timeout=5000)
        page.wait_for_timeout(600)
        page.screenshot(path=f"{SCREENSHOT_DIR}/02_edit_mode_initial.png")

        rows = page.locator("#itemTableBody tr")
        print(f"Table row count: {rows.count()}")

        # -------------------------------------------------------------
        # TEST CASE 1: Type-as-you-go Auto-Match (已有食材: 白菜)
        # -------------------------------------------------------------
        print("\n--- TEST CASE 1: Type-as-you-go Auto-Match (已有食材: 白菜) ---")
        first_row = rows.first
        name_input = first_row.locator(".inp-name")
        pill = first_row.locator(".sku-pill")
        id_input = first_row.locator(".inp-sku-id")

        print(f"Initial pill text: '{pill.inner_text().strip()}', class: '{pill.get_attribute('class')}'")

        # Type '白菜' and measure time to green capsule switch
        t0 = time.time()
        name_input.click()
        name_input.fill("")
        name_input.type("白菜", delay=50)

        # Wait for pill to switch to matched state
        page.wait_for_function(
            "document.querySelector('#itemTableBody tr .sku-pill').classList.contains('sku-pill-matched') && document.querySelector('#itemTableBody tr .sku-pill').innerText.includes('白菜')",
            timeout=3000
        )
        t_match = (time.time() - t0) * 1000

        matched_pill_class = pill.get_attribute("class")
        matched_pill_text = pill.inner_text().strip()
        matched_sku_id = id_input.input_value()

        print(f"-> Auto-Match SUCCESS in {t_match:.1f}ms!")
        print(f"   Pill class: {matched_pill_class}")
        print(f"   Pill text: {matched_pill_text}")
        print(f"   Hidden SKU ID: {matched_sku_id}")

        page.wait_for_timeout(300)
        page.screenshot(path=f"{SCREENSHOT_DIR}/03_automatch_baicai.png")

        results["test_case_1_auto_match"] = {
            "name": "输入已有食材名即时静默关联",
            "input_name": "白菜",
            "target_sku_id": "1",
            "actual_sku_id": matched_sku_id,
            "pill_class": matched_pill_class,
            "pill_text": matched_pill_text,
            "latency_ms": round(t_match, 1),
            "passed": ("sku-pill-matched" in matched_pill_class and "白菜" in matched_pill_text and matched_sku_id == "1"),
            "extra_clicks_needed": 0
        }

        # -------------------------------------------------------------
        # TEST CASE 2: Zero-Modal 1-Click Fast Create (新食材: 极品野生大黄鱼)
        # -------------------------------------------------------------
        print("\n--- TEST CASE 2: Zero-Modal 1-Click Fast Create (新食材: 极品野生大黄鱼) ---")
        # Add second row
        page.evaluate("appendTableRow({ raw_name: '', quantity: 2.0, raw_unit: '条', unit_price: 388.0, amount: 776.0 });")
        page.wait_for_timeout(300)

        rows = page.locator("#itemTableBody tr")
        second_row = rows.nth(1)
        name_input2 = second_row.locator(".inp-name")
        pill2 = second_row.locator(".sku-pill")
        id_input2 = second_row.locator(".inp-sku-id")
        menu2 = second_row.locator(".unit-dropdown-menu")

        new_ingredient = f"极品野生大黄鱼_{int(time.time()) % 10000}"
        name_input2.click()
        name_input2.fill(new_ingredient)
        page.wait_for_timeout(300) # wait debounce

        # Verify initial state is unlinked
        pill2_class_before = pill2.get_attribute("class")
        print(f"Before click pill class: {pill2_class_before}")

        # Click pill capsule to toggle menu
        pill2.click()
        page.wait_for_timeout(200)

        # Check search input focus and dropdown menu appearance
        search_focused = page.evaluate("document.activeElement === document.querySelectorAll('#itemTableBody tr')[1].querySelector('.inp-sku-search')")
        print(f"Is dropdown search input auto-focused on pill click? {search_focused}")
        page.screenshot(path=f"{SCREENSHOT_DIR}/04_fast_create_dropdown_open.png")

        quick_add_item = menu2.locator(".sku-quick-add-item")
        quick_add_text = quick_add_item.inner_text().strip()
        print(f"Dropdown quick add option: {quick_add_text}")

        # Check that no modal overlay is present
        modal_visible_before = page.locator("#skuModal").is_visible()
        print(f"Is traditional SKU modal visible before click? {modal_visible_before}")

        # 1-Click fast create
        t_create_start = time.time()
        quick_add_item.click()

        # Wait for pill to turn green matched and menu to close
        page.wait_for_function(
            f"document.querySelectorAll('#itemTableBody tr')[1].querySelector('.sku-pill').classList.contains('sku-pill-matched')",
            timeout=3000
        )
        t_create_duration = (time.time() - t_create_start) * 1000

        modal_visible_after = page.locator("#skuModal").is_visible()
        pill2_class_after = pill2.get_attribute("class")
        pill2_text_after = pill2.inner_text().strip()
        new_created_sku_id = id_input2.input_value()

        print(f"-> 1-Click Fast Create SUCCESS in {t_create_duration:.1f}ms!")
        print(f"   Pill class: {pill2_class_after}")
        print(f"   Pill text: {pill2_text_after}")
        print(f"   New SKU ID: {new_created_sku_id}")
        print(f"   Was modal displayed? {modal_visible_after} (Expected: False, Zero-Modal)")

        page.wait_for_timeout(300)
        page.screenshot(path=f"{SCREENSHOT_DIR}/05_post_fast_create.png")

        results["test_case_2_fast_create"] = {
            "name": "1-Click 极速一键秒建档（Zero-Modal 1-Click Fast Create）",
            "ingredient_name": new_ingredient,
            "created_sku_id": new_created_sku_id,
            "pill_class": pill2_class_after,
            "pill_text": pill2_text_after,
            "latency_ms": round(t_create_duration, 1),
            "zero_modal_verified": (not modal_visible_after),
            "search_auto_focused": search_focused,
            "passed": ("sku-pill-matched" in pill2_class_after and new_ingredient in pill2_text_after and bool(new_created_sku_id) and not modal_visible_after and t_create_duration < 1000)
        }

        # -------------------------------------------------------------
        # TEST CASE 3: Keyboard Flow (Enter to select / Esc to close / Unlink)
        # -------------------------------------------------------------
        print("\n--- TEST CASE 3: Keyboard Flow & Unlink Option ---")
        page.evaluate("appendTableRow({ raw_name: '', quantity: 3.0, raw_unit: '斤', unit_price: 25.0, amount: 75.0 });")
        page.wait_for_timeout(300)

        rows = page.locator("#itemTableBody tr")
        third_row = rows.nth(2)
        pill3 = third_row.locator(".sku-pill")
        id_input3 = third_row.locator(".inp-sku-id")
        menu3 = third_row.locator(".unit-dropdown-menu")

        # 1. Click pill to open menu
        pill3.click()
        page.wait_for_timeout(200)
        search3 = menu3.locator(".inp-sku-search")

        # 2. Type "红虾" in search input and press Enter
        search3.fill("红虾")
        page.wait_for_timeout(300)
        page.screenshot(path=f"{SCREENSHOT_DIR}/06_keyboard_search_type.png")

        search3.press("Enter")
        page.wait_for_timeout(300)

        pill3_class_selected = pill3.get_attribute("class")
        pill3_text_selected = pill3.inner_text().strip()
        pill3_id_selected = id_input3.input_value()
        print(f"After Enter key: pill='{pill3_text_selected}', id={pill3_id_selected}")

        # 3. Re-open and click '✕ 设为未关联 (临时消费单)'
        pill3.click()
        page.wait_for_timeout(200)
        unlink_btn = menu3.locator(".unit-dropdown-item:has-text('设为未关联')")
        unlink_btn.click()
        page.wait_for_timeout(200)

        pill3_class_unlinked = pill3.get_attribute("class")
        pill3_text_unlinked = pill3.inner_text().strip()
        pill3_id_unlinked = id_input3.input_value()
        print(f"After Unlink: pill='{pill3_text_unlinked}', class='{pill3_class_unlinked}', id='{pill3_id_unlinked}'")
        page.screenshot(path=f"{SCREENSHOT_DIR}/07_unlinked_pill_state.png")

        results["test_case_3_keyboard_and_unlink"] = {
            "name": "极速键盘流与解绑体验",
            "enter_selected_text": pill3_text_selected,
            "enter_selected_id": pill3_id_selected,
            "unlinked_class": pill3_class_unlinked,
            "unlinked_text": pill3_text_unlinked,
            "unlinked_id": pill3_id_unlinked,
            "passed": ("红虾" in pill3_text_selected and pill3_id_selected == "10" and "sku-pill-unlinked" in pill3_class_unlinked and pill3_id_unlinked == "")
        }

        # Full view screenshot
        page.screenshot(path=f"{SCREENSHOT_DIR}/08_full_page_view.png", full_page=True)
        
        browser.close()

    print("\n==========================================")
    print("ALL TEST SCENARIOS EXECUTION FINISHED:")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print("==========================================")
    return results

if __name__ == "__main__":
    run_eval()

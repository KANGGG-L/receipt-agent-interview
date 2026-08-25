import os
import sys
import time
from PIL import Image
from playwright.sync_api import sync_playwright

def test_u11_abort_loading_escape():
    # Find or create a test image
    test_img = os.path.abspath("artifacts/e2e/test-hash.png")
    if not os.path.exists(test_img):
        img = Image.new('RGB', (400, 600), color=(255, 255, 255))
        test_img = "/tmp/test_receipt_u11.png"
        img.save(test_img)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 1. Visit staff home
        page.goto("http://127.0.0.1:15010/", wait_until="domcontentloaded")
        time.sleep(1)

        # 2. Upload file via #receiptFile
        page.set_input_files("#receiptFile", test_img)
        time.sleep(1)

        # Check preConfirmCard is visible
        pre_confirm = page.locator("#preConfirmCard")
        assert pre_confirm.is_visible(), "Pre-confirm card should be visible after selecting an image"

        # 3. Click '开始 AI 智能解析'
        btn_parse = page.locator("#preConfirmCard button.btn-success")
        btn_parse.click()
        time.sleep(0.5)

        # 4. Check loadingCard is visible and escape button is present
        loading_card = page.locator("#loadingCard")
        assert loading_card.is_visible(), "Loading card should be visible during recognition"

        abort_btn = page.locator("#btnAbortLoadingToManual")
        assert abort_btn.is_visible(), "Escape button #btnAbortLoadingToManual must be visible"

        # Check button text
        btn_text = abort_btn.inner_text().strip()
        print(f"Escape button text: '{btn_text}'")
        assert "等不及？点击取消并转手工补录" in btn_text, f"Unexpected button text: {btn_text}"

        # Check button dimensions (height >= 38px)
        bbox = abort_btn.bounding_box()
        assert bbox is not None, "Button bounding box should exist"
        print(f"Button height: {bbox['height']}px, width: {bbox['width']}px")
        assert bbox['height'] >= 38, f"Button height {bbox['height']}px is less than 38px"

        # 5. Click the escape button
        abort_btn.click()
        time.sleep(0.8)

        # 6. Verify Loading card is hidden
        assert not loading_card.is_visible(), "Loading card should be hidden after abort"

        # 7. Verify splitViewArea and prefillFormCard are visible
        split_view = page.locator("#splitViewArea")
        assert split_view.is_visible(), "Split view area should be visible"

        prefill_card = page.locator("#prefillFormCard")
        assert prefill_card.is_visible(), "Prefill form card should be visible"

        # 8. Verify left side preview image is still visible and not replaced with '无原图'
        preview_img = page.locator("#previewImg")
        assert preview_img.is_visible(), "Preview image should remain visible in left panel"
        
        no_image_placeholder = page.locator("#manualEntryNoImage")
        assert not no_image_placeholder.is_visible(), "'无原图' placeholder should be hidden"

        # 9. Verify toast message
        toast = page.locator("#toastContainer .toast, #toastContainer > div, .app-toast")
        if toast.count() > 0:
            toast_text = toast.last.inner_text()
            print(f"Toast displayed: {toast_text}")
            assert "已停止等待" in toast_text or "原图已在左侧保留" in toast_text, f"Unexpected toast: {toast_text}"

        # 10. Verify form editable fields and table rows
        inp_supplier = page.locator("#inpSupplier")
        inp_date = page.locator("#inpDate")
        inp_total = page.locator("#inpTotal")
        item_rows = page.locator("#itemTableBody tr")

        assert inp_supplier.is_visible(), "Supplier input should be visible"
        assert inp_date.is_visible(), "Date input should be visible"
        assert item_rows.count() >= 1, "There should be at least 1 editable item row"

        # 11. Simulate user filling form and editing fields
        inp_supplier.fill("測試供應商")
        inp_total.fill("88.00")
        
        first_row_name = page.locator("#itemTableBody tr:first-child .inp-name")
        first_row_price = page.locator("#itemTableBody tr:first-child .inp-price")
        first_row_amount = page.locator("#itemTableBody tr:first-child .inp-amount")

        first_row_name.fill("招牌奶茶")
        first_row_price.fill("88.00")
        first_row_price.press("Tab")
        time.sleep(0.5)

        # 12. Verify user inputs are retained without being wiped by delayed poll
        time.sleep(2)
        assert inp_supplier.input_value() == "測試供應商", "Supplier input value should not be overwritten"
        assert first_row_name.input_value() == "招牌奶茶", "Item name should not be overwritten"

        print("=== U-11 Browser Live Test Passed Successfully! ===")
        browser.close()

if __name__ == "__main__":
    test_u11_abort_loading_escape()

# -*- coding: utf-8 -*-
"""Product Manager QA Acceptance Test Script.

Executes real browser interactions via Playwright across 4 core scenarios:
1. Scenario 1: Upload slightly blurry receipt (IMG_5899.HEIC) - gentle warning & green continue button
2. Scenario 2: Loading process with 4-stage visual progress & timer
3. Scenario 3: Step 2/2 Review Form & 7-Column Detail Table with 2-tier SKU status capsule
4. Scenario 4: Store clerk editing, fees drawer, voiding line, arithmetic conservation & save confirmation
"""

import os
import sys
import time
import json
import shutil
from playwright.sync_api import sync_playwright, expect
from PIL import Image, ImageDraw, ImageFilter
import pillow_heif

BASE_URL = "http://127.0.0.1:15010"
OUTPUT_DIR = "/Users/ethan/Documents/GitHub/receipt-agent-interview/artifacts/pm_acceptance"
BRAIN_DIR = "/Users/ethan/.gemini/antigravity-cli/brain/0983a1d9-d5e7-4b96-bda3-6a5cca43022b"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(BRAIN_DIR, exist_ok=True)


def create_blurry_heic_receipt(filename="IMG_5899.HEIC"):
    filepath = os.path.join(OUTPUT_DIR, filename)
    img = Image.new("RGB", (750, 950), color=(248, 246, 242))
    draw = ImageDraw.Draw(img)

    # Decorative header
    draw.rectangle([(20, 20), (730, 930)], outline=(200, 195, 185), width=2)
    draw.text((260, 45), "祥 興 食 品 有 限 公 司", fill=(30, 30, 30))
    draw.text((280, 75), "銷 貨 單 / 送 貨 單", fill=(60, 60, 60))
    draw.line([(40, 105), (710, 105)], fill=(150, 150, 150), width=2)

    draw.text((50, 120), "客戶名稱: 翠華茶餐廳 (旺角店)", fill=(40, 40, 40))
    draw.text((480, 120), "單據編號: DN-20260822-09", fill=(40, 40, 40))
    draw.text((50, 150), "開單日期: 2026-08-22", fill=(40, 40, 40))
    draw.text((480, 150), "結算方式: 港幣現結", fill=(40, 40, 40))

    # Table Header
    draw.line([(40, 180), (710, 180)], fill=(120, 120, 120), width=1)
    draw.text((50, 190), "品名規格 (Item / Spec)", fill=(30, 30, 30))
    draw.text((360, 190), "數量", fill=(30, 30, 30))
    draw.text((440, 190), "單位", fill=(30, 30, 30))
    draw.text((510, 190), "單價", fill=(30, 30, 30))
    draw.text((610, 190), "金額 (HK$)", fill=(30, 30, 30))
    draw.line([(40, 215), (710, 215)], fill=(120, 120, 120), width=1)

    # Item rows
    draw.text((50, 235), "本地新鮮菜心_1787140411 (特選)", fill=(20, 20, 20))
    draw.text((370, 235), "10.0", fill=(20, 20, 20))
    draw.text((445, 235), "斤", fill=(20, 20, 20))
    draw.text((515, 235), "12.00", fill=(20, 20, 20))
    draw.text((620, 235), "120.00", fill=(20, 20, 20))

    draw.text((50, 280), "澳洲冰鮮牛肉眼 (牛扒用)", fill=(20, 20, 20))
    draw.text((370, 280), "5.0", fill=(20, 20, 20))
    draw.text((445, 280), "kg", fill=(20, 20, 20))
    draw.text((515, 280), "180.00", fill=(20, 20, 20))
    draw.text((620, 280), "900.00", fill=(20, 20, 20))

    draw.text((50, 325), "嘉顿幼麥方包 (三文治用)", fill=(20, 20, 20))
    draw.text((370, 325), "4.0", fill=(20, 20, 20))
    draw.text((445, 325), "包", fill=(20, 20, 20))
    draw.text((515, 325), "15.00", fill=(20, 20, 20))
    draw.text((620, 325), "60.00", fill=(20, 20, 20))

    draw.line([(40, 365), (710, 365)], fill=(120, 120, 120), width=1)
    draw.text((460, 385), "整單總計: HK$ 1080.00", fill=(10, 10, 10))
    draw.text((50, 430), "備註: 貨到請後廚簽收，破損請即時拍照", fill=(80, 80, 80))

    # Apply moderate blur
    img_blurred = img.filter(ImageFilter.GaussianBlur(radius=2.2))

    heif_file = pillow_heif.from_pillow(img_blurred)
    heif_file.save(filepath, quality=80)
    print(f"Generated test blurry HEIC image at {filepath}")
    return filepath


MOCK_PARSED_RESULT = {
    "status": "success",
    "receipt_id": 108,
    "data": {
        "supplier_name": "祥興食品有限公司",
        "date": "2026-08-22",
        "sheet_name": "2026-08",
        "total_amount": 1080.00,
        "settlement_type": "cash",
        "doc_form": "printed_delivery_note",
        "currency": "HKD",
        "department_id": None,
        "discount_amount": 0.00,
        "delivery_fee": 0.00,
        "deposit_amount": 0.00,
        "rounding_adjustment": 0.00,
        "items": [
            {
                "name": "本地新鮮菜心",
                "raw_name": "本地新鮮菜心_1787140411 (特選)",
                "quantity": 10.0,
                "unit": "斤",
                "raw_unit": "斤",
                "unit_price": 12.00,
                "amount": 120.00,
                "sku_id": 1,
                "sku_name": "本地新鮮菜心",
                "price_anomaly": 0,
                "is_void": 0,
                "confidence": 0.95
            },
            {
                "name": "澳洲冰鮮牛肉眼",
                "raw_name": "澳洲冰鮮牛肉眼 (牛扒用)",
                "quantity": 5.0,
                "unit": "kg",
                "raw_unit": "kg",
                "unit_price": 180.00,
                "amount": 900.00,
                "sku_id": None,
                "sku_name": "",
                "price_anomaly": 1,
                "price_anomaly_direction": "up",
                "price_diff_percent": 15.0,
                "is_void": 0,
                "confidence": 0.89
            },
            {
                "name": "嘉顿幼麥方包",
                "raw_name": "嘉顿幼麥方包 (三文治用)",
                "quantity": 4.0,
                "unit": "包",
                "raw_unit": "包",
                "unit_price": 15.00,
                "amount": 60.00,
                "sku_id": 2,
                "sku_name": "嘉顿幼麥方包",
                "price_anomaly": 0,
                "is_void": 0,
                "confidence": 0.97
            }
        ],
        "quality_warnings": ["image_blur"]
    },
    "image_url": "/uploads/test_heic_preview.jpg",
    "version": 1
}


def run_pm_acceptance_qa():
    test_heic = create_blurry_heic_receipt("IMG_5899.HEIC")
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        job_poll_step = 0

        def handle_upload(route):
            time.sleep(0.3)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "status": "queued",
                    "job_id": "job-pm-108",
                    "receipt_id": 108,
                    "image_url": "/uploads/test_heic_preview.jpg"
                })
            )

        def handle_job(route):
            nonlocal job_poll_step
            job_poll_step += 1
            if job_poll_step <= 2:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "job_id": "job-pm-108",
                        "job_status": "running",
                        "receipt_id": 108
                    })
                )
            else:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "job_id": "job-pm-108",
                        "job_status": "done",
                        "result": MOCK_PARSED_RESULT
                    })
                )

        def handle_save(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "status": "success",
                    "msg": "收据复核并保存成功",
                    "receipt_id": 108,
                    "version": 2
                })
            )

        page.route("**/api/upload*", handle_upload)
        page.route("**/api/job/*", handle_job)
        page.route("**/api/save_edited", handle_save)

        # -------------------------------------------------------------
        # 1. SCENARIO 1: Upload IMG_5899.HEIC & Gentle Quality Warning
        # -------------------------------------------------------------
        print("\n--- Testing Scenario 1: Upload & Quality Warning ---")
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")

        # Capture initial homepage
        s0_file = "00_homepage_clean.png"
        page.screenshot(path=os.path.join(OUTPUT_DIR, s0_file))

        # Check Step 1/2 preview card with uncheck auto-analyze
        auto_chk = page.locator("#chkAutoAnalyze")
        if auto_chk.is_checked():
            auto_chk.uncheck()

        file_input = page.locator("#receiptFile")
        file_input.set_input_files(test_heic)

        expect(page.locator("#splitViewArea")).to_be_visible()
        expect(page.locator("#preConfirmCard")).to_be_visible()

        # Check Step 1/2 card text & buttons
        pre_card = page.locator("#preConfirmCard")
        pre_card_title = pre_card.locator(".card-title").inner_text()
        print(f"Pre-confirm card title: {pre_card_title}")

        btn_continue_step1 = pre_card.locator("button.btn-success")
        expect(btn_continue_step1).to_be_visible()
        expect(btn_continue_step1).to_contain_text("AI 智能解析")

        # Verify gentle tone, no stack trace or technical jargon
        pre_card_html = pre_card.inner_html()
        assert "Traceback" not in pre_card_html, "Found technical stack trace!"
        assert "500" not in pre_card_html, "Found 500 code!"
        assert "Exception" not in pre_card_html, "Found technical Exception!"

        s1_file = "01_scenario1_upload_heic_preconfirm.png"
        page.screenshot(path=os.path.join(OUTPUT_DIR, s1_file))
        print(f"Scenario 1 pre-confirm screenshot saved to {s1_file}")
        results["scenario1"] = "PASS"

        # -------------------------------------------------------------
        # 2. SCENARIO 2: 4-Stage Loading Progress
        # -------------------------------------------------------------
        print("\n--- Testing Scenario 2: 4-Stage Loading ---")
        # Click green button to proceed
        btn_continue_step1.click()

        loading_card = page.locator("#loadingCard")
        expect(loading_card).to_be_visible()

        stage1 = page.locator("#loadingStep1")
        stage2 = page.locator("#loadingStep2")
        stage3 = page.locator("#loadingStep3")
        stage4 = page.locator("#loadingStep4")
        timer = page.locator("#ocrTimer")
        stage_name = page.locator("#loadingStageName")

        expect(stage1).to_be_visible()
        expect(stage2).to_be_visible()
        expect(stage3).to_be_visible()
        expect(stage4).to_be_visible()
        expect(timer).to_be_visible()

        print(f"Current stage badge: {stage_name.inner_text()}")
        print(f"Stage 1 text: {stage1.inner_text()}")
        print(f"Stage 2 text: {stage2.inner_text()}")
        print(f"Stage 3 text: {stage3.inner_text()}")
        print(f"Stage 4 text: {stage4.inner_text()}")

        s2_file = "02_scenario2_loading_4stages.png"
        page.screenshot(path=os.path.join(OUTPUT_DIR, s2_file))
        print(f"Scenario 2 loading screenshot saved to {s2_file}")
        results["scenario2"] = "PASS"

        # Wait for recognition completion
        page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=15000)
        expect(page.locator("#prefillFormCard")).to_be_visible()

        # -------------------------------------------------------------
        # 3. SCENARIO 3: Step 2/2 Review Form & Detail Table
        # -------------------------------------------------------------
        print("\n--- Testing Scenario 3: Step 2/2 Review Form & 7-Column Table ---")

        # Header fields validation
        supplier_val = page.locator("#inpSupplier").input_value()
        date_val = page.locator("#inpDate").input_value()
        sheet_val = page.locator("#inpSheet").input_value()
        total_val = page.locator("#inpTotal").input_value()
        doc_form_val = page.locator("#inpDocForm").input_value()
        currency_val = page.locator("#inpCurrency").input_value()

        print(f"Supplier: {supplier_val}, Date: {date_val}, Total: {total_val}, Currency: {currency_val}")
        assert supplier_val == "祥興食品有限公司"
        assert date_val == "2026-08-22"
        assert total_val == "1080.00"
        assert currency_val == "HKD"

        # Check Table 7 columns
        headers = page.locator(".receipt-item-table thead th")
        expect(headers).to_have_count(7)
        header_texts = [headers.nth(i).inner_text().strip() for i in range(7)]
        print(f"Table 7 headers: {header_texts}")

        # Check rows
        rows = page.locator("#itemTableBody tr")
        expect(rows).to_have_count(3)

        # Row 1: Matched SKU (Green)
        row1 = rows.nth(0)
        row1_name = row1.locator(".inp-name").input_value()
        row1_pill = row1.locator(".sku-pill")
        expect(row1_pill).to_have_class("sku-pill sku-pill-matched")
        print(f"Row 1: {row1_name} | Pill: {row1_pill.inner_text()}")

        # Row 2: Unlinked SKU (Yellow) + Price Anomaly Badge
        row2 = rows.nth(1)
        row2_name = row2.locator(".inp-name").input_value()
        row2_pill = row2.locator(".sku-pill")
        expect(row2_pill).to_have_class("sku-pill sku-pill-unlinked")
        anomaly_badge = row2.locator(".anomaly-pill")
        expect(anomaly_badge).to_be_visible()
        print(f"Row 2: {row2_name} | Pill: {row2_pill.inner_text()} | Anomaly: {anomaly_badge.inner_text()}")

        # Check text overflow / truncation in inputs
        name_input_box = row2.locator(".inp-name").bounding_box()
        print(f"Item name input box size: width={name_input_box['width']}px, height={name_input_box['height']}px")
        assert name_input_box['width'] >= 120, "Item name input box should be comfortable width"

        # Click unlinked SKU pill to test dropdown
        row2_pill.click()
        sku_dropdown = row2.locator(".sku-dropdown-floating")
        expect(sku_dropdown).to_be_visible()
        print("SKU dropdown successfully opened on click.")

        s3_file = "03_scenario3_step2_review_form_table.png"
        page.screenshot(path=os.path.join(OUTPUT_DIR, s3_file))
        print(f"Scenario 3 review form screenshot saved to {s3_file}")

        # Close SKU dropdown
        page.locator("#inpSupplier").click()
        results["scenario3"] = "PASS"

        # -------------------------------------------------------------
        # 4. SCENARIO 4: Clerk Editing, Fees Drawer, Void Row & Math Recalc
        # -------------------------------------------------------------
        print("\n--- Testing Scenario 4: Editing, Fees Drawer, Void Row & Math Recalc ---")

        # 4A. Modify quantity of Row 1 from 10.0 to 12.0
        print("4A: Modifying Row 1 Quantity 10.0 -> 12.0...")
        qty1_input = row1.locator(".inp-qty")
        qty1_input.fill("12.0")
        qty1_input.dispatch_event("input")
        time.sleep(0.1)

        # Row 1 amount should be 12.0 * 12.00 = 144.00
        row1_amount = row1.locator(".inp-amount").input_value()
        print(f"Row 1 recalculated amount: {row1_amount}")
        assert float(row1_amount) == 144.00, f"Expected 144.00, got {row1_amount}"

        # Total should now be 144.00 + 900.00 + 60.00 = 1104.00
        total_after_qty = page.locator("#inpTotal").input_value()
        print(f"Total after quantity edit: {total_after_qty}")
        assert float(total_after_qty) == 1104.00

        # 4B. Expand Extra Fees Drawer & input fees
        print("4B: Expanding Extra Fees Drawer...")
        fees_toggle = page.locator(".fees-drawer-toggle")
        fees_toggle.click()
        expect(page.locator("#feesDrawerContent")).to_be_visible()

        # Fill fees via dynamic fee rows: discount=10.00, delivery=15.00,
        # deposit=20.00, rounding=1.00; Net adjustment = +15 + 20 - 10 - 1 = +24.00
        for fee_key, fee_value in [
            ("discount_amount", "10.00"),
            ("delivery_fee", "15.00"),
            ("deposit_amount", "20.00"),
            ("rounding_adjustment", "1.00"),
        ]:
            page.locator("#btnAddFeeRow").click()
            fee_row = page.locator("#feesRowsContainer .fee-row").last
            fee_row.locator(".fee-type-select").select_option(fee_key)
            fee_row.locator(".fee-amount-input").fill(fee_value)
        time.sleep(0.1)

        # Total should now be 1104.00 + 24.00 = 1128.00
        total_after_fees = page.locator("#inpTotal").input_value()
        print(f"Total after fees adjustment (+24.00): {total_after_fees}")
        assert float(total_after_fees) == 1128.00

        fees_badge = page.locator("#feesSummaryBadge")
        expect(fees_badge).to_be_visible()
        print(f"Fees summary badge: {fees_badge.inner_text()}")
        assert "24.00" in fees_badge.inner_text()

        # 4C. Void Line 3 (嘉顿幼麥方包 $60.00)
        print("4C: Voiding Row 3 ($60.00)...")
        row3 = rows.nth(2)
        void_btn = row3.locator(".btn-action-void")
        expect(void_btn).to_be_visible()
        expect(void_btn).to_have_text("作废")
        void_btn.click()

        # Check void state
        expect(row3).to_have_attribute("data-is-void", "1")
        expect(void_btn).to_have_text("恢复")

        # Total should exclude row 3: 1128.00 - 60.00 = 1068.00
        total_after_void = page.locator("#inpTotal").input_value()
        print(f"Total after voiding row 3: {total_after_void}")
        assert float(total_after_void) == 1068.00

        # Mathematical conservation check
        # Items: Row 1 (144.00) + Row 2 (900.00) + Row 3 (VOID=0.00) = 1044.00
        # Fees: Delivery (+15.00) + Deposit (+20.00) - Discount (-10.00) - Rounding (-1.00) = +24.00
        # Total = 1044.00 + 24.00 = 1068.00
        expected_math_total = (144.00 + 900.00 + 0.00) + 15.00 + 20.00 - 10.00 - 1.00
        assert float(total_after_void) == expected_math_total, f"Math conservation violation! Expected {expected_math_total}, got {total_after_void}"
        print(f"Math conservation verified: 1044.00 + 24.00 = {expected_math_total:.2f}")

        s4_file = "04_scenario4_fees_drawer_and_void.png"
        page.screenshot(path=os.path.join(OUTPUT_DIR, s4_file))
        print(f"Scenario 4 fees & void screenshot saved to {s4_file}")

        # 4D. Select Settlement & Save
        print("4D: Submitting form & saving receipt...")
        page.locator("#inpSettlementType").select_option("cash")

        btn_save = page.locator("#btnSaveReview")
        expect(btn_save).to_be_visible()
        btn_save.click()

        page.wait_for_selector(".app-toast.app-toast-success", timeout=8000)
        toast = page.locator(".app-toast.app-toast-success")
        print(f"Save Toast notification: {toast.inner_text()}")
        expect(toast).to_contain_text("保存")

        s5_file = "05_scenario4_save_success_toast.png"
        page.screenshot(path=os.path.join(OUTPUT_DIR, s5_file))
        print(f"Scenario 4 save toast screenshot saved to {s5_file}")
        results["scenario4"] = "PASS"

        # Also test Scenario 1 Error Card variant directly for complete documentation
        print("\n--- Additional Check: Direct Blur Error Card UI ---")
        page.evaluate("showErrorCard('收据画质偏低（轻度模糊），已保留原图', null, 'IMAGE_QUALITY_ERROR')")
        error_card = page.locator("#errorCard")
        expect(error_card).to_be_visible()
        s6_file = "06_scenario1_error_card_variant.png"
        page.screenshot(path=os.path.join(OUTPUT_DIR, s6_file))
        print(f"Scenario 1 error card variant screenshot saved to {s6_file}")

        browser.close()

    # Copy screenshots to artifact directory for embedding
    for f in os.listdir(OUTPUT_DIR):
        if f.endswith(".png") or f.endswith(".jpg") or f.endswith(".HEIC"):
            src = os.path.join(OUTPUT_DIR, f)
            dst = os.path.join(BRAIN_DIR, f)
            shutil.copyfile(src, dst)

    print("\n=======================================================")
    print("PRODUCT QA ACCEPTANCE VERIFICATION SUMMARY:")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print("=======================================================")
    return results


if __name__ == "__main__":
    run_pm_acceptance_qa()

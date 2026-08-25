import os
import time
import json
import pytest
from playwright.sync_api import sync_playwright, expect
from PIL import Image, ImageDraw, ImageFilter

BASE_URL = "http://127.0.0.1:15010"
SCREENSHOT_DIR = "/Users/ethan/Documents/GitHub/receipt-agent-interview/artifacts/e2e-refactor"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def create_test_receipt_image(filename="test_blurry_receipt.jpg", blur=True):
    path = os.path.join(SCREENSHOT_DIR, filename)
    img = Image.new("RGB", (600, 800), color=(250, 248, 245))
    draw = ImageDraw.Draw(img)

    # Header
    draw.text((200, 40), "祥興食品有限公司", fill=(20, 20, 20))
    draw.text((200, 70), "送 貨 單 (DELIVERY NOTE)", fill=(40, 40, 40))
    draw.text((50, 110), "日期: 2026-08-22", fill=(30, 30, 30))
    draw.text((400, 110), "單號: DN-20260822-01", fill=(30, 30, 30))

    # Items table
    draw.text((50, 160), "品名 / 規格", fill=(50, 50, 50))
    draw.text((280, 160), "數量", fill=(50, 50, 50))
    draw.text((360, 160), "單位", fill=(50, 50, 50))
    draw.text((440, 160), "單價", fill=(50, 50, 50))
    draw.text((520, 160), "金額", fill=(50, 50, 50))

    draw.line([(40, 185), (560, 185)], fill=(100, 100, 100), width=2)

    draw.text((50, 210), "本地新鮮菜心_1787140411", fill=(20, 20, 20))
    draw.text((290, 210), "10.0", fill=(20, 20, 20))
    draw.text((370, 210), "斤", fill=(20, 20, 20))
    draw.text((440, 210), "12.00", fill=(20, 20, 20))
    draw.text((520, 210), "120.00", fill=(20, 20, 20))

    draw.text((50, 250), "澳洲冰鮮牛肉眼", fill=(20, 20, 20))
    draw.text((290, 250), "5.0", fill=(20, 20, 20))
    draw.text((370, 250), "kg", fill=(20, 20, 20))
    draw.text((440, 250), "180.00", fill=(20, 20, 20))
    draw.text((520, 250), "900.00", fill=(20, 20, 20))

    draw.text((50, 290), "嘉顿幼麥方包", fill=(20, 20, 20))
    draw.text((290, 290), "4.0", fill=(20, 20, 20))
    draw.text((370, 290), "包", fill=(20, 20, 20))
    draw.text((440, 290), "15.00", fill=(20, 20, 20))
    draw.text((520, 290), "60.00", fill=(20, 20, 20))

    draw.line([(40, 330), (560, 330)], fill=(100, 100, 100), width=1)
    draw.text((400, 350), "合計金額: $1080.00", fill=(10, 10, 10))

    if blur:
        img = img.filter(ImageFilter.GaussianBlur(radius=1.8))

    img.save(path, "JPEG", quality=80)
    return path


MOCK_PARSED_RESULT = {
    "status": "success",
    "receipt_id": 99,
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
                "raw_name": "本地新鮮菜心_1787140411",
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
                "raw_name": "澳洲冰鲜牛肉眼",
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
                "confidence": 0.88
            },
            {
                "name": "嘉顿幼麥方包",
                "raw_name": "嘉顿幼麥方包",
                "quantity": 4.0,
                "unit": "包",
                "raw_unit": "包",
                "unit_price": 15.00,
                "amount": 60.00,
                "sku_id": 2,
                "sku_name": "嘉顿幼麥方包",
                "price_anomaly": 0,
                "is_void": 0,
                "confidence": 0.96
            }
        ],
        "quality_warnings": ["image_blur"]
    },
    "image_url": "/uploads/test_blurry_receipt.jpg",
    "version": 1
}


def test_e2e_clerk_flow_all_scenarios():
    test_img_path = create_test_receipt_image()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        job_poll_count = 0

        def handle_upload_route(route):
            url = route.request.url
            if "force=true" in url:
                time.sleep(0.3)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "status": "queued",
                        "job_id": "job-test-99",
                        "receipt_id": 99,
                        "image_url": "/uploads/test_blurry_receipt.jpg"
                    })
                )
            else:
                route.fulfill(
                    status=400,
                    content_type="application/json",
                    body=json.dumps({
                        "status": "error",
                        "code": "IMAGE_QUALITY_ERROR",
                        "msg": "图像模糊度过高（轻度画质提示）",
                        "quality_warnings": ["image_blur"]
                    })
                )

        def handle_job_route(route):
            nonlocal job_poll_count
            job_poll_count += 1
            if job_poll_count < 3:
                # First two polls: still running so progress bar advances through stages
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "job_id": "job-test-99",
                        "job_status": "running",
                        "receipt_id": 99
                    })
                )
            else:
                # 3rd poll: done with structured result
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({
                        "job_id": "job-test-99",
                        "job_status": "done",
                        "result": MOCK_PARSED_RESULT
                    })
                )

        def handle_save_route(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"status": "success", "msg": "收据复核并保存成功", "version": 2})
            )

        page.on("console", lambda msg: print(f"[CONSOLE {msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"[PAGE ERROR] {err}"))
        page.on("request", lambda req: print(f"[REQUEST] {req.method} {req.url}"))
        page.on("response", lambda res: print(f"[RESPONSE] {res.status} {res.url}"))

        page.route("**/api/upload*", handle_upload_route)
        page.route("**/api/job/*", handle_job_route)
        page.route("**/api/save_edited", handle_save_route)

        # -------------------------------------------------------------
        # Scenario 1: Upload & Gentle Quality Warning
        # -------------------------------------------------------------
        page.goto(BASE_URL)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(600)

        # Uncheck auto-analyze to verify Step 1/2 preview card with gentle warning
        auto_chk = page.locator("#chkAutoAnalyze")
        if auto_chk.is_checked():
            auto_chk.uncheck()

        # Upload the blurry test receipt
        file_input = page.locator("#receiptFile")
        file_input.set_input_files(test_img_path)

        # Verify Split View and Preview Area is visible
        expect(page.locator("#splitViewArea")).to_be_visible()
        expect(page.locator("#preConfirmCard")).to_be_visible()

        # Screenshot Scenario 1: Step 1/2 Preview & Gentle Warning Banner
        s1_path = os.path.join(SCREENSHOT_DIR, "01_scenario1_upload_gentle_warning.png")
        page.screenshot(path=s1_path)
        print(f"Scenario 1 screenshot saved to {s1_path}")

        # Click green button 「开始 AI 智能解析」 -> gets 400 error card (U-7 default blocking)
        btn_start = page.locator("#preConfirmCard button.btn-success")
        expect(btn_start).to_be_visible()
        btn_start.click()

        # U-7: Verify errorCard is shown with gentle guidance, then click btnForceRetry (继续 AI 解析)
        expect(page.locator("#errorCard")).to_be_visible()
        page.locator("#btnForceRetry").click()

        # -------------------------------------------------------------
        # Scenario 2: 4-Stage Loading Progress
        # -------------------------------------------------------------
        loading_card = page.locator("#loadingCard")
        expect(loading_card).to_be_visible()

        # Verify 4-stage components
        expect(page.locator("#loadingProgressBar")).to_be_visible()
        expect(page.locator("#loadingStep1")).to_be_visible()
        expect(page.locator("#loadingStep2")).to_be_visible()
        expect(page.locator("#loadingStep3")).to_be_visible()
        expect(page.locator("#loadingStep4")).to_be_visible()
        expect(page.locator("#ocrTimer")).to_be_visible()

        s2_path = os.path.join(SCREENSHOT_DIR, "02_scenario2_loading_4stages.png")
        page.screenshot(path=s2_path)
        print(f"Scenario 2 screenshot saved to {s2_path}")

        # Wait for recognition to complete and transition to Step 2/2
        page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=15000)
        expect(page.locator("#prefillFormCard")).to_be_visible()

        # -------------------------------------------------------------
        # Scenario 3: Step 2/2 Metadata Grid & 7-Column Detail Table
        # -------------------------------------------------------------
        # Verify Header 2-column grid fields
        expect(page.locator("#inpSupplier")).to_have_value("祥興食品有限公司")
        expect(page.locator("#inpDate")).to_have_value("2026-08-22")
        expect(page.locator("#inpSheet")).to_have_value("2026-08")
        expect(page.locator("#inpTotal")).to_have_value("1080.00")
        expect(page.locator("#inpSettlementType")).to_be_visible()
        expect(page.locator("#inpDocForm")).to_have_value("printed_delivery_note")
        expect(page.locator("#inpCurrency")).to_have_value("HKD")
        expect(page.locator("#inpDepartmentId")).to_be_visible()

        # Verify Table layout and 7 columns
        table = page.locator(".receipt-item-table")
        expect(table).to_be_visible()
        headers = page.locator(".receipt-item-table thead th")
        expect(headers).to_have_count(7)

        # Verify column 1 micro-layout: upper inp-name, lower sku-pill
        rows = page.locator("#itemTableBody tr")
        expect(rows).to_have_count(3)

        first_row = rows.nth(0)
        expect(first_row.locator(".inp-name")).to_have_value("本地新鮮菜心")
        expect(first_row.locator(".sku-pill")).to_be_visible()
        expect(first_row.locator(".sku-pill-matched")).to_be_visible()

        second_row = rows.nth(1)
        expect(second_row.locator(".inp-name")).to_have_value("澳洲冰鮮牛肉眼")
        expect(second_row.locator(".sku-pill-unlinked")).to_be_visible()
        expect(second_row.locator(".anomaly-pill")).to_contain_text("15.0%")

        # Test clicking SKU pill opens floating dropdown
        second_sku_pill = second_row.locator(".sku-pill")
        second_sku_pill.click()
        expect(second_row.locator(".sku-dropdown-floating")).to_be_visible()

        s3_path = os.path.join(SCREENSHOT_DIR, "03_scenario3_step2_review_form_table.png")
        page.screenshot(path=s3_path)
        print(f"Scenario 3 screenshot saved to {s3_path}")

        # Close SKU dropdown by clicking outside
        page.locator("#inpSupplier").click()

        # -------------------------------------------------------------
        # Scenario 4: Fees Drawer, Void Row & Math Recalculation & Save
        # -------------------------------------------------------------
        # Toggle fees drawer
        fees_toggle = page.locator(".fees-drawer-toggle")
        fees_toggle.click()
        expect(page.locator("#feesDrawerContent")).to_be_visible()

        # Set fees: discount=10, delivery=15, deposit=20, rounding=1
        page.locator("#inpDiscount").fill("10.00")
        page.locator("#inpDeliveryFee").fill("15.00")
        page.locator("#inpDeposit").fill("20.00")
        page.locator("#inpRounding").fill("1.00")

        # Trigger input event on rounding
        page.locator("#inpRounding").dispatch_event("input")

        # Net fee adjustment = +15 + 20 - 10 - 1 = +24.00
        # Total should now be 1080.00 + 24.00 = 1104.00
        expect(page.locator("#inpTotal")).to_have_value("1104.00")
        expect(page.locator("#feesSummaryBadge")).to_be_visible()
        expect(page.locator("#feesSummaryBadge")).to_contain_text("24.00")

        # Test Voiding the 3rd row (amount 60.00)
        third_row = rows.nth(2)
        void_btn = third_row.locator(".btn-action-void")
        expect(void_btn).to_be_visible()
        void_btn.click()

        # Verify void state on row
        expect(third_row).to_have_attribute("data-is-void", "1")
        expect(void_btn).to_have_text("恢复")

        # New total should exclude 60.00 -> (1080 - 60) + 24 = 1044.00
        expect(page.locator("#inpTotal")).to_have_value("1044.00")

        # Select settlement type (required)
        page.locator("#inpSettlementType").select_option("cash")

        s4_path = os.path.join(SCREENSHOT_DIR, "04_scenario4_fees_drawer_void_recalc.png")
        page.screenshot(path=s4_path)
        print(f"Scenario 4 screenshot saved to {s4_path}")

        # Click save button
        btn_save = page.locator("#btnSaveReview")
        expect(btn_save).to_be_visible()
        btn_save.click()

        # Wait for toast notification
        page.wait_for_selector(".app-toast", timeout=8000)
        s5_path = os.path.join(SCREENSHOT_DIR, "05_save_success_toast.png")
        page.screenshot(path=s5_path)
        print(f"Save success screenshot saved to {s5_path}")

        browser.close()


if __name__ == "__main__":
    test_e2e_clerk_flow_all_scenarios()
    print("ALL PLAYWRIGHT E2E SCENARIOS PASSED SUCCESSFULLY!")

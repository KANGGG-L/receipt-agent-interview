import os
import socket
import time
import json
from urllib.parse import urlparse

import pytest

try:
    from PIL import Image, ImageDraw, ImageFilter
    _PIL_OK = True
except Exception:  # pragma: no cover
    _PIL_OK = False

# 环境守卫（离线干净 skip）：
# 本用例依赖 127.0.0.1:15010 活体服务（页面壳）+ playwright 浏览器（API 均已 route mock）。
# 两种不满足情况分别给出 skip 理由，任一不满足即用例级 skip：
#   1) 服务不可达（socket 探测，超时约 1s）；
#   2) 服务可达但浏览器不可用（playwright 未安装或 chromium 启动失败）。
try:
    from playwright.sync_api import sync_playwright, expect
    _PLAYWRIGHT_IMPORT_OK = True
except Exception:  # pragma: no cover - 离线/未装 playwright 时走 skip 分支
    _PLAYWRIGHT_IMPORT_OK = False

BASE_URL = "http://127.0.0.1:15010"
SCREENSHOT_DIR = "/Users/ethan/Documents/GitHub/receipt-agent-interview/artifacts/e2e-refactor"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def _service_reachable(timeout=1.0):
    parsed = urlparse(BASE_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def _browser_available():
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


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


_SKIP_REASON = None if (_PLAYWRIGHT_IMPORT_OK and _PIL_OK) else "需要 playwright 与 Pillow"
if _SKIP_REASON is None:
    if not _service_reachable():
        _SKIP_REASON = "需要活体服务与浏览器: 服务 %s 不可达" % BASE_URL
    elif not _browser_available():
        _SKIP_REASON = "需要活体服务与浏览器: playwright 浏览器启动失败"
pytestmark = pytest.mark.skipif(
    _SKIP_REASON is not None,
    reason=_SKIP_REASON or "需要活体服务与浏览器",
)


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
                # 作废态改为只读展示：仅来自票面划线的 AI 提取（is_void），
                # 手动作废按钮已按产品决策移除（main.js appendTableRow 上方注释）。
                "is_void": 1,
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

        def handle_inventory_route(route):
            # SKU 组合框聚焦时会拉取 /api/inventory 候选；mock 之保持用例确定性
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "status": "success",
                    "data": [
                        {"id": 1, "name": "本地新鮮菜心", "sku_code": "SKU-001"},
                        {"id": 2, "name": "嘉顿幼麥方包", "sku_code": "SKU-002"}
                    ]
                })
            )

        page.on("console", lambda msg: print(f"[CONSOLE {msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"[PAGE ERROR] {err}"))
        page.on("request", lambda req: print(f"[REQUEST] {req.method} {req.url}"))
        page.on("response", lambda res: print(f"[RESPONSE] {res.status} {res.url}"))

        page.route("**/api/upload*", handle_upload_route)
        page.route("**/api/job/*", handle_job_route)
        page.route("**/api/save_edited", handle_save_route)
        page.route("**/api/inventory*", handle_inventory_route)

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

        # Click green button 「开始 AI 智能解析」 -> 服务端对未带 force 的模糊图返回
        # 400 IMAGE_QUALITY_ERROR（U-7 方案：默认不强制进管线，由用户显式确认）
        btn_start = page.locator("#preConfirmCard button.btn-success")
        expect(btn_start).to_be_visible()
        btn_start.click()

        # U-7 期望修正说明（依据 HEAD 8e90066 与当前 main.js 实际行为，经活体服务实测）：
        # 8e90066 的「门禁不阻断/回退即时通知」适用于算术/契约/明细为空等门禁矛盾与引擎异常
        # （routeRecognitionFailure 对 gate/engine 返回 true：人话 Toast + 自动转手工补录，
        # 不出错误卡；引擎降级经 notifyFallbackIfTriggered 即时提示）。而画质类
        # （IMAGE_QUALITY_ERROR）在 routeRecognitionFailure 中显式返回 false ——
        # 「仅画质问题保留可自救的错误卡片」：错误卡仍展示（画质归因三分类文案，
        # 不再误导归因），并保留「继续 AI 解析」(btnForceRetry) 供用户显式确认后
        # 以 force=true 重发。故此处仍断言 errorCard 可见 + 画质警告横幅可见，
        # 而非「无任何卡片」。
        expect(page.locator("#errorCard")).to_be_visible()
        # 画质警告横幅（showQualityWarnings('image_blur')）应包含「继续 AI 解析」逃生指引
        expect(page.locator("#qualityWarningsBanner")).to_contain_text("继续 AI 解析")
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

        # Verify column 1 micro-layout（期望修正，依据当前 appendTableRow 实现）：
        # SKU 徽标（.sku-pill / .sku-pill-matched / .sku-pill-unlinked）已演进为
        # 「品名输入框 + 隐藏 SKU 字段」组合框（sku-combobox-wrap）：
        #   - 匹配态：inp-name 可见 + 隐藏 .inp-sku-id 携带 sku_id；
        #   - 未匹配态：.inp-sku-id 为空 + 单价格子区渲染 .anomaly-pill 价格偏离徽标。
        rows = page.locator("#itemTableBody tr")
        expect(rows).to_have_count(3)

        first_row = rows.nth(0)
        expect(first_row.locator(".inp-name")).to_have_value("本地新鮮菜心")
        expect(first_row.locator(".inp-sku-id")).to_have_value("1")
        expect(first_row.locator(".inp-sku")).to_have_value("本地新鮮菜心")

        second_row = rows.nth(1)
        expect(second_row.locator(".inp-name")).to_have_value("澳洲冰鮮牛肉眼")
        expect(second_row.locator(".inp-sku-id")).to_have_value("")
        expect(second_row.locator(".anomaly-pill")).to_contain_text("15.0%")

        # Test focusing name input opens floating SKU dropdown（原 .sku-pill 点击入口
        # 改为 inp-name 聚焦/点击触发 openSkuMenuForNameInput）
        second_name_input = second_row.locator(".inp-name")
        second_name_input.click()
        expect(second_row.locator(".sku-dropdown-floating")).to_be_visible()

        s3_path = os.path.join(SCREENSHOT_DIR, "03_scenario3_step2_review_form_table.png")
        page.screenshot(path=s3_path)
        print(f"Scenario 3 screenshot saved to {s3_path}")

        # Close SKU dropdown by clicking outside
        page.locator("#inpSupplier").click()

        # -------------------------------------------------------------
        # Scenario 4: Fees Drawer, Void (read-only) Row & Math Recalculation & Save
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

        # 期望修正（依据 recalcTotalSum 当前口径）：作废行金额不计入 itemsSum，
        # 明细有效合计 = 120 + 900 = 1020；净费用 = 15 + 20 - 10 - 1 = +24.00；
        # 总额 = 1020 + 24 = 1044.00（作废行 60.00 自始即被排除）。
        expect(page.locator("#inpTotal")).to_have_value("1044.00")
        expect(page.locator("#feesSummaryBadge")).to_be_visible()
        expect(page.locator("#feesSummaryBadge")).to_contain_text("24.00")

        # Verify read-only void state on 3rd row（票面划线行：不计入总额与入库）
        third_row = rows.nth(2)
        expect(third_row).to_have_attribute("data-is-void", "1")
        expect(third_row.locator(".badge-secondary")).to_contain_text("作废")

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

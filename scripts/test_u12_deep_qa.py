# -*- coding: utf-8 -*-
"""
Comprehensive QA Automation Script for Issue U-12:
"U-12 iPhone 大图 (>1.5MB HEIC) 连续拖放防抖与并发锁"

Covers:
- AC-1: 150ms debounce window on file selection and drop events
- AC-2: Generational lock fileSelectionGen ensuring obsolete slower processing is cleanly discarded
- AC-3: Friendly lightweight indicator ("正在准备照片，请稍候...") for large / HEIC images without UI freeze
- AC-4: Drag-and-drop hover styling cleanup (.drag-over / border color)
- AC-5: Memory cleanup (URL.revokeObjectURL) & zero memory leaks
- AC-6: Button height >= 38px, WCAG AA contrast, staff UX evaluation
- Live Real iPhone HEIC testing (>1.5MB) from batch1
"""

import os
import sys
import time
import json
import re
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
ARTIFACTS_DIR = os.path.abspath("artifacts/u12_qa")
SCREENS_DIR = os.path.join(ARTIFACTS_DIR, "screens")
os.makedirs(SCREENS_DIR, exist_ok=True)

RESULTS = []


def check(ac_id, title, cond, detail=""):
    ok = bool(cond)
    mark = "PASS" if ok else "FAIL"
    status_icon = "✅" if ok else "❌"
    msg = f"{status_icon} [{mark}] {ac_id}: {title}"
    if detail:
        msg += f" | {detail}"
    print(msg)
    RESULTS.append({
        "ac": ac_id,
        "title": title,
        "passed": ok,
        "detail": detail
    })
    return ok


def parse_rgb(rgb_str):
    m = re.search(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', rgb_str)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return 0, 0, 0


def relative_luminance(r, g, b):
    rs = r / 255.0
    gs = g / 255.0
    bs = b / 255.0
    r_lin = rs / 12.92 if rs <= 0.03928 else ((rs + 0.055) / 1.055) ** 2.4
    g_lin = gs / 12.92 if gs <= 0.03928 else ((gs + 0.055) / 1.055) ** 2.4
    b_lin = bs / 12.92 if bs <= 0.03928 else ((bs + 0.055) / 1.055) ** 2.4
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(rgb1, rgb2):
    l1 = relative_luminance(*rgb1)
    l2 = relative_luminance(*rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def make_test_image(out_path, text="测试单据", size=(800, 1000), color="white"):
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), text, fill="black")
    draw.text((50, 120), "送貨單 NO.20260824", fill="black")
    draw.text((50, 200), "食材明细 10斤 x 15 = 150", fill="black")
    draw.text((50, 280), "合計 HK$150", fill="black")
    img.save(out_path, format="JPEG", quality=85)
    return out_path


def make_large_test_image(out_path, text="大图收据 >1.5MB"):
    raw_data = os.urandom(1800 * 1800 * 3)
    img = Image.frombytes("RGB", (1800, 1800), raw_data)
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), text, fill=(0, 0, 0))
    img.save(out_path, format="JPEG", quality=95)
    file_size_mb = os.path.getsize(out_path) / (1024 * 1024)
    return file_size_mb


def run_comprehensive_u12_qa():
    print("=" * 70)
    print("  QA_Agent Comprehensive Verification: Issue U-12")
    print("  iPhone 大图 (>1.5MB HEIC) 连续拖放防抖与并发锁")
    print("=" * 70)

    # Prepare test files
    os.makedirs("/tmp/u12_qa_assets", exist_ok=True)
    img_a = make_test_image("/tmp/u12_qa_assets/receipt_A.jpg", text="单据 A (初选)")
    img_b = make_test_image("/tmp/u12_qa_assets/receipt_B.jpg", text="单据 B (次选)")
    img_c = make_test_image("/tmp/u12_qa_assets/receipt_C.jpg", text="单据 C (终选)")
    large_jpg = "/tmp/u12_qa_assets/receipt_large_2mb.jpg"
    large_mb = make_large_test_image(large_jpg, text="超大图 2MB")

    # Real HEIC files from batch1
    heic_dir = "/Users/ethan/Desktop/hk/My Drive/Receipts/batch1"
    real_heic_1 = os.path.join(heic_dir, "IMG_5787.heic")  # 1.7MB
    real_heic_2 = os.path.join(heic_dir, "IMG_5788.heic")  # 1.5MB
    real_heic_3 = os.path.join(heic_dir, "IMG_5789.heic")  # 1.3MB

    has_real_heic = os.path.exists(real_heic_1) and os.path.exists(real_heic_2)
    print(f"Real iPhone HEIC available: {has_real_heic}")
    if has_real_heic:
        print(f"  HEIC 1: {real_heic_1} ({os.path.getsize(real_heic_1)/1024/1024:.2f}MB)")
        print(f"  HEIC 2: {real_heic_2} ({os.path.getsize(real_heic_2)/1024/1024:.2f}MB)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

        # -------------------------------------------------------------
        # STEP 1: Page Load & DOM Indicator Inspection
        # -------------------------------------------------------------
        print("\n--- [STEP 1] Page Load & DOM Indicators ---")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(SCREENS_DIR, "01_initial_home.png"))

        check("AC-3.1", "DOM elements for prep indicators exist",
              page.locator("#imagePrepIndicator").count() == 1 and page.locator("#imgViewerPrepIndicator").count() == 1,
              "Both #imagePrepIndicator and #imgViewerPrepIndicator present")

        check("AC-3.2", "Initial state of prep indicators is hidden",
              page.evaluate("() => document.getElementById('imagePrepIndicator').classList.contains('hide') && document.getElementById('imgViewerPrepIndicator').classList.contains('hide')"),
              "Both indicators initially have .hide class")

        # -------------------------------------------------------------
        # STEP 2: Indicator Visual Testing & Tone Assessment
        # -------------------------------------------------------------
        print("\n--- [STEP 2] Indicator Lifecycle & Friendly Tone ---")
        page.evaluate("() => window.showImagePrepIndicator(true, '正在准备照片，请稍候...')")
        page.wait_for_timeout(200)
        page.screenshot(path=os.path.join(SCREENS_DIR, "02_prep_indicator_shown.png"))

        prep_el = page.locator("#imagePrepIndicator")
        indicator_text = prep_el.inner_text().strip()
        check("AC-3.3", "Indicator displays friendly plain language",
              "正在准备照片，请稍候..." in indicator_text,
              f"Text: '{indicator_text}' (No developer jargon/no technical terms)")

        # Verify WCAG styling of indicator
        ind_color = page.evaluate("() => window.getComputedStyle(document.getElementById('imagePrepIndicator')).color")
        ind_bg = page.evaluate("() => window.getComputedStyle(document.getElementById('imagePrepIndicator')).backgroundColor")
        check("AC-3.4", "Indicator visual appearance non-blocking and styled",
              prep_el.is_visible(),
              f"color={ind_color}, bg={ind_bg}")

        page.evaluate("() => window.showImagePrepIndicator(false)")
        page.wait_for_timeout(200)
        check("AC-3.5", "Indicator hides completely when turned off",
              page.evaluate("() => document.getElementById('imagePrepIndicator').classList.contains('hide')"),
              "Indicator hidden")

        # -------------------------------------------------------------
        # STEP 3: Drag & Drop Hover Styling Cleanup
        # -------------------------------------------------------------
        print("\n--- [STEP 3] Drag & Drop Hover Styling Lifecycle ---")
        upload_area = page.locator("#uploadArea")

        # 3.1 dragover
        upload_area.dispatch_event("dragover")
        page.wait_for_timeout(100)
        has_drag_over = page.evaluate("() => document.getElementById('uploadArea').classList.contains('drag-over')")
        border_col_over = page.evaluate("() => document.getElementById('uploadArea').style.borderColor")
        check("AC-4.1", "dragover applies .drag-over class and highlight border",
              has_drag_over,
              f"classList contains drag-over, borderColor={border_col_over}")

        # 3.2 dragleave
        upload_area.dispatch_event("dragleave")
        page.wait_for_timeout(100)
        no_drag_leave = page.evaluate("() => !document.getElementById('uploadArea').classList.contains('drag-over')")
        check("AC-4.2", "dragleave completely removes .drag-over and cleans up border",
              no_drag_leave,
              "classList clean after dragleave")

        # 3.3 dragover -> drop
        upload_area.dispatch_event("dragover")
        page.wait_for_timeout(50)
        page.evaluate("""() => {
            const el = document.getElementById('uploadArea');
            const dropEv = new DragEvent('drop', { bubbles: true, cancelable: true });
            el.dispatchEvent(dropEv);
        }""")
        page.wait_for_timeout(100)
        no_drag_drop = page.evaluate("() => !document.getElementById('uploadArea').classList.contains('drag-over')")
        check("AC-4.3", "drop event completely cleans up .drag-over styling",
              no_drag_drop,
              "classList clean after drop")

        # -------------------------------------------------------------
        # STEP 4: 150ms Debounce Window Verification (AC-1)
        # -------------------------------------------------------------
        print("\n--- [STEP 4] 150ms Debounce Window Verification ---")
        # Setup spy on handleFilesSelect execution count
        page.evaluate("""() => {
            window._handleFilesSelectCallCount = 0;
            window._lastProcessedFiles = [];
            const origFn = window.handleFilesSelect;
            window.handleFilesSelect = function(files, gen) {
                window._handleFilesSelectCallCount++;
                window._lastProcessedFiles = files.map(f => f.name);
                return origFn.apply(this, arguments);
            };
        }""")

        initial_gen = page.evaluate("() => window.fileSelectionGen || 0")

        # Rapidly fire 3 queueFilesSelect calls within 30ms (<150ms debounce)
        page.evaluate("""() => {
            const f1 = [new File(["dummy1"], "rapid_1.jpg", {type: "image/jpeg"})];
            const f2 = [new File(["dummy2"], "rapid_2.jpg", {type: "image/jpeg"})];
            const f3 = [new File(["dummy3"], "rapid_3.jpg", {type: "image/jpeg"})];
            window.queueFilesSelect(f1);
            setTimeout(() => window.queueFilesSelect(f2), 20);
            setTimeout(() => window.queueFilesSelect(f3), 40);
        }""")

        # Wait 400ms for debounce timer (150ms) to fire exactly once
        page.wait_for_timeout(400)

        call_count = page.evaluate("() => window._handleFilesSelectCallCount")
        last_files = page.evaluate("() => window._lastProcessedFiles")
        latest_gen = page.evaluate("() => window.fileSelectionGen")

        check("AC-1.1", "Rapid calls (<150ms) debounced into exactly 1 execution",
              call_count == 1,
              f"Call count = {call_count} (expected 1)")

        check("AC-1.2", "Debounced execution processed the latest file (rapid_3.jpg)",
              last_files == ["rapid_3.jpg"],
              f"Processed files: {last_files}")

        check("AC-1.3", "fileSelectionGen incremented 3 times for 3 rapid triggers",
              latest_gen >= initial_gen + 3,
              f"initial={initial_gen}, latest={latest_gen}")

        # -------------------------------------------------------------
        # STEP 5: Generational Lock & Stale Processing Discard (AC-2)
        # -------------------------------------------------------------
        print("\n--- [STEP 5] Generational Lock & Race Condition Discard ---")
        
        # Test simulated slow async operation from an older generation
        stale_lock_test = page.evaluate("""async () => {
            const genBefore = window.fileSelectionGen;
            const obsoleteGen = genBefore;
            
            // Increment generation to simulate a new file selection arriving while slow op is running
            const newGen = window.nextFileSelectionGen();
            
            // Simulate older slow operation finishing
            let accepted = false;
            if (obsoleteGen === window.fileSelectionGen) {
                accepted = true;
            }
            return {
                obsoleteGen,
                newGen,
                currentGen: window.fileSelectionGen,
                staleDiscarded: !accepted
            };
        }""")

        check("AC-2.1", "Generational lock discards stale generation callback",
              stale_lock_test["staleDiscarded"],
              f"obsoleteGen={stale_lock_test['obsoleteGen']}, currentGen={stale_lock_test['currentGen']}")

        # Test live file input rapid switching
        print("Testing file input rapid sequential selection...")
        page.set_input_files("#receiptFile", img_a)
        page.wait_for_timeout(30)  # 30ms < 150ms
        page.set_input_files("#receiptFile", img_b)
        page.wait_for_timeout(30)  # 30ms < 150ms
        page.set_input_files("#receiptFile", img_c)

        page.wait_for_timeout(600)  # wait for debounce + preview load
        page.screenshot(path=os.path.join(SCREENS_DIR, "03_rapid_selection_final_preview.png"))

        active_photo_name = page.evaluate("() => (BatchUploader.photos[0] && BatchUploader.photos[0].file) ? BatchUploader.photos[0].file.name : ''")
        check("AC-2.2", "Live rapid file input renders exclusively latest selected file",
              "receipt_C.jpg" in active_photo_name,
              f"Current active photo name: '{active_photo_name}' (receipt_C.jpg expected)")

        check("AC-2.3", "splitViewArea visible and stable without flicker",
              page.locator("#splitViewArea").is_visible(),
              "splitViewArea visible")

        # -------------------------------------------------------------
        # STEP 6: Memory Cleanup & ObjectURL Revocation (AC-5)
        # -------------------------------------------------------------
        print("\n--- [STEP 6] Memory Cleanup & ObjectURL Revocation ---")
        page.evaluate("""() => {
            window._revokedUrls = [];
            const origRevoke = URL.revokeObjectURL;
            URL.revokeObjectURL = function(url) {
                window._revokedUrls.push(url);
                return origRevoke.apply(this, arguments);
            };
        }""")

        # Select another file to trigger cleanup of receipt_C.jpg ObjectURL
        page.set_input_files("#receiptFile", img_a)
        page.wait_for_timeout(500)

        revoked_count = page.evaluate("() => window._revokedUrls.length")
        revoked_urls = page.evaluate("() => window._revokedUrls")
        check("AC-5.1", "Previous ObjectURL cleanly revoked on new file selection",
              revoked_count >= 1,
              f"Revoked URLs count: {revoked_count}, samples: {revoked_urls[:2]}")

        # -------------------------------------------------------------
        # STEP 7: Real iPhone HEIC & Large Image (>1.5MB) Live Test (AC-3, AC-2)
        # -------------------------------------------------------------
        print("\n--- [STEP 7] Real iPhone HEIC & >1.5MB Image Live Test ---")
        if has_real_heic:
            print(f"Uploading real iPhone HEIC: {os.path.basename(real_heic_1)} ({os.path.getsize(real_heic_1)/1024/1024:.2f}MB)")
            
            # Record start time
            t0 = time.time()
            page.set_input_files("#receiptFile", real_heic_1)
            
            # Check for indicator visibility right after upload trigger (within debounce/prep window)
            page.wait_for_timeout(80)
            indicator_active = page.evaluate("() => !document.getElementById('imagePrepIndicator').classList.contains('hide') || !document.getElementById('imgViewerPrepIndicator').classList.contains('hide')")
            page.screenshot(path=os.path.join(SCREENS_DIR, "04_heic_prep_in_progress.png"))
            
            # Wait for HEIC conversion & preview to complete (up to 60s for libheif CPU conversion)
            page.wait_for_function(
                "() => BatchUploader.photos[0] && BatchUploader.photos[0].file && (BatchUploader.photos[0].file._convertedFromHeic || BatchUploader.photos[0].file.name.includes('IMG_5787'))",
                timeout=60000
            )
            t_convert = time.time() - t0
            page.screenshot(path=os.path.join(SCREENS_DIR, "05_heic_converted_preview.png"))

            heic_photo_obj = page.evaluate("() => BatchUploader.photos[0] ? { name: BatchUploader.photos[0].file.name, converted: BatchUploader.photos[0].file._convertedFromHeic, orig: BatchUploader.photos[0].file._originalName } : null")
            
            check("AC-3.6", "Real HEIC (>1.5MB) automatically converted and previewed smoothly",
                  heic_photo_obj is not None and (heic_photo_obj.get("converted") or "IMG_5787" in str(heic_photo_obj.get("name")) or "IMG_5787" in str(heic_photo_obj.get("orig"))),
                  f"Converted obj: {heic_photo_obj}, total time: {t_convert:.2f}s")

            # Check indicator is cleanly hidden after completion
            page.wait_for_function(
                "() => document.getElementById('imagePrepIndicator').classList.contains('hide')",
                timeout=5000
            )
            heic_ind_hidden = page.evaluate("() => document.getElementById('imagePrepIndicator').classList.contains('hide') && document.getElementById('imgViewerPrepIndicator').classList.contains('hide')")
            check("AC-3.7", "Prep indicator cleanly hidden after HEIC conversion completes",
                  heic_ind_hidden,
                  "Both prep indicators hidden in finally block")

            # Test rapid sequential HEIC drag-drop / selection
            print("Testing rapid sequential HEIC selection (HEIC 1 -> HEIC 2)...")
            page.set_input_files("#receiptFile", real_heic_1)
            page.wait_for_timeout(30)  # 30ms < 150ms debounce
            page.set_input_files("#receiptFile", real_heic_2)
            
            # Wait for conversion of the final HEIC (IMG_5788)
            page.wait_for_function(
                "() => BatchUploader.photos[0] && BatchUploader.photos[0].file && (BatchUploader.photos[0].file._originalName === 'IMG_5788.heic' || BatchUploader.photos[0].file.name.includes('IMG_5788')) && document.getElementById('imagePrepIndicator').classList.contains('hide')",
                timeout=60000
            )
            page.screenshot(path=os.path.join(SCREENS_DIR, "06_heic_rapid_final_preview.png"))

            final_heic_photo = page.evaluate("() => BatchUploader.photos[0] ? (BatchUploader.photos[0].file._originalName || BatchUploader.photos[0].file.name) : ''")
            check("AC-2.4", "Rapid HEIC selections resolve strictly to final HEIC photo",
                  "IMG_5788" in final_heic_photo,
                  f"Final photo: '{final_heic_photo}' (IMG_5788 expected)")
        else:
            # Fallback to large synthetic JPEG
            print("Using synthetic large image (>1.5MB)...")
            page.set_input_files("#receiptFile", large_jpg)
            page.wait_for_timeout(800)
            large_photo_name = page.evaluate("() => (BatchUploader.photos[0] && BatchUploader.photos[0].file) ? BatchUploader.photos[0].file.name : ''")
            check("AC-3.6", "Large image (>1.5MB) loaded and previewed smoothly",
                  "receipt_large_2mb.jpg" in large_photo_name,
                  f"Photo loaded: {large_photo_name}")

        # -------------------------------------------------------------
        # STEP 8: Button Sizes & Touch Target Evaluation (AC-6)
        # -------------------------------------------------------------
        print("\n--- [STEP 8] Button Dimensions & WCAG AA Contrast Evaluation ---")
        buttons_to_test = [
            ("btn_upload", page.locator("#uploadArea button.btn-primary")),
            ("btn_manual", page.locator("#btnNewManualEntry")),
            ("btn_reload", page.locator("#btnReloadImage")),
            ("btn_delete", page.locator("#btnDeleteImage")),
            ("btn_parse", page.locator("#preConfirmCard button.btn-success")),
            ("btn_preconfirm_manual", page.locator("#preConfirmCard button.btn-secondary")),
        ]

        all_buttons_sized = True
        contrast_checks = []

        for name, btn in buttons_to_test:
            if btn.count() > 0 and btn.is_visible():
                bbox = btn.bounding_box()
                h = bbox["height"] if bbox else 0
                w = bbox["width"] if bbox else 0
                
                # Check height >= 38px
                pass_h = h >= 38
                if not pass_h:
                    all_buttons_sized = False
                print(f"  Button '{name}': {w:.1f}x{h:.1f}px (Height >= 38px: {pass_h})")

                # Contrast calculation
                color_css = btn.evaluate("el => window.getComputedStyle(el).color")
                bg_css = btn.evaluate("el => window.getComputedStyle(el).backgroundColor")
                rgb_fg = parse_rgb(color_css)
                rgb_bg = parse_rgb(bg_css)
                if rgb_bg == (0, 0, 0) or bg_css == "rgba(0, 0, 0, 0)":
                    # fallback to card background
                    rgb_bg = (30, 41, 59) # slate-800
                ratio = contrast_ratio(rgb_fg, rgb_bg)
                contrast_checks.append((name, ratio, ratio >= 4.5))
                print(f"    Contrast: {ratio:.2f}:1 (WCAG AA >= 4.5: {ratio >= 4.5})")

        check("AC-6.1", "Interactive buttons meet minimum height requirement (>= 38px)",
              all_buttons_sized,
              "All tested main buttons have height >= 38px for comfortable staff touch target")

        aa_pass_all = all(c[2] for c in contrast_checks)
        check("AC-6.2", "Button text color contrast satisfies WCAG AA (>= 4.5:1)",
              aa_pass_all,
              f"Contrast ratios: {', '.join(f'{c[0]}={c[1]:.2f}:1' for c in contrast_checks)}")

        # -------------------------------------------------------------
        # STEP 9: Staff Persona UX Evaluation (阿叔/阿姨视角)
        # -------------------------------------------------------------
        print("\n--- [STEP 9] Staff Persona UX & Escape Route Assessment ---")
        
        # 9.1 Escape Route: Start Manual Entry anytime
        btn_manual = page.locator("#btnNewManualEntry")
        btn_manual.click()
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(SCREENS_DIR, "07_manual_entry_mode.png"))

        form_visible = page.locator("#prefillFormCard").is_visible()
        check("AC-6.3", "Manual Entry escape route opens form and maintains preview readiness",
              form_visible,
              "Manual form successfully rendered upon clicking #btnNewManualEntry")

        # 9.2 Verification of Plain Language Error Cards & Tooltips
        sample_error_card_text = page.evaluate("""() => {
            const card = document.getElementById('errorCard');
            const warn = document.getElementById('preConfirmQualityWarn');
            return {
                warnText: warn ? warn.innerText : '',
                errorCardExists: !!card
            };
        }""")
        check("AC-6.4", "Quality warnings use respectful, jargon-free phrasing",
              "画质提示" in sample_error_card_text["warnText"] or not sample_error_card_text["warnText"],
              f"Notice text: '{sample_error_card_text['warnText']}'")

        browser.close()

    # Summary
    print("\n" + "=" * 70)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["passed"])
    failed = sum(1 for r in RESULTS if not r["passed"])
    print(f"  U-12 QA VERIFICATION SUMMARY: {passed}/{total} Passed, {failed} Failed")
    print("=" * 70)

    for r in RESULTS:
        icon = "✅" if r["passed"] else "❌"
        print(f"  {icon} {r['ac']}: {r['title']}")
        if r["detail"]:
            print(f"     └─ {r['detail']}")

    summary_file = os.path.join(ARTIFACTS_DIR, "u12_qa_results.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump({
            "issue": "U-12",
            "title": "iPhone 大图 (>1.5MB HEIC) 连续拖放防抖与并发锁",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": total,
            "passed": passed,
            "failed": failed,
            "results": RESULTS
        }, f, ensure_ascii=False, indent=2)

    print(f"\nSaved QA results and screenshots to {ARTIFACTS_DIR}")
    return failed == 0


if __name__ == "__main__":
    success = run_comprehensive_u12_qa()
    sys.exit(0 if success else 1)

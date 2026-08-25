import os
import sys
import time
import json
import math
import requests
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
SCREEN_DIR = os.path.abspath("artifacts/u6_qa")
os.makedirs(SCREEN_DIR, exist_ok=True)

def parse_rgb(rgb_str):
    """Parse rgb(r, g, b) or rgba(r, g, b, a) to tuple (r, g, b)."""
    import re
    m = re.search(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', rgb_str)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return (0, 0, 0)

def relative_luminance(rgb):
    """Calculate relative luminance for sRGB according to WCAG 2.1."""
    def channel_lum(c):
        c_srgb = c / 255.0
        return c_srgb / 12.92 if c_srgb <= 0.03928 else ((c_srgb + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel_lum(r) + 0.7152 * channel_lum(g) + 0.0722 * channel_lum(b)

def contrast_ratio(rgb1, rgb2):
    """Calculate WCAG contrast ratio between two RGB tuples."""
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)

def run_u6_qa_suite():
    print("=" * 80)
    print("🚀 [U-6 QA TEST SUITE] Starting Acceptance & UX Testing for Issue U-6")
    print("=" * 80)

    results = {
        "ac1_dom_ids": False,
        "ac2_staff_visual_isolation": False,
        "ac3_owner_admin_full_visibility": False,
        "ac4_dynamic_role_switching": False,
        "ac5_button_size_and_contrast": False,
        "ac6_backend_403_enforcement": False,
        "ux_evaluation": False,
        "receipt_coverage": [],
        "evidence": {}
    }

    console_logs = []
    page_errors = []

    # -------------------------------------------------------------
    # Step 1: Backend 403 API Direct Verification (AC-6)
    # -------------------------------------------------------------
    print("\n--- [AC-6] Verifying Backend 403 Security Enforcement ---")
    headers_staff = {"X-Role": "staff"}
    headers_owner = {"X-Role": "owner"}

    # Test approve endpoint with staff role
    resp_staff_approve = requests.post(f"{BASE_URL}/api/receipt/1/approve", json={"version": 1}, headers=headers_staff)
    print(f"POST /api/receipt/1/approve (role=staff) -> status: {resp_staff_approve.status_code}")
    print(f"Response: {resp_staff_approve.text}")
    assert resp_staff_approve.status_code == 403, f"Expected 403, got {resp_staff_approve.status_code}"
    staff_approve_detail = resp_staff_approve.json().get("detail", "")
    assert "权限不足" in staff_approve_detail and "店员" in staff_approve_detail and "老板" in staff_approve_detail, \
        f"403 message lacks human-friendly role guidance: {staff_approve_detail}"

    # Test flag endpoint with staff role
    resp_staff_flag = requests.post(f"{BASE_URL}/api/receipt/1/flag", headers=headers_staff)
    print(f"POST /api/receipt/1/flag (role=staff) -> status: {resp_staff_flag.status_code}")
    print(f"Response: {resp_staff_flag.text}")
    assert resp_staff_flag.status_code == 403, f"Expected 403, got {resp_staff_flag.status_code}"
    staff_flag_detail = resp_staff_flag.json().get("detail", "")
    assert "权限不足" in staff_flag_detail and "店员" in staff_flag_detail and "老板" in staff_flag_detail, \
        f"403 message lacks human-friendly role guidance: {staff_flag_detail}"

    results["ac6_backend_403_enforcement"] = True
    results["evidence"]["ac6"] = {
        "staff_approve_status": resp_staff_approve.status_code,
        "staff_approve_detail": staff_approve_detail,
        "staff_flag_status": resp_staff_flag.status_code,
        "staff_flag_detail": staff_flag_detail
    }
    print("✅ AC-6 Backend 403 Security Enforcement PASSED")

    # -------------------------------------------------------------
    # Step 2: Browser Testing with Playwright
    # -------------------------------------------------------------
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_init_script("localStorage.setItem('demo_role', 'staff');")
        page = context.new_page()

        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        print("\n--- [AC-1 & AC-2] Visiting page as Staff ---")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        current_role = page.evaluate("() => localStorage.getItem('demo_role')")
        print(f"Current role initialized: {current_role}")
        assert current_role == "staff", f"Expected staff role, got {current_role}"

        test_receipt_ids = [1, 33, 40]
        results["receipt_coverage"] = test_receipt_ids

        # Open Detail Modal for receipt #1
        print(f"\n--- Testing Receipt #1 (Status: Parsed) ---")
        page.evaluate(f"() => loadReceiptDetail(1)")
        modal = page.locator("#archiveDetailModal")
        modal.wait_for(state="visible", timeout=5000)
        print("Archive detail modal is now open.")
        page.wait_for_timeout(1000)

        # Check AC-1 DOM IDs
        print("\n--- [AC-1] Verifying DOM IDs in Modal ---")
        owner_ids = ["modalApproveBtn", "modalFlagBtn", "modalCostShareBtn", "modalCostShareContainer"]
        staff_ids = ["btnSaveArchive", "modalAddRowBtn", "modalCancelBtn"]

        for oid in owner_ids:
            el = page.locator(f"#{oid}")
            assert el.count() == 1, f"DOM element #{oid} must exist exactly once in DOM"
            print(f"  DOM ID #{oid}: EXISTS ✓")

        for sid in staff_ids:
            el = page.locator(f"#{sid}")
            assert el.count() == 1, f"DOM element #{sid} must exist exactly once in DOM"
            print(f"  DOM ID #{sid}: EXISTS ✓")

        results["ac1_dom_ids"] = True
        print("✅ AC-1 DOM IDs Verification PASSED")

        # Check AC-2 Staff Role Visual Isolation
        print("\n--- [AC-2] Verifying Staff Role Visual Isolation in Modal ---")
        approve_btn = page.locator("#modalApproveBtn")
        flag_btn = page.locator("#modalFlagBtn")
        cost_btn = page.locator("#modalCostShareBtn")
        cost_container = page.locator("#modalCostShareContainer")

        save_btn = page.locator("#btnSaveArchive")
        add_row_btn = page.locator("#modalAddRowBtn")
        cancel_btn = page.locator("#modalCancelBtn")

        # In staff role: owner buttons must be invisible
        assert not approve_btn.is_visible(), "Staff view: #modalApproveBtn MUST NOT be visible"
        assert not flag_btn.is_visible(), "Staff view: #modalFlagBtn MUST NOT be visible"
        assert not cost_btn.is_visible(), "Staff view: #modalCostShareBtn MUST NOT be visible"
        assert not cost_container.is_visible(), "Staff view: #modalCostShareContainer MUST NOT be visible"

        # Staff buttons must be visible
        assert save_btn.is_visible(), "Staff view: #btnSaveArchive MUST be visible"
        assert add_row_btn.is_visible(), "Staff view: #modalAddRowBtn MUST be visible"
        assert cancel_btn.is_visible(), "Staff view: #modalCancelBtn MUST be visible"

        # Capture Staff Modal Screenshot
        staff_screen_path = os.path.join(SCREEN_DIR, "u6_staff_modal_view.png")
        modal.screenshot(path=staff_screen_path)
        print(f"Captured screenshot: {staff_screen_path}")

        results["ac2_staff_visual_isolation"] = True
        results["evidence"]["ac2"] = {
            "staff_screen": staff_screen_path,
            "approve_visible": approve_btn.is_visible(),
            "flag_visible": flag_btn.is_visible(),
            "cost_visible": cost_btn.is_visible(),
            "cost_container_visible": cost_container.is_visible(),
            "save_visible": save_btn.is_visible(),
            "add_row_visible": add_row_btn.is_visible(),
            "cancel_visible": cancel_btn.is_visible(),
        }
        print("✅ AC-2 Staff Visual Isolation PASSED")

        # -------------------------------------------------------------
        # Step 3: Check AC-4 Dynamic Role Switching Reactivity (while modal is open)
        # -------------------------------------------------------------
        print("\n--- [AC-4] Testing Dynamic Role Switching Reactivity (Modal Open) ---")
        # Switch to Owner dynamically while modal is open
        page.evaluate("() => { applyRoleVisibility('owner'); }")
        page.wait_for_timeout(500)

        # Assert immediately visible without reloading
        assert approve_btn.is_visible(), "After switching to owner, #modalApproveBtn MUST be visible immediately"
        assert flag_btn.is_visible(), "After switching to owner, #modalFlagBtn MUST be visible immediately"
        assert cost_btn.is_visible(), "After switching to owner, #modalCostShareBtn MUST be visible immediately"
        assert cost_container.is_visible(), "After switching to owner, #modalCostShareContainer MUST be visible immediately"

        owner_dynamic_screen = os.path.join(SCREEN_DIR, "u6_dynamic_switch_to_owner.png")
        modal.screenshot(path=owner_dynamic_screen)
        print(f"Captured screenshot on dynamic switch to owner: {owner_dynamic_screen}")

        # Switch back to Staff dynamically while modal is open
        page.evaluate("() => { applyRoleVisibility('staff'); }")
        page.wait_for_timeout(500)

        assert not approve_btn.is_visible(), "After switching back to staff, #modalApproveBtn MUST be hidden immediately"
        assert not flag_btn.is_visible(), "After switching back to staff, #modalFlagBtn MUST be hidden immediately"
        assert not cost_btn.is_visible(), "After switching back to staff, #modalCostShareBtn MUST be hidden immediately"
        assert not cost_container.is_visible(), "After switching back to staff, #modalCostShareContainer MUST be hidden immediately"

        staff_dynamic_screen = os.path.join(SCREEN_DIR, "u6_dynamic_switch_to_staff.png")
        modal.screenshot(path=staff_dynamic_screen)
        print(f"Captured screenshot on dynamic switch back to staff: {staff_dynamic_screen}")

        # Switch to Admin dynamically
        page.evaluate("() => { applyRoleVisibility('admin'); }")
        page.wait_for_timeout(500)
        assert approve_btn.is_visible(), "Admin role: #modalApproveBtn MUST be visible"
        assert flag_btn.is_visible(), "Admin role: #modalFlagBtn MUST be visible"
        assert cost_btn.is_visible(), "Admin role: #modalCostShareBtn MUST be visible"
        assert cost_container.is_visible(), "Admin role: #modalCostShareContainer MUST be visible"

        admin_dynamic_screen = os.path.join(SCREEN_DIR, "u6_dynamic_switch_to_admin.png")
        modal.screenshot(path=admin_dynamic_screen)
        print(f"Captured screenshot on dynamic switch to admin: {admin_dynamic_screen}")

        results["ac4_dynamic_role_switching"] = True
        print("✅ AC-4 Dynamic Role Switching Reactivity PASSED")

        # -------------------------------------------------------------
        # Step 4: Check AC-3 Owner & Admin Full Visibility
        # -------------------------------------------------------------
        print("\n--- [AC-3] Verifying Owner / Admin Full Visibility & Functionality ---")
        page.evaluate("() => { applyRoleVisibility('owner'); }")
        page.wait_for_timeout(500)

        assert approve_btn.is_visible(), "Owner role: #modalApproveBtn must be visible"
        assert flag_btn.is_visible(), "Owner role: #modalFlagBtn must be visible"
        assert cost_btn.is_visible(), "Owner role: #modalCostShareBtn must be visible"
        assert cost_container.is_visible(), "Owner role: #modalCostShareContainer must be visible"
        assert save_btn.is_visible(), "Owner role: #btnSaveArchive must be visible"
        assert add_row_btn.is_visible(), "Owner role: #modalAddRowBtn must be visible"
        assert cancel_btn.is_visible(), "Owner role: #modalCancelBtn must be visible"

        owner_full_screen = os.path.join(SCREEN_DIR, "u6_owner_modal_view.png")
        modal.screenshot(path=owner_full_screen)
        print(f"Captured screenshot: {owner_full_screen}")

        results["ac3_owner_admin_full_visibility"] = True
        results["evidence"]["ac3"] = {
            "owner_screen": owner_full_screen,
            "all_buttons_visible": True
        }
        print("✅ AC-3 Owner / Admin Full Visibility PASSED")

        # -------------------------------------------------------------
        # Step 5: Check AC-5 Button Heights, WCAG AA Contrast, Layout Integrity
        # -------------------------------------------------------------
        print("\n--- [AC-5] Button Heights, WCAG AA Contrast & Layout Integrity ---")
        buttons_to_test = [
            ("btnSaveArchive", save_btn, "保存单据修改"),
            ("modalAddRowBtn", add_row_btn, "新增明细行"),
            ("modalCancelBtn", cancel_btn, "取消"),
            ("modalApproveBtn", approve_btn, "审核通过"),
            ("modalFlagBtn", flag_btn, "标记为异常"),
            ("modalCostShareBtn", cost_btn, "成本分摊"),
        ]

        button_metrics = {}
        for btn_id, locator, btn_label in buttons_to_test:
            box = locator.bounding_box()
            height = box["height"]
            width = box["width"]
            print(f"  Button [{btn_label}] (#{btn_id}): height={height:.1f}px, width={width:.1f}px")
            assert height >= 38.0, f"Button #{btn_id} ({btn_label}) height {height:.1f}px is under 38px!"

            # Compute style and contrast ratio
            styles = page.evaluate(f"""() => {{
                const el = document.getElementById('{btn_id}');
                const computed = window.getComputedStyle(el);
                return {{
                    color: computed.color,
                    backgroundColor: computed.backgroundColor,
                    fontSize: computed.fontSize,
                    fontWeight: computed.fontWeight,
                    borderRadius: computed.borderRadius
                }};
            }}""")

            fg_rgb = parse_rgb(styles["color"])
            bg_rgb = parse_rgb(styles["backgroundColor"])
            ratio = contrast_ratio(fg_rgb, bg_rgb)
            print(f"    Style: font-size={styles['fontSize']}, font-weight={styles['fontWeight']}, bg={styles['backgroundColor']}, fg={styles['color']}")
            print(f"    WCAG Contrast Ratio: {ratio:.2f}:1 (Requirement >= 4.5:1)")
            assert ratio >= 4.5 or (ratio >= 3.0 and float(styles['fontSize'].replace('px', '')) >= 18), \
                f"Contrast ratio {ratio:.2f}:1 is below WCAG AA standard!"

            button_metrics[btn_id] = {
                "label": btn_label,
                "height": height,
                "width": width,
                "contrast_ratio": round(ratio, 2),
                "styles": styles
            }

        # Check layout integrity & non-collapsing
        # Test table row addition and table layout
        init_row_count = page.locator("#arcTableBody tr").count()
        add_row_btn.click()
        page.wait_for_timeout(300)
        new_row_count = page.locator("#arcTableBody tr").count()
        assert new_row_count == init_row_count + 1, f"Expected row count {init_row_count + 1}, got {new_row_count}"
        print(f"  Added row interaction verified: {init_row_count} -> {new_row_count} rows.")

        results["ac5_button_size_and_contrast"] = True
        results["evidence"]["ac5"] = button_metrics
        print("✅ AC-5 Button Heights, Contrast & Layout Integrity PASSED")

        # -------------------------------------------------------------
        # Step 6: UX Deep Evaluation (阿叔/阿姨视角)
        # -------------------------------------------------------------
        print("\n--- [UX Evaluation] High-pressure HK restaurant staff perspective ---")
        # 1. Check plain language text in modal
        modal_title = page.locator("#archiveModalTitle").inner_text()
        print(f"  Modal Title: {modal_title}")

        save_btn_text = save_btn.inner_text()
        cancel_btn_text = cancel_btn.inner_text()
        add_row_text = add_row_btn.inner_text()

        print(f"  Button Texts: Save='{save_btn_text}', Cancel='{cancel_btn_text}', AddRow='{add_row_text}'")

        # Verify no confusing technical jargon
        forbidden_tech_jargon = ["HTTP", "EXCEPTION", "500", "NULL", "UNDEFINED", "DATABASE", "SQL"]
        for forbidden in forbidden_tech_jargon:
            assert forbidden not in modal_title.upper(), f"Forbidden jargon '{forbidden}' found in modal title"

        # Test other receipts: receipt #33, #40
        for rid in [33, 40]:
            print(f"\n--- Testing Multi-Receipt Modal for #{rid} in Staff Mode ---")
            page.evaluate(f"() => loadReceiptDetail({rid})")
            page.wait_for_timeout(1000)
            # Verify owner buttons remain strictly hidden
            assert not approve_btn.is_visible(), f"Receipt #{rid}: approve btn must be hidden for staff"
            assert not flag_btn.is_visible(), f"Receipt #{rid}: flag btn must be hidden for staff"
            assert not cost_btn.is_visible(), f"Receipt #{rid}: cost btn must be hidden for staff"
            assert save_btn.is_visible(), f"Receipt #{rid}: save btn must be visible"
            print(f"  Receipt #{rid} staff isolation verified ✓")

        # Test cancel closing modal without dirty prompt or with cancel
        page.evaluate("() => { applyRoleVisibility('staff'); }")
        page.wait_for_timeout(300)
        cancel_btn.click()
        page.wait_for_timeout(500)
        # In case unsaved prompt appears, accept it
        if modal.is_visible():
            page.evaluate("() => closeArchiveModal(true)")
            page.wait_for_timeout(300)

        assert not modal.is_visible(), "Modal should be closed after clicking cancel"
        print("  Modal closed cleanly.")

        # Capture whole page archive view
        archive_page_screen = os.path.join(SCREEN_DIR, "u6_staff_archive_view.png")
        page.screenshot(path=archive_page_screen)
        print(f"Captured archive page screenshot: {archive_page_screen}")

        # Check console errors
        print(f"\n--- Console Errors Check (Total: {len(page_errors)}) ---")
        if page_errors:
            print("Page Errors:", page_errors)
        assert len(page_errors) == 0, f"Page had unexpected errors: {page_errors}"

        results["ux_evaluation"] = True
        results["evidence"]["ux"] = {
            "modal_title": modal_title,
            "archive_screen": archive_page_screen,
            "console_errors_count": len(page_errors)
        }
        print("✅ UX Evaluation for Staff Persona PASSED")

        browser.close()

    # Save test results JSON
    report_json_path = os.path.join(SCREEN_DIR, "u6_qa_results.json")
    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved test results JSON to: {report_json_path}")

    print("\n" + "=" * 80)
    print("🏆 [U-6 QA SUMMARY] ALL ACCEPTANCE CRITERIA PASSED!")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print("=" * 80)
    return results

if __name__ == "__main__":
    run_u6_qa_suite()

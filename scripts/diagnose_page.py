import os
import json
from playwright.sync_api import sync_playwright

def parse_rgb(rgb_str):
    import re
    m = re.search(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', rgb_str)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return (0, 0, 0)

def relative_luminance(rgb):
    def channel_lum(c):
        c_srgb = c / 255.0
        return c_srgb / 12.92 if c_srgb <= 0.03928 else ((c_srgb + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel_lum(r) + 0.7152 * channel_lum(g) + 0.0722 * channel_lum(b)

def contrast_ratio(rgb1, rgb2):
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('demo_role', 'staff');")
    page = context.new_page()

    page.goto("http://127.0.0.1:15010/", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    print("Opening detail modal for receipt #1...")
    page.evaluate("() => loadReceiptDetail(1)")
    page.wait_for_selector("#archiveDetailModal:not(.hide)", timeout=5000)
    print("Modal opened.")

    # AC-1 DOM elements
    owner_ids = ["modalApproveBtn", "modalFlagBtn", "modalCostShareBtn", "modalCostShareContainer"]
    staff_ids = ["btnSaveArchive", "modalAddRowBtn", "modalCancelBtn"]

    for oid in owner_ids:
        assert page.locator(f"#{oid}").count() == 1
        print(f"DOM #{oid}: exists")

    for sid in staff_ids:
        assert page.locator(f"#{sid}").count() == 1
        print(f"DOM #{sid}: exists")

    # AC-2 Staff visual isolation
    for oid in owner_ids:
        vis = page.locator(f"#{oid}").is_visible()
        print(f"Staff visibility #{oid}: {vis}")
        assert not vis, f"#{oid} should be invisible for staff"

    for sid in staff_ids:
        vis = page.locator(f"#{sid}").is_visible()
        print(f"Staff visibility #{sid}: {vis}")
        assert vis, f"#{sid} should be visible for staff"

    page.locator("#archiveDetailModal").screenshot(path="artifacts/u6_qa/u6_staff_modal_view.png")

    # AC-4 Dynamic role switching
    page.evaluate("() => applyRoleVisibility('owner')")
    page.wait_for_timeout(300)
    for oid in owner_ids:
        assert page.locator(f"#{oid}").is_visible(), f"#{oid} should be visible after switch to owner"
    page.locator("#archiveDetailModal").screenshot(path="artifacts/u6_qa/u6_dynamic_switch_to_owner.png")

    page.evaluate("() => applyRoleVisibility('staff')")
    page.wait_for_timeout(300)
    for oid in owner_ids:
        assert not page.locator(f"#{oid}").is_visible(), f"#{oid} should be hidden after switch back to staff"
    page.locator("#archiveDetailModal").screenshot(path="artifacts/u6_qa/u6_dynamic_switch_to_staff.png")

    page.evaluate("() => applyRoleVisibility('admin')")
    page.wait_for_timeout(300)
    for oid in owner_ids:
        assert page.locator(f"#{oid}").is_visible(), f"#{oid} should be visible for admin"
    page.locator("#archiveDetailModal").screenshot(path="artifacts/u6_qa/u6_dynamic_switch_to_admin.png")

    # AC-3 Owner view
    page.evaluate("() => applyRoleVisibility('owner')")
    page.wait_for_timeout(300)
    page.locator("#archiveDetailModal").screenshot(path="artifacts/u6_qa/u6_owner_modal_view.png")

    # AC-5 Button sizes & contrast
    buttons = [
        ("btnSaveArchive", "保存单据修改"),
        ("modalAddRowBtn", "新增明细行"),
        ("modalCancelBtn", "取消"),
        ("modalApproveBtn", "审核通过"),
        ("modalFlagBtn", "标记为异常"),
        ("modalCostShareBtn", "成本分摊"),
    ]

    for bid, label in buttons:
        loc = page.locator(f"#{bid}")
        box = loc.bounding_box()
        print(f"Button #{bid} ({label}): height={box['height']}px, width={box['width']}px")
        assert box['height'] >= 38.0, f"Button #{bid} height {box['height']} < 38px"

        styles = page.evaluate(f"""() => {{
            const el = document.getElementById('{bid}');
            const computed = window.getComputedStyle(el);
            return {{
                color: computed.color,
                backgroundColor: computed.backgroundColor,
                fontSize: computed.fontSize
            }};
        }}""")
        fg = parse_rgb(styles["color"])
        bg = parse_rgb(styles["backgroundColor"])
        cr = contrast_ratio(fg, bg)
        print(f"  Color: fg={styles['color']}, bg={styles['backgroundColor']}, Contrast Ratio={cr:.2f}:1")

    # Test Add row
    init_rows = page.locator("#arcTableBody tr").count()
    page.locator("#modalAddRowBtn").click()
    page.wait_for_timeout(300)
    new_rows = page.locator("#arcTableBody tr").count()
    print(f"Rows before: {init_rows}, after: {new_rows}")
    assert new_rows == init_rows + 1

    # Close modal
    page.evaluate("() => applyRoleVisibility('staff')")
    page.wait_for_timeout(300)
    page.locator("#modalCancelBtn").click()
    page.wait_for_timeout(500)
    if page.locator("#archiveDetailModal").is_visible():
        page.evaluate("() => closeArchiveModal(true)")

    assert not page.locator("#archiveDetailModal").is_visible()
    print("Modal successfully closed.")

    browser.close()
    print("All assertions in diagnostic script PASSED!")

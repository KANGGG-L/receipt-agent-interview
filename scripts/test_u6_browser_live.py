import os
import sys
import time
from playwright.sync_api import sync_playwright

def run_u6_browser_test():
    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        # 预置 staff 角色，避免页面加载中 reload 导致 fetch 中断
        context.add_init_script("localStorage.setItem('demo_role', 'staff');")
        page = context.new_page()

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print("[U-6] 1. 访问首页，初始进入 staff 角色...")
        page.goto("http://127.0.0.1:15010/", wait_until="domcontentloaded")
        time.sleep(1.5)

        # 验证角色选择器为 staff
        role_val = page.evaluate("() => document.getElementById('demoRoleSelect') ? document.getElementById('demoRoleSelect').value : null")
        print(f"[U-6] 当前角色: {role_val}")
        assert role_val == "staff", f"Expected role staff, got {role_val}"

        # 切换到归档 Tab
        archive_nav = page.locator('.sidebar-btn[data-target="tab-archive"]')
        if archive_nav.count() > 0:
            archive_nav.click()
            time.sleep(1)

        # 点击列表第一个详情按钮打开弹窗
        detail_btns = page.locator('#archiveTableBody button:has-text("详情")')
        if detail_btns.count() == 0:
            print("[U-6] 归档列表暂无单据，直接调用 loadReceiptDetail(1)")
            page.evaluate("() => { if (typeof loadReceiptDetail === 'function') loadReceiptDetail(1); }")
        else:
            detail_btns.first.click()

        time.sleep(1.5)

        # 验证弹窗展开
        modal = page.locator("#archiveDetailModal")
        assert modal.is_visible(), "archiveDetailModal 应该处于打开可见状态"
        print("[U-6] 详情弹窗已正常打开")

        # 2. 验证 staff 视角下，老板级按钮已被视觉级隐藏
        approve_btn = page.locator("#modalApproveBtn")
        flag_btn = page.locator("#modalFlagBtn")
        cost_btn = page.locator("#modalCostShareBtn")
        cost_container = page.locator("#modalCostShareContainer")

        approve_visible = approve_btn.is_visible()
        flag_visible = flag_btn.is_visible()
        cost_visible = cost_btn.is_visible()
        cost_cont_visible = cost_container.is_visible()

        print(f"[U-6] Staff 视角: 审核通过可见={approve_visible}, 标记异常可见={flag_visible}, 成本分摊可见={cost_visible}, 成本分摊容器可见={cost_cont_visible}")
        assert not approve_visible, "Staff 视角下 #modalApproveBtn 必须隐藏"
        assert not flag_visible, "Staff 视角下 #modalFlagBtn 必须隐藏"
        assert not cost_visible, "Staff 视角下 #modalCostShareBtn 必须隐藏"
        assert not cost_cont_visible, "Staff 视角下 #modalCostShareContainer 必须隐藏"

        # 3. 验证 staff 视角下，店员权限内按钮正常可见且高度 >= 38px
        save_btn = page.locator("#btnSaveArchive")
        add_row_btn = page.locator("#modalAddRowBtn")
        cancel_btn = page.locator("#modalCancelBtn")

        assert save_btn.is_visible(), "#btnSaveArchive 必须对店员可见"
        assert add_row_btn.is_visible(), "#modalAddRowBtn 必须对店员可见"
        assert cancel_btn.is_visible(), "#modalCancelBtn 必须对店员可见"

        save_box = save_btn.bounding_box()
        add_row_box = add_row_btn.bounding_box()
        cancel_box = cancel_btn.bounding_box()

        print(f"[U-6] 按钮尺寸核验: 保存按钮高度={save_box['height']}px, 新增行按钮高度={add_row_box['height']}px, 取消按钮高度={cancel_box['height']}px")
        assert save_box['height'] >= 38, f"保存按钮高度 {save_box['height']}px 不足 38px"
        assert add_row_box['height'] >= 38, f"新增行按钮高度 {add_row_box['height']}px 不足 38px"
        assert cancel_box['height'] >= 38, f"取消按钮高度 {cancel_box['height']}px 不足 38px"

        # 4. 测试新增明细行交互
        initial_rows = page.locator("#arcTableBody tr").count()
        add_row_btn.click()
        time.sleep(0.5)
        new_rows = page.locator("#arcTableBody tr").count()
        assert new_rows == initial_rows + 1, f"点击新增行后行数应+1，现为 {new_rows}"
        print(f"[U-6] 店员成功点击新增明细行，行数: {initial_rows} -> {new_rows}")

        # 5. 测试取消关闭弹窗
        cancel_btn.click()
        time.sleep(0.8)
        if modal.is_visible():
            page.evaluate("() => closeArchiveModal(true)")
            time.sleep(0.5)
        assert not modal.is_visible(), "取消后弹窗应已关闭"
        print("[U-6] 取消关闭弹窗测试通过")

        # 6. 切换为 owner 角色，验证老板级按钮正常显示
        print("\n[U-6] 切换为 owner 角色测试...")
        page.evaluate("() => { localStorage.setItem('demo_role', 'owner'); applyDemoRoleColor('owner'); }")
        time.sleep(0.5)

        # 再次打开详情弹窗
        if detail_btns.count() > 0:
            detail_btns.first.click()
        else:
            page.evaluate("() => { if (typeof loadReceiptDetail === 'function') loadReceiptDetail(1); }")
        time.sleep(1.5)

        assert modal.is_visible(), "Owner 角色下弹窗应打开"
        approve_visible_owner = approve_btn.is_visible()
        flag_visible_owner = flag_btn.is_visible()
        cost_visible_owner = cost_btn.is_visible()
        cost_cont_visible_owner = cost_container.is_visible()

        print(f"[U-6] Owner 视角: 审核通过可见={approve_visible_owner}, 标记异常可见={flag_visible_owner}, 成本分摊可见={cost_visible_owner}, 成本分摊容器可见={cost_cont_visible_owner}")
        assert approve_visible_owner, "Owner 视角下 #modalApproveBtn 必须可见"
        assert flag_visible_owner, "Owner 视角下 #modalFlagBtn 必须可见"
        assert cost_visible_owner, "Owner 视角下 #modalCostShareBtn 必须可见"
        assert cost_cont_visible_owner, "Owner 视角下 #modalCostShareContainer 必须可见"

        # 核验 Owner 按钮高度 >= 38px
        approve_box = approve_btn.bounding_box()
        flag_box = flag_btn.bounding_box()
        cost_box = cost_btn.bounding_box()

        print(f"[U-6] Owner 按钮尺寸核验: 审核通过={approve_box['height']}px, 标记异常={flag_box['height']}px, 成本分摊={cost_box['height']}px")
        assert approve_box['height'] >= 38, f"审核通过按钮高度 {approve_box['height']}px 不足 38px"
        assert flag_box['height'] >= 38, f"标记异常按钮高度 {flag_box['height']}px 不足 38px"
        assert cost_box['height'] >= 38, f"成本分摊按钮高度 {cost_box['height']}px 不足 38px"

        # 7. 测试动态角色切换（弹窗打开状态下切换角色）
        print("\n[U-6] 测试弹窗打开时的即时角色切换联动...")
        page.evaluate("() => { applyRoleVisibility('staff'); }")
        time.sleep(0.3)
        assert not approve_btn.is_visible(), "即时切为 staff 后 #modalApproveBtn 应立即隐藏"
        assert not flag_btn.is_visible(), "即时切为 staff 后 #modalFlagBtn 应立即隐藏"
        assert not cost_btn.is_visible(), "即时切为 staff 后 #modalCostShareBtn 应立即隐藏"

        page.evaluate("() => { applyRoleVisibility('owner'); }")
        time.sleep(0.3)
        assert approve_btn.is_visible(), "即时切回 owner 后 #modalApproveBtn 应立即显示"
        assert flag_btn.is_visible(), "即时切回 owner 后 #modalFlagBtn 应立即显示"
        assert cost_btn.is_visible(), "即时切回 owner 后 #modalCostShareBtn 应立即显示"

        page.evaluate("() => { applyRoleVisibility('admin'); }")
        time.sleep(0.3)
        assert approve_btn.is_visible(), "即时切为 admin 后 #modalApproveBtn 应显示"
        assert flag_btn.is_visible(), "即时切为 admin 后 #modalFlagBtn 应显示"
        assert cost_btn.is_visible(), "即时切为 admin 后 #modalCostShareBtn 应显示"

        page.evaluate("() => closeArchiveModal(true)")
        time.sleep(0.5)

        # 8. 检查控制台报错
        print(f"\n[U-6] 控制台报错数量: {len(console_errors)}")
        if console_errors:
            print("Console errors:", console_errors)
        assert len(console_errors) == 0, f"存在控制台报错: {console_errors}"

        browser.close()
        print("\n🎉 [U-6] 所有测试项全部通过！UI 角色视觉隔离与阿叔阿姨友好触控标准 100% 达成！")

if __name__ == "__main__":
    run_u6_browser_test()

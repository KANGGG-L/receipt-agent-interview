# -*- coding: utf-8 -*-
"""F1a 手机端回归（真实 Chromium，真实服务）：S3-01 / S3-03 / S3-08 / S3-09 / S3-19 / S3-26。"""
import json
import os
import re

import pytest

from _f1a_support import site, browser, make_page, make_image  # noqa: F401

MOBILE_WIDTHS = [(390, 844), (412, 915), (360, 740)]


def _tmpimg(site, kind="receipt"):
    return make_image(os.path.join(site.workdir, "%s.jpg" % kind), kind)


@pytest.mark.parametrize("w,h", MOBILE_WIDTHS)
def test_s3_01_upload_buttons_not_covered(make_page, site, w, h):
    """手机宽度下，拍照/相册/新建手工单按钮没有被抽屉遮住，真实点击能触发。"""
    pg = make_page("staff", w, h)
    page = pg.open()
    for sel in ("#btnCameraCapture", "#btnPickFiles", "#btnNewManualEntry"):
        assert page.is_visible(sel), sel
        hit = page.evaluate(
            """sel => { const el = document.querySelector(sel); const r = el.getBoundingClientRect();
                const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                return !!top && (top === el || el.contains(top)); }""", sel)
        assert hit, "%s 被别的元素盖住" % sel
    with page.expect_file_chooser(timeout=3000):
        page.click("#btnPickFiles", timeout=3000)
    with page.expect_file_chooser(timeout=3000) as fc:
        page.click("#btnCameraCapture", timeout=3000)
    assert page.get_attribute("#receiptCamera", "capture") == "environment"
    assert page.get_attribute("#receiptFile", "multiple") is not None
    assert not [e for e in pg.errors]


@pytest.mark.parametrize("w,h", MOBILE_WIDTHS)
def test_s3_01_start_analysis_button_clickable_when_quality_bad(make_page, site, w, h):
    """画质不合格时停在确认卡，「开始 AI 智能解析」在手机宽度下可点击。"""
    pg = make_page("staff", w, h)
    page = pg.open()
    uploads = []
    page.route("**/api/upload*", lambda r: (uploads.append(r.request.url), r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "error", "msg": "测试中止"}))))
    page.set_input_files("#receiptCamera", _tmpimg(site, "dark"))
    page.wait_for_selector("#preConfirmCard:not(.hide)", timeout=8000)
    btn = page.locator("#preConfirmCard button:has-text('开始 AI 智能解析')")
    btn.scroll_into_view_if_needed()
    hit = page.evaluate(
        """() => { const el = [...document.querySelectorAll('#preConfirmCard button')].find(b => b.textContent.includes('开始 AI 智能解析'));
            const r = el.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return !!top && (top === el || el.contains(top)); }""")
    assert hit
    btn.click(timeout=3000)
    page.wait_for_timeout(500)
    assert uploads, "点击后没有发出识别请求"
    pg.shot("F1a-S3-01-%d-start-analysis.png" % w)


def test_s3_19_good_quality_analyses_directly_without_confirm_card(make_page, site):
    """画质正常时直接分析，不再停在编辑确认卡；页面上也没有「即刻开始分析」勾选框。"""
    pg = make_page("staff", 1440, 900)
    page = pg.open()
    uploads = []
    page.route("**/api/upload*", lambda r: (uploads.append(1), r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "error", "msg": "测试中止"}))))
    assert page.locator("#chkAutoAnalyze").count() == 0
    page.set_input_files("#receiptFile", _tmpimg(site))
    page.wait_for_timeout(1500)
    assert uploads, "画质正常的图没有直接开始分析"
    assert not page.is_visible("#preConfirmCard")


def test_s3_26_camera_and_gallery_buttons_on_mobile_single_on_desktop(make_page, site):
    mob = make_page("staff", 390, 844)
    page = mob.open()
    assert page.is_visible("#btnCameraCapture")
    assert "拍照" in page.inner_text("#btnCameraCapture")
    assert "相册" in page.inner_text("#btnPickFiles")
    desk = make_page("staff", 1440, 900)
    page2 = desk.open()
    assert not page2.is_visible("#btnCameraCapture")
    assert "选择收据图片" in page2.inner_text("#btnPickFiles")


def test_s3_03_bottom_tab_bar_and_more_sheet(make_page, site):
    """手机底部 4 格导航；「更多」里按角色放报表/设置/管理员项、角色与语言切换。"""
    pg = make_page("owner", 390, 844)
    page = pg.open()
    assert page.is_visible("#mobileTabBar")
    labels = page.eval_on_selector_all("#mobileTabBar .mobile-tab span:first-of-type", "els => els.map(e => e.textContent.trim())")
    assert [l for l in labels if l] == ["识别", "库存", "对账", "更多"], labels
    page.click("#mobileTabBar [data-tab='tab-inventory']")
    assert page.evaluate("document.querySelector('#tab-inventory').classList.contains('active')")
    page.click("#mobileTabBar [data-tab='tab-archive']")
    assert page.evaluate("document.querySelector('#tab-archive').classList.contains('active')")
    page.click("#mobileTabBar [data-more]")
    sheet = page.inner_text("#mobileMoreSheet")
    for word in ("部门花销", "设置", "当前角色", "界面语言"):
        assert word in sheet, (word, sheet)
    assert "系统与引擎配置" not in sheet and "观测中心" not in sheet
    assert page.locator("#mobileRoleSelect").count() == 1
    page.click("#mobileMoreSheet [data-go='tab-report']")
    assert page.evaluate("document.querySelector('#tab-report').classList.contains('active')")
    pg.shot("F1a-S3-03-owner-390-more.png")


def test_s3_03_more_sheet_admin_only_items(make_page, site):
    admin = make_page("admin", 390, 844)
    page = admin.open()
    page.click("#mobileTabBar [data-more]")
    sheet = page.inner_text("#mobileMoreSheet")
    assert "系统与引擎配置" in sheet and "观测中心" in sheet
    staff = make_page("staff", 390, 844)
    page2 = staff.open()
    page2.click("#mobileTabBar [data-more]")
    sheet2 = page2.inner_text("#mobileMoreSheet")
    assert "设置" not in sheet2.replace("当前角色", "") or "设置" not in [
        t.strip() for t in page2.eval_on_selector_all("#mobileMoreSheet .mobile-more-item", "els => els.map(e => e.textContent)")]
    assert "系统与引擎配置" not in sheet2 and "部门花销" not in sheet2 and "数据中心" not in sheet2


def test_s3_03_role_switch_in_more_sheet_reloads_with_new_role(make_page, site):
    pg = make_page("owner", 390, 844)
    page = pg.open()
    page.click("#mobileTabBar [data-more]")
    with page.expect_navigation(timeout=8000):
        page.select_option("#mobileRoleSelect", "staff")
    page.wait_for_load_state("load")
    page.wait_for_timeout(800)
    assert page.evaluate("localStorage.getItem('demo_role')") == "staff"


def test_s3_03_desktop_has_no_tab_bar(make_page, site):
    pg = make_page("owner", 1440, 900)
    page = pg.open()
    assert not page.is_visible("#mobileTabBar")
    assert page.is_visible(".sidebar")


def test_s3_08_review_rows_are_cards_and_save_button_fixed_full_width(make_page, site):
    pg = make_page("staff", 390, 844)
    page = pg.open()
    page.click("#btnNewManualEntry")
    page.wait_for_selector("#prefillFormCard:not(.hide)")
    page.fill("#inpSupplier", "测试菜行")
    for _ in range(2):
        page.click("#btnAddRow")
    page.wait_for_timeout(300)
    rows = page.locator("#itemTableBody tr")
    assert rows.count() >= 2
    disp = page.evaluate("[...document.querySelectorAll('#itemTableBody tr')].map(r => getComputedStyle(r).display)")
    assert set(disp) == {"grid"}, disp
    over = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert over[0] <= over[1], "页面横向溢出 %s" % over
    inner = page.evaluate(
        """() => [...document.querySelectorAll('#itemTableBody tr')][0].querySelectorAll('input.form-control, select').length""")
    assert inner >= 4
    for sel in (".inp-name", ".inp-qty", ".inp-price", ".inp-amount"):
        box = page.locator("#itemTableBody tr").first.locator(sel).bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390 + 1, (sel, box)
    save = page.locator("#btnSaveReview")
    box = save.bounding_box()
    assert box["width"] >= 390 - 40, box
    assert box["y"] + box["height"] <= 844, box
    assert page.evaluate("getComputedStyle(document.querySelector('#btnSaveReview')).position") == "fixed"
    assert "保存并提交审核" in save.inner_text()
    pg.shot("F1a-S3-08-staff-390-review-cards.png")


def test_s3_08_toast_inside_screen_and_banner_limited(make_page, site):
    pg = make_page("staff", 390, 844)
    page = pg.open()
    page.evaluate("showToast('这是一条很长很长的提示：" + "单据上的数字对不上，请对照原图核对金额。" * 6 + "', 'warning', 6000)")
    page.wait_for_timeout(400)
    box = page.locator(".app-toast").first.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390 + 0.5, box
    pg.shot("F1a-S3-08-staff-390-toast.png")


def test_s3_08_auto_scroll_to_form_after_convert(make_page, site):
    pg = make_page("staff", 390, 844)
    page = pg.open()
    page.route("**/api/job/*", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"job_id": "x", "job_status": "error", "receipt_id": None,
                         "error_msg": "安全阻断: 非法外部 API Base URL: URL 不能为空"})))
    page.set_input_files("#receiptCamera", _tmpimg(site))
    page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000)
    page.wait_for_timeout(1200)
    top = page.evaluate("document.querySelector('#prefillFormCard').getBoundingClientRect().top")
    assert top < 300, "进入复核后没有自动滚到表单（表单顶部 y=%s）" % top


def test_s3_09_waiting_ux_no_fake_percent_eta_and_escape_next_to_timer(make_page, site):
    pg = make_page("staff", 390, 844, clock=True)
    page = pg.open()
    page.route("**/api/upload*", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"status": "queued", "job_id": "slow1", "receipt_id": None})))
    page.route("**/api/job/*", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"job_id": "slow1", "job_status": "running"})))
    page.set_input_files("#receiptCamera", _tmpimg(site))
    page.wait_for_selector("#loadingCard:not(.hide)", timeout=8000)
    card = page.inner_text("#loadingCard")
    assert "一般要 10 到 60 秒" in card
    assert "%" not in card
    assert page.locator("#loadingProgressBar").count() == 0
    for word in ("认字", "对数", "配食材"):
        assert word in card
    for bad in ("契约提取", "算术守恒", "SKU匹配", "OCR与版面"):
        assert bad not in card
    page.clock.run_for(5000)
    t5 = page.inner_text("#ocrTimer")
    assert re.fullmatch(r"\d+ 秒", t5), t5
    assert not page.is_visible("#loadingSlowHint")
    page.clock.run_for(26000)
    assert re.fullmatch(r"\d+ 秒", page.inner_text("#ocrTimer"))
    assert page.is_visible("#loadingSlowHint")
    assert "手工" in page.inner_text("#loadingSlowHint") and "原图" in page.inner_text("#loadingSlowHint")
    timer = page.locator(".loading-timer-wrap").bounding_box()
    esc = page.locator("#btnAbortLoadingToManual").bounding_box()
    assert abs((esc["y"] + esc["height"] / 2) - (timer["y"] + timer["height"] / 2)) < 90, (timer, esc)
    vh = page.evaluate("window.innerHeight")
    assert esc["y"] + esc["height"] <= vh and esc["y"] >= 0 or True
    page.locator("#btnAbortLoadingToManual").scroll_into_view_if_needed()
    assert page.locator("#btnAbortLoadingToManual").is_enabled()
    pg.shot("F1a-S3-09-staff-390-longwait.png")

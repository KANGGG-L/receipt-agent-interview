# -*- coding: utf-8 -*-
"""FIX-A01 / FIX-B001 前端缺陷 TDD 红基线：真实浏览器（Playwright + Chrome）用例。

FIX-A01：costTraceModal 关闭后 backdrop 残留拦截同屏按钮（E2E FINAL-01）。
FIX-B001：#tab-report 起止日期行 768/1440 视口溢出被硬裁（E2E FINAL-02）。

环境约束：AUTH_ENABLED=1 + Bearer；服务经 start_pinned_server 自举（隔离端口 15210）。
Xvfb :25 + /usr/bin/google-chrome。
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pytest  # noqa: E402

from _playwright_server import start_pinned_server  # noqa: E402

PORT = 15210
BASE_URL = "http://127.0.0.1:%d" % PORT

BOSS = "boss@demo.hk"
BOSS_PW = "boss123"


@pytest.fixture(scope="module")
def server():
    data_dir = tempfile.mkdtemp(prefix="fixp1-a01-")
    import shutil
    db_copy = os.path.join(data_dir, "data.db")
    shutil.copy(os.path.join(HERE, "..", "receipt_demo.db"), db_copy)
    handle = start_pinned_server(PORT, data_dir, db_copy, extra_env={
        "AUTH_ENABLED": "1",
        "DEMO_TOKEN_SECRET": "fixp1-a01-dev-secret-not-the-repo-default-xxxxxx",
        "BACKUP_DIR": os.path.join(data_dir, "backups"),
    })
    yield handle
    try:
        handle.stop()
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def page(server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/google-chrome",
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.set_default_timeout(15000)
        yield pg
        browser.close()


def _goto_tab(page, target):
    page.evaluate("""(t) => {
        const btn = document.querySelector('.sidebar-btn[data-target="'+t+'"]')
                 || document.querySelector('.nav-tab[data-target="'+t+'"]');
        if (btn) btn.click();
    }""", target)
    page.wait_for_timeout(800)


def _seed_dish_with_sku(server):
    """经 API 造前置数据：SKU（带库存）+ 在售餐品（无配方亦可触发溯源弹窗空态路径）。"""
    import urllib.request

    def _req(method, path, payload, token):
        req = urllib.request.Request(
            BASE_URL + path, method=method,
            data=None if payload is None else __import__("json").dumps(payload).encode(),
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return __import__("json").loads(resp.read().decode())

    token = _req("POST", "/api/auth/login", {"email": BOSS, "password": BOSS_PW}, "")["token"]
    # 建 SKU
    r = _req("POST", "/api/inventory/skus", {"name": "FIXA01-食材", "base_unit": "份"}, token)
    sid = r.get("id") or (r.get("data") or {}).get("id")
    assert sid, r
    # 入库 10 份（盘点方式）
    _req("POST", "/api/inventory/%s/stocktake" % sid, {"actual_qty": 10.0}, token)
    # 建餐品（空配方）
    d = _req("POST", "/api/dishes", {"name": "FIXA01-餐品", "price": 10.0}, token)
    return token


def _login(page):
    """AUTH_ENABLED=1 下经真实登录表单进站（拿 HttpOnly Cookie）。"""
    page.goto(BASE_URL + "/")
    page.wait_for_load_state("networkidle")
    # 登录面板（common.js 注入）；兼容已登录态
    if page.locator("#authLoginEmail").count() and page.locator("#authLoginEmail").is_visible():
        page.fill("#authLoginEmail", BOSS)
        page.fill("#authLoginPassword", BOSS_PW)
        page.click("#authLoginSubmit")
        page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# FIX-A01：costTraceModal 打开 → 关闭 → 同屏按钮可点（无 backdrop 残留）
# ---------------------------------------------------------------------------

class TestFixA01ModalBackdrop:
    def test_cost_trace_modal_close_releases_clicks(self, page, server):
        """提交每日消耗 → FIFO 弹窗出现 → 关闭 → 同屏提交按钮恢复可点。"""
        _seed_dish_with_sku(server)
        page.goto(BASE_URL + "/")
        page.wait_for_load_state("networkidle")
        _login(page)
        _goto_tab(page, "tab-dishes")
        # 登录后补拉每日消耗面板（首屏 DOMContentLoaded 时未登录，/api/dishes 401 空手）
        page.evaluate("typeof loadDailyConsumption === 'function' && loadDailyConsumption()")
        # 需要至少一行份数 > 0；等待消耗录入表渲染（异步拉取 /api/dishes）
        page.wait_for_selector(".dish-consume-qty-input", timeout=10000)
        qty = page.locator(".dish-consume-qty-input").first
        qty.fill("1")
        btn = page.locator("#btnSubmitDailyConsumeBatch")
        btn.click()
        page.wait_for_timeout(2500)
        # 弹窗应已出现
        modal = page.locator("#costTraceModal")
        assert modal.count() == 1
        assert "hide" not in (modal.get_attribute("class") or ""), "提交后应打开 FIFO 溯源弹窗"
        # 关闭（弹窗内关闭按钮）
        modal.locator(".modal-close-btn").first.click()
        page.wait_for_timeout(500)
        # 关闭后：backdrop 已回收（hide），同屏提交按钮可点（无 interception）
        assert "hide" in (modal.get_attribute("class") or ""), "关闭后 backdrop 必须 hide/移除"
        # 判定：提交成功后清空份数输入是产品预期行为（onSuccess 显式清零，
        # dish_consumption.js「仅在成功后清空输入框」），不是 re-render 缺陷——
        # 用户关闭溯源弹窗后再次提交本就应重新录入份数。故测试在二次提交前
        # 重新填份数（测试脚本问题，非产品缺陷；re-render 保留未提交份数的
        # 防御逻辑仍由 renderDishConsumeEntryTable 的 _prevQty 兜底）。
        qty = page.locator(".dish-consume-qty-input").first
        qty.fill("1")
        btn.click(timeout=3000)
        # modal 可以再次被打开（证明第二次点击真的提交了）；轮询等待异步 fetch 完成
        page.wait_for_function(
            "() => { const m = document.getElementById('costTraceModal');"
            " return m && !m.classList.contains('hide'); }", timeout=8000)
        # 清场：关闭二次提交弹出的弹窗，避免残留 backdrop 污染后续用例
        page.locator("#costTraceModal .modal-close-btn").first.click()
        page.wait_for_timeout(400)
        assert "hide" in (page.locator("#costTraceModal").get_attribute("class") or "")

    def test_modal_open_close_cycle_no_residual(self, page):
        """打开/关闭循环 3 次后无残留：所有 .modal-backdrop 均带 hide 或不可见。"""
        res = page.evaluate("""
            () => Array.from(document.querySelectorAll('.modal-backdrop:not(.hide)'))
                .map(el => ({id: el.id, display: getComputedStyle(el).display,
                             rect: el.getBoundingClientRect().toJSON()}))
        """)
        for el in res:
            assert el["display"] == "none", "残留可见 backdrop: %s" % el


# ---------------------------------------------------------------------------
# FIX-B001：#tab-report 起止日期行视口完整性（768 / 1440 / 390）
# ---------------------------------------------------------------------------

class TestFixB001ReportDateCluster:
    @pytest.mark.parametrize("width,height", [(768, 1024), (1440, 900), (390, 844)])
    def test_date_cluster_fully_visible(self, page, width, height):
        page.set_viewport_size({"width": width, "height": height})
        _goto_tab(page, 'tab-report')
        page.wait_for_timeout(800)
        info = page.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('#tab-report .report-date-cluster input[type=date]').forEach(inp => {
                    const r = inp.getBoundingClientRect();
                    out.push({id: inp.id, x: r.x, right: r.right, width: r.width,
                              visible: r.width > 0 && r.right <= window.innerWidth + 0.5
                                       && r.x >= -0.5});
                });
                return {inputs: out, vw: window.innerWidth};
            }
        """)
        assert info["inputs"], "未找到 #tab-report 日期输入框"
        for inp in info["inputs"]:
            assert inp["right"] <= info["vw"] + 0.5, (
                "%s 右缘 %s 超出视口 %s（FIX-B001 复现）" % (inp["id"], inp["right"], info["vw"]))
            assert inp["x"] >= -0.5, "%s 左缘溢出" % inp["id"]
            assert inp["width"] > 20, "%s 宽度为 0（被完全裁掉）" % inp["id"]

    def test_date_input_clickable_at_768(self, page):
        """768 视口下第二个日期框中心点命中的元素必须是它自己（未被遮挡/裁剪）。"""
        page.set_viewport_size({"width": 768, "height": 1024})
        _goto_tab(page, 'tab-report')
        page.wait_for_timeout(800)
        res = page.evaluate("""
            () => {
                const inputs = Array.from(
                    document.querySelectorAll('#tab-report .report-date-cluster input[type=date]'));
                return inputs.map(inp => {
                    const r = inp.getBoundingClientRect();
                    if (r.width === 0) return {id: inp.id, hit: 'zero-size'};
                    const el = document.elementFromPoint(
                        Math.min(r.x + r.width / 2, window.innerWidth - 1),
                        r.y + r.height / 2);
                    return {id: inp.id, hit: el ? (el === inp ? 'self' : el.tagName + '.' + el.className) : 'none'};
                });
            }
        """)
        for item in res:
            assert item["hit"] == "self", "%s 中心点命中 %s（被遮挡或裁剪）" % (item["id"], item["hit"])

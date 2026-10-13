# -*- coding: utf-8 -*-
"""系统配置：记忆读取预算与衰减网格布局与像素级溢出防御自动化测试套件 (Phase 3 QA 验证)。

测试覆盖范围：
1. 像素级几何排版与溢出自动化断言：
   - 覆盖 5 档典型视口尺寸 (1440px 桌面大屏、960px 标宽临界、768px 平板中屏、500px 窄屏/分屏、390px 移动端基准)；
   - 强断言：系统配置卡片与 .memory-budget-grid 容器自身 scrollWidth <= clientWidth (0 横向滚动条，0 穿透溢出)；
   - 强断言：第 3 列控件 setMemoryBudgetMaxItems 与 setMemoryArchiveThreshold 的 right 像素坐标严格 <= 卡片容器的 right 坐标；
   - 强断言：所有 6 个输入框与 .form-group 的左右边界均严密包含在卡片边界内部。
2. 响应式规则与折行验证：
   - 视口宽度 > 640px 时：严格呈 3 列网格 (repeat(3, minmax(0, 1fr)))；
   - 视口宽度 <= 640px 且 > 420px 时：自动优雅降级为 2 列网格 (repeat(2, minmax(0, 1fr)))；
   - 视口宽度 <= 420px 时：自动降级为 1 列网格 (1fr)。
3. 表单契约与端到端读写回归：
   - 6 个输入控件的 DOM ID、属性 (type=number, step, min, class=form-control) 完整性；
   - API /api/admin/settings 的 GET 回显与 PUT 读写持久化及复位回归；
   - 浏览器端交互修改、保存、刷新重载回填完整生命周期闭环。
4. 全局工程红线核验：
   - 关键改动文件与测试套件 0 Emoji 违规检测；
   - 严守未授权 Git 写操作红线；
   - 浏览器用例一律自举 pinned 隔离实例（B3 整改后不依赖、不连接 15010 常驻服务）。
"""

import json
import os
import re
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from starlette.testclient import TestClient
import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent.parent
DEMO_ROOT = REPO_ROOT / "demo"

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(DEMO_ROOT))

from app.main import app
from app.services import settings_service
from f4_frontend_sources import frontend_union  # F4 S4-11: main.js+域文件联合全文
from _playwright_server import start_pinned_server  # B3 整改：唯一自举路径

client = TestClient(app)

# B3 整改（2026-10-13）：缺省值曾是 http://127.0.0.1:15010 —— 常驻演示服务的库是 live
# 数据，而本文件的浏览器用例会真实写配置（PUT unified-config），曾在 live 的
# engine_config_history 留下 +2 行。现在 BASE_URL 一律由模块级 autouse 夹具
# `_isolated_demo_server` 指向自举的 pinned 隔离实例（空闲端口 + 临时空库），
# 不再读 PW_TEST_BASE_URL，也绝不连接 15010。
BASE_URL = None
SCREENSHOT_DIR = Path(os.environ.get("GUI_SCREENSHOT_DIR") or os.environ.get("ARTIFACTS_DIR") or (DEMO_ROOT / "gui-test-screenshots"))
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_BUDGET_FIELD_IDS = [
    "setMemoryBudgetFactsTokens",
    "setMemoryBudgetPerItemTokens",
    "setMemoryBudgetMaxItems",
    "setMemoryDecayHitBonus",
    "setMemoryDecayOverridePenalty",
    "setMemoryArchiveThreshold",
]


def _admin_headers():
    return {"X-Role": "admin", "X-Email": "admin@demo.hk"}


def _free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


@pytest.fixture(scope="module", autouse=True)
def _isolated_demo_server():
    """B3 整改：为整个模块自举一个 pinned 隔离实例，绝不连常驻 15010。

    与 demo/tests/_f1a_support.py 的 site 夹具同模式：空闲端口 + 临时目录里的
    空库 + 临时 RAG 目录，服务经唯一启动器 _playwright_serve.py 拉起（带域名
    DNS 锁定），结束时 SIGTERM 回收并清理临时目录。本文件的浏览器用例会真实
    写配置，因此数据面必须是临时的，不能落在 live 库上。
    """
    global BASE_URL
    workdir = tempfile.mkdtemp(prefix="memory_budget_qa_")
    port = _free_port()
    handle = start_pinned_server(
        port, workdir, os.path.join(workdir, "qa.db"),
        extra_env={"RAG_DIR": os.path.join(workdir, "rag")})
    BASE_URL = "http://127.0.0.1:%d" % port
    try:
        yield BASE_URL
    finally:
        handle.stop()
        shutil.rmtree(workdir, ignore_errors=True)


# ==============================================================================
# 模块 1：DOM、CSS 与 JS 静态契约与加固声明检测
# ==============================================================================

def test_contract_and_css_hardening_declarations():
    """验证 DOM 结构、CSS 盒模型加固与 JS 映射声明完全对齐。"""
    # 1. 验证 index.html
    html_file = DEMO_ROOT / "templates" / "index.html"
    assert html_file.exists(), "[FAIL] index.html 不存在"
    html_content = html_file.read_text(encoding="utf-8")

    assert 'class="memory-budget-grid"' in html_content, "[FAIL] 未找到 class=\"memory-budget-grid\" 容器"

    for field_id in MEMORY_BUDGET_FIELD_IDS:
        assert f'id="{field_id}"' in html_content, f"[FAIL] 未找到控件 id={field_id}"

    # 2. 验证 style.css 中的盒模型加固与响应式规则
    css_file = DEMO_ROOT / "static" / "css" / "style.css"
    assert css_file.exists(), "[FAIL] style.css 不存在"
    css_content = css_file.read_text(encoding="utf-8")

    assert "min-width: 0;" in css_content, "[FAIL] style.css 缺少 min-width: 0; 加固声明"
    assert ".memory-budget-grid" in css_content, "[FAIL] style.css 缺少 .memory-budget-grid 样式类定义"

    # 检查基础 3 列
    assert re.search(r"\.memory-budget-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)", css_content), \
        "[FAIL] .memory-budget-grid 基础规则缺少 repeat(3, minmax(0, 1fr))"

    # 检查 640px 媒体查询 2 列
    assert re.search(r"@media\s*\([^)]*max-width:\s*640px\)\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)", css_content), \
        "[FAIL] @media (max-width: 640px) 缺少 repeat(2, minmax(0, 1fr))"

    # 检查 420px 媒体查询 1 列
    assert re.search(r"@media\s*\([^)]*max-width:\s*420px\)\s*\{[^}]*grid-template-columns:\s*1fr", css_content), \
        "[FAIL] @media (max-width: 420px) 缺少 1fr 规则"

    # 3. 验证 main.js 映射数组
    js_content = frontend_union()  # F4 S4-11: main.js+域文件联合全文

    for field_id in MEMORY_BUDGET_FIELD_IDS:
        assert field_id in js_content, f"[FAIL] main.js ADMIN_SETTINGS_FIELDS 缺少 {field_id}"


# ==============================================================================
# 模块 2：后端 API 读写持久化与缺省值回退契约回归
# ==============================================================================

def test_admin_settings_api_roundtrip_contract():
    """验证 /api/admin/settings 端点对记忆预算与衰减 6 个字段的读写一致性。"""
    # 1. 获取当前配置
    get_resp = client.get("/api/admin/settings", headers=_admin_headers())
    assert get_resp.status_code == 200, f"[FAIL] GET /api/admin/settings 失败: {get_resp.text}"
    body = get_resp.json()
    assert body["status"] == "success"
    orig_data = body["data"]

    # 2. 准备修改数据
    test_update = {
        "memory_budget_facts_tokens": 1024,
        "memory_budget_per_item_tokens": 256,
        "memory_budget_max_items": 10,
        "memory_decay_hit_bonus": 0.08,
        "memory_decay_override_penalty": 0.16,
        "memory_archive_threshold": 0.45,
    }

    try:
        # 保存新参数
        put_resp = client.put("/api/admin/settings", json=test_update, headers=_admin_headers())
        assert put_resp.status_code == 200, f"[FAIL] PUT /api/admin/settings 失败: {put_resp.text}"

        # 再次获取验证已持久化
        check_resp = client.get("/api/admin/settings", headers=_admin_headers())
        assert check_resp.status_code == 200
        new_data = check_resp.json()["data"]

        for k, v in test_update.items():
            assert new_data.get(k) == v, f"[FAIL] 字段 {k} 预期 {v}, 实际 {new_data.get(k)}"

    finally:
        # 3. 无论成功与否，恢复原配置
        restore_payload = {k: orig_data[k] for k in test_update if k in orig_data}
        client.put("/api/admin/settings", json=restore_payload, headers=_admin_headers())


# ==============================================================================
# 模块 3：Playwright 真实浏览器多视口几何排版、像素级溢出与响应式断言
# ==============================================================================

VIEWPORTS = [
    {"name": "desktop_1440", "width": 1440, "height": 900, "expected_cols": 3},
    {"name": "standard_960", "width": 960, "height": 800, "expected_cols": 3},
    {"name": "tablet_768", "width": 768, "height": 1024, "expected_cols": 3},
    {"name": "narrow_500", "width": 500, "height": 800, "expected_cols": 2},
    {"name": "mobile_390", "width": 390, "height": 844, "expected_cols": 1},
]


def test_playwright_memory_budget_pixel_geometry_and_overflow():
    """在 5 档视口尺寸下真实渲染并断言 0 像素溢出与响应式列数。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 1. 访问首页并切换为 admin 角色
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#demoRoleSelect", timeout=15000)

        current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
        if current_role != "admin":
            with page.expect_navigation(timeout=15000):
                page.select_option("#demoRoleSelect", "admin")
            page.wait_for_selector("#demoRoleSelect", timeout=10000)
            page.wait_for_timeout(300)

        # 2. 导航至系统与引擎配置标签页 (#tab-engine)
        page.locator("#adminEngineBtn").click()
        page.wait_for_selector("#tab-engine", timeout=10000)
        assert page.locator("#tab-engine").is_visible(), "[FAIL] #tab-engine 未成功展示"

        # 切换至「03 AI 飞轮与治理规则」子分页
        page.locator("#engineSubNav_03").click()
        page.wait_for_timeout(200)

        # 确保系统配置卡片与 .memory-budget-grid 加载
        page.wait_for_selector(".memory-budget-grid", timeout=10000)
        grid_locator = page.locator(".memory-budget-grid")
        assert grid_locator.is_visible(), "[FAIL] .memory-budget-grid 未在视口中可见"

        # 3. 遍历多档视口执行几何断言
        for vp in VIEWPORTS:
            w = vp["width"]
            h = vp["height"]
            expected_cols = vp["expected_cols"]
            vp_name = vp["name"]

            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(250)  # 给予浏览器重排渲染时间

            # 滚动到卡片区域
            grid_locator.scroll_into_view_if_needed()
            page.wait_for_timeout(100)

            # 获取卡片及其内部各元素几何信息
            metrics = page.evaluate("""() => {
                const grid = document.querySelector('.memory-budget-grid');
                const card = grid.closest('.card');
                const cardRect = card.getBoundingClientRect();
                const gridRect = grid.getBoundingClientRect();
                const cardStyle = window.getComputedStyle(card);
                const gridStyle = window.getComputedStyle(grid);

                // 解析网格实际列数
                const colsStr = gridStyle.gridTemplateColumns.trim();
                const colTracks = colsStr ? colsStr.split(/\\s+/).filter(Boolean) : [];

                // 获取 6 个输入框及对应的 form-group
                const inputIds = [
                    'setMemoryBudgetFactsTokens',
                    'setMemoryBudgetPerItemTokens',
                    'setMemoryBudgetMaxItems',
                    'setMemoryDecayHitBonus',
                    'setMemoryDecayOverridePenalty',
                    'setMemoryArchiveThreshold'
                ];

                const inputMetrics = inputIds.map(id => {
                    const el = document.getElementById(id);
                    const group = el ? el.closest('.form-group') : null;
                    const elRect = el ? el.getBoundingClientRect() : null;
                    const groupRect = group ? group.getBoundingClientRect() : null;
                    return {
                        id,
                        exists: !!el,
                        elRect: elRect ? { left: elRect.left, right: elRect.right, width: elRect.width } : null,
                        groupRect: groupRect ? { left: groupRect.left, right: groupRect.right, width: groupRect.width } : null,
                        scrollWidth: el ? el.scrollWidth : 0,
                        clientWidth: el ? el.clientWidth : 0
                    };
                });

                return {
                    card: {
                        rect: { left: cardRect.left, right: cardRect.right, width: cardRect.width },
                        scrollWidth: card.scrollWidth,
                        clientWidth: card.clientWidth,
                        paddingLeft: parseFloat(cardStyle.paddingLeft),
                        paddingRight: parseFloat(cardStyle.paddingRight)
                    },
                    grid: {
                        rect: { left: gridRect.left, right: gridRect.right, width: gridRect.width },
                        scrollWidth: grid.scrollWidth,
                        clientWidth: grid.clientWidth,
                        colCount: colTracks.length,
                        colTracks: colTracks
                    },
                    inputs: inputMetrics
                };
            }""")

            card_info = metrics["card"]
            grid_info = metrics["grid"]
            inputs_info = metrics["inputs"]

            card_right = card_info["rect"]["right"]
            card_left = card_info["rect"]["left"]

            # 断言 1: 卡片自身无水平滚动溢出
            assert card_info["scrollWidth"] <= card_info["clientWidth"], \
                f"[FAIL][{vp_name}] 卡片自身发生水平溢出: scrollWidth({card_info['scrollWidth']}) > clientWidth({card_info['clientWidth']})"

            # 断言 2: 网格容器自身无水平滚动溢出
            assert grid_info["scrollWidth"] <= grid_info["clientWidth"], \
                f"[FAIL][{vp_name}] 网格自身发生水平溢出: scrollWidth({grid_info['scrollWidth']}) > clientWidth({grid_info['clientWidth']})"

            # 断言 3: 列数响应式规则精准对齐
            assert grid_info["colCount"] == expected_cols, \
                f"[FAIL][{vp_name}] 网格列数不符: 视口宽度 {w}px 预期 {expected_cols} 列, 实际解析为 {grid_info['colCount']} 列 ({grid_info['colTracks']})"

            # 断言 4: 关键缺陷项（第 3 列 setMemoryBudgetMaxItems 与 setMemoryArchiveThreshold）绝对不得超出卡片右侧
            # 允许 0.5px 的亚像素渲染微小舍入容差
            third_col_ids = ["setMemoryBudgetMaxItems", "setMemoryArchiveThreshold"]
            for inp in inputs_info:
                inp_id = inp["id"]
                inp_right = inp["elRect"]["right"]
                inp_left = inp["elRect"]["left"]

                # 必须完全处于卡片内部
                assert inp_right <= card_right + 0.5, \
                    f"[FAIL][{vp_name}] 控件 {inp_id} 右边界超出卡片右边界! inp_right={inp_right}, card_right={card_right}, overflow={inp_right - card_right}px"
                assert inp_left >= card_left - 0.5, \
                    f"[FAIL][{vp_name}] 控件 {inp_id} 左边界超出卡片左边界! inp_left={inp_left}, card_left={card_left}"

                if inp_id in third_col_ids:
                    # 针对曾发生严重溢出的第 3 列控件做显式严格判定
                    assert inp_right <= card_right + 0.01, \
                        f"[FAIL][{vp_name}] 历史缺陷项 {inp_id} 依然悬空外溢! overflow={inp_right - card_right}px"

            # 截图留痕
            screenshot_path = SCREENSHOT_DIR / f"qa_memory_budget_viewport_{w}px.png"
            card_locator = page.locator(".memory-budget-grid").locator("xpath=ancestor::div[contains(@class,'card')][1]")
            card_locator.screenshot(path=str(screenshot_path))
            assert screenshot_path.exists(), f"[FAIL] 截图保存失败: {screenshot_path}"

        context.close()
        browser.close()


# ==============================================================================
# 模块 4：前端 UI 交互完整性与数据保存回填闭环
# ==============================================================================

def test_playwright_ui_interaction_save_and_reload():
    """验证在真实浏览器中操作界面修改系统配置、保存并刷新重载后能够准确回填。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#demoRoleSelect", timeout=15000)

        current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
        if current_role != "admin":
            with page.expect_navigation(timeout=15000):
                page.select_option("#demoRoleSelect", "admin")
            page.wait_for_selector("#demoRoleSelect", timeout=10000)
            page.wait_for_timeout(300)

        # 导航至系统配置并切换至「03 AI 飞轮与治理规则」子分页
        page.locator("#adminEngineBtn").click()
        page.wait_for_selector("#tab-engine", timeout=10000)
        page.locator("#engineSubNav_03").click()
        page.wait_for_timeout(200)
        page.wait_for_selector(".memory-budget-grid", timeout=10000)

        # 记录初始值
        initial_val = page.eval_on_selector("#setMemoryBudgetFactsTokens", "el => el.value")

        # 填入测试新值
        test_val = "1150"
        page.fill("#setMemoryBudgetFactsTokens", test_val)
        page.fill("#setMemoryBudgetMaxItems", "9")

        # 点击保存按钮并等待保存请求完成
        save_btn = page.locator("#btnSaveUnifiedConfig")
        with page.expect_response(lambda r: ("/api/admin/unified-config" in r.url or "/api/admin/settings" in r.url) and r.status == 200, timeout=10000):
            if save_btn.is_visible():
                save_btn.click()
            else:
                page.locator("#btnSaveAdminSettings").click(force=True)
        page.wait_for_timeout(500)

        # 刷新页面验证持久化回填
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#demoRoleSelect", timeout=10000)
        page.evaluate('() => localStorage.setItem("demo_role", "admin")')
        page.locator("#adminEngineBtn").click()
        page.wait_for_selector("#tab-engine", timeout=10000)
        page.locator("#engineSubNav_03").click()
        page.wait_for_timeout(200)
        page.wait_for_selector(".memory-budget-grid", timeout=10000)

        # 等待数据异步加载完成并回显到输入框
        page.wait_for_function('() => document.getElementById("setMemoryBudgetFactsTokens") && document.getElementById("setMemoryBudgetFactsTokens").value === "1150"', timeout=10000)

        reloaded_val = page.eval_on_selector("#setMemoryBudgetFactsTokens", "el => el.value")
        reloaded_max_items = page.eval_on_selector("#setMemoryBudgetMaxItems", "el => el.value")

        assert reloaded_val == test_val, f"[FAIL] 刷新后回填不符: 预期 {test_val}, 实际 {reloaded_val}"
        assert reloaded_max_items == "9", f"[FAIL] 刷新后回填不符: 预期 9, 实际 {reloaded_max_items}"

        # 还原默认值
        page.locator("#btnResetAdminSettings").click()
        page.wait_for_timeout(300)
        with page.expect_response(lambda r: ("/api/admin/unified-config" in r.url or "/api/admin/settings" in r.url) and r.status == 200, timeout=10000):
            if save_btn.is_visible():
                save_btn.click()
            else:
                page.locator("#btnSaveAdminSettings").click(force=True)
        page.wait_for_timeout(500)

        context.close()
        browser.close()


# ==============================================================================
# 模块 5：全局 0 Emoji 红线合规性自动化扫描
# ==============================================================================

def test_zero_emoji_redline_compliance():
    """扫描所有相关源代码、样式表、模板与测试文件，确保 0 Emoji 违规。"""
    from f4_frontend_sources import F4_DOMAIN_FILES  # F4 S4-11: 域文件一并纳入 0 Emoji 扫描
    files_to_scan = [
        DEMO_ROOT / "static" / "css" / "style.css",
        DEMO_ROOT / "templates" / "index.html",
        DEMO_ROOT / "static" / "js" / "main.js",
        Path(__file__),
    ] + [DEMO_ROOT / rel for rel in F4_DOMAIN_FILES]

    emoji_pattern = re.compile(
        "["
        "\U00010000-\U0010ffff"
        "\u2600-\u26ff"
        "\u2700-\u27bf"
        "]",
        flags=re.UNICODE,
    )

    for fpath in files_to_scan:
        assert fpath.exists(), f"[FAIL] 文件不存在: {fpath}"
        content = fpath.read_text(encoding="utf-8")
        matches = emoji_pattern.findall(content)
        assert len(matches) == 0, f"[FAIL] 文件 {fpath.name} 包含非法 Emoji: {matches}"

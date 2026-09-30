# -*- coding: utf-8 -*-
"""
管理端导航与 AI 效果观测与评测中心单页大盘端到端 (E2E) 测试套件
Admin Tabs Consolidation & Single-Page Unified Dashboard Verification

覆盖范围:
Phase 1: RBAC 角色权限与侧边栏动态导航隔离
  - admin 角色:
    - 侧边栏小节 #adminSidebarSection 可见且包含 "系统与算法工程"
    - 系统与引擎配置入口 #adminEngineBtn 可见且包含 "系统与引擎配置"
    - AI 效果观测与评测中心入口 #analyticsBoardBtn 可见且包含 "AI 效果观测与评测中心"
    - 旧独立入口 #goldenBoardBtn 和 #evalsetReviewBtn 保持不可见 (隐藏)
  - staff 角色:
    - #adminSidebarSection, #adminEngineBtn, #analyticsBoardBtn 均不可见
  - owner 角色:
    - #adminSidebarSection, #adminEngineBtn, #analyticsBoardBtn 均不可见

Phase 2: AI 效果观测与评测中心四大独立功能区 (Segmented Control) 统一渲染
  - 切换回 admin 角色并点击 #analyticsBoardBtn
  - #tab-analytics 激活且可见
  - 分段选项卡 4 功能区 (sec-telemetry, sec-canary, sec-experiment, sec-eval) 独立隔离校验
  - 全部核心卡片/组件加载并存在于对应功能区 DOM 中:
    1. 全量埋点事件分布 (#analyticsEventDistBody) 与 挽回/点踩指标 (#analyticsRecoveryBody)
    2. 金丝雀灰度现状与脱敏单据观测 (#analyticsGreyBody, #adminGreySamplesBody)
    3. A/B 实验与显著性检验 (包含 #analyticsExperimentsBody 与 #pvalueCardsContainer)
    4. 基准资产看板与 GT 确权 (包含 #goldenBoardBody 与 #gtEvalsetStats, a[href="/evalset"])
  - 自动生成高清断言截屏:
    - demo/gui-test-screenshots/admin_tab_sidebar_layout.png
    - demo/gui-test-screenshots/admin_tab_analytics_subview1_telemetry.png
    - demo/gui-test-screenshots/admin_tab_analytics_subview2_canary.png
    - demo/gui-test-screenshots/admin_tab_analytics_subview3_experiment.png
    - demo/gui-test-screenshots/admin_tab_analytics_subview4_eval.png
    - demo/gui-test-screenshots/admin_tab_analytics_unified.png
    - demo/gui-test-screenshots/staff_sidebar_no_admin.png

服务依赖与默认行为（AS1 红线修复）:
  本套件需要 127.0.0.1:15010 上的活体服务，但**缺省不会自动拉起任何进程**：
  - 15010 已连通 → 直接跑完整用例；
  - 15010 不通且未显式 opt-in → pytest.skip 并给出可读原因（既不静默 pass、也不 fail）；
  - 只有显式设 E2E_ALLOW_SPAWN_UVICORN=1 时才允许后台拉起 uvicorn（结束时回收子进程）。
  why: app/main.py 的 startup 会执行 hydrate_engine_config_from_env()，live 的
  config_source=auto 走的是「无条件 set_engine_config(cfg)」分支（demo/app/db.py:4625）
  —— 即启动一次服务就会把它所用库的引擎配置按 .env 重装一遍。裸跑本文件时该子进程不会
  带上 conftest 的 DB_PATH 隔离，可能落在 live 库上，于是「目录级回归」会在无人察觉的
  情况下重写 live 的 engine_config（历史上「管理台改了引擎活不过一次重启」的同源机制），
  且事后难以归因。故默认改为显式 opt-in。
"""

import ast
import os
import sys
import time
import socket
import subprocess
from urllib.parse import urlparse
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
# 显式 opt-in 开关名：只有它的值为真（1/true/yes/on，忽略大小写）时才允许拉起 uvicorn。
ALLOW_SPAWN_ENV = "E2E_ALLOW_SPAWN_UVICORN"
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DEMO_DIR = os.path.join(ROOT_DIR, "demo")
SCREENSHOT_DIR = os.path.join(DEMO_DIR, "gui-test-screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

_TRUTHY = ("1", "true", "yes", "on")


def is_service_reachable(timeout=1.0):
    """检测本地 15010 端口服务是否连通"""
    parsed = urlparse(BASE_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def allow_spawn_uvicorn():
    """是否允许本用例自动拉起 uvicorn（仅 E2E_ALLOW_SPAWN_UVICORN 取真值时为真）。

    接受 1 / true / yes / on（忽略大小写与首尾空白），避免运行者因写法差异被误判成「没开」。
    """
    return os.environ.get(ALLOW_SPAWN_ENV, "").strip().lower() in _TRUTHY


def unavailability_reason():
    """返回「当前无法执行端到端用例」的可读原因；可以执行时返回 None。

    why 需要这道门（AS1 红线）：本用例原先无条件拉起 uvicorn，而 app/main.py 的 startup
    会执行 hydrate_engine_config_from_env()；live 库的 config_source=auto 走的是
    「无条件 set_engine_config(cfg)」分支（demo/app/db.py:4625），即启动一次服务就会把它
    所用库的引擎配置按 .env 重装一遍。目录级回归若隐式触发本用例，可能静默重写 live 的
    engine_config（历史上「管理台改了引擎活不过一次重启」的同源机制），事后无从归因。
    因此缺省不启任何进程：要么复用已在跑的服务，要么显式 opt-in。
    """
    if is_service_reachable():
        return None
    if allow_spawn_uvicorn():
        return None
    return (
        "本地 %s 无可用服务，且未显式 opt-in 自动拉起（缺省不会启动任何进程：本用例拉起的 "
        "uvicorn 会执行 startup 的引擎配置自愈，可能重写所在库的 engine_config）。"
        "如需自动拉起请设 %s=1；或先手动启动 demo 服务后重跑。" % (BASE_URL, ALLOW_SPAWN_ENV)
    )


def ensure_server():
    """确保本地 Demo 服务可用；返回本进程刚拉起的子进程，没有则返回 None。

    缺省下绝不 spawn（见 unavailability_reason 的 why）：只有 E2E_ALLOW_SPAWN_UVICORN 为真
    且 15010 不通时，才在 opt-in 判断块内后台拉起 uvicorn 并等待就绪。返回的子进程由调用方
    的 finally 回收（terminate + wait），不留孤儿。
    """
    if is_service_reachable():
        return None
    if allow_spawn_uvicorn():
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "15010"],
            cwd=DEMO_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        start = time.time()
        while time.time() - start < 15:
            if is_service_reachable():
                return proc
            time.sleep(0.4)
        return proc
    # 缺省路径：不拉任何进程；由各入口以 skip（或可读报错）呈现原因。
    return None


def switch_demo_role(page, target_role):
    """切换角色并通过 reload 重新初始化权限状态"""
    current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
    if current_role == target_role:
        return
    with page.expect_navigation(timeout=15000):
        page.select_option("#demoRoleSelect", target_role)
    page.wait_for_selector("#demoRoleSelect", timeout=10000)
    page.wait_for_timeout(400)


def test_admin_tabs_consolidation_e2e():
    """Pytest 入口：执行全量管理端导航合并与单页大盘 E2E 测试。

    缺省（15010 不通且未 opt-in）走 pytest.skip 并给出可读原因：不得静默 pass，也不得 fail。
    """
    reason = unavailability_reason()
    if reason:
        pytest.skip(reason)
    run_admin_tabs_consolidation_e2e()


def run_admin_tabs_consolidation_e2e():
    """管理端合并与 AI 观测大盘 E2E 测试完整流程"""
    server_proc = ensure_server()
    try:
        # 服务不可达时以可读原因报错；这条检查放在 try 内，保证 opt-in 拉起的子进程
        # 无论从哪条路径失败都会被 finally 回收，不留孤儿。
        if not is_service_reachable():
            raise RuntimeError(unavailability_reason() or ("本地 %s 不可达：服务未就绪" % BASE_URL))
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 960})
            page = context.new_page()

            # ------------------------------------------------------------
            # Phase 1: RBAC and Sidebar Navigation
            # ------------------------------------------------------------
            print("\n[Phase 1] 校验 RBAC 角色权限与侧边栏动态导航...")
            page.goto(BASE_URL, wait_until="domcontentloaded")
            page.wait_for_selector("#demoRoleSelect", timeout=12000)

            # 1.1 admin 角色检验
            switch_demo_role(page, "admin")
            admin_role_val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
            assert admin_role_val == "admin", f"Expected admin role, got {admin_role_val}"

            # 验证 #adminSidebarSection 可见且包含 '系统与算法工程'
            admin_section = page.locator("#adminSidebarSection")
            assert admin_section.is_visible(), "adminSidebarSection should be visible for admin role"
            admin_section_text = admin_section.inner_text().strip()
            assert "系统与算法工程" in admin_section_text, (
                f"Expected '系统与算法工程' in #adminSidebarSection, got '{admin_section_text}'"
            )

            # 验证 #adminEngineBtn 可见且包含 '系统与引擎配置'
            admin_engine_btn = page.locator("#adminEngineBtn")
            assert admin_engine_btn.is_visible(), "adminEngineBtn should be visible for admin role"
            admin_engine_text = admin_engine_btn.inner_text().strip()
            assert "系统与引擎配置" in admin_engine_text, (
                f"Expected '系统与引擎配置' in #adminEngineBtn, got '{admin_engine_text}'"
            )

            # 验证 #analyticsBoardBtn 可见且包含 'AI 效果观测与评测中心'
            analytics_btn = page.locator("#analyticsBoardBtn")
            assert analytics_btn.is_visible(), "analyticsBoardBtn should be visible for admin role"
            analytics_btn_text = analytics_btn.inner_text().strip()
            assert "AI 效果观测与评测中心" in analytics_btn_text, (
                f"Expected 'AI 效果观测与评测中心' in #analyticsBoardBtn, got '{analytics_btn_text}'"
            )

            # 验证 #goldenBoardBtn 和 #evalsetReviewBtn 独立入口保持不可见
            golden_btn = page.locator("#goldenBoardBtn")
            evalset_btn = page.locator("#evalsetReviewBtn")
            assert not golden_btn.is_visible(), "goldenBoardBtn must NOT be visible (consolidated into unified dashboard)"
            assert not evalset_btn.is_visible(), "evalsetReviewBtn must NOT be visible (consolidated into unified dashboard)"

            # 保存 admin 侧边栏布局截屏
            shot_sidebar = os.path.join(SCREENSHOT_DIR, "admin_tab_sidebar_layout.png")
            page.screenshot(path=shot_sidebar, full_page=False)
            print(f"Saved: {shot_sidebar}")

            # 1.2 staff 角色检验
            print("Switching to staff role...")
            switch_demo_role(page, "staff")
            staff_role_val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
            assert staff_role_val == "staff", f"Expected staff role, got {staff_role_val}"

            assert not page.locator("#adminSidebarSection").is_visible(), (
                "adminSidebarSection must NOT be visible for staff role"
            )
            assert not page.locator("#adminEngineBtn").is_visible(), (
                "adminEngineBtn must NOT be visible for staff role"
            )
            assert not page.locator("#analyticsBoardBtn").is_visible(), (
                "analyticsBoardBtn must NOT be visible for staff role"
            )
            assert not page.locator("#goldenBoardBtn").is_visible(), (
                "goldenBoardBtn must NOT be visible for staff role"
            )
            assert not page.locator("#evalsetReviewBtn").is_visible(), (
                "evalsetReviewBtn must NOT be visible for staff role"
            )

            # 保存 staff 侧边栏截屏 (无管理入口)
            shot_staff = os.path.join(SCREENSHOT_DIR, "staff_sidebar_no_admin.png")
            page.screenshot(path=shot_staff, full_page=False)
            print(f"Saved: {shot_staff}")

            # 1.3 owner 角色检验
            print("Switching to owner role...")
            switch_demo_role(page, "owner")
            owner_role_val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
            assert owner_role_val == "owner", f"Expected owner role, got {owner_role_val}"

            assert not page.locator("#adminSidebarSection").is_visible(), (
                "adminSidebarSection must NOT be visible for owner role"
            )
            assert not page.locator("#adminEngineBtn").is_visible(), (
                "adminEngineBtn must NOT be visible for owner role"
            )
            assert not page.locator("#analyticsBoardBtn").is_visible(), (
                "analyticsBoardBtn must NOT be visible for owner role"
            )
            assert not page.locator("#goldenBoardBtn").is_visible(), (
                "goldenBoardBtn must NOT be visible for owner role"
            )
            assert not page.locator("#evalsetReviewBtn").is_visible(), (
                "evalsetReviewBtn must NOT be visible for owner role"
            )

            # ------------------------------------------------------------
            # Phase 2: AI 效果观测与评测中心四大独立功能区 (Segmented Subviews)
            # ------------------------------------------------------------
            print("\n[Phase 2] 校验 AI 效果观测与评测中心四大独立功能区 (Segmented Control)...")
            switch_demo_role(page, "admin")

            # 点击 AI 效果观测与评测中心入口
            page.locator("#analyticsBoardBtn").click()
            page.wait_for_selector("#tab-analytics", timeout=10000)

            # 验证 #tab-analytics 处于激活且可见状态
            tab_analytics = page.locator("#tab-analytics")
            assert tab_analytics.is_visible(), "#tab-analytics should be visible after clicking analyticsBoardBtn"
            tab_classes = page.eval_on_selector("#tab-analytics", "el => el.className")
            assert "active" in tab_classes, f"#tab-analytics should have 'active' class, got '{tab_classes}'"

            # 校验四大独立功能区分段导航栏 (Segmented Bar)
            segmented_bar = page.locator(".analytics-segmented-bar")
            assert segmented_bar.is_visible(), ".analytics-segmented-bar must be visible"
            seg_buttons = segmented_bar.locator(".analytics-segment-btn")
            assert seg_buttons.count() == 4, f"Expected 4 segment buttons, got {seg_buttons.count()}"

            # 等待各数据接口异步拉取完成并由前端渲染到 DOM (初始功能区 1 埋点就绪)
            page.wait_for_function(
                """() => {
                    const dist = document.getElementById('analyticsEventDistBody');
                    const rec = document.getElementById('analyticsRecoveryBody');
                    if (!dist || !rec) return false;
                    return !dist.innerText.includes('加载中') &&
                           !rec.innerText.includes('加载中');
                }""",
                timeout=15000
            )

            # ------------------------------------------------------------
            # 2.1 独立功能区一：线上体验感知与埋点监控 (#sec-telemetry)
            # ------------------------------------------------------------
            print("校验独立功能区 1: 线上体验感知与埋点监控...")
            sec_telemetry = page.locator("#sec-telemetry")
            sec_canary = page.locator("#sec-canary")
            sec_experiment = page.locator("#sec-experiment")
            sec_eval = page.locator("#sec-eval")

            # 默认处于功能区一，其它三区保持隐藏 (独立隔离)
            assert sec_telemetry.is_visible(), "sec-telemetry must be visible by default"
            assert not sec_canary.is_visible(), "sec-canary must NOT be visible when view 1 is active"
            assert not sec_experiment.is_visible(), "sec-experiment must NOT be visible when view 1 is active"
            assert not sec_eval.is_visible(), "sec-eval must NOT be visible when view 1 is active"

            # 验证功能区一内部组件
            if page.locator("#btnTelemetrySubTabEvents").count() > 0:
                page.locator("#btnTelemetrySubTabEvents").click()
                page.wait_for_timeout(300)
            event_dist = page.locator("#analyticsEventDistBody")
            assert event_dist.is_visible(), "#analyticsEventDistBody must be visible in view 1"
            recovery = page.locator("#analyticsRecoveryBody")
            assert recovery.is_visible(), "#analyticsRecoveryBody must be visible in view 1"

            shot_subview1 = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_subview1_telemetry.png")
            page.screenshot(path=shot_subview1, full_page=False)
            print(f"Saved: {shot_subview1}")

            # ------------------------------------------------------------
            # 2.2 独立功能区二：金丝雀发布监控 (#sec-canary)
            # ------------------------------------------------------------
            print("校验独立功能区 2: 金丝雀发布监控...")
            page.locator("#btnSubViewCanary").click()
            page.wait_for_timeout(300)
            page.wait_for_function(
                """() => {
                    const grey = document.getElementById('analyticsGreyBody');
                    return grey && !grey.innerText.includes('加载中');
                }""",
                timeout=15000
            )

            assert not sec_telemetry.is_visible(), "sec-telemetry must NOT be visible when view 2 is active"
            assert sec_canary.is_visible(), "sec-canary must be visible when switched to view 2"
            assert not sec_experiment.is_visible(), "sec-experiment must NOT be visible when view 2 is active"
            assert not sec_eval.is_visible(), "sec-eval must NOT be visible when view 2 is active"

            # 验证功能区二内部组件 (纯粹金丝雀灰度，解耦 A/B 实验)
            grey_status = page.locator("#analyticsGreyBody")
            assert grey_status.is_visible(), "#analyticsGreyBody must be visible in view 2"
            assert page.locator("#adminGreySamplesTable").is_visible() or page.locator("#adminGreySamplesBody").is_visible()
            # 验证 #analyticsExperimentsBody 不在 #sec-canary 内
            assert sec_canary.locator("#analyticsExperimentsBody").count() == 0, "#analyticsExperimentsBody must NOT be inside #sec-canary"

            shot_subview2 = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_subview2_canary.png")
            page.screenshot(path=shot_subview2, full_page=False)
            print(f"Saved: {shot_subview2}")

            # ------------------------------------------------------------
            # 2.3 独立功能区三：A/B 科学实验台 (#sec-experiment)
            # ------------------------------------------------------------
            print("校验独立功能区 3: A/B 科学实验台...")
            page.locator("#btnSubViewExperiment").click()
            page.wait_for_timeout(300)
            page.wait_for_function(
                """() => {
                    const ab = document.getElementById('analyticsExperimentsBody');
                    const pval = document.getElementById('pvalueCardsContainer');
                    if (!ab || !pval) return false;
                    return !ab.innerText.includes('加载中') && pval.children.length > 0;
                }""",
                timeout=15000
            )

            assert not sec_telemetry.is_visible(), "sec-telemetry must NOT be visible when view 3 is active"
            assert not sec_canary.is_visible(), "sec-canary must NOT be visible when view 3 is active"
            assert sec_experiment.is_visible(), "sec-experiment must be visible when switched to view 3"
            assert not sec_eval.is_visible(), "sec-eval must NOT be visible when view 3 is active"

            ab_body = page.locator("#analyticsExperimentsBody")
            assert ab_body.is_visible(), "#analyticsExperimentsBody must be visible in view 3"
            pvalue_container = page.locator("#pvalueCardsContainer")
            assert pvalue_container.is_visible(), "#pvalueCardsContainer must be visible in view 3"

            shot_subview3 = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_subview3_experiment.png")
            page.screenshot(path=shot_subview3, full_page=False)
            print(f"Saved: {shot_subview3}")

            # ------------------------------------------------------------
            # 2.4 独立功能区四：基准资产与 GT 确权 (#sec-eval)
            # ------------------------------------------------------------
            print("校验独立功能区 4: 基准资产与 GT 确权...")
            page.locator("#btnSubViewEval").click()
            page.wait_for_timeout(300)
            page.wait_for_function(
                """() => {
                    const golden = document.getElementById('goldenBoardBody');
                    const gt = document.getElementById('gtEvalsetStats');
                    if (!golden || !gt) return false;
                    return !golden.innerText.includes('加载中') && !gt.innerText.includes('加载中');
                }""",
                timeout=15000
            )

            assert not sec_telemetry.is_visible(), "sec-telemetry must NOT be visible when view 4 is active"
            assert not sec_canary.is_visible(), "sec-canary must NOT be visible when view 4 is active"
            assert not sec_experiment.is_visible(), "sec-experiment must NOT be visible when view 4 is active"
            assert sec_eval.is_visible(), "sec-eval must be visible when switched to view 4"

            # 验证功能区四内部组件
            golden_body = page.locator("#goldenBoardBody")
            assert golden_body.is_visible(), "#goldenBoardBody must be visible in view 4"
            gt_stats = page.locator("#gtEvalsetStats")
            assert gt_stats.is_visible(), "#gtEvalsetStats must be visible in view 4"
            evalset_links = page.locator("#sec-eval a[href='/evalset']")
            assert evalset_links.count() >= 1, "Expected GT review link inside #sec-eval"
            assert evalset_links.first.is_visible(), "GT review link in #sec-eval must be visible"

            shot_subview4 = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_subview4_eval.png")
            page.screenshot(path=shot_subview4, full_page=False)
            print(f"Saved: {shot_subview4}")

            # 切回功能区 1 保存统一总览截屏
            page.locator("#btnSubViewTelemetry").click()
            page.wait_for_timeout(300)
            assert sec_telemetry.is_visible(), "sec-telemetry should be visible after switching back"

            shot_analytics = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_unified.png")
            page.screenshot(path=shot_analytics, full_page=False)
            print(f"Saved: {shot_analytics}")

            shot_segmented = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_segmented.png")
            page.screenshot(path=shot_segmented, full_page=False)
            print(f"Saved: {shot_segmented}")

            browser.close()
            print("\nAll 4 independent functional areas checks passed successfully.")

    finally:
        if server_proc:
            server_proc.terminate()
            server_proc.wait()


# ----------------------------------------------------------------------------
# 红线结构守卫：默认路径不得拉起 uvicorn（AS1）
# 这三条用例不需要服务、也不会启动任何进程，纯静态判定 / 纯函数判定。
# ----------------------------------------------------------------------------

def _is_subprocess_popen(node):
    """节点是否为 subprocess.Popen(...) 调用。"""
    func = getattr(node, "func", None)
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "Popen"
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
    )


def _references_opt_in_switch(test_node):
    """if 条件是否引用了 opt-in 开关（allow_spawn_uvicorn() 或 ALLOW_SPAWN_ENV）。"""
    for sub in ast.walk(test_node):
        if isinstance(sub, ast.Name) and sub.id in ("allow_spawn_uvicorn", "ALLOW_SPAWN_ENV"):
            return True
    return False


def _scan_popen_spawn_guard(source):
    """解析源码，返回 (全部 subprocess.Popen 调用节点, 其中被 opt-in 判断块包住的那些)。

    为什么用 AST 而不是看缩进文本：文本判据会被「看起来缩进了但其实是并列语句」骗过，
    结构判据才能回答「缺省路径究竟能不能执行到这一行」。
    """
    tree = ast.parse(source)
    all_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _is_subprocess_popen(n)]
    guarded_ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _references_opt_in_switch(node.test):
            continue
        for branch in (node.body, node.orelse):
            for stmt in branch:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Call) and _is_subprocess_popen(sub):
                        guarded_ids.add(id(sub))
    return all_calls, guarded_ids


def _check_popen_spawn_is_opt_in(source):
    """检查源码里每个 subprocess.Popen 是否都落在 opt-in 判断块内；返回 (是否安全, 说明)。

    三个判据同时成立才算安全：
      1. 至少存在一个 Popen 调用 —— 一个都找不到说明自检对象不对（被删/改名），判不安全，
         避免守卫退化成恒真；
      2. 每个 Popen 都被「引用 opt-in 开关」的 if 块包住 —— 缺省路径结构上执行不到；
      3. 每个 Popen 的源码片段含 uvicorn 与 15010 —— 确认要拉起的正是那个会触发 startup
         引擎配置自愈的服务，而不是别的无害子进程。
    """
    calls, guarded_ids = _scan_popen_spawn_guard(source)
    if not calls:
        return False, "未找到 subprocess.Popen 调用：自检对象不对（可能被删除或改名）"
    unguarded = [c for c in calls if id(c) not in guarded_ids]
    if unguarded:
        return False, "存在未被 opt-in 判断块包住的 subprocess.Popen（缺省路径可能执行到），行号 %s" % (
            sorted({c.lineno for c in unguarded}),
        )
    for call in calls:
        segment = ast.get_source_segment(source, call) or ""
        for needle in ("uvicorn", "15010"):
            if needle not in segment:
                return False, "Popen 调用片段缺少关键串 %r：%r" % (needle, segment[:120])
    return True, "OK：%d 个 Popen 调用全部位于 opt-in 判断块内，且片段含 uvicorn / 15010" % len(calls)


def _module_source():
    """读取本文件源码（用 __file__ 直读，避免依赖 inspect 的实现细节）。"""
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as fh:
        return fh.read()


def test_default_path_cannot_spawn_uvicorn():
    """结构断言：subprocess.Popen 必须落在 opt-in 判断块内（缺省路径执行不到）。

    why 固化成用例而不是靠人记：本文件曾被目录级回归隐式拉起服务，而那条启动路径会执行
    startup 的引擎配置自愈，可能重写 live 的 engine_config（见 unavailability_reason）。
    删掉 opt-in 判断、回到无条件 spawn 时，本用例必须变红。
    """
    ok, detail = _check_popen_spawn_is_opt_in(_module_source())
    assert ok, detail


def test_opt_in_spawn_guard_self_calibrates():
    """自校准：把 opt-in 判断换成恒真（等价于回到无条件 spawn），结构守卫必须判不安全。

    why：守卫若恒真比没有更糟 —— 必须先证明它真的在检查「有没有 opt-in」。
    变异锚点取「opt-in 判断 + 紧随其后的 Popen 两行」，它在本文件里必须唯一命中：
    只取单个 if 行会命中 3 处（本文件另有两处同类判断与一条示例文本）。
    """
    source = _module_source()
    needle = "    if allow_spawn_uvicorn():\n        proc = subprocess.Popen("
    hits = source.count(needle)
    assert hits == 1, "结构守卫依赖唯一一处「opt-in 判断 + Popen」锚点，实际命中 %d 处" % hits
    mutant = source.replace(needle, "    if (True):\n        proc = subprocess.Popen(")
    assert mutant != source, "变异未生效"
    ok, detail = _check_popen_spawn_is_opt_in(mutant)
    assert not ok, "opt-in 判断被替换成恒真后守卫仍通过 = 守卫恒真：%s" % detail


def test_unavailability_reason_is_readable_and_offers_opt_in(monkeypatch):
    """边界双向断言：缺省（无服务 + 未 opt-in）必须给出可读原因并指明 opt-in 开关名；
    设了开关则应允许执行（返回 None）。两条都断言，才能同时排除「恒真」与「恒假」。
    """
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "is_service_reachable", lambda *a, **k: False)

    monkeypatch.delenv(ALLOW_SPAWN_ENV, raising=False)
    reason = unavailability_reason()
    assert reason, "缺省（无服务 + 未 opt-in）必须给出原因，不得静默通过"
    assert ALLOW_SPAWN_ENV in reason, "原因里必须告诉运行者 opt-in 开关名：%s" % reason

    monkeypatch.setenv(ALLOW_SPAWN_ENV, "1")
    assert unavailability_reason() is None, "显式 opt-in 后应允许执行（返回 None）"


def test_default_path_does_not_spawn_any_process(monkeypatch):
    """行为级证据（与结构断言互补）：缺省下即便服务不通也绝不建进程。

    把 subprocess.Popen 换成记录桩，断言「零调用」且 ensure_server 返回 None。全程不启真服务。
    """
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "is_service_reachable", lambda *a, **k: False)
    monkeypatch.delenv(ALLOW_SPAWN_ENV, raising=False)

    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append((a, k)))

    assert ensure_server() is None, "缺省无服务时 ensure_server 必须返回 None（不接管任何进程）"
    assert spawned == [], "缺省路径不得调用 subprocess.Popen：%r" % (spawned,)


def test_opt_in_spawn_is_reaped_on_failure(monkeypatch):
    """opt-in 拉起后，用例失败路径也必须回收子进程（不得留孤儿）。

    用假子进程 + 假 playwright 覆盖整条链路：首次探测不通（触发 opt-in spawn）→ 二次探测就绪
    → playwright 立刻抛错 → finally 必须 terminate + wait。全程不启真服务、不 import app.*。
    """
    module = sys.modules[__name__]
    probes = {"n": 0}

    def fake_reachable(*args, **kwargs):
        # ensure_server 内的首次探测返回 False（走 opt-in spawn），其后返回 True（视为就绪）。
        probes["n"] += 1
        return probes["n"] > 1

    class FakeProc:
        def __init__(self):
            self.terminated = 0
            self.waited = 0

        def terminate(self):
            self.terminated += 1

        def wait(self):
            self.waited += 1

    fake_proc = FakeProc()
    spawn_args = []

    def fake_popen(args, **kwargs):
        spawn_args.append(args)
        return fake_proc

    def boom(*args, **kwargs):
        raise RuntimeError("boom-for-test")

    monkeypatch.setattr(module, "is_service_reachable", fake_reachable)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module, "sync_playwright", boom)
    monkeypatch.setenv(ALLOW_SPAWN_ENV, "1")

    with pytest.raises(RuntimeError, match="boom-for-test"):
        run_admin_tabs_consolidation_e2e()

    assert len(spawn_args) == 1, "opt-in 下应恰好拉起一次，实际 %d 次" % len(spawn_args)
    assert "uvicorn" in spawn_args[0] and "15010" in spawn_args[0], spawn_args[0]
    assert (fake_proc.terminated, fake_proc.waited) == (1, 1), (
        "失败路径必须回收子进程：terminate=%d wait=%d" % (fake_proc.terminated, fake_proc.waited)
    )


if __name__ == "__main__":
    _reason = unavailability_reason()
    if _reason:
        print("[SKIP] %s" % _reason)
        sys.exit(0)
    run_admin_tabs_consolidation_e2e()

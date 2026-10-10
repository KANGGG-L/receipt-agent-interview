# -*- coding: utf-8 -*-
"""
真实浏览器 Playwright E2E 回归测试：系统与引擎配置中的 A/B 科学实验编排高级抽屉及闭环深链
Verifies:
1. 系统与引擎配置 (#tab-engine) 包含并列的双抽屉结构：
   - Drawer 1 (#adminGreyDrawer): 标题包含 "高级配置 · 金丝雀灰度发布"（不含 A/B）
   - Drawer 2 (#adminExperimentDrawer): 标题包含 "高级配置 · A/B 科学实验编排"
2. 点击展开 #adminExperimentDrawer，抽屉移除 .collapsed
3. 下拉列表 #adminExpSelect 成功加载实验，实验详情卡片 #adminExpDetailCard 可见
4. 核心实验要素字段非空：
   - 实验假设 #adminExpHypothesis
   - 主判定指标 #adminExpMetric
   - 分流配比 #adminExpTraffic
   - 最小样本量 #adminExpMinSample
5. 对照组 vs 实验组模型对比卡片正常渲染 (#adminExpCtrlModel 与 #adminExpTreatModel 可见且非空)
6. 截图保存: demo/gui-test-screenshots/admin_ab_experiment_drawer.png
7. 闭环深链验证：
   - 点击抽屉底部的 "前往 A/B 科学实验台查看 p 值指标看板" 链接
   - 页面平滑跳转至 AI 效果观测与评测中心 (#tab-analytics.active)
   - 激活的功能区为 A/B 科学实验台 (#sec-experiment)
   - 标题包含 "A/B 科学实验台"
   - 实验数据表格 (#analyticsExperimentsBody) 与 p 值统计卡片 (#pvalueCardsContainer) 成功渲染可见
8. 截图保存: demo/gui-test-screenshots/admin_ab_experiment_deep_link_land.png

数据自足（夹具缺口修复）：本用例自举服务时**自造它需要的前置数据** —— 在服务使用的
库文件（DB_PATH）里写入本用例自己的 A/B 实验记录（含 grey_snapshot 快照字段）。此前
本文件自起的服务继承了 pytest 进程的 DB_PATH（demo/conftest.py 的 autouse 隔离指向
tmp_path 空库），库里没有任何实验 → `#adminExpSelect` 永远不被填充 → 等待函数超时。
现在断言只依赖本用例写入的那一条夹具实验，与 live 库是否恰好有实验彻底解耦；服务跑在
独立端口上（不与共享的 15010 会话级夹具实例复用），确保读到的一定是这份受控副本。
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

from playwright.sync_api import sync_playwright

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

# 唯一服务自举路径：经启动器拉起并锁定域名 DNS 解析
# （见 _playwright_server.py / _playwright_serve.py）。
from _playwright_server import start_pinned_server  # noqa: E402

ROOT_DIR = os.path.abspath(os.path.join(TESTS_DIR, "../.."))
DEMO_DIR = os.path.join(ROOT_DIR, "demo")
LIVE_DB = os.environ.get("PW_TEST_DB_SOURCE", os.path.join(DEMO_DIR, "receipt_demo.db"))
SCREENSHOT_DIR = os.environ.get("GUI_SCREENSHOT_DIR") or os.environ.get("ARTIFACTS_DIR") or os.path.join(DEMO_DIR, "gui-test-screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# 自举端口刻意与共享夹具（15010、session 级、live 副本）分开：本用例需要「自己的受控
# 数据被自己的服务实例读取」，复用别人的服务会让自造数据不可见（非确定性）。
PORT = int(os.environ.get("PW_AB_DRAWER_PORT", "15011"))
BASE_URL = "http://127.0.0.1:%d" % PORT

# 受控夹具实验：断言只依赖这一行（不依赖 live 库恰好有实验、也不依赖其字段非空）。
EXP_NAME = "E2E-AB-Drawer-Fixture"
EXP_HYPOTHESIS = "A/B 抽屉夹具实验：验证假设字段非空渲染"
EXP_SUCCESS_METRIC = "accuracy"
EXP_TARGET_PERCENT = 50
EXP_MIN_SAMPLE = 30
EXP_STATUS = "draft"
EXP_CTRL_MODEL = "fixture-control-model"
EXP_TREAT_MODEL = "fixture-treatment-model"


def _prepare_data_db(data_dir):
    """准备被测服务的数据源：受控数据源 -> 临时目录的单向只读拷贝。"""
    dst = os.path.join(data_dir, "ab_drawer_ui.db")
    custom_source = os.environ.get("PW_TEST_DB_SOURCE", "").strip()
    no_live = os.environ.get("PW_TEST_NO_LIVE_DB", "").strip() == "1"
    src = custom_source if (custom_source and os.path.isfile(custom_source)) else (LIVE_DB if not no_live else None)
    if src and os.path.isfile(src):
        try:
            shutil.copy2(src, dst)
            for suffix in ("-wal", "-shm"):
                side = src + suffix
                if os.path.isfile(side):
                    shutil.copy2(side, dst + suffix)
        except OSError:
            pass
    return dst


def _seed_experiment(db_path):
    """在本用例自举的数据源里写入它需要的那条 A/B 实验记录，返回其 id。

    为什么必须自造：抽屉下拉 `#adminExpSelect` 由 `/api/admin/experiments` 填充，
    库里没有实验记录时该下拉永远为空。这里清空副本里继承来的实验行、只留本用例的
    夹具实验，使「默认选中项」恒为本用例自造的确定输入，UI 断言与 live 数据解耦。
    只写副本（DB_PATH 指向的文件），不触碰 live 库。
    表不存在时按 ORM 声明的列形态建表（裸库兜底），与 app.db 的 create_all 兼容。
    """
    snapshot = json.dumps({
        "control_model": EXP_CTRL_MODEL,
        "treatment_model": EXP_TREAT_MODEL,
    }, ensure_ascii=False)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS experiment ("
            " id INTEGER NOT NULL PRIMARY KEY,"
            " name VARCHAR DEFAULT '',"
            " hypothesis TEXT DEFAULT '',"
            " success_metric VARCHAR DEFAULT 'accuracy',"
            " guardrail_metrics TEXT DEFAULT '[]',"
            " status VARCHAR DEFAULT 'draft',"
            " grey_snapshot TEXT DEFAULT '{}',"
            " target_percent INTEGER DEFAULT 0,"
            " target_supplier_ids TEXT DEFAULT '[]',"
            " start_ts VARCHAR,"
            " end_ts VARCHAR,"
            " min_sample INTEGER DEFAULT 100,"
            " conclusion VARCHAR,"
            " conclusion_reason TEXT,"
            " concluded_by VARCHAR,"
            " concluded_at VARCHAR)")
        conn.execute("DELETE FROM experiment")
        cur = conn.execute(
            "INSERT INTO experiment (name, hypothesis, success_metric,"
            " guardrail_metrics, status, grey_snapshot, target_percent,"
            " target_supplier_ids, min_sample)"
            " VALUES (?, ?, ?, '[]', ?, ?, ?, '[]', ?)",
            (EXP_NAME, EXP_HYPOTHESIS, EXP_SUCCESS_METRIC,
             EXP_STATUS, snapshot, EXP_TARGET_PERCENT, EXP_MIN_SAMPLE))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def ensure_server():
    """在本用例的独立端口拉起被测服务，返回必须由本用例回收的 ServerHandle。

    统一经 `_playwright_server.start_pinned_server()` 启动：必然经启动器拉起并锁定
    域名 DNS 解析，且注入本用例受控副本为 DB_PATH、独立 RAG_DIR。不复用已在监听的
    服务（复用会让本用例自造的夹具实验不可见）；端口被占用时启动会失败并附日志尾部，
    避免「静默测了另一份数据」。
    """
    data_dir = tempfile.mkdtemp(prefix="ab_drawer_ui_db_")
    data_db = _prepare_data_db(data_dir)
    _seed_experiment(data_db)
    return start_pinned_server(
        PORT, data_dir, data_db,
        # 向量库存到本用例自己的临时目录，避免与别的服务实例争用同一 Chroma 目录。
        extra_env={"RAG_DIR": os.path.join(data_dir, "rag_chroma")},
        startup_timeout=40.0,
    )


def switch_demo_role(page, target_role="admin"):
    """切换角色并通过 reload 重新初始化权限状态"""
    current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
    if current_role == target_role:
        return
    with page.expect_navigation(timeout=15000):
        page.select_option("#demoRoleSelect", target_role)
    page.wait_for_selector("#demoRoleSelect", timeout=10000)
    page.wait_for_timeout(400)


def test_playwright_ab_experiment_drawer():
    """执行 A/B 科学实验高级抽屉及闭环深链的 Playwright 真实浏览器端到端核验"""
    server_handle = ensure_server()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 960})
            page = context.new_page()

            page.goto(BASE_URL, wait_until="domcontentloaded")
            page.wait_for_selector("#demoRoleSelect", timeout=12000)

            # 1. 切换为 admin 角色
            switch_demo_role(page, "admin")
            admin_role_val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
            assert admin_role_val == "admin", f"Expected admin role, got {admin_role_val}"

            # 2. 导航至系统与引擎配置 (click #adminEngineBtn)
            print("\n[Step 1] 点击 #adminEngineBtn 进入系统与引擎配置...")
            page.locator("#adminEngineBtn").click()
            page.wait_for_selector("#tab-engine", timeout=10000)
            assert page.locator("#tab-engine").is_visible(), "#tab-engine should be visible"

            # 切换至「02 流量调度与实验」子分页
            page.locator("#engineSubNav_02").click()
            page.wait_for_timeout(200)

            # 3. 断言两个高级抽屉均存在且标题正确
            print("[Step 2] 校验金丝雀灰度发布抽屉与 A/B 科学实验编排抽屉...")
            grey_drawer = page.locator("#adminGreyDrawer")
            exp_drawer = page.locator("#adminExperimentDrawer")

            assert grey_drawer.is_visible(), "#adminGreyDrawer should exist and be visible"
            assert exp_drawer.is_visible(), "#adminExperimentDrawer should exist and be visible"

            grey_title_text = grey_drawer.locator(".engine-drawer-title").inner_text().strip()
            assert "高级配置 · 金丝雀灰度发布" in grey_title_text, (
                f"Expected canary title in #adminGreyDrawer, got '{grey_title_text}'"
            )
            assert "A/B" not in grey_title_text, (
                f"金丝雀抽屉标题不得含 A/B（已与 A/B 实验解耦），got '{grey_title_text}'"
            )

            exp_title_text = exp_drawer.locator(".engine-drawer-title").inner_text().strip()
            assert "高级配置 · A/B 科学实验编排" in exp_title_text, (
                f"Expected A/B title in #adminExperimentDrawer, got '{exp_title_text}'"
            )

            # 4. 点击 #adminExperimentDrawer 头部展开抽屉 (移除 .collapsed)
            print("[Step 3] 展开 #adminExperimentDrawer 抽屉...")
            exp_drawer_header = exp_drawer.locator(".engine-drawer-header")
            exp_drawer_header.click()
            page.wait_for_timeout(300)

            drawer_classes = exp_drawer.get_attribute("class") or ""
            assert "collapsed" not in drawer_classes, (
                f"#adminExperimentDrawer should not be collapsed after click, got classes: '{drawer_classes}'"
            )

            # 5. 等待 #adminExpSelect 被实验选项填充
            print("[Step 4] 等待实验下拉列表 #adminExpSelect 加载...")
            page.wait_for_function(
                """() => {
                    const sel = document.getElementById('adminExpSelect');
                    return sel && sel.options.length > 0 && sel.options[0].value !== '';
                }""",
                timeout=15000
            )

            # 6. 断言实验详情面板 #adminExpDetailCard 可见
            print("[Step 5] 校验实验详情面板及关键要素...")
            detail_card = page.locator("#adminExpDetailCard")
            assert detail_card.is_visible(), "#adminExpDetailCard should be visible"

            # 7. 断言假设、指标、分流与样本量具有非空内容
            hyp_text = page.locator("#adminExpHypothesis").inner_text().strip()
            metric_text = page.locator("#adminExpMetric").inner_text().strip()
            traffic_text = page.locator("#adminExpTraffic").inner_text().strip()
            sample_text = page.locator("#adminExpMinSample").inner_text().strip()

            assert hyp_text and hyp_text != "—", f"Expected non-empty hypothesis, got '{hyp_text}'"
            assert metric_text and metric_text != "—", f"Expected non-empty metric, got '{metric_text}'"
            assert traffic_text and traffic_text != "—", f"Expected non-empty traffic, got '{traffic_text}'"
            assert sample_text and sample_text != "—", f"Expected non-empty min sample, got '{sample_text}'"

            # 8. 断言 Control vs Treatment 模型对比卡片可见且非空
            ctrl_model = page.locator("#adminExpCtrlModel")
            treat_model = page.locator("#adminExpTreatModel")
            assert ctrl_model.is_visible(), "#adminExpCtrlModel should be visible"
            assert treat_model.is_visible(), "#adminExpTreatModel should be visible"

            ctrl_model_text = ctrl_model.inner_text().strip()
            treat_model_text = treat_model.inner_text().strip()
            assert ctrl_model_text, "Expected non-empty Control model text"
            assert treat_model_text, "Expected non-empty Treatment model text"

            # 9. 截图保存: demo/gui-test-screenshots/admin_ab_experiment_drawer.png
            shot_drawer = os.path.join(SCREENSHOT_DIR, "admin_ab_experiment_drawer.png")
            page.screenshot(path=shot_drawer, full_page=False)
            print(f"Saved drawer screenshot: {shot_drawer}")

            # 10. 测试闭环深链 (Deep Link)
            print("[Step 6] 校验由抽屉直达 A/B 科学实验台的深链 (Deep Link)...")
            deep_link = exp_drawer.locator("a", has_text="前往 A/B 科学实验台查看 p 值指标看板")
            assert deep_link.is_visible(), "Deep link in #adminExperimentDrawer should be visible"
            deep_link.click()

            # 等待 #tab-analytics 变为 active
            page.wait_for_selector("#tab-analytics.active", timeout=10000)
            assert page.locator("#tab-analytics.active").is_visible(), "#tab-analytics.active should be visible"

            # 断言 #sec-experiment 可见
            sec_experiment = page.locator("#sec-experiment")
            page.wait_for_selector("#sec-experiment", state="visible", timeout=10000)
            assert sec_experiment.is_visible(), "#sec-experiment should be visible"

            # 断言标题包含 'A/B 科学实验台'
            exp_title = sec_experiment.locator(".analytics-section-title")
            assert exp_title.is_visible(), ".analytics-section-title in #sec-experiment should be visible"
            assert "A/B 科学实验台" in exp_title.inner_text(), (
                f"Expected 'A/B 科学实验台' in title, got '{exp_title.inner_text()}'"
            )

            # 等待实验指标与 p-value 卡片加载
            page.wait_for_function(
                """() => {
                    const ab = document.getElementById('analyticsExperimentsBody');
                    const pval = document.getElementById('pvalueCardsContainer');
                    return ab && pval && !ab.innerText.includes('加载中') && pval.children.length > 0;
                }""",
                timeout=15000
            )

            # 断言核心实验组件可见
            assert page.locator("#analyticsExperimentsBody").is_visible(), "#analyticsExperimentsBody should be visible"
            assert page.locator("#pvalueCardsContainer").is_visible(), "#pvalueCardsContainer should be visible"

            # 11. 截图保存: demo/gui-test-screenshots/admin_ab_experiment_deep_link_land.png
            shot_landing = os.path.join(SCREENSHOT_DIR, "admin_ab_experiment_deep_link_land.png")
            page.screenshot(path=shot_landing, full_page=False)
            print(f"Saved deep link landing screenshot: {shot_landing}")

            browser.close()
            print("\nPlaywright A/B Experiment Drawer E2E test passed successfully.")

    finally:
        if server_handle:
            server_handle.stop()


if __name__ == "__main__":
    test_playwright_ab_experiment_drawer()

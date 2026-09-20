# -*- coding: utf-8 -*-
"""
Exhaustive Playwright Audit of EVERY component in 'AI 效果观测与评测中心' (tab-analytics):
1. Segmented control switching between all 4 subviews
2. Subview 01: 埋点分布与挽回 (Tenant selector, refresh button, event distribution table, recovery stat cards)
3. Subview 02: 金丝雀发布监控 (Grey status, metrics bar, limit notice, samples table, modal inspection, deep-link button)
4. Subview 03: A/B 科学实验台 (Experiments table, refresh button, pvalue experiment selector switching, pvalue z-test cards)
5. Subview 04: 基准资产与确权 (GT stats, GT workbench link, golden scope filter switching, golden table rows, actions)
"""
import os
import pytest
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

BASE_URL = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:15010")
SCREENSHOT_DIR = "demo/gui-test-screenshots"


def _fetch_pvalue_cards(page, exp_ids):
    """只读取回每个实验的 p-value 卡片数据（页面自身的端点与请求出口，仅发 GET）。"""
    return page.evaluate(
        """async (ids) => {
            const out = {};
            for (const id of ids) {
                try {
                    const res = await window.apiFetch('/api/admin/experiments/' + id + '/pvalue');
                    const ret = await res.json();
                    out[String(id)] = (ret && ret.status === 'success' && ret.cards) ? ret.cards : null;
                } catch (e) {
                    out[String(id)] = null;
                }
            }
            return out;
        }""",
        exp_ids,
    )


def _fetch_golden_scope(page, scope):
    """只读取回黄金基准集看板在指定 scope 下的统计口径（页面自身的端点，仅发 GET）。"""
    return page.evaluate(
        """async (scope) => {
            try {
                const res = await window.apiFetch('/api/admin/golden-samples?scope=' + scope);
                const ret = await res.json();
                if (!ret || ret.status !== 'success') return null;
                return {
                    total: ret.total,
                    golden_total: ret.golden_total,
                    gt_confirmed_total: ret.gt_confirmed_total,
                    gt_pending_total: ret.gt_pending_total
                };
            } catch (e) {
                return null;
            }
        }""",
        scope,
    )


def _expected_view(cards):
    """把卡片数据折算成 DOM 上「必须出现」的判据（main.js renderPValueCards 的渲染口径）。"""
    p_values = []
    badges = set()
    for card in cards or []:
        if card.get("p_value") is None:
            p_values.append(None)
            badges.add("无法判定")
        else:
            p_values.append("%.4f" % float(card["p_value"]))
            badges.add("显著 (p <" if card.get("significant") else "不显著 (p >=")
    return {"p_values": p_values, "badges": badges}


def _wait_cards_render_as(page, cards, timeout=10000):
    """等待卡片容器渲染成 cards 对应的样子，返回是否成功（失败时由调用方给出可读断言）。"""
    view = _expected_view(cards)
    non_null = [p for p in view["p_values"] if p]
    try:
        page.wait_for_function(
            """(exp) => {
                const el = document.getElementById('pvalueCardsContainer');
                if (!el) return false;
                const txt = el.innerText || '';
                if (txt.indexOf('p-value：') === -1) return false;
                for (const p of exp.nonNull) { if (txt.indexOf(p) === -1) return false; }
                for (const b of exp.badges) { if (txt.indexOf(b) === -1) return false; }
                const rendered = (txt.match(/\\d+\\.\\d{4}/g) || []).length;
                return rendered === exp.nonNull.length;
            }""",
            arg={"nonNull": non_null, "badges": sorted(view["badges"])},
            timeout=timeout,
        )
        return True
    except PlaywrightTimeoutError:
        return False


def test_analytics_center_every_component_audit():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # -------------------------------------------------------------
        # Step 0: 登录并切入 AI 效果观测与评测中心
        # -------------------------------------------------------------
        page.goto(BASE_URL)
        page.wait_for_selector(".app-wrapper", timeout=10000)
        page.evaluate("() => { if (typeof setRole === 'function') setRole('admin'); }")
        page.wait_for_timeout(500)

        page.locator('.sidebar-btn[data-target="tab-analytics"]').click()
        page.wait_for_timeout(300)

        # -------------------------------------------------------------
        # Step 1: 功能区 01 - 埋点分布与挽回 (#sec-telemetry)
        # -------------------------------------------------------------
        print("=== 走查功能区 01: 埋点分布与挽回 ===")
        page.locator("#btnSubViewTelemetry").click()
        page.wait_for_timeout(500)

        # 等待事件分布和挽回指标卡片加载完毕
        page.wait_for_function(
            """() => {
                const dist = document.getElementById('analyticsEventDistBody');
                const rec = document.getElementById('analyticsRecoveryBody');
                return dist && rec && !dist.innerText.includes('加载中') && !rec.innerText.includes('加载中');
            }""",
            timeout=15000
        )

        # 1.1 检查租户选择器
        tenant_sel = page.locator("#analyticsTenantSelect")
        assert tenant_sel.is_visible(), "#analyticsTenantSelect must be visible"
        assert tenant_sel.locator("option").count() >= 1, "Tenant select must have at least 'all' option"
        # 切换租户并触发刷新
        tenant_sel.select_option("all")
        page.wait_for_function(
            """() => {
                const dist = document.getElementById('analyticsEventDistBody');
                return dist && !dist.innerText.includes('加载中') && dist.querySelector('table');
            }""",
            timeout=10000
        )

        # 1.2 检查全量埋点事件分布表格
        dist_body = page.locator("#analyticsEventDistBody")
        assert dist_body.is_visible()
        table_rows = dist_body.locator("table tbody tr")
        print(f"Telemetry event distribution rows: {table_rows.count()}")
        assert table_rows.count() > 0, "Telemetry event distribution table must have rows"

        # 1.3 检查挽回与点踩指标小卡片
        rec_body = page.locator("#analyticsRecoveryBody")
        assert rec_body.is_visible()
        print("Telemetry recovery metrics content loaded successfully")

        # 截屏功能区 01
        shot_01 = os.path.join(SCREENSHOT_DIR, "audit_01_telemetry_board.png")
        page.screenshot(path=shot_01, full_page=False)
        print(f"Saved: {shot_01}")

        # -------------------------------------------------------------
        # Step 2: 功能区 02 - 金丝雀发布监控 (#sec-canary)
        # -------------------------------------------------------------
        print("=== 走查功能区 02: 金丝雀发布监控 ===")
        page.locator("#btnSubViewCanary").click()
        page.wait_for_timeout(500)

        # 等待灰测样本表格与指标加载
        page.wait_for_function(
            """() => {
                const tbody = document.getElementById('adminGreySamplesBody');
                const mTotal = document.getElementById('mGreyTotal');
                return tbody && mTotal && !tbody.innerText.includes('拉取数据') && !tbody.innerText.includes('正在拉取') && mTotal.innerText !== '0';
            }""",
            timeout=15000
        )

        # 2.1 检查指标卡
        m_total = int(page.locator("#mGreyTotal").inner_text() or "0")
        print(f"Canary total samples reported: {m_total}")
        assert m_total > 0, "Canary total samples must be > 0"

        # 2.2 检查样本抽样提示
        notice = page.locator("#adminGreySampleLimitNotice")
        assert notice.is_visible(), "Sample limit notice should be visible when samples > 100"
        print(f"Notice text: {notice.inner_text()}")

        # 2.3 检查脱敏单据流表格无截断
        sample_rows = page.locator("#adminGreySamplesBody tr")
        print(f"Rendered canary sample rows: {sample_rows.count()}")
        assert sample_rows.count() > 0

        # 2.4 测试单据脱敏详情弹窗
        first_detail_btn = sample_rows.first.locator("button:has-text('查看脱敏解析')")
        assert first_detail_btn.is_visible(), "Detail button must be visible"
        first_detail_btn.click()
        page.wait_for_timeout(400)

        modal = page.locator("#adminGreySampleModal")
        assert modal.is_visible(), "adminGreySampleModal must open on click"
        modal_title = page.locator("#adminGreyModalTitle").inner_text()
        print(f"Modal opened: {modal_title}")

        # 截屏弹窗
        shot_modal = os.path.join(SCREENSHOT_DIR, "audit_02_canary_detail_modal.png")
        page.screenshot(path=shot_modal, full_page=False)
        print(f"Saved: {shot_modal}")

        # 关闭弹窗
        modal.locator(".modal-close, button:has-text('关闭')").first.click()
        page.wait_for_timeout(300)
        assert not modal.is_visible(), "Modal must be closed"

        # 2.5 测试“刷新脱敏单据流”按钮
        refresh_canary_btn = page.locator("#sec-canary button:has-text('刷新脱敏单据流')")
        assert refresh_canary_btn.is_visible()
        refresh_canary_btn.click()
        page.wait_for_function(
            """() => {
                const tbody = document.getElementById('adminGreySamplesBody');
                return tbody && !tbody.innerText.includes('正在拉取') && tbody.children.length > 0;
            }""",
            timeout=10000
        )

        # 截屏功能区 02
        shot_02 = os.path.join(SCREENSHOT_DIR, "audit_02_canary_board.png")
        page.screenshot(path=shot_02, full_page=False)
        print(f"Saved: {shot_02}")

        # -------------------------------------------------------------
        # Step 3: 功能区 03 - A/B 科学实验台 (#sec-experiment)
        # -------------------------------------------------------------
        print("=== 走查功能区 03: A/B 科学实验台 ===")
        page.locator("#btnSubViewExperiment").click()
        page.wait_for_timeout(500)

        page.wait_for_function(
            """() => {
                const ab = document.getElementById('analyticsExperimentsBody');
                const sel = document.getElementById('pvalueExperimentSelect');
                const pval = document.getElementById('pvalueCardsContainer');
                return ab && sel && pval && !ab.innerText.includes('加载中') && pval.children.length > 0;
            }""",
            timeout=15000
        )

        # 3.1 检查实验概览表格
        ab_table = page.locator("#analyticsExperimentsBody table")
        assert ab_table.is_visible(), "A/B experiments overview table must be visible"
        ab_rows = ab_table.locator("tbody tr")
        print(f"A/B experiment rows: {ab_rows.count()}")
        assert ab_rows.count() >= 2, "Should have at least 2 experiments (#1, #2)"

        # 3.2 检查“刷新实验列表”按钮
        #     按行为（onclick 绑定的处理函数）定位而非按文案：该按钮文案曾由
        #     “刷新实验数据”改为“刷新实验列表”，按 ::has-text 定位的旧断言因此
        #     长期失效（HEAD 起就不匹配），按 handler 定位可免受文案演变影响。
        refresh_exp_btn = page.locator(
            "#sec-experiment button[onclick*='loadAnalyticsExperiments']"
        )
        assert refresh_exp_btn.is_visible(), "刷新实验列表按钮必须可见"
        refresh_exp_btn.click()
        page.wait_for_function(
            """() => {
                const ab = document.getElementById('analyticsExperimentsBody');
                return ab && !ab.innerText.includes('加载中') && ab.querySelector('table');
            }""",
            timeout=10000
        )

        # 3.3 检查显著性实验选择器切换与卡片联动
        #     数据无关：实验 id 与 p-value 数值都随库中数据变化（清理测试单据后默认
        #     选中项就会变），故不写死任何 id 或数值；改以页面自身的只读端点
        #     /api/admin/experiments/{id}/pvalue 作为「该实验应渲染成什么样」的口径，
        #     交叉核对卡片内容确实属于当前选中项（这是「不串台」的硬证据）。
        pval_sel = page.locator("#pvalueExperimentSelect")
        assert pval_sel.is_visible()
        pval_cards = page.locator("#pvalueCardsContainer")

        # 刷新实验列表后选择器会被重建，等它稳定下来再读取默认选中项
        page.wait_for_function(
            """() => {
                const sel = document.getElementById('pvalueExperimentSelect');
                return sel && sel.options.length >= 2 && sel.value !== '';
            }""",
            timeout=10000
        )
        pval_option_values = [
            v for v in pval_sel.locator("option").evaluate_all("els => els.map(e => e.value)") if v
        ]
        assert len(pval_option_values) >= 2, "显著性实验选择器至少应有 2 个实验可选"

        default_exp = pval_sel.input_value()
        assert default_exp in pval_option_values, f"选择器默认值 '{default_exp}' 不是有效实验"
        print(f"显著性卡片默认选中实验: {default_exp}")

        pval_expected = _fetch_pvalue_cards(page, pval_option_values)
        assert pval_expected.get(default_exp), f"实验 #{default_exp} 的 p-value 数据不可用"
        assert _wait_cards_render_as(page, pval_expected[default_exp]), (
            f"默认实验 #{default_exp} 的卡片未渲染出对应内容，实际渲染:\n{pval_cards.inner_text()}"
        )
        default_text = pval_cards.inner_text()
        # 卡片区必须渲染 3 个指标卡片，且每张都带 p-value 行与显著性角标
        assert default_text.count("p-value：") == 3, f"应渲染 3 张指标卡片，实际:\n{default_text}"
        assert any(b in default_text for b in ("无法判定", "显著 (p <", "不显著 (p >=")), (
            f"卡片必须带显著性角标，实际:\n{default_text}"
        )

        # 选一个「渲染结果与默认项不同」的实验来验证联动（不写死 id）；若所有实验
        # 渲染结果一致（例如全库实验都无样本），退化为任取另一项并只验证内容与选中项一致
        other_exp = None
        for v in reversed(pval_option_values):
            if v == default_exp:
                continue
            if pval_expected.get(v) and pval_expected[v] != pval_expected[default_exp]:
                other_exp = v
                break
        if other_exp is None:
            other_exp = next(v for v in pval_option_values if v != default_exp)
            print("提示：所有实验的卡片渲染结果一致，本次只验证「内容与选中项一致」")

        # 切换到另一个实验：内容必须变成「该实验」的，且选择值不能被重置
        print(f"显著性卡片切换到实验: {other_exp}")
        pval_sel.select_option(other_exp)
        assert _wait_cards_render_as(page, pval_expected[other_exp]), (
            f"切到实验 #{other_exp} 后卡片内容与该实验数据不一致，实际渲染:\n{pval_cards.inner_text()}"
        )
        assert pval_sel.input_value() == other_exp, (
            f"切换后被重置：期望 '{other_exp}'，实际 '{pval_sel.input_value()}'"
        )

        # 切回默认实验：内容必须复原，值与初值一致
        pval_sel.select_option(default_exp)
        assert _wait_cards_render_as(page, pval_expected[default_exp]), (
            f"切回实验 #{default_exp} 后卡片内容未复原，实际渲染:\n{pval_cards.inner_text()}"
        )
        assert pval_sel.input_value() == default_exp, (
            f"切回后被重置：期望 '{default_exp}'，实际 '{pval_sel.input_value()}'"
        )
        assert pval_cards.inner_text() == default_text, "切回默认实验后卡片内容与初值不一致（串台）"

        # 截屏功能区 03
        shot_03 = os.path.join(SCREENSHOT_DIR, "audit_03_experiment_board.png")
        page.screenshot(path=shot_03, full_page=False)
        print(f"Saved: {shot_03}")

        # -------------------------------------------------------------
        # Step 4: 功能区 04 - 基准资产与确权 (#sec-eval)
        # -------------------------------------------------------------
        print("=== 走查功能区 04: 基准资产与确权 ===")
        page.locator("#btnSubViewEval").click()
        page.wait_for_timeout(500)

        page.wait_for_function(
            """() => {
                const stats = document.getElementById('gtEvalsetStats');
                const gbody = document.getElementById('goldenBoardBody');
                return stats && gbody && !stats.innerText.includes('加载中') && !gbody.innerText.includes('加载中');
            }""",
            timeout=15000
        )

        # 4.1 检查确权进度统计与独立确权台深链
        evalset_stats = page.locator("#gtEvalsetStats")
        assert evalset_stats.is_visible()
        print(f"GT evalset stats: {evalset_stats.inner_text()}")
        assert "已确权" in evalset_stats.inner_text()

        evalset_link = page.locator("#sec-eval a.btn[href='/evalset']")
        assert evalset_link.is_visible(), "Link to /evalset must be visible"

        # 4.2 检查黄金基准集范围选择器过滤
        #     数据无关：各范围内的样本条数随库中数据变化（曾写死「已 GT 确权 >= 5」，
        #     那 5 条恰好来自与被清理测试单据 1–5 绑定的确权样本，清理后必然假失败），
        #     故改为「DOM 行数 == 页面自身只读端点返回的条数」这一交叉核对。
        #     端点 items 上限 500、空结果渲染 1 行占位，故行数 = total>0 ? min(total,500) : 1。
        scope_sel = page.locator("#goldenScopeSelect")
        assert scope_sel.is_visible()

        def expected_row_count(scope_name):
            data = _fetch_golden_scope(page, scope_name)
            assert data is not None, f"scope={scope_name} 的看板数据不可用"
            total = int(data["total"])
            return (min(total, 500) if total > 0 else 1), data

        golden_rows = page.locator("#goldenBoardBody tr")
        all_expected, all_data = expected_row_count("all")
        total_all_rows = golden_rows.count()
        print(f"Golden board 'all' rows: {total_all_rows} (接口 total={all_data['total']})")
        assert total_all_rows == all_expected

        def wait_golden_loaded():
            page.wait_for_function(
                """() => {
                    const gbody = document.getElementById('goldenBoardBody');
                    return gbody && !gbody.innerText.includes('加载中');
                }""",
                timeout=10000
            )

        # 切换范围至仅基准集成员
        scope_sel.select_option("golden")
        wait_golden_loaded()
        assert scope_sel.input_value() == "golden"
        golden_expected, golden_data = expected_row_count("golden")
        golden_member_rows = page.locator("#goldenBoardBody tr").count()
        print(f"Golden member rows: {golden_member_rows} (接口 total={golden_data['total']})")
        assert golden_member_rows == golden_expected
        assert int(golden_data["golden_total"]) > 0, "基准集成员数应大于 0"

        # 切换范围至仅已 GT 确权
        scope_sel.select_option("gt_confirmed")
        wait_golden_loaded()
        assert scope_sel.input_value() == "gt_confirmed"
        confirmed_expected, confirmed_data = expected_row_count("gt_confirmed")
        confirmed_rows = page.locator("#goldenBoardBody tr").count()
        print(f"GT confirmed rows: {confirmed_rows} (接口 total={confirmed_data['total']})")
        assert confirmed_rows == confirmed_expected

        # 切换范围至待确权候选池
        scope_sel.select_option("gt_pending")
        wait_golden_loaded()
        assert scope_sel.input_value() == "gt_pending"
        pending_expected, pending_data = expected_row_count("gt_pending")
        pending_rows = page.locator("#goldenBoardBody tr").count()
        print(f"GT pending rows: {pending_rows} (接口 total={pending_data['total']})")
        assert pending_rows == pending_expected

        # 切回全部
        scope_sel.select_option("all")
        wait_golden_loaded()
        assert scope_sel.input_value() == "all"
        assert page.locator("#goldenBoardBody tr").count() == total_all_rows

        # 4.3 检查基准集刷新按钮
        refresh_golden_btn = page.locator("#sec-eval button:has-text('刷新')")
        assert refresh_golden_btn.is_visible()
        refresh_golden_btn.click()
        page.wait_for_timeout(400)

        # 截屏功能区 04
        shot_04 = os.path.join(SCREENSHOT_DIR, "audit_04_golden_eval_board.png")
        page.screenshot(path=shot_04, full_page=False)
        print(f"Saved: {shot_04}")

        print("=== 全量 4 大功能区所有组件走查 100% 成功！ ===")
        browser.close()

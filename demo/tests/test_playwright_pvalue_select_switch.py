# -*- coding: utf-8 -*-
"""
E2E Playwright 用例：验证 select#pvalueExperimentSelect 切换实验时选择框的值不被重置，
且显著性卡片始终反映「当前选中的那个实验」（联动 + 可逆 + 不串台）。

数据无关原则（2026-09-20 重写）：实验 id、p-value 数值、样本量都随库中数据变化，
原用例把「默认选中实验 #2、其 p 值为 0.1360」写死在断言里，一旦库中实验集合或样本
规模变化（例如清理历史测试单据）就必然假失败。现改为：
  1. 从选择器的 option 列表读出现有实验 id —— 不写死任何 id；
  2. 以页面自身使用的只读端点 /api/admin/experiments/{id}/pvalue 作为「该实验应渲染
     成什么样」的判据，交叉核对卡片内容确实属于当前选中项（这是「不串台」的硬证据）；
  3. 断言「默认项渲染正确 → 切到另一个渲染结果不同的实验 → 内容随之变化且值保持
     → 切回默认项 → 内容复原且值与初值一致」。
"""
import os

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

BASE_URL = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:15010")
SCREENSHOT_DIR = "demo/gui-test-screenshots"


def _fetch_pvalue_cards(page, exp_ids):
    """只读取回每个实验的 p-value 卡片数据。

    走 window.apiFetch（与页面同一请求出口，自动附带身份头），只发 GET，不产生任何写操作。
    """
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


def _expected_view(cards):
    """把卡片数据折算成 DOM 上「必须出现」的判据。

    main.js renderPValueCards 的渲染口径：p-value 为 4 位小数、无数据时显示 '-'；
    角标为「显著 (p < ...)」「不显著 (p >= ...)」「无法判定」三选一。
    """
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
    """等待卡片容器渲染成 cards 对应的样子，返回是否成功。

    用「已知口径」等待而不是盲等固定时长，既避免竞态也避免无意义 sleep。
    判据中的「4 位小数数字个数 == 非空 p-value 个数」保证不多不少，能抓出串台
    （显示成上一个实验的数值）与残留（旧数值没被替换）。
    """
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


def test_pvalue_experiment_select_switch():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 1. 访问首页并以 admin 身份登录
        page.goto(BASE_URL)
        page.wait_for_selector(".app-wrapper", timeout=10000)
        page.evaluate("() => { if (typeof setRole === 'function') setRole('admin'); }")
        page.wait_for_timeout(500)

        # 2. 切换到 AI 效果观测与评测中心
        page.locator('.sidebar-btn[data-target="tab-analytics"]').click()
        page.wait_for_timeout(300)

        # 3. 切换到 功能区 3: A/B 科学实验台
        page.locator("#btnSubViewExperiment").click()
        page.wait_for_timeout(500)

        # 等待实验指标与 p-value 卡片加载（选择器至少 2 个实验、卡片有内容）
        page.wait_for_function(
            """() => {
                const sel = document.getElementById('pvalueExperimentSelect');
                const pval = document.getElementById('pvalueCardsContainer');
                return sel && sel.options.length >= 2 && sel.value !== ''
                    && pval && pval.children.length > 0;
            }""",
            timeout=15000
        )

        select = page.locator("#pvalueExperimentSelect")
        cards = page.locator("#pvalueCardsContainer")

        # 4. 实验 id 不写死：从选择器的 option 列表读出现有实验
        option_values = [
            v for v in select.locator("option").evaluate_all("els => els.map(e => e.value)") if v
        ]
        assert len(option_values) >= 2, f"至少需要 2 个实验选项才能验证切换联动，实际为 {option_values}"

        initial_val = select.input_value()
        print(f"默认选中的实验 id: {initial_val}")
        assert initial_val in option_values, (
            f"选择器默认值 '{initial_val}' 不在选项列表 {option_values} 中"
        )

        # 5. 取回每个实验「应渲染」的卡片数据（只读），作为后续交叉核对与确定性等待的口径
        expected_cards = _fetch_pvalue_cards(page, option_values)
        available = [k for k, v in expected_cards.items() if v]
        print(f"可取得 p-value 卡片数据的实验数: {len(available)}/{len(option_values)}")
        assert expected_cards.get(initial_val), (
            f"无法取回默认实验 #{initial_val} 的 p-value 数据，页面端点或身份头异常"
        )

        # 默认选中项的卡片必须与「该实验」的数据一致（联动基线）
        assert _wait_cards_render_as(page, expected_cards[initial_val]), (
            f"默认实验 #{initial_val} 的卡片内容与其数据不一致，实际渲染:\n{cards.inner_text()}"
        )
        initial_text = cards.inner_text()
        assert initial_text.strip(), "默认选中实验的 p-value 卡片必须渲染出内容"

        # 6. 选一个「渲染结果与默认项不同」的实验来验证联动（仍不写死 id）。
        #    若所有实验渲染结果一致（例如全库实验都无样本），则退化为任取另一项：
        #    此时无法验证「内容随切换变化」，但仍会验证「内容与选中项一致」。
        other_val = None
        for v in reversed(option_values):
            if v == initial_val:
                continue
            if expected_cards.get(v) and expected_cards[v] != expected_cards[initial_val]:
                other_val = v
                break
        if other_val is None:
            other_val = next(v for v in option_values if v != initial_val)
            print("提示：所有实验的卡片渲染结果一致，本次只能验证「内容与选中项一致」，无法验证「内容随切换变化」")

        # 7. 切到另一个实验：卡片内容必须变成「该实验」的，且选择值不能被重置
        print(f"切换到实验 id: {other_val}")
        select.select_option(other_val)
        assert _wait_cards_render_as(page, expected_cards[other_val]), (
            f"切到实验 #{other_val} 后卡片内容与该实验数据不一致，实际渲染:\n{cards.inner_text()}"
        )
        assert select.input_value() == other_val, (
            f"切换后被重置：期望 '{other_val}'，实际 '{select.input_value()}'"
        )
        if expected_cards[other_val] != expected_cards[initial_val]:
            assert cards.inner_text() != initial_text, "卡片内容未随选择器切换而变化（联动失效）"

        shot_other = os.path.join(SCREENSHOT_DIR, "pvalue_select_switched_to_other.png")
        page.screenshot(path=shot_other, full_page=False)
        print(f"保存切换后截图: {shot_other}")

        # 8. 切回默认实验：内容必须复原，值必须回到初值
        print(f"切回实验 id: {initial_val}")
        select.select_option(initial_val)
        assert _wait_cards_render_as(page, expected_cards[initial_val]), (
            f"切回实验 #{initial_val} 后卡片内容未复原，实际渲染:\n{cards.inner_text()}"
        )
        assert select.input_value() == initial_val, (
            f"切回后被重置：期望 '{initial_val}'，实际 '{select.input_value()}'"
        )
        assert cards.inner_text() == initial_text, "切回默认实验后卡片内容与初值不一致（串台）"

        shot_back = os.path.join(SCREENSHOT_DIR, "pvalue_select_switched_back_to_default.png")
        page.screenshot(path=shot_back, full_page=False)
        print(f"保存切回截图: {shot_back}")

        browser.close()

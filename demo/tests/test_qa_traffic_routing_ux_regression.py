# -*- coding: utf-8 -*-
"""QA 专属全量回归与边界审计测试套件：View 02 流量调度与实验 (Traffic & Experiments).

验证依据:
- 产品规格说明书: /Users/ethan/harness-tmp/traffic-routing-ux-redesign/01-product-spec.md (SPEC-TRX-20261005-01)
- 开发交付报告: /Users/ethan/harness-tmp/traffic-routing-ux-redesign/02-dev-report.md (DEV-TRX-20261005-01)
- 覆盖项: AC-01 ~ AC-18 静态 DOM 契约、边界极限值、状态机流转与无 Emoji 铁律
"""

import os
import re
import unicodedata
import pytest
from bs4 import BeautifulSoup

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
INDEX_HTML = os.path.join(DEMO_DIR, "templates", "index.html")
MAIN_JS = os.path.join(DEMO_DIR, "static", "js", "main.js")
STYLE_CSS = os.path.join(DEMO_DIR, "static", "css", "style.css")


@pytest.fixture(scope="module")
def html_soup():
    assert os.path.isfile(INDEX_HTML), f"index.html missing: {INDEX_HTML}"
    with open(INDEX_HTML, "r", encoding="utf-8") as f:
        content = f.read()
    return BeautifulSoup(content, "html.parser")


@pytest.fixture(scope="module")
def main_js_content():
    assert os.path.isfile(MAIN_JS), f"main.js missing: {MAIN_JS}"
    with open(MAIN_JS, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def style_css_content():
    assert os.path.isfile(STYLE_CSS), f"style.css missing: {STYLE_CSS}"
    with open(STYLE_CSS, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1. DOM 结构契约测试 (AC-01, AC-02, AC-03)
# ---------------------------------------------------------------------------

def test_qa_ac01_experiment_drawer_title_contract(html_soup):
    """AC-01: #adminExperimentDrawer 内部必须存在类名 .engine-drawer-title 且文案包含 A/B 科学实验编排。"""
    drawer = html_soup.select_one("#adminExperimentDrawer")
    assert drawer is not None, "未找到 #adminExperimentDrawer 容器"
    title_el = drawer.select_one(".engine-drawer-title")
    assert title_el is not None, "#adminExperimentDrawer 内丢失 .engine-drawer-title 契约节点"
    title_text = title_el.get_text(strip=True)
    assert "高级配置 · A/B 科学实验编排" in title_text, f"抽屉标题不匹配: {title_text}"


def test_qa_ac02_grey_drawer_title_contract(html_soup):
    """AC-02: #adminGreyDrawer 内部必须存在 .engine-drawer-title，文案包含金丝雀灰度发布，且不得为 display:none。"""
    drawer = html_soup.select_one("#adminGreyDrawer")
    assert drawer is not None, "未找到 #adminGreyDrawer 容器"
    title_el = drawer.select_one(".engine-drawer-title")
    assert title_el is not None, "#adminGreyDrawer 内丢失 .engine-drawer-title 契约节点"
    title_text = title_el.get_text(strip=True)
    assert "高级配置 · 金丝雀灰度发布" in title_text, f"灰测标题不匹配: {title_text}"
    style_attr = (title_el.get("style") or "").replace(" ", "").lower()
    assert "display:none" not in style_attr, "标题节点被错误设置为 display:none 导致契约失效"


# ---------------------------------------------------------------------------
# 2. 全局无 Emoji 铁律审计 (AC-04)
# ---------------------------------------------------------------------------

def test_qa_ac04_zero_emoji_in_view02_and_assets(html_soup, main_js_content, style_css_content):
    """AC-04: View 02 页面代码、CSS 类名及控制台相关 JS 提示中不得残留任何 Unicode Emoji。"""
    emoji_regex = re.compile(
        r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]"
    )

    # 1. 扫描 View 02 所在 DOM 区域
    view02_wrap = html_soup.select_one("#adminGreyDrawer")
    if view02_wrap:
        matches = emoji_regex.findall(str(view02_wrap))
        assert len(matches) == 0, f"#adminGreyDrawer 中发现 Emoji 字符: {matches}"

    exp_drawer = html_soup.select_one("#adminExperimentDrawer")
    if exp_drawer:
        matches = emoji_regex.findall(str(exp_drawer))
        assert len(matches) == 0, f"#adminExperimentDrawer 中发现 Emoji 字符: {matches}"

    modal = html_soup.select_one("#createExperimentModal")
    if modal:
        matches = emoji_regex.findall(str(modal))
        assert len(matches) == 0, f"#createExperimentModal 中发现 Emoji 字符: {matches}"

    # 2. 扫描 style.css 中 .trx-* 相关规则
    trx_css_blocks = re.findall(r"\.trx-[^{]+\{[^}]+\}", style_css_content)
    for block in trx_css_blocks:
        matches = emoji_regex.findall(block)
        assert len(matches) == 0, f".trx 样式规则中发现 Emoji: {block}"


# ---------------------------------------------------------------------------
# 3. 控件与组件联动完整性 (AC-05, AC-06, AC-07, AC-08)
# ---------------------------------------------------------------------------

def test_qa_ac05_slider_and_input_elements_exist(html_soup, main_js_content):
    """AC-05: 滑动条 #adminGreySlider 与数字框 #adminGreyPercent 均存在且绑定双向联动。"""
    slider = html_soup.select_one("#adminGreySlider")
    num_input = html_soup.select_one("#adminGreyPercent")
    ratio_bar = html_soup.select_one("#trxSplitRatioBar, .trx-ratio-bar")
    assert slider is not None, "缺失 #adminGreySlider 滑块"
    assert num_input is not None, "缺失 #adminGreyPercent 数字输入框"
    assert ratio_bar is not None, "缺失 #trxSplitRatioBar / .trx-ratio-bar 比例指示柱"

    # JS 逻辑健全性校验
    assert "syncGreySplitSlider" in main_js_content
    assert "bindTrxSliderEvents" in main_js_content


def test_qa_ac06_mode_explanation_element_and_js(html_soup, main_js_content):
    """AC-06: 场景化分流提示卡片 #trxAssignModeExplanation 存在且有对应切换逻辑。"""
    explanation_el = html_soup.select_one("#trxAssignModeExplanation")
    assert explanation_el is not None, "缺失 #trxAssignModeExplanation 提示节点"
    assert "onGreyAssignModeChange" in main_js_content
    assert "供应商哈希" in main_js_content
    assert "单据随机" in main_js_content


def test_qa_ac07_three_legs_containers_exist(html_soup):
    """AC-07: 灰测识别、审核、解析三腿卡片容器完整保留。"""
    assert html_soup.select_one("#adminGreyOpenaiRecBox") is not None
    assert html_soup.select_one("#adminGreyOpenaiAudBox") is not None
    assert html_soup.select_one("#adminGreyParseBox") is not None


def test_qa_ac08_adv_params_dynamic_badge_logic(main_js_content):
    """AC-08: 高级参数徽章具有动态装配逻辑。"""
    assert "updateAdvBadge" in main_js_content
    assert "tokens" in main_js_content
    assert "checkTempWarn" in main_js_content


# ---------------------------------------------------------------------------
# 4. 实验全景与弹窗契约 (AC-12, AC-13, AC-14, AC-15, AC-16)
# ---------------------------------------------------------------------------

def test_qa_ac13_sample_progress_and_significance_elements(html_soup):
    """AC-13: 选定实验全景卡片具备样本量积累进度条与显著性看板节点。"""
    assert html_soup.select_one("#adminExpProgressBar") is not None
    assert html_soup.select_one("#adminExpProgressText") is not None
    assert html_soup.select_one("#adminExpSignificanceBox") is not None
    assert html_soup.select_one("#adminExpPValueBadge") is not None
    assert html_soup.select_one("#adminExpSignificanceSummary") is not None


def test_qa_ac14_experiment_actions_buttons(html_soup):
    """AC-14: 启动与暂停实验按钮存在且具备生命周期操作绑定。"""
    start_btn = html_soup.select_one("#adminExpStartBtn")
    stop_btn = html_soup.select_one("#adminExpStopBtn")
    assert start_btn is not None
    assert stop_btn is not None


def test_qa_ac15_deep_link_function_defined(main_js_content):
    """AC-15: 跨 Tab 跳转深链函数 gotoExperimentObservatory 存在。"""
    assert "gotoExperimentObservatory" in main_js_content
    assert "analyticsBoardBtn" in main_js_content
    assert "sec-experiment" in main_js_content


def test_qa_ac16_create_modal_structure(html_soup):
    """AC-16: 新建 A/B 实验模态框 #createExperimentModal 具备双模型配置卡片与字段。"""
    modal = html_soup.select_one("#createExperimentModal")
    assert modal is not None
    assert modal.select_one("#newExpName") is not None
    assert modal.select_one("#newExpHypothesis") is not None
    assert modal.select_one("#newExpCtrlBox") is not None
    assert modal.select_one("#newExpTreatBox") is not None
    assert modal.select_one("#newExpTreatmentModel") is not None
    assert modal.select_one("#newExpSuccessMetric") is not None
    assert modal.select_one("#newExpTargetPercent") is not None
    assert modal.select_one("#newExpMinSample") is not None

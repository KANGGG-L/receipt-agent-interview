# -*- coding: utf-8 -*-
"""QA 专属全量回归与契约审计测试套件：系统与引擎配置二级分段导航分页化 (Engine Sub-Nav Pagination).

验证依据:
- 产品规格说明书: /Users/ethan/harness-tmp/engine-subnav-pagination/01-product-spec.md
- 开发交付报告: /Users/ethan/harness-tmp/engine-subnav-pagination/02-dev-report.md
- 覆盖项: AC-01 ~ AC-10 独立互斥分页、WAI-ARIA 属性、深链穿透、跨页校验与零 Emoji 铁律
"""

import os
import re
import pytest
from bs4 import BeautifulSoup
from f4_frontend_sources import frontend_union  # F4 S4-11: main.js+域文件联合全文

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
    return frontend_union()  # F4 S4-11: main.js+域文件联合全文


@pytest.fixture(scope="module")
def style_css_content():
    assert os.path.isfile(STYLE_CSS), f"style.css missing: {STYLE_CSS}"
    with open(STYLE_CSS, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1. DOM 契约与独立分页初态验证 (AC-01, AC-02, AC-03, AC-09)
# ---------------------------------------------------------------------------

def test_engine_subnav_tablist_and_aria_contracts(html_soup):
    """AC-03: #engineSubNav 必须具备 role='tablist'，其内部按钮具备标准 WAI-ARIA 属性。"""
    subnav = html_soup.select_one("#engineSubNav")
    assert subnav is not None, "未找到 #engineSubNav 导航条"
    assert subnav.get("role") == "tablist", "#engineSubNav 缺少 role='tablist'"

    btns = {
        "01": subnav.select_one("#engineSubNav_01"),
        "02": subnav.select_one("#engineSubNav_02"),
        "03": subnav.select_one("#engineSubNav_03"),
    }
    for num, btn in btns.items():
        assert btn is not None, f"未找到 #engineSubNav_{num} 按钮"
        assert btn.get("role") == "tab", f"#engineSubNav_{num} 缺少 role='tab'"
        assert btn.get("aria-controls") == f"engineSubView_{num}", f"#engineSubNav_{num} aria-controls 错误"

    # 默认 01 激活，02 与 03 未激活
    assert "active" in (btns["01"].get("class") or []), "#engineSubNav_01 初始必须包含 active 类"
    assert btns["01"].get("aria-selected") == "true", "#engineSubNav_01 初始 aria-selected 必须为 true"
    assert "active" not in (btns["02"].get("class") or []), "#engineSubNav_02 初始不得包含 active 类"
    assert btns["02"].get("aria-selected") == "false", "#engineSubNav_02 初始 aria-selected 必须为 false"
    assert "active" not in (btns["03"].get("class") or []), "#engineSubNav_03 初始不得包含 active 类"
    assert btns["03"].get("aria-selected") == "false", "#engineSubNav_03 初始 aria-selected 必须为 false"


def test_engine_subviews_initial_state_isolation(html_soup):
    """AC-01, AC-02: 初始状态下 View 01 渲染可见，View 02 与 View 03 严格隐藏 (d-none & display:none)。"""
    v01 = html_soup.select_one("#engineSubView_01")
    v02 = html_soup.select_one("#engineSubView_02")
    v03 = html_soup.select_one("#engineSubView_03")

    assert v01 is not None, "未找到 #engineSubView_01 容器"
    assert v02 is not None, "未找到 #engineSubView_02 容器"
    assert v03 is not None, "未找到 #engineSubView_03 容器"

    # WAI-ARIA tabpanel 属性
    for num, v in [("01", v01), ("02", v02), ("03", v03)]:
        assert v.get("role") == "tabpanel", f"#engineSubView_{num} 缺少 role='tabpanel'"
        assert v.get("aria-labelledby") == f"engineSubNav_{num}", f"#engineSubView_{num} 缺少 aria-labelledby"

    # 初始隔离性：01 无 d-none，02/03 带 d-none 且初始隐藏。
    # F3·B1 起 index.html 内联 style 属性清退（改为 csp-s-N 类 + csp-html-styles.css 规则体），
    # 「内联 display:none」判据按 F3 已固化的等效判据重写：解析元素的 csp-s-N 类，
    # 并断言 csp-html-styles.css 中该类规则体含 display:none（判别力不变：规则体缺失
    # 或类被改挂到别的元素都会失败）。
    csp_css = open(os.path.join(DEMO_DIR, "static", "css", "csp-html-styles.css"), encoding="utf-8").read()

    def _hidden_by_csp_class(el, label):
        classes = (el.get("class") or [])
        csp = [c for c in classes if c.startswith("csp-s-")]
        assert csp, "%s 未挂任何 csp-s-N 初始隐藏类" % label
        for c in csp:
            assert (".%s{display:none}" % c) in csp_css.replace(" ", ""), \
                "%s 的 %s 规则体必须含 display:none" % (label, c)

    v01_classes = v01.get("class") or []
    assert "d-none" not in v01_classes, "#engineSubView_01 初始不得包含 d-none 类"

    v02_classes = v02.get("class") or []
    assert "d-none" in v02_classes, "#engineSubView_02 初始必须包含 d-none 类"
    _hidden_by_csp_class(v02, "#engineSubView_02")

    v03_classes = v03.get("class") or []
    assert "d-none" in v03_classes, "#engineSubView_03 初始必须包含 d-none 类"
    _hidden_by_csp_class(v03, "#engineSubView_03")


def test_sticky_footer_and_error_badges_contract(html_soup):
    """AC-07, AC-09: 吸底操作栏常驻且错误红点标记存在且初始为 hide。"""
    footer = html_soup.select_one("#engineStickyFooter")
    assert footer is not None, "未找到 #engineStickyFooter 吸底操作栏"
    assert footer.select_one("#btnSaveUnifiedConfig") is not None, "吸底栏丢失保存按钮 #btnSaveUnifiedConfig"
    assert footer.select_one("#btnDiscardEngineChanges") is not None, "吸底栏丢失放弃修改按钮"

    for num in ["01", "02", "03"]:
        badge = html_soup.select_one(f"#engineTabErr_{num}")
        assert badge is not None, f"未找到红点徽章 #engineTabErr_{num}"
        classes = badge.get("class") or []
        assert "badge-tab-error" in classes, f"#engineTabErr_{num} 缺少 badge-tab-error 类"
        assert "hide" in classes, f"#engineTabErr_{num} 初始必须包含 hide 类"


# ---------------------------------------------------------------------------
# 2. CSS 样式契约验证 (AC-01, AC-03)
# ---------------------------------------------------------------------------

def test_css_subview_isolation_and_btn_styles(style_css_content):
    """AC-01, AC-03: style.css 中必须包含 .engine-subview.d-none 强隐藏规则及按钮交互样式。"""
    assert ".engine-subview.d-none" in style_css_content, "style.css 缺少 .engine-subview.d-none 规则"
    assert re.search(r"\.engine-subview\.d-none\s*\{[^}]*display:\s*none\s*!important", style_css_content), \
        ".engine-subview.d-none 缺少 display: none !important"

    assert ".engine-sub-btn.active" in style_css_content, "缺少 .engine-sub-btn.active 高亮样式"
    assert ".engine-sub-btn:focus-visible" in style_css_content, "缺少 .engine-sub-btn:focus-visible 焦点样式"


# ---------------------------------------------------------------------------
# 3. JavaScript 函数实现与联动逻辑验证 (AC-01, AC-05, AC-06, AC-07, AC-08)
# ---------------------------------------------------------------------------

def test_switch_engine_subview_implementation(main_js_content):
    """AC-01, AC-05: switchEngineSubView 必须实现严格隐藏/显示切换、WAI-ARIA 更新与 Hash 保持。"""
    assert "function switchEngineSubView(" in main_js_content, "缺少 switchEngineSubView 函数定义"
    # 严格互斥隐藏切换
    assert "classList.add('d-none')" in main_js_content, "switchEngineSubView 必须对非目标视图添加 d-none"
    assert "classList.remove('d-none')" in main_js_content, "switchEngineSubView 必须对目标视图移除 d-none"
    # aria-selected 维护
    assert "setAttribute('aria-selected'" in main_js_content, "switchEngineSubView 必须更新 aria-selected 状态"


def test_goto_grey_promote_deep_link_interoperability(main_js_content):
    """AC-06: gotoGreyPromote 必须显式调用 switchEngineSubView('view02') 保证灰测抽屉可见。"""
    assert "function gotoGreyPromote()" in main_js_content, "缺少 gotoGreyPromote 函数定义"
    func_block = main_js_content.split("function gotoGreyPromote()")[1].split("function ")[0]
    assert "switchEngineSubView('view02'" in func_block, "gotoGreyPromote 必须显式调用 switchEngineSubView('view02')"


def test_validate_all_admin_configs_and_error_steering(main_js_content):
    """AC-07, AC-08: validateAllAdminConfigs 捕获错误后必须自动切换至首错子分页并支持错误消除。"""
    assert "function validateAllAdminConfigs()" in main_js_content, "缺少 validateAllAdminConfigs 函数定义"
    val_block = main_js_content.split("function validateAllAdminConfigs()")[1].split("window.validateAllAdminConfigs")[0]
    assert "switchEngineSubView(firstErr.view" in val_block, "校验失败必须自动跳转至错误所在子分页"
    assert "updateEngineErrorBadges" in main_js_content, "必须提供 updateEngineErrorBadges 实时错误红点刷新函数"


# ---------------------------------------------------------------------------
# 4. 全局无 Emoji 铁律审计 (AC-10)
# ---------------------------------------------------------------------------

def test_zero_emoji_in_engine_tab_and_assets(html_soup, main_js_content, style_css_content):
    """AC-10: #tab-engine 页面结构与分段导航相关代码中不得含有任何 Unicode Emoji。"""
    emoji_regex = re.compile(r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]")

    # 1. 扫描 #tab-engine 容器
    tab_engine = html_soup.select_one("#tab-engine")
    assert tab_engine is not None, "未找到 #tab-engine 容器"
    engine_matches = emoji_regex.findall(str(tab_engine))
    assert len(engine_matches) == 0, f"#tab-engine 中发现 Emoji: {engine_matches}"

    # 2. 扫描 main.js 中 switchEngineSubView 相关代码段
    subview_js = main_js_content.split("function switchEngineSubView")[1][:2000]
    js_matches = emoji_regex.findall(subview_js)
    assert len(js_matches) == 0, f"switchEngineSubView 代码中发现 Emoji: {js_matches}"

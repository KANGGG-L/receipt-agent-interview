# -*- coding: utf-8 -*-
"""
QA Automated Regression Test Suite:
Observation Center KPI Trend Modal & Time-Series Drilldown UI Refactor
Validates PRD / Spec requirements (AC-01 through AC-07).
"""
import os
import re
import pytest
from bs4 import BeautifulSoup


@pytest.fixture(scope="module")
def index_html_content():
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def head_css_content():
    # F3 B1 起模板内联 style 块外移到独立样式表
    with open("demo/static/css/index-head.css", "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def main_js_content():
    # F4 S4-11 起 KPI 趋势控制器随域迁至 kpi_trend.js（index.html 加载链中紧跟 main.js）。
    # 守真不变量＝main.js + kpi_trend.js 联合全文（钉「函数在 KPI 域文件」，不再要求在 main.js）。
    parts = []
    for rel in ("demo/static/js/main.js", "demo/static/js/kpi_trend.js"):
        with open(rel, "r", encoding="utf-8") as f:
            parts.append(f.read())
    return "\n".join(parts)


@pytest.fixture(scope="module")
def kpi_modal_soup(index_html_content):
    soup = BeautifulSoup(index_html_content, "html.parser")
    modal = soup.find(id="kpiTrendModal")
    assert modal is not None, "kpiTrendModal element must exist in index.html"
    return modal


def test_ac_01_header_close_button_uniqueness(kpi_modal_soup):
    """
    AC-01: Header 关闭按钮唯一化。
    模态框头部右侧仅存在一个标准的 modal-close-btn (x 按钮)，
    原先并排的冗余文本 [关闭] 按钮已被彻底移除。
    """
    header = kpi_modal_soup.find("div", class_="modal-header")
    assert header is not None, "modal-header not found in kpiTrendModal"

    buttons = header.find_all("button")
    assert len(buttons) == 1, f"Expected exactly 1 button in modal-header, found {len(buttons)}"

    close_btn = buttons[0]
    assert "modal-close-btn" in close_btn.get("class", []), "Header button must have class 'modal-close-btn'"
    assert '"f":"closeKpiTrendModal"' in (close_btn.get("data-csp-click") or ""), "Header button must call closeKpiTrendModal()"
    assert "[关闭]" not in header.get_text(), "Header must not contain redundant text '[关闭]'"


def test_ac_02_no_slogans_or_dev_declarations(index_html_content, main_js_content):
    """
    AC-02: 规则口号与开发声明 100% 清理。
    检查 HTML 与 JS 文件，严禁出现 [规则守护: ...]、(基于 HTML5 内联 SVG 纯原生绘制) 等口号。
    """
    slogans = [
        "[规则守护",
        "规则守护: 0 Emoji",
        "(基于 HTML5 内联 SVG 纯原生绘制)",
        "基于 HTML5 内联 SVG 纯原生绘制",
    ]
    for slogan in slogans:
        assert slogan not in index_html_content, f"Found prohibited slogan in index.html: {slogan}"
        assert slogan not in main_js_content, f"Found prohibited slogan in main.js: {slogan}"

    # 检查图例说明文案已修正为规范的“异常波动点”而非旧的“[异动点]”
    assert "异常波动点" in index_html_content, "Legend label should be '异常波动点'"
    assert "时序走势与异动检测" in index_html_content, "Chart card title should be '时序走势与异动检测'"


def test_ac_03_header_visual_clean_and_dom_compatibility(kpi_modal_soup):
    """
    AC-03: Header 视觉降噪与关键 DOM ID 兼容性留存。
    隐藏内部 debug 键 avg_elapsed_ms 与宽分类 Badge，但完整保留 DOM ID，确保 JS 运行时无报错。
    """
    # 关键 DOM ID 留存验证
    critical_ids = [
        "kpiTrendModalTitle",
        "kpiTrendModalSectionBadge",
        "kpiTrendModalTenantBadge",
        "kpiTrendModalMetricKey",
        "kpiTrendModalUnit",
        "kpiTrendModalPeriodSpan",
        "btnKpiTrend7D",
        "btnKpiTrend14D",
        "btnKpiTrend30D",
        "kpiTrendSummaryAvg",
        "kpiTrendSummaryCurrent",
        "kpiTrendSummaryExtremes",
        "kpiTrendSummaryTrend",
        "kpiTrendChartContainer",
        "kpiTrendChartTooltip",
        "kpiTrendTableContainer",
        "kpiTrendFooterSummary",
    ]
    for cid in critical_ids:
        elem = kpi_modal_soup.find(id=cid)
        assert elem is not None, f"Required DOM element id '{cid}' must be present in kpiTrendModal"

    # 验证面向开发的字段与宽分类 Badge 在 DOM 中初始为隐藏状态 (display:none)
    # F3 B1 起 inline style 迁为 csp-s-N 类（规则体在 demo/static/css/csp-html-styles.css），
    # 等效 style = style 属性 + 元素所带 csp-s-N 类的规则体。
    def _csp_css():
        path = os.path.join(os.path.dirname(__file__), "../demo/static/css/csp-html-styles.css")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def _effective_style(el, csp_css):
        parts = [el.get("style", "") or ""]
        classes = el.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        for cls in classes:
            if cls.startswith("csp-s-"):
                m = re.search(r"\.%s\{([^}]*)\}" % re.escape(cls), csp_css)
                if m:
                    parts.append(m.group(1))
        return ";".join(parts).replace(" ", "")

    csp_css = _csp_css()
    section_badge = kpi_modal_soup.find(id="kpiTrendModalSectionBadge")
    assert "display:none" in _effective_style(section_badge, csp_css), "kpiTrendModalSectionBadge must be hidden"

    metric_key = kpi_modal_soup.find(id="kpiTrendModalMetricKey")
    assert "display:none" in _effective_style(metric_key, csp_css), "kpiTrendModalMetricKey must be hidden"

    tenant_badge = kpi_modal_soup.find(id="kpiTrendModalTenantBadge")
    assert "display:none" in _effective_style(tenant_badge, csp_css), "kpiTrendModalTenantBadge must default to hidden"


def test_ac_04_footer_structure_and_summary_container(kpi_modal_soup):
    """
    AC-01 / Feedback 1 & 2: 底部冗余栏与关闭按钮彻底移除。
    kpiTrendModal 内彻底不存在 modal-footer 容器及底栏关闭按钮。
    """
    footer = kpi_modal_soup.find("div", class_="modal-footer")
    assert footer is None, "modal-footer must be completely removed from kpiTrendModal"

    # 模态框中不存在文本为 [关闭] 的按钮 (唯一关闭收敛至右上角 x)
    buttons = kpi_modal_soup.find_all("button")
    for btn in buttons:
        assert "[关闭]" not in btn.get_text() and btn.get_text().strip() != "关闭", (
            "No [关闭] button should remain in kpiTrendModal, only close button"
        )


def test_ac_05_table_alignment_and_tabular_nums_styling(index_html_content, head_css_content, main_js_content):
    """
    AC-05: 表格对齐与排版规范。
    - 表头与数据单元格中，当日数值、构成样本、较前日环比列显式设置 text-align: right 与 tabular-nums。
    - 日期列为 text-align: left 与 monospace 字体。
    - 健康评估与操作列为 text-align: center。
    - 数据行具备 .kpi-trend-row 类名与 CSS hover 样式。
    """
    # 1. 检查 CSS 中 .kpi-trend-table 及 hover 样式（F3 B1 起规则在外置样式表 index-head.css）
    assert ".kpi-trend-table" in head_css_content, "CSS must define .kpi-trend-table"
    assert ".kpi-trend-row:hover" in head_css_content or "tr.kpi-trend-row:hover" in head_css_content, (
        "CSS must define hover style for .kpi-trend-row"
    )

    # 2. 检查 main.js 中 renderKpiTrendTable 的表头对齐
    fn_idx = main_js_content.find("function renderKpiTrendTable")
    assert fn_idx != -1, "renderKpiTrendTable must be defined in main.js"
    fn_body = main_js_content[fn_idx : fn_idx + 4000]

    assert 'text-align:left;">日期</th>' in fn_body.replace(" ", "")
    assert 'text-align:right;">当日数值</th>' in fn_body.replace(" ", "")
    assert 'text-align:right;">构成样本</th>' in fn_body.replace(" ", "")
    assert 'text-align:right;">较前日环比</th>' in fn_body.replace(" ", "")
    assert 'text-align:center;">健康评估</th>' in fn_body.replace(" ", "")
    assert 'text-align:center;">操作</th>' in fn_body.replace(" ", "")

    # 3. 检查数据单元格渲染中的样式
    assert "kpi-trend-row" in fn_body, "Rendered row must have class 'kpi-trend-row'"
    assert "tabular-nums" in fn_body, "Numeric cells must use font-variant-numeric: tabular-nums"
    assert "text-align:right" in fn_body, "Numeric cells must be right-aligned"
    assert "查看详情" in fn_body, "Action button text should be '查看详情'"


def test_ac_06_drilldown_action_linkage(main_js_content):
    """
    AC-06: 下钻联动功能与历史单据追溯闭环。
    验证 drillDownKpiDate 包含关闭模态框、切换 Tab 至 audit、填充并选中日期筛选器。
    """
    fn_idx = main_js_content.find("function drillDownKpiDate")
    assert fn_idx != -1, "drillDownKpiDate function must exist in main.js"
    fn_body = main_js_content[fn_idx : fn_idx + 1500]

    assert "closeKpiTrendModal()" in fn_body, "drillDownKpiDate must call closeKpiTrendModal()"
    assert "switchTelemetrySubTab('audit')" in fn_body, "drillDownKpiDate must switch tab to 'audit'"
    assert "parsingLogDateRangeSelect" in fn_body, "drillDownKpiDate must link to parsingLogDateRangeSelect"


def test_ac_07_zero_emoji_governance(index_html_content, main_js_content):
    """
    AC-07: 全局无 Emoji 铁律验证 (0 Emoji)。
    在 index.html 模态框区域及 main.js 对应时序下钻代码段中，严格无任何 Unicode Emoji 字符。
    """
    def find_emojis(text: str):
        emojis = []
        for ch in text:
            cp = ord(ch)
            if (
                (0x1F600 <= cp <= 0x1F64F) or  # Emoticons
                (0x1F300 <= cp <= 0x1F5FF) or  # Misc Symbols and Pictographs
                (0x1F680 <= cp <= 0x1F6FF) or  # Transport and Map
                (0x1F1E0 <= cp <= 0x1F1FF) or  # Flags
                (0x2600 <= cp <= 0x26FF) or    # Misc symbols
                (0x2700 <= cp <= 0x27BF) or    # Dingbats
                (0x1F900 <= cp <= 0x1F9FF) or  # Supplemental Symbols and Pictographs
                (0x1FA70 <= cp <= 0x1FAFF)     # Symbols and Pictographs Extended-A
            ):
                emojis.append((ch, hex(cp)))
        return emojis

    # 1. 验证 index.html 中 kpiTrendModal 范围
    m_start = index_html_content.find('id="kpiTrendModal"')
    assert m_start != -1
    m_end = index_html_content.find('id="archiveDetailModal"')
    assert m_end != -1
    modal_slice = index_html_content[m_start:m_end]
    modal_emojis = find_emojis(modal_slice)
    assert len(modal_emojis) == 0, f"Found emoji in kpiTrendModal HTML: {modal_emojis}"

    # 2. 验证 main.js 中 KPI trend 控制器代码段
    j_start = main_js_content.find("// 8 大核心 KPI 时序趋势与多维下钻分析控制器")
    assert j_start != -1
    j_end = main_js_content.find("function drillDownKpiDate") + 2000
    js_slice = main_js_content[j_start:j_end]
    js_emojis = find_emojis(js_slice)
    assert len(js_emojis) == 0, f"Found emoji in KPI trend JS code: {js_emojis}"

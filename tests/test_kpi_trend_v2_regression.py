# -*- coding: utf-8 -*-
"""
V2 Regression Test Suite:
Observation Center KPI Trend Modal & Slice Drilldown UI Refactor (PRD-OBS-KPI-DRILLDOWN-V2-20260930)
Validates all 15 Design Feedback items (AC-01 through AC-11).
"""
import os
import pytest
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def index_html_content():
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
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


# --------------------------------------------------------------------------
# AC-01: 彻底移除底部冗余栏与关闭按钮 (Feedback 1 & 2)
# --------------------------------------------------------------------------
def test_ac_01_footer_and_bottom_close_completely_removed(kpi_modal_soup):
    """
    AC-01:
    1. kpiTrendModal 内彻底不存在 div.modal-footer 容器。
    2. 模态框中无任何 [关闭] 按钮，关闭唯一收敛至右上角 modal-close-btn。
    3. '共 X 天数据切片记录' 文本彻底不在可见 DOM 中。
    """
    footer = kpi_modal_soup.find("div", class_="modal-footer")
    assert footer is None, "modal-footer must be completely removed from kpiTrendModal"

    buttons = kpi_modal_soup.find_all("button")
    for btn in buttons:
        btn_text = btn.get_text().strip()
        assert btn_text != "[关闭]" and btn_text != "关闭", (
            f"Found forbidden close button in kpiTrendModal: {btn_text}"
        )

    # 验证右上角关闭按钮
    close_btns = kpi_modal_soup.find_all("button", class_="modal-close-btn")
    assert len(close_btns) == 1, "Exactly one modal-close-btn should exist"
    assert '"f":"closeKpiTrendModal"' in (close_btns[0].get("data-csp-click") or "")


# --------------------------------------------------------------------------
# AC-02: 快捷周期按钮纯化 (Feedback 4, 5, 6)
# --------------------------------------------------------------------------
def test_ac_02_period_buttons_purified(kpi_modal_soup):
    """
    AC-02:
    按钮文本严格为 '7天', '14天', '30天'，严禁包含 (7D), (14D), (30D)。
    """
    btn7 = kpi_modal_soup.find(id="btnKpiTrend7D")
    btn14 = kpi_modal_soup.find(id="btnKpiTrend14D")
    btn30 = kpi_modal_soup.find(id="btnKpiTrend30D")

    assert btn7 is not None and btn7.get_text().strip() == "7天"
    assert btn14 is not None and btn14.get_text().strip() == "14天"
    assert btn30 is not None and btn30.get_text().strip() == "30天"

    for b in (btn7, btn14, btn30):
        assert "(7D)" not in b.get_text()
        assert "(14D)" not in b.get_text()
        assert "(30D)" not in b.get_text()


# --------------------------------------------------------------------------
# AC-03: 支持自定义日期范围筛选与后端校验 (Feedback 3)
# --------------------------------------------------------------------------
def test_ac_03_custom_date_inputs_and_api(kpi_modal_soup, main_js_content, client):
    """
    AC-03:
    1. 控制栏提供 #kpiTrendStartDate, #kpiTrendEndDate, #btnKpiTrendCustomApply。
    2. JS 包含 applyKpiTrendCustomDate 函数。
    3. 后端 API 支持 start_date 与 end_date 参数，并执行参数边界校验。
    """
    s_input = kpi_modal_soup.find(id="kpiTrendStartDate")
    e_input = kpi_modal_soup.find(id="kpiTrendEndDate")
    apply_btn = kpi_modal_soup.find(id="btnKpiTrendCustomApply")

    assert s_input is not None and s_input.name == "input"
    assert e_input is not None and e_input.name == "input"
    assert apply_btn is not None and '"f":"applyKpiTrendCustomDate"' in (apply_btn.get("data-csp-click") or "")

    assert "function applyKpiTrendCustomDate" in main_js_content

    headers = {"X-Role": "admin"}
    today = datetime.now().date()
    d_start = (today - timedelta(days=5)).strftime("%Y-%m-%d")
    d_end = today.strftime("%Y-%m-%d")

    # 正常自定义查询
    resp = client.get(
        f"/api/analytics/kpi-trends?metric_key=total_parses&start_date={d_start}&end_date={d_end}",
        headers=headers,
    )
    assert resp.status_code == 200
    res_data = resp.json()["data"]
    assert len(res_data["series"]) == 6
    assert res_data["date_range"]["start_date"] == d_start
    assert res_data["date_range"]["end_date"] == d_end

    # 重复路由 /api/obs/kpi-trend 已删除，只保留 /api/analytics/kpi-trends
    resp_alias = client.get(
        f"/api/obs/kpi-trend?metric_key=total_parses&start_date={d_start}&end_date={d_end}",
        headers=headers,
    )
    assert resp_alias.status_code == 404

    # 边界校验：仅传单个日期
    resp_bad1 = client.get(
        f"/api/analytics/kpi-trends?metric_key=total_parses&start_date={d_start}",
        headers=headers,
    )
    assert resp_bad1.status_code == 400

    # 边界校验：start_date > end_date
    resp_bad2 = client.get(
        f"/api/analytics/kpi-trends?metric_key=total_parses&start_date={d_end}&end_date={d_start}",
        headers=headers,
    )
    assert resp_bad2.status_code == 400

    # 边界校验：跨度超过 90 天
    d_old = (today - timedelta(days=95)).strftime("%Y-%m-%d")
    resp_bad3 = client.get(
        f"/api/analytics/kpi-trends?metric_key=total_parses&start_date={d_old}&end_date={d_end}",
        headers=headers,
    )
    assert resp_bad3.status_code == 400


# --------------------------------------------------------------------------
# AC-04: Header 视觉降噪 (Feedback 7 & 8)
# --------------------------------------------------------------------------
def test_ac_04_header_visual_clean(kpi_modal_soup):
    """
    AC-04:
    Header 中严禁出现可见的 '统计单位:' 和 '当前周期:' 文本。
    """
    header = kpi_modal_soup.find("div", class_="modal-header")
    assert header is not None
    text = header.get_text()
    assert "统计单位:" not in text
    assert "当前周期:" not in text

    # DOM 占位兼容（F3 B1 起 inline style 迁为 csp-s-N 类，等效 style 含类规则体）
    import re as _re
    csp_css_path = os.path.join(os.path.dirname(__file__), "../demo/static/css/csp-html-styles.css")
    with open(csp_css_path, encoding="utf-8") as f:
        csp_css = f.read()

    def _effective_style(el):
        parts = [el.get("style", "") or ""]
        classes = el.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        for cls in classes:
            if cls.startswith("csp-s-"):
                m = _re.search(r"\.%s\{([^}]*)\}" % _re.escape(cls), csp_css)
                if m:
                    parts.append(m.group(1))
        return ";".join(parts).replace(" ", "")

    unit_el = kpi_modal_soup.find(id="kpiTrendModalUnit")
    span_el = kpi_modal_soup.find(id="kpiTrendModalPeriodSpan")
    assert unit_el is not None
    assert "display:none" in _effective_style(unit_el)
    assert span_el is not None
    assert "display:none" in _effective_style(span_el)


# --------------------------------------------------------------------------
# AC-05, AC-06, AC-07: 标题与操作按钮文案规范 (Feedback 9, 10, 13, 14)
# --------------------------------------------------------------------------
def test_ac_05_06_07_titles_and_action_button(kpi_modal_soup, main_js_content):
    """
    AC-05: 模态框标题为 ${metric_name}趋势，副标题为 切片明细
    AC-06: 移除 '点击行尾钻取单据联动审计看板' 提示
    AC-07: 行尾操作按钮为 '查看详情'，且功能闭环
    """
    # 模态框初始标题
    title_el = kpi_modal_soup.find(id="kpiTrendModalTitle")
    assert title_el is not None
    assert "·" not in title_el.get_text()
    assert "趋势与切片分析" not in title_el.get_text()
    assert "时序趋势与多维下钻" not in title_el.get_text()

    # JS 中动态渲染标题
    # 2026-10-10 契约对齐：标题简化为 metric_name 本身（后端 metric_name 已含
    # 「趋势」语义，main.js:20147 注释明言「简化为 ${metric_name}趋势」）
    assert "titleEl.textContent = data.metric_name;" in main_js_content

    # 卡片副标题为 '切片明细'
    body_text = kpi_modal_soup.find("div", class_="modal-body").get_text()
    assert "切片明细" in body_text
    assert "每日数据切片明细" not in body_text

    # 移除行尾提示
    assert "点击行尾钻取单据联动审计看板" not in kpi_modal_soup.get_text()

    # 操作按钮为 '查看详情'
    assert "查看详情" in main_js_content
    assert "[查看当日单据详情]" in main_js_content
    assert "drillDownKpiDate" in main_js_content


# --------------------------------------------------------------------------
# AC-08 & AC-09: 智能数值格式化与 0.0 单消除 (Feedback 11 & 12)
# --------------------------------------------------------------------------
def test_ac_08_09_smart_number_formatting(client):
    """
    AC-08 & AC-09:
    1. 整数指标 (total_parses, avg_tokens) 的 formatted_value 必须为整数字符串，禁止出现 0.0 或带小数。
    2. formatted_value 严禁包含中文单位 '单'。
    3. 比率指标包含 %。
    """
    headers = {"X-Role": "admin"}

    # total_parses (整数指标)
    resp = client.get("/api/analytics/kpi-trends?metric_key=total_parses&days=7", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["is_integer"] is True
    assert data["is_rate"] is False

    for rec in data["daily_records"]:
        f_val = rec["formatted_value"]
        assert "单" not in f_val, f"formatted_value should not contain '单': {f_val}"
        assert ".0" not in f_val, f"Integer metric formatted_value should not contain '.0': {f_val}"
        assert f_val.isdigit() or f_val == "0", f"formatted_value should be pure integer digits: {f_val}"

    # avg_tokens (整数指标)
    resp_tokens = client.get("/api/analytics/kpi-trends?metric_key=avg_tokens&days=7", headers=headers)
    assert resp_tokens.status_code == 200
    data_tokens = resp_tokens.json()["data"]
    assert data_tokens["is_integer"] is True
    for rec in data_tokens["daily_records"]:
        assert ".0" not in rec["formatted_value"]

    # success_rate (比率指标)
    resp_rate = client.get("/api/analytics/kpi-trends?metric_key=success_rate&days=7", headers=headers)
    assert resp_rate.status_code == 200
    data_rate = resp_rate.json()["data"]
    assert data_rate["is_rate"] is True
    for rec in data_rate["daily_records"]:
        assert "%" in rec["formatted_value"]


# --------------------------------------------------------------------------
# AC-10: N+1 预取基线日与较前日环比全覆盖 (Feedback 15)
# --------------------------------------------------------------------------
def test_ac_10_pop_continuity_and_zero_matrix(client):
    """
    AC-10:
    1. 首日具备有效基线，prev_diff_pct 不为 None（除非是无样本的比率指标）。
    2. 非比率指标（如 total_parses）中，0 波动被正确计算（如 0.0% 或 -100.0%）。
    """
    headers = {"X-Role": "admin"}
    resp = client.get("/api/analytics/kpi-trends?metric_key=total_parses&days=7", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    series = data["series"]

    # 首日 (series[0]) 必定存在 prev_diff_pct，不为 None
    first_day = series[0]
    assert first_day["prev_diff_pct"] is not None, "First day must have valid PoP from N+1 baseline"
    assert isinstance(first_day["prev_diff_pct"], (int, float))

    # 全量切片记录均有明确数值
    for item in series:
        assert item["prev_diff_pct"] is not None
        assert isinstance(item["prev_diff_pct"], (int, float))


# --------------------------------------------------------------------------
# AC-11: 全局 0 Emoji 规范
# --------------------------------------------------------------------------
def test_ac_11_zero_emoji(index_html_content, main_js_content):
    """
    AC-11: 全局 0 Emoji 铁律验证。
    """
    def find_emojis(text: str):
        emojis = []
        for ch in text:
            cp = ord(ch)
            if (
                (0x1F600 <= cp <= 0x1F64F) or
                (0x1F300 <= cp <= 0x1F5FF) or
                (0x1F680 <= cp <= 0x1F6FF) or
                (0x1F1E0 <= cp <= 0x1F1FF) or
                (0x2600 <= cp <= 0x26FF) or
                (0x2700 <= cp <= 0x27BF) or
                (0x1F900 <= cp <= 0x1F9FF) or
                (0x1FA70 <= cp <= 0x1FAFF)
            ):
                emojis.append((ch, hex(cp)))
        return emojis

    m_start = index_html_content.find('id="kpiTrendModal"')
    m_end = index_html_content.find('id="archiveDetailModal"')
    modal_slice = index_html_content[m_start:m_end]
    assert len(find_emojis(modal_slice)) == 0

    j_start = main_js_content.find("window.currentKpiTrendState")
    j_end = main_js_content.find("function drillDownKpiDate") + 2000
    js_slice = main_js_content[j_start:j_end]
    assert len(find_emojis(js_slice)) == 0

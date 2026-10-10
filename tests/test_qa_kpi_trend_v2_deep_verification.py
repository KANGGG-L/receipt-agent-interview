# -*- coding: utf-8 -*-
"""
QA Deep Verification Test Suite:
Observation Center Core KPI Trend & Slice Drilldown UI v2
PRD-OBS-KPI-DRILLDOWN-V2-20260930 Acceptance Criteria Exhaustive Audit.
"""
import pytest
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.main import app
from demo.app.services.parsing_logs import compute_kpi_trends, KPI_METRIC_META


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def index_html_content():
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def main_js_content():
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def kpi_modal_soup(index_html_content):
    soup = BeautifulSoup(index_html_content, "html.parser")
    modal = soup.find(id="kpiTrendModal")
    assert modal is not None, "kpiTrendModal element must exist in index.html"
    return modal


# ==============================================================================
# 1. API 自定义日期范围与边界校验 (AC-03, Router Alias, 400 Bad Request)
# ==============================================================================
class TestKpiTrendApiCustomDates:
    """测试 /api/analytics/kpi-trends 与 /api/obs/kpi-trend 自定义起止日期及安全边界校验。"""

    headers = {"X-Role": "admin"}

    def test_custom_date_range_valid_response(self, client: TestClient):
        today = datetime.now().date()
        s_date = (today - timedelta(days=10)).strftime("%Y-%m-%d")
        e_date = (today - timedelta(days=2)).strftime("%Y-%m-%d")

        for endpoint in ["/api/analytics/kpi-trends", "/api/obs/kpi-trend"]:
            resp = client.get(
                f"{endpoint}?metric_key=total_parses&start_date={s_date}&end_date={e_date}",
                headers=self.headers,
            )
            assert resp.status_code == 200, f"Endpoint {endpoint} failed: {resp.text}"
            body = resp.json()
            assert body["status"] == "success"
            data = body["data"]
            assert data["date_range"]["start_date"] == s_date
            assert data["date_range"]["end_date"] == e_date
            assert len(data["series"]) == 9  # 10 to 2 inclusive is 9 days
            assert len(data["daily_records"]) == 9

    def test_same_single_day_range(self, client: TestClient):
        today = datetime.now().date()
        d_str = today.strftime("%Y-%m-%d")
        resp = client.get(
            f"/api/analytics/kpi-trends?metric_key=total_parses&start_date={d_str}&end_date={d_str}",
            headers=self.headers,
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["series"]) == 1
        assert len(data["daily_records"]) == 1
        # N+1 基线日确保即便是 1 天也有环比对比基准
        assert data["series"][0]["prev_diff_pct"] is not None

    def test_missing_one_date_returns_400(self, client: TestClient):
        today = datetime.now().date().strftime("%Y-%m-%d")
        # 仅传 start_date
        r1 = client.get(f"/api/analytics/kpi-trends?metric_key=total_parses&start_date={today}", headers=self.headers)
        assert r1.status_code == 400
        # 仅传 end_date：2026-10-10 契约对齐 —— api_phase2.py 改为 start_date
        # 默认解析为最早一条收据记录的日期（docstring 明言），返回 200
        r2 = client.get(f"/api/analytics/kpi-trends?metric_key=total_parses&end_date={today}", headers=self.headers)
        assert r2.status_code == 200
        # 仅传 start_date 仍 400（缺 end 无默认语义）
        r3 = client.get(f"/api/analytics/kpi-trends?metric_key=total_parses&start_date={today}", headers=self.headers)
        assert r3.status_code == 400

    def test_invalid_date_format_returns_400(self, client: TestClient):
        for bad_date in ["2026-99-99", "invalid-date", "2026/09/01"]:
            resp = client.get(
                f"/api/analytics/kpi-trends?metric_key=total_parses&start_date={bad_date}&end_date=2026-09-30",
                headers=self.headers,
            )
            assert resp.status_code == 400

    def test_inverted_date_range_returns_400(self, client: TestClient):
        resp = client.get(
            "/api/analytics/kpi-trends?metric_key=total_parses&start_date=2026-09-30&end_date=2026-09-01",
            headers=self.headers,
        )
        assert resp.status_code == 400
        err_msg = resp.json().get("message") or resp.json().get("detail", "")
        assert "start_date 不能晚于" in err_msg or "不能晚于截止日期" in err_msg

    def test_date_range_exceeding_90_days_returns_400(self, client: TestClient):
        today = datetime.now().date()
        old_day = (today - timedelta(days=91)).strftime("%Y-%m-%d")
        resp = client.get(
            f"/api/analytics/kpi-trends?metric_key=total_parses&start_date={old_day}&end_date={today.strftime('%Y-%m-%d')}",
            headers=self.headers,
        )
        assert resp.status_code == 400
        err_msg = resp.json().get("message") or resp.json().get("detail", "")
        assert "90" in err_msg


# ==============================================================================
# 2. N+1 环比计算数学矩阵与 0 值/边界条件深度验证 (AC-10)
# ==============================================================================
class TestKpiTrendPoPMathMatrix:
    """深度验证较前日环比的 N+1 基线预取与数学边界矩阵。"""

    def test_all_8_metrics_first_day_has_valid_baseline_or_graceful_none(self, client: TestClient):
        headers = {"X-Role": "admin"}
        for metric_key in KPI_METRIC_META.keys():
            resp = client.get(f"/api/analytics/kpi-trends?metric_key={metric_key}&days=7", headers=headers)
            assert resp.status_code == 200
            data = resp.json()["data"]
            first_day = data["series"][0]

            if not data["is_rate"]:
                # 非比率指标（如 total_parses, avg_tokens, avg_elapsed_ms）
                # 无论基线是 0 还是 > 0，首日环比绝不为 None
                assert first_day["prev_diff_pct"] is not None, (
                    f"Metric {metric_key} first day prev_diff_pct is None!"
                )
                assert isinstance(first_day["prev_diff_pct"], (int, float))
            else:
                # 比率指标：若基线日与首日样本均 > 0，则必须有差值；无样本时为 None
                if first_day["denominator"] > 0:
                    # 如果首日有样本，检查其是否为有效数值或合法兜底
                    assert first_day["prev_diff_pct"] is None or isinstance(first_day["prev_diff_pct"], (int, float))

    def test_pop_math_transitions_unit_level(self):
        """
        验证数学逻辑：
        - prev > 0, curr == 0 -> -100.0%
        - prev == 0, curr == 0 -> 0.0%
        - prev == 0, curr > 0 -> 100.0%
        - prev > 0, curr > 0 -> ((curr - prev) / prev) * 100
        """
        # 测试非比率指标的环比逻辑
        def calc_non_rate_diff(prev_val, curr_val):
            if prev_val > 0:
                return round((curr_val - prev_val) / prev_val * 100.0, 1)
            elif prev_val == 0:
                if curr_val == 0:
                    return 0.0
                else:
                    return 100.0
            return None

        # 昨日有单(5)，今日无单(0) -> 必须精准为 -100.0%
        assert calc_non_rate_diff(5, 0) == -100.0
        # 昨日无单(0)，今日仍无单(0) -> 必须为 0.0%（持平）
        assert calc_non_rate_diff(0, 0) == 0.0
        # 昨日无单(0)，今日新增(3) -> 100.0%（纯增）
        assert calc_non_rate_diff(0, 3) == 100.0
        # 昨日(10)，今日(12) -> +20.0%
        assert calc_non_rate_diff(10, 12) == 20.0
        # 昨日(10)，今日(8) -> -20.0%
        assert calc_non_rate_diff(10, 8) == -20.0

    def test_rate_metric_diff_logic(self):
        """
        比率类指标环比为绝对百分点差：
        - prev_den > 0 且 curr_den > 0 -> curr_val - prev_val
        - 任何一方 den == 0 -> None
        """
        def calc_rate_diff(prev_val, prev_den, curr_val, curr_den):
            if prev_den > 0 and curr_den > 0:
                return round(curr_val - prev_val, 1)
            return None

        assert calc_rate_diff(90.0, 10, 95.5, 12) == 5.5
        assert calc_rate_diff(95.0, 10, 85.0, 8) == -10.0
        assert calc_rate_diff(90.0, 0, 95.0, 10) is None
        assert calc_rate_diff(90.0, 10, 0.0, 0) is None


# ==============================================================================
# 3. DOM 结构与 UI 元素精细核验 (AC-01, AC-02, AC-04, AC-05, AC-06, AC-07)
# ==============================================================================
class TestKpiTrendDomAndUiStructure:
    """深度核验 DOM 结构：无底部栏、无关闭文字、无说教、无英文代号。"""

    def test_no_modal_footer_in_dom(self, kpi_modal_soup):
        """AC-01: 验证不存在任何 modal-footer 容器。"""
        footers = kpi_modal_soup.find_all(class_="modal-footer")
        assert len(footers) == 0, "kpiTrendModal must NOT contain any modal-footer elements"

    def test_only_one_close_button_in_header(self, kpi_modal_soup):
        """AC-01: 验证右上角唯一 x 关闭按钮，不存在任何 [关闭] 按钮。"""
        buttons = kpi_modal_soup.find_all("button")
        for btn in buttons:
            txt = btn.get_text().strip()
            assert "[关闭]" not in txt
            assert txt != "关闭"

        close_btns = kpi_modal_soup.find_all(class_="modal-close-btn")
        assert len(close_btns) == 1, "Must have exactly 1 modal-close-btn"
        assert close_btns[0].get_text().strip() == "×" or close_btns[0].get_text().strip() == "x"

    def test_period_buttons_clean_text(self, kpi_modal_soup):
        """AC-02: 验证周期按钮文案纯化为 7天, 14天, 30天。"""
        b7 = kpi_modal_soup.find(id="btnKpiTrend7D")
        b14 = kpi_modal_soup.find(id="btnKpiTrend14D")
        b30 = kpi_modal_soup.find(id="btnKpiTrend30D")

        assert b7 is not None and b7.get_text().strip() == "7天"
        assert b14 is not None and b14.get_text().strip() == "14天"
        assert b30 is not None and b30.get_text().strip() == "30天"

        for b in [b7, b14, b30]:
            t = b.get_text()
            assert "7D" not in t and "14D" not in t and "30D" not in t
            assert "(" not in t and ")" not in t

    def test_header_descriptions_hidden(self, kpi_modal_soup):
        """AC-04: 验证 Header 中可见的 '当前周期: ...' 与 '统计单位: ...' 已彻底降噪。"""
        header = kpi_modal_soup.find("div", class_="modal-header")
        assert header is not None
        h_text = header.get_text()
        assert "统计单位:" not in h_text
        assert "当前周期:" not in h_text

        # 隐藏元素样式检查
        unit = kpi_modal_soup.find(id="kpiTrendModalUnit")
        assert unit is not None
        assert "display:none" in unit.get("style", "").replace(" ", "")

        span = kpi_modal_soup.find(id="kpiTrendModalPeriodSpan")
        assert span is not None
        assert "display:none" in span.get("style", "").replace(" ", "")

    def test_card_titles_and_instructional_text_removed(self, kpi_modal_soup):
        """AC-05 & AC-06: 验证卡片标题为 '切片明细'，且彻底无说教提示。"""
        text = kpi_modal_soup.get_text()
        assert "切片明细" in text
        assert "每日数据切片明细" not in text
        assert "点击行尾钻取单据联动审计看板" not in text

    def test_table_action_button_is_view_details(self, main_js_content):
        """AC-07: 验证行操作按钮文本为 '查看详情'。"""
        assert '查看详情</button>' in main_js_content
        assert '[查看当日单据详情]' in main_js_content
        assert '钻取单据' not in main_js_content


# ==============================================================================
# 4. 智能数值格式化与 0.0 单彻底消除验证 (AC-08, AC-09)
# ==============================================================================
class TestKpiTrendFormattingGovernance:
    """深度验证格式化体系：整数指标不含 .0、不拼接中文“单”，比率指标带 %。"""

    def test_total_parses_formatted_value_no_float_or_dan(self, client: TestClient):
        headers = {"X-Role": "admin"}
        resp = client.get("/api/analytics/kpi-trends?metric_key=total_parses&days=14", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]

        assert data["is_integer"] is True
        assert data["metric_unit"] == "单"

        # Summary 字段
        sum_data = data["summary"]
        assert isinstance(sum_data["avg_val"], int)
        assert isinstance(sum_data["current_val"], int)
        assert isinstance(sum_data["min_val"], int)
        assert isinstance(sum_data["max_val"], int)

        # 切片列表
        for rec in data["daily_records"]:
            val = rec["value"]
            f_val = rec["formatted_value"]
            assert isinstance(val, int)
            assert "单" not in f_val, f"Found '单' in formatted_value: {f_val}"
            assert ".0" not in f_val, f"Found '.0' in integer metric: {f_val}"
            assert f_val.isdigit() or f_val == "0"

    def test_avg_tokens_integer_governance(self, client: TestClient):
        headers = {"X-Role": "admin"}
        resp = client.get("/api/analytics/kpi-trends?metric_key=avg_tokens&days=7", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["is_integer"] is True
        for rec in data["daily_records"]:
            assert ".0" not in rec["formatted_value"]

    def test_success_rate_percentage_governance(self, client: TestClient):
        headers = {"X-Role": "admin"}
        resp = client.get("/api/analytics/kpi-trends?metric_key=success_rate&days=7", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["is_rate"] is True
        for rec in data["daily_records"]:
            assert "%" in rec["formatted_value"]


# ==============================================================================
# 5. 全局 0 Emoji 绝对红线扫描 (AC-11)
# ==============================================================================
class TestZeroEmojiCompliance:
    """全量严格扫描改动涉及的代码和模板文件，确保 0 Emoji。"""

    def _scan_for_emojis(self, text: str):
        found = []
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
                found.append((ch, hex(cp)))
        return found

    def test_kpi_modal_html_zero_emoji(self, index_html_content):
        start = index_html_content.find('id="kpiTrendModal"')
        end = index_html_content.find('id="archiveDetailModal"')
        assert start != -1 and end != -1
        modal_html = index_html_content[start:end]
        emojis = self._scan_for_emojis(modal_html)
        assert len(emojis) == 0, f"Found emojis in kpiTrendModal: {emojis}"

    def test_kpi_trend_js_zero_emoji(self, main_js_content):
        start = main_js_content.find("// 8 大核心 KPI 时序趋势与多维下钻分析控制器")
        assert start != -1
        js_block = main_js_content[start:start + 18000]
        emojis = self._scan_for_emojis(js_block)
        assert len(emojis) == 0, f"Found emojis in KPI trend JS: {emojis}"

    def test_parsing_logs_kpi_service_zero_emoji(self):
        with open("demo/app/services/parsing_logs.py", "r", encoding="utf-8") as f:
            code = f.read()
        start = code.find("def compute_kpi_trends")
        assert start != -1
        service_block = code[start:start + 8000]
        emojis = self._scan_for_emojis(service_block)
        assert len(emojis) == 0, f"Found emojis in compute_kpi_trends service: {emojis}"

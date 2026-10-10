# -*- coding: utf-8 -*-
"""
Tests for 8 Core KPI Trends & Time-Series Drilldown (PRD-TELEMETRY-KPI-TREND-001)
Validates AC-01 through AC-11.
"""
import pytest
from fastapi.testclient import TestClient


ALL_8_METRIC_KEYS = [
    "total_parses",
    "success_rate",
    "avg_elapsed_ms",
    "avg_tokens",
    "positive_rate",
    "edit_rate",
    "recovery_entry_rate",
    "recovery_success_rate",
]


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_ac_04_backend_api_all_8_metrics(client: TestClient):
    """
    AC-04: 后端 /api/analytics/kpi-trends 接口支持全部 8 大指标，
    返回符合规范的数据结构 (summary, series, daily_records)。
    """
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}

    for m_key in ALL_8_METRIC_KEYS:
        resp = client.get(f"/api/analytics/kpi-trends?metric_key={m_key}&days=7&tenant_id=default", headers=headers)
        assert resp.status_code == 200, f"指标 {m_key} 请求失败: {resp.text}"
        data = resp.json().get("data", {})
        assert data.get("metric_key") == m_key
        assert data.get("period_days") == 7
        assert "summary" in data
        assert "series" in data
        assert "daily_records" in data

        summary = data["summary"]
        for field in ("current_val", "avg_val", "min_val", "max_val", "change_rate_pct", "trend_direction"):
            assert field in summary, f"{m_key} summary 缺少字段 {field}"

        series = data["series"]
        assert len(series) == 7, f"{m_key} 7天切片长度不为 7"
        for item in series:
            assert "date" in item
            assert "value" in item
            assert "numerator" in item
            assert "denominator" in item
            assert "is_anomaly" in item

        daily = data["daily_records"]
        assert len(daily) == 7
        for item in daily:
            assert "formatted_value" in item
            assert "health_status" in item


def test_ac_04_backend_api_periods_and_validation(client: TestClient):
    """
    AC-04 & AC-05: 验证 7D, 14D, 30D 周期及非法指标校验。
    """
    headers = {"X-Role": "admin"}

    # 14 天
    resp14 = client.get("/api/analytics/kpi-trends?metric_key=total_parses&days=14", headers=headers)
    assert resp14.status_code == 200
    assert len(resp14.json()["data"]["series"]) == 14

    # 30 天
    resp30 = client.get("/api/analytics/kpi-trends?metric_key=success_rate&days=30", headers=headers)
    assert resp30.status_code == 200
    assert len(resp30.json()["data"]["series"]) == 30

    # 非法指标参数返回 400
    resp_err = client.get("/api/analytics/kpi-trends?metric_key=invalid_kpi_foo", headers=headers)
    assert resp_err.status_code == 400
    assert resp_err.json().get("status") == "error"

    # 非管理员拦截
    resp_guest = client.get("/api/analytics/kpi-trends?metric_key=total_parses", headers={"X-Role": "clerk"})
    assert resp_guest.status_code in (401, 403)


def test_ac_01_02_card_affordance_and_modal_dom():
    """
    AC-01 & AC-02 & AC-03: 静态检查 index.html 中 8 张卡片与模态框 DOM 结构。
    """
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # 8 张卡片均包含 onclick="openKpiTrendModal(...)"
    for m_key in ALL_8_METRIC_KEYS:
        assert f"openKpiTrendModal('{m_key}')" in html, f"卡片缺少对 {m_key} 的 openKpiTrendModal 绑定"

    # 8 张卡片均包含类名 kpi-trend-card
    assert html.count("kpi-trend-card") >= 8
    # 2026-10-10 契约对齐：趋势入口由纯文本微标 [查看趋势] 演化为整卡可点
    # （_analyticsStatHtml 生成 role="button" + aria-haspopup + 键盘激活，a11y 轮产物）
    assert 'openKpiTrendModal' in html
    assert 'aria-haspopup="dialog"' in html
    assert 'kpi-trend-card' in html

    # 模态框与关键容器 ID
    for el_id in (
        "kpiTrendModal",
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
    ):
        assert f'id="{el_id}"' in html, f"index.html 缺少关键元素 ID: {el_id}"


def test_ac_06_to_11_js_functions_and_pure_native_svg():
    """
    AC-06 到 AC-11: 检查 main.js 中函数定义、原生 SVG、下钻联动与无 Emoji 规范。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    # 控制器函数
    for fn in (
        "openKpiTrendModal",
        "closeKpiTrendModal",
        "switchKpiTrendDays",
        "loadKpiTrendData",
        "renderKpiTrendChart",
        "renderKpiTrendTable",
        "drillDownKpiDate",
        "showKpiTrendTooltip",
        "hideKpiTrendTooltip",
    ):
        assert f"function {fn}" in js, f"main.js 缺少核心函数 {fn}"

    # 原生 SVG 绘制
    assert "<svg" in js
    assert "linearGradient" in js
    assert "stroke-dasharray" in js
    assert "kpiTrendChartTooltip" in js

    # 钻取联动单据与详情查看
    assert "[查看当日单据详情]" in js
    assert "查看详情" in js
    assert "drillDownKpiDate" in js

    # 严禁任何 Emoji（禁止 unicode 表情范围）
    import re
    # 纯 ASCII / 常见中文测试，确保无表情符
    emoji_chars = [chr(c) for c in range(0x1F600, 0x1F64F)]
    start_idx = js.find("// 8 大核心 KPI 时序趋势与多维下钻分析控制器")
    assert start_idx != -1
    trend_js_block = js[start_idx:start_idx + 15000]
    for em in emoji_chars:
        assert em not in trend_js_block, "趋势控制器代码中违规残留 Emoji"

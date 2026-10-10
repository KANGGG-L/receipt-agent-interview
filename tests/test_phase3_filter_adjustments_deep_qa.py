# -*- coding: utf-8 -*-
"""
Phase 3 QA Deep Verification Test Suite for Observation Center Filter Adjustments & Time-Range Governance.
Covers AC-FILTER-01 through AC-FILTER-07 with deep boundary, edge-case, and contract verification:
- [AC-FILTER-01] DOM decoupling & elimination of collapsed shared filter bar.
- [AC-FILTER-02] Self-contained filter bar in Tab A with period buttons & compatible parsingLogDateRangeSelect.
- [AC-FILTER-03] Dedicated filter bar in Tab B with tenant, periods, categories, and keyword search.
- [AC-FILTER-04] Truthfulness verification for /api/analytics/recovery-summary across all date ranges, custom ranges, and empty states.
- [AC-FILTER-05] Client-side category classification, alias mapping, and instant search filtering.
- [AC-FILTER-06] Forward/backward regression contract compatibility.
- [AC-FILTER-07] Strict 0-emoji compliance scan across modified and related codebase.
"""
import os
import re
import urllib.request
import json
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def index_html_content():
    path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def main_js_content():
    path = os.path.join(os.path.dirname(__file__), "../demo/static/js/main.js")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# =============================================================================
# AC-FILTER-01: DOM 解耦与无坍缩验证
# =============================================================================

def test_qa_ac01_dom_decoupling_and_no_collapse(index_html_content, main_js_content):
    """
    [AC-FILTER-01] 校验 #sec-telemetry 中顶部旧 Card 1 筛选栏已彻底移除：
    1. 8 大 KPI 卡片与 .analytics-sub-tabs 之间不存在任何 card 筛选栏。
    2. switchTelemetrySubTab 函数彻底移除了 auditOnlyFilterIds 遍历修改 display: none 逻辑。
    """
    sec_idx = index_html_content.find('id="sec-telemetry"')
    assert sec_idx != -1, "[FAIL] id='sec-telemetry' not found in index.html"

    sub_tabs_idx = index_html_content.find('class="analytics-sub-tabs"', sec_idx)
    assert sub_tabs_idx != -1, "[FAIL] class='analytics-sub-tabs' not found in sec-telemetry"

    # 截取 KPI 区块和 Sub Tabs 之间的中间 DOM 片段
    mid_section = index_html_content[sec_idx:sub_tabs_idx]
    assert 'class="parsing-log-filter-control"' not in mid_section, (
        "[FAIL] Shared filter controls found above sub tabs"
    )
    assert 'id="parsingLogStatusSelect"' not in mid_section, (
        "[FAIL] parsingLogStatusSelect found above sub tabs"
    )

    # 检查 main.js 中已清除 auditOnlyFilterIds
    assert "auditOnlyFilterIds" not in main_js_content, (
        "[FAIL] auditOnlyFilterIds still exists in main.js"
    )

    # 检查 switchTelemetrySubTab 只切换 display block/none
    fn_idx = main_js_content.find("function switchTelemetrySubTab")
    assert fn_idx != -1, "[FAIL] switchTelemetrySubTab function not found"
    fn_body = main_js_content[fn_idx:fn_idx + 1800]
    assert "telemetrySubContentAudit" in fn_body
    assert "telemetrySubContentEvents" in fn_body
    assert "style.display = isAudit ? 'block' : 'none'" in fn_body


# =============================================================================
# AC-FILTER-02: Tab A 卡片自内聚筛选器与时间跨度双向联动
# =============================================================================

def test_qa_ac02_tab_a_dedicated_filter_and_bidirectional_sync(index_html_content, main_js_content):
    """
    [AC-FILTER-02] 单据解析流水明细卡片内聚时间跨度及完整筛选器：
    1. 包含 5 个预设周期按钮 (all/24h/7d/14d/30d) 与自定义起止日期。
    2. 包含兼容契约的 parsingLogDateRangeSelect 下拉选择器。
    3. main.js 实现了 setParsingLogPeriod、applyParsingLogCustomPeriod 及双向联动。
    """
    tab_a_idx = index_html_content.find('id="telemetrySubContentAudit"')
    assert tab_a_idx != -1, "[FAIL] telemetrySubContentAudit not found"
    tab_b_idx = index_html_content.find('id="telemetrySubContentEvents"')
    assert tab_b_idx != -1, "[FAIL] telemetrySubContentEvents not found"

    tab_a_slice = index_html_content[tab_a_idx:tab_b_idx]

    # 必须包含在 table 之前
    tbl_idx = tab_a_slice.find('id="parsingLogsTable"')
    assert tbl_idx != -1, "[FAIL] parsingLogsTable not found in Tab A"
    filter_slice = tab_a_slice[:tbl_idx]

    required_controls = [
        'id="analyticsTenantSelect"',
        'id="parsingLogStatusSelect"',
        'id="parsingLogGateSelect"',
        'id="parsingLogFeedbackSelect"',
        'id="parsingLogModifiedSelect"',
        'id="btnLogRangeAll"',
        'id="btnLogRange24h"',
        'id="btnLogRange7d"',
        'id="btnLogRange14d"',
        'id="btnLogRange30d"',
        'id="parsingLogStartDate"',
        'id="parsingLogEndDate"',
        'id="parsingLogDateRangeSelect"',
        'id="parsingLogSearchInput"',
    ]
    for ctrl in required_controls:
        assert ctrl in filter_slice, f"[FAIL] Tab A missing filter control: {ctrl}"

    # 检查 main.js 中函数及状态同步
    assert "function setParsingLogPeriod" in main_js_content
    assert "function applyParsingLogCustomPeriod" in main_js_content
    assert "function onParsingLogDateRangeSelectChange" in main_js_content

    fn_set_idx = main_js_content.find("function setParsingLogPeriod")
    fn_set_body = main_js_content[fn_set_idx:fn_set_idx + 800]
    assert "parsingLogDateRangeSelect" in fn_set_body, (
        "[FAIL] setParsingLogPeriod must sync with parsingLogDateRangeSelect"
    )
    assert "_updateParsingLogPeriodButtons()" in fn_set_body


# =============================================================================
# AC-FILTER-03: Tab B 卡片专属筛选栏与控制项完备性
# =============================================================================

def test_qa_ac03_tab_b_dedicated_filter_bar_completeness(index_html_content, main_js_content):
    """
    [AC-FILTER-03] 全量埋点事件分布卡片专属筛选控制栏：
    1. 包含租户、时间跨度 (全部/24h/7d/14d/30d 及自定义起止)。
    2. 包含事件类别下拉框 (all, lifecycle, recovery, governance, __other__)。
    3. 包含事件搜索输入框、[查询/刷新]、[重置] 按钮。
    """
    tab_b_idx = index_html_content.find('id="telemetrySubContentEvents"')
    assert tab_b_idx != -1, "[FAIL] telemetrySubContentEvents not found"
    dist_body_idx = index_html_content.find('id="analyticsEventDistBody"', tab_b_idx)
    assert dist_body_idx != -1, "[FAIL] analyticsEventDistBody not found in Tab B"

    tab_b_filter_slice = index_html_content[tab_b_idx:dist_body_idx]

    expected_elements = [
        'id="eventsFilterTenant"',
        'id="btnEventsRangeAll"',
        'id="btnEventsRange24h"',
        'id="btnEventsRange7d"',
        'id="btnEventsRange14d"',
        'id="btnEventsRange30d"',
        'id="eventsStartDate"',
        'id="eventsEndDate"',
        'id="eventsCategorySelect"',
        'id="eventsSearchInput"',
        'onclick="loadRecoverySummaryBlocks()"',
        'onclick="resetEventsFilters()"',
    ]
    for el in expected_elements:
        assert el in tab_b_filter_slice, f"[FAIL] Tab B filter slice missing element: {el}"

    # 验证分类选项值
    assert 'value="all"' in tab_b_filter_slice
    assert 'value="lifecycle"' in tab_b_filter_slice
    assert 'value="recovery"' in tab_b_filter_slice
    assert 'value="governance"' in tab_b_filter_slice
    assert 'value="__other__"' in tab_b_filter_slice


# =============================================================================
# AC-FILTER-04: 后端 /api/analytics/recovery-summary 真实性与多窗口时间过滤
# =============================================================================

def test_qa_ac04_backend_truthfulness_multi_interval(client):
    """
    [AC-FILTER-04] 验证后端时间过滤的真实性 (Truthfulness)：
    覆盖 24h, 7d, 14d, 30d, all, 自定义起止, 探针过滤, 租户隔离及空状态返回。
    """
    now = datetime.now()
    tenant_primary = "qa_truthfulness_tenant_alpha"
    tenant_other = "qa_truthfulness_tenant_beta"

    s = db.get_session()
    try:
        s.query(db._UserEventRow).filter(
            db._UserEventRow.tenant_id.in_([tenant_primary, tenant_other])
        ).delete()
        s.commit()

        # 播种事件：
        # e0: 30 分钟前 (24h, 7d, 14d, 30d, all)
        e0 = db._UserEventRow(
            event_type="ocr_parsed",
            tenant_id=tenant_primary,
            ts=(now - timedelta(minutes=30)).isoformat(),
        )
        # e1: 2 天前 (7d, 14d, 30d, all)
        e1 = db._UserEventRow(
            event_type="field_edited",
            tenant_id=tenant_primary,
            ts=(now - timedelta(days=2)).isoformat(),
        )
        # e2: 10 天前 (14d, 30d, all)
        e2 = db._UserEventRow(
            event_type="retake_clicked",
            tenant_id=tenant_primary,
            ts=(now - timedelta(days=10)).isoformat(),
        )
        # e3: 20 天前 (30d, all)
        e3 = db._UserEventRow(
            event_type="math_guard_checked",
            tenant_id=tenant_primary,
            ts=(now - timedelta(days=20)).isoformat(),
        )
        # e4: 45 天前 (仅 all)
        e4 = db._UserEventRow(
            event_type="custom_ancient_event",
            tenant_id=tenant_primary,
            ts=(now - timedelta(days=45)).isoformat(),
        )
        # e_probe: 探针事件 (任何周期均应被排除)
        e_probe = db._UserEventRow(
            event_type="qa_fe_probe",
            tenant_id=tenant_primary,
            ts=(now - timedelta(minutes=10)).isoformat(),
        )
        # e_beta: 其他租户事件
        e_beta = db._UserEventRow(
            event_type="ocr_parsed",
            tenant_id=tenant_other,
            ts=(now - timedelta(minutes=15)).isoformat(),
        )

        s.add_all([e0, e1, e2, e3, e4, e_probe, e_beta])
        s.commit()
    finally:
        s.close()

    headers = {"X-Role": "admin"}

    # 1. 24h 过滤
    r24 = client.get(f"/api/analytics/recovery-summary?tenant_id={tenant_primary}&date_range=24h", headers=headers)
    assert r24.status_code == 200
    dist24 = r24.json()["data"]["event_distribution"]
    assert len(dist24) == 1
    assert dist24[0]["event_type"] == "ocr_parsed"
    assert dist24[0]["count"] == 1
    assert dist24[0]["share"] == 1.0

    # 2. 7d 过滤
    r7 = client.get(f"/api/analytics/recovery-summary?tenant_id={tenant_primary}&date_range=7d", headers=headers)
    assert r7.status_code == 200
    dist7 = r7.json()["data"]["event_distribution"]
    types7 = {x["event_type"] for x in dist7}
    assert types7 == {"ocr_parsed", "field_edited"}

    # 3. 14d 过滤
    r14 = client.get(f"/api/analytics/recovery-summary?tenant_id={tenant_primary}&date_range=14d", headers=headers)
    assert r14.status_code == 200
    dist14 = r14.json()["data"]["event_distribution"]
    types14 = {x["event_type"] for x in dist14}
    assert types14 == {"ocr_parsed", "field_edited", "retake_clicked"}

    # 4. 30d 过滤
    r30 = client.get(f"/api/analytics/recovery-summary?tenant_id={tenant_primary}&date_range=30d", headers=headers)
    assert r30.status_code == 200
    dist30 = r30.json()["data"]["event_distribution"]
    types30 = {x["event_type"] for x in dist30}
    assert types30 == {"ocr_parsed", "field_edited", "retake_clicked", "math_guard_checked"}

    # 5. all 过滤 (包含 45 天前，但不含 probe)
    r_all = client.get(f"/api/analytics/recovery-summary?tenant_id={tenant_primary}&date_range=all", headers=headers)
    assert r_all.status_code == 200
    dist_all = r_all.json()["data"]["event_distribution"]
    types_all = {x["event_type"] for x in dist_all}
    assert types_all == {"ocr_parsed", "field_edited", "retake_clicked", "math_guard_checked", "custom_ancient_event"}
    assert "qa_fe_probe" not in types_all

    # 6. 自定义区间 (仅覆盖 2 天前至 10 天前)
    start_str = (now - timedelta(days=12)).isoformat()
    end_str = (now - timedelta(days=1)).isoformat()
    rcust = client.get(
        f"/api/analytics/recovery-summary?tenant_id={tenant_primary}&start_date={start_str}&end_date={end_str}",
        headers=headers,
    )
    assert rcust.status_code == 200
    dist_cust = rcust.json()["data"]["event_distribution"]
    types_cust = {x["event_type"] for x in dist_cust}
    assert types_cust == {"field_edited", "retake_clicked"}

    # 7. 空状态处理 (未来时间或无事件区间)
    future_start = (now + timedelta(days=1)).isoformat()
    future_end = (now + timedelta(days=2)).isoformat()
    rempty = client.get(
        f"/api/analytics/recovery-summary?tenant_id={tenant_primary}&start_date={future_start}&end_date={future_end}",
        headers=headers,
    )
    assert rempty.status_code == 200
    assert rempty.json()["data"]["event_distribution"] == []
    assert rempty.json()["data"]["total_events"] == 0

    # 8. 租户隔离
    r_beta = client.get(f"/api/analytics/recovery-summary?tenant_id={tenant_other}&date_range=all", headers=headers)
    assert r_beta.status_code == 200
    dist_beta = r_beta.json()["data"]["event_distribution"]
    assert len(dist_beta) == 1
    assert dist_beta[0]["event_type"] == "ocr_parsed"

    # 清理
    s = db.get_session()
    try:
        s.query(db._UserEventRow).filter(
            db._UserEventRow.tenant_id.in_([tenant_primary, tenant_other])
        ).delete()
        s.commit()
    finally:
        s.close()


# =============================================================================
# AC-FILTER-05: 客户端分类过滤与即时搜索逻辑
# =============================================================================

def test_qa_ac05_client_category_classification_and_search(main_js_content):
    """
    [AC-FILTER-05] 校验全量埋点事件类别映射与客户端瞬时搜索过滤逻辑：
    1. TELEMETRY_EVENT_CATEGORIES 定义完整 (lifecycle, recovery, governance)。
    2. _resolveEventCategory 能正确分类事件。
    3. renderEventDistribution 实现类别与搜索关键词联动及空状态提示。
    """
    assert "const TELEMETRY_EVENT_CATEGORIES" in main_js_content
    assert "function _resolveEventCategory" in main_js_content
    assert "function renderEventDistribution" in main_js_content

    # 校验分类定义与映射规则
    cat_idx = main_js_content.find("const TELEMETRY_EVENT_CATEGORIES")
    assert cat_idx != -1
    cat_block = main_js_content[cat_idx:cat_idx + 1500]
    assert "lifecycle" in cat_block
    assert "recovery" in cat_block
    assert "governance" in cat_block

    fn_res_idx = main_js_content.find("function _resolveEventCategory")
    assert fn_res_idx != -1
    fn_res_body = main_js_content[fn_res_idx:fn_res_idx + 400]
    assert "TELEMETRY_EVENT_CATEGORIES" in fn_res_body
    assert "'__other__'" in fn_res_body

    # 校验瞬时搜索匹配逻辑
    fn_dist_idx = main_js_content.find("function renderEventDistribution")
    fn_dist_body = main_js_content[fn_dist_idx:fn_dist_idx + 3500]
    assert "eventsCategorySelect" in fn_dist_body
    assert "eventsSearchInput" in fn_dist_body
    assert "_eventTypeAlias" in fn_dist_body
    assert "暂无符合当前筛选条件的埋点事件记录。" in fn_dist_body


# =============================================================================
# AC-FILTER-06: 历史兼容性与回溯下钻联动契约
# =============================================================================

def test_qa_ac06_compatibility_and_drilldown_contracts(index_html_content, main_js_content):
    """
    [AC-FILTER-06] 兼容既有契约：
    1. 包含 parsingLogDateRangeSelect 下拉框且具备 .parsing-log-filter-control 类。
    2. drillDownKpiDate 下钻函数正确填充日期并切换至 audit tab。
    """
    assert 'id="parsingLogDateRangeSelect" class="parsing-log-filter-control"' in index_html_content

    fn_dd_idx = main_js_content.find("function drillDownKpiDate")
    assert fn_dd_idx != -1
    fn_dd_body = main_js_content[fn_dd_idx:fn_dd_idx + 1500]
    assert "closeKpiTrendModal()" in fn_dd_body
    assert "switchTelemetrySubTab('audit')" in fn_dd_body
    assert "parsingLogDateRangeSelect" in fn_dd_body


# =============================================================================
# AC-FILTER-07: 全局严格 0 Emoji 静态扫描
# =============================================================================

def test_qa_ac07_zero_emoji_scan():
    """
    [AC-FILTER-07] 零 Emoji 铁律：
    扫描所有涉及的代码、模板与脚本文件，严禁包含任何 Emoji。
    """
    checked_files = [
        "demo/templates/index.html",
        "demo/static/js/main.js",
        "demo/app/api_phase2.py",
        "demo/app/services/parsing_logs.py",
        "tests/test_obs_filter_adjustments_regression.py",
        "tests/test_phase3_filter_adjustments_deep_qa.py",
    ]

    def has_emoji(text: str):
        violations = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            for ch in line:
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
                    violations.append((line_no, ch, hex(cp)))
        return violations

    for rel in checked_files:
        full_path = os.path.join(os.path.dirname(__file__), "..", rel)
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        violations = has_emoji(content)
        assert len(violations) == 0, f"[FAIL] Emoji detected in {rel}: {violations[:5]}"


# =============================================================================
# 常驻服务在线链路健康与实时接口验证 (Port 15010)
# =============================================================================

def test_qa_live_service_port_15010_healthy():
    """
    验证 15010 常驻后台服务处于运行状态且 /api/analytics/recovery-summary 正常响应。
    """
    url_health = "http://127.0.0.1:15010/api/health"
    req = urllib.request.Request(url_health)
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body.get("status") == "ok"

    url_api = "http://127.0.0.1:15010/api/analytics/recovery-summary?tenant_id=all&date_range=24h"
    req_api = urllib.request.Request(url_api, headers={"X-Role": "admin"})
    with urllib.request.urlopen(req_api, timeout=5) as resp:
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body.get("status") == "success"
        assert "event_distribution" in body.get("data", {})

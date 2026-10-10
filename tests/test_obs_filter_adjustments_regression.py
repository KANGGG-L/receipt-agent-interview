# -*- coding: utf-8 -*-
"""
Automated Regression Test Suite for Observation Center Filter Adjustments & Time-Range Governance.
Covers AC-FILTER-01 through AC-FILTER-07:
1. Removal of top collapsed shared filter card.
2. Self-contained filter controls inside Tab A (Parsing Logs) with time range buttons and compatible parsingLogDateRangeSelect.
3. Self-contained filter controls inside Tab B (Event Distribution) with tenant, period, category, and search.
4. Backend /api/analytics/recovery-summary strictly enforces date_range, start_date, and end_date filtering.
5. Client-side category and keyword filtering logic.
6. Zero Emoji static scan across all affected files.
"""
import os
import re
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


# -----------------------------------------------------------------------------
# AC-FILTER-01 & AC-FILTER-02 & AC-FILTER-03: DOM 结构与自内聚筛选器验证
# -----------------------------------------------------------------------------

def test_ac_filter_01_no_external_filter_card_in_telemetry(index_html_content):
    """
    [AC-FILTER-01] 校验 #sec-telemetry 中顶部粗暴跨 Tab 共享的筛选 Card 已彻底移除。
    在 8 KPI 卡片与 .analytics-sub-tabs 之间不得存在任何多维筛选表单控件。
    """
    sec_start = index_html_content.find('id="sec-telemetry"')
    assert sec_start != -1, "sec-telemetry must exist in index.html"

    tab_bar = index_html_content.find('class="analytics-sub-tabs"', sec_start)
    assert tab_bar != -1, "analytics-sub-tabs must exist in sec-telemetry"

    # 在 sec-telemetry 头部和子 Tab 切换器之间，不应出现 parsing-log-filter-control
    top_slice = index_html_content[sec_start:tab_bar]
    assert 'class="parsing-log-filter-control"' not in top_slice, (
        "No filter controls should exist above the sub-tabs in sec-telemetry"
    )


def test_ac_filter_02_tab_a_dedicated_filter_controls(index_html_content):
    """
    [AC-FILTER-02] 单据解析流水明细内聚时间跨度及完整筛选器。
    验证 Tab A 容器内包含所有细粒度筛选器，且保留契约兼容控件。
    """
    tab_a_start = index_html_content.find('id="telemetrySubContentAudit"')
    assert tab_a_start != -1, "telemetrySubContentAudit container must exist"
    tab_b_start = index_html_content.find('id="telemetrySubContentEvents"')
    assert tab_b_start != -1, "telemetrySubContentEvents container must exist"

    tab_a_slice = index_html_content[tab_a_start:tab_b_start]

    # 1. 租户与基础筛选
    assert 'id="analyticsTenantSelect" class="parsing-log-filter-control"' in tab_a_slice
    assert 'id="parsingLogStatusSelect" class="parsing-log-filter-control"' in tab_a_slice
    assert 'id="parsingLogGateSelect" class="parsing-log-filter-control"' in tab_a_slice
    assert 'id="parsingLogFeedbackSelect" class="parsing-log-filter-control"' in tab_a_slice
    assert 'id="parsingLogModifiedSelect" class="parsing-log-filter-control"' in tab_a_slice
    assert 'id="parsingLogSearchInput" class="parsing-log-filter-control"' in tab_a_slice

    # 2. 时间跨度控制（按钮组 + 自定义起止 + 下拉兼容控件）
    assert 'id="btnLogRangeAll"' in tab_a_slice
    assert 'id="btnLogRange24h"' in tab_a_slice
    assert 'id="btnLogRange7d"' in tab_a_slice
    assert 'id="btnLogRange14d"' in tab_a_slice
    assert 'id="btnLogRange30d"' in tab_a_slice
    assert 'id="parsingLogStartDate"' in tab_a_slice
    assert 'id="parsingLogEndDate"' in tab_a_slice
    assert 'id="parsingLogDateRangeSelect" class="parsing-log-filter-control"' in tab_a_slice


def test_ac_filter_03_tab_b_dedicated_filter_controls(index_html_content):
    """
    [AC-FILTER-03] 全量埋点事件分布新增专属筛选控制栏。
    包含租户、时间跨度、事件类别选择、事件搜索与查询重置按钮。
    """
    tab_b_start = index_html_content.find('id="telemetrySubContentEvents"')
    assert tab_b_start != -1, "telemetrySubContentEvents container must exist"
    tab_b_end = index_html_content.find('</section>', tab_b_start)
    assert tab_b_end != -1

    tab_b_slice = index_html_content[tab_b_start:tab_b_end]

    # 1. 租户输入
    assert 'id="eventsFilterTenant" class="parsing-log-filter-control"' in tab_b_slice

    # 2. 时间跨度按钮组与自定义起止
    assert 'id="btnEventsRangeAll"' in tab_b_slice
    assert 'id="btnEventsRange24h"' in tab_b_slice
    assert 'id="btnEventsRange7d"' in tab_b_slice
    assert 'id="btnEventsRange14d"' in tab_b_slice
    assert 'id="btnEventsRange30d"' in tab_b_slice
    assert 'id="eventsStartDate"' in tab_b_slice
    assert 'id="eventsEndDate"' in tab_b_slice

    # 3. 事件分类下拉框与事件搜索框
    assert 'id="eventsCategorySelect" class="parsing-log-filter-control"' in tab_b_slice
    assert 'value="lifecycle"' in tab_b_slice
    assert 'value="recovery"' in tab_b_slice
    assert 'value="governance"' in tab_b_slice
    assert 'id="eventsSearchInput" class="parsing-log-filter-control"' in tab_b_slice


# -----------------------------------------------------------------------------
# AC-FILTER-04: 后端 recovery-summary 接口时间跨度真实过滤
# -----------------------------------------------------------------------------

def test_ac_filter_04_recovery_summary_date_filtering(client):
    """
    [AC-FILTER-04] 验证 /api/analytics/recovery-summary 真实执行时间跨度过滤。
    构造测试用例数据并断言返回的 event_distribution 严格与指定时间范围吻合。
    """
    now = datetime.now()
    t_id = "qa_filter_test_tenant"

    # 清理并播种测试事件
    s = db.get_session()
    try:
        s.query(db._UserEventRow).filter(db._UserEventRow.tenant_id == t_id).delete()
        s.commit()

        # 事件 A: 1 小时前 (命中 24h, 7d, 14d, 30d)
        e1 = db._UserEventRow(
            event_type="ocr_parsed",
            tenant_id=t_id,
            ts=(now - timedelta(hours=1)).isoformat(),
        )
        # 事件 B: 3 天前 (命中 7d, 14d, 30d)
        e2 = db._UserEventRow(
            event_type="retake_clicked",
            tenant_id=t_id,
            ts=(now - timedelta(days=3)).isoformat(),
        )
        # 事件 C: 10 天前 (命中 14d, 30d)
        e3 = db._UserEventRow(
            event_type="rag_hit",
            tenant_id=t_id,
            ts=(now - timedelta(days=10)).isoformat(),
        )
        # 事件 D: 25 天前 (命中 30d)
        e4 = db._UserEventRow(
            event_type="price_anomaly_flagged",
            tenant_id=t_id,
            ts=(now - timedelta(days=25)).isoformat(),
        )
        # 事件 E: 60 天前 (仅全部命中)
        e5 = db._UserEventRow(
            event_type="inventory_in",
            tenant_id=t_id,
            ts=(now - timedelta(days=60)).isoformat(),
        )
        s.add_all([e1, e2, e3, e4, e5])
        s.commit()
    finally:
        s.close()

    headers = {"X-Role": "admin"}

    # 1. 查询全部 (all)
    r_all = client.get(f"/api/analytics/recovery-summary?tenant_id={t_id}&date_range=all", headers=headers)
    assert r_all.status_code == 200
    d_all = r_all.json()["data"]["event_distribution"]
    types_all = {x["event_type"] for x in d_all}
    assert types_all == {"ocr_parsed", "retake_clicked", "rag_hit", "price_anomaly_flagged", "inventory_in"}

    # 2. 查询 24h (应仅含 ocr_parsed)
    r_24h = client.get(f"/api/analytics/recovery-summary?tenant_id={t_id}&date_range=24h", headers=headers)
    assert r_24h.status_code == 200
    d_24h = r_24h.json()["data"]["event_distribution"]
    types_24h = {x["event_type"] for x in d_24h}
    assert types_24h == {"ocr_parsed"}

    # 3. 查询 7d (应含 ocr_parsed, retake_clicked)
    r_7d = client.get(f"/api/analytics/recovery-summary?tenant_id={t_id}&date_range=7d", headers=headers)
    assert r_7d.status_code == 200
    d_7d = r_7d.json()["data"]["event_distribution"]
    types_7d = {x["event_type"] for x in d_7d}
    assert types_7d == {"ocr_parsed", "retake_clicked"}

    # 4. 查询 14d (应含 ocr_parsed, retake_clicked, rag_hit)
    r_14d = client.get(f"/api/analytics/recovery-summary?tenant_id={t_id}&date_range=14d", headers=headers)
    assert r_14d.status_code == 200
    d_14d = r_14d.json()["data"]["event_distribution"]
    types_14d = {x["event_type"] for x in d_14d}
    assert types_14d == {"ocr_parsed", "retake_clicked", "rag_hit"}

    # 5. 查询自定义时间段 (以事件 C 和 D 为起止)
    s_date = (now - timedelta(days=26)).isoformat()
    e_date = (now - timedelta(days=8)).isoformat()
    r_custom = client.get(
        f"/api/analytics/recovery-summary?tenant_id={t_id}&start_date={s_date}&end_date={e_date}",
        headers=headers,
    )
    assert r_custom.status_code == 200
    d_custom = r_custom.json()["data"]["event_distribution"]
    types_custom = {x["event_type"] for x in d_custom}
    assert types_custom == {"rag_hit", "price_anomaly_flagged"}

    # 清理测试数据
    s = db.get_session()
    try:
        s.query(db._UserEventRow).filter(db._UserEventRow.tenant_id == t_id).delete()
        s.commit()
    finally:
        s.close()


# -----------------------------------------------------------------------------
# AC-FILTER-05: 前端 JS 逻辑重构与客户端联动函数定义
# -----------------------------------------------------------------------------

def test_ac_filter_05_js_tab_switching_and_events_filter_helpers(main_js_content):
    """
    [AC-FILTER-05] 验证 main.js 中已清理 auditOnlyFilterIds 隐藏逻辑，
    并包含 Tab B 所需的交互函数与客户端过滤逻辑。
    """
    # 1. switchTelemetrySubTab 中不得残留 auditOnlyFilterIds
    assert "auditOnlyFilterIds" not in main_js_content, (
        "auditOnlyFilterIds display toggling must be removed from main.js"
    )

    # 2. 必须定义并导出 Tab B 筛选控制函数
    assert "function setEventsPeriod" in main_js_content
    assert "function applyEventsCustomPeriod" in main_js_content
    assert "function onEventsTenantChange" in main_js_content
    assert "function resetEventsFilters" in main_js_content
    assert "function onEventsCategoryChange" in main_js_content
    assert "function onEventsSearchInput" in main_js_content

    # 3. loadRecoverySummaryBlocks 中已透传 date_range 及起止时间
    fn_rec_idx = main_js_content.find("function loadRecoverySummaryBlocks")
    assert fn_rec_idx != -1
    fn_rec_body = main_js_content[fn_rec_idx : fn_rec_idx + 2500]
    assert "eventsFilterTenant" in fn_rec_body
    assert "_eventsPeriod" in fn_rec_body
    assert "date_range" in fn_rec_body

    # 4. renderEventDistribution 包含客户端类别过滤与搜索过滤
    fn_dist_idx = main_js_content.find("function renderEventDistribution")
    assert fn_dist_idx != -1
    fn_dist_body = main_js_content[fn_dist_idx : fn_dist_idx + 3500]
    assert "eventsCategorySelect" in fn_dist_body
    assert "eventsSearchInput" in fn_dist_body


# -----------------------------------------------------------------------------
# AC-FILTER-07: 全局无 Emoji 红线核验 (0 Emoji)
# -----------------------------------------------------------------------------

def test_ac_filter_07_zero_emoji_scan():
    """
    [AC-FILTER-07] 全局严禁使用 Emoji。
    扫描本次修改的 HTML、JS、Python 文件，确保 0 个 Unicode Emoji。
    """
    target_files = [
        "demo/templates/index.html",
        "demo/static/js/main.js",
        "demo/app/api_phase2.py",
        "demo/app/services/parsing_logs.py",
    ]

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

    for rel_path in target_files:
        full_path = os.path.join(os.path.dirname(__file__), "..", rel_path)
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        found = find_emojis(content)
        assert len(found) == 0, f"Found emoji in {rel_path}: {found[:5]}"

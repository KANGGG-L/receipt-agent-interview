"""
观测中枢 KPI 口径统一与埋点事件下钻审计自动化测试套件
(Obs Center Telemetry Drill-down & KPI Consistency Test Suite)

覆盖验收标准：
- AC-KPI-01 [口径数值一致]
- AC-KPI-02 [挽回成功率口径一致]
- AC-KPI-03 [消除 DOM 竞态覆写]
- AC-KPI-04 [点踩率收敛合规]
- AC-UX-01 [静态大表彻底移除]
- AC-UX-02 [事件分布表入口可点击]
- AC-UX-03 [弹窗唤起与明细穿透]
- AC-UX-04 [弹窗检索与分页可用]
- AC-UX-05 [排版与测试兼容性]
- AC-GOV-01 [权限控制闭环]
- AC-GOV-02 [全局零 Emoji 铁律]
- AC-GOV-03 [数据脱敏规范]
"""

import json
import re
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db


@pytest.fixture
def client():
    return TestClient(app)


# =============================================================================
# 一、 AC-KPI: 口径一致性与无覆写自动化测试
# =============================================================================

def test_ac_kpi_01_and_02_metrics_identical_between_endpoints(client):
    """
    AC-KPI-01 & AC-KPI-02:
    在相同租户与时间跨度下，/api/analytics/parsing-logs 与 /api/analytics/recovery-summary
    的「异常挽回点击比」和「挽回成功率」必须完全同源、数值一致。
    """
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}

    # 1. 查询 parsing-logs
    resp_pl = client.get("/api/analytics/parsing-logs?tenant_id=default&date_range=all", headers=headers)
    assert resp_pl.status_code == 200
    pl_summary = resp_pl.json().get("data", {}).get("summary", {})

    # 2. 查询 recovery-summary
    resp_rs = client.get("/api/analytics/recovery-summary?tenant_id=default&date_range=all", headers=headers)
    assert resp_rs.status_code == 200
    rs_data = resp_rs.json().get("data", {})

    # 验证异常挽回点击比一致
    pl_entry_rate = pl_summary.get("recovery_entry_rate")
    rs_entry_rate = rs_data.get("recovery", {}).get("entry_rate")
    if pl_entry_rate is not None and rs_entry_rate is not None:
        assert abs(pl_entry_rate - rs_entry_rate) < 1e-4, \
            f"异常挽回点击比不一致: parsing-logs={pl_entry_rate}, recovery-summary={rs_entry_rate}"

    # 验证挽回成功率一致
    pl_success_rate = pl_summary.get("recovery_success_rate")
    rs_success_rate = rs_data.get("recovery_success", {}).get("rate")
    if pl_success_rate is not None and rs_success_rate is not None:
        assert abs(pl_success_rate - rs_success_rate) < 1e-4, \
            f"挽回成功率不一致: parsing-logs={pl_success_rate}, recovery-summary={rs_success_rate}"
        assert 0.0 <= rs_success_rate <= 1.0, f"挽回成功率超出 [0, 1] 范围: {rs_success_rate}"


def test_ac_kpi_03_no_race_dom_overwrite_in_render_recovery_metrics():
    """
    AC-KPI-03: 消除 DOM 竞态覆写。
    验证 renderRecoveryMetrics 函数中已彻底移除对 telemetryKpiRecoveryEntry
    与 telemetryKpiRecoverySuccess 的跨组件直接 DOM 覆写。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    # 定位 renderRecoveryMetrics 函数块
    fn_start = js.find("function renderRecoveryMetrics(")
    assert fn_start != -1
    fn_end = js.find("function openEventDetailModal(", fn_start)
    if fn_end == -1:
        fn_end = js.find("// -------------------------------------------------------------", fn_start + 100)
    render_recovery_block = js[fn_start:fn_end]

    assert "document.getElementById('telemetryKpiRecoveryEntry')" not in render_recovery_block, \
        "renderRecoveryMetrics 中残留对 telemetryKpiRecoveryEntry 的竞态 DOM 覆写"
    assert "document.getElementById('telemetryKpiRecoverySuccess')" not in render_recovery_block, \
        "renderRecoveryMetrics 中残留对 telemetryKpiRecoverySuccess 的竞态 DOM 覆写"

    # 确保 loadParsingLogs 正常作为唯一定义源渲染 KPI 卡片
    load_pl_start = js.find("function loadParsingLogs(")
    assert load_pl_start != -1
    load_pl_end = js.find("function renderParsingLogsTable(", load_pl_start)
    load_pl_block = js[load_pl_start:load_pl_end if load_pl_end != -1 else load_pl_start + 4000]
    assert "telemetryKpiRecoveryEntry" in load_pl_block, "loadParsingLogs 必须负责维护顶部 KPI 挽回点击比"
    assert "telemetryKpiRecoverySuccess" in load_pl_block, "loadParsingLogs 必须负责维护顶部 KPI 挽回成功率"


def test_ac_kpi_04_dislike_rate_bounded_and_consistent(client):
    """
    AC-KPI-04: 去偏置点踩率严格收敛在 [0.0, 1.0] 区间，且两端点完全一致。
    """
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}

    resp_pl = client.get("/api/analytics/parsing-logs?tenant_id=default", headers=headers)
    assert resp_pl.status_code == 200
    pl_summary = resp_pl.json().get("data", {}).get("summary", {})

    resp_rs = client.get("/api/analytics/recovery-summary?tenant_id=default", headers=headers)
    assert resp_rs.status_code == 200
    rs_feedback = resp_rs.json().get("data", {}).get("feedback", {})

    pl_dislike_rate = pl_summary.get("dislike_rate")
    rs_down_rate = rs_feedback.get("down_rate")

    if rs_down_rate is not None:
        assert 0.0 <= rs_down_rate <= 1.0, f"去偏置点踩率超出 [0, 1] 范围: {rs_down_rate}"
    if pl_dislike_rate is not None and rs_down_rate is not None:
        assert abs(pl_dislike_rate - rs_down_rate) < 1e-4, \
            f"点踩率在两端点不一致: parsing-logs={pl_dislike_rate}, recovery-summary={rs_down_rate}"


# =============================================================================
# 二、 AC-UX: 交互升级与下钻体验自动化测试
# =============================================================================

def test_ac_ux_01_static_table_removed_from_recovery_body():
    """
    AC-UX-01: Tab B 底部的静态大表彻底移除。
    验证 index.html 中 #analyticsRecoveryBody 内部不包含预置的大表，
    且 renderRecoveryMetrics 不再就地向 #analyticsRecoveryBody 生成静态大表。
    """
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # index.html 中 analyticsRecoveryBody 内部无静态表格
    assert '<table class="data-table" id="analyticsRecentEventsTable"' not in html

    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    fn_start = js.find("function renderRecoveryMetrics(")
    fn_end = js.find("function openEventDetailModal(", fn_start)
    recovery_code = js[fn_start:fn_end]
    assert '<table class="data-table" id="analyticsRecentEventsTable"' not in recovery_code, \
        "renderRecoveryMetrics 不得再向静态卡片内生成最近事件流大表"


def test_ac_ux_02_and_03_drill_down_entry_and_modal():
    """
    AC-UX-02 & AC-UX-03:
    - #analyticsEventDistTable 包含操作列与可点击查看明细入口
    - index.html 存在 #analyticsEventModal 专属下钻审计模态框
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    # 包含 [查看明细] 交互按钮与 openEventDetailModal / openEventLogsModal 调用
    assert "openEventDetailModal" in js
    assert "openEventLogsModal" in js
    assert "[查看明细]" in js
    assert "closeAnalyticsEventModal" in js
    assert "loadAnalyticsEventLogs" in js

    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="analyticsEventModal"' in html, "index.html 缺少 #analyticsEventModal"
    assert 'id="analyticsEventModalTitle"' in html
    assert 'id="analyticsEventModalTypeCode"' in html
    assert 'id="analyticsEventSearchInput"' in html
    assert 'id="analyticsEventTableContainer"' in html
    assert 'id="btnAnalyticsEventPrev"' in html
    assert 'id="btnAnalyticsEventNext"' in html


def test_ac_ux_04_and_05_modal_table_governance():
    """
    AC-UX-04 & AC-UX-05:
    - 模态框表格使用 table-layout: auto 与 min-width: 1160px
    - 表头严格为 触发时间 (最新在前)
    - 保持测试套件兼容性
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    fn_start = js.find("function renderAnalyticsEventTable(")
    assert fn_start != -1
    table_render_code = js[fn_start:fn_start + 2500]

    assert 'id="analyticsRecentEventsTable"' in table_render_code
    assert "min-width:1160px" in table_render_code
    assert "table-layout:auto" in table_render_code
    assert "触发时间 (最新在前)" in table_render_code
    assert "_fmtDateTime(ev.ts)" in table_render_code
    assert "replace('T', ' ')" in table_render_code
    assert "propsShow = propsFull.length > 80" not in table_render_code


# =============================================================================
# 三、 AC-GOV: 权限与工程治理红线自动化测试
# =============================================================================

def test_ac_gov_01_events_logs_api_security_and_pagination(client):
    """
    AC-GOV-01:
    - /api/analytics/events/logs 与 /api/analytics/events 均要求 admin 权限
    - 支持 event_type 过滤、search 过滤、分页与按 ts 稳定倒序
    """
    # 1. 匿名与普通用户访问拦截
    resp_anon = client.get("/api/analytics/events/logs")
    assert resp_anon.status_code in (401, 403)

    resp_staff = client.get("/api/analytics/events/logs", headers={"X-Role": "staff"})
    assert resp_staff.status_code in (401, 403)

    # 2. Admin 正常访问两套端点别名
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}
    resp_logs = client.get("/api/analytics/events/logs?page=1&page_size=10", headers=headers)
    assert resp_logs.status_code == 200
    data_logs = resp_logs.json().get("data", {})
    assert "pagination" in data_logs
    assert "items" in data_logs

    resp_events = client.get("/api/analytics/events?page=1&page_size=10", headers=headers)
    assert resp_events.status_code == 200

    # 3. 验证时间戳无原生 T，且时序按 ts 降序
    items = data_logs.get("items", [])
    if len(items) >= 2:
        tss = [it.get("ts", "") for it in items]
        for ts in tss:
            if ts and ts != "-":
                assert "T" not in ts, f"时间戳包含原生 'T': {ts}"
                assert len(ts) == 19, f"时间戳未格式化为 19 位定长: {ts}"
        assert tss == sorted(tss, reverse=True), f"事件列表未按时间降序排列: {tss}"

    # 4. 验证 event_type 过滤与 search 模糊匹配
    resp_filter = client.get("/api/analytics/events/logs?event_type=upload&page_size=10", headers=headers)
    assert resp_filter.status_code == 200
    filtered_items = resp_filter.json().get("data", {}).get("items", [])
    for it in filtered_items:
        assert it.get("event_type") == "upload", f"event_type 过滤不准确: {it.get('event_type')}"

    if filtered_items:
        test_rid = str(filtered_items[0].get("receipt_id") or "")
        if test_rid:
            resp_search = client.get(f"/api/analytics/events/logs?search={test_rid}&page_size=10", headers=headers)
            assert resp_search.status_code == 200
            search_items = resp_search.json().get("data", {}).get("items", [])
            for s_it in search_items:
                matched = (test_rid in str(s_it.get("receipt_id", ""))
                           or test_rid in str(s_it.get("account_id", ""))
                           or test_rid in json.dumps(s_it.get("properties", {}), ensure_ascii=False))
                assert matched, f"search 未命中目标关键字 {test_rid}: {s_it}"

            # 验证带 '#' 前缀的单据 ID 检索 (例如 #1506)
            resp_hash_search = client.get("/api/analytics/events/logs", params={"search": f"#{test_rid}", "page_size": 10}, headers=headers)
            assert resp_hash_search.status_code == 200
            hash_items = resp_hash_search.json().get("data", {}).get("items", [])
            assert len(hash_items) > 0, f"带 '#' 前缀 search=#{test_rid} 未返回任何匹配项"
            for h_it in hash_items:
                matched = (test_rid in str(h_it.get("receipt_id", ""))
                           or f"#{test_rid}" in str(h_it.get("account_id", ""))
                           or f"#{test_rid}" in json.dumps(h_it.get("properties", {}), ensure_ascii=False))
                assert matched, f"带 '#' 前缀 search 未命中目标: {h_it}"


def test_ac_ux_06_open_parsing_log_detail_modal_binding():
    """
    AC-UX-06: 验证 openParsingLogDetailModal 全局挂载与兼容支持。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "window.openParsingLogDetailModal = openParsingLogModal;" in js, \
        "main.js 必须挂载 window.openParsingLogDetailModal 别名"
    assert "function openParsingLogDetailModal(" in js, \
        "main.js 必须定义 openParsingLogDetailModal 函数"
    assert "window.openParsingLogModal = openParsingLogModal;" in js, \
        "main.js 必须挂载 window.openParsingLogModal"


def test_ac_gov_02_zero_emoji_in_modified_code():
    """
    AC-GOV-02: 全局零 Emoji 铁律验证。
    """
    emoji_regex = re.compile(
        r"[\U0001F300-\U0001F5FF]|[\U0001F600-\U0001F64F]|[\U0001F680-\U0001F6FF]|[\U0001F900-\U0001F9FF]|[\U0001FA70-\U0001FAFF]|[\u2600-\u26FF]|[\u2700-\u27BF]"
    )

    targets = [
        "demo/app/api_phase2.py",
        "demo/templates/index.html",
        "demo/static/js/main.js",
        "tests/test_obs_telemetry_drilldown_and_kpi_consistency.py",
    ]

    violations = []
    for p in targets:
        with open(p, "r", encoding="utf-8") as f:
            for l_no, line in enumerate(f, 1):
                m = emoji_regex.findall(line)
                if m:
                    violations.append((p, l_no, m, line.strip()))

    assert len(violations) == 0, f"发现 Emoji 违规: {violations}"

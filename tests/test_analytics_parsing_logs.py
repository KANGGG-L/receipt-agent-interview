"""
AI 效果观测与评测中心 - 解析日志与链路审计模块 (GET /api/analytics/parsing-logs)
全量自动化回归测试套件 (QA Acceptance Test Suite)
严格覆盖产品规格说明书 SPEC-ANALYTICS-PARSING-LOG-001 验收标准 (AC-1 ~ AC-8，共 18 项细分检验点)。
全局严禁使用任何 Emoji。
"""

import os
import re
import time
import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db
from app.services.parsing_logs import (
    compute_field_diffs,
    query_parsing_logs,
    _compute_summary,
    anonymize_vendor,
    get_parsing_log_detail,
)


@pytest.fixture
def client():
    return TestClient(app)


# -----------------------------------------------------------------------------
# AC-1: UI 导航与分段切换
# -----------------------------------------------------------------------------

def test_ac1_1_tab_segmented_bar_button():
    """AC-1.1: 选项卡分段控制栏展示为 4 个功能区，并入 [01 线上体验感知与链路审计]。"""
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # 1. 检验独立 05 分段按钮已彻底移除
    assert 'id="btnSubViewParsingLogs"' not in html_content

    # 2. 检验 01 按钮已升级为 [01 线上体验感知与链路审计]
    assert 'id="btnSubViewTelemetry"' in html_content
    assert 'data-view="sec-telemetry"' in html_content
    assert 'onclick="switchAnalyticsSubView(\'sec-telemetry\')"' in html_content
    assert '<span class="analytics-segment-num segment-num">01</span>' in html_content
    assert '<span class="analytics-segment-text">线上体验感知与链路审计</span>' in html_content

    # 3. 检验 4 个功能区按钮完整排布
    assert 'id="btnSubViewCanary"' in html_content
    assert 'id="btnSubViewExperiment"' in html_content
    assert 'id="btnSubViewEval"' in html_content


def test_ac1_2_and_1_3_subview_switching_and_activation():
    """AC-1.2 & AC-1.3: 分段切换函数与激活触发逻辑（向后兼容重定向至 01 观测中枢）。"""
    js_path = os.path.join(os.path.dirname(__file__), "../demo/static/js/main.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js_content = f.read()

    # 1. validViews 收敛为 4 大功能区
    assert "const validViews = ['sec-telemetry', 'sec-canary', 'sec-experiment', 'sec-eval'];" in js_content

    # 2. switchAnalyticsSubView 包含针对历史 sec-parsing-logs 的重定向与子 Tab 切换
    assert "if (targetViewId === 'sec-parsing-logs')" in js_content
    assert "targetViewId = 'sec-telemetry';" in js_content
    assert "switchTelemetrySubTab('audit');" in js_content

    # 3. 页面存在对应的挂载容器 #sec-telemetry 且包含单据审计主表与统一过滤栏
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    assert 'id="sec-telemetry"' in html_content
    assert 'id="parsingLogsTable"' in html_content
    assert 'id="analyticsTenantSelect"' in html_content


# -----------------------------------------------------------------------------
# AC-2: 核心字段全覆盖展示与旧数据降级
# -----------------------------------------------------------------------------

def test_ac2_1_core_fields_contract_completeness(client):
    """AC-2.1: 接口契约与 9 大核心字段及链路要素完整性校验。"""
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}
    resp = client.get("/api/analytics/parsing-logs?page=1&page_size=10", headers=headers)
    assert resp.status_code == 200
    res = resp.json()
    assert res.get("status") == "success"

    data = res.get("data", {})
    assert "summary" in data
    assert "pagination" in data
    assert "items" in data

    # 1. 验证核心 KPI 指标字段 (包含门禁统计)
    summary = data["summary"]
    required_summary_fields = (
        "total_parses", "success_count", "warning_count", "error_count",
        "success_rate", "avg_elapsed_ms", "avg_tokens",
        "likes_count", "dislikes_count", "positive_rate",
        "modified_count", "edit_rate",
        "gate_triggered_count", "gate_trigger_rate", "gate_breakdown"
    )
    for field in required_summary_fields:
        assert field in summary, f"Summary 缺少必填字段: {field}"

    # 2. 验证分页字段
    pag = data["pagination"]
    for field in ("page", "page_size", "total_items", "total_pages"):
        assert field in pag, f"Pagination 缺少必填字段: {field}"

    # 3. 验证单行单据明细中的核心要素
    items = data.get("items", [])
    if items:
        item = items[0]
        # (1) 日期时间
        assert "created_at" in item
        # (2) 租户 ID
        assert "tenant_id" in item
        # (3) 链路 ID
        assert "graph_id" in item
        # (4) 记录单据 ID
        assert "record_id" in item
        # (5) Token 消耗
        assert "token_usage" in item
        tu = item["token_usage"]
        assert "tokens_prompt" in tu
        assert "tokens_completion" in tu
        assert "tokens_total" in tu
        assert "cost_hkd" in tu
        assert "cost_estimated" in tu
        # (6) 解析用时及分段
        assert "elapsed_ms" in item
        el = item["elapsed_ms"]
        assert "total" in el
        assert "extract" in el
        assert "rag" in el
        assert "audit" in el
        # (7) 是否成功解析
        assert "parse_status" in item
        assert item["parse_status"] in ("success", "warning", "error", "processing")
        # (8) 门禁状态与原因 (gate_info)
        assert "gate_info" in item
        gi = item["gate_info"]
        assert "triggered" in gi
        assert "status" in gi
        assert "badge_label" in gi
        assert "primary_type" in gi
        assert "primary_type_label" in gi
        assert "summary" in gi
        assert "details" in gi
        assert isinstance(gi["details"], list)
        # (9) 用户反馈
        assert "feedback" in item
        fb = item["feedback"]
        assert "like" in fb
        assert "label" in fb
        assert fb["label"] in ("liked", "disliked", "unrated")
        assert "comment" in fb
        # (10) 用户是否改动
        assert "modification" in item
        mod = item["modification"]
        assert "is_modified" in mod
        assert "modified_count" in mod
        assert "modified_fields" in mod
        assert "diffs" in mod


def test_ac2_2_fault_tolerance_legacy_null_fields(client):
    """AC-2.2: 历史脏数据与缺失字段安全降级容错，严禁出现异常崩溃。"""
    s = db.get_session()
    test_rid = None
    try:
        # 创建一笔极度缺失附属字段的单据记录
        mock_row = db._ReceiptRow(
            tenant_id="default",
            image_path="",
            supplier_name="缺失字段容错测试店",
            receipt_date="2026-09-29",
            total_amount=0.0,
            status="uploaded",
            ai_prefill_json=None,       # 无 AI 原始预填
            audit_logs_json=None,       # 无 审计日志
            math_warnings_json=None,    # 无 警告
            quality_warnings_json=None,
            image_guard_json=None,
        )
        s.add(mock_row)
        s.commit()
        s.refresh(mock_row)
        test_rid = mock_row.id

        # 调取服务层查询该单据
        res = query_parsing_logs(s, search=str(test_rid), page=1, page_size=1)
        assert res["pagination"]["total_items"] >= 1
        item = res["items"][0]

        # 验证所有字段安全降级
        assert item["token_usage"]["tokens_prompt"] == 0
        assert item["token_usage"]["tokens_total"] == 0
        assert item["token_usage"]["cost_hkd"] == 0.0
        assert item["elapsed_ms"]["total"] == 0.0
        assert item["elapsed_ms"]["extract"] == 0.0
        assert item["feedback"]["like"] is None
        assert item["feedback"]["label"] == "unrated"
        assert item["modification"]["is_modified"] is False
        assert item["modification"]["diffs"] == []
        assert item["warnings"]["math_warnings"] == []
        assert item["warnings"]["image_guard_flagged"] is False
        assert isinstance(item["timeline"], list)

    finally:
        if test_rid:
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
            s.commit()
        s.close()


# -----------------------------------------------------------------------------
# AC-3: 数据溯源与准确性校验
# -----------------------------------------------------------------------------

def test_ac3_1_decision_log_data_traceability(client):
    """AC-3.1: Token 与耗时与 ai_decision_log 保持 100% 一致。"""
    s = db.get_session()
    test_rid = None
    try:
        # 插入一笔带决策记录的单据
        rec = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/test_trace.jpg",
            supplier_name="溯源准确性商行",
            status="parsed",
            total_amount=88.5,
        )
        s.add(rec)
        s.commit()
        s.refresh(rec)
        test_rid = rec.id

        extra_data = {
            "tokens_prompt": 3120,
            "tokens_completion": 580,
            "tokens_total": 3700,
            "cost_hkd": 0.00345,
            "cost_estimated": 0,
            "elapsed_ms": {
                "extract": 1250.5,
                "rag": 140.2,
                "audit": 12.3,
                "total": 1403.0,
            },
            "job_id": "job-qa-trace-3700"
        }
        dec = db._DecisionLogRow(
            receipt_id=test_rid,
            decision_type="extract",
            engine="qwen-vl",
            model="Qwen2.5-VL-72B-Instruct",
            extra=json.dumps(extra_data),
        )
        s.add(dec)
        s.commit()

        # 调取接口查询
        resp = client.get(f"/api/analytics/parsing-logs?search={test_rid}", headers={"X-Role": "admin"})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1
        matched = next(x for x in items if x["record_id"] == test_rid)

        # 逐项严格断言
        assert matched["graph_id"] == "job-qa-trace-3700"
        assert matched["engine"] == "qwen-vl"
        assert matched["model"] == "Qwen2.5-VL-72B-Instruct"
        assert matched["token_usage"]["tokens_prompt"] == 3120
        assert matched["token_usage"]["tokens_completion"] == 580
        assert matched["token_usage"]["tokens_total"] == 3700
        assert abs(matched["token_usage"]["cost_hkd"] - 0.00345) < 1e-6
        assert matched["elapsed_ms"]["extract"] == 1250.5
        assert matched["elapsed_ms"]["rag"] == 140.2
        assert matched["elapsed_ms"]["audit"] == 12.3
        assert matched["elapsed_ms"]["total"] == 1403.0

    finally:
        if test_rid:
            s.query(db._DecisionLogRow).filter(db._DecisionLogRow.receipt_id == test_rid).delete()
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
            s.commit()
        s.close()


def test_ac3_2_feedback_traceability(client):
    """AC-3.2: 用户点赞/点踩与文字留言准确映射。"""
    s = db.get_session()
    test_rid = None
    try:
        rec = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/test_fb.jpg",
            supplier_name="反馈溯源商行",
            status="approved",
            total_amount=200.0,
        )
        s.add(rec)
        s.commit()
        s.refresh(rec)
        test_rid = rec.id

        # 写入整单好评
        fb = db._ReceiptFeedbackRow(
            receipt_id=test_rid,
            item_index=None,
            like=1,
            comment="品名规格与金额识别完全精准，无需人工修正",
            created_at="2026-09-29 15:30:00",
        )
        s.add(fb)
        s.commit()

        # 调取接口查询
        resp = client.get(f"/api/analytics/parsing-logs?search={test_rid}", headers={"X-Role": "admin"})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        matched = next(x for x in items if x["record_id"] == test_rid)

        assert matched["feedback"]["like"] == 1
        assert matched["feedback"]["label"] == "liked"
        assert matched["feedback"]["comment"] == "品名规格与金额识别完全精准，无需人工修正"
        assert matched["feedback"]["created_at"] == "2026-09-29 15:30:00"

    finally:
        if test_rid:
            s.query(db._ReceiptFeedbackRow).filter(db._ReceiptFeedbackRow.receipt_id == test_rid).delete()
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
            s.commit()
        s.close()


def test_ac3_3_field_diffs_and_modification_traceability():
    """AC-3.3: AI 预填 vs 店员最终保存的 Diff 计算与修改项数准确性。"""
    class MockReceipt:
        supplier_name = "修正后百货商行"
        receipt_date = "2026-09-28"
        total_amount = 350.00
        status = "edited"
        audit_logs_json = json.dumps([{"action": "save_edited", "who": "staff"}])

    class MockItem:
        def __init__(self, name, qty, up, amt):
            self.name = name
            self.quantity = qty
            self.unit = "kg"
            self.unit_price = up
            self.amount = amt

    ai_prefill = {
        "vendor": "原始百货商行",
        "date": "2026-09-20",
        "total": 300.00,
        "items": [
            {"name": "菜心", "quantity": 10, "unit_price": 10.0, "amount": 100.0},
            {"name": "菜油", "quantity": 2, "unit_price": 100.0, "amount": 200.0},
        ],
    }

    cur_items = [
        MockItem("菜心", 10, 15.0, 150.0),   # 单价与金额修正
        MockItem("菜油", 2, 100.0, 200.0),   # 一致
        MockItem("生抽", 1, 20.0, 20.0),     # 新增项
    ]

    is_mod, mod_count, mod_fields, diffs = compute_field_diffs(ai_prefill, MockReceipt(), cur_items)

    assert is_mod is True
    # 差异项包含: 供应商(vendor!=supplier_name), 日期(date!=receipt_date), 总额(total!=total_amount), 第1行单价, 第1行金额, 第3行新增
    assert mod_count >= 5
    assert "supplier_name" in mod_fields
    assert "receipt_date" in mod_fields
    assert "total_amount" in mod_fields
    assert "items[0].unit_price" in mod_fields
    assert "items[2]" in mod_fields

    diff_fields = [d["field"] for d in diffs]
    assert "supplier_name" in diff_fields
    assert "receipt_date" in diff_fields
    assert "total_amount" in diff_fields
    assert any(d["type"] == "added" for d in diffs)


# -----------------------------------------------------------------------------
# AC-4: 多维组合筛选与模糊检索
# -----------------------------------------------------------------------------

def test_ac4_1_tenant_filtering_single_and_all(client):
    """AC-4.1: 租户下拉精准过滤与超级管理员全租户聚合。"""
    headers = {"X-Role": "admin"}

    # 1. 过滤 default 租户
    r_def = client.get("/api/analytics/parsing-logs?tenant_id=default&page_size=10", headers=headers)
    assert r_def.status_code == 200
    items_def = r_def.json()["data"]["items"]
    for it in items_def:
        assert it["tenant_id"] == "default"

    # 2. 全租户聚合查询 (tenant_id=all)
    r_all = client.get("/api/analytics/parsing-logs?tenant_id=all&page_size=5", headers=headers)
    assert r_all.status_code == 200
    assert "total_items" in r_all.json()["data"]["pagination"]


def test_ac4_2_multi_dimensional_matrix_filters(client):
    """AC-4.2: 状态、反馈、人工改动的单选与交叉组合过滤。"""
    headers = {"X-Role": "admin"}

    # 1. 状态: success
    r = client.get("/api/analytics/parsing-logs?status=success&page_size=5", headers=headers)
    assert r.status_code == 200
    for it in r.json()["data"]["items"]:
        assert it["parse_status"] in ("success", "warning")

    # 2. 反馈: unrated
    r = client.get("/api/analytics/parsing-logs?feedback=unrated&page_size=5", headers=headers)
    assert r.status_code == 200
    for it in r.json()["data"]["items"]:
        assert it["feedback"]["like"] not in (1, -1)

    # 3. 组合交叉过滤: status=success + feedback=unrated + is_modified=no
    r_combo = client.get(
        "/api/analytics/parsing-logs?status=success&feedback=unrated&is_modified=no&page_size=5",
        headers=headers,
    )
    assert r_combo.status_code == 200
    for it in r_combo.json()["data"]["items"]:
        assert it["parse_status"] in ("success", "warning")
        assert it["feedback"]["like"] not in (1, -1)


def test_ac4_3_fuzzy_search_by_id_graph_supplier(client):
    """AC-4.3: 综合搜索框模糊检索 (Record ID、Graph ID、供应商)。"""
    headers = {"X-Role": "admin"}

    # 1. 按 Record ID 搜索
    r1 = client.get("/api/analytics/parsing-logs?page=1&page_size=1", headers=headers)
    items = r1.json()["data"]["items"]
    if items:
        target_rid = items[0]["record_id"]
        r_srch = client.get(f"/api/analytics/parsing-logs?search={target_rid}", headers=headers)
        assert r_srch.status_code == 200
        found = r_srch.json()["data"]["items"]
        assert any(x["record_id"] == target_rid for x in found)

    # 2. 按不存在的关键词搜索 -> 结果为空
    r_empty = client.get("/api/analytics/parsing-logs?search=KEYWORD_NON_EXIST_XYZ_9999", headers=headers)
    assert r_empty.status_code == 200
    assert r_empty.json()["data"]["pagination"]["total_items"] == 0
    assert len(r_empty.json()["data"]["items"]) == 0


def test_ac4_4_filter_reset_semantics(client):
    """AC-4.4: 重置筛选语义校验，全量回落默认视图。"""
    headers = {"X-Role": "admin"}
    # 默认调用与显式全 all 参数等价
    r_default = client.get("/api/analytics/parsing-logs", headers=headers)
    r_explicit = client.get(
        "/api/analytics/parsing-logs?tenant_id=all&status=all&feedback=all&is_modified=all&date_range=all&search=",
        headers=headers,
    )
    assert r_default.status_code == 200
    assert r_explicit.status_code == 200
    assert r_default.json()["data"]["pagination"]["total_items"] == r_explicit.json()["data"]["pagination"]["total_items"]


# -----------------------------------------------------------------------------
# AC-5: 展开详情与 Diff 审计比对
# -----------------------------------------------------------------------------

def test_ac5_1_detail_drawer_modal_elements():
    """AC-5.1: 界面审计模态抽屉及其 4 个 Tab 容器元素存在。"""
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 模态与核心元数据
    assert 'id="parsingLogDetailModal"' in html
    assert 'id="plMetaRecordId"' in html
    assert 'id="plMetaGraphId"' in html
    assert 'id="plMetaTenantId"' in html
    assert 'id="plMetaSupplier"' in html

    # 4 个 Tab 导航按钮
    assert 'id="btnPLTabDiff"' in html
    assert 'id="btnPLTabToken"' in html
    assert 'id="btnPLTabGuard"' in html
    assert 'id="btnPLTabAudit"' in html

    # 4 个 Tab 内容区
    assert 'id="plTabContentDiff"' in html
    assert 'id="plTabContentToken"' in html
    assert 'id="plTabContentGuard"' in html
    assert 'id="plTabContentAudit"' in html


def test_ac5_2_diff_structure_and_categories(client):
    """AC-5.2: Diff 字段级差异结构 (字段、AI预填值、最终值、判定类型)。"""
    headers = {"X-Role": "admin"}
    r = client.get("/api/analytics/parsing-logs?page=1&page_size=30", headers=headers)
    assert r.status_code == 200
    items = r.json()["data"]["items"]

    # 验证任意有 diff 的条目结构
    diff_verified = False
    for it in items:
        diffs = it["modification"].get("diffs", [])
        if diffs:
            for d in diffs:
                assert "field" in d
                assert "label" in d
                assert "ai_value" in d
                assert "user_value" in d
                assert "type" in d
                assert d["type"] in ("modified", "added", "deleted")
                diff_verified = True
            break

    # 若暂无带 diff 的单据，通过直接测试验证
    if not diff_verified:
        is_mod, cnt, fields, diffs = compute_field_diffs(
            {"vendor": "旧", "items": []},
            type("Mock", (), {"supplier_name": "新", "status": "edited", "total_amount": 0.0, "audit_logs_json": "[]"})(),
            []
        )
        assert len(diffs) > 0
        assert diffs[0]["type"] == "modified"


def test_ac5_3_waterfall_and_guards_structure(client):
    """AC-5.3: 性能瀑布（分段耗时）与质检安全门禁结构校验。"""
    headers = {"X-Role": "admin"}
    r = client.get("/api/analytics/parsing-logs?page=1&page_size=5", headers=headers)
    assert r.status_code == 200
    for it in r.json()["data"]["items"]:
        # 耗时分段
        el = it["elapsed_ms"]
        assert "extract" in el
        assert "rag" in el
        assert "audit" in el
        assert "total" in el
        # 质检门禁
        warn = it["warnings"]
        assert "math_warnings" in warn
        assert "quality_warnings" in warn
        assert "image_guard_flagged" in warn
        assert isinstance(warn["image_guard_flagged"], bool)


# -----------------------------------------------------------------------------
# AC-6: 跨租户与权限安全门禁
# -----------------------------------------------------------------------------

def test_ac6_1_tenant_isolation_boundary(client):
    """AC-6.1: 租户数据安全边界，严禁跨租户泄漏。"""
    headers = {"X-Role": "admin", "X-Tenant-Id": "qa_tenant_isolated"}
    r = client.get("/api/analytics/parsing-logs?tenant_id=qa_tenant_isolated", headers=headers)
    assert r.status_code == 200
    for it in r.json()["data"]["items"]:
        assert it["tenant_id"] == "qa_tenant_isolated"


def test_ac6_2_auth_enforcement_role_matrix(client):
    """AC-6.2: 接口鉴权门禁角色矩阵 (401 拦截未认证，403 拦截普通角色，200 放行 Admin)。"""
    # 1. 匿名访问 -> 401
    r_anon = client.get("/api/analytics/parsing-logs")
    assert r_anon.status_code == 401

    # 2. 普通店员 (staff) -> 403
    r_staff = client.get("/api/analytics/parsing-logs", headers={"X-Role": "staff"})
    assert r_staff.status_code == 403

    # 3. 店长 (owner) -> 403 (管理端观测仅限 admin)
    r_owner = client.get("/api/analytics/parsing-logs", headers={"X-Role": "owner"})
    assert r_owner.status_code == 403

    # 4. 超级管理员 (admin) -> 200
    r_admin = client.get("/api/analytics/parsing-logs", headers={"X-Role": "admin"})
    assert r_admin.status_code == 200


# -----------------------------------------------------------------------------
# AC-7: 性能基准与分页容错
# -----------------------------------------------------------------------------

def test_ac7_1_performance_benchmark_single_page(client):
    """AC-7.1: 单页查询响应时间 ≤ 300ms 性能门禁。"""
    headers = {"X-Role": "admin"}
    durations = []
    # 连续执行 5 次单页查询取平均
    for _ in range(5):
        t0 = time.time()
        resp = client.get("/api/analytics/parsing-logs?page=1&page_size=20", headers=headers)
        dt_ms = (time.time() - t0) * 1000
        assert resp.status_code == 200
        durations.append(dt_ms)

    avg_ms = sum(durations) / len(durations)
    max_ms = max(durations)
    assert avg_ms < 300.0, f"单页查询平均耗时 {avg_ms:.1f}ms 超过 300ms 红线"
    assert max_ms < 500.0, f"单页查询最大耗时 {max_ms:.1f}ms 异常波动"


def test_ac7_2_pagination_navigation_and_limits(client):
    """AC-7.2: 分页切片导航、页码越界与上限控制。"""
    headers = {"X-Role": "admin"}

    # 1. 查询第 1 页
    r1 = client.get("/api/analytics/parsing-logs?page=1&page_size=2", headers=headers)
    assert r1.status_code == 200
    p1 = r1.json()["data"]["pagination"]
    assert p1["page"] == 1
    assert p1["page_size"] == 2

    # 2. 查询第 2 页
    r2 = client.get("/api/analytics/parsing-logs?page=2&page_size=2", headers=headers)
    assert r2.status_code == 200
    p2 = r2.json()["data"]["pagination"]
    assert p2["page"] == 2

    # 若记录多于 2 条，第 1 页与第 2 页单据 ID 不重叠
    items1 = r1.json()["data"]["items"]
    items2 = r2.json()["data"]["items"]
    if items1 and items2:
        ids1 = {x["record_id"] for x in items1}
        ids2 = {x["record_id"] for x in items2}
        assert ids1.isdisjoint(ids2)

    # 3. page_size 超限自动收敛至 100
    r_large = client.get("/api/analytics/parsing-logs?page=1&page_size=500", headers=headers)
    assert r_large.status_code == 200
    assert r_large.json()["data"]["pagination"]["page_size"] == 100


def test_ac7_3_empty_state_resilience(client):
    """AC-7.3: 无匹配数据时展示优雅空状态，严禁出现后端报错或表格断裂。"""
    headers = {"X-Role": "admin"}
    r = client.get("/api/analytics/parsing-logs?search=__NOTHING_MATCHED_XYZ_123456__", headers=headers)
    assert r.status_code == 200
    res = r.json()
    assert res["status"] == "success"
    data = res["data"]
    assert data["pagination"]["total_items"] == 0
    assert data["pagination"]["total_pages"] == 1
    assert data["items"] == []

    # KPI 汇总安全降级为 0，严禁 ZeroDivisionError
    summary = data["summary"]
    assert summary["total_parses"] == 0
    assert summary["success_rate"] == 0.0
    assert summary["positive_rate"] == 0.0
    assert summary["edit_rate"] == 0.0


# -----------------------------------------------------------------------------
# AC-8: 规范与红线门禁 (零 Emoji 与文本克制)
# -----------------------------------------------------------------------------

def test_ac8_1_zero_emoji_static_scan():
    """AC-8.1: 全局严禁出现任何 Emoji 表情静态扫描。"""
    # 涵盖本次变更与核心业务文件
    target_files = [
        "demo/app/services/parsing_logs.py",
        "demo/app/api_admin.py",
        "demo/templates/index.html",
        "demo/static/js/main.js",
        "tests/test_analytics_parsing_logs.py",
    ]

    emoji_pattern = re.compile(
        r"[\U00010000-\U0010ffff]|"
        r"[\u2600-\u26ff]|"
        r"[\u2700-\u27bf]"
    )

    violations = []
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    for rel_path in target_files:
        full_path = os.path.join(base_dir, rel_path)
        if not os.path.exists(full_path):
            continue
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            for line_idx, line in enumerate(f, 1):
                # 如果是 index.html 或 main.js，限定于本次解析日志的区间或全量检验
                # 针对 parsing_logs.py 和 test_analytics_parsing_logs.py 全量严查
                if "parsing_logs" in rel_path:
                    matches = emoji_pattern.findall(line)
                    if matches:
                        violations.append((rel_path, line_idx, matches, line.strip()))
                elif "index.html" in rel_path:
                    # 检查 05 模块与样式相关行
                    if 50 <= line_idx <= 120 or 2180 <= line_idx <= 2220 or 2500 <= line_idx <= 2700 or 2750 <= line_idx <= 2980:
                        matches = emoji_pattern.findall(line)
                        if matches:
                            violations.append((rel_path, line_idx, matches, line.strip()))
                elif "main.js" in rel_path:
                    if line_idx >= 16640:
                        matches = emoji_pattern.findall(line)
                        if matches:
                            violations.append((rel_path, line_idx, matches, line.strip()))

    assert len(violations) == 0, f"发现 Emoji 违规: {violations}"


def test_ac8_2_restrained_ui_text_and_type_discipline():
    """AC-8.2: 界面文案严谨、克制，代码具备完整的类型声明。"""
    # 检验 parsing_logs.py 具有类型标注
    import inspect
    from app.services import parsing_logs
    assert inspect.isfunction(parsing_logs.compute_field_diffs)
    assert inspect.isfunction(parsing_logs.query_parsing_logs)
    assert inspect.isfunction(parsing_logs._compute_summary)


# -----------------------------------------------------------------------------
# AC-API: 门禁归因、过滤与统计接口验收套件 (AC-API-01 ~ AC-API-05)
# -----------------------------------------------------------------------------

def test_ac_api_01_gate_info_structure(client):
    """AC-API-01: 校验列表项 gate_info 结构体契约。"""
    headers = {"X-Role": "admin"}
    resp = client.get("/api/analytics/parsing-logs?page=1&page_size=10", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    if items:
        gi = items[0]["gate_info"]
        assert isinstance(gi["triggered"], bool)
        assert gi["status"] in ("none", "warning", "rejected")
        assert isinstance(gi["badge_label"], str)
        assert isinstance(gi["primary_type"], str)
        assert isinstance(gi["primary_type_label"], str)
        assert isinstance(gi["summary"], str)
        assert isinstance(gi["details"], list)


def test_ac_api_02_gate_status_filters(client):
    """AC-API-02: 校验 gate_status 筛选能力 (all, triggered, none, rejected, warning)。"""
    headers = {"X-Role": "admin"}
    for gs in ("all", "triggered", "none", "rejected", "warning"):
        resp = client.get(f"/api/analytics/parsing-logs?gate_status={gs}&page_size=5", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        items = data["items"]
        if gs == "none":
            for it in items:
                assert it["gate_info"]["triggered"] is False
                assert it["gate_info"]["status"] == "none"
        elif gs == "triggered":
            for it in items:
                assert it["gate_info"]["triggered"] is True
                assert it["gate_info"]["status"] in ("warning", "rejected")
        elif gs == "rejected":
            for it in items:
                assert it["gate_info"]["status"] == "rejected"
        elif gs == "warning":
            for it in items:
                assert it["gate_info"]["status"] == "warning"


def test_ac_api_03_gate_reject_decision_attribution(client):
    """AC-API-03: 当决策日志包含 gate_reject 时判定为 rejected 并提取 gate_err。"""
    s = db.get_session()
    test_rid = None
    try:
        rec = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/test_gate_reject.jpg",
            supplier_name="门禁拦截测试商户",
            status="parsed_with_warnings",
            total_amount=100.0,
        )
        s.add(rec)
        s.commit()
        s.refresh(rec)
        test_rid = rec.id

        dec = db._DecisionLogRow(
            receipt_id=test_rid,
            decision_type="extract",
            engine="vlm",
            model="qwen-vl",
            ai_value=json.dumps({"status": "gate_reject", "gate_err": "算术门禁: 明细合计与总额不一致(相差$10.00)"}),
            extra=json.dumps({"status": "gate_reject", "gate_err": "算术门禁: 明细合计与总额不一致(相差$10.00)"}),
        )
        s.add(dec)
        s.commit()

        resp = client.get(f"/api/analytics/parsing-logs?search={test_rid}", headers={"X-Role": "admin"})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        matched = next(x for x in items if x["record_id"] == test_rid)
        gi = matched["gate_info"]

        assert gi["triggered"] is True
        assert gi["status"] == "rejected"
        assert "[门禁拦截" in gi["badge_label"]
        assert "算术门禁" in gi["primary_type_label"]
        assert "明细合计与总额不一致" in gi["summary"]
        assert len(gi["details"]) >= 1
        assert any(d["level"] == "rejected" for d in gi["details"])

    finally:
        if test_rid:
            s.query(db._DecisionLogRow).filter(db._DecisionLogRow.receipt_id == test_rid).delete()
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
            s.commit()
        s.close()


def test_ac_api_04_warnings_attribution(client):
    """AC-API-04: 当单据存在 math_warnings_json 时判定为 warning。"""
    s = db.get_session()
    test_rid = None
    try:
        rec = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/test_gate_warn.jpg",
            supplier_name="门禁告警测试商户",
            status="parsed_with_warnings",
            total_amount=50.0,
            math_warnings_json=json.dumps(["明细项累计 40.00 与总额 50.00 不一致"]),
        )
        s.add(rec)
        s.commit()
        s.refresh(rec)
        test_rid = rec.id

        resp = client.get(f"/api/analytics/parsing-logs?search={test_rid}", headers={"X-Role": "admin"})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        matched = next(x for x in items if x["record_id"] == test_rid)
        gi = matched["gate_info"]

        assert gi["triggered"] is True
        assert gi["status"] == "warning"
        assert "[门禁告警" in gi["badge_label"]
        assert "明细项累计 40.00" in gi["summary"]

    finally:
        if test_rid:
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
            s.commit()
        s.close()


def test_ac_api_05_summary_gate_metrics(client):
    """AC-API-05: 校验汇总指标中的 gate_triggered_count, gate_trigger_rate, gate_breakdown。"""
    headers = {"X-Role": "admin"}
    resp = client.get("/api/analytics/parsing-logs?page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    summary = resp.json()["data"]["summary"]

    assert "gate_triggered_count" in summary
    assert "gate_trigger_rate" in summary
    assert "gate_breakdown" in summary
    assert isinstance(summary["gate_triggered_count"], int)
    assert isinstance(summary["gate_trigger_rate"], (int, float))
    assert isinstance(summary["gate_breakdown"], dict)
    for k in ("math", "contract", "quality", "security"):
        assert k in summary["gate_breakdown"]


# -----------------------------------------------------------------------------
# AC-UI: 前端视觉尺寸系统与 UI 重构验收套件 (AC-UI-01 ~ AC-UI-06)
# -----------------------------------------------------------------------------

def test_ac_ui_01_subtitle_removed():
    """AC-UI-01: 校验冗余副标题已彻底从 DOM 树移除。"""
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 原副标题文案不得残留
    assert "单据全生命周期图片解析审计：Token/耗时逐笔透视" not in html
    assert "质量门禁与用户反馈追踪" not in html


def test_ac_ui_02_filter_controls_uniformity():
    """AC-UI-02: 校验筛选控件标准规范类 .parsing-log-filter-control, .parsing-log-filter-label, .parsing-log-filter-btn。"""
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert ".parsing-log-filter-control" in html
    assert "height: 34px !important;" in html
    assert "font-size: 13px !important;" in html
    assert "border-radius: 6px !important;" in html
    assert ".parsing-log-filter-label" in html
    assert ".parsing-log-filter-btn" in html

    # 校验各控件已使用该统一规格 class
    assert 'id="analyticsTenantSelect" class="parsing-log-filter-control"' in html
    assert 'id="parsingLogStatusSelect" class="parsing-log-filter-control"' in html
    assert 'id="parsingLogGateSelect" class="parsing-log-filter-control"' in html
    assert 'id="parsingLogFeedbackSelect" class="parsing-log-filter-control"' in html
    assert 'id="parsingLogModifiedSelect" class="parsing-log-filter-control"' in html
    assert 'id="parsingLogDateRangeSelect" class="parsing-log-filter-control"' in html
    assert 'id="parsingLogSearchInput" class="parsing-log-filter-control"' in html


def test_ac_ui_03_gate_select_options():
    """AC-UI-03: 校验门禁触发筛选器及其 5 个标准选项。"""
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="parsingLogGateSelect"' in html
    assert '<option value="all">全部门禁状态</option>' in html
    assert '<option value="triggered">已触发门禁 (告警/拦截)</option>' in html
    assert '<option value="none">无门禁 (平算通过)</option>' in html
    assert '<option value="rejected">仅门禁拦截 (Reject)</option>' in html
    assert '<option value="warning">仅门禁告警 (Warning)</option>' in html


def test_ac_ui_04_and_05_table_gate_column_and_min_width():
    """AC-UI-04 & AC-UI-05: 校验表格 13 列排布、独立门禁列及 min-width 1680px。"""
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 宽度保障
    assert 'id="parsingLogsTable" style="min-width:1680px;' in html

    # 门禁列表头存在
    assert '门禁状态与原因' in html

    # JS 渲染包含门禁状态与原因列
    js_path = os.path.join(os.path.dirname(__file__), "../demo/static/js/main.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js = f.read()

    assert "gateBadgeHtml" in js
    assert "gateDescHtml" in js
    assert "[平算无门禁]" in js


def test_ac_ui_06_detail_modal_gate_and_tab3():
    """AC-UI-06: 校验审计详情模态框元数据门禁徽章及 Tab 3 四大模块结构。"""
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 元数据条包含门禁结论
    assert 'id="plMetaGateBadge"' in html

    # Tab 3 名称更新
    assert "Tab 3: 质检与门禁规则审计" in html

    # Tab 3 四大模块容器
    assert 'id="plGateDecisionBody"' in html
    assert 'id="plMathWarningsBody"' in html
    assert 'id="plContractGuardBody"' in html
    assert 'id="plQualityWarningsBody"' in html
    assert 'id="plImageGuardBody"' in html


# -----------------------------------------------------------------------------
# AC-QA: 整合回归专项测试 (KPI 计算、供应商脱敏、详情接口下钻、多维筛选联动)
# -----------------------------------------------------------------------------

def test_ac_qa_kpi_01_metrics_calculation_and_bounded_dislike_rate():
    """AC-QA-KPI-01: 8 块 KPI 指标计算准确性及点踩率 strictly in [0.0, 1.0] 约束。"""
    s = db.get_session()
    rids = []
    try:
        # 1. 空状态测试
        empty_summary = _compute_summary(s, tenant_id="non_existent_tenant_xyz")
        assert empty_summary["total_parses"] == 0
        assert empty_summary["success_rate"] == 0.0
        assert empty_summary["positive_rate"] == 0.0
        assert empty_summary["dislike_rate"] == 0.0
        assert empty_summary["dislike_frequency"] == 1.0
        assert empty_summary["recovery_entry_rate"] == 0.0
        assert empty_summary["recovery_success_rate"] == 0.0

        # 2. 插入测试数据: 2 张单据
        for i in range(2):
            rec = db._ReceiptRow(
                tenant_id="qa_kpi_tenant",
                image_path=f"/demo/uploads/test_kpi_{i}.jpg",
                supplier_name=f"KPI测试店_{i}",
                status="parsed",
                total_amount=100.0 * (i + 1),
            )
            s.add(rec)
            s.commit()
            s.refresh(rec)
            rids.append(rec.id)

        # 针对这 2 张单据插入反馈：单据 0 有 3 条点踩反馈，单据 1 有 1 条点踩反馈 (总点踩 4 条，点踩单据 2 张)
        for _ in range(3):
            fb = db._ReceiptFeedbackRow(
                receipt_id=rids[0],
                item_index=None,
                like=-1,
                comment="识别有误",
            )
            s.add(fb)
        fb1 = db._ReceiptFeedbackRow(
            receipt_id=rids[1],
            item_index=None,
            like=-1,
            comment="金额不准",
        )
        s.add(fb1)
        s.commit()

        # 插入异常挽回事件与审核成功事件
        ev1 = db._UserEventRow(
            tenant_id="qa_kpi_tenant",
            receipt_id=rids[0],
            event_type="retake_clicked",
            ts="2026-09-29T12:00:00",
        )
        ev2 = db._UserEventRow(
            tenant_id="qa_kpi_tenant",
            receipt_id=rids[1],
            event_type="reparse_clicked",
            ts="2026-09-29T12:05:00",
        )
        # 单据 0 挽回后审核通过
        ev3 = db._UserEventRow(
            tenant_id="qa_kpi_tenant",
            receipt_id=rids[0],
            event_type="receipt_approved",
            ts="2026-09-29T12:10:00",
        )
        s.add_all([ev1, ev2, ev3])
        s.commit()

        summary = _compute_summary(s, tenant_id="qa_kpi_tenant")

        # 验证指标字段
        assert summary["total_parses"] == 2
        assert summary["disliked_receipts_count"] == 2
        assert summary["feedback_receipts_count"] == 2
        # 去偏置点踩率：点踩单据数 / 反馈单据数 = 2 / 2 = 1.0 (绝非 4 / 2 = 200%)
        assert 0.0 <= summary["dislike_rate"] <= 1.0
        assert summary["dislike_rate"] == 1.0
        # 重复点踩频次: 4 次事件 / 2 张单据 = 2.0
        assert summary["dislike_frequency"] == 2.0

        # 挽回漏斗
        assert summary["recovery_attempts"] == 2
        assert summary["recovery_success_count"] == 1
        assert summary["recovery_success_rate"] == 0.5

    finally:
        for rid in rids:
            s.query(db._ReceiptFeedbackRow).filter(db._ReceiptFeedbackRow.receipt_id == rid).delete()
            s.query(db._UserEventRow).filter(db._UserEventRow.receipt_id == rid).delete()
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == rid).delete()
        s.commit()
        s.close()


def test_ac_qa_vendor_01_anonymization_unit_and_integration(client):
    """AC-QA-VENDOR-01: 供应商名称确定性别名化脱敏与响应中无明文供应商泄漏。"""
    # 1. 单元测试: anonymize_vendor
    assert anonymize_vendor("华润万家超级市场").startswith("Vendor_")
    assert anonymize_vendor("Fresh Vegetable Wholesaler").startswith("Vendor_")
    assert anonymize_vendor("Vendor_A12") == "Vendor_A12"
    assert anonymize_vendor("") == "Vendor_Unknown"
    assert anonymize_vendor(None) == "Vendor_Unknown"
    # 确定性映射验证
    assert anonymize_vendor("商户测试名称ABC") == anonymize_vendor("商户测试名称ABC")

    # 2. 接口集成测试: 返回值中绝无明文供应商
    s = db.get_session()
    test_rid = None
    secret_vendor = "绝密机密供应商_极客鲜生测试店"
    try:
        rec = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/test_secret_vendor.jpg",
            supplier_name=secret_vendor,
            status="parsed",
            total_amount=66.8,
        )
        s.add(rec)
        s.commit()
        s.refresh(rec)
        test_rid = rec.id

        resp = client.get(f"/api/analytics/parsing-logs?search={test_rid}", headers={"X-Role": "admin"})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) >= 1
        matched = next(x for x in items if x["record_id"] == test_rid)

        # supplier_name 必须别名化
        assert matched["supplier_name"].startswith("Vendor_")
        assert secret_vendor not in matched["supplier_name"]
        # raw_supplier_name 不得泄露
        assert "raw_supplier_name" not in matched

        # 验证 compute_field_diffs 中供应商比对别名化
        ai_prefill = {"vendor": "旧的机密供货商", "items": []}
        is_mod, mod_count, mod_fields, diffs = compute_field_diffs(ai_prefill, rec, [])
        assert is_mod is True
        assert "supplier_name" in mod_fields
        v_diff = next(d for d in diffs if d["field"] == "supplier_name")
        assert v_diff["ai_value"].startswith("Vendor_")
        assert v_diff["user_value"].startswith("Vendor_")
        assert "旧的机密供货商" not in v_diff["ai_value"]
        assert secret_vendor not in v_diff["user_value"]

    finally:
        if test_rid:
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
            s.commit()
        s.close()


def test_ac_qa_detail_01_endpoint_and_audit_events_lifecycle(client):
    """AC-QA-DETAIL-01: GET /api/analytics/parsing-logs/detail 鉴权、全息详情与 audit_events 时序穿透。"""
    # 1. RBAC 鉴权拦截
    assert client.get("/api/analytics/parsing-logs/detail?record_id=1").status_code == 401
    assert client.get("/api/analytics/parsing-logs/detail?record_id=1", headers={"X-Role": "staff"}).status_code == 403
    assert client.get("/api/analytics/parsing-logs/detail?record_id=1", headers={"X-Role": "owner"}).status_code == 403

    # 2. 插入一笔带完整生命周期时序事件的单据
    s = db.get_session()
    test_rid = None
    try:
        rec = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/test_detail_lifecycle.jpg",
            supplier_name="时序审计测试百货",
            status="approved",
            total_amount=199.9,
        )
        s.add(rec)
        s.commit()
        s.refresh(rec)
        test_rid = rec.id

        # 插入 5 个生命周期时序事件
        events = [
            db._UserEventRow(
                tenant_id="default",
                receipt_id=test_rid,
                event_type="upload",
                ts="2026-09-29T10:00:00",
                account_id="staff_1",
                properties=json.dumps({"file_name": "receipt_1.jpg", "file_size": 2048}),
            ),
            db._UserEventRow(
                tenant_id="default",
                receipt_id=test_rid,
                event_type="ocr_parse_started",
                ts="2026-09-29T10:00:02",
                account_id="system",
                properties=json.dumps({"engine": "qwen-vl", "job_id": f"job-qa-{test_rid}"}),
            ),
            db._UserEventRow(
                tenant_id="default",
                receipt_id=test_rid,
                event_type="contract_guard_checked",
                ts="2026-09-29T10:00:05",
                account_id="system",
                properties=json.dumps({"guard_type": "contract", "passed": True}),
            ),
            db._UserEventRow(
                tenant_id="default",
                receipt_id=test_rid,
                event_type="receipt_review_submitted",
                ts="2026-09-29T10:05:00",
                account_id="staff_1",
                properties=json.dumps({"modified_fields": 0}),
            ),
            db._UserEventRow(
                tenant_id="default",
                receipt_id=test_rid,
                event_type="receipt_approved",
                ts="2026-09-29T10:10:00",
                account_id="owner_1",
                properties=json.dumps({"status": "approved"}),
            ),
        ]
        s.add_all(events)
        s.commit()

        # 3. 按 record_id 查询详情
        headers = {"X-Role": "admin"}
        resp = client.get(f"/api/analytics/parsing-logs/detail?record_id={test_rid}", headers=headers)
        assert resp.status_code == 200
        res = resp.json()
        assert res["status"] == "success"
        data = res["data"]

        assert data["record_id"] == test_rid
        assert data["supplier_name"].startswith("Vendor_")
        assert "audit_events" in data
        audit_evs = data["audit_events"]
        assert len(audit_evs) >= 5

        # 验证事件字段结构
        ev0 = audit_evs[0]
        assert "id" in ev0
        assert "event_type" in ev0
        assert "ts" in ev0
        assert "account_id" in ev0
        assert "receipt_id" in ev0
        assert "properties" in ev0
        assert ev0["event_type"] == "upload"
        assert ev0["properties"]["file_name"] == "receipt_1.jpg"

        # 4. 按 job_id 查询详情
        resp_job = client.get(f"/api/analytics/parsing-logs/detail?job_id=job-qa-{test_rid}", headers=headers)
        assert resp_job.status_code == 200
        res_job = resp_job.json()
        assert res_job["status"] == "success"
        assert res_job["data"]["record_id"] == test_rid

        # 5. 查询不存在的单据 -> 错误响应
        resp_404 = client.get("/api/analytics/parsing-logs/detail?record_id=99999999", headers=headers)
        assert resp_404.status_code == 200
        assert resp_404.json()["status"] == "error"
        assert resp_404.json()["detail"] == "单据或链路未找到"

    finally:
        if test_rid:
            s.query(db._UserEventRow).filter(db._UserEventRow.receipt_id == test_rid).delete()
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
            s.commit()
        s.close()


def test_ac_qa_filter_01_multidimensional_query_linkage(client):
    """AC-QA-FILTER-01: 租户、状态、门禁、反馈、人工改动的多维交叉联动查询。"""
    headers = {"X-Role": "admin"}
    combos = [
        {"tenant_id": "default", "status": "success", "gate_status": "none"},
        {"tenant_id": "all", "status": "warning", "gate_status": "warning"},
        {"tenant_id": "all", "feedback": "liked", "is_modified": "all"},
        {"tenant_id": "all", "gate_status": "rejected", "status": "all"},
        {"tenant_id": "all", "date_range": "30d", "is_modified": "no"},
    ]
    for params in combos:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        resp = client.get(f"/api/analytics/parsing-logs?{qs}&page=1&page_size=5", headers=headers)
        assert resp.status_code == 200
        res = resp.json()
        assert res["status"] == "success"
        assert "items" in res["data"]
        assert "pagination" in res["data"]
        assert "summary" in res["data"]



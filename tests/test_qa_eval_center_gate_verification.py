"""
AI 效果观测与评测中心 - 解析日志与链路审计：门禁触发全量留痕与 UI 重构
QA 专项目标自动化验收测试套件 (QA Comprehensive Acceptance Test Suite)
严格核对 01-product-spec.md 中验收标准 (AC-API-01~05, AC-UI-01~06, AC-RED-01~02)。
规范红线：全局严禁使用任何 Emoji。
"""

import os
import re
import json
import urllib.request
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db
from app.services.parsing_logs import query_parsing_logs, _compute_summary


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def setup_qa_standard_receipts():
    """为 QA 测试套件注入 5 大标准门禁场景单据，并在测试后安全清理。"""
    s = db.get_session()
    rids = []
    try:
        # 1. 算术门禁告警
        r_math = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/qa_math.jpg",
            supplier_name="QA算术门禁商户",
            status="parsed_with_warnings",
            total_amount=200.0,
            math_warnings_json=json.dumps(["算术门禁: 明细合计(180.00) 与 总额(200.00) 差额 20.00"]),
        )
        s.add(r_math)

        # 2. 画质门禁告警
        r_quality = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/qa_quality.jpg",
            supplier_name="QA画质门禁商户",
            status="parsed_with_warnings",
            total_amount=300.0,
            quality_warnings_json=json.dumps(["画质门禁: 图面模糊且检出花码乱码"]),
        )
        s.add(r_quality)

        # 3. 安全门禁告警
        r_security = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/qa_security.jpg",
            supplier_name="QA安全门禁商户",
            status="parsed_with_warnings",
            total_amount=400.0,
            image_guard_json=json.dumps({"flagged": True, "reasons": ["检测到图内隐写指令文字"]}),
        )
        s.add(r_security)

        # 4. 契约门禁拦截
        r_contract = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/qa_contract.jpg",
            supplier_name="QA契约门禁商户",
            status="error",
            total_amount=500.0,
        )
        s.add(r_contract)

        # 5. 无门禁正常单据
        r_none = db._ReceiptRow(
            tenant_id="default",
            image_path="/demo/uploads/qa_none.jpg",
            supplier_name="QA正常平算商户",
            status="parsed",
            total_amount=600.0,
            math_warnings_json=json.dumps([]),
            quality_warnings_json=json.dumps([]),
            image_guard_json=json.dumps({"flagged": False, "reasons": []}),
        )
        s.add(r_none)

        s.commit()
        s.refresh(r_math)
        s.refresh(r_quality)
        s.refresh(r_security)
        s.refresh(r_contract)
        s.refresh(r_none)

        rids.extend([r_math.id, r_quality.id, r_security.id, r_contract.id, r_none.id])

        # 关联契约拦截决策日志
        d_contract = db._DecisionLogRow(
            receipt_id=r_contract.id,
            decision_type="extract",
            engine="vlm",
            model="qwen-vl",
            ai_value=json.dumps({"status": "gate_reject", "gate_err": "契约门禁: 必填供应商名称与明细项缺失"}),
            extra=json.dumps({"status": "gate_reject", "gate_err": "契约门禁: 必填供应商名称与明细项缺失"}),
        )
        s.add(d_contract)
        s.commit()

        yield {
            "math_id": r_math.id,
            "quality_id": r_quality.id,
            "security_id": r_security.id,
            "contract_id": r_contract.id,
            "none_id": r_none.id,
        }
    finally:
        for rid in rids:
            s.query(db._DecisionLogRow).filter(db._DecisionLogRow.receipt_id == rid).delete()
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == rid).delete()
        s.commit()
        s.close()


# -----------------------------------------------------------------------------
# 1. AC-API-01: API gate_info 完整数据契约校验
# -----------------------------------------------------------------------------

def test_qa_api_01_gate_info_contract_completeness(client):
    """
    AC-API-01: 校验返回的每条记录中必须包含完整的 gate_info 字典结构：
    - triggered (bool)
    - status (str: 'none' | 'warning' | 'rejected')
    - badge_label (str)
    - primary_type (str)
    - primary_type_label (str)
    - summary (str)
    - details (list of dict)
    """
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}
    resp = client.get("/api/analytics/parsing-logs?page=1&page_size=20", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "success"
    items = body["data"]["items"]
    assert len(items) >= 5, f"预期至少返回 5 条测试数据，实际为 {len(items)}"

    for idx, item in enumerate(items):
        assert "gate_info" in item, f"记录 #{item.get('record_id')} 缺少 gate_info 字段"
        gi = item["gate_info"]
        assert isinstance(gi["triggered"], bool), f"条目 {idx} triggered 必须为 bool"
        assert gi["status"] in ("none", "warning", "rejected"), f"条目 {idx} status 不合法: {gi['status']}"
        assert isinstance(gi["badge_label"], str) and len(gi["badge_label"]) > 0, f"条目 {idx} badge_label 不能为空"
        assert isinstance(gi["primary_type"], str), f"条目 {idx} primary_type 必须为 str"
        assert isinstance(gi["primary_type_label"], str), f"条目 {idx} primary_type_label 必须为 str"
        assert isinstance(gi["summary"], str) and len(gi["summary"]) > 0, f"条目 {idx} summary 不能为空"
        assert isinstance(gi["details"], list), f"条目 {idx} details 必须为 list"

        for didx, d in enumerate(gi["details"]):
            assert "type" in d, f"条目 {idx} details[{didx}] 缺少 type"
            assert "type_label" in d, f"条目 {idx} details[{didx}] 缺少 type_label"
            assert "level" in d, f"条目 {idx} details[{didx}] 缺少 level"
            assert d["level"] in ("warning", "rejected"), f"条目 {idx} details[{didx}] level 异常: {d['level']}"
            assert "reason" in d and len(d["reason"]) > 0, f"条目 {idx} details[{didx}] reason 不能为空"
            assert "source" in d, f"条目 {idx} details[{didx}] 缺少 source"


# -----------------------------------------------------------------------------
# 2. AC-API-03 & 04: 门禁多源归因准确性测试 (涵盖 5 大场景: math, quality, contract, security, none)
# -----------------------------------------------------------------------------

def test_qa_api_gate_attribution_five_scenarios(client, setup_qa_standard_receipts):
    """
    AC-API-03 & AC-API-04: 多源归因准确性断言，完整覆盖：
    1. 算术门禁 (math): math_warnings_json
    2. 画质门禁 (quality): quality_warnings_json
    3. 安全门禁 (security): image_guard_json
    4. 契约门禁 (contract): decision_log 中的 gate_reject
    5. 无门禁场景 (none): 正常平算无警告单据
    """
    seeds = setup_qa_standard_receipts
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}

    # 验证 1: 算术门禁
    resp_m = client.get(f"/api/analytics/parsing-logs?search={seeds['math_id']}", headers=headers)
    assert resp_m.status_code == 200
    item_m = next(x for x in resp_m.json()["data"]["items"] if x["record_id"] == seeds["math_id"])
    assert item_m["gate_info"]["triggered"] is True
    assert item_m["gate_info"]["status"] == "warning"
    assert item_m["gate_info"]["primary_type"] == "math"
    assert item_m["gate_info"]["primary_type_label"] == "算术门禁"
    assert "[门禁告警: 算术门禁]" in item_m["gate_info"]["badge_label"]
    assert "差额 20.00" in item_m["gate_info"]["summary"]

    # 验证 2: 画质门禁
    resp_q = client.get(f"/api/analytics/parsing-logs?search={seeds['quality_id']}", headers=headers)
    assert resp_q.status_code == 200
    item_q = next(x for x in resp_q.json()["data"]["items"] if x["record_id"] == seeds["quality_id"])
    assert item_q["gate_info"]["triggered"] is True
    assert item_q["gate_info"]["status"] == "warning"
    assert item_q["gate_info"]["primary_type"] == "quality"
    assert item_q["gate_info"]["primary_type_label"] == "画质门禁"
    assert "[门禁告警: 画质门禁]" in item_q["gate_info"]["badge_label"]
    assert "模糊且检出花码" in item_q["gate_info"]["summary"]

    # 验证 3: 安全门禁
    resp_s = client.get(f"/api/analytics/parsing-logs?search={seeds['security_id']}", headers=headers)
    assert resp_s.status_code == 200
    item_s = next(x for x in resp_s.json()["data"]["items"] if x["record_id"] == seeds["security_id"])
    assert item_s["gate_info"]["triggered"] is True
    assert item_s["gate_info"]["status"] == "warning"
    assert item_s["gate_info"]["primary_type"] == "security"
    assert item_s["gate_info"]["primary_type_label"] == "安全门禁"
    assert "[门禁告警: 安全门禁]" in item_s["gate_info"]["badge_label"]
    assert "隐写指令文字" in item_s["gate_info"]["summary"]

    # 验证 4: 契约门禁拦截
    resp_c = client.get(f"/api/analytics/parsing-logs?search={seeds['contract_id']}", headers=headers)
    assert resp_c.status_code == 200
    item_c = next(x for x in resp_c.json()["data"]["items"] if x["record_id"] == seeds["contract_id"])
    assert item_c["gate_info"]["triggered"] is True
    assert item_c["gate_info"]["status"] == "rejected"
    assert item_c["gate_info"]["primary_type"] == "contract"
    assert item_c["gate_info"]["primary_type_label"] == "契约门禁"
    assert "[门禁拦截: 契约门禁]" in item_c["gate_info"]["badge_label"]
    assert "必填供应商名称与明细项缺失" in item_c["gate_info"]["summary"]

    # 验证 5: 平算无门禁
    resp_n = client.get(f"/api/analytics/parsing-logs?search={seeds['none_id']}", headers=headers)
    assert resp_n.status_code == 200
    item_n = next(x for x in resp_n.json()["data"]["items"] if x["record_id"] == seeds["none_id"])
    assert item_n["gate_info"]["triggered"] is False
    assert item_n["gate_info"]["status"] == "none"
    assert item_n["gate_info"]["badge_label"] == "[平算无门禁]"
    assert item_n["gate_info"]["summary"] == "算术契约检验全部通过"
    assert len(item_n["gate_info"]["details"]) == 0


# -----------------------------------------------------------------------------
# 3. AC-API-02: gate_status 五维筛选过滤测试
# -----------------------------------------------------------------------------

def test_qa_api_02_gate_status_filters_matrix(client):
    """
    AC-API-02: 校验 gate_status 五大枚举过滤参数：
    - all: 全部记录
    - triggered: 仅 triggered == True
    - none: 仅 triggered == False 且 status == 'none'
    - rejected: 仅 status == 'rejected'
    - warning: 仅 status == 'warning'
    """
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}
    filter_expectations = {
        "all": lambda gi: True,
        "triggered": lambda gi: gi["triggered"] is True and gi["status"] in ("warning", "rejected"),
        "none": lambda gi: gi["triggered"] is False and gi["status"] == "none",
        "rejected": lambda gi: gi["status"] == "rejected",
        "warning": lambda gi: gi["status"] == "warning",
    }

    for gs, rule in filter_expectations.items():
        resp = client.get(f"/api/analytics/parsing-logs?gate_status={gs}&page=1&page_size=20", headers=headers)
        assert resp.status_code == 200, f"gate_status={gs} 接口请求失败"
        body = resp.json()
        assert body["status"] == "success"
        items = body["data"]["items"]
        total_items = body["data"]["pagination"]["total_items"]
        assert total_items >= len(items)

        for item in items:
            gi = item["gate_info"]
            assert rule(gi), f"gate_status={gs} 过滤出现不符合记录: id={item['record_id']}, gate_info={gi}"


# -----------------------------------------------------------------------------
# 4. AC-API-05: 大盘汇总指标 (gate_triggered_count, gate_trigger_rate, gate_breakdown)
# -----------------------------------------------------------------------------

def test_qa_api_05_summary_gate_metrics(client):
    """
    AC-API-05: 校验汇总指标包含门禁统计要素，且具备正确的数值范围与类型：
    - gate_triggered_count >= 0
    - 0.0 <= gate_trigger_rate <= 1.0
    - gate_breakdown 包含 math, contract, quality, security
    """
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}
    resp = client.get("/api/analytics/parsing-logs?page=1&page_size=5", headers=headers)
    assert resp.status_code == 200
    summary = resp.json()["data"]["summary"]

    assert "gate_triggered_count" in summary
    assert "gate_trigger_rate" in summary
    assert "gate_breakdown" in summary

    gtc = summary["gate_triggered_count"]
    gtr = summary["gate_trigger_rate"]
    gbd = summary["gate_breakdown"]

    assert isinstance(gtc, int) and gtc >= 0
    assert isinstance(gtr, (int, float)) and 0.0 <= gtr <= 1.0
    assert isinstance(gbd, dict)

    for k in ("math", "contract", "quality", "security"):
        assert k in gbd, f"gate_breakdown 缺少门禁类型: {k}"
        assert isinstance(gbd[k], int) and gbd[k] >= 0

    breakdown_sum = sum(gbd.values())
    assert breakdown_sum >= 0


# -----------------------------------------------------------------------------
# 5. AC-UI-01: 校验副标题彻底移除
# -----------------------------------------------------------------------------

def test_qa_ui_01_subtitle_thoroughly_removed():
    """
    AC-UI-01: 校验 sec-parsing-logs 容器内不存在任何 .analytics-section-desc。
    """
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    sec_start = html.find('id="sec-parsing-logs"')
    assert sec_start != -1, "未找到 id=sec-parsing-logs 容器"
    sec_end = html.find('id="sec-accuracy"', sec_start)
    if sec_end == -1:
        sec_end = sec_start + 15000
    sec_content = html[sec_start:sec_end]

    assert "analytics-section-desc" not in sec_content, "sec-parsing-logs 内部仍残留 analytics-section-desc 副标题"
    assert "单据全生命周期图片解析审计" not in sec_content, "原副标题文案仍残留"


# -----------------------------------------------------------------------------
# 6. AC-UI-02: 校验 Filter 控件尺寸统一规范 (34px, 13px, 6px 10px, 6px)
# -----------------------------------------------------------------------------

def test_qa_ui_02_filter_controls_uniformity():
    """
    AC-UI-02: 校验筛选控件标准规范类 .parsing-log-filter-control 与控件应用：
    - height: 34px !important
    - font-size: 13px !important
    - padding: 6px 10px !important
    - border-radius: 6px !important
    - 租户、解析状态、门禁状态、用户反馈、人工改动、时间跨度、搜索框等 7 大控件均使用统一样式
    """
    # F3 S4-07 强制 CSP 起 index.html 内联 <style> 外移至 index-head.css /
    # csp-html-styles.css。等效判据＝模板 + 外链样式表联合文本（判据不放宽）。
    base = os.path.join(os.path.dirname(__file__), "../demo")
    html = "\n".join(
        open(os.path.join(base, rel), "r", encoding="utf-8").read()
        for rel in ("templates/index.html", "static/css/index-head.css",
                    "static/css/csp-html-styles.css"))

    # CSS 规范定义
    assert ".parsing-log-filter-control {" in html
    assert "height: 34px !important;" in html
    assert "font-size: 13px !important;" in html
    assert "padding: 6px 10px !important;" in html
    assert "border-radius: 6px !important;" in html

    # 按钮规范类
    assert ".parsing-log-filter-btn {" in html
    assert "height: 34px !important;" in html

    # 控件统一引用检验
    # 2026-10-10 契约对齐：租户过滤重构为可见 datalist 输入 #analyticsTenantSelect
    # （挂统一类）；#parsingLogTenantSelect 降为 display:none 的兼容回退读点，
    # 不再承担可见过滤控件职责，从统一类断言清单移除。
    expected_controls = [
        'id="analyticsTenantSelect"',
        'id="parsingLogStatusSelect"',
        'id="parsingLogGateSelect"',
        'id="parsingLogFeedbackSelect"',
        'id="parsingLogModifiedSelect"',
        'id="parsingLogDateRangeSelect"',
        'id="parsingLogSearchInput"',
    ]
    for ctrl in expected_controls:
        pattern = re.compile(rf'{ctrl}[^>]*class="[^"]*parsing-log-filter-control[^"]*"')
        assert pattern.search(html), f"控件 {ctrl} 未挂载统一规范类 parsing-log-filter-control"


# -----------------------------------------------------------------------------
# 7. AC-UI-03: 校验门禁筛选下拉框及 5 个选项
# -----------------------------------------------------------------------------

def test_qa_ui_03_gate_select_dropdown_options():
    """
    AC-UI-03: 校验 parsingLogGateSelect 下拉选择器包含 5 个标准选项：
    - all: 全部门禁状态
    - triggered: 已触发门禁 (告警/拦截)
    - none: 无门禁 (平算通过)
    - rejected: 仅门禁拦截 (Reject)
    - warning: 仅门禁告警 (Warning)
    """
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert '<select id="parsingLogGateSelect"' in html
    assert '<option value="all">全部门禁状态</option>' in html
    assert '<option value="triggered">已触发门禁 (告警/拦截)</option>' in html
    assert '<option value="none">无门禁 (平算通过)</option>' in html
    assert '<option value="rejected">仅门禁拦截 (Reject)</option>' in html
    assert '<option value="warning">仅门禁告警 (Warning)</option>' in html


# -----------------------------------------------------------------------------
# 8. AC-UI-04 & 05: 校验表格 13 列排布、独立门禁列与 min-width 1680px
# -----------------------------------------------------------------------------

def test_qa_ui_04_and_05_table_columns_and_safe_width():
    """
    AC-UI-04 & AC-UI-05: 表格包含第 7 列“门禁状态与原因”，容器 min-width 为 1680px，整表 13 列。
    """
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    css = "\n".join(
        open(os.path.join(os.path.dirname(__file__), "../demo", rel), "r", encoding="utf-8").read()
        for rel in ("static/css/index-head.css", "static/css/csp-html-styles.css"))

    # 1. 最小安全宽度（F3 CSP 起 style 属性改为 csp-s-N 类 + 外链规则，等效判据）
    _i = html.find('id="parsingLogsTable"')
    assert _i != -1, "parsingLogsTable 必须存在"
    _tag = html[html.rfind("<table", 0, _i):html.find(">", _i)]
    import re as _re
    _m = _re.search(r'csp-s-(\d+)', _tag)
    assert _m, "parsingLogsTable 必须携带 csp-s-N 宽度类"
    assert ('.csp-s-%s{min-width:1680px' % _m.group(1)) in css.replace(" ", ""), \
        "parsingLogsTable 的宽度类必须在外链样式中声明 min-width:1680px"

    # 2. 表头必须包含 13 个 th，且第 7 个为“门禁状态与原因”
    table_match = re.search(r'<table[^>]*id="parsingLogsTable"[^>]*>(.*?)</table>', html, re.DOTALL)
    assert table_match is not None, "未找到 parsingLogsTable 表格"
    table_content = table_match.group(1)

    th_list = re.findall(r'<th[^>]*>(.*?)</th>', table_content, re.DOTALL)
    assert len(th_list) == 13, f"表格列数必须为 13 列，当前为 {len(th_list)} 列"
    assert "门禁状态与原因" in th_list[6], f"第 7 列必须为门禁状态与原因，当前为: {th_list[6]}"

    # 3. 前端 JS 渲染逻辑覆盖 13 列与门禁列
    # F4/F5 S4-11：解析日志渲染实现已随域迁至 parsing_logs.js；联合全文口径。
    js = "\n".join(
        open(os.path.join(os.path.dirname(__file__), rel), "r", encoding="utf-8").read()
        for rel in ("../demo/static/js/main.js", "../demo/static/js/parsing_logs.js"))
    assert "colspan=\"13\"" in js, "表格加载/空状态必须占满 13 列"
    assert "it.gate_info" in js, "JS 必须消费 it.gate_info"
    assert "gateBadgeHtml" in js, "JS 必须构造 gateBadgeHtml"


# -----------------------------------------------------------------------------
# 9. AC-UI-06: 校验详情模态框顶部门禁徽章与 Tab 3 四大审计模块
# -----------------------------------------------------------------------------

def test_qa_ui_06_detail_modal_gate_elements():
    """
    AC-UI-06: 模态框顶部元数据条包含 plMetaGateBadge，Tab 3 深度升级为四大审计卡片：
    - plGateDecisionBody (门禁综合触发决策流水)
    - plMathWarningsBody (算术一致性检查)
    - plContractGuardBody (契约门禁与结构有效性)
    - plQualityWarningsBody (画质门禁)
    - plImageGuardBody (安全隐写门禁)
    """
    html_path = os.path.join(os.path.dirname(__file__), "../demo/templates/index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 顶部元数据
    assert 'id="plMetaGateBadge"' in html, "模态框顶部缺少 plMetaGateBadge 门禁结论徽章"

    # Tab 3 结构
    assert "Tab 3: 质检与门禁规则审计" in html
    assert 'id="plGateDecisionBody"' in html
    assert 'id="plMathWarningsBody"' in html
    assert 'id="plContractGuardBody"' in html
    assert 'id="plQualityWarningsBody"' in html
    assert 'id="plImageGuardBody"' in html

    # JS 渲染联动
    # F4/F5 S4-11：解析日志渲染实现已随域迁至 parsing_logs.js；联合全文口径。
    js = "\n".join(
        open(os.path.join(os.path.dirname(__file__), rel), "r", encoding="utf-8").read()
        for rel in ("../demo/static/js/main.js", "../demo/static/js/parsing_logs.js"))

    assert "document.getElementById('plMetaGateBadge')" in js
    assert "document.getElementById('plGateDecisionBody')" in js
    assert "document.getElementById('plMathWarningsBody')" in js
    assert "document.getElementById('plContractGuardBody')" in js
    assert "document.getElementById('plQualityWarningsBody')" in js
    assert "document.getElementById('plImageGuardBody')" in js


# -----------------------------------------------------------------------------
# 10. AC-RED-01: 全局无 Emoji 静态与动态扫描
# -----------------------------------------------------------------------------

def test_qa_red_01_zero_emoji_static_and_dynamic(client):
    """
    AC-RED-01: 全局严禁使用任何 Emoji 图标字符。
    1. 静态扫描修改的代码、模板与脚本。
    2. 动态扫描 API 返回的 JSON 文本。
    """
    emoji_regex = re.compile(
        "[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u203c-\u2049]",
        flags=re.UNICODE
    )

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    scan_targets = [
        "demo/app/services/parsing_logs.py",
        "demo/app/api_admin.py",
        "demo/templates/index.html",
        "demo/static/js/main.js",
        "tests/test_analytics_parsing_logs.py",
        "tests/test_qa_eval_center_gate_verification.py",
    ]

    violations = []
    for rel_path in scan_targets:
        full_path = os.path.join(base_dir, rel_path)
        if not os.path.exists(full_path):
            continue
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            for line_idx, line in enumerate(f, 1):
                if "parsing_logs" in rel_path or "api_admin" in rel_path or "qa_" in rel_path:
                    m = emoji_regex.findall(line)
                    if m:
                        violations.append((rel_path, line_idx, m, line.strip()))
                elif "index.html" in rel_path:
                    if 50 <= line_idx <= 120 or 2180 <= line_idx <= 2220 or 2500 <= line_idx <= 2980:
                        m = emoji_regex.findall(line)
                        if m:
                            violations.append((rel_path, line_idx, m, line.strip()))
                elif "main.js" in rel_path:
                    if line_idx >= 16640:
                        m = emoji_regex.findall(line)
                        if m:
                            violations.append((rel_path, line_idx, m, line.strip()))

    assert len(violations) == 0, f"发现静态 Emoji 违规: {violations}"

    # 动态扫描
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}
    resp = client.get("/api/analytics/parsing-logs?page=1&page_size=20", headers=headers)
    assert resp.status_code == 200
    raw_text = resp.text
    dynamic_violations = emoji_regex.findall(raw_text)
    assert len(dynamic_violations) == 0, f"发现 API 动态响应 Emoji 违规: {dynamic_violations}"


# -----------------------------------------------------------------------------
# 11. AC-RED-02: 向后兼容性容错测试
# -----------------------------------------------------------------------------

def test_qa_red_02_backward_compatibility_graceful_fallback(client):
    """
    AC-RED-02: 历史无决策日志或字段为 NULL 的单据必须安全兜底判定为 [平算无门禁]，不得返回 500。
    """
    s = db.get_session()
    test_rid = None
    try:
        legacy_rec = db._ReceiptRow(
            tenant_id="legacy_tenant",
            image_path="/demo/uploads/legacy_sample.jpg",
            supplier_name="历史旧数据商户",
            status="parsed",
            total_amount=99.9,
            math_warnings_json=None,
            quality_warnings_json=None,
            image_guard_json=None,
        )
        s.add(legacy_rec)
        s.commit()
        s.refresh(legacy_rec)
        test_rid = legacy_rec.id

        resp = client.get(f"/api/analytics/parsing-logs?search={test_rid}", headers={"X-Role": "admin"})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        matched = next(x for x in items if x["record_id"] == test_rid)
        gi = matched["gate_info"]

        assert gi["triggered"] is False
        assert gi["status"] == "none"
        assert gi["badge_label"] == "[平算无门禁]"
        assert gi["summary"] == "算术契约检验全部通过"
        assert gi["details"] == []

    finally:
        if test_rid:
            s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
            s.commit()
        s.close()


# -----------------------------------------------------------------------------
# 12. 常驻服务 (Port 15010) 实测联调验证
# -----------------------------------------------------------------------------

def test_qa_live_service_port_15010_e2e():
    """
    对常驻运行在 15010 端口的实盘服务进行端到端链路验证。
    """
    url = "http://127.0.0.1:15010/api/analytics/parsing-logs?gate_status=rejected&page_size=2"
    req = urllib.request.Request(url, headers={"X-Role": "admin"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            assert response.status == 200
            content = response.read().decode("utf-8")
            data = json.loads(content)
            assert data["status"] == "success"
            summary = data["data"]["summary"]
            assert "gate_triggered_count" in summary
            assert "gate_trigger_rate" in summary
            assert "gate_breakdown" in summary
            assert summary["gate_triggered_count"] >= 0
            items = data["data"]["items"]
            for it in items:
                assert it["gate_info"]["status"] == "rejected"
                assert it["gate_info"]["triggered"] is True
    except urllib.error.URLError:
        pytest.skip("Port 15010 service is not running or not reachable")

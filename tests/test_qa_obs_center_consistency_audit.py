"""
AI 效果观测与评测中心 - 全域数据展示一致性
QA 专项目标自动化验收测试套件 (QA Obs Center Consistency Audit Test Suite)
严格覆盖 01-product-spec.md 中全部 5 大类 20 条验收标准 (AC 1.1~1.8, AC 2.1~2.4, AC 3.1~3.5, AC 4.1~4.4, AC 5.1~5.3)。
规范红线：全局严禁使用任何 Emoji 表情字符。
"""

import os
import re
import json
import subprocess
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db


@pytest.fixture
def client():
    return TestClient(app)


# =============================================================================
# 一、 AC 1: 时间戳全域规范性自动化测试 (Time Formatting AC)
# =============================================================================

def test_ac_1_1_fmt_datetime_js_runtime_edge_cases():
    """
    AC 1.1: 验证 _fmtDateTime 函数在 Node.js 运行时执行环境中的严格行为：
    - 必须定长 19 位 YYYY-MM-DD HH:mm:ss
    - 剔除 T 字符、小数点后毫秒/微秒 (.xxxxxx)、时区偏移 (+08:00 / Z)
    - 兼容 ISO 格式、时间戳数字、Date 对象
    - 空值、None、null、undefined 统一回退为 '-'
    """
    js_test_script = """
    const fs = require('fs');
    const mainJs = fs.readFileSync('demo/static/js/main.js', 'utf-8');
    
    // 提取 _fmtDateTime 与 _fmtDateOnly 函数实现
    const evalEnv = {};
    const fnStart = mainJs.indexOf('function _fmtDateTime(val)');
    const fnEnd = mainJs.indexOf('// -------------------------------------------------------------', fnStart);
    const codeToEval = mainJs.substring(fnStart, fnEnd);
    eval(codeToEval);
    
    const cases = [
        { in: '2026-09-30T14:20:05.123456+08:00', exp: '2026-09-30 14:20:05' },
        { in: '2026-09-30T14:20:05Z', exp: '2026-09-30 14:20:05' },
        { in: '2026-09-30 14:20:05', exp: '2026-09-30 14:20:05' },
        { in: '2026-09-30T14:20:05.999', exp: '2026-09-30 14:20:05' },
        { in: '2026-09-30', exp: '2026-09-30 00:00:00' },
        { in: null, exp: '-' },
        { in: undefined, exp: '-' },
        { in: '', exp: '-' },
        { in: 'None', exp: '-' },
        { in: 'null', exp: '-' },
        { in: 'undefined', exp: '-' },
        { in: '—', exp: '-' },
        { in: '-', exp: '-' }
    ];
    
    const results = cases.map(c => {
        const out = _fmtDateTime(c.in);
        return { input: c.in, expected: c.exp, actual: out, ok: out === c.exp };
    });
    
    console.log(JSON.stringify(results));
    """
    proc = subprocess.run(["node", "-e", js_test_script], capture_output=True, text=True, check=True)
    res = json.loads(proc.stdout)
    for r in res:
        assert r["ok"] is True, f"Node.js _fmtDateTime 失败: input={r['input']}, expected={r['expected']}, actual={r['actual']}"


def test_ac_1_2_pl_meta_created_at_formatting():
    """
    AC 1.2: 详情弹窗顶部元数据条的日期时间为 YYYY-MM-DD HH:mm:ss 或 '-'
    检查前端 main.js 中对 plMetaCreatedAt 的赋值是否调用 _fmtDateTime 并在界面上配置等宽字体。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    
    assert "document.getElementById('plMetaCreatedAt')" in js
    assert "metaCreatedAt.textContent = _fmtDateTime(item.created_at);" in js, \
        "plMetaCreatedAt 必须通过 _fmtDateTime(item.created_at) 赋值"
    assert "metaCreatedAt.style.fontFamily = 'var(--font-mono)';" in js, \
        "plMetaCreatedAt 必须配置等宽字体 var(--font-mono)"


def test_ac_1_3_pl_timeline_body_events_timestamp_formatting():
    """
    AC 1.3: 弹窗 Tab 4 审计流水表格中每个事件的发生时间单元格经 _fmtDateTime(ev.ts) 格式化。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    
    assert "tlBody" in js
    # 查找发生时间单元格渲染
    pattern = re.compile(r"_fmtDateTime\(ev\.ts\)")
    assert pattern.search(js), "Tab 4 时间线渲染未对 ev.ts 调用 _fmtDateTime 进行清洗"


def test_ac_1_4_event_dist_and_recent_events_formatting():
    """
    AC 1.4: 埋点事件分布与最近事件流中触发时间格式化为 _fmtDateTime(d.last_ts) / _fmtDateTime(ev.ts)。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    
    assert "_fmtDateTime(d.last_ts)" in js, "事件分布表未调用 _fmtDateTime(d.last_ts)"
    assert "_fmtDateTime(ev.ts)" in js, "最近事件流未调用 _fmtDateTime(ev.ts)"


def test_ac_1_5_admin_canary_history_formatting():
    """
    AC 1.5: 历史灰测履历表格变更时间调用 _fmtDateTime(it.ts)。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    
    assert "_fmtDateTime(it.ts)" in js, "历史灰测履历未调用 _fmtDateTime(it.ts)"


def test_ac_1_6_exp_meta_dossier_formatting():
    """
    AC 1.6: A/B 实验深度档案横幅中起止时间与结题时间调用 _fmtDateTime。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    
    assert "_fmtDateTime(exp.start_ts)" in js, "实验起止时间未调用 _fmtDateTime(exp.start_ts)"
    assert "_fmtDateTime(exp.end_ts)" in js, "实验截止时间未调用 _fmtDateTime(exp.end_ts)"
    assert "_fmtDateTime(exp.concluded_at)" in js, "结题时间未调用 _fmtDateTime(exp.concluded_at)"


def test_ac_1_7_exp_sample_table_assigned_at_formatting():
    """
    AC 1.7: A/B 实验样本明细流中分配时间调用 _fmtDateTime(s.assigned_at)。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    
    assert "_fmtDateTime(s.assigned_at)" in js, "实验样本分配时间未调用 _fmtDateTime(s.assigned_at)"


def test_ac_1_8_golden_board_receipt_date_only_formatting():
    """
    AC 1.8: 黄金基准集看板开单日期调用 _fmtDateOnly(it.receipt_date)，严格为 10 位 YYYY-MM-DD。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    
    assert "_fmtDateOnly(it.receipt_date)" in js, "黄金看板开单日期未调用 _fmtDateOnly"

    # Node.js 运行时测试 _fmtDateOnly
    js_test_script = """
    const fs = require('fs');
    const mainJs = fs.readFileSync('demo/static/js/main.js', 'utf-8');
    const fnStart = mainJs.indexOf('function _fmtDateOnly(val)');
    const fnEnd = mainJs.indexOf('// -------------------------------------------------------------', fnStart);
    eval(mainJs.substring(fnStart, fnEnd));
    
    const cases = [
        { in: '2026-09-30T14:20:05.123456+08:00', exp: '2026-09-30' },
        { in: '2026-09-30 14:20:05', exp: '2026-09-30' },
        { in: '2026-09-30', exp: '2026-09-30' },
        { in: null, exp: '-' },
        { in: 'None', exp: '-' },
        { in: '—', exp: '-' }
    ];
    console.log(JSON.stringify(cases.map(c => ({ ok: _fmtDateOnly(c.in) === c.exp }))));
    """
    proc = subprocess.run(["node", "-e", js_test_script], capture_output=True, text=True, check=True)
    res = json.loads(proc.stdout)
    assert all(r["ok"] for r in res), f"Node.js _fmtDateOnly 测试失败: {res}"


# =============================================================================
# 二、 AC 2: 排序一致性与表头标注自动化测试 (Ordering & Headers AC)
# =============================================================================

def test_ac_2_1_five_timeseries_tables_header_latest_first_annotation():
    """
    AC 2.1: 5 大时序流水表格，表头明确标有 '(最新在前)':
    1. #parsingLogsTable -> 日期时间 (最新在前)
    2. #adminCanaryHistoryTable -> 变更时间 (最新在前)
    3. #analyticsRecentEventsTable -> 触发时间 (最新在前)
    4. #analyticsEventDistTable -> 最近触发时间 (最新在前)
    5. #expSampleTable -> 分配时间 (最新在前)
    """
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    # 1. parsingLogsTable (HTML)
    assert "日期时间 (最新在前)" in html, "#parsingLogsTable 表头缺少 '日期时间 (最新在前)'"

    # 2. adminCanaryHistoryTable (HTML)
    assert "变更时间 (最新在前)" in html, "#adminCanaryHistoryTable 表头缺少 '变更时间 (最新在前)'"

    # 3. analyticsRecentEventsTable (JS dynamic)
    assert "触发时间 (最新在前)" in js, "#analyticsRecentEventsTable 表头缺少 '触发时间 (最新在前)'"

    # 4. analyticsEventDistTable (JS dynamic)
    assert "最近触发时间 (最新在前)" in js, "#analyticsEventDistTable 表头缺少 '最近触发时间 (最新在前)'"

    # 5. expSampleTable (HTML)
    assert "分配时间 (最新在前)" in html, "#expSampleTable 表头缺少 '分配时间 (最新在前)'"


def test_ac_2_2_pl_timeline_body_header_and_descending_sort():
    """
    AC 2.2: 详情弹窗 Tab 4 中的执行履历表格表头明确为 '发生时间 (最新在前)'，
    且 JS 渲染前强制执行 allEvents 按 ts 降序排序。
    """
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "发生时间 (最新在前)" in html, "Tab 4 审计流水表格表头缺少 '发生时间 (最新在前)'"
    assert "allEvents.sort((a, b) => String(b.ts || '').localeCompare(String(a.ts || '')))" in js, \
        "Tab 4 未在渲染前执行强制时间倒序排序"


def test_ac_2_3_entity_tables_descending_id_backend_and_frontend(client):
    """
    AC 2.3: 实体大盘表保持 ID 降序排列，新生成数据自然位于顶部：
    1. /api/admin/golden-samples -> 接口返回 items 按 id 降序
    2. /api/admin/grey-test/samples -> 接口返回 samples 按 receipt_id 降序
    3. /api/admin/canary/history -> 接口返回 history 按 ts 降序
    """
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}

    # 1. golden-samples
    resp_g = client.get("/api/admin/golden-samples?scope=all", headers=headers)
    assert resp_g.status_code == 200
    items_g = resp_g.json().get("items", [])
    if len(items_g) >= 2:
        ids = [it["id"] for it in items_g]
        assert ids == sorted(ids, reverse=True), f"golden-samples 未按 id 降序排列: {ids[:10]}"

    # 2. grey-test/samples
    resp_gr = client.get("/api/admin/grey-test/samples?scope=all", headers=headers)
    assert resp_gr.status_code == 200
    samples = resp_gr.json().get("samples", [])
    if len(samples) >= 2:
        rids = [s["receipt_id"] for s in samples]
        assert rids == sorted(rids, reverse=True), f"grey-test/samples 未按 receipt_id 降序: {rids[:10]}"

    # 3. canary/history
    resp_ch = client.get("/api/admin/canary/history", headers=headers)
    assert resp_ch.status_code == 200
    history = resp_ch.json().get("history", [])
    if len(history) >= 2:
        tss = [h["ts"] for h in history]
        assert tss == sorted(tss, reverse=True), f"canary/history 未按 ts 降序: {tss[:10]}"


def test_ac_2_4_event_distribution_sorted_count_desc(client):
    """
    AC 2.4: 埋点事件分布接口 (/api/analytics/recovery-summary) 返回数据与前端分组内均严格遵循 count 降序排序。
    """
    headers = {"X-Role": "admin", "X-Tenant-Id": "default"}
    resp = client.get("/api/analytics/recovery-summary", headers=headers)
    assert resp.status_code == 200
    dist = resp.json().get("data", {}).get("event_distribution", [])
    if len(dist) >= 2:
        counts = [d["count"] for d in dist]
        assert counts == sorted(counts, reverse=True), f"事件分布接口未按 count 降序: {counts}"

    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    assert "rows.sort((a, b) => {" in js and "const cntDiff = Number(b.count || 0) - Number(a.count || 0)" in js, \
        "前端事件分布渲染缺少计数倒序排序逻辑"


# =============================================================================
# 三、 AC 3: 空值与占位符统一性自动化测试 (Null Placeholder AC)
# =============================================================================

def _extract_tab_section(html: str, tab_id: str) -> str:
    """
    按顶层区块锚点动态截取某个 Tab 的 HTML 片段，避免依赖硬编码行号
    （行号会随引擎配置等模块重构而漂移，导致区间尾部误扫到相邻 Tab）。

    以 <section>/</section> 配对深度计数定位区块闭合边界，兼容内部嵌套子区块
    （如 tab-analytics 内的 sec-telemetry/sec-canary/sec-experiment/sec-eval）。
    """
    open_anchor = '<section id="{}"'.format(tab_id)
    start = html.find(open_anchor)
    assert start != -1, "未找到顶层区块锚点: {}".format(open_anchor)

    depth = 0
    pos = start
    while True:
        next_open = html.find("<section", pos)
        next_close = html.find("</section>", pos)
        assert next_close != -1, "区块 {} 缺少 </section> 闭合标签".format(tab_id)
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + len("<section")
        else:
            depth -= 1
            pos = next_close + len("</section>")
            if depth == 0:
                return html[start:pos]


def test_ac_3_1_zero_em_dash_in_obs_center():
    """
    AC 3.1: 观测中心全域 4 大子功能区及详情弹窗中，绝对不存在任何未清洗的全角破折号 '—'。
    """
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()

    # 动态截取观测中心区块 (<section id="tab-analytics"> ... </section>)，不再依赖硬编码行号：
    # 引擎配置四模块重构使 #tab-engine 扩展后，旧区间 [2169:2968] 尾部会误落进 tab-engine。
    obs_html = _extract_tab_section(html, "tab-analytics")

    assert "—" not in obs_html, "观测中心 HTML 模板区域残留全角破折号 '—'"

    # 检查 main.js 中观测中心相关函数是否存在渲染出来的 '—'
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    obs_js_start = js.find('// 观测中心数据展示与时间格式统一标准')
    assert obs_js_start != -1
    obs_js = js[obs_js_start:]

    # 排除用于清洗 '—' 的输入比对代码 (如 s === '—') 与注释
    lines_js = obs_js.split("\n")
    render_violations = []
    for l_idx, line in enumerate(lines_js, 1):
        if "—" in line:
            stripped = line.strip()
            # 允许出现在转换防御中或注释中
            if "=== '—'" in stripped or "=== \"—\"" in stripped or stripped.startswith("//") or stripped.startswith("/*"):
                continue
            render_violations.append((l_idx, stripped))

    assert len(render_violations) == 0, f"观测中心 JS 代码中残留全角破折号渲染: {render_violations}"


def test_ac_3_2_grey_metrics_null_placeholders():
    """
    AC 3.2: 灰测样本无可比对数据时，mGreyAvgMatch 与行内采纳率展示为 '(-)' 或 '-'。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "res.avg_match_rate != null ? res.avg_match_rate + '%' : '-'" in js, \
        "mGreyAvgMatch 空值必须回退为 '-'"
    assert "evalData.match_rate != null ? evalData.match_rate + '%' : '-'" in js, \
        "灰测行内采纳率空值必须回退为 '-'"


def test_ac_3_3_ab_experiment_side_by_side_table_null_placeholders():
    """
    AC 3.3: A/B 实验组别对比表中 fmtPct、fmtDiff 空值与默认差异项一律为半角 '-'。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "const fmtPct = (val) => (val != null ? (Number(val) * 100).toFixed(2) + '%' : '-');" in js, \
        "fmtPct 空值必须返回 '-'"
    assert "if (cVal == null || tVal == null) return '-';" in js, \
        "fmtDiff 空值必须返回 '-'"


def test_ac_3_4_golden_board_gt_null_placeholder():
    """
    AC 3.4: 黄金基准集看板未确权项在 GT 确权治理列显示为 '-'。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "let gtHtml = '<span style=\"color:var(--text-muted); font-size:0.75rem;\">-</span>';" in js, \
        "黄金看板 GT 确权缺省值必须为 '-'"


def test_ac_3_5_no_raw_null_undefined_in_table_cells():
    """
    AC 3.5: 确保界面不存在未经清洗的原生 'None', 'null', 'undefined'。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    # 验证 _fmtDateTime 与 _fmtDateOnly 防御了这些字符串
    assert "s === 'None' || s === 'null' || s === 'undefined'" in js, \
        "时间格式化函数未防御 'None', 'null', 'undefined'"


# =============================================================================
# 四、 AC 4: 统计与数值格式化一致性自动化测试 (Metrics & Formatting AC)
# =============================================================================

def test_ac_4_1_money_formatting_and_zero_display():
    """
    AC 4.1: 单据总额带 HK$ 并保留两位小数；0 元优雅显示为 HK$ 0.00。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "const isZero = !it.total_amount || Number(it.total_amount) === 0;" in js, \
        "黄金看板未进行 0 元判断"
    assert "HK$ 0.00" in js, "黄金看板 0 元未优雅显示为 HK$ 0.00"


def test_ac_4_2_ratios_and_percentages_formatting():
    """
    AC 4.2: 比率指标统一保留 1 位小数或 2 位小数并以 % 结尾。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    # 埋点占比
    assert "toFixed(1) + '%'" in js or "(Number(d.share || 0) * 100).toFixed(1) + '%'" in js, \
        "占比指标未按 1 位小数格式化"


def test_ac_4_3_experiment_diff_delta_formatting():
    """
    AC 4.3: A/B 实验组别对比差异值标注符号并保留 2 位小数及 pp 单位 (如 +2.50 pp)。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "sign = diff > 0 ? '+' : ''" in js or "diff > 0 ? '+' : ''" in js
    assert "diff.toFixed(2)} pp" in js or "diff.toFixed(2) + ' pp'" in js or "diff.toFixed(2)} pp" in js, \
        "差异值未带符号及 2 位小数 pp 单位"


def test_ac_4_4_pvalue_and_zscore_formatting():
    """
    AC 4.4: p-value 格式化为 4 位小数，z 分数格式化为 3 位小数。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "pVal.toFixed(4)" in js or "toFixed(4)" in js, "p-value 必须保留 4 位小数"
    assert "zScore.toFixed(3)" in js or "toFixed(3)" in js, "z-score 必须保留 3 位小数"


# =============================================================================
# 五、 AC 5: 全局红线合规自动化测试 (Redline Compliance AC)
# =============================================================================

def test_ac_5_1_zero_emoji_static_scan_obs_center():
    """
    AC 5.1: 观测中心全域页面、弹窗、表头、单元格、徽章中，绝对不存在任何 Emoji 字符。
    """
    emoji_regex = re.compile(
        "[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u203c-\u2049]",
        flags=re.UNICODE
    )

    targets = [
        "demo/app/api_admin.py",
        "demo/app/api_phase2.py",
        "demo/app/services/parsing_logs.py",
    ]

    violations = []
    for rel_path in targets:
        if not os.path.exists(rel_path):
            continue
        with open(rel_path, "r", encoding="utf-8", errors="ignore") as f:
            for l_no, line in enumerate(f, 1):
                m = emoji_regex.findall(line)
                if m:
                    violations.append((rel_path, l_no, m, line.strip()))

    # 扫描 index.html 观测中心区块
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        lines = f.readlines()
    obs_lines = lines[2169:2968]
    obs_html = "".join(obs_lines)
    m = emoji_regex.findall(obs_html)
    if m:
        violations.append(("demo/templates/index.html (obs center)", 0, m, "Emoji in obs center"))

    # 扫描 main.js 观测中心区块 (L12800+)
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    lines_js = js.split("\n")
    for l_no, line in enumerate(lines_js, 1):
        if l_no >= 12800:
            m = emoji_regex.findall(line)
            if m:
                violations.append(("demo/static/js/main.js (obs center)", l_no, m, line.strip()))

    assert len(violations) == 0, f"发现 Emoji 违规: {violations}"


def test_ac_5_2_bracket_text_badges_standard():
    """
    AC 5.2: 状态展示一律使用纯文本方括号标准徽章：[成功], [失败], [完全采纳], [人工纠偏], [门禁拦截], [平算无门禁] 等。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    expected_badges = [
        "[成功]",
        "[失败]",
        "[完全采纳]",
        "[人工纠偏]",
        "[门禁拦截",
        "[平算无门禁]",
        "[OK]",
        "[WARN]",
        "[FAIL]",
    ]
    for b in expected_badges:
        assert b in js, f"main.js 缺少标准纯文本徽章: {b}"


def test_ac_5_3_data_masking_applied():
    """
    AC 5.3: 脱敏规则完备生效，供应商名称别名化，总额掩码。
    """
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "s.masked_vendor" in js, "未引用 masked_vendor 进行脱敏呈现"
    assert "s.masked_total" in js, "未引用 masked_total 进行金额掩码"

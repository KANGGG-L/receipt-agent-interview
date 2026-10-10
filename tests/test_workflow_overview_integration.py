# -*- coding: utf-8 -*-
"""
自动化测试：动态生效态概览与 Workflow 拓扑架构重构验证
测试范围与五大核心质量边界：
1. 边界一：DOM 完整性与清理验证 (div#enginePriorityGuideCard 100% 移除，#engineEffectiveBar, #adminEngineStatus, #btnToggleEngineTopology 存在)
2. 边界二：双模拓扑视图状态验证 (常驻精简模式 S0~S5 流水线、全景拓扑模式双层 Grid 结构与节点属性)
3. 边界三：动态状态机映射准确性 (P0 A/B 实验接管独占挂起、P1 灰度分流与分流模式、S3 按需自愈 BYPASS/MUTED、S4 交叉审核 ACTIVE/MUTED)
4. 边界四：全局无 Emoji 铁律验证 (扫描 templates/index.html, static/js/main.js, static/css/style.css, test_workflow_overview_integration.py 保证零 Emoji)
5. 边界五：穿透下钻函数签名与参数有效性校验 (jumpToEngineSection 签名及所有 targetElementId 在模板中存在)
"""

import os
import re
import json
import subprocess
import pytest

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))


def _run_render_engine_overview_node(routing, experiments=None, settings=None, expanded=True, latest=None):
    """
    通过 Node.js 执行 main.js 中的 renderEngineOverview 函数，返回渲染后的 HTML 字符串。
    注入隔离的全局作用域与 DOM 节点 mock。
    """
    js_file = os.path.join(DEMO_DIR, "static", "js", "main.js")
    node_script = f"""
    const fs = require('fs');
    const js = fs.readFileSync({json.dumps(js_file)}, 'utf8');

    const domStore = {{}};
    global.document = {{
        addEventListener: () => {{}},
        querySelectorAll: () => [],
        querySelector: () => null,
        getElementById: (id) => {{
            if (!domStore[id]) {{
                domStore[id] = {{ innerHTML: '', textContent: '', style: {{}}, classList: {{ add: ()=>{{}}, remove: ()=>{{}} }}, addEventListener: ()=>{{}} }};
            }}
            return domStore[id];
        }}
    }};
    global.window = global;
    global.window.addEventListener = () => {{}};
    global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
    global.fetch = () => Promise.resolve({{}});

    eval(js);

    global.window._engineOverviewExpanded = {str(expanded).lower()};
    global.window._engineOverviewState = {{ routing: {json.dumps(routing)}, latest: {json.dumps(latest)}, message: null }};
    global.window._adminExperimentsList = {json.dumps(experiments or [])};
    global.window._adminSettingsState = {json.dumps(settings or {})};

    global.renderEngineOverview();
    const output = domStore['adminEngineStatus'] ? domStore['adminEngineStatus'].innerHTML : '';
    process.stdout.write(output);
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
    return res.stdout


# ==============================================================================
# 兼容性测试套件 (与原有测试用例保持 100% 签名与断言兼容)
# ==============================================================================

def test_static_guide_card_removed_from_template():
    """断言旧静态说明卡片 id='enginePriorityGuideCard' 已被彻底移除"""
    html_path = os.path.join(DEMO_DIR, "templates", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert "enginePriorityGuideCard" not in html
    assert "enginePriorityContent" not in html


def test_engine_effective_bar_and_status_container_retained():
    """断言顶部常驻概览条容器及生效态容器保留，并增加了拓扑展开切换按钮"""
    html_path = os.path.join(DEMO_DIR, "templates", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="engineEffectiveBar"' in html
    assert 'id="adminEngineStatus"' in html
    assert 'id="btnToggleEngineTopology"' in html
    assert "[展开全景拓扑]" in html
    assert "toggleEngineTopologyInspector()" in html


def test_style_css_defines_topology_and_semantic_badge_classes():
    """断言 style.css 中包含拓扑流容器与纯 CSS 语义状态徽章样式"""
    css_path = os.path.join(DEMO_DIR, "static", "css", "style.css")
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    # 容器与卡片样式
    assert ".engine-topo-compact" in css
    assert ".engine-topo-chain" in css
    assert ".engine-topo-arrow" in css
    assert ".engine-topo-inspector" in css
    assert ".engine-topo-grid" in css
    assert ".engine-topo-plane" in css
    assert ".engine-topo-node" in css
    assert ".engine-node-badge" in css

    # 语义颜色徽章
    assert ".engine-node-badge.badge-active" in css
    assert ".engine-node-badge.badge-takeover" in css
    assert ".engine-node-badge.badge-standby" in css
    assert ".engine-node-badge.badge-bypass" in css
    assert ".engine-node-badge.badge-muted" in css


def test_main_js_topology_state_and_functions():
    """断言 main.js 实现了完整的状态维护、展开切换、深度跳转与动态计算渲染"""
    js_path = os.path.join(DEMO_DIR, "static", "js", "main.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js = f.read()

    # 核心状态变量
    assert "_engineOverviewExpanded" in js
    assert "_adminSettingsState" in js

    # 核心交互函数
    assert "function toggleEngineTopologyInspector()" in js
    assert "function jumpToEngineSection(" in js
    assert "function renderEngineOverview()" in js

    # 双模视图容器与标记
    assert "engineOverviewCompact" in js
    assert "engineOverviewInspector" in js
    assert "[流量路由决策层]" in js
    assert "[串行执行流水线]" in js

    # P0/P1/P2 动态仲裁关键断言
    assert "[独占接管: 实验 #" in js
    assert "[就绪备用: 无运行实验]" in js
    assert "[已启用: 灰度分流" in js
    assert "[已启用: 生产基线" in js

    # S0~S5 流水线时序关键断言
    assert "S0 质检熔断" in js
    assert "S1 动态记忆注入" in js
    assert "S2 核心视觉识别" in js
    assert "S3 语义自愈" in js
    assert "S4 交叉审核" in js
    assert "S5 飞轮治理" in js


def test_zero_emoji_in_workflow_overview_code():
    """断言所有涉及本轮重构的模板、样式与脚本关键代码段均绝对无 Emoji 字符"""
    files_to_check = [
        os.path.join(DEMO_DIR, "templates", "index.html"),
        os.path.join(DEMO_DIR, "static", "css", "style.css"),
        os.path.join(DEMO_DIR, "static", "js", "main.js"),
    ]

    emoji_pattern = re.compile(
        r"[\U00010000-\U0010ffff"
        r"\u2600-\u27bf"
        r"\u2300-\u23ff"
        r"\u2b50]"
    )

    for path in files_to_check:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for idx, line in enumerate(lines, 1):
            if any(term in line for term in [
                "engineEffectiveBar", "engineOverviewCompact", "engineOverviewInspector",
                "btnToggleEngineTopology", "engine-topo-", "renderEngineOverview",
                "jumpToEngineSection", "toggleEngineTopologyInspector"
            ]):
                matches = emoji_pattern.findall(line)
                assert not matches, f"Emoji detected in {path}:{idx}: {matches}"


# ==============================================================================
# 边界一：DOM 完整性与清理深度验证
# ==============================================================================

def test_boundary_1_dom_integrity_and_cleanup():
    """
    边界一：
    1. 彻底移除旧说明卡片：div#enginePriorityGuideCard 与 #enginePriorityContent 在模板中 0 出现；
    2. 兼容性容器保留：div#engineEffectiveBar 与 div#adminEngineStatus 存在；
    3. 全景展开按钮：button#btnToggleEngineTopology 存在，包含默认文字与点击事件。
    """
    html_path = os.path.join(DEMO_DIR, "templates", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 断言彻底移除旧静态说明卡片及其子内容
    assert 'id="enginePriorityGuideCard"' not in html
    assert 'id="enginePriorityContent"' not in html
    assert 'enginePriorityGuideCard' not in html

    # 断言保留生效态展示容器
    assert 'id="engineEffectiveBar"' in html
    assert 'id="adminEngineStatus"' in html

    # 断言全景展开切换按钮存在且参数正确
    assert 'id="btnToggleEngineTopology"' in html
    assert 'onclick="toggleEngineTopologyInspector()"' in html
    assert '[展开全景拓扑]' in html


# ==============================================================================
# 边界二：双模拓扑视图状态验证
# ==============================================================================

def test_boundary_2_dual_mode_topology_view_structure():
    """
    边界二：
    1. 常驻精简模式 (engineOverviewCompact)：包含 S0~S5 节点微徽章、箭头指示器及版本和来源展示；
    2. 全景诊断模式 (engineOverviewInspector)：包含双层 Grid（流量决策层 + 串行执行流水线层）。
    """
    routing = {
        "version": 4,
        "config_source": "manual",
        "module_a": {
            "audit_enabled": True,
            "parse_llm_enabled": True,
            "legs": {
                "rec": {"model": "gpt-4o"},
                "aud": {"model": "gpt-4o-mini"},
                "parse": {"model": "gpt-4o-mini"},
            },
        },
        "module_c": {
            "grey_enabled": False,
            "grey_percent": 0,
            "legs": {},
        },
    }
    rendered_html = _run_render_engine_overview_node(routing=routing, expanded=True)

    # 1. 常驻精简模式结构断言
    assert 'id="engineOverviewCompact"' in rendered_html
    assert 'class="engine-topo-compact"' in rendered_html
    assert 'class="engine-topo-chain"' in rendered_html
    assert 'class="engine-overview-version"' not in rendered_html
    assert '管理台手工配置' in rendered_html
    assert '[路由决策: 生产基线 100%]' in rendered_html
    for step in ["S0", "S1", "S2", "S3", "S4", "S5"]:
        assert f"<strong>{step}</strong>" in rendered_html
    assert "-&gt;" in rendered_html

    # 2. 全景诊断模式双层 Grid 结构断言
    assert 'id="engineOverviewInspector"' in rendered_html
    assert 'class="engine-topo-grid"' in rendered_html
    assert '[流量路由决策层]' in rendered_html
    assert '[串行执行流水线]' in rendered_html
    assert '点击卡片直达调度面板' not in rendered_html
    assert '点击阶段卡片穿透定位配置源' not in rendered_html
    assert 'class="engine-routing-item-jump"' not in rendered_html
    assert 'class="engine-topo-node-jump"' not in rendered_html
    assert 'P0 A/B 实验' in rendered_html
    assert 'P1 灰度发布' in rendered_html
    assert 'P2 生产基线' in rendered_html
    assert 'S0 质检熔断' in rendered_html
    assert 'S1 动态记忆注入' in rendered_html
    assert 'S2 核心视觉识别' in rendered_html
    assert 'S3 语义自愈' in rendered_html
    assert 'S4 交叉审核' in rendered_html
    assert 'S5 飞轮治理' in rendered_html


# ==============================================================================
# 边界三：动态状态机映射准确性验证
# ==============================================================================

def test_boundary_3_state_machine_p0_ab_takeover_and_standby():
    """
    边界三 (P0 A/B 实验接管与挂起)：
    - 当存在 running 状态实验时，P0 标记为 [TAKEOVER]，下层 P1 与 P2 独占挂起为 [STANDBY]；
    - 当无 running 实验时，P0 处于 [STANDBY: 无运行中实验]。
    """
    routing = {
        "version": 7,
        "config_source": "manual",
        "module_a": {
            "audit_enabled": True,
            "parse_llm_enabled": True,
            "legs": {"rec": {"model": "gpt-4o"}, "aud": {"model": "gpt-4o-mini"}},
        },
        "module_c": {
            "grey_enabled": True,
            "grey_percent": 30,
            "grey_assign_mode": "supplier_hash",
            "legs": {},
        },
    }

    # 1. 实验运行中 (P0 Takeover)
    running_exp = [{"id": 88, "name": "PromptV2_Test", "status": "running", "target_percent": 50}]
    html_takeover = _run_render_engine_overview_node(routing=routing, experiments=running_exp)

    assert "[独占接管: 实验 #88]" in html_takeover
    assert "badge-takeover" in html_takeover
    assert "[就绪备用: 实验独占挂起]" in html_takeover
    assert "[就绪备用: 受实验控制]" in html_takeover
    assert "[路由决策: 实验接管 (50%)]" in html_takeover

    # 2. 无运行中实验 (P0 Standby)
    html_no_exp = _run_render_engine_overview_node(routing=routing, experiments=[])
    assert "[就绪备用: 无运行实验]" in html_no_exp


def test_boundary_3_state_machine_p1_grayscale_and_p2_baseline():
    """
    边界三 (P1 灰度分流与 P2 基线兜底)：
    - 灰度开启且放量 > 0%：P1 为 [已启用: 灰度分流]，显示分流比例与分配模式；P2 为基线兜底 (100 - X)%；
    - 灰度开启但放量为 0%：P1 处于 [就绪备用: 灰度放量 0%]；
    - 灰度未开启：P1 为 [未启用]，P2 为 [已启用: 生产基线 100%]。
    """
    base_routing = {
        "version": 3,
        "config_source": "manual",
        "module_a": {
            "audit_enabled": False,
            "parse_llm_enabled": False,
            "legs": {"rec": {"model": "gpt-4o"}, "aud": {"model": "gpt-4o-mini"}},
        },
        "module_c": {
            "grey_enabled": True,
            "grey_percent": 35,
            "grey_assign_mode": "supplier_hash",
            "legs": {
                "grey_rec": {"model": "gpt-4o-2024-11-20"},
                "grey_aud": {"model": "gpt-4o-mini"},
            },
        },
    }

    # 1. 灰度放量 35%
    html_grey_active = _run_render_engine_overview_node(routing=base_routing, experiments=[])
    assert "[已启用: 灰度分流 35%]" in html_grey_active
    assert "供应商哈希" in html_grey_active
    assert "[已启用: 基线兜底 65%]" in html_grey_active
    assert "[路由决策: 灰度分流 35% (供应商哈希)]" in html_grey_active

    # 2. 灰度开启但放量 0%
    base_routing["module_c"]["grey_percent"] = 0
    html_grey_zero = _run_render_engine_overview_node(routing=base_routing, experiments=[])
    assert "[就绪备用: 灰度放量 0%]" in html_grey_zero
    assert "[已启用: 生产基线 100%]" in html_grey_zero
    assert "[路由决策: 生产基线 100%]" in html_grey_zero

    # 3. 灰度未启用
    base_routing["module_c"]["grey_enabled"] = False
    html_grey_muted = _run_render_engine_overview_node(routing=base_routing, experiments=[])
    assert "[未启用]" in html_grey_muted
    assert "[已启用: 生产基线 100%]" in html_grey_muted
    assert "[路由决策: 生产基线 100%]" in html_grey_muted


def test_boundary_3_state_machine_s3_self_heal_and_s4_audit():
    """
    边界三 (S3 语义自愈与 S4 交叉审核状态机)：
    - S3: parse_llm_enabled 为 True 时呈现 [按需自愈] (badge-bypass)，False 时呈现 [未启用] (badge-muted)；
    - S4: audit_enabled 为 True 时呈现 [已启用] (badge-active)，False 时呈现 [未启用] (badge-muted)。
    """
    routing = {
        "version": 2,
        "config_source": "manual",
        "module_a": {
            "audit_enabled": True,
            "parse_llm_enabled": True,
            "legs": {
                "rec": {"model": "gpt-4o"},
                "aud": {"model": "gpt-4o-mini"},
                "parse": {"model": "gpt-4o-mini"},
            },
        },
        "module_c": {"grey_enabled": False, "grey_percent": 0, "legs": {}},
    }

    # 1. S3 开启 ([按需自愈]), S4 开启 ([已启用])
    html_enabled = _run_render_engine_overview_node(routing=routing, experiments=[])
    assert "S3 语义自愈" in html_enabled
    assert "[按需自愈]" in html_enabled
    assert "按需自愈 (gpt-4o-mini)" in html_enabled
    assert "S4 交叉审核" in html_enabled
    assert "[已启用]" in html_enabled
    assert "双盲复核 (gpt-4o-mini)" in html_enabled

    # 2. S3 关闭 ([未启用]), S4 关闭 ([未启用])
    routing["module_a"]["parse_llm_enabled"] = False
    routing["module_a"]["audit_enabled"] = False
    html_disabled = _run_render_engine_overview_node(routing=routing, experiments=[])
    assert "规则硬解析 (直通)" in html_disabled
    assert "复核已关闭" in html_disabled
    assert "[未启用]" in html_disabled


def test_boundary_3_state_machine_s0_quality_and_s1_rag_thresholds():
    """
    边界三 (S0 质检与 S1 动态记忆注入阈值状态机)：
    - S0: blur_laplacian_threshold > 0 时为已启用，等于 0 时为未启用；
    - S1: memory_enabled 为 True 时为已启用，为 False 时为未启用。
    """
    routing = {
        "version": 1,
        "config_source": "default",
        "module_a": {
            "audit_enabled": False,
            "parse_llm_enabled": False,
            "legs": {"rec": {"model": "gpt-4o"}},
        },
        "module_c": {"grey_enabled": False, "grey_percent": 0, "legs": {}},
    }

    # 1. 质检与记忆均开启
    settings_on = {
        "blur_laplacian_threshold": 30.0,
        "memory_enabled": True,
        "memory_budget_facts_tokens": 1200,
        "vendor_precheck_enabled": True,
    }
    html_on = _run_render_engine_overview_node(routing=routing, settings=settings_on)
    assert "模糊度: 30" in html_on
    assert "[阈值 30]" in html_on
    assert "预算: 1200 词元 (预判开)" in html_on
    assert "[1200 词元]" in html_on

    # 2. 质检与记忆均关闭
    settings_off = {
        "blur_laplacian_threshold": 0,
        "memory_enabled": False,
    }
    html_off = _run_render_engine_overview_node(routing=routing, settings=settings_off)
    assert "质检已关闭" in html_off
    assert "记忆已停用" in html_off
    assert "[未启用]" in html_off


# ==============================================================================
# 边界四：全局无 Emoji 铁律验证
# ==============================================================================

def test_boundary_4_strict_zero_emoji_scan():
    """
    边界四：严格全局扫描。
    扫描 demo/templates/index.html, demo/static/js/main.js, demo/static/css/style.css,
    以及本测试用例文件 tests/test_workflow_overview_integration.py，
    断言全量文本 Emoji 字符数恒等于 0。
    """
    files_to_scan = [
        os.path.join(DEMO_DIR, "templates", "index.html"),
        os.path.join(DEMO_DIR, "static", "js", "main.js"),
        os.path.join(DEMO_DIR, "static", "css", "style.css"),
        os.path.abspath(__file__),
    ]

    emoji_pattern = re.compile(
        "["
        "\U00010000-\U0010ffff"
        "\u2600-\u26ff"
        "\u2700-\u27bf"
        "]",
        flags=re.UNICODE,
    )

    for fpath in files_to_scan:
        assert os.path.exists(fpath), f"文件不存在: {fpath}"
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        matches = emoji_pattern.findall(content)
        assert len(matches) == 0, f"文件 {os.path.basename(fpath)} 违反红线，包含非法 Emoji 表情: {matches}"


# ==============================================================================
# 边界五：穿透下钻函数签名与参数有效性校验
# ==============================================================================

def test_boundary_5_drilldown_signature_and_target_element_ids():
    """
    边界五：
    1. 校验 jumpToEngineSection(viewId, targetElementId) 函数签名；
    2. 校验 main.js 中所有 jumpToEngineSection 调用的 targetElementId 在 index.html 中真实存在；
    3. 校验 viewId 均为有效的发动机子面板枚举 ('view01', 'view02', 'view03')。
    """
    js_path = os.path.join(DEMO_DIR, "static", "js", "main.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js = f.read()

    # 1. 函数声明断言
    assert "function jumpToEngineSection(viewId, targetElementId)" in js

    # 2. 提取拓扑视图中所有 jumpToEngineSection('viewXX', 'targetId') 的实际调用
    call_pattern = re.compile(r"jumpToEngineSection\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)")
    calls = call_pattern.findall(js)
    assert len(calls) > 0, "未能从 main.js 中解析到任何 jumpToEngineSection 拓扑跳转调用"

    html_path = os.path.join(DEMO_DIR, "templates", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    valid_subviews = {"view01", "view02", "view03"}
    for view_id, target_id in calls:
        assert view_id in valid_subviews, f"非法子面板 viewId: {view_id}"
        # 断言目标 DOM ID 在模板中存在
        id_needle = f'id="{target_id}"'
        assert id_needle in html, f"下钻目标元素 ID 未在 index.html 中定义: {target_id}"

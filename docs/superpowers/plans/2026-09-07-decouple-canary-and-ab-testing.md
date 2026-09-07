# 灰度发布与 A/B 测试彻底解耦实施计划 (Decouple Canary Release & A/B Testing Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将系统中的“金丝雀灰度发布 (Canary Release)”与“A/B 科学实验 (A/B Testing)”在配置端（抽屉文案与模型）、观测端（将 3 分段扩展为 4 独立功能区）及前端加载流中彻底解耦。

**Architecture:** 
1. 引擎配置页 (`tab-engine`) 抽屉剥离所有 A/B 词汇，专注于金丝雀流量放量、配置 Diff 及一键推全/回滚；
2. 效果观测中心 (`tab-analytics`) 拆分为 4 个独立功能区：`01 埋点分布与挽回`、`02 金丝雀发布监控`、`03 A/B 科学实验台`、`04 基准资产与确权`；
3. 前端切换器 `switchAnalyticsSubView` 严格按需触发对应专属端点，消灭跨子视图的无序并发与请求污染。

**Tech Stack:** FastAPI, Jinja2/HTML5, Vanilla JavaScript, CSS3 Flexbox/Grid, Playwright, Pytest.

## Global Constraints
- 不破坏现有 RBAC（owner / admin 均具备访问与操作权限）；
- 保持既有端到端测试 `demo/tests/test_admin_tabs_consolidation_e2e.py` 100% 通过；
- 缓存控制破除符统一步进到 `?v=20260907c`；
- 所有单元格样式保持 `table-layout: auto !important;`，绝不引入文字截断。

---

### Task 1: 纯化「引擎与系统配置」的金丝雀灰度抽屉

**Files:**
- Modify: `demo/templates/index.html:1612-1630`
- Modify: `demo/static/js/main.js:12765-12776`
- Test: `demo/tests/test_decouple_canary_and_ab_testing.py`

**Interfaces:**
- Consumes: `#adminGreyDrawer`, `toggleEngineDrawer`, `gotoGreyPromote`
- Produces: 纯粹的金丝雀灰度抽屉标题与说明，无任何 A/B 混淆文案

- [ ] **Step 1: 编写失败的自动化测试**

在 `demo/tests/test_decouple_canary_and_ab_testing.py` 中编写对抽屉文案与结构的检查：
```python
# -*- coding: utf-8 -*-
import pytest
from bs4 import BeautifulSoup


def test_engine_drawer_purified():
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    drawer_title = soup.find(id="adminGreyDrawer").find(class_="engine-drawer-title").text
    assert "金丝雀灰度发布" in drawer_title
    assert "A/B" not in drawer_title
```

- [ ] **Step 2: 运行测试验证失败**

运行：`pytest demo/tests/test_decouple_canary_and_ab_testing.py::test_engine_drawer_purified -v`
预期：FAIL（目前标题包含 `高级配置 · A/B 分组灰度测试`）

- [ ] **Step 3: 修改 index.html 抽屉标题与引导文案**

在 `demo/templates/index.html` 第 1616 行更新标题：
```html
<div class="engine-drawer-title">
    <span style="color:#b8860b;">高级配置 · 金丝雀灰度发布 (Canary Rollout)</span>
</div>
```
并在开关旁增加说明文本：“控制新版引擎流量放量比例（0-100%），支持配置对比与一键推全/回滚”。

- [ ] **Step 4: 运行测试验证通过**

运行：`pytest demo/tests/test_decouple_canary_and_ab_testing.py::test_engine_drawer_purified -v`
预期：PASS

- [ ] **Step 5: 提交代码**

```bash
git add demo/templates/index.html demo/tests/test_decouple_canary_and_ab_testing.py
git commit -m "feat(canary): purify engine drawer title and eliminate ab testing conflation"
```

---

### Task 2: 拆分 AI 效果观测中心为 4 大独立功能区 (HTML 结构改造)

**Files:**
- Modify: `demo/templates/index.html:1865-2015`
- Test: `demo/tests/test_decouple_canary_and_ab_testing.py`

**Interfaces:**
- Consumes: `analytics-segmented-nav`, `sec-canary`, `sec-experiment`, `sec-eval`
- Produces: 
  - Button 1: `data-view="sec-telemetry"` (01 埋点分布与挽回)
  - Button 2: `data-view="sec-canary"` (02 金丝雀发布监控)
  - Button 3: `data-view="sec-experiment"` (03 A/B 科学实验台)
  - Button 4: `data-view="sec-eval"` (04 基准资产与确权)

- [ ] **Step 1: 编写 4 分段导航与独立容器的断言测试**

在 `demo/tests/test_decouple_canary_and_ab_testing.py` 追加：
```python
def test_analytics_four_subviews_structure():
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    buttons = soup.select(".analytics-segmented-nav button.analytics-segment-btn")
    assert len(buttons) == 4
    views = [b.get("data-view") for b in buttons]
    assert views == ["sec-telemetry", "sec-canary", "sec-experiment", "sec-eval"]

    assert soup.find("section", id="sec-canary") is not None
    assert soup.find("section", id="sec-experiment") is not None
    assert soup.find("section", id="sec-eval") is not None
```

- [ ] **Step 2: 运行测试验证失败**

运行：`pytest demo/tests/test_decouple_canary_and_ab_testing.py::test_analytics_four_subviews_structure -v`
预期：FAIL（目前仅有 3 个按钮，且缺失 `sec-experiment` section）

- [ ] **Step 3: 修改 index.html 分段导航栏与创建 sec-experiment 独立区**

1. 将导航栏更新为 4 个按钮：
```html
<div class="analytics-segmented-nav" role="tablist" style="margin-bottom:18px; display:flex; gap:8px; flex-wrap:wrap;">
    <button type="button" class="analytics-segment-btn active" id="btnSubViewTelemetry" data-view="sec-telemetry" role="tab" aria-selected="true" onclick="switchAnalyticsSubView('sec-telemetry')">
        <span class="segment-num">01</span> 埋点分布与挽回
    </button>
    <button type="button" class="analytics-segment-btn" id="btnSubViewCanary" data-view="sec-canary" role="tab" aria-selected="false" onclick="switchAnalyticsSubView('sec-canary')">
        <span class="segment-num">02</span> 金丝雀发布监控
    </button>
    <button type="button" class="analytics-segment-btn" id="btnSubViewExperiment" data-view="sec-experiment" role="tab" aria-selected="false" onclick="switchAnalyticsSubView('sec-experiment')">
        <span class="segment-num">03</span> A/B 科学实验台
    </button>
    <button type="button" class="analytics-segment-btn" id="btnSubViewEval" data-view="sec-eval" role="tab" aria-selected="false" onclick="switchAnalyticsSubView('sec-eval')">
        <span class="segment-num">04</span> 基准资产与确权
    </button>
</div>
```

2. 剥离 `sec-canary`：
   - 标题改为：`金丝雀发布监控`；
   - 描述改为：`新版引擎运行稳定性、脱敏单据流实时巡检与渐进放量推全闭环，回答「新版本稳不稳、敢不敢推全」`；
   - 移出原卡片二（`A/B 实验数据与双侧显著性检验`）。

3. 新建独立功能区 `sec-experiment`（03 区）：
```html
<!-- 功能区三：A/B 科学实验台 (独立视图) -->
<section id="sec-experiment" class="analytics-section-block analytics-subview-panel" style="display:none;">
    <div class="analytics-section-header">
        <div class="analytics-section-title-wrap">
            <span class="analytics-section-badge">03</span>
            <div>
                <div class="analytics-section-title">A/B 科学实验台</div>
                <div class="analytics-section-desc">双盲对等分组实验、Control vs Treatment 组别指标对比与双侧显著性假设检验 (p-value)，回答「哪个方案真正更好」</div>
            </div>
        </div>
    </div>
    <div class="card">
        <div class="card-title" style="justify-content:space-between; align-items:center;">
            <span>A/B 实验对比与组别表现</span>
            <div style="display:flex; gap:8px; align-items:center;">
                <select id="experimentSelect" class="form-control" style="width:auto; padding:3px 8px; font-size:0.8rem;" onchange="loadExperimentDetail(this.value)">
                    <option value="2">实验 #2: LongCat-2.0 vs Qwen3-VL (运行中)</option>
                </select>
                <button class="btn btn-secondary" style="font-size:0.8rem; padding:4px 10px;" onclick="loadAnalyticsExperiments()">刷新实验数据</button>
            </div>
        </div>
        <div id="analyticsExperimentsBody" style="font-size:0.85rem; color:var(--text-muted);">加载中</div>
        <div style="margin-top:18px; border-top:1px dashed var(--border-color); padding-top:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <span style="font-weight:600; font-size:0.9rem;">A/B 显著性假设检验卡片（双侧 z 检验）</span>
            </div>
            <div id="pvalueThresholdNote" style="font-size:0.75rem; color:var(--text-muted); margin-bottom:8px;">
                判定标准：双侧双比例 z 检验，显著性水平 alpha = 0.05。p &lt; 0.05 视为具备统计学显著差异；样本量 N &lt; 30 时标记低置信度。
            </div>
            <div id="pvalueCardsContainer" style="display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:10px;"></div>
        </div>
    </div>
</section>
```

4. 将原 `sec-eval` 序号顺延为 `04 基准资产与确权`。
5. 升级缓存破除控制符为 `?v=20260907c`。

- [ ] **Step 4: 运行测试验证通过**

运行：`pytest demo/tests/test_decouple_canary_and_ab_testing.py::test_analytics_four_subviews_structure -v`
预期：PASS

- [ ] **Step 5: 提交代码**

```bash
git add demo/templates/index.html demo/tests/test_decouple_canary_and_ab_testing.py
git commit -m "feat(ui): split analytics center into four distinct subviews"
```

---

### Task 3: 前端加载调度器与事件绑定改造 (JavaScript)

**Files:**
- Modify: `demo/static/js/main.js:12958-13010`
- Test: `demo/tests/test_decouple_canary_and_ab_testing.py`

**Interfaces:**
- Consumes: `switchAnalyticsSubView(targetViewId)`, `loadAnalyticsBoard(tenantId)`
- Produces: 4 个独立加载分支，互不干扰

- [ ] **Step 1: 编写加载调度器单元测试**

在 `demo/tests/test_decouple_canary_and_ab_testing.py` 追加：
```python
def test_js_switch_analytics_subview_supports_experiment():
    with open("demo/static/js/main.js", "r", encoding="utf-8") as f:
        js = f.read()
    assert "'sec-experiment'" in js
    assert "validViews = ['sec-telemetry', 'sec-canary', 'sec-experiment', 'sec-eval']" in js or \
           "validViews = ['sec-telemetry', 'sec-canary', 'sec-experiment', 'sec-eval']" in js.replace('"', "'")
```

- [ ] **Step 2: 运行测试验证失败**

运行：`pytest demo/tests/test_decouple_canary_and_ab_testing.py::test_js_switch_analytics_subview_supports_experiment -v`
预期：FAIL

- [ ] **Step 3: 更新 switchAnalyticsSubView 与 loadAnalyticsBoard**

在 `demo/static/js/main.js`：
```javascript
function switchAnalyticsSubView(targetViewId) {
    const validViews = ['sec-telemetry', 'sec-canary', 'sec-experiment', 'sec-eval'];
    if (!validViews.includes(targetViewId)) {
        targetViewId = 'sec-telemetry';
    }

    const buttons = document.querySelectorAll('.analytics-segment-btn');
    buttons.forEach(btn => {
        const isMatch = btn.getAttribute('data-view') === targetViewId;
        btn.classList.toggle('active', isMatch);
        btn.setAttribute('aria-selected', isMatch ? 'true' : 'false');
    });

    validViews.forEach(viewId => {
        const panel = document.getElementById(viewId);
        if (panel) {
            if (viewId === targetViewId) {
                panel.style.display = '';
                panel.classList.add('active');
            } else {
                panel.style.display = 'none';
                panel.classList.remove('active');
            }
        }
    });

    // 针对 4 大激活功能区触发专属刷新
    if (targetViewId === 'sec-telemetry') {
        const sel = document.getElementById('analyticsTenantSelect');
        const tId = sel ? sel.value : 'all';
        if (typeof loadRecoverySummaryBlocks === 'function') loadRecoverySummaryBlocks(tId);
    } else if (targetViewId === 'sec-canary') {
        if (typeof loadAnalyticsGreyStatus === 'function') loadAnalyticsGreyStatus();
        if (typeof loadAdminGreySamples === 'function') loadAdminGreySamples(null, true);
    } else if (targetViewId === 'sec-experiment') {
        if (typeof loadAnalyticsExperiments === 'function') loadAnalyticsExperiments();
        if (typeof loadPValueCards === 'function') loadPValueCards();
    } else if (targetViewId === 'sec-eval') {
        if (typeof loadGoldenBoard === 'function') loadGoldenBoard();
        if (typeof loadGTEvalsetStats === 'function') loadGTEvalsetStats();
    }
}
```

并在 `loadAnalyticsBoard` 中支持 `sec-experiment` 活跃分支：
```javascript
    } else if (activeView === 'sec-experiment') {
        loadAnalyticsExperiments();
        loadPValueCards();
    }
```

- [ ] **Step 4: 运行测试验证通过**

运行：`pytest demo/tests/test_decouple_canary_and_ab_testing.py::test_js_switch_analytics_subview_supports_experiment -v`
预期：PASS

- [ ] **Step 5: 提交代码**

```bash
git add demo/static/js/main.js demo/tests/test_decouple_canary_and_ab_testing.py
git commit -m "feat(analytics): update subview switcher to support 4 independent sections"
```

---

### Task 4: 真实浏览器 Playwright E2E 全流程回归核验

**Files:**
- Modify: `demo/tests/test_admin_tabs_consolidation_e2e.py`
- Create: `demo/tests/test_playwright_canary_ab_decoupled.py`

- [ ] **Step 1: 编写 Playwright E2E 回归脚本**

创建 `demo/tests/test_playwright_canary_ab_decoupled.py`：
1. 切换到 `admin` 角色；
2. 检查「引擎与系统配置」中 `#adminGreyDrawer` 展开后标题为 `高级配置 · 金丝雀灰度发布 (Canary Rollout)`；
3. 进入「AI 效果观测中心」；
4. 依次点击 4 个分段按钮：
   - 点击 `sec-canary`：验证仅加载脱敏单据流与金丝雀状态卡片；
   - 点击 `sec-experiment`：验证展示 A/B 实验对比与 p-value 卡片；
   - 截取高清屏幕快照 `demo/gui-test-screenshots/sec_canary_decoupled.png` 与 `demo/gui-test-screenshots/sec_experiment_decoupled.png`。

- [ ] **Step 2: 运行测试并生成截图**

运行：`~/.pyenv/versions/3.9.6/bin/pytest demo/tests/test_playwright_canary_ab_decoupled.py -v`
预期：PASS，截图成功生成。

- [ ] **Step 3: 运行既有全量 E2E 测试确保零退化**

运行：`~/.pyenv/versions/3.9.6/bin/pytest demo/tests/test_admin_tabs_consolidation_e2e.py -v`
预期：PASS

- [ ] **Step 4: 提交代码**

```bash
git add demo/tests/test_playwright_canary_ab_decoupled.py demo/tests/test_admin_tabs_consolidation_e2e.py
git commit -m "test(e2e): verify decoupling of canary release and ab testing with playwright"
```

---

### Task 5: 派发独立 UI/UX 审计 Subagent 验收打分

**Files:**
- Audit: `demo/gui-test-screenshots/sec_canary_decoupled.png`
- Audit: `demo/gui-test-screenshots/sec_experiment_decoupled.png`

- [ ] **Step 1: 派发独立 UI Auditor subagent**
- [ ] **Step 2: Auditor 验证 4 分段导航对齐、文字截断率、语义色阶与闭环深链**
- [ ] **Step 3: 获取审计评分（满分 10 分）与终验裁决**

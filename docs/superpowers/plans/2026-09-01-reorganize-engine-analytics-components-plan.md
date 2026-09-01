# 整理“引擎配置与灰测大盘”测试组件至“埋点观测”实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `#tab-engine` 中的灰测单据抽样与效果评估大盘迁移至 `#tab-analytics`，并将 `#tab-engine` 明确为纯「引擎与系统配置」。

**Architecture:** 
- 视图层：在 `index.html` 中调整侧边栏导航名称，将灰测脱敏抽样面板与指标卡从 `#tab-engine` 移入 `#tab-analytics`；
- 逻辑层：在 `main.js` 中将 `loadAdminGreySamples` 与 `loadAnalyticsBoard` 深度联动，支持统一租户筛选与刷新；
- 验证层：Pytest 单元/回归测试 + Playwright 端到端视觉与交互截图验证。

**Tech Stack:** FastAPI, HTML5, Vanilla JavaScript, CSS, Playwright, Pytest.

## Global Constraints
- 遵循全局纯文本规范，按钮无 emoji；
- 保持所有 ID 命名向前兼容，避免破坏现有模态弹窗与调用；
- 保持 Admin RBAC 权限隔离。

---

### Task 1: DOM 结构调整（`demo/templates/index.html`）

**Files:**
- Modify: `demo/templates/index.html`

- [ ] **Step 1: 修改侧边栏导航名称**
将 `#adminEngineBtn` 的文本修改为 `引擎与系统配置`，`data-title="引擎与系统配置"`。

- [ ] **Step 2: 移出 `#tab-engine` 中的灰测单据抽样观测卡片**
将 `<!-- 灰测用户使用状态与脱敏单据抽样观测面板 (Admin 专属) -->`（约 L1755-L1789）从 `#tab-engine` 中剪切。

- [ ] **Step 3: 迁入 `#tab-analytics` 中的灰测现状区块**
替换 `#tab-analytics` 内现有的简略 card，将灰测指标徽章组与脱敏单据流表格放入 `#tab-analytics` 的灰测现状卡片中。

---

### Task 2: 前端逻辑与联动优化（`demo/static/js/main.js`）

**Files:**
- Modify: `demo/static/js/main.js`

- [ ] **Step 1: 在 `loadAnalyticsBoard` 中统一调用 `loadAdminGreySamples`**
更新 `loadAnalyticsBoard(tenantId)`，在加载全量埋点事件与挽回指标的同时触发 `loadAdminGreySamples(tenantId)`。

- [ ] **Step 2: 更新 `loadAdminGreySamples` 支持 `tenantId` 参数**
支持将 `tenantId` 作为查询参数或请求头传给 `/api/admin/grey-test/samples`，并在切换租户时即时刷新。

- [ ] **Step 3: 检查语法**
运行 `node --check demo/static/js/main.js`。

---

### Task 3: 全量测试回归与 Playwright 视觉验证

**Files:**
- Modify/Execute: `demo/scratch/verify_reorganize.py`

- [ ] **Step 1: 运行全量 pytest 测试**
运行 `~/.pyenv/versions/3.9.6/bin/python -m pytest demo/tests/ -q`，确认 218 个用例全部通过。

- [ ] **Step 2: 运行 Playwright 验证脚本**
验证：
1. 侧边栏按钮文案为「引擎与系统配置」；
2. `#tab-engine` 页面只包含配置项，无脱敏单据表格；
3. 进入 `#tab-analytics`，灰测区块正确展示指标卡片与脱敏单据流表格，点击「查看脱敏解析」弹窗正常弹出；
4. 截图保存至 Artifacts 目录。

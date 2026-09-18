# 「系统与引擎配置」A/B 科学实验编排抽屉实施计划 (A/B Experimentation Engine Drawer Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在「系统与引擎配置」(`tab-engine`) 中并列新增独立的「高级配置 · A/B 科学实验编排」抽屉与新建弹窗，支持实验列表浏览、Control (生产主力) vs Treatment (实验组) 方案对照、启停控制，并与「03 A/B 科学实验台」形成双向闭环。

**Architecture:**
1. **后端 API (`demo/app/api_admin.py`)**: 补齐 Admin 角色的 A/B 实验创建、启动、暂停、列表接口，统一调用 `db.py` 中的 `_ExperimentRow` 引擎层。
2. **前端 HTML 结构 (`demo/templates/index.html`)**: 在 `#adminGreyDrawer`（金丝雀灰度）下方并列新增 `#adminExperimentDrawer`（A/B 实验编排）以及 `#createExperimentModal`（新建实验弹窗）。
3. **前端控制流 (`demo/static/js/main.js`)**: 实现实验加载与联动、启停状态机、双向深链导航（配置页 <-> 观测台）。
4. **端到端自动化验证 (`Playwright`)**: 真实浏览器全流程覆盖，验证双抽屉隔离、参数呈现、操作有效性及深链跳转。

**Tech Stack:** FastAPI, SQLite/SQLAlchemy, Vanilla JS, CSS3, Playwright, Pytest.

---

### Task 1: 后端 Admin A/B 实验管理接口与单元测试

**Files:**
- Modify: `demo/app/api_admin.py`
- Create: `demo/tests/test_admin_experiments_api.py`

**Interfaces:**
- `POST /api/admin/experiments`: 创建实验 (name, hypothesis, success_metric, target_percent, min_sample)
- `POST /api/admin/experiments/{exp_id}/start`: 启动实验 (status -> running)
- `POST /api/admin/experiments/{exp_id}/stop`: 暂停实验 (status -> stopped)
- `GET /api/admin/experiments`: 列表
- `GET /api/admin/experiments/{exp_id}`: 详情

- [ ] **Step 1: 编写失败的单元测试**
创建 `demo/tests/test_admin_experiments_api.py`，测试 admin 角色下创建、启动、暂停和获取实验详情。

- [ ] **Step 2: 运行测试验证失败**
`~/.pyenv/versions/3.9.6/bin/pytest demo/tests/test_admin_experiments_api.py -v`

- [ ] **Step 3: 在 `api_admin.py` 中实现对应接口**
接入 `require_admin(request)`，调用 `db.create_experiment`, `db.start_experiment`, `db.stop_experiment`。

- [ ] **Step 4: 运行测试验证通过**
`~/.pyenv/versions/3.9.6/bin/pytest demo/tests/test_admin_experiments_api.py -v`

- [ ] **Step 5: 提交代码**
`git add demo/app/api_admin.py demo/tests/test_admin_experiments_api.py && git commit -m "feat(api): add admin endpoints for ab experiment management"`

---

### Task 2: 前端 HTML 抽屉与新建弹窗结构设计

**Files:**
- Modify: `demo/templates/index.html`
- Test: `demo/tests/test_admin_experiments_drawer_dom.py`

- [ ] **Step 1: 编写 DOM 结构断言测试**
创建 `demo/tests/test_admin_experiments_drawer_dom.py`，断言 `#adminExperimentDrawer`、`#adminExpSelect`、`#adminExpStartBtn`、`#adminExpStopBtn` 以及新建弹窗 `#createExperimentModal` 存在。

- [ ] **Step 2: 运行测试验证失败**
`~/.pyenv/versions/3.9.6/bin/pytest demo/tests/test_admin_experiments_drawer_dom.py -v`

- [ ] **Step 3: 在 `index.html` 中插入 `#adminExperimentDrawer` 与 `#createExperimentModal`**
紧随 `#adminGreyDrawer` 之后插入抽屉，提供清晰优雅的 Control vs Treatment 视觉对比与操作栏。

- [ ] **Step 4: 运行测试验证通过**
`~/.pyenv/versions/3.9.6/bin/pytest demo/tests/test_admin_experiments_drawer_dom.py -v`

- [ ] **Step 5: 提交代码**
`git add demo/templates/index.html demo/tests/test_admin_experiments_drawer_dom.py && git commit -m "feat(ui): add ab experiment drawer and creation modal in engine config"`

---

### Task 3: 前端数据交互、状态绑定与深链闭环 (JavaScript)

**Files:**
- Modify: `demo/static/js/main.js`
- Test: `demo/tests/test_admin_experiments_drawer_dom.py`

- [ ] **Step 1: 编写 JS 逻辑单元测试**
在 `demo/tests/test_admin_experiments_drawer_dom.py` 中增加对 JS 核心方法定义的断言。

- [ ] **Step 2: 在 `main.js` 中实现实验管理函数**
实现 `loadAdminExperimentsList`, `onAdminExpSelectChange`, `startCurrentExperiment`, `stopCurrentExperiment`, `openCreateExperimentModal`, `submitCreateExperiment`, `gotoExperimentObservatory`。

- [ ] **Step 3: 运行语法检查与断言测试**
`node -c demo/static/js/main.js`
`~/.pyenv/versions/3.9.6/bin/pytest demo/tests/test_admin_experiments_drawer_dom.py -v`

- [ ] **Step 4: 提交代码**
`git add demo/static/js/main.js demo/tests/test_admin_experiments_drawer_dom.py && git commit -m "feat(js): implement ab experiment drawer controls and observatory deep link"`

---

### Task 4: Playwright 真实浏览器端到端回归与断言截图

**Files:**
- Create: `demo/tests/test_playwright_ab_experiment_drawer.py`

- [ ] **Step 1: 编写真实浏览器 E2E 测试**
测试 admin 进入系统与引擎配置，展开抽屉 2，切换实验，创建新实验，验证深链跳转至 `03 A/B 科学实验台`。
生成高清截图：`demo/gui-test-screenshots/admin_ab_experiment_drawer.png`。

- [ ] **Step 2: 运行全量 E2E 测试**
`~/.pyenv/versions/3.9.6/bin/pytest demo/tests/test_playwright_ab_experiment_drawer.py demo/tests/test_playwright_canary_ab_decoupled.py demo/tests/test_admin_tabs_consolidation_e2e.py -v`

- [ ] **Step 3: 提交代码**
`git add demo/tests/test_playwright_ab_experiment_drawer.py && git commit -m "test(e2e): add playwright verification for ab experiment engine drawer"`

---

### Task 5: 成果审查与终验交付

- [ ] 检查生成的截图质量
- [ ] 确认全流程双向闭环（系统与引擎配置 -> A/B 科学实验台 -> 系统与引擎配置）
- [ ] 汇总并汇报用户

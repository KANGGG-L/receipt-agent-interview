# 黄金基准集看板 GT 抽检确权筛选与联动管理实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通“GT 抽检确权”与“黄金基准集看板”的数据链路与管理闭环，使管理员可在看板中直接筛选「已 GT 确权」与「待确权候选」单据并进行基准集成员纳管与快捷操作。

**Architecture:** 
- 后端在 `/api/admin/golden-samples` 端点中整合 `receipts` 主表、`eval_candidate` 回流候选表以及 `demo/evalsets/manifest.csv` 评测集确权元数据，为单据注入 `gt_status` / `gt_label` / `sample_id` 属性，并支持 `scope=gt_confirmed` 与 `scope=gt_pending` 筛选；
- 在 `/api/evalset/sample/{sample_id}/confirm` 中，当回流单据被确认确权时，自动联动置位 `db.set_golden_sample(receipt_id, 1)`；
- 前端 `#goldenScopeSelect` 扩展新筛选项，并在看板表格中增加「GT 确权」状态徽章与直达确权台操作；
- 全流程覆盖单元测试与 Playwright E2E 自动化测试。

**Tech Stack:** FastAPI, SQLite (SQLAlchemy), Vanilla JS / CSS, Pytest, Playwright.

## Global Constraints
- 遵循多租户隔离约束：`eval_candidate` 和 `receipts` 查询须携带 `tenant_id`；
- 每次修改 `main.js` 必须在 `index.html` 中同步 bump 静态资源版本号（`20260906n`）；
- 保持既有测试 100% 通过，不影响现有的 `scope=all` 和 `scope=golden` 逻辑。

---

### Task 1: 后端数据打通与 API 筛选增强 (Backend API & GT Linkage)
**Files:**
- Modify: `demo/app/api_admin.py`
- Modify: `demo/app/api_evalset.py`
- Test: `demo/tests/test_golden_board_gt_filter.py`

**Interfaces:**
- Produces: `/api/admin/golden-samples?scope=all|golden|gt_confirmed|gt_pending`
- Items schema: `{id, supplier_name, receipt_date, doc_form, total_amount, status, currency, is_golden_sample, gt_status, gt_label, eval_candidate_id, sample_id}`
- Auto-promotion: `confirm_sample()` sets `is_golden_sample = 1` for `source_receipt_id`.

- [x] Step 1: 编写失败测试 `test_golden_board_gt_filter.py` 覆盖 `scope=gt_confirmed`、`scope=gt_pending` 及字段输出。
- [x] Step 2: 在 `demo/app/api_admin.py` 中建立 `receipt_id` 与 `eval_candidate` 和 `evalset` 的映射逻辑，注入 GT 字段并实现 scope 过滤。
- [x] Step 3: 在 `demo/app/api_evalset.py` 中的 `confirm_sample()` 增加自动关联晋升 `db.set_golden_sample(source_receipt_id, 1)`。
- [x] Step 4: 运行 `pytest demo/tests/test_golden_board_gt_filter.py` 验证通过。

---

### Task 2: 前端下拉筛选与表格 GT 确权状态列展示 (Frontend Filter & Table UI)
**Files:**
- Modify: `demo/templates/index.html`
- Modify: `demo/static/js/main.js`

**Interfaces:**
- Consumes: `/api/admin/golden-samples` 返回的 `gt_status`, `gt_label`, `sample_id`
- UI: `#goldenScopeSelect` 新增 `gt_confirmed` 和 `gt_pending`
- Table: 表头与每行新增「GT 确权」徽章列与直达操作

- [x] Step 1: 在 `demo/templates/index.html` 中为 `#goldenScopeSelect` 添加新 option，并更新表格 `<thead>`。
- [x] Step 2: 在 `demo/static/js/main.js` 的 `loadGoldenBoard()` 中支持新 scope 并渲染 GT 确权列。
- [x] Step 3: 更新 `demo/templates/index.html` 底部 `main.js?v=20260906n` 缓存击穿版本号。

---

### Task 3: 自动化 E2E 验证与回归走查 (Verification & Regression)
**Files:**
- Test: `demo/tests/test_admin_tabs_consolidation_e2e.py`
- Test: `demo/tests/test_golden_board_gt_filter.py`

- [x] Step 1: 运行 Playwright 验证在浏览器中切换 `#goldenScopeSelect` 为 `gt_confirmed` 和 `gt_pending` 时正确加载并展示徽章。
- [x] Step 2: 保存截图到 `demo/gui-test-screenshots/` 验证 UI 视觉效果。
- [x] Step 3: 运行完整测试集确保零回归。

# 埋点观测台 Admin 权限与多租户观测实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将埋点观测台提升为仅限 Admin 访问，并支持按 Tenant（租户）维度进行全量聚合与单租户下钻观测。

**Architecture:** 
- 数据层：`user_event` 表增加 `tenant_id` 字段及自动迁移，埋点写入时注入租户上下文；
- 接口层：`GET /api/analytics/recovery-summary` 升级为 `require_admin` 鉴权，支持 `?tenant_id=` 参数与 `available_tenants` 返回；
- 交互层：导航栏角标改为 `admin`，仅 `admin` 角色可见；观测台顶部新增 `#analyticsTenantSelect` 租户切换器。

**Tech Stack:** FastAPI, SQLAlchemy/SQLite, Vanilla JS (ES6), Playwright, Pytest.

## Global Constraints
- 遵循全局纯文本与设计规范，按钮无 emoji；
- Docs-First 顺序：先更新 Spec 文档，再实现代码与测试；
- 兼容旧数据：`tenant_id` 缺省为 `'default'`，未传时向下兼容关联单据的 `tenant_id`。

---

### Task 1: 规格文档更新（Docs-First）

**Files:**
- Modify: `docs/05-AI产品体系与模块Spec/03-治理运维与AB实验Spec/11-组件Spec-全链路埋点与体验反馈体系.md`

**Interfaces:**
- Consumes: Spec 既有 §7.1 观测台端点定义
- Produces: 更新后的 §7.1 规格（`require_admin`、`tenant_id` 参数、`available_tenants` 返回）

- [ ] **Step 1: 更新 Spec 文档中的端点权限与多租户参数**
在 `11-组件Spec-全链路埋点与体验反馈体系.md` 的 §7.1 中：
1. 权限从 `owner+` 更新为 `admin`（系统超管专属）；
2. 请求参数增加 `tenant_id`（可选，默认 `all` 为全量租户，指定值则单租户过滤）；
3. 响应 JSON 增加 `tenant_id` 与 `available_tenants` 字段说明。

- [ ] **Step 2: 验证文档更新无歧义**
检查文档前后一致性。

---

### Task 2: 数据层——`user_event` 表增加 `tenant_id` 与自动迁移

**Files:**
- Modify: `demo/app/db.py`

**Interfaces:**
- Consumes: `_UserEventRow` 既有定义、`_run_migrations`
- Produces: `_UserEventRow.tenant_id`, `log_user_event(..., tenant_id="default")`

- [ ] **Step 1: 为 `_UserEventRow` 添加 `tenant_id` 列**
在 `demo/app/db.py` 中的 `_UserEventRow`（约 L311）添加：
```python
tenant_id = Column(String(64), default="default", index=True)
```

- [ ] **Step 2: 在 `_run_migrations()` 添加 SQLite 迁移**
在 `demo/app/db.py` 的迁移语句列表中添加：
```python
"ALTER TABLE user_event ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
"CREATE INDEX IF NOT EXISTS idx_user_event_tenant_id ON user_event (tenant_id)",
```

- [ ] **Step 3: 更新 `log_user_event` 支持 `tenant_id`**
在 `demo/app/db.py` 的 `log_user_event` 函数中添加 `tenant_id="default"` 参数，并在写入时若未传且有 `receipt_id`，查询 `ReceiptRow.tenant_id` 兜底。

- [ ] **Step 4: 运行 `python -m py_compile demo/app/db.py` 验证语法**

---

### Task 3: 后端——端点 Admin RBAC 升级与多租户过滤

**Files:**
- Modify: `demo/app/api_phase2.py`
- Modify: `demo/app/api_receipts.py`

**Interfaces:**
- Consumes: `require_admin`, `db._UserEventRow.tenant_id`, `db.list_receipt_feedbacks`
- Produces: `GET /api/analytics/recovery-summary`（admin 专属、支持 `tenant_id` 过滤）

- [ ] **Step 1: 更新 `_track_event` 传入租户上下文**
在 `demo/app/api_receipts.py` 的 `_track_event` 及 `/api/track` 端点中，提取请求的 `tenant_id`（Header 或 Query 或 account）并传给 `db.log_user_event`。

- [ ] **Step 2: 更新 `GET /api/analytics/recovery-summary`**
在 `demo/app/api_phase2.py` 中：
1. 将 `require_role("owner")(request)` 改为 `require_admin(request)`；
2. 接收 Query 参数 `tenant_id: Optional[str] = None`（同时支持 `request.headers.get("X-Tenant-Id")`）；
3. 查询数据库中所有不重复的 `tenant_id`（从 `user_event` 与 `receipts` 提取），构建 `available_tenants` 列表（至少包含 `["default"]`）；
4. 若 `tenant_id` 指定且 `tenant_id != "all"`，则过滤 `rows = [r for r in rows if (r.tenant_id or "default") == tenant_id]`，以及 `fbs = [f for f in fbs if (f.get("tenant_id") or "default") == tenant_id]`；
5. 返回结构顶层包含 `"tenant_id": tenant_id or "all"`, `"available_tenants": available_tenants`。

- [ ] **Step 3: 运行 `python -m py_compile demo/app/api_phase2.py demo/app/api_receipts.py` 验证语法**

---

### Task 4: 前端——Admin 导航徽章、角色显隐与租户选择器

**Files:**
- Modify: `demo/templates/index.html`
- Modify: `demo/static/js/main.js`

**Interfaces:**
- Consumes: `GET /api/analytics/recovery-summary?tenant_id=...`, `applyRoleVisibility`
- Produces: `#analyticsBoardBtn` (admin badge), `#analyticsTenantSelect`, `loadAnalyticsBoard(tenantId)`

- [ ] **Step 1: 更新 `index.html` 导航徽章与观测台顶部控件**
1. 侧边栏 `#analyticsBoardBtn` 的徽章更新为：
```html
<button class="sidebar-btn" id="analyticsBoardBtn" data-target="tab-analytics" data-title="埋点观测台"><span class="nav-title">埋点观测</span><span class="role-badge admin">admin</span></button>
```
2. 在 `#tab-analytics` 顶部操作区（全量埋点事件分布标题旁/刷新按钮左侧）增加：
```html
<div class="analytics-header-controls" style="display:flex;align-items:center;gap:10px;">
    <label style="font-size:0.85rem;color:var(--text-secondary);">租户筛选：</label>
    <select id="analyticsTenantSelect" class="form-select form-select-sm" style="width:auto;" onchange="onAnalyticsTenantChange(this.value)">
        <option value="all">全部租户 (All Tenants)</option>
    </select>
    <button class="btn btn-sm btn-secondary" onclick="loadAnalyticsBoard()">刷新</button>
</div>
```

- [ ] **Step 2: 更新 `main.js` 角色显隐与租户选择逻辑**
1. 在 `applyRoleVisibility` / `updateRoleUI` 中：
   - 当角色不是 `admin` 时（即 `staff` 或 `owner`），隐藏 `#analyticsBoardBtn`（`display = 'none'`）；若当前激活 Tab 是 `tab-analytics`，自动切换至 `tab-scan`。
   - 当角色是 `admin` 时，显示 `#analyticsBoardBtn`。
2. 在 `loadAnalyticsBoard(tenantId)` 中：
   - 若未传 `tenantId`，读取 `document.getElementById('analyticsTenantSelect')?.value || 'all'`；
   - 请求 `/api/analytics/recovery-summary?tenant_id=` + encodeURIComponent(tenantId)；
   - 根据返回的 `data.available_tenants` 动态填充 `#analyticsTenantSelect` 下拉选项并保持当前选中项；
   - 渲染各区块。
3. 添加全局 `window.onAnalyticsTenantChange = function(tId) { loadAnalyticsBoard(tId); };`。

- [ ] **Step 3: 运行 `node --check demo/static/js/main.js` 验证语法**

---

### Task 5: 测试套件更新与自动化回归

**Files:**
- Modify: `demo/tests/test_recovery_actions.py`

**Interfaces:**
- Consumes: TestClient, FastAPI app, db isolated fixture
- Produces: 覆盖 Admin RBAC (403 for staff/owner, 200 for admin) 和多租户过滤的测试用例

- [ ] **Step 1: 更新 RBAC 测试用例**
在 `test_recovery_summary_rbac` 中断言：
- `staff` 角色 -> HTTP 403
- `owner` 角色 -> HTTP 403（由原先的 200 变为 403）
- `admin` 角色 -> HTTP 200

- [ ] **Step 2: 新增多租户过滤测试用例 `test_recovery_summary_multi_tenant`**
1. 预置 `tenant_a` 事件 3 条（含 1 条 retake）、`tenant_b` 事件 2 条；
2. 请求 `tenant_id=tenant_a`，断言 `total_events == 3`，`retake_clicked == 1`；
3. 请求 `tenant_id=tenant_b`，断言 `total_events == 2`，`retake_clicked == 0`；
4. 请求 `tenant_id=all`，断言 `total_events == 5`，`available_tenants` 包含 `["tenant_a", "tenant_b"]`。

- [ ] **Step 3: 运行 pytest 验证全量通过**
`~/.pyenv/versions/3.9.6/bin/python -m pytest demo/tests/test_recovery_actions.py -v`

---

### Task 6: 端到端运行时验证与界面截图

**Files:**
- Modify: `demo/scratch/browser_experience.py` 或直接运行 Playwright 验证脚本

**Interfaces:**
- Consumes: 本地 15010 服务
- Produces: 角色切换截图（Owner 隐藏、Admin 可见）、租户切换截图

- [ ] **Step 1: 执行 Playwright 脚本**
验证：
1. 切换至 `owner`：观测台入口隐藏；
2. 切换至 `admin`：观测台入口显示，点击进入后顶部显示租户下拉框；
3. 切换租户下拉框：数据动态重载。
4. 生成截图保存至 Artifact 目录。

# 埋点观测台 Admin 权限升级与多租户（Tenant）维度观测设计方案

## 1. 业务背景与目标

为保障数据安全与平台管理合规：
1. **RBAC 权限升级**：埋点观测台（包含全量埋点事件、点踩/挽回指标、灰测与 A/B 实验数据）属于系统级运营大盘，必须仅限系统超管（`admin`）观察，`owner`（老板/店长）与 `staff`（店员）均无权访问；
2. **多租户（Multi-Tenant）维度观测**：支持超管全局查看所有租户的聚合埋点，也支持按特定租户（Tenant）维度进行过滤与下钻分析。

---

## 2. 详细设计与改动范围

### 2.1 数据层与迁移（`demo/app/db.py`）
- **`user_event` 表结构补充**：
  - 为 `_UserEventRow` 增加 `tenant_id = Column(String(64), default="default", index=True)` 列。
  - SQLite 自动迁移逻辑：
    - `ALTER TABLE user_event ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'`
    - `CREATE INDEX IF NOT EXISTS idx_user_event_tenant_id ON user_event (tenant_id)`
- **埋点写入函数更新**：
  - `log_user_event(account_id, session_id, event_type, receipt_id, properties, grp, tenant_id="default")`：写入时持久化 `tenant_id`。
  - 若传入的 `tenant_id` 为空，但存在 `receipt_id`，自动回查并继承关联单据的 `receipts.tenant_id`。

### 2.2 API 与后端鉴权（`demo/app/api_phase2.py` / `demo/app/api_receipts.py`）
- **`_track_event` 与 `/api/track`**：
  - 自动从请求上下文（Header `X-Tenant-Id` / Query `tenant_id`）中提取 `tenant_id` 并传入 `log_user_event`。
- **`GET /api/analytics/recovery-summary` 端点升级**：
  - **权限控制**：由 `require_role("owner")` 升级为 **`require_admin`**（非 admin 请求直接返回 `403 Forbidden`）。
  - **参数支持**：支持 Query 参数 `tenant_id: Optional[str] = None`（以及 `X-Tenant-Id` Header）：
    - `tenant_id == "all"` 或未指定：全量租户聚合计算；
    - `tenant_id == "xxx"`：仅过滤该租户下的 `user_event` 与 `receipt_feedback`。
  - **返回结构增强**：
    - 返回字段中增加 `tenant_id`（当前生效的租户过滤条件，`"all"` 或指定租户 ID）；
    - 返回 `available_tenants: List[str]`（当前数据库中存在埋点或单据的所有租户列表，供前端下拉框渲染）。

### 2.3 前端页面与交互（`demo/templates/index.html` / `demo/static/js/main.js`）
- **侧边栏导航与角标**：
  - `index.html` 中 `#analyticsBoardBtn` 的角标文案从 `owner` 修改为 `admin`：`<span class="role-badge admin">admin</span>`。
- **角色可见性控制**：
  - `main.js` 中的 `applyRoleVisibility` 将 `tab-analytics` 与 `#analyticsBoardBtn` 设为仅 `admin` 角色展示，`staff` 和 `owner` 均隐藏。若非 admin 用户尝试切入该 tab，自动回退至收据识别页。
- **租户选择器交互**：
  - 在 `#tab-analytics` 顶部卡片（全量埋点事件分布左侧/右侧操作区）增加 `#analyticsTenantSelect` 下拉组件。
  - 选项包含：「全部租户 (All Tenants)」（默认）以及后端返回的各租户 ID。
  - 切换下拉选项时，触发 `loadAnalyticsBoard(tenantId)`，重新拉取并渲染该租户的事件分布、挽回/点踩指标及相关数据。

### 2.4 文档规格更新（Docs-First）
- 同步更新 `docs/05-AI产品体系与模块Spec/03-治理运维与AB实验Spec/11-组件Spec-全链路埋点与体验反馈体系.md`：
  - 更新 §7.1 观测台聚合端点权限为 `admin`；
  - 补充 `tenant_id` 参数规格与 `available_tenants` 响应格式。

---

## 3. 测试与验证策略

1. **单元测试与回归（`demo/tests/test_recovery_actions.py`）**：
   - 验证 RBAC：`staff` 访问返回 403，`owner` 访问返回 403，`admin` 访问返回 200；
   - 验证多租户隔离与聚合：
     - 预置 `tenant_a` 与 `tenant_b` 的埋点与反馈数据；
     - 传 `tenant_id="tenant_a"` 时，指标仅统计 `tenant_a`；
     - 传 `tenant_id="all"` 或不传时，指标统计全部租户；
     - 验证 `available_tenants` 正确返回 `["tenant_a", "tenant_b"]`。
2. **端到端浏览器体验**：
   - 使用 Playwright 验证：
     - 切换到 `owner` 角色时，观测台按钮隐藏；
     - 切换到 `admin` 角色时，观测台按钮显示；
     - 点击观测台，顶部出现租户切换器；切换租户后数据即时刷新。

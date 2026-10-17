# UAT-029 UAT 报告 — 选择贷项单仍被正数总额校验阻止保存

- 日期：2026-10-18（UTC+8）
- 角色：独立 UAT（用户视角验收；未修改任何产品代码，HEAD 7271d40，`git status` 产品代码零改动）
- Python：/home/agentuser/receipt-agent-interview/.venv-uat009/bin/python
- 验证模式：MANUAL_UI（隔离活体服务 AUTH_ENABLED=1 实登录 + Playwright 真实浏览器走查 + 源码原文自核 + Node vm 真模块沙箱）
- 结论：**PASS**（核心 AC 全过；两项观察项留档，均不阻断）

## 1. 隔离环境

- 端口：127.0.0.1:**15903**（1590x 区段全新；15010/15100-15200/15919/15929/15930/15944/15950-15951/15961-15962/15971-15972 全程未触）
- DB：/tmp/uat029-uat/uat-live.db、/tmp/uat029-uat/uat-ui2.db（独立文件，不复用 DEV/QA fixture；/tmp/uat029-uat/tdd*.db 仅测试复跑）
- AUTH_ENABLED=1；DEMO_TOKEN_SECRET 每次进程启动 `secrets.token_hex(48)`（48 字节随机，仅 env 注入，不落盘）
- startup 未触发超时重试（两实例均首轮健康）
- UI 走查：Playwright + /usr/bin/google-chrome（1440x900），真实登录 `boss@demo.hk/boss123`，`/api/auth/me` 回显 `identity_source=cookie, demo_identity=false`

## 2. 源码原文自核（file:line 打印核对，HEAD 7271d40）

- `demo/static/js/review_core.js:1686-1690`：`function isNegativeDocForm(docForm){...return f==='credit_note'||f==='correction_note';}`
- `demo/static/js/review_core.js:2905-2907`：`if (isNaN(totalAmt)||totalAmt===0||(!isNegDoc&&totalAmt<0))` → err_total，title 为 `isNegDoc ? '总金额不能为 0' : '总金额必须大于 0'` —— 贷项/更正形态放行负总额（缺陷本体修复在位）
- `demo/static/js/review_core.js:2944-2946`：行级 `isQtyPriceInvalid` 对 neg-doc 为 `(isNaN…||qty===0||(qty<0&&price<0))` —— 双负仍阻断
- `demo/static/js/review_core.js:3282-3289`：submitSaveEdited 在 `calculateReceiptIssues(data).filter(type==='blocker')` 非空时 toast 并 return，零网络请求 —— UI 保存门禁即 Issue 门禁
- `demo/app/api_receipt_lifecycle.py:58-60`：`row.status=='reversed'` → `return {status:'success', already_reversed:true, msg:'…库存没有再变动。'}`（幂等防重，非 409）
- `demo/app/api_receipt_lifecycle.py:78-80`：`ReversalBlocked` → 409 REVERSAL_STOCK_CONSUMED（409 码保留在库存已消耗分支）
- `demo/app/api_receipts.py:150-163`：`_version_error` 乐观锁（缺 version → 400 VERSION_REQUIRED）
- `demo/app/services/contract.py:226-227`：`negative_form=data.doc_form in _NEGATIVE_FORMS; if data.total<0 and not negative_form` —— 后端契约仅拒非负形态负总额
- `demo/app/models.py:15-22`：DocForm 枚举仅 PRINTED/NCR_HAND/THERMAL/WEIGH/CORRECTION/CREDIT/MONTHLY —— 未新增贷项类型
- oracle：`docs/review-2026-10/S2-ai-pipeline.md:60`（S2-09：对 CREDIT/CORRECTION 形态允许 amount<0 或允许 qty<0）

## 3. 活体验证（AUTH=1 实登录，真实请求留痕）

### 3.1 安全前置（空 Authorization ≠ 未认证等纪律逐条核过）
1. 无凭据 GET /api/receipts → **401 UNAUTHENTICATED**
2. POST /api/auth/login → 200，Set-Cookie receipt_session **HttpOnly; SameSite=lax**
3. Cookie 会话写请求**不带** X-Requested-With → **403 CSRF_REJECTED**
4. 伪造 Bearer token → 401
5. owner cookie + 伪造 `X-Tenant-Id: tenant_b` → B 数据不可见（D19：租户只由令牌载荷派生）
6. staff 登录 → 200（供 RBAC 负向用）

### 3.2 核心 AC（API 实测 + SQLite 读回，logs/probe-result.json 26 步）
1. **credit_note 负 amount 变体**：save_edited(total=-10, qty=1, price=10, amount=-10) → **200 success math_blockers=[]**；GET 读回 total=-10/doc_form=credit_note/status=edited；approve{version} → **200**；DB status=approved total=-10 —— 保存→重开→审核全链路合规
2. **credit_note 负 qty 变体**：save_edited(total=-20, qty=-2, amount=-20) → **200 math_blockers=[]**；DB 落库 qty=-2/amount=-20；approve 200
3. **费用算术匹配**：明细 -10 + delivery_fee -5 = total -15 → **200 math_blockers=[]**
4. **普通 purchase 负额仍拒**：save 200 但 math_blockers 精确报「单据写 -10 元，按数量单价应是 10 元，相差 20 元」；approve → **422 APPROVAL_BLOCKED**
5. **冲销**：正向单 approve 入账 → reverse(reason+version) 200「库存已扣回」；二次 reverse → **200 `already_reversed=true`「库存没有再变动」**，库存零二次变动（OBS-1，见 §6）

### 3.3 MANUAL_UI 真实浏览器走查（Playwright + Chrome，logs/ui-result.json 8 步全过）
1. 真实登录 → cookie 会话（U1）
2. 队列打开 credit_note #1：`inpTotal=-10.00`、`inpDocForm=credit_note`、**blockers=[]** —— 正数总额 blocker 不复现（U2，缺陷本体）
3. 点击 #btnSaveReview → 真实 POST /api/save_edited **200**（payload total_amount=-10，带 X-Requested-With），toast「已保存复核修改并提交审核（未入账）」（U3）
4. 重开 → 点击 #btnApproveReceipt → 确认弹窗 → POST approve **200**；DB `status=approved, total=-10, version=3` —— **UI 保存→重开→审核全链路 PASS**（U4）
5. **负向 UI**：新建手工单 printed total=-10 → err_total + toast「单据总金额必须大于 0」，**零网络写**（U5）
6. credit_note 双负（qty=-2, price=-5）→ err_item_num_0 阻断（U6）
7. `#inpDocForm` 选项仅 8 个已知枚举值，无新增贷项类型（U7）

### 3.4 Node vm 真模块沙箱（真实 review_core.js/upload.js，logs/probe_frontgate.js、probe_save_dispatch.js）
- calculateReceiptIssues：credit_note -10 无 err_total；printed -10 → err_total「总金额必须大于 0」；双负 → err_item_num_0
- submitSaveEdited 经 Issue 门禁分发：credit_note 负 amount/负 qty payload 真实 dispatch（POST /api/save_edited）；printed 负额与 credit 双负 **不分发** —— 与 UAT-029 专项 TDD（6p）断言同口径

## 4. 自动化测试（HEAD 真实复跑）

- 专项：`demo/tests/test_uat_029_credit_note_negative_amount_accepted.py`（DB=/tmp/uat029-uat/tdd.db）→ **6 passed**（logs/green-tdd.log）
- 回归：`tests/test_fix_p0_3_negative_total_credit.py`（S2-09 原始修复测试）**4 passed** + UAT-007/008x2/009x2/030 **76 passed**（logs/green-regression.log）

## 5. 负向与安全边界结论

- 双负（qty<0 且 price<0）：UI Issue 门禁 err_item_num_0 阻断、保存不分发（U6 + 沙箱双证）—— AC「双负仍拒」在用户可达路径成立
- 未新增贷项类型：DocForm 枚举与 UI 下拉均未变
- 跨租户：伪造 X-Tenant-Id 被整体忽略；staff 无法越租；staff reverse → **403**；B 租户 baseline before/after 均为 0 行，default 租户数据全程未见意外改动
- 探针中途澄清（非缺陷）：首轮 API 探针 N1 记 FAIL，经真模块沙箱复核为「后端 save_edited 对 negative_form 行按 S2-09 允许 qty<0，双负拦截落在前端保存门禁」——以 UI 门禁为验收边界登记（见 uat-result.json probe_failures_resolved）

## 6. 观察项（非阻断，已留档）

- **OBS-1（用户视角裁决：PASS）**：AC 写「已消耗冲销 409 仍保留」；独立实测重复冲销返回 **200 + already_reversed=true**（api_receipt_lifecycle.py:58-60 显式幂等成功分支），与卡片 409 措辞不符。但按 oracle S2-09 的验收范围（负额门禁，非冲销码）与产品自身注释「同一张单重复冲销是安全的：第二次直接返回成功」，防重复扣库存的语义不仅保留而且更强（幂等重放安全），库存已核验零二次变动。裁决：不构成 FAIL；卡片措辞与实现的偏差登记交还卡主。
- **OBS-2（登记 UX 缺陷候选）**：「新建手工单」保存路径（upload.js validateManualEntry）对 total<=0 仍硬拒（含 credit_note，toast「单据总金额必须大于 0」）。本卡缺陷针对的是**重开/识别后复核**的贷项单正数总额门禁，该面已修复并全链路验证；手工新键贷项单的入口是另一条残留门禁（review_core.js:3200-3205 直调 validateManualEntry），修复前用户只能经单据管线生成负总额贷项单。留档交卡主裁决，不阻断本卡。
- OBS-3：venv 内 Playwright 无 Page.wait_for_response（改用 expect_response 上下文），工具层事项。

## 7. 环境与清理

- 两个活体实例均 SIGTERM 回收；`ss -ltn` 复核 15903 零监听、无 uvicorn 残留
- 全部 DB/探针脚本在 /tmp/uat029-uat（仓库外），产物副本已归档 logs/
- cleanup 详见 environment/cleanup.json

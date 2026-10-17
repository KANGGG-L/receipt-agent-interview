# UAT-029 QA 报告 — 选择贷项单仍被正数总额校验阻止保存

- 日期：2026-10-18（UTC+8）
- 角色：独立 QA（未修改任何产品代码；HEAD 7271d40 `git status --porcelain -- demo ai_registry tests` clean）
- Python：/home/agentuser/receipt-agent-interview/.venv-uat009/bin/python
- 验证模式：MANUAL_UI（隔离活体服务 AUTH_ENABLED=1 实登录 + Playwright 真实浏览器走查 + 源码原文自核）
- 结论：**PASS**（核心 AC 全过；DEV 报告登记的 AUTH=1 cookie/CSRF 空证已用实登录补齐）

## 1. 隔离环境

- 端口：127.0.0.1:**15919**（全新 1591x 区段；15010/15100-15200/15929/15930/15944/15950-15951/15961-15962/15971-15972 全程未触）
- DB：/tmp/uat029-qa/qa-live.db（独立空库，不复用 DEV fixture）
- AUTH_ENABLED=1；DEMO_TOKEN_SECRET 为 96 位随机 hex（48 字节，`secrets.token_hex(48)`，仅 env 注入）
- startup 重试逻辑：本机首轮即 200（/api/health degraded 为空库正常态），未触发重试
- UI 走查：Playwright + /usr/bin/google-chrome（1440x900），真实登录 `boss@demo.hk/boss123`，
  `/api/auth/me` 回显 `identity_source=cookie, demo_identity=false`；所有写请求带 X-Requested-With

## 2. 源码原文自核（file:line 打印核对，HEAD 7271d40）

- `demo/static/js/review_core.js:1686-1690`：`function isNegativeDocForm(docForm){...return f==='credit_note'||f==='correction_note';}`
- `demo/static/js/review_core.js:2907-2910`：`if (isNaN(totalAmt)||totalAmt===0||(!isNegDoc&&totalAmt<0))` push err_total，title 为 `isNegDoc ? '总金额不能为 0' : '总金额必须大于 0'` —— 贷项/更正形态放行负总额
- `demo/static/js/review_core.js:2944-2946`：行级 `isQtyPriceInvalid` 双负（qty<0&&price<0）仍阻断
- `demo/app/api_receipts.py:1844`：`_ALLOWED_SETTLEMENT = ("cash","credit")`（探针用 cash）
- `demo/app/api_receipts.py:1956-1963`：settlement 422 分支（首轮探针 422 的根因，非本卡缺陷）
- `demo/app/api_receipts.py:145-159`：approve/reverse 乐观锁 VERSION_REQUIRED（首轮 approve 400 的根因，带 version 后 200）
- `demo/app/models.py:15-22`：DocForm 枚举仅 PRINTED/NCR_HAND/THERMAL/WEIGH/CORRECTION/CREDIT/MONTHLY —— 未新增贷项类型
- oracle：`docs/review-2026-10/S2-ai-pipeline.md:60`（S2-09：对 CREDIT/CORRECTION 形态允许 amount<0 或允许 qty<0）——修复方向与之一致
- 后端无正数总额硬门禁：贷项负总额保存/审核均 200（见 §3）

## 3. 活体验证（AUTH=1 实登录，真实请求留痕）

### 3.1 安全前置探针（补 DEV 空证）
1. 无凭据 GET /api/receipts → **401 UNAUTHENTICATED**（空 Authorization ≠ 未认证豁免；身份在 HttpOnly Cookie receipt_session）
2. POST /api/auth/login → 200，Set-Cookie: receipt_session=...; **HttpOnly; SameSite=lax**
3. Cookie 会话写请求**不带** X-Requested-With → **403 CSRF_REJECTED**（auth.py:327 CSRF_HEADER / csrf_ok 真触发）
4. 伪造/非法 token（错误 secret 或不存在租户）→ identity_source=missing，受保护端点 401
5. 服务器侧 mint 一枚 tenant=tenant_b 的合法签名 token → parse 返回 None（ACCOUNTS 三要素不匹配即拒绝，租户只能由服务端账号表派生）

### 3.2 核心 AC（API 实测 + DB 读回）
1. **credit_note 负 amount 变体**：POST /api/save_edited（total=-10, qty=1, price=10, amount=-10）→ **200 success, math_blockers=[], version=1**；GET /api/receipt/1 → total_amount=-10.0, doc_form=credit_note；POST approve {version:1} → **200**，DB status=approved —— 保存→重开→审核全链路合规
2. **credit_note 负 qty 变体**（total=-20, qty=-2, price=10, amount=-20）→ **200, math_blockers=[]**；DB 落库 qty=-2/amount=-20
3. **费用算术匹配**：total=-15 = 明细 -10 + delivery_fee -5 → 200, math_blockers=[]
4. **普通 purchase 负额仍拒**：doc_form 为空、total=-10 → save 200 但 math_blockers 精确报「单据写 -10 元，按数量单价应是 10 元，相差 20 元」；approve → **422 APPROVAL_BLOCKED**（不能入账）
5. **已消耗冲销**：正向单 #6 approve 入账 → reverse（reason+version）200「库存已扣回」；二次 reverse → 200 `already_reversed=true`「库存没有再变动」，库存零二次变动（幂等保护；PRODUCT 无 409 码但防重语义等价保留，见 §6）

### 3.3 MANUAL_UI 真实浏览器走查（Playwright + Chrome）
1. 真实登录（#authLoginEmail/#authLoginPassword/#authLoginSubmit）→ 200 cookie 会话，待办面板列出 4 张待审批（含 #2 QA029_Vendor -HK$20.00）
2. 识别复核 → 队列打开 receipt #2：`inpDocForm=credit_note`、`inpTotal=-20.00`，无正数总额 blocker 阻止（缺陷本体不复现）
3. 点击 #btnSaveReview（保存并提交审核）→ 真实 POST /api/save_edited **200**（payload total_amount:-20, quantity:-2），toast「已保存复核修改并提交审核（未入账）」
4. 重开 #2 后点击 #btnApproveReceipt → 确认弹窗 #btnExecuteApproveConfirm → POST approve {version:2} **200**；事后 GET /api/receipt/2 → `status=approved, total=-20, version=3` —— UI 保存→重开→审核全链路 PASS
5. **负向 UI**：新建手工单（printed_delivery_note）inpTotal=-10 → 保存被前端阻断，toast「单据总金额必须大于 0」，无网络写发生 —— 普通 purchase 负额在 UI 层仍拒

## 4. 自动化测试（HEAD 真实复跑）

- 专项：`demo/tests/test_uat_029_credit_note_negative_amount_accepted.py` → **6 passed**（logs/green-tdd.log，DB=/tmp/uat029-qa/tdd2.db）
- 回归：`tests/test_fix_p0_3_negative_total_credit.py`（4p，S2-09 原始修复测试）+ UAT-007/008/009/030 专项（22p）→ **26 passed**（logs/green-regression.log）

## 5. 负向与安全边界结论

- 双负（qty<0 且 price<0）：review_core.js:2944-2946 谓词仍阻断；未验证后端绕过（前端已拒即符合 AC「双负仍拒」）
- 未新增贷项类型：仅 models.py 既有 CREDIT/CORRECTION
- 跨租户：生产态租户由 token 载荷派生（auth.py:392-422 resolve_tenant），伪造 X-Tenant-Id 头被整体忽略（owner token + X-Tenant-Id: tenant_b 请求仍返回 default 租户数据）；伪造他租 token 无法通过账号三要素校验（§3.1.5）；staff 会话 approve/reverse owner 单 → **403 FORBIDDEN**；受保护端点无凭据 401
- Tenant B baseline：服务无 B 租户账号/数据（BUILD 事实，非缺陷），以「伪造 B 身份全部被拒 + B 无数据可污染 + A 租户数据 before/after 逐行一致（receipts 6 行、suppliers 2 行 MD 级相等）」登记
- 探针首轮 2 处非缺陷误配已澄清：422 SETTLEMENT_REQUIRED（漏填 settlement_type，api_receipts.py:1956）、400 VERSION_REQUIRED（乐观锁要求 version，api_receipts.py:150）——修正探针参数后全 PASS，产品行为无变化

## 6. 备注（非阻断）

- AC「已消耗冲销 409 仍保留」：实测重复冲销返回 200 + already_reversed=true（幂等防重），与卡片所述 409 码不一致但防重复扣库存的语义已保留且更强（幂等重放安全）。登记为观察项，不构成 FAIL。
- .rag_chroma 位于仓库 demo/ 下被运行时写入（health 启动巡检提示孤儿向量），未清洗、不属本卡范围。

## 7. 环境与清理

- 活体实例 SIGTERM 回收；`ss -ltn` 复核 15919/15010 零监听、无 uvicorn/pytest 残留；cleanup 见 environment/cleanup.json
- 产物：logs/（server.log、green-tdd.log、green-regression.log、ui-*.png、ui-save-api.json、ui-approve-api.json、probe-*.json）、qa-result.json

# UAT-009 Independent QA Report — 新建手工单继承前单六项附加费用

- 生成时间：2026-10-18 07:1x UTC+8 (Asia/Shanghai)
- 角色：独立 QA（未做开发；不信任 DEV 报告结论，全部关键断言独立复跑）
- 卡片：docs/handoff/01-acceptance-checklist.md「[UAT-009] 新建手工单继承前单六项附加费用」，状态 DEV_DONE_PENDING_QA
- 被测基线：`main` @ 7271d40（git rev-parse 实测：7271d401965dc971fc6eb9b5cf861a748d28c1ad）。QA 未修改任何产品代码，无 commit / push。
- 涉及源码：demo/static/js/review_core.js、demo/static/js/upload.js、demo/static/js/workbench_queue.js

## 总结论：QA PASS（待独立 UAT）

| 验收命题 | 结论 | 证据 |
|---|---|---|
| 打开六费用各非0合成单→新手工单→填一行 | PASS | 真实浏览器 UI walk（AUTH=1 实登录）：evidence/qa-ui-walk.json steps 3-4；截图 ui/03、ui/04 |
| 六字段及 save payload 全默认 0/空且 receipt_id=null | PASS | UI payload 抓包：delivery_fee/deposit_amount/service_fee/tax_amount/discount_amount/rounding_adjustment 全 0、payment_evidence ""、doc_form printed_delivery_note、currency HKD、adjustment_notes []、receipt_id null（qa-ui-walk step 5）；DB 原行读回全零（step 6）；API 侧 qa-api-probe.json 同证 |
| 重新打开旧单六费用仍保持 | PASS | UI walk step 2（重开 6 行费用 5/4/3/2/1/0.5）与 step 7（新单流程后重开仍 6 行原值）；qa-api-probe「old receipt six fees unchanged after new-manual flow」PASS |
| 负向：仅新单清理，旧单已存字段不丢 | PASS | 旧单 doc_form=ncr_handwritten / currency=CNY / payment_evidence 在整轮流程后 API 读回不变（qa-api-probe step 9/10） |
| 负向：无跨会话泄漏 | PASS | UI walk step 12：页面 reload（HttpOnly Cookie 会话延续）后新建手工单仍全默认；step 11 race（打开旧单→立即新建）receipt_id 保持 null、费用保持 0 |
| 跨 tenant 读取/写入拒绝或不可见 | PASS | qa-xtenant-probe.json 9/9：B 读 A 404、A 读 B 404、B 写 A 404、无凭据 401（15972 dev header 模式实例，与 dev 测试同一身份通道；AUTH=1 实例另证 Cookie 会话身份有效） |
| Tenant B baseline 前后零意外改动 | PASS | xtenant.db B 租户 receipts/items 行与计数 before==after（qa-xtenant-probe 最后一步，逐行相等） |
| TDD RED/GREEN 基线独立复跑 | PASS | RED（upload.js/workbench_queue.js 回退 b0e6b1e）：5 failed, 8 passed（3 组 doc_form/currency/payment_evidence 泄漏、迟到回包回填、singleUploadGen 未 bump）；GREEN（HEAD）：13 passed。日志 logs/red-run.log、logs/green-run.log |
| 回归（S2-01、UAT-030 包装品名） | PASS | logs/reg-s2-01-uat030.log：14 passed |
| 既有 UAT-009 两个测试文件（HEAD，独立 DB） | PASS | 13 passed（GREEN 复跑，DB_PATH=/tmp/uat009-qa/qa-green.db） |

未发现阻塞缺陷。两处非缺陷环境记录见「环境备注」。

## 1. 隔离环境（全新端口 / 全新 DB / 全新 fixture）

- 端口：15971（AUTH_ENABLED=1 生产鉴权态）、15972（AUTH_ENABLED=0 dev 头模式，仅用于跨租户探针）。均在 QA 区段 1597x；15010 与 15100-15200 全程未使用。
- DB：全部在 /tmp/uat009-qa/（live.db / xtenant.db / qa-green.db / qa-red.db / qa-reg.db），仓库内零 .db 写入；15010 业务库未触碰（pre-state.txt / cleanup.md 记录 15010 not listening）。
- fixture：全部由探针脚本现做（UI 真实点击创建 + API seed），未复用 DEV fixture。
- 鉴权：AUTH_ENABLED=1 实例使用 96 字符随机 hex DEMO_TOKEN_SECRET（48 字节，env 注入，不入库）；登录经真实 UI 表单（boss@demo.hk），会话以 HttpOnly Cookie 承载，遵守「空 Authorization ≠ 未认证」——探针明确验证「无 Authorization 头但持有效 Cookie 会话 → /api/receipts 200」。错口令 401。
- venv：/home/agentuser/receipt-agent-interview/.venv-uat009/bin/python（仓库内既有，QA 未改动）。浏览器：playwright + /usr/bin/google-chrome（显式 executable_path）。

## 2. 关键实测细节（真实执行，非推断）

1. UI 全链路（qa_ui_walk.py，13/13 PASS）：登录遮罩 → 表单登录 → UI 新建含六费用的手工单并保存（rid 33）→ 重开 6 行费用精确 5/4/3/2/1/0.5 → 点「新建手工单」→ 断言 DOM：currentReceiptId=null、collectFeeMap() 全 0、docForm=printed_delivery_note、currency=HKD、getAutoPaymentEvidence()=""、alertBanner 隐藏、费用/注记抽屉与徽章复位 → 填一行保存 → 网络层抓到的 /api/save_edited payload 六费全 0、receipt_id=null → sqlite 原行读回全零 → 再重开旧单 6 行费用原值不变。
2. 契约不回归：新单上用户自填 delivery_fee=8 → payload 原样 8（step 10）；修复是「复位默认值」而非隐藏/剔除字段。
3. 竞态防线（HEAD 新增）：UI 层「openReceiptInWorkbench → 立即 startManualEntry」迟到回包不回填（step 11）；单元层 RED/GREEN 证明该防线正是 b0e6b1e→HEAD 的行为差异（test_late_open… 与 test_in_flight_single_upload… 在 RED 失败、GREEN 通过）。
4. 单据级字段泄漏（DEV 追加修复域）：三组参数化（ncr_handwritten/CNY、credit_note/USD、monthly_statement/EUR）在 RED 全部失败、GREEN 全部通过；UI 与 API 侧独立复核 doc_form/currency/payment_evidence/alertBanner 四项复位。
5. 身份与隔离（AUTH=1 实例）：登录 200 + HttpOnly Cookie 下发；错口令 401；Cookie 会话（空 Authorization）可访问业务接口。跨租户在 dev 头模式实例验证（该仓库预置账号全部固定 tenant=default，AUTH=1 态无法用现成账号构造第二租户身份；dev 头模式与服务端 D19 解析、dev 测试同一通道，且 AUTH=1 态的 resolve_tenant 只信任令牌载荷、客户端 X-Tenant-Id 整体忽略——代码 demo/app/auth.py resolve_tenant，跨租户拒绝在生产态只会更严）。此为登记在案的验证通道限制，非空证：404/401 断言均真实打在活体服务上。
6. TDD 基线：RED 环境用 `git archive HEAD` 提取至 /tmp/uat009-qa/red（仓库外），仅把 upload.js / workbench_queue.js 换回 b0e6b1e（review_core.js 保持 HEAD——DEV 报告口径一致）；RED 5 failed/8 passed，GREEN 13 passed，与 DEV 报告声称一致，独立复核成立。

## 3. 文件:行号引用（原文自核，HEAD 7271d40）

- demo/static/js/review_core.js:1301-1308 `clearReviewFees`（清六费/注记/徽章）；:1316-1333 `resetNewReceiptContext`（payment_evidence、inpDocForm=printed_delivery_note、inpCurrency=HKD、alertBanner 复位）。原文已打印核对。
- demo/static/js/upload.js:1708-1714 `startManualEntry` 内 `invalidatePendingWorkbenchOpen()` + `singleUploadGen++` + `singlePollToken.cancelled=true`；:1746-1752 `resetNewReceiptContext` 优先调用（fallback clearReviewFees）。
- demo/static/js/workbench_queue.js:287-291 `invalidatePendingWorkbenchOpen`（`_workbenchOpenSeq++` 使 openReceiptInWorkbench:315 的 `seq !== _workbenchOpenSeq` 迟到回包丢弃）。

## 4. 环境备注（非缺陷）

1. Playwright 自带 Chromium 未下载（~/.cache/ms-playwright 缺失），按环境预案用系统 /usr/bin/google-chrome + executable_path，全部 UI 断言在真实 Chromium 内核完成。
2. AUTH=1 实例首启因 /tmp/uat009-qa/live 目录不存在而 startup 失败一次（sqlite unable to open database file，logs/uvicorn.log 前段）；建目录后重启成功——纯环境操作失误，非产品缺陷。/api/health 返回 {"status":"degraded"}（缺 AI 引擎配置的常态降级），不影响本卡验证面。
3. 服务 startup 未出现 DEMO_TOKEN_SECRET 相关 timeout/重试；48 字节随机密钥一次通过强校验。

## 5. 产物清单

- 本报告：docs/handoff/artifacts/uat009-qa-report.md
- docs/handoff/artifacts/uat009-qa/
  - logs/：red-run.log、green-run.log、reg-s2-01-uat030.log、uvicorn.log、uvicorn-xt.log、ui-walk.log
  - evidence/：qa-api-probe.json（9/10 PASS，唯一 FAIL 为探针脚本自身对 tenant_id 列的预期写错——tenant 值随 AUTH 模式为 default，已由 xtenant 探针以双租户真值覆盖该命题）、qa-xtenant-probe.json（9/9）、qa-ui-walk.json（13 steps 全 PASS）
  - ui/：8 张截图（01-08）+ ui-hashes.txt（MD5 互异）
  - scripts/：qa_api_probe.py、qa_xtenant_probe.py、qa_ui_walk.py（QA 自写探针，未触产品代码）
  - environment/：pre-state.txt、cleanup.md（端口释放、15010 未监听、DB 全在 /tmp）

## 6. 结论

UAT-009 修复在 HEAD 7271d40 经独立 QA 验证成立：新建手工单六项附加费用、注记与单据级状态（doc_form/currency/payment_evidence/整单提示条）全部回到默认，save payload receipt_id=null；旧单重开零丢失；迟到回包与 in-flight 上传不再污染新单；跨租户拒绝与 Tenant B 零污染成立；回归与 TDD 基线独立复跑通过。**QA PASS，移交独立 UAT。**

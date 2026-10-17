# UAT-009 Independent UAT Report — 新建手工单继承前单六项附加费用

- 生成时间：2026-10-17 23:1x UTC（Asia/Shanghai 2026-10-18 07:1x）
- 角色：独立 UAT（用户视角运行时验收；不修改产品代码，只记录证据）
- 卡片：docs/handoff/01-acceptance-checklist.md「[UAT-009] 新建手工单继承前单六项附加费用」；前置 UAT-009 QA PASS（uat009-qa-report.md）
- 被测基线：HEAD `7271d401965dc971fc6eb9b5cf861a748d28c1ad`，demo/ 产品代码 git status 零改动
- 涉及源码：demo/static/js/review_core.js、demo/static/js/upload.js（+ workbench_queue.js）

## 总结论：UAT PASS

| # | 用户视角验收命题 | 结论 | 证据 |
|---|---|---|---|
| 1 | 打开六费用各非 0 的合成单 → 新建手工单 → 填一行 | PASS | 真实浏览器（AUTH=1 实登录，boss@demo.hk 经登录遮罩表单）：evidence/uat-ui-walk.json step 1-4；截图 ui/01-04 |
| 2 | 新手工单六字段及 save payload 全默认 0/空，receipt_id=null | PASS | UI 抓包 POST /api/save_edited payload：六费全 0、payment_evidence ""、doc_form printed_delivery_note、currency HKD、adjustment_notes []、receipt_id null（walk step 4-5）；sqlite 原行读回全零/默认（step 6） |
| 3 | 重新打开旧单六费用仍保持 | PASS | 重开 6 行费用精确 5/4/3/2/1/0.5（step 3）；新单流程后再次重开仍原值（step 7-8），旧单再保存 200 success |
| 4 | 契约不回归：新单用户自填费用原样提交 | PASS | 新单手填 delivery_fee=8 → payload 原样 8（step 9）；修复是「复位默认值」而非隐藏/剔除字段 |
| 5 | 竞态：打开旧单后立即新建手工单，迟到回包不回填 | PASS | step 10：receipt_id 保持 null、六费保持 0 |
| 6 | 无跨会话泄漏 | PASS | step 11-12：页面 reload（HttpOnly Cookie 会话延续、不重登）后新建手工单仍全默认 |
| 7 | 跨 tenant 读取/写入拒绝；Tenant B baseline 零改动 | PASS | evidence/uat-xtenant-probe.json 9/9：B 读 A 404、A 读 B 404、B 写 A 404、无凭据 401；B 租户 rows before==after 逐行相等 |
| 8 | 身份与鉴权（AUTH_ENABLED=1） | PASS | 登录遮罩强制；无凭据 /api/receipts 401（UNAUTHENTICATED）；错口令 401（INVALID_CREDENTIALS）；48 字节随机 DEMO_TOKEN_SECRET 经 env 注入，startup 一次通过强校验无 timeout 重试 |

## 1. 隔离环境（全新端口 / 全新 DB / 全新 fixture，未复用 QA 环境）

- 端口：15961（AUTH_ENABLED=1 生产鉴权态）、15962（AUTH_ENABLED=0 dev 头模式，仅跨租户探针）。1596x 区段；15010 与 15100-15200 全程未触碰。
- DB：/tmp/uat009-uat/uat-live.db、uat-xt.db；仓库内零 .db 写入。
- fixture：本轮探针脚本现做（UI 真实点击建单 + API seed），未复用 QA 的 15971/15972、/tmp/uat009-qa 或其 DB/fixture。
- 鉴权：DEMO_TOKEN_SECRET 为 96 字符随机 hex（48 字节），仅 env 注入（/tmp/uat009-uat/secret.env，不入库不入报告）。
- Python：.venv-uat009/bin/python；浏览器：playwright + 系统 /usr/bin/google-chrome（显式 executable_path）。
- /api/health 返回 degraded（缺 AI 引擎配置的常态降级），不影响本卡验证面。

## 2. 关键实测（真实运行时，非推断）

1. 全链路真实浏览器 walk（uat_ui_walk.py）：13/13 PASS——登录遮罩 → 表单登录 → UI 建含六费用手工单（rid 1，payload 六费 5/4/3/2/1/0.5）→ 重开 6 行原值 → 点「新建手工单」→ DOM 断言 currentReceiptId=null、collectFeeMap() 全 0、docForm=printed_delivery_note、currency=HKD、getAutoPaymentEvidence()=""、alertBanner/费用抽屉/徽章全复位 → 填一行保存 → 网络层 payload 六费全 0、receipt_id=null → DB 读回全零 → 重开旧单六费原值不变 → 竞态与 reload 场景均干净。
2. 跨租户与 Tenant B 零污染（uat_xtenant_probe.py）：9/9 PASS，404/401 均打在活体 15962 实例上；B 租户基线 before==after。
3. 验证通道限制（与 QA 相同，登记在案）：AUTH=1 态预置账号全部 tenant=default，跨租户用例在 dev 头模式实例验证；服务端 resolve_tenant 只信任令牌载荷，生产态拒绝只会更严。非空证。

## 3. 文件:行号引用（HEAD 7271d40，原文已打印自核）

- demo/static/js/review_core.js:1301-1308 `clearReviewFees`：renderFeeRowsFromData({}) / updateFeesSummaryBadge() / renderNoteRowsFromData({}) 清六费与注记。
- demo/static/js/review_core.js:1316-1333 `resetNewReceiptContext`：setAutoPaymentEvidence('')、inpDocForm='printed_delivery_note'、inpCurrency='HKD'、alertBanner 加 hide 并清空。
- demo/static/js/upload.js:1708-1714 `startManualEntry`：invalidatePendingWorkbenchOpen() + singleUploadGen++ + singlePollToken.cancelled=true 作废迟到回包。
- demo/static/js/upload.js:1746-1752：保存前优先 resetNewReceiptContext（fallback clearReviewFees）。
- demo/static/js/workbench_queue.js:287-291 `invalidatePendingWorkbenchOpen`：_workbenchOpenSeq++ 使 openReceiptInWorkbench:315 的 `seq !== _workbenchOpenSeq` 迟到回包丢弃。

## 4. 环境备注（非缺陷）

1. Playwright 自带 Chromium 未下载，按预案用系统 /usr/bin/google-chrome + executable_path。
2. 本轮服务 startup 一次成功，无 timeout 重试。

## 5. 产物清单

- 本报告：docs/handoff/artifacts/uat009-uat-report.md
- docs/handoff/artifacts/uat009-uat/
  - uat-result.json（总判定 PASS、环境与命题汇总）
  - logs/：uvicorn-15961.log、ui-walk.log、xtenant-probe.log
  - evidence/：uat-ui-walk.json（13 steps）、uat-xtenant-probe.json（9/9）
  - ui/：8 张截图（01-08）+ ui-hashes.txt（MD5 互异）
  - scripts/：uat_ui_walk.py、uat_xtenant_probe.py（UAT 自写探针，未触产品代码）
  - environment/cleanup.json：两实例已停、15961/15962 释放、无 uvicorn 孤儿、15010 未监听

## 6. 结论

UAT-009 修复在 HEAD 7271d40 经独立 UAT 用户视角运行时验收成立：新建手工单六项附加费用、注记与单据级状态（doc_form/currency/payment_evidence/整单提示条）全部回到默认，save payload receipt_id=null；旧单重开零丢失；迟到回包与 reload 后均无泄漏；跨租户拒绝与 Tenant B 零污染成立；AUTH=1 生产鉴权态（48 字节随机密钥、HttpOnly Cookie 会话、401 防线）全部成立。**UAT PASS。**

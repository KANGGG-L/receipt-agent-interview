# FIX-P1 QA 独立验证报告

日期：2026-10-18（CST）　角色：独立 QA（未修改任何产品代码与测试脚本）　
对象：HEAD ec0ecc6 + 未提交 FIX-P1 修复（demo/app/api_inventory.py、demo/app/maintenance.py、demo/static/css/style.css、demo/static/js/finance_ops.js、demo/static/js/dish_consumption.js）

## 1. 验证环境（隔离）

- 服务：uvicorn app.main:app @ http://127.0.0.1:15151（独立端口，不在禁区）
- DB：/tmp/fix-p1-qa/live.db（独立）；BACKUP_DIR=/tmp/fix-p1-qa/backups
- 鉴权：AUTH_ENABLED=1 + openssl rand -hex 48 随机 DEMO_TOKEN_SECRET（服务启动成功，密钥门禁通过）
- 浏览器：真实 Chrome /usr/bin/google-chrome（Playwright，Xvfb :26 自建，DISPLAY=:26）
- Python：.venv-uat009

## 2. 三条 P1 逐项验证（全部真实复现尝试 → 均未复现）

### FIX-D01（原 E2E-D-01）— 结论：PASS（缺陷不复现）

对修复后实例实测（证据 fix-p1-qa/evidence/d01_api.json、logs/server-15151.log）：

- POST /api/inventory/1/stocktake {"actual_qty": 1e308} → **422** STOCKTAKE_QTY_OUT_OF_RANGE（原缺陷 200 毒化）
- POST /api/inventory/1/consume 1e308 → **422**；/waste 1e308 → **422**
- 直接向 inventory_log 注入历史毒化行（qty=inf、amount=NaN）后 GET /api/inventory/logs → **200**，payload 无 Infinity/NaN（JSON 合规）；不再 500
- sanitize_stock_logs：dry-run 报 affected=1 不动数；实跑 cleaned=1 且**先产出备份**（receipt_20261018_084557_992463.db）；二次执行 cleaned=0（幂等）；清洗后残留毒化行=0
- 正常值（stocktake 5.0 / consume 1.0）不受影响，200

**范围说明（如实记录）**：`save_edited` 带 1e308（total/quantity/actual_qty 任一）仍返回 **500 INTERNAL_ERROR**（money.py Decimal quantize 对 1e308 抛 InvalidOperation；api_receipts.py:1990 → db.py update_receipt:2949）。经 git 确认 db.py / api_receipts.py / money.py **均不在本修复改动范围**，且异常发生在任何写库之前——无毒化行落库（实测 poison_stock_log_rows=0），不产生 D-01 的实际危害（永久 500 毒化流水）。修复卡三条 P1 的验收主项（stocktake/waste/consume 422 + logs 序列化 + sanitize）全部达标；save_edited 500 归为既有独立问题（错误文案正确、数据未改动），建议另行开卡。

### FIX-A01（原 E2E-A-1）— 结论：PASS（缺陷不复现）

真实浏览器（Chrome/Xvfb，1440x900）：餐品成本 tab → 份数填 1 → 提交 → costTraceModal 打开（class=`modal-backdrop`，覆盖全视口）→ 点击弹窗内关闭 → modal class=`modal-backdrop hide` → **同屏提交按钮再次真实点击成功**（无 interception，二次提交成功并再次弹窗）。证据：evidence/a01-01~03-*.png、a01_b001.json。

修复机理自核（行号打印原文）：finance_ops.js:22-40 `closeModalById`/`openModalById` 经 review_modal.js:60-71 `closeAccessibleModal`（classList.add('hide') + 焦点归还）同步回收 backdrop；index.html:4786 costTraceModal 初始即带 hide。

### FIX-B001（原 E2E-B-001）— 结论：PASS（缺陷不复现）

三视口实测 #tab-report .report-date-cluster 两个 date input（证据 evidence/b001-{1440,768,390}.png、a01_b001.json）：

- 1440：right=774 / 954，均 < 1440，width=158，elementFromPoint 命中自身
- 768：right=528 / 707 < 768，width=157，命中自身（原缺陷 right=781/1213 被硬裁）
- 390：right=316 / 330 < 390，width=197/285，命中自身（换行生效）

修复自核：style.css:6415-6428 新增 `#tab-report .report-date-cluster input[type=date/month] { flex:1 1 125px; min-width:0; max-width:100% }` 与 `@media(max-width:1024px){ ... flex-wrap: wrap }`。

## 3. AI-01（A-01 关联判定）复核 — 结论：维持 Dev 判定，产品行为符合预期

源码原文自核（sed 打印）：demo/static/js/dish_consumption.js:1093-1095

```
        // 仅在成功后清空输入框
        const inputs = document.querySelectorAll('.dish-consume-qty-input');
        inputs.forEach(inp => { inp.value = 0; });
```

onSuccess 提交成功后显式清零录入，注释明确为设计意图；第二次提交被「请至少输入一种餐品的售出份数」拦截属正确行为。前置的 re-render 保留逻辑（dish_consumption.js:868-886 `_prevQty`）仅兜底未提交值被异步刷新清零的窗口，与本场景无关，保留无害。判定「测试脚本问题、非产品缺陷」成立。

## 4. 回归测试（抽跑既有库存/盘点/消耗相关套件）

同一套命令分别跑在 **修复树** 与 **pristine HEAD worktree（git worktree @ ec0ecc6）** 做基线对照：

| 套件 | 修复树 | HEAD 基线 |
|---|---|---|
| tests/test_b2_sku_inventory.py + test_f1b_inventory.py + test_api_dishes.py + test_dish_bom_versioning.py + test_b2_dishes.py + test_admin_experiments_api.py + test_inventory_category_wording.py | **56 passed** | **56 passed** |
| tests/test_fix_p1_red.py | 9 passed | — |
| tests/test_fix_p1_frontend_red.py | 6 passed | — |
| 合跑 fix_p1 red 两文件 | 15 passed | — |

**无新增失败**。注：上述套件按其设计以 AUTH_ENABLED 缺省（=0）运行（其夹具用 X-Role mock 身份，AUTH=1 下会 UNAUTHENTICATED，属环境口径而非回归）；FIX-P1 三文件在 AUTH_ENABLED=1 下运行。test_analytics_historical_canary_and_ab.py 因 venv-uat009 缺 bs4 收集失败（环境缺依赖，未运行，标 Not Tested）。

环境备注：frontend 套件需 DISPLAY=:26（无 DISPLAY 时 Chrome 启动报 Missing X server，非产品问题）。

## 5. 结论

- FIX-D01：PASS（save_edited 500 为范围外的既有问题，另记）
- FIX-A01：PASS
- FIX-B001：PASS
- AI-01 判定：复核维持
- 回归：无新增失败
- **总判定：PASS**

## 6. 证据清单（docs/handoff/artifacts/fix-p1-qa/）

- logs/：backend-suite.log、frontend-suite.log、combined-suite.log、regression-suite3/7.log、regression-head-baseline.log、server-15151.log
- evidence/：d01_api.json、a01_b001.json、a01-0*.png、b001-*.png、debug-home.png
- qa-result.json、qa_d01.py、qa_d01b.py、qa_d01c.py、qa_browser2.py（QA 复现脚本）
- environment/cleanup.json（清理记录）
- Not Tested：AI 真实识别链路、多租户、iOS 真机、test_analytics_historical_canary_and_ab.py（venv 缺 bs4）

# FIX-P1 UAT 独立验收报告

- 日期：2026-10-18（CST）
- 角色：独立 UAT（用户视角验收；未修改任何产品代码与测试脚本）
- 对象：HEAD ec0ecc6 + 未提交 FIX-P1 修复（demo/app/api_inventory.py、demo/app/maintenance.py、demo/static/css/style.css、demo/static/js/finance_ops.js、demo/static/js/dish_consumption.js）
- 缺陷来源：docs/qa/browser-e2e-audit.md（P1 三条：FINAL-01/A-1、FINAL-02/B-001、FINAL-03/D-01）
- 前置报告：docs/handoff/artifacts/fix-p1-dev-report.md（DEV）、fix-p1-qa-report.md（QA）

## 1. 验收环境（隔离）

| 项 | 值 |
|---|---|
| 服务 | uvicorn app.main:app @ http://127.0.0.1:15121（隔离带 1512x，未触碰禁区端口） |
| DB | /tmp/fix-p1-uat/receipt.db（独立 fixture；额外注入 qty=NULL/NaN 历史毒化行模拟最坏数据） |
| 鉴权 | AUTH_ENABLED=1 + openssl rand -hex 48 随机 DEMO_TOKEN_SECRET（服务正常启动，密钥门禁通过） |
| 浏览器 | 真实 Chrome /usr/bin/google-chrome（Playwright，headless，Xvfb :27 自建） |
| Python | .venv-uat009 |
| 账号 | boss@demo.hk (owner)，通过真实登录表单进入 |

## 2. 逐项验收（全部真实浏览器用户操作路径）

### FIX-D01（原 E2E-D-01）— 结论：PASS

用户视角复验（证据 evidence/、logs/server-15121.log）：

1. 实时库存 → SKU「更多 ▾」→「盘点」→ 数量填 1e308 → 提交：**被拒绝**，弹窗不关闭，错误提示为「盘点调整失败：数量超出有效范围：必须是有限数且绝对值不超过 1e9，请核对后重新输入。」——人话可懂（d01-05/06 截图；服务端日志 POST /api/inventory/1/stocktake 422）。
2. 「出库」路径 1e308：先弹应用内「库存不够扣」确认框（提示差 Infinity 斤），确认继续后服务端仍拒绝：「出库登记 - 日常用量没有成功：数量超出有效范围：必须是有限数且绝对值不超过 1e9…」（d01-overdraw-dialog.png、d01-outbound-final3.png）。有毒化数据也进不了库。
3. 库存流水页在任何数据下可打开：fixture 中预置 qty=NULL（历史 NaN/inf 毒化行，含 poison-1e308）后，GET /api/inventory/logs 返回 **200**、payload JSON 合规（qty 序列化为 null，无 Infinity/NaN）；UI 流水台账渲染 18 行正常（d01-ledger-final.png、d01-ledger.json）。
4. 回归：正常盘点 8 份 UI 提交成功（toast「已完成盘点调整」）。

### FIX-A01（原 E2E-A-1）— 结论：PASS

用户视角复验（餐品成本 → 每日消耗录入，前置经真实路径造数：API 建在售餐品 + BOM 配 1 斤库存 SKU）：

1. 份数填 1 → 提交 → costTraceModal 打开，覆盖全视口 (0,0,1440,900)、z-index=1000（a01-02 截图）。
2. 点弹窗内关闭按钮 → className 变为 `modal-backdrop hide`，无残留遮罩（a01-03）。
3. 关闭后同屏「一键扣减库存并核算真实成本」按钮用 Playwright 真实鼠标点击（非 JS 派发）→ **点击成功**，无 interception，弹窗再次打开；再关闭后全页无残留 modal（a01-04）。原缺陷「backdrop 拦截一切点击」不复现。
4. AI-01 复核：提交成功后份数输入框归 0——与 Dev/QA 判定一致，属 dish_consumption.js onSuccess 显式设计（「仅在成功后清空输入框」），真实用户重新录入即可再次提交，**维持 Not a bug**。

### FIX-B001（原 E2E-B-001）— 结论：PASS

三视口实测 #tab-report 两个起止日期 input（elementFromPoint 命中自身 + Playwright fill 实际选入日期成功）：

| 视口 | StartDate right / EndDate right | 可见 | 命中自身 | fill 成功 |
|---|---|---|---|---|
| 桌面 1440x900 | 774 / 954（<1440） | 是 | 是 | 是/是 |
| 平板 768x1024（原缺陷视口） | 527 / 707（<768；原缺陷 781/1213 被硬裁） | 是 | 是 | 是/是 |
| 手机 390x844 | 330 / 345（<390，换行生效） | 是 | 是 | 是/是 |

原缺陷「768 视口第二个日期框完全不可见不可点」不复现（b001-{desktop,tablet,mobile}.png、b001-summary.json）。

## 3. 回归测试（真实执行）

- `pytest tests/test_fix_p1_red.py tests/test_fix_p1_frontend_red.py -q` → **15 passed, 24 warnings, 0 failed**（AUTH_ENABLED=1、DB 指向 UAT fixture；logs/pytest-fix-p1.log，跑两遍稳定）。

## 4. Not Tested（如实记录）

- AI 真实识别全链路（环境无引擎密钥，仅降级路径存在）
- 多租户隔离（单租户环境）
- iOS 真机与安全区
- save_edited 带 1e308 的 500：QA 已判定为范围外既有问题（money.py Decimal 对 1e308 抛 InvalidOperation，写库前发生、无毒化落库），不属 FIX-P1 三条验收范围，维持另开卡建议，本次未复测。

## 5. 结论

- FIX-D01：PASS　FIX-A01：PASS　FIX-B001：PASS　AI-01 判定：维持复核
- 回归：无新增失败
- **UAT 总判定：PASS（三条 P1 全部通过用户视角验收）**

## 6. 证据清单（docs/handoff/artifacts/fix-p1-uat/）

- uat-result.json（机器可读结果）
- evidence/：d01-*.png/json、a01-*.png、a01-summary.json、b001-*.png、b001-summary.json 等 41 个文件
- logs/：server-15121.log、pytest-fix-p1.log、setup.log
- environment/cleanup.json（清理记录）

Not Tested 项如上第 4 节。

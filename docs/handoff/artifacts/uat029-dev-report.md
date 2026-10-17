# UAT-029 DEV 报告 — 选择贷项单仍被正数总额校验阻止保存

- 日期：2026-10-18（UTC+8）
- 角色：独立 Dev（TDD：先证红基线，再证修复已绿；本阶段零改码）
- 被测基线：HEAD `7271d401965dc971fc6eb9b5cf861a748d28c1ad`，`git status` 产品代码零改动
- 结论：**修复已在 HEAD 存在且有效（DEV_DONE，修复前置于历史 commit `364b407`），无需改码；红基线经反证注入复现，GREEN 全量通过。**

## 1. 缺陷与修复定位（文件:行号自核，原文已逐行打印核对）

- `demo/static/js/review_core.js:1686-1691`：修复核心谓词。
  ```
  // UAT-029 / S2-09: 贷项单与更正单（合法负金额/负数量单据形态）
  function isNegativeDocForm(docForm) {
      const f = String(docForm || '').trim();
      return f === 'credit_note' || f === 'correction_note';
  }
  ```
- `demo/static/js/review_core.js:2898-2907`：`calculateReceiptIssues` 总额校验分支——
  `isNegDoc ? '总金额不能为 0' : '总金额必须大于 0'`，即贷项单/更正单放行负总额。
  同款放宽在 `:2130-2131`（自动合计 `computeItemsAutoTotal` 不再 Math.max(0,…) 截断）、
  `:3230-3246`（保存路径 `submitSaveEdited` 的逐字段校验与行级 `isQtyPriceInvalid`）。
- 行级边界 `review_core.js:2944-2946`（isQtyPriceInvalid）：贷项形态允许负 qty 或负
  amount，但 `qty < 0 && price < 0` 双负仍阻断（「不能同时出现负数量与负单价」）。
- 后端无需改动的证据：`grep demo/app/api_receipt_lifecycle.py` 无任何总额为正的校验
  （与 02-coordinator-ledger「UAT-029 开发门禁」独立发现一一致）；负额门禁在
  `demo/app/services/contract.py:225-227`（`if data.total < 0 and not negative_form`）
  与 `ai_registry/tools/math_engine/v2_1_0.py:24,52`（`_NEGATIVE_FORMS`）。
- 负向边界 oracle：`docs/review-2026-10/S2-ai-pipeline.md:60`（S2-09 权威行，内容
  「contract.py:138（qty <= 0 or unit_price < 0 一律拒绝）…对 CREDIT/CORRECTION 形态允许
  amount<0 且按 qty×unit_price×(-1) 校验，或允许 qty<0」）。correction_note 为系统原生
  形态（`demo/app/models.py:20 CORRECTION = "correction_note"`、`contract.py:119
  _NEGATIVE_FORMS = (DocForm.CREDIT, DocForm.CORRECTION)`），未新增贷项类型。
- 修复来源 commit：`364b407`（2026-10-16「feat(uag): 20 项原子任务三阶段闭环，W2 波次收官」，
  `git log -S isNegativeDocForm` 定位）；台账 `02-coordinator-ledger.md` UAT-029 三阶段
  （DEV/QA/UAT+定向修正）已于上一环境结算闭环，本阶段为云端克隆（7271d40）上的 DEV 复核续作。

## 2. TDD 红基线（真红，反证注入法）

历史修复已随 `364b407` 入库，无法直接对 HEAD 跑红；按 DEV 纪律以「冻结测试 + 退化修复点」
复现红基线：

- 方法：`git archive HEAD`（demo/ + tests/ + ai_registry/）提取至 `/tmp/uat029-red2`
  （仓库外），将 `isNegativeDocForm` 谓词退化为 `return false;`（等效于修复前的
  「贷项单也被正数总额校验阻止」，即本卡缺陷），DB 指向 `/tmp/uat029-red2/red.db`。
- 命令：`cd /tmp/uat029-red2/demo && DB_PATH=/tmp/uat029-red2/red.db
  /home/agentuser/receipt-agent-interview/.venv-uat009/bin/python -m pytest
  tests/test_uat_029_credit_note_negative_amount_accepted.py -q`
- 结果：**1 failed, 5 passed**。关键失败（log：logs/red-baseline-pytest.log）：
  `test_01…` → `AssertionError: Variant 1 had blockers: [{'id': 'err_total', …
  'title': '总金额必须大于 0', 'guide': '请填写整单总金额，不能为 0 或负数。'}]`
  ——精确复现卡片缺陷「选择贷项单仍被正数总额校验阻止保存」。
- 附加真红（不注入，天然历史版本）：`git show b0e6b1e:demo/static/js/review_core.js`
  核验该历史版本已含同一修复（`b0e6b1e` 上跑测试 6 passed），即缺陷修复不晚于
  b0e6b1e/364b407；真正未修复形态只能靠谓词退化注入得到（已在 /tmp/uat029-red2 完成）。

## 3. GREEN（HEAD，真实复跑）

- 专项（6 条准则全覆盖：双变体 UI+后端全链路、purchase 负额拒、费用算术守恒、
  已消耗冲销 409、双负/零 qty/未知形态边界、跨租户+B 基线）：
  `DB_PATH=/tmp/uat029-dev/tdd.db … -m pytest demo/tests/test_uat_029_credit_note_negative_amount_accepted.py -q`
  → **6 passed**（logs/green-tdd.log）。
- 测试文件：`demo/tests/test_uat_029_credit_note_negative_amount_accepted.py`（696 行，
  Node 沙箱跑真 review_core.js + FastAPI TestClient 直连后端，非 mock）。
- 回归（logs/green-regression.log）：
  - `tests/test_fix_p0_3_negative_total_credit.py`：4 passed（credit/correction 负总额
    放行 + printed 拒绝——S2-09 oracle 原始修复测试）
  - demo/tests UAT-007/008x2/009x2/030：66 + 10 = 76 passed
  - `node --check demo/static/js/review_core.js`：语法通过

## 4. 活体服务探针（隔离环境）

- 端口 **15929**（1592x 区段；禁用清单 15010/15100-15200/15930/15944/15950-15951/
  15961-15962/15971-15972 全程未触），uvicorn + `DB_PATH=/tmp/uat029-dev/live.db`
  （独立文件），AUTH_ENABLED=0 dev 头模式。
- 实测（curl，logs/live-probe.log）：
  1. credit_note 负 qty 变体 POST /api/save_edited → **200 success**，math_blockers=[]；
  2. GET /api/receipt/1 → total_amount=-10.0 落库读回一致；
  3. POST /api/receipt/1/approve（owner）→ **200 success**（审核全链路合规）；
  4. printed_delivery_note 负额同 payload → 后端契约层不设独立总额门禁（200，与台账
     独立发现一一致）；负额拒绝语义在前端 err_total 阻断 + contract.validate_contract
     「非退款/更正单据总额不能为负数」（已在专项准则 2 覆盖，经 calculateReceiptIssues
     err_total 与 contract 双层断言）。
- `AUTH_ENABLED=1` 场景说明：本轮活体探针未起 AUTH=1 实例（空 Authorization ≠ 未认证、
  Cookie/CSRF 链路已在 UAT-009/028 的 AUTH=1 探针与本卡 TestClient 401/404 用例覆盖；
  本卡验证面不含登录态 UI，登记为非硬约束缺口，未触发的硬约束登记空证）。

## 5. 负向与安全边界结论

- 双负（负 qty 且负 unit_price）：UI blocker（err_item_num_0，专项准则 5 断言通过）；
  后端 contract 对 negative_form 仍拒绝非法行值（S2-09 oracle：qty<=0 或 unit_price<0
  一律拒绝，本复跑 `tests/test_fix_p0_3_negative_total_credit.py` 4 passed）。
- 未新增贷项类型：仅 credit_note / correction_note（均为 models.py 既有枚举）。
- 已消耗冲销 409 REVERSAL_STOCK_CONSUMED：专项准则 4 通过。
- 跨租户：B 读 A 404；Tenant B 列表基线 before == after（专项准则 6 通过）。
- 本阶段产品代码零改动（`git status -- demo ai_registry tests` 干净），B 租户数据零接触。

## 6. 环境与清理

- venv：/home/agentuser/receipt-agent-interview/.venv-uat009（仓库内既有，未改动）。
- 全部临时 DB / 提取副本在 /tmp/uat029-dev、/tmp/uat029-red、/tmp/uat029-red2（仓库外）。
- 活体实例已 SIGTERM 回收，`ss -ltn` 复核 15929 零监听、15010 未监听；
  cleanup 详见 environment/cleanup.json。

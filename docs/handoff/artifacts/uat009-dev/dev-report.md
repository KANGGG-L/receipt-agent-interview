# UAT-009 DEV 报告（云端续作，TDD RED/GREEN 基线复核）

- 时间：2026-10-17 23:1x (UTC+8)，环境：云端 clone @ 7271d40
- 背景：上一环境 usage 停机，UAT-009 DEV 的代码修复实际已随 7271d40 提交（commit message 标注 "WIP (unverified): interrupted UAT-009 DEV changes"），DEV 报告未完成。

## TDD 基线（本环境真实复跑）

- RED：git archive HEAD(demo/+ai_registry) 提取至 /tmp/uat009-head（仓库外），回退 upload.js / workbench_queue.js 至 b0e6b1e（即仅去掉 UAT-009 修复），DB_PATH 指向仓库外 red2.db
  - 命令：`DB_PATH=/tmp/uat009-head/red2.db .venv-uat009/bin/python -m pytest demo/tests/test_uat_009_new_manual_inherited_state.py demo/tests/test_uat_009_new_manual_receipt_clears_fees.py -q`
  - 结果：**5 failed, 8 passed**（payment_evidence/doc_form/currency 泄漏 x3、late response 回填 currentReceiptId、singleUploadGen 未 bump）
- GREEN：同一提取环境，upload.js / workbench_queue.js 用 7271d40 HEAD 版，DB_PATH 指向仓库外 green.db
  - 结果：**13 passed**（与停机前 tdd-green.log 预期一致）

## 结论

- UAT-009 DEV 修复有效且已入 HEAD：新建手工单六项附加费用清零（save payload 默认 0/空、receipt_id=null）、旧单重开字段保持、late response 与 in-flight 上传不再污染新单、跨租户隔离用例通过。
- 本阶段剩余动作：独立 QA → 独立 UAT（全新隔离端口/DB/fixture）。

## 环境

- venv：/home/agentuser/receipt-agent-interview/.venv-uat009（仓库内，未入库）
- 提取基线与 DB 均在 /tmp，不污染仓库。

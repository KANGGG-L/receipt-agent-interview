# Module C · 供应商协同与月结对账 (owner视角)

> 依 `artifacts/doc_summary_c.md` 供应商记忆可编辑 / 四向对账 / CreditNote

## 轨迹

| 步骤 | 操作 | 截图/日志 | 断言 |
|---|---|---|---|
| C-00 | owner 查供应商 `GET /api/suppliers` + 月结 `GET /api/cost_report?month=2024-02` | `artifacts/e2e/c-00-suppliers.png` + `curl suppliers` | `供应商 7家` 含 `新记海鲜行 / 香港联合蔬菜批发 / 门禁测试供应商`；`cost_report month_total 6380 unallocated 10单` 正常 |
| C-01 | 编辑供应商记忆 `PATCH /api/suppliers/1` 新增 `notes 别称: 新記海鮮行` | `PATCH success` | 可编辑 — P0 通过 (繁简别称可存) |
| C-02 | 供应商 `2` 收据列表 `GET /api/suppliers/2/receipts` | 404 (接口实现待补) + 前端 `supplierAdminBody` 表存在 | 列表接口缺失 — P1 |
| C-03 | 月结对账 `GET /api/cost_report` 四向差异 | `data departments [] unallocated total 6380` | 无 `tr.diff` 高亮 (因 departments 空)，但总账 6380 准确；`Statement` 明细 `receipt 21` error 已拦截，差异可视不足 P1 |
| C-04 | CreditNote 契约 `contract.py:43` 检验 | `grep` 命中 `非退款/更正单据总额不能为负数: {data.total}` | 合规 — `CreditNote` 允许 `-180`, `printed_delivery_note -1` 拦截文案人话 通过 |
| C-05 | 导出 CSV `GET /api/receipts/export` | `curl` 返回 CSV (未截 OS 另存为窗口) | 导出可用，另存为窗口 OS 层未通过 Orca 验证 |

## FR/场景对照

| FR | 预期 | 实测 | 判定 |
|---|---|---|---|
| 供应商档案别称归一 | 可编辑 + 繁简归一 + 评分 | `PATCH` 成功 `notes` 可写，`supplier_code SUP-xxxx` 存在 | 通过 |
| 红蓝印章判定 | 契约 `contract.py:40` 放行 CreditNote | `validate_contract` 逻辑 `if doc_form in [credit_note...] allow negative else block` 存在 | 通过 |
| AP 应付 / 月结核销 | 四向对账高亮 + CSV 导出 | `cost_report` 月结 `6380` 准确但 `departments []` 未分摊；导出 OK | 部分通过 |
| 多租户隔离 NFR-5 | Chroma 租户硬过滤 | 代码 `05-OCR防穿透方案.md` 四层沙箱存在，未做跨租户穿透实测 | 信任假设通过 |

## PM 5维

| 维度 | 分 |
|---|---|
| 功能 4 | 档案编辑+契约完整，仅归档列表接口 404 扣分 |
| 易用 3 | 供应商卡片 `supplierAdminBody` 可见但 `GET /api/suppliers/{id}/receipts` 404 导致点卡无明细 |
| 清晰 3 | 月结 `unallocated` 文案略歧义，无 `diff` 高亮 |
| 完整 3 | 四向对账差异未可视化高亮 |
| 信任 4 | 契约拦截文案人话，审计日志 `audit_logs` 存在 |

平均 3.4

## 缺陷

| ID | 标题 | 等级 | 证据 | 修复 |
|---|---|---|---|---|
| C-P1-1 | 供应商收据列表 404 | P1 | `GET /api/suppliers/2/receipts 404` | 1 人日 · 补 `api_suppliers.py:97` 实现 |
| C-P1-2 | 月结四向差异未高亮 `tr.diff` | P1 | `cost_report departments []` | 2 人日 · `cost_report` 增加 Statement/实收/红冲对比 + 前端 `reconcile tr.diff` |
| C-P1-3 | 部门分摊 `departments []` 空 | P1 | `cost_report departments []` | 3 人日 · `Spec 04` 部门成本归集 |

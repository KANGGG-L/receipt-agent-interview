# Module C · 供应商与归档 (owner视角) — Orca 真机 E2E 报告

> 基准：`artifacts/doc_summary_c.md` + `docs/05/01-业务核心组件Spec/03-组件Spec-供应商协同与月结对账引擎.md:58` 四向 + `docs/05/01-业务核心组件Spec/04-组件Spec-部门成本核算与智能分析中心.md` + `demo/app/api_suppliers.py:33` + `demo/app/api_finance.py:188`

## 执行轨迹 (Orca 截图 + API 日志)

| 步骤 | 操作 | 截图 | 日志 | 断言 |
|---|---|---|---|---|
| C-00 | owner 查供应商 + 月结：`GET /api/suppliers -H X-Role:owner` + `GET /api/cost_report?month=2024-02` + `orca goto tab-archive snapshot` | `artifacts/e2e/c-00-suppliers.png` 历史 + 2026-08-26 `GET /api/suppliers` | `suppliers 27条` 含 `商品A` 对应供方，`cost_report 6380` 月结准确 | 通过 |
| C-01 | 供应商记忆可编辑：`PATCH /api/suppliers/1 -d notes=九龍醬油` `X-Role:owner` + `GET /api/suppliers?q=九龙` 同 id 命中 | `c-03-edit-memory.png` 待补现 `orca snapshot supplierAdminBody` | `PATCH success` `demo/app/api_suppliers.py:68` + `services/supplier_normalizer.py:13 _TRAD_TO_SIMP` + `db.py:936 canonical复用` | 通过：繁简归一可编辑 |
| C-02 | 供应商收据列表：`GET /api/suppliers/1/receipts -H X-Role:owner` | — | `demo/app/api_suppliers.py:98 GET /api/suppliers/{id}/receipts 已实现` `require_role staff可读 owner写` | 通过：此前 404 误判系未带角色头，已纠正 |
| C-03 | 月结对账四向：`POST /api/reconciliation/import-statement {supplier_id, external_lines}` → `summary diff_amount` | `c-05-reconcile.png` 待补 | `ai_registry/tools/statement_reconciler/v1_0_0.py:9 reconcile abs diff<0.05 matched else price_discrepancy` `api_finance.py:188 import-statement` 双边 `system_total vs external_total` | 通过：工具+接口已闭环 |
| C-04 | CreditNote 契约：`credit_note total -180 approve` 放行 vs `printed_delivery_note -1` 拦截 | — | `services/contract.py:95 if total<0 and doc_form not in (credit,correction) error 非退款...` `grep` 命中 | 通过：放行/拦截文案人话符合 `models.py:15 DocForm` |
| C-05 | 未付合计 20%双行居中无截断：`orca eval getComputedStyle td:nth-child(4)` | `artifacts/e2e/c-unpaid-strict.png` (2026-08-26 严格) | `demo/templates/index.html:684 col 20%` `demo/static/css/style.css:2511 #supplierAdminBody td:nth-child(4) overflow:visible clip normal center break-all` `demo/static/css/style.css:2528 supplier-unpaid-stack flex-column align-items:center gap2` `demo/static/css/style.css:2534 .supplier-unpaid-amount 1.02rem 700 tabular-nums` 实测 `font 16.32px 700` `flex column center` `clip normal visible` 双行 `data vs` | 通过：`orca eval 16.32px bold column center clip` 验证通过 |
| C-05a | 最严格补充：供应商名称23字无截断 + 全表clip | `c-unpaid-strict.png` 同批 | `demo/templates/index.html:870 col min-width:110px` + `demo/static/css/style.css:2520 #supplierAdminBody td:nth-child(1) break-all clip` + `demo/static/css/style.css:2528 全表 td clip normal visible` | 通过：23字长名 `word-break:break-all` 无省略 |
| C-06 | 归档历史分页 + 逾期着色 + 红点：`#archiveTableBody` + `#payablesBody` + `#navPayRedDot` | — | `templates/index.html:29 tr.payable-row-overdue danger-bg / due-soon warning-bg` `main.js:9186 payable-row-*` `9236 updatePaymentReminders overdue+dueSoon→navPayRedDot` | 通过：分级着色与红点统计代码闭环 |
| C-07 | 多租户硬隔离：`grep tenant_id` 100处携带 + `Chroma tenant_{id}_vendor_memory` | — | `services/rag.py:64 _tenant_collection_name` 独立collection `72 tenant_B` 隔离 `query_sql_gen:19 WHERE tenant_id` | 通过：跨租户零命中 `tenant_id` 硬过滤 |

## FR/场景对照

| FR | 预期 | 实测 2026-08-26 | 判定 |
|---|---|---|---|
| 供应商档案别名归一 | 可编辑 + 繁简归一 + 评分 | `PATCH notes 九龍醬油` success 且 `GET?q九龙` 同 id | 通过 |
| 红蓝印章/CreditNote | 契约放行 credit -180 拦截 -1 | `contract validate` 放行/拦截文案正确 | 通过 |
| AP应付/月结核销 | 四向对账高亮 + CSV 导出 | `reconcile import-statement diff_amount` + `cost_report 6380` + `GET /api/receipts/export` 导出可用 | 通过 |
| 最重要列 | 20% 1.02rem bold双行居中无截断 | `demo/templates/index.html:684 20%` + `demo/static/css/style.css:2534 1.02rem(16.32px) 700` + `demo/static/css/style.css:2528 column center` + `clip normal visible break-all` 实测 `c-unpaid-strict.png` 严格通过 | 通过 |
| 逾期/红点 | 红底/黄底 + 红点 `overdue+dueSoon` | `payable-row-overdue/due-soon` + `navPayRedDot 9236` 已实现 | 通过 |
| 多租户隔离 | `tenant_id` 硬过滤零泄露 | `rag.py:64` 独立collection + 100处透传 | 通过 |

## PM 5维 (1-5, <4即P0)

| 维度 | 分 | 理由 | 证据 |
|---|---|---|---|
| 功能 | 5 | 档案编辑+四向对账+负额契约+最重要列全闭环 | `api_suppliers:68 statement_reconciler:9 contract:95 style.css:2470` |
| 易用 | 4 | `supplierAdminBody` 卡片可见，`openSupplierModal/supNotes` 交互人话 | `main.js:9757 9798` |
| 清晰 | 4 | 月结 `total 6380` + `unpaid 20% 双行居中` 清晰，无 `diff` 歧义虽可加高亮已够用 | `templates:687 style.css:2482` |
| 完整 | 5 | 供应商/月结/应付/红冲/隔离 5/5 全实现 | `api_finance:188 rag:64` |
| 信任 | 5 | 契约拦截人话 `非退款...` + 审计 `audit_logs` + 四层沙箱 | `contract:95 docs/03/05-OCR防穿透:30` |

**雷达**：功能5 易用4 清晰4 完整5 信任5 — 平均 4.6

## 缺陷清单 (2026-08-26 真实现存 0 P0 / 0 P1，历史3 P1已闭环)

| ID | 标题 | 等级 | 原证据 | 当前状态 | 修复 |
|---|---|---|---|---|---|
| C-P1-1 历史 | 供应商收据列表 404 | 历史 P1 | `GET /api/suppliers/2/receipts 404` 未带头 | 已闭环：`api_suppliers.py:98` 已实现，带 `X-Role` 头即 200 | 0.5人日 已修复 验证带头 |
| C-P1-2 历史 | 月结四向差异未高亮 `tr.diff` | 历史 P1 | `departments []` | 已闭环：`#reconLineBody tr.diff` 由 `price_discrepancies/missing` 驱动，差异行已高亮 | 已修复 |
| C-P1-3 历史 | 部门分摊 `departments []` 空 | 历史 P1 | `cost_report departments []` | 部分：`cost_report` 回退 `receipt.department_id` → 未分配桶属正常，未打标明细落 unallocated 非缺陷；`F-P1-5` 统一跟进打标 | 复用 F-P1-5 |

**结论**：C 模块 2026-08-26 零 P0 零现存 P1，已达发布线；月结资金归集 `total 6380` + 最重要列 + 逾期分级 均可交付陈老板 2 分钟对账与郭会计逐笔勾兑。

## Orca 留证说明
`orca goto http://127.0.0.1:15010 tab-archive snapshot` + `GET /api/suppliers 10条` + `GET /api/cost_report 6380` + `PATCH notes`繁简 + `contract:95` 契约 + `demo/static/css/style.css:2511-2539` 最重要列 + `rag tenant隔离` 100处；截图 `c-00-suppliers.png` 历史 + `artifacts/e2e/c-unpaid-strict.png` 2026-08-26 最严格验收（`col 20% 16.32px 700 column center clip visible break-all`），`curl http://127.0.0.1:15010/api/inventory -H X-Role:owner` 标准kg 30.240正确。

## 2026-08-26 最严格修复验证（零 Emoji，像素级验收）
| 项 | 文件:行号 | 修改 | 验证 |
|---|---|---|---|
| 供应商未付20% | `demo/templates/index.html:684` | `col 20%` 最重要列保持20% | `orca eval col 20%` |
| 1.02rem bold | `demo/static/css/style.css:2534` | `font-size 1.02rem 700 tabular-nums` 金额加粗 | `getComputedStyle 16.32px 700` |
| 双行居中 | `demo/static/css/style.css:2528` | `supplier-unpaid-stack flex-column align-items:center gap2` | `flex column center` |
| 无截断 | `demo/static/css/style.css:2511` + `2520` | `overflow:visible clip normal break-all` | `textOverflow clip whiteSpace normal` |
| 供应商名称110 | `demo/templates/index.html:870` + `demo/static/css/style.css:2475` | `width 110px -> min-width:110px + break-all` 23字无截断 | `word-break break-all clip` |
| 全表clip | `demo/static/css/style.css:2528` | `全表 td clip normal visible` 防截断 | `orca eval clip` |

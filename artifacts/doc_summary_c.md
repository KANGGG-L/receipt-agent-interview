Phase0 Done: C
# SubAgent-C · 供应商协同与月结对账 — 文档通读摘要

## 一句话业务总结
供应商记忆可编辑（繁简别称一键归一 `九龙酱油↔九龍醬油`）+ 四向对账差异高亮 + CreditNote 负总额放行／非退款拦截 + 未付合计20%双行居中无截断 + 逾期/快到期分级着色与红点提醒 + Chroma tenant_id 硬过滤零泄露，是老板月结对得平、钱算得清的关卡。

## FR/场景映射表

| FR/场景 | UI 元素 | 后端接口/契约 | 验收阈值 |
|---|---|---|---|
| 供应商档案别名归一 | `tab-archive` `div#supplierAdminBody supplier-card` → `编辑` 弹 `supplierModal` 输入 `别称: 九龍醬油` | `demo/app/api_suppliers.py:68 PATCH /api/suppliers/{id}` + `services/supplier_normalizer.py:38 canonical_supplier_key` 繁→简 | `PATCH success` 即时回显，`GET /api/suppliers?q=九龙` 同 id 命中 |
| 供应商记忆可编辑 | 同上 `supNotes` | `demo/app/db.py:1085 upsert_vendor_memory` + `services/rag.py:97 ingest_memory` Chroma | 持久化 notes，下次识别 retrieve_context 可召回 |
| FR-12 财务费用结构化 | `discount/deposit/delivery/service/tax/rounding` 全口径进入 `expected_total` | `statement_reconciler/v1_0_0.py:9 reconcile()` + `services/math_engine.py:44` | 四类 matched/missing/price_discrepancy/unapplied-CN，账期匹配一目了然 |
| FR-12 四向对账核销 | `月结` Tab `openReconModal` → `reconLineBody table.reconcile tr.diff` 高亮 | `demo/app/api_finance.py:188 POST /api/reconciliation/import-statement` | diff 行高亮，导出 CSV 经另存为，2026-08-26 `cost_report total 6380` 准确 |
| 场景 CreditNote -180 | `credit_note total -180` Owner Approve 放行 | `services/contract.py:95` 仅 `credit/correction` 允许负总额 | `printed_delivery_note -1` 拦截红字 `非退款/更正单据总额不能为负数: -1` |
| NFR-5 多租户隔离 | Chroma `tenant_{id}_vendor_memory` 独立 collection | `services/rag.py:64 _tenant_collection_name` + `query_sql_gen/v1_0_0.py:19 tenant_id WHERE` | 跨租户零命中，`docs/03/05-OCR防穿透与多租户隔离方案.md:30` 四层沙箱 |
| 归档分页/逾期着色/红点 | `#archiveTableBody` 历史 + `#payablesBody` + `#navPayRedDot hide→show` | `templates/index.html:29 tr.payable-row-overdue/due-soon` + `main.js:9236 updatePaymentReminders()` | 分页防抖 + 逾期红底 / 快到期黄底 / 红点 `overdue+dueSoon`，20% 1.02rem bold双行居中无截断 `style.css:2470-2484` |

## 关联画像
Owner 月终加班对账痛点 PT-5；当月 163 张真实收据 `新协兴78 / 德利行27 ...`；全表 `text-align:center vertical-align:middle overflow:visible text-overflow:clip` 无省略，郭会计逐笔打勾可信。

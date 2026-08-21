# SubAgent-C · 供应商协同与月结对账 — 文档通读摘要

Phase0 Done: C 摘要已产出

## 一句话业务总结
供应商记忆可编辑（繁简别称一键归一）+ 四向对账差异高亮 + CreditNote 负总额放行／非退款拦截 是老板月结对得平、钱算得清的关卡。

## FR/场景映射表

| FR/场景 | UI 元素 | 后端接口/契约 | 验收阈值 |
|---|---|---|---|
| 供应商档案 | `div.supplier-card` 列表 → 卡片 `九龙酱油` → `编辑记忆` 弹窗输入 `别称: 九龍醬油` | `demo/app/api_suppliers.py` `Chroma租户硬过滤` | 可编辑保存，繁简归一；评分/别称可管 |
| FR-12 财务费用结构化 | 费用 `discount/deposit/delivery/service/tax/rounding` 拆解 | `statement_reconciler/v1_0_0.py` | 账期匹配一目了然 |
| FR-12 四向对账核销 | `月结` Tab → 选 `2026-08` → `对账` → `table.reconcile tr.diff` 高亮 | `四向对账`：系统/Statement/实收/红冲 | 差异行高亮，导出 CSV 经 `另存为` 窗口 |
| 场景14 月结核销 | 月结差异可视 + 红冲 | 同上 | 导出留痕 |
| 场景 CreditNote | `total -180` 退回死虾 Owner Approve 放行 | `contract.py:40` 仅 `CreditNote/更正` 允许负总额 | `printed_delivery_note total -1` 拦截红字 `非退款/更正单据总额不能为负数` |
| NFR-5 多租户隔离 | Chroma `tenant_id` 硬过滤 | `05-OCR防穿透与多租户隔离方案.md` 四层沙箱 | 跨租户零泄露 |

## 关联画像
Owner 月终加班对账痛点 PT-5；当月 163 张真实收据 `新协兴78 / 德利行27 ...` 为核销基数。

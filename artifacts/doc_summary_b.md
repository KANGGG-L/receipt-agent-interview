Phase0 Done: B
# SubAgent-B · 实时库存与 SKU 中心 — 文档通读摘要

## 一句话业务总结
SKU 幂等归一（有机菜心去流水号后缀 _\d{10}）+ 司马斤/磅自动换算（10司馬斤→6.048kg）+ 涨价>10%标红预警 + Append-Only台账 + 零差异盘点 是库存准确与老板即时感知成本异动的生命线，当前代码全链已闭环但历史脏数据遗留爆炸需一键自愈。

## FR/场景映射表

| FR/场景 | UI 元素 | 后端接口/算法 | 验收阈值 |
|---|---|---|---|
| FR-7 单位治理 | 库存详情 `6.048kg` 换算展示 `standard_kg` hover 司马斤×0.6048 | `currency_unit_converter/v1_0_0.py:20` `司马斤0.6048kg / 磅0.4536kg` + `api_inventory.py:38 _standard_kg` | `10司馬斤 → 6.048kg` 自动换算，计件不折算返回 null |
| FR-7 SKU 归一 | `table#inventoryTableBody` 仅 1 条`有机菜心` 去重 | `smart_splitter/v1_2_0_multi_pack.py:47` `SERIAL_SUFFIX_RE` + `api_inventory.py:17 _canonical_name` + `db.py:813 deduplicate` | 幂等：二次上传同品仍 1 行，均价加权 `(85+95)/2`，新脏数据被拦但历史遗留需清理 |
| 场景06 复合包装 | 库存行 `2樽` 保留包装单位 | `smart_splitter:51 MULTI_PACK_PATTERN` 识别 `5L*2樽` | 非 `10L`，保留 `樽`，总额 HK$ 422.00，代码有待真实单合成回归 |
| FR-6 价格预警 | 行级 `badge-danger 涨价 +X%` + 可点 `价格走势` → `priceHistoryChart` SVG 折线 | `services/price_anomaly.py:11 compute_vs_avg_and_anomaly` 阈值 `PRICE_ANOMALY_THRESHOLD_PCT 10.0` + `api_inventory.py:66` | `>10%` 标红，<2条不判异动，实测 `商品A vs 36.4%` 已标红 |
| FR-11 库存 Append-Only | `inventory_log` 台账只增不改 `kind in/consume/waste/stocktake` | `db.py:690 apply_stock_log` + `services/inventory.py:65 weighted_avg_price` | 审批入库 Approve 原子写台账，零差异盘点不落账 |
| 场景11 SKU去重 | 去重后库存合并 + 合并反哺 | `receipt_utils.py:145 canonical_sku_name` + `db.merge_skus` + `POST /api/inventory/skus/merge` | 36.8%→100% 宣称需通过 `POST /api/maintenance/deduplicate` 自愈历史爆炸 |
| 度量预留 | 盘点校准 `stocktakeModal` | `api_inventory.py:216 POST /{sku}/stocktake` + `db.stocktake_sku` | 已实现，库存全链北极星复盘 |

## 17场景锚点
场景07 SKU幂等归一、场景03 价格异动 为重点；2026-08-26 实测：`本地新鲜菜心 50斤 30.240kg` 换算通过、`商品A 36.4% anomaly true` 标红通过、`_17871*` 历史爆炸仍存需自愈。

## 度量预留
`step7-指标体系.md:1-198` 准确率/修正率/信任度 5 张度量表对应 `demo/app/api_inventory.py:1-100` + `models.py:116` 阈值统一。

## 状态机与门禁
`demo/app/db.py:690` Append-Only 只 add 不 update；`api_inventory.py:81 require_role staff 可读` / `159 require_role owner 独占写/合并` / `252 price_history owner` 满足职责分离。

# 设置与财务接口契约（单位 / 换算 / 汇率 / SKU 候选 / 付款应付 / 对账 / 库存 / 成本报表 / 餐品消耗 / 导出权限 / 供应商合并）

> 从 `docs/review-2026-10/fix-B2.md` 的接口契约部分抽出（2026-10 审查修复周期已收官，该周期的交接与交付记录已清理）。端点行为以 `demo/app/` 源码与测试为准，本文为前端与对接方的口径说明。

所有错误响应统一为 `{"status":"error","code":"XXX","msg":"人话提示","data":{...可选}}`。msg 可直接展示给店员。
权限：设置类接口 owner 及以上（admin 也可），staff 返回 403。租户由登录身份决定（演示态读 X-Tenant-Id）。

## 1. 单位与换算

### GET /api/settings/units
```
200 {"status":"success","data":{
  "units":[{"id":-1,"name":"公斤","kind":"weight","builtin":true}, {"id":7,"name":"大桶","kind":"count","builtin":false}],
  "conversions":[{"id":-4,"from_unit":"斤","to_unit":"公斤","factor":0.6048,"note":"港斤（司马斤），香港单据口径","builtin":true},
                 {"id":3,"from_unit":"箱","to_unit":"瓶","factor":12.0,"note":"","builtin":false}]}}
```
- builtin 的 id 是负数，只读：PUT/DELETE 返回 400，提示「新增同一对单位的换算来覆盖」。
- 老板覆盖种子后，列表里只显示老板的那一条（builtin=false），不再显示被覆盖的种子。
- `units` 即「SKU 基准单位下拉」的数据源（内置 + 本租户自定义）。

### POST /api/settings/units    body `{"name":"大桶","kind":"count"}`（kind 可省略，取值 weight|volume|count|other）
- 200 `{"status":"success","id":7,"msg":"单位已添加"}`
- 409 `{"code":"UNIT_DUPLICATE","msg":"「大桶」已经有了，不用重复添加"}`；400 `UNIT_INVALID`（空名、超过 20 字）

### DELETE /api/settings/units/{id}
- 200 `{"status":"success","msg":"单位已删除"}`
- 409 `UNIT_IN_USE`：msg 如「「引用桶」还在使用中，不能删除：库存商品「引用测试食材」的库存单位。请先改掉再删。」
- 400 `UNIT_DELETE_REFUSED`（内置）；404（不存在或不属于本租户）

### POST /api/settings/unit_conversions    body `{"from_unit":"箱","to_unit":"瓶","factor":12,"note":""}`
语义：1 个 from_unit = factor 个 to_unit。两个单位不存在会自动创建；同一对单位（含反向写法）已有换算则覆盖。
- 200 `{"status":"success","id":3,"msg":"换算已保存"}`
- 400 `CONVERSION_INVALID`：factor<=0/非数字/NaN、两个单位相同、单位为空、和现有换算矛盾（msg 例：「和已有换算对不上：按现有设置（经过 盒X、瓶、毫升），1 个「盒X」等于 3000 个「毫升」，而你填的是 1。请先改掉其中一条再保存。」）
- 界面示例文案建议：「1 箱 = 12 瓶」「1 斤 = 0.6048 公斤」

### PUT /api/settings/unit_conversions/{id}   body 任意子集 `{"from_unit","to_unit","factor","note"}`
- 200 `{"status":"success","id":3,"msg":"换算已保存"}`（id 不变）；校验同 POST；404 不属于本租户

### DELETE /api/settings/unit_conversions/{id}
- 200 `{"status":"success","msg":"换算已删除"}`；404；400 `CONVERSION_DELETE_REFUSED`（内置）

### GET /api/settings/unit_conversions/check?from_unit=箱&to_unit=毫升
```
200 {"status":"success","convertible":true,"factor":3960.0,"path":["箱","瓶","毫升"]}
200 {"status":"success","convertible":false,"factor":null,"path":[]}
```
BFS 多跳；种子和租户换算合并成一张图，反向自动推导。单位写法会归一（kg=公斤=千克，司马斤=港斤=catty=斤，lb=磅，ml=毫升…）。

### 入库（approve）遇到换算不了：HTTP 422
`POST /api/receipt/{id}/approve` 在写入任何库存流水之前预检；任一明细不能换算则整张单拒绝，库存不变。
```
422 {"status":"error","code":"UNIT_CONVERSION_MISSING",
     "msg":"「箱」和库存单位「斤」之间还没有换算关系，请老板到「设置」里添加换算，或把这一行的单位改成「斤」",
     "data":{"pairs":[{"from_unit":"箱","to_unit":"斤","item_name":"预检乙"}]}}
```
`pairs` 是所有需要处理的行（msg 只描述第一行；多行时末尾带「一共有 N 行需要处理」）。前端据此引导到设置页，并可用 pairs 预填「添加换算」表单（from_unit、to_unit）。

## 2. 汇率（F1b 设置页）

### GET /api/settings/fx_rates
```
200 {"status":"success","data":{"base_currency":"HKD","rates":[
  {"currency":"HKD","rate":1.0,"confirmed":true,"note":"本位币","seed":false},
  {"currency":"CNY","rate":1.08,"confirmed":false,"note":"参考值，请老板按实际汇率确认","seed":true},
  {"currency":"USD","rate":7.78,"confirmed":true,"note":"","seed":false}]}}
```
rate = 1 单位该币种折合多少港币。seed=true 表示还是系统给的参考值，界面要标明「参考值，需老板确认」。种子币种：CNY USD EUR GBP JPY SGD MOP AUD CAD TWD THB。

### PUT /api/settings/fx_rates   body `{"rates":[{"currency":"USD","rate":7.78,"confirmed":true,"note":""}]}`
- 200 `{"status":"success","msg":"汇率已保存","data":{...同 GET...}}`；400 `FX_INVALID`（币种代码不是 3 个字母、rate<=0 等）
- HKD 行会被忽略。

### 非港币单据 approve
没有汇率则 422：`{"code":"FX_RATE_MISSING","msg":"这张单是「XXX」单，设置里还没有 XXX 兑港币的汇率，请老板到「设置」里填好汇率后再入账","data":{"currency":"XXX"}}`。
有汇率则入账，并把当时的汇率存快照，之后所有报表（成本、应付、对账、库存货值）按快照折算成港币，明细里保留原币种金额。

## 3. SKU 候选（F1a 复核界面）

`GET /api/receipt/{id}` 的 `data.items[]`（以及 upload 返回的 data）里，**非 approved 单据**的每条明细多一个字段 `sku_candidates`（数组，默认 []）：
```
"items":[{"id":11,"name":"牛肉丸","sku_id":null,"matched":false,
  "sku_candidates":[{"sku_id":3,"name":"牛肉","unit":"斤",
     "reason":"名字相近：「牛肉丸」和「牛肉」互相包含；单位「包」和库存单位「斤」没有换算关系，可能不是同一种东西",
     "unit_compatible":false}]}]
```
- 只对没有 sku_id 的明细给候选，最多 5 个，名字相似度高的在前。这是读取时现算的只读字段，保存时不用回传（回传也会被忽略）。
- 老板在复核界面点「就是这个」：把该明细的 `sku_id` 设成候选的 `sku_id` 后走现有的 `POST /api/save_edited` 保存（后端据此把 matched 置 1，视为人工确认），approve 时才会并入该 SKU。
- 没有确认、也没有精确/别名命中的明细：approve 时按规范名**新建 SKU**，不会并入包含匹配到的 SKU。


## 4. 付款与应付（F1b 财务页）

### 付款状态新值 `partial`
`GET /api/receipts` 每行和 `GET /api/receipt/{id}` 里的 `payment_status` 对已入账赊单取：`unpaid | partial | paid | overdue`（现结单仍是 `paid_at_delivery`）。新增字段：
`paid_amount`（已付，港币）、`outstanding_amount`（未付，港币）、`is_overdue`（bool）、`due_date`（到期日：单据上的到期日，没有就用「单据日期 + 供应商账期天数」，都没有为空）。
partial 的单据 `is_overdue` 可能同时为 true；界面用「部分已付」徽章，逾期另加标记。

### GET /api/payables?supplier_id=（owner）  应付清单的唯一口径
```
200 {"status":"success","data":{
  "rows":[{"receipt_id":5,"supplier_id":2,"supplier_name":"甲","receipt_date":"2026-10-01","due_date":"2026-10-31",
           "currency":"USD","total_amount":100.0,"total_hkd":800.0,"paid_amount":0.0,"outstanding_amount":800.0,
           "payment_status":"unpaid","is_overdue":false,"overdue_days":0,"fx_missing":false}],
  "total_outstanding":800.0,"outstanding_count":1,"overdue_amount":0.0,"overdue_count":0,"currency":"HKD",
  "basis":"只算已入账的赊单，已付的金额已扣除；外币单据按入账时的汇率折成港币"}}
```
### GET /api/suppliers 每行新增字段（供应商表的「未付赊单合计」与上面同一口径）
`unpaid_credit_total / unpaid_credit_count`（已入账赊单的未付金额与张数，已扣已付）、`is_overdue / overdue_amount / overdue_count`、`pending_credit_total / pending_credit_count`（赊单已录入但还没审批入账的金额，另列，不进未付合计）、`unpaid_basis`（口径说明，界面用小字或 tooltip 展示在「未付赊单合计」旁边）。

### POST /api/payments（multipart 表单，owner）
字段：`supplier_id, amount, method(cash|bank_transfer|cheque|fps), paid_at(YYYY-MM-DD，不能是将来), notes?, receipt_ids[]?, allocations?(JSON 文本), voucher?(文件)`。
- 不传 `allocations`：按所选单据日期从早到晚依次付，每张最多付到它未付的金额，可以一笔付多张、只付一部分。
- 传 `allocations`，如 `[{"receipt_id":3,"amount":50}]`：按指定分配，合计必须等于 `amount`。
- 不选单据也可以登记（预付/未指定），此时 `unallocated_amount = amount`。
```
200 {"status":"success","msg":"付款已登记","id":7,
     "data":{"allocations":[{"receipt_id":3,"amount":100.0,"payment_status":"paid"},{"receipt_id":4,"amount":30.0,"payment_status":"partial"}],"unallocated_amount":0.0}}
```
错误码（均带人话 msg）：400 `PAYMENT_AMOUNT_INVALID / PAYMENT_METHOD_INVALID / PAYMENT_DATE_INVALID / RECEIPT_NOT_APPROVED / RECEIPT_NOT_CREDIT / RECEIPT_SUPPLIER_MISMATCH / PAYMENT_OVER_OUTSTANDING / PAYMENT_ALLOCATION_INVALID / PAYMENT_ALLOCATION_MISMATCH / VOUCHER_REJECTED / FX_RATE_MISSING`；404 `SUPPLIER_NOT_FOUND / RECEIPT_NOT_FOUND`（含跨租户）；409 `RECEIPT_ALREADY_PAID`（重复付已付清的单）。
凭证只收图片（按内容判断，重新编码）和 PDF（含脚本的拒绝），其他类型 400。

### GET /api/payments
每条多 `allocations:[{receipt_id,amount}]` 和 `unallocated_amount`；`voucher_image_url` 仍是签名地址。凭证取回：图片 inline，PDF 与其他文件一律 attachment 下载，全部带 `X-Content-Type-Options: nosniff`；历史上的 HTML 凭证不再以 text/html 回放。

## 5. 对账（F1b 财务页）

所有对账任务落库、按租户隔离；不存在或不属于本租户一律 404 `RECON_NOT_FOUND`；确认完成后再改返回 409 `RECON_CONFIRMED`。

- `POST /api/reconciliation` body `{supplier_id, period_type:"month|3d|7d|14d", start?, end?}`：start/end 都不填时按周期取（month=本月）。系统一侧只取该供应商（含合并过的别名）在期间内**已入账的赊单**。返回 `{status,task_id,msg}`。所有行初始为 `pending`（待核对），「已匹配」只表示和对账单一致，与有没有付款无关。
- `GET /api/reconciliation?supplier_id=`：列表项 `{id, supplier_id, supplier_name, period_start, period_end, period_type_label, status, has_statement, summary:{total_lines, matched, open_issues, statement_amount, diff_amount}}`。
- `GET /api/reconciliation/{id}` -> `{task:{id,supplier_id,period_start,period_end,status,summary}, lines:[...], restaurant_receipts:[{id,receipt_date,total_amount,matched}]}`。`task.summary`：
  `has_statement, statement_amount（对账单合计）, restaurant_only_amount（只有我方有的金额）, system_total（系统单据合计）, diff_amount（对账单合计 - 系统合计）, total_lines, matched, open_issues`。
  明细 line：`{id, side:"restaurant|statement", line_date, docket_no, amount, currency, original_amount, match_status, match_status_label, matched_receipt_id, resolution_note, resolved, statement_amount?, statement_docket_no?, statement_date?, matched_by?("docket|date_amount")}`。
  `match_status`：`pending 待核对 | matched 已匹配 | vendor_only 对账单有、我方没有 | restaurant_only 我方有、对账单没有 | amount_diff 单号对上但金额不同 | write_off 核销 | disputed 争议`。`match_status_label` 已是人话中文。
- `POST /api/reconciliation/import-statement` body `{supplier_id, statement_period:"2026-10"|"2026-10-01~2026-10-31"|"", external_lines:[{date, invoice_no?, amount, notes?}], task_id?}`：配对顺序：单号（#收据号或收据单号）-> 日期+金额；未入账的单据不参与。传 `task_id` 则把对账单导入已有任务（每个任务只能导一次，否则 409 `STATEMENT_ALREADY_IMPORTED`）。返回 `{task_id, summary:{external_statement_total, statement_amount, system_receipts_total, restaurant_only_amount, diff_amount, discrepancy_count}, discrepancies:[line...]}`。这个接口目前**没有前端入口**，需要 F1b 在对账页加「导入对账单」（粘贴/上传行：日期、单号、金额）。
- `POST /api/reconciliation/{id}/resolve` body `{line_id, action:"confirm_match|write_off|dispute", note?, receipt_id?}`：action 非法 400 `RECON_ACTION_INVALID`；line_id 不存在 404 `RECON_LINE_NOT_FOUND`；已匹配的行 409 `RECON_LINE_MATCHED`。对 `side:"statement"` 的行做 confirm_match 必须带 `receipt_id`（要对上的赊单，必须是本次对账里还没对上的），成功后两行合成一行 matched。
- `POST /api/reconciliation/{id}/confirm`：确认完成，之后不能再改（前端 `confirmReconTask` 原来调的这个接口后端之前没有）。
- 已删除：`GET /api/reconciliation/{id}/discrepancies`（前端无引用，与详情重复；差异行见详情里 `match_status` 不是 matched/write_off/disputed 的行，或 import-statement 返回的 discrepancies）。

## 6. 库存

- `POST /api/inventory/{sku_id}/consume|waste` body 新增 `allow_negative:bool, override_reason:str`。库存不够返回 409：
  `{"code":"INSUFFICIENT_STOCK","msg":"库存只有 21 斤，不够扣 1000 斤。请先核对数量；如果确实要扣，请确认后写明原因。","data":{"current_stock":21,"requested":1000,"unit":"斤","can_override":true,"reason_required":true}}`
  界面弹确认框（显示 msg）、要求填原因，再带 `allow_negative:true` 和 `override_reason` 重发。原因会写进流水备注和审计。
- `GET /api/inventory/negative_stock`（owner）：`{data:{count, items:[{sku_id,name,unit,current_stock,active,last_movement:{kind,qty,date,note},advice}]}}` 库存为负的商品巡检。
- `GET /api/price_history/{sku_id}`：`summary.avg_30d / vs_avg_pct / latest_price / min_price / max_price` 没有数据时为 **null**（不再是 0.0），界面显示「近 30 天无进货」；这时 `is_anomaly` 为 false。
- `GET /api/inventory/skus/{id}`：同样 null 语义；staff 看不到价格：`prices_visible:false` 且 `last_unit_price/avg_cost/vs_avg_pct/avg_30d/latest_price` 为 null、`batches[].unit_cost` 为 null；owner 不变（`prices_visible:true`）。
- SKU 基准单位下拉：`GET /api/settings/units` 的 `data.units`（owner）。staff 没有这个接口。

## 7. 成本报表（口径变化，前端字段不变）

`/api/cost_report`、`/api/cost_report/items`、导出 `spend_summary` 同一口径：已入账单据的 total_amount（含服务费、税、折扣、运费、押金、抹零之后的应付总额）折成港币，按明细金额比例摊到部门；作废行不计。新增字段：`cost_report.data.currency="HKD"`、`basis`（口径说明，建议报表页小字展示）、`unconverted`（没有汇率、没算进合计的外币单）、`unconverted_warning`（人话提示，非空时要显示）。
`cost_report/items` 每行的 `amount` 是该行摊到的港币金额（各行相加 = 部门成本），另有 `line_amount`（票面行金额）、`original_currency`、`original_amount`、`cost_center_id`；无明细的单据显示一行「（整单，无明细）」。

## 8. 餐品消耗

- `POST /api/dishes/daily_consumption/batch` 新增可选 `request_id`（前端每次点提交生成一个随机串，如 uuid）：同一个 request_id 重复提交只记一次，返回 `data.replayed:true`。不传则沿用「同日同餐品合并累加」。
- 新错误：`DISH_INACTIVE`（餐品已停用）、`INVALID_CONSUMPTION_DATE`、`UNIT_CONVERSION_MISSING`（422，带 data.pairs）。
- `GET /api/dishes/cost_analysis` 改为 owner；收入按售出当时的售价。
- `DELETE /api/dishes/{id}?hard=1`：被别的餐品当子配方、或已有售出记录时 409（`DISH_IN_USE / DISH_HAS_HISTORY`），提示改用停用。

## 9. 导出中心权限

`GET /api/export/reports` 的每张报表带 `min_role`。成本、毛利、进价、货值、比价和含金额的台账（`dish_ingredient_consumption, inventory_valuation, inventory_price_anomaly, dish_cost_margin, spend_summary, inventory_stock_logs, receipts_itemized_ledger`）都是 `owner`，staff 预览/下载 403；只有 `inventory_stocktake` 对 staff 开放。前端应按 `min_role` 隐藏 staff 看不到的报表。

## 10. 供应商合并

`POST /api/suppliers/merge {keep_id, drop_id}`：相同 id 返回 400 `MERGE_SAME_SUPPLIER`；找不到 404；成功 `{status,msg:"已合并",data:{moved:{receipts,payments,sku_aliases,memories}}}`。被并入的名字记为别名，对账/应付按别名也能找到这些单据。


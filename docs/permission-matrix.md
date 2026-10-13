# 端点 x 角色权限矩阵

本文件由 `demo/scripts/permission_scan.py --write` 生成，测试 `demo/tests/test_b2_permission_matrix.py` 对比代码和本文件。改了端点的权限后，请重新运行生成命令并一起提交。

角色从低到高：staff（店员）< owner（老板）< admin（管理员）。门槛是「至少需要的角色」，高角色自动包含低角色的权限。

共 162 个端点。

| 方法 | 路径 | 最低角色 | 备注 |
|---|---|---|---|
| GET | `/api/admin/canary/history` | admin |  |
| GET | `/api/admin/csp-violations` | admin |  |
| GET | `/api/admin/engine-config` | admin |  |
| PUT | `/api/admin/engine-config` | admin |  |
| GET | `/api/admin/engine-config/diff` | admin |  |
| GET | `/api/admin/engine-config/history` | admin |  |
| PUT | `/api/admin/engine-config/promote` | admin |  |
| POST | `/api/admin/engine-config/reset` | admin |  |
| PUT | `/api/admin/engine-config/rollback` | admin |  |
| GET | `/api/admin/engine-config/routing` | admin |  |
| GET | `/api/admin/engine-presets` | admin |  |
| POST | `/api/admin/engine-presets` | admin |  |
| DELETE | `/api/admin/engine-presets/{key}` | admin |  |
| GET | `/api/admin/experiments` | admin |  |
| POST | `/api/admin/experiments` | admin |  |
| GET | `/api/admin/experiments/{exp_id}` | admin |  |
| POST | `/api/admin/experiments/{exp_id}/conclude` | admin |  |
| GET | `/api/admin/experiments/{exp_id}/pvalue` | admin |  |
| GET | `/api/admin/experiments/{exp_id}/samples` | admin |  |
| POST | `/api/admin/experiments/{exp_id}/start` | admin |  |
| POST | `/api/admin/experiments/{exp_id}/stop` | admin |  |
| GET | `/api/admin/funnel` | owner |  |
| GET | `/api/admin/golden-samples` | admin |  |
| POST | `/api/admin/golden-samples/import` | admin |  |
| POST | `/api/admin/golden-samples/{receipt_id}/membership` | admin |  |
| GET | `/api/admin/grey-test` | admin |  |
| GET | `/api/admin/grey-test/samples` | admin |  |
| POST | `/api/admin/guardian/check` | admin |  |
| POST | `/api/admin/maintenance/deduplicate` | owner |  |
| GET | `/api/admin/memory-consistency` | admin |  |
| GET | `/api/admin/memory-metrics` | admin |  |
| GET | `/api/admin/metrics` | admin |  |
| GET | `/api/admin/parsing-stale` | admin |  |
| POST | `/api/admin/probe-single-engine` | admin |  |
| GET | `/api/admin/prompts` | admin |  |
| GET | `/api/admin/security-events` | admin |  |
| GET | `/api/admin/security-stats` | admin |  |
| GET | `/api/admin/settings` | admin |  |
| PUT | `/api/admin/settings` | admin |  |
| GET | `/api/admin/system-audit` | admin |  |
| POST | `/api/admin/test-engine-config` | admin |  |
| PUT | `/api/admin/unified-config` | admin |  |
| GET | `/api/admin/vendor-memory` | admin |  |
| GET | `/api/admin/vendor-memory/{memory_id}` | admin |  |
| GET | `/api/admin/vendor-sku-alias-consistency` | admin |  |
| GET | `/api/ai-insights` | owner |  |
| GET | `/api/analytics/events/logs` | admin |  |
| GET | `/api/analytics/kpi-trends` | admin |  |
| GET | `/api/analytics/parsing-logs` | admin |  |
| GET | `/api/analytics/parsing-logs/detail` | admin |  |
| GET | `/api/analytics/recognition-summary` | admin |  |
| GET | `/api/analytics/recovery-summary` | admin |  |
| POST | `/api/auth/login` | 匿名可访问 | 不要求角色（登录接口、健康检查、签名链接） |
| POST | `/api/auth/logout` | 匿名可访问 | 不要求角色（登录接口、健康检查、签名链接） |
| GET | `/api/auth/me` | 匿名可访问 | 不要求角色（登录接口、健康检查、签名链接） |
| POST | `/api/convert-image` | staff |  |
| GET | `/api/cost` | owner |  |
| GET | `/api/cost_report` | owner |  |
| GET | `/api/cost_report/items` | owner |  |
| GET | `/api/departments` | staff |  |
| POST | `/api/departments` | owner |  |
| DELETE | `/api/departments/{dept_id}` | owner |  |
| PATCH | `/api/departments/{dept_id}` | owner |  |
| GET | `/api/dishes` | staff |  |
| POST | `/api/dishes` | owner |  |
| GET | `/api/dishes/categories` | staff |  |
| DELETE | `/api/dishes/categories/{name}` | owner |  |
| GET | `/api/dishes/cost_analysis` | owner |  |
| GET | `/api/dishes/daily_consumption` | staff |  |
| POST | `/api/dishes/daily_consumption/batch` | staff |  |
| POST | `/api/dishes/daily_consumption/{consumption_id}/void` | owner |  |
| DELETE | `/api/dishes/{dish_id}` | owner |  |
| GET | `/api/dishes/{dish_id}` | staff |  |
| PUT | `/api/dishes/{dish_id}` | owner |  |
| GET | `/api/dishes/{dish_id}/versions` | staff |  |
| GET | `/api/dishes/{dish_id}/versions/{version}` | staff |  |
| POST | `/api/dishes/{dish_id}/versions/{version}/restore` | owner |  |
| GET | `/api/evalset/candidates` | admin |  |
| POST | `/api/evalset/candidates/{candidate_id}/promote` | admin |  |
| POST | `/api/evalset/candidates/{candidate_id}/reject` | admin |  |
| GET | `/api/evalset/sample/{sample_id}` | admin |  |
| POST | `/api/evalset/sample/{sample_id}/confirm` | admin |  |
| GET | `/api/evalset/samples` | admin |  |
| GET | `/api/evalset/stats` | admin |  |
| POST | `/api/export/download` | staff | 按报表分级，见下表 |
| POST | `/api/export/preview` | staff | 按报表分级，见下表 |
| GET | `/api/export/reports` | staff |  |
| GET | `/api/funnel` | owner |  |
| GET | `/api/health` | 匿名可访问 | 不要求角色（登录接口、健康检查、签名链接） |
| GET | `/api/inventory` | staff |  |
| GET | `/api/inventory/logs` | staff |  |
| GET | `/api/inventory/negative_stock` | owner |  |
| GET | `/api/inventory/price_threshold` | owner |  |
| PUT | `/api/inventory/price_threshold` | owner |  |
| GET | `/api/inventory/sku-duplicates` | owner |  |
| POST | `/api/inventory/sku-duplicates/merge` | owner |  |
| POST | `/api/inventory/skus` | staff |  |
| POST | `/api/inventory/skus/merge` | owner |  |
| DELETE | `/api/inventory/skus/{sku_id}` | owner |  |
| GET | `/api/inventory/skus/{sku_id}` | staff |  |
| PATCH | `/api/inventory/skus/{sku_id}` | owner |  |
| POST | `/api/inventory/{sku_id}/consume` | owner |  |
| DELETE | `/api/inventory/{sku_id}/price_anomaly_ack` | owner |  |
| POST | `/api/inventory/{sku_id}/price_anomaly_ack` | owner |  |
| POST | `/api/inventory/{sku_id}/stocktake` | owner |  |
| POST | `/api/inventory/{sku_id}/waste` | owner |  |
| GET | `/api/job/{job_id}` | staff |  |
| GET | `/api/memory/pending` | admin |  |
| POST | `/api/memory/pending/{pending_id}/approve` | admin |  |
| POST | `/api/memory/pending/{pending_id}/reject` | admin |  |
| DELETE | `/api/memory/vendor/{vendor_name}` | admin |  |
| DELETE | `/api/memory/{memory_id}` | admin |  |
| POST | `/api/memory/{memory_id}/revive` | admin |  |
| GET | `/api/payables` | owner |  |
| GET | `/api/payment/voucher/{name}` | 匿名可访问 | 不要求角色（登录接口、健康检查、签名链接） |
| GET | `/api/payments` | owner |  |
| POST | `/api/payments` | owner |  |
| GET | `/api/price_history/{sku_id}` | owner |  |
| GET | `/api/receipt/{receipt_id}` | staff |  |
| POST | `/api/receipt/{receipt_id}/acknowledge_warning` | staff |  |
| POST | `/api/receipt/{receipt_id}/adopt-ai` | staff |  |
| POST | `/api/receipt/{receipt_id}/approve` | owner |  |
| POST | `/api/receipt/{receipt_id}/convert_manual` | staff |  |
| POST | `/api/receipt/{receipt_id}/discard` | staff |  |
| GET | `/api/receipt/{receipt_id}/feedback` | staff |  |
| POST | `/api/receipt/{receipt_id}/feedback` | staff |  |
| POST | `/api/receipt/{receipt_id}/flag` | owner |  |
| GET | `/api/receipt/{receipt_id}/image` | 匿名可访问 | 不要求角色（登录接口、健康检查、签名链接） |
| POST | `/api/receipt/{receipt_id}/pay_date` | owner |  |
| POST | `/api/receipt/{receipt_id}/replace-image` | staff |  |
| POST | `/api/receipt/{receipt_id}/retry` | staff |  |
| POST | `/api/receipt/{receipt_id}/reverse` | owner |  |
| POST | `/api/receipt/{receipt_id}/unflag` | owner |  |
| GET | `/api/receipts` | staff |  |
| GET | `/api/reconciliation` | owner |  |
| POST | `/api/reconciliation` | owner |  |
| POST | `/api/reconciliation/import-statement` | owner |  |
| GET | `/api/reconciliation/{task_id}` | owner |  |
| POST | `/api/reconciliation/{task_id}/confirm` | owner |  |
| POST | `/api/reconciliation/{task_id}/resolve` | owner |  |
| POST | `/api/save_edited` | staff |  |
| GET | `/api/settings/fx_rates` | owner |  |
| PUT | `/api/settings/fx_rates` | owner |  |
| POST | `/api/settings/unit_conversions` | owner |  |
| GET | `/api/settings/unit_conversions/check` | owner |  |
| DELETE | `/api/settings/unit_conversions/{conv_id}` | owner |  |
| PUT | `/api/settings/unit_conversions/{conv_id}` | owner |  |
| GET | `/api/settings/units` | owner |  |
| POST | `/api/settings/units` | owner |  |
| DELETE | `/api/settings/units/{unit_id}` | owner |  |
| GET | `/api/suppliers` | staff |  |
| POST | `/api/suppliers` | owner |  |
| POST | `/api/suppliers/merge` | owner |  |
| DELETE | `/api/suppliers/{supplier_id}` | owner |  |
| PATCH | `/api/suppliers/{supplier_id}` | owner |  |
| POST | `/api/track` | staff |  |
| GET | `/api/trash/receipts` | owner |  |
| DELETE | `/api/trash/receipts/{receipt_id}` | owner |  |
| POST | `/api/trash/receipts/{receipt_id}/restore` | owner |  |
| POST | `/api/upload` | staff |  |
| POST | `/api/upload_batch` | staff |  |
| GET | `/api/vendor_price_hint` | staff |  |

## 导出中心：逐张报表的最低角色

成本、毛利、进价、货值、比价、含金额的台账一律老板及以上。

| 报表 id | 名称 | 最低角色 |
|---|---|---|
| inventory_stocktake | 实时库存盘点底单 | staff |
| dish_ingredient_consumption | 采购补货建议清单 | owner |
| inventory_valuation | 库存货值结存表 | owner |
| inventory_price_anomaly | 采购进价波动与比价表 | owner |
| dish_cost_margin | 菜品成本与毛利表 | owner |
| spend_summary | 期间花销汇总表 | owner |
| inventory_stock_logs | 出入库全量流水台账 | owner |
| receipts_itemized_ledger | 采购入账明细台账 | owner |

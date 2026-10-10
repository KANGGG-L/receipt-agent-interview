# 数据中心（Export Center）14→8 重构与缺陷修复变更说明

- 日期：2026-10-06
- 依据：`EXPORT_CENTER_TABLE_REFACTOR_NEEDS_ANALYSIS.md`（需求事实源，重点第 3/4/7 节）
- 范围：`demo/app/api_export.py`、`demo/static/js/export_center.js`、`demo/templates/index.html`、`demo/static/js/main.js` 及受影响测试

---

## 1. 删除的 6 个 report_id（连带处理器整体移除）

| report_id | 去向与原因 |
|---|---|
| `supplier_payables_aging` | 移出上线清单。硬编码假数据（逾期/7天内到期金额写死 0.0、风险阈值写死 5000）随之消亡（缺陷 A3）；账期列在 `suppliers.payment_terms_days` 全 NULL 下无法诚实供数。将来以应付管理模块回归 |
| `supplier_payments_journal` | 移出上线清单。payments 表 0 行，纯空壳 |
| `supplier_recon_discrepancies` | 移出上线清单。数据源为内存对象 `api_finance.RECON_TASKS`，重启即丢（缺陷 A4）；RECON_TASKS 落库属将来应付管理模块 |
| `dish_bom_recipes` | **移出数据中心**（B2）：BOM 配方是餐品管理的维护档案而非周期性报表。在餐品管理「餐品 BOM 配方库」子 Tab 增加「打印配方库」入口（`printDishBomLibrary()`，复用列表视图渲染出简单 A4 打印页） |
| `department_cost_summary` | 并入 `spend_summary`（合并组 2） |
| `department_items_breakdown` | 并入 `spend_summary`（合并组 2） |

## 2. 合并的 2 组报表

| 新报表 | id | 合并来源 | 粒度/维度切换 |
|---|---|---|---|
| 菜品成本与毛利表 | `dish_cost_margin` | `dish_daily_sales_cost` + `dish_margin_variance` | 列取两者并集（核算周期/分类/侵蚀点/诊断建议等 15 列）；`params.cost_granularity` 切换 `daily`（逐单）/ `period`（按餐品汇总，含零销量在售菜） |
| 期间花销汇总表 | `spend_summary` | `department_cost_summary` + `department_items_breakdown` | 部门/供应商降级为透视维度：`params.spend_pivot` 切换 `department` / `supplier`；环比增减额/变化率/趋势状态口径沿用原实现；继承 staff 403 门禁（`min_role: owner`） |

## 3. REPORTS_META 新结构（唯一事实源）

- **3 个使用者组**（原 4 业务域）：
  - `stocktake` 盘点与补货（库管）：`inventory_stocktake`、`dish_ingredient_consumption`
  - `business` 经营与毛利（owner）：`inventory_valuation`、`inventory_price_anomaly`、`dish_cost_margin`、`spend_summary`
  - `ledger` 台账与合规（出纳/审计）：`inventory_stock_logs`、`receipts_itemized_ledger`
- 每条 meta 声明：`id / category / category_name / title / description / filters / supports_desensitization / supports_drafts`，新增 **`min_role`**（权限标记，目前仅 `spend_summary: owner`）
- 前端 `export_center.js` 的 `exportCategoryTabs` 渲染同步为 3 组；筛选控件新增 `cost_granularity`（核算粒度）与 `spend_pivot`（透视维度）两种动态控件；报表清单/分组/权限不抄第二份
- 保留报表标题口径对齐新清单：库存货值结存表、出入库全量流水台账、采购入账明细台账、采购补货建议清单

## 4. Part A 缺陷修复方式

| # | 缺陷 | 修复方式 |
|---|---|---|
| A1 | preview 返回全量行，与 docstring「前 15 行示例」不符 | `preview_report` 截断为 `all_rows[:15]`（`PREVIEW_ROW_LIMIT = 15`）；`total_rows` / `summary` 保持全量口径；返回体新增 `truncated` 布尔标记；前端 `renderExportPreviewUI` 在 `truncated=true` 时追加「仅预览前 N 行，导出获取全量」提示胶囊。注：该缺陷系工作区未提交改动把原 `all_rows[:15]` 改为全量引入，本次除还原外补齐 truncated 契约 |
| A2 | 角色门禁按 report_id 硬编码且 preview/download 两处重复 | 抽成单一函数 `_enforce_report_role_gate(report_id, request)`，两端点共用；门禁依据为 `REPORTS_META` 的 `min_role` 标记（ROLE_RANK 比较），合并报表 `spend_summary` 由新机制继承门禁 |
| A3 | 应付账龄表硬编码假数据 | 报表按 Part B 整体移除，假数据列随之消亡；「账期 0 天不得被吞」的回归守卫迁移至仍存活的供应商档案展示路径（`test_falsy_zero_passthrough.py` 改守卫 `_supplier_dict` 序列化） |
| A4 | 对账差异表数据源为内存对象 | 处理器 `_handle_supplier_recon_discrepancies` 与 meta 一并移除，未为此落库 |
| A5 | 前端徽标靠字符串子串猜测 | 保持现实现不阻塞重构，已在 `export_center.js` 判定处注释记录为已知债（正道：后端显式下发单元格语义标记），非本次必做 |

## 5. B4 单位口径

- `dish_ingredient_consumption`（采购补货建议清单）新增「折算消耗(kg)」列与「折算消耗合计(kg)」汇总，折算系数走 `app.services.unit_factors.unit_to_kg_factor`（与成本层同源 SSOT；斤=0.5kg），计件/体积/空单位返回 None 不假装折算（显示 "-"）
- `inventory_valuation` 的 `折算标准kg` 列经 `api_inventory._standard_kg`（unit_factors 同源）确认一致，未改动
- 存量 receipt_items 34 种单位写法映射属数据治理，本次不做；处理器读数已按上条归一

## 6. 安全性质不回退核对

UTF-8 BOM、`_sanitize_csv_cell` 防公式注入、RFC 5987 文件名编码、`supports_desensitization` 脱敏（`spend_summary` 供应商透视支持脱敏）、`require_role`、租户隔离 `_tenant_id` 全部保留；前端仍为一张动态预览表架构，未新增每报表 DOM，未引入重复元素 ID。

## 7. 测试

- 更新：`test_export_center_ac.py`（AC-1 改 8 报表/3 组 + 被裁报表回流守卫；AC-7 改截断契约；AC-10/AC-12 改用合并报表）、`test_falsy_zero_passthrough.py`（守卫迁移）、`test_recipe_cost_caliber.py`（口径断言迁移至餐品列表展示层 + 合并报表双粒度）、`audit_export_tables.py`（审计目标换新清单）、`test_export_center_browser_e2e.py`（3 组 Tab、报表名、RBAC 目标）
- 新增：`test_export_center_refactor.py`（14 条：A1 截断 + truncated、dish_cost_margin 双粒度、spend_summary 双透视/脱敏/门禁双端点、被裁报表 400、B4 折算）
- `test_unit_factors_ssot.py` 保持全绿

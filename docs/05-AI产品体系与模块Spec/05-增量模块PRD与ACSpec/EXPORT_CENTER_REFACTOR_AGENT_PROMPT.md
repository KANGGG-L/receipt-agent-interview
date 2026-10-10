# Agent 实施 Prompt — 数据中心（Export Center）修复与重构

> 用法：将本文件全文作为任务 prompt 交给下一个 agent。文中坐标均已实地核实（2026-10-06，main 分支）。

---

你接手 `/Users/ethan/Documents/GitHub/receipt-agent-interview` 仓库（香港中小餐饮商户的小票 OCR + 库存 SaaS，FastAPI + 原生 JS 单页模板）的**数据中心模块修复与重构**。工作分两部分：A. 修复已知缺陷；B. 按已定稿的重设计方案把报表体系从 14 张收敛为 8 张。重设计方案已在需求分析文档中定稿，你的任务是落地实现，不是重新讨论方案。

## 必读文档（动手前按顺序读完）

1. `docs/05-AI产品体系与模块Spec/05-增量模块PRD与ACSpec/EXPORT_CENTER_TABLE_REFACTOR_NEEDS_ANALYSIS.md` — **本次重构的唯一需求事实源**，重点第 3 节（判定矩阵）、第 4 节（缺陷清单）、第 7 节（8 张表重设计 + 信息架构）
2. `docs/05-AI产品体系与模块Spec/05-增量模块PRD与ACSpec/EXPORT_CENTER_PRD_AC_SPEC.md` — 现行 14 报表规格，单表列结构/脱敏/打印规格仍然有效，只裁清单不推翻设计
3. `demo/app/api_export.py`（约 1700 行）— 后端：`REPORTS_META`（90-245 行）+ 14 个 `_handle_*` 处理器
4. `demo/static/js/export_center.js` — 前端：`renderExportPreviewUI()`（549 行起）渲染唯一动态预览表
5. `demo/templates/index.html` 1598-1659 行（tab-export 区块）；`demo/static/js/main.js` 1291-1293 行（惰性初始化）

## 背景事实（已调查核实，不必重查）

- 数据中心前端物理上只有一张动态预览表 `exportPreviewTable`，headers/rows 全部由后端 `REPORTS_META` + `_handle_{report_id}` 处理器下发，前端只是渲染器。**重构主要发生在后端。**
- 数据库 `demo/receipt_demo.db`（31 表）：receipts 672 / receipt_items 654 / skus 32 / suppliers 6 / departments 仅 1 个（"后厨"）/ **payments 0 行**。
- suppliers 表 `payment_terms_days` 全 NULL、`settlement_pref`/`contact_phone` 全空 → 账期类报表无数据源。
- approved 单据行项目 sku_id 关联 56/56 = 100%；未关联集中在 uploaded/parsed 等未审核单据——这是流程阶段问题，不是 bug，报表用状态口径解决。
- 单位换算 SSOT 已存在：`demo/app/services/unit_factors`（与 costing_service 同源），库存 API 已接入；缺口只在 receipt_items 存量数据（34 种单位写法）与报表侧折算展示。

## Part A — 缺陷修复（独立可交付，先做完并验证）

| # | 缺陷 | 位置 | 修复要求 |
|---|---|---|---|
| A1 | preview 返回全量行，与 docstring"前 15 行示例"不符 | `demo/app/api_export.py:1637`（`preview_rows = all_rows  # 返回全量记录展示`） | 截断至 15 行，保留 `total_rows` 与 `summary` 全量口径；如行数被截断，在返回体中加 `truncated: true` 并让前端 `renderExportPreviewUI` 提示"仅预览前 15 行，导出获取全量" |
| A2 | 角色门禁按单个 report_id 硬编码且在 preview/download 两处重复 | `api_export.py` 约 1622-1627 与 1660-1665 行 | 抽成单一函数，改为按报表 meta 的 category/权限标记判定（重构后由新报表 id 继承此门禁，见 B3） |
| A3 | 供应商应付账龄表硬编码假数据：逾期/7 天内到期金额写死 `0.0`、风险阈值写死 `5000` | `api_export.py:1166, 1183-1184` | 若该报表按 Part B 被移出上线清单则随裁剪消亡；若测试仍引用其处理器，处理器的假数据列必须改为真实计算或删除该列，禁止保留写死值 |
| A4 | 对账差异表数据源为内存对象 `api_finance.RECON_TASKS`，重启即丢 | `api_export.py:1288, 1323` | 该报表按 Part B 移出上线清单；`_handle_supplier_recon_discrepancies` 处理器一并移除即可，不需要为此落库（那是将来应付管理模块的事） |
| A5 | 前端徽标高亮靠字符串子串猜测（`cellText.includes('异常')` 等） | `demo/static/js/export_center.js` 约 617-624 行 | 保持现实现不阻塞重构，但记录为已知债；如顺手，可改为后端在 summary/单元格元数据中显式下发语义标记——非本次必做 |

## Part B — 报表体系重构（14 → 8，方案已定稿）

### B1. 目标清单与映射

| 新 # | 新报表 | 来源 | 处理动作 |
|---|---|---|---|
| 1 | 实时库存盘点底单 | `inventory_stocktake` | 保留原样（含留白手填列、签字栏、A4 打印） |
| 2 | 库存货值结存表（含低库存预警列） | `inventory_valuation` | 保留 |
| 3 | 采购补货建议清单 | `dish_ingredient_consumption` | 保留 |
| 4 | 采购进价波动与比价表 | `inventory_price_anomaly` | 保留（阈值已接租户配置，勿回退成硬编码） |
| 5 | 菜品成本与毛利表（理论 vs FIFO 实际 + 偏差归因，日/期间粒度切换） | `dish_daily_sales_cost` + `dish_margin_variance` | **合并两个处理器为一个**，列取两者并集，日/期间通过 `params` 切换 |
| 6 | 期间花销汇总表（供应商/部门两个透视维度 + 环比列） | `department_cost_summary` + `department_items_breakdown` | **合并**；部门与供应商从"报表"降级为筛选维度；继承 staff 403 门禁（A2 改造后的通用机制） |
| 7 | 出入库全量流水台账 | `inventory_stock_logs` | 保留（支持脱敏） |
| 8 | 采购入账明细台账 | `receipts_itemized_ledger` | 保留（支持草稿口径，状态口径默认 approved+edited，UI 可选含草稿并明确提示） |

### B2. 移除项

- `supplier_payables_aging`、`supplier_payments_journal`、`supplier_recon_discrepancies`：从 `REPORTS_META` 与处理器中**整体移除**（连同 A3/A4 的假数据）。这些是将来"应付管理模块"的表，本次不做占位、不做 "Coming soon" 空壳。
- `dish_bom_recipes`：**移出数据中心**。BOM 配方是餐品管理的维护档案，不是周期性报表。在餐品管理模块（tab-dishes 相关区域）内为 BOM 配方提供一个查看/打印入口即可（复用现有渲染或简单表格 + 打印，不要求新建复杂 UI）。

### B3. 信息架构调整

- `REPORTS_META` 的 category 分组从 4 业务域改为 3 个使用者组：`stocktake`（盘点与补货：#1、#3）、`business`（经营与毛利：#2、#4、#5、#6）、`ledger`（台账与合规：#7、#8）。`category_name`、`exportCategoryTabs` 渲染逻辑、CSS 类名同步更新。
- `REPORTS_META` 保持唯一事实源地位：分组、描述、筛选器、权限标记全部只在这一处声明，前端不抄写第二份清单。
- 单元格语义高亮（异常/正常徽标）维持现有后端下发文本、前端转义渲染的路径不变。

### B4. 单位口径（小项，量力完成）

- 报表侧的折算展示统一走 `app.services.unit_factors`（勿在 api_export.py 内私设换算表）；`dish_ingredient_consumption`、`inventory_valuation` 的合计列需确认折算口径与成本层一致。存量 receipt_items 的 34 种单位写法映射属数据治理，本次不强制完成，但处理器读数必须已经过 `unit_to_kg_factor` 归一。

## 约束

1. **禁止造假数据**：任何列若无真实数据源，删列或删报表，不允许硬编码、随机数、写死示例值。
2. 保持现有安全性质不回退：UTF-8 BOM、`_sanitize_csv_cell` 防公式注入、RFC 5987 文件名编码、脱敏 `supports_desensitization`、`require_role`、租户隔离 `_tenant_id`。
3. 前端保持"一张动态预览表"架构，不新增每报表独立 DOM；`index.html` 刚做过元素 ID 去重（commit 7987e1d），不要重新引入重复 ID。
4. 遵循仓库现有代码风格：中文注释、AC/WS 编号惯例、`REPORTS_META` 模板文案模式。

## 受影响的测试（改行为后必须同步更新，而不是删除断言凑绿）

- `demo/tests/test_export_center_ac.py`（3 处引用被裁报表 → 改为新清单断言）
- `demo/tests/test_export_center_browser_e2e.py`（Playwright，类目 Tab 数量与报表清单断言）
- `demo/tests/audit_export_tables.py`（3 处引用）
- `demo/tests/test_falsy_zero_passthrough.py`、`demo/tests/test_recipe_cost_caliber.py`（各 1 处引用）
- 单位口径回归：`demo/tests/test_unit_factors_ssot.py` 必须保持全绿
- 为 A1（preview 截断 + truncated 标记）和 B1 合并报表各补一条新测试

## 验证与交付

1. `python -m pytest demo/tests -x -q`（在 `demo/` 目录下，conftest 已处理 sys.path）全绿；Playwright 用例如环境不齐可说明并跳过，但需列出未跑清单。
2. 手动冒烟：启动 demo 应用，逐一切换 3 个新类目 × 8 张报表，确认预览、筛选、CSV 下载（Excel 打开无乱码）、盘点底单打印四条链路。
3. 交付物：代码 + 更新后的测试 + 一份简短变更说明（列出：删除的 6 个 report_id、合并的 2 组、`REPORTS_META` 新结构、A1/A2/A3 的修复方式）。文档层面同步：给 `EXPORT_CENTER_PRD_AC_SPEC.md` 打上"报表清单已被 REFACTOR_NEEDS_ANALYSIS 第 7 节取代"的标注并升版本号，不要求重写全文。
4. 分阶段提交：Part A 一个 commit，Part B 一个 commit（或按 B1 移除 / B1 合并 / B3 分组三个），commit message 沿用仓库现有 `feat(scope): ...` 风格。提交前征得用户同意。

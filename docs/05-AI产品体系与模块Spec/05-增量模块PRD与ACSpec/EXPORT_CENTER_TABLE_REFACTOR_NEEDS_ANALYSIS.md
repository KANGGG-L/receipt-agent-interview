# 数据中心（Export Center）表格整理与重构需求分析

- 文档版本：v1.0.0
- 文档性质：重构前置需求分析（第一性原理判定），非实现规格
- 负责角色：产品 / 需求分析
- 关联文档：`EXPORT_CENTER_PRD_AC_SPEC.md`（现行 14 报表规格）、`demo/app/api_export.py`、`demo/static/js/export_center.js`、`demo/templates/index.html`（tab-export）
- 实施状态：[IMPLEMENTED]（2026-10-06，14→8 落地，见同目录 EXPORT_CENTER_REFACTOR_CHANGELOG.md）
- 分析日期：2026-10-06

---

## 1. 分析方法：从餐饮用户回到第一性原理

不问"我们做了 14 张表，哪张好看"，而问：**一家香港中小餐饮店的 owner / 库管 / 出纳，每周和每月真正需要回答的问题是什么？系统里的数据能否支撑回答？**

三条第一性用户问题：

| 用户 | 每周/每月必须回答的问题 | 需要的数据 |
|---|---|---|
| Owner（老板） | 我库里还压多少钱的货？哪个菜在亏钱？哪个部门/供应商花超了？ | 库存货值、菜品毛利、部门/供应商花销 |
| 库管阿叔（Storekeeper） | 冷库里实际还剩多少？和账面对得上吗？ | 可打印的盘点底单 + 盘后调账闭环 |
| 出纳/财务 | 这个月要付供应商多少钱？什么时候付？账对得上吗？ | 应付账款、账期、付款流水、对账差异 |

判定一张报表是否"必需"的三个判据：

1. **用户判据**：是否直接回答上表中的问题（而非"看起来专业"）。
2. **数据判据**：数据库中是否存在支撑该报表每一列的真实数据源。
3. **闭环判据**：报表输出的结果能否回流到系统动作（调账、排款、补货），还是只是单向展示。

---

## 2. 现状盘点（事实基础）

### 2.1 前端结构

数据中心**物理上只有一张动态预览表** `exportPreviewTable`（`demo/templates/index.html:1598-1659`，tab-export 区块），由后端 `REPORTS_META` 下发 headers/rows，`demo/static/js/export_center.js` 的 `renderExportPreviewUI()` 渲染。配套子块：

- `exportCategoryTabs`：4 个业务域 Tab（inventory / dishes / suppliers / department）
- `exportReportList`：类目下报表清单
- `export-control-card`：刷新 / 打印 / CSV 导出
- `exportDynamicFilters`：8 种动态筛选控件
- `exportExtraOptions`、`exportPreviewSummary`

初始化：`demo/static/js/main.js:1291-1293`（切到 tab-export 时惰性 `initExportCenter()`）。

### 2.2 后端供数

`demo/app/api_export.py`（约 1700 行）三个端点：`/api/export/reports`、`/api/export/preview`、`/api/export/download`。14 个 `_handle_*` 处理器对应 14 套报表。已有正确设计：CSV 带 UTF-8 BOM 防乱码、防公式注入、价格异动阈值从租户配置取数（WS6 已消除硬编码 10%）、`department_cost_summary` 对 staff 返回 403。

### 2.3 数据库实测（demo/receipt_demo.db，31 张表）

| 数据源 | 实测 | 影响 |
|---|---|---|
| receipts | 672 行 | 充足 |
| receipt_items | 654 行 | 充足 |
| sku_id 关联 | 仅 134/654 行 | **但 approved 单据为 56/56 = 100%**，未关联集中在 uploaded/parsed 等未审核单据——这是复核流程的阶段性自然结果，不是 bug；全量报表需按状态过滤或明确口径 |
| skus | 32 行 | 充足 |
| suppliers | 6 行，payment_terms_days 全 NULL，settlement_pref / contact_phone 全空 | **账期/结算类报表无数据源** |
| payments | **0 行** | 付款流水类报表是空壳 |
| departments | 仅 1 个（"后厨"） | 部门间环比对照无意义 |
| receipt_items 单位 | 34 种不同单位 | 单位规范化缺失，跨单据汇总口径存疑 |

---

## 3. 14 套报表第一性判定矩阵

### 3.1 必需（保留并作为重构核心）

| 报表 | id | 判定理由 |
|---|---|---|
| 实时库存盘点底单 | `inventory_stocktake` | 库管阿叔的核心场景，"页面即产品"：留白手填列 + 签字栏 + A4 打印是真实刚需，且数据源（skus）完备。**这是数据中心不可替代的一张表** |
| 食材库存结存与货值 | `inventory_valuation` | 直接回答 owner"库里压了多少钱"，数据源完备 |
| 采购进价波动与比价 | `inventory_price_anomaly` | 议价刚需；最新进价 vs 30 日均价数据真实存在；阈值已接租户配置 |
| 每日销售与食材实际成本 | `dish_daily_sales_cost` | owner 毛利问题的主答案；FIFO 扣减是本产品技术亮点 |
| 后厨食材消耗与补货建议 | `dish_ingredient_consumption` | 直接产出补货动作，满足闭环判据 |
| 采购收据入账全量台账 | `receipts_itemized_ledger` | 财务做账基础台账，数据最全（672 单），且是唯一 supports_drafts 的流水表 |

### 3.2 保留（有价值但依赖前置数据或属进阶场景）

| 报表 | id | 理由与前置条件 |
|---|---|---|
| 出入库全量流水台账 | `inventory_stock_logs` | 审计追溯价值真实；依赖库存日志数据完整 |
| BOM 标准配方明细 | `dish_bom_recipes` | 标准成本基准，是 3.3 两张诊断表的锚；依赖 BOM 建档质量 |
| 理论毛利 vs 真实毛利偏差 | `dish_margin_variance` | owner 进阶诊断，依赖 BOM 理论成本 + FIFO 实际成本两者齐全 |
| 部门采购货品穿透明细 | `department_items_breakdown` | 单部门现状下仍可用于"后厨主管复核"，比环比表生存力强 |

### 3.3 降级 / 移除候选（数据不支撑，属演示性堆砌）

| 报表 | id | 硬伤 |
|---|---|---|
| 供应商应付账款与账期逾期表 | `supplier_payables_aging` | ① 逾期金额、7 天内到期金额**硬编码 0.0**（`api_export.py:1183-1184`）；② 风险阈值硬编码 5000（`api_export.py:1166`）；③ `receipts` 无 expected_pay_date 字段，suppliers.payment_terms_days 全 NULL——即使修掉硬编码，账期列依然无数据可填 |
| 供应商往来付款与核销台账 | `supplier_payments_journal` | **payments 表 0 行**，任何渲染都是空表或假数据，纯演示空壳 |
| 供应商月结对账差异明细 | `supplier_recon_discrepancies` | 数据源是内存对象 `api_finance.RECON_TASKS`（`api_export.py:1288,1323`），进程重启即丢，不满足持久化闭环判据 |
| 部门采购花销期间环比对照表 | `department_cost_summary` | 系统中仅 1 个部门（"后厨"），"部门间环比对照"名不副实；且这是 PRD 明示的"管理层汇报报表"，单部门下场景为假 |

**结论：14 套中有 4 套（module 3 的 3 张供应商财务表 + department 环比表）在当前数据模型下无法诚实供数，是重构裁剪的首要对象。**

---

## 4. 已知 Bug 与缺陷清单（重构时一并修复）

1. **假数据硬编码**：`supplier_payables_aging` 逾期金额 / 7 天内到期金额硬编码 `0.0`（`demo/app/api_export.py:1183-1184`）；供应商风险阈值硬编码 `5000`（`:1166`）。
2. **内存态数据源**：`supplier_recon_discrepancies` 读 `api_finance.RECON_TASKS` 内存字典，重启丢失且不可审计。
3. **preview 全量 vs 注释**：`/api/export/preview` docstring 写"前 15 行示例"（`:1616`），实际返回全量行——大表预览会把全表拉到前端。
4. **前端占位列**：`export_center.js` 渲染中存在占位假列（与后端 headers 不一致时以假列充数），需盘点并清除。
5. **单位口径**：receipt_items 34 种单位未规范化，跨单据汇总表（消耗、结存）的合计列口径存疑。
6. **sku_id 关联口径**：全量关联率 134/654，但 approved 单据 100% 关联——报表需明确"仅 approved / 含草稿"口径并在 UI 上呈现，避免把未审核单据混入财务口径。

---

## 5. 重构整理方案（分级）

### P0 — 诚实化（不做假报表）

- 移除或降级 3.3 所列 4 张报表。建议：
  - `supplier_payments_journal`：**移除**（payments 表 0 行，连字段都填不出）。
  - `supplier_payables_aging`、`supplier_recon_discrepancies`：**移除或标记 "Coming soon"**，除非先落地 receipts.expected_pay_date / payments 建档与账期数据回填。
  - `department_cost_summary`：**移除**，或先在部门管理中支持多部门建档后再恢复。
- ~~修复 preview 全量返回：按 docstring 语义截断至 15 行 + 返回 total_rows + summary~~ **（2026-10-06 用户决策修订：预览改为全量返回，与导出同口径，不再截断；docstring 与 AC-7 契约已随改。）**
- 清除前端占位列，保证 headers 与后端单一事实源一致。

### P1 — 修复与口径

- 修复 `supplier_payables_aging` 若保留路径上的硬编码（改为真实计算或干脆删除该报表）。
- 全量明细类报表（ledger、stock_logs）默认只含 approved+edited，UI 提示草稿单据是否计入。
- 单位规范化：建立单位换算 SSOT（克/kg/斤/两/件/包…），入库时归一，存量数据做一次映射清洗。

### P2 — 数据治理（让被裁掉的报表将来能回来）

- suppliers 补齐 payment_terms_days / settlement_pref / contact_phone（可由 OCR + owner 补录）。
- receipts 增加 expected_pay_date；payments 表建档并入账闭环。
- 部门管理支持多部门后，恢复 department 环比表。
- RECON_TASKS 落库，届时对账差异表才有真实数据源。

### 最终目标形态

~~保留 10 张（沿 4 业务域结构裁剪）~~ → 已被第 7 节的生产场景重设计取代：**不沿袭 14 张框架，按用户工作重新设计为 8 张**。

---

## 7. 生产场景报表体系重设计（8 张）

> v1.1 增补。前提变化：不考虑演示/测试场景，以上线后的真实使用场景为准；不沿袭"4 业务域 × N 报表 = 14 张"的框架，而是**按用户工作（job）重新设计表格数量与职责**。

### 7.1 设计原则

1. **一表一工作**：每张表绑定一个用户、一个使用节奏（每天/每周/每月/按需）、一个看完之后的动作。回答不了"看完做什么"的表不进清单。
2. **维度是筛选器，不是报表**：部门、供应商、单据状态、时间都是既有报表上的筛选条件，不再为每个维度单开报表（原 14 张中 department 两张、supplier 花销类均属此类）。
3. **数据不支撑的表不上线**：生产环境不允许硬编码 0.0 或内存态数据源（第 3.3 节的 4 张全部出局，但其中真实的需求以"模块"形态在数据模型就绪后回归，见 7.4）。
4. **打印表与屏幕表分离**：盘点底单是"页面即产品"的打印物料，不与屏显分析表混用排版逻辑。

### 7.2 新报表清单（8 张）

| # | 报表 | 使用者 / 节奏 | 看完的动作 | 数据源（实测就绪） | 取代原报表 |
|---|---|---|---|---|---|
| 1 | **实时库存盘点底单**（留白手填 + 签字栏，A4） | 库管阿叔 / 每周+月末 | 冷库现场点货 → 回来录实盘数触发调账 | skus（32 行，就绪） | `inventory_stocktake`（原样保留） |
| 2 | **库存货值结存表**（含低库存预警列） | Owner / 每月末 | 掌握压货金额、识别低库存风险 | skus + FIFO 批次 | `inventory_valuation` |
| 3 | **采购补货建议清单** | 库管阿叔 / 每周下单前 | 直接照单向供应商下单 | 消耗流水 + 当前库存 | `dish_ingredient_consumption` |
| 4 | **采购进价波动与比价表**（阈值接租户配置） | Owner / 每周议价 | 找涨幅异常食材，与供应商议价或换供应商 | 进价历史（就绪） | `inventory_price_anomaly` |
| 5 | **菜品成本与毛利表**（理论成本 vs FIFO 实际成本 + 偏差归因列，按日/期间切换） | Owner / 每周+每月 | 定价调整、下架亏损菜、查侵蚀点 | BOM + FIFO 扣减 | **合并** `dish_daily_sales_cost` + `dish_margin_variance`（同一数据链路的两段展示，owner 不需要看两张表） |
| 6 | **期间花销汇总表**（供应商 / 部门两个透视维度 + 环比指标列） | Owner / 每月汇报 | 看钱花在哪、环比涨跌，穿透到 #7 | receipts（672 单） | **合并** `department_cost_summary` + `department_items_breakdown`，并吸收供应商花销诉求（部门、供应商降级为维度） |
| 7 | **出入库全量流水台账**（支持脱敏导出） | Owner/出纳 / 按需追溯 | 审计任一笔库存变动的来龙去脉 | 库存日志 | `inventory_stock_logs` |
| 8 | **采购入账明细台账**（单据 + 行项目全景，支持草稿口径） | 出纳 / 每月做账+税局留存 | 做账凭证；HK 税局要求业务记录留存（IRSD 7 年） | receipts + receipt_items（approved 单据 sku 关联 100%） | `receipts_itemized_ledger`（原样保留） |

### 7.3 原报表的去向一览

| 原 14 张 | 去向 |
|---|---|
| inventory_stocktake / valuation / price_anomaly / stock_logs | 保留为 #1 / #2 / #4 / #7 |
| dish_daily_sales_cost + dish_margin_variance | 合并为 #5 |
| dish_ingredient_consumption | 保留为 #3 |
| dish_bom_recipes | **移出数据中心**：BOM 配方是厨房标准档案，属于餐品管理的维护对象而非周期性报表；在餐品模块内做查看/打印入口 |
| receipts_itemized_ledger | 保留为 #8 |
| supplier_payables_aging / payments_journal / recon_discrepancies | **移出上线清单**（数据不支撑）；真实需求回归为 7.4 的应付管理模块 |
| department_cost_summary + department_items_breakdown | 合并为 #6，部门/供应商降级为维度 |

### 7.4 应付管理：以"模块"而非"报表"的形态回归

应付账款、付款核销、对账差异是上线后**真实存在**的出纳需求（判定矩阵里它们死于数据模型，不是死于需求）。正确路径是把它作为一个后置模块整体交付，而不是塞三张空表进数据中心：

- 前置数据模型：`receipts.expected_pay_date`、`payments`（付款核销流水的数据回填与核销闭环，表结构已存在，当前 0 行）。
- 模块交付时自带 2 张表：**应付排款表**（合并账龄 + 到期日）与**付款核销台账**；对账差异若月结工作流上线再随行。
- 在此之前，数据中心不出现任何应付/付款/对账表。

### 7.5 信息架构调整

数据中心的导航从"4 业务域"改为**按使用者分三组**（与 7.1 原则一致，用户按"我是谁、我要干什么"找表）：

- **盘点与补货**（库管）：#1、#3
- **经营与毛利**（owner）：#2、#4、#5、#6
- **台账与合规**（出纳/审计）：#7、#8

前端无需结构改造：现有 `exportPreviewTable` + `exportCategoryTabs` 的"一表动态渲染"架构天然支持换清单换分组，`REPORTS_META` 即单一事实源。

---

## 8. Filter 缺口评估与落地记录

### 8.1 字段勘误说明（receipts 表原生字段）

在前期分析（§3.3、§5 P2、§7.4）中，曾误记 `receipts` 表缺失 `expected_pay_date`、`paid_at`、`currency` 字段。现作正式勘误修正如下：

- **勘误实测**：经数据库实测校验，`receipts` 表已原生包含 `expected_pay_date`（期望付款日期/到期日）、`paid_at`（实际付款时间戳）与 `currency`（币种代码，如 HKD/RMB/USD）三项字段。
- **业务影响与修正**：
  1. 采购入账台账（`receipts_itemized_ledger`）的付款状态筛选（`paid_status`: all / unpaid / paid / overdue）与币种筛选（`currency`）已具备原生数据源支撑，无需变更 DDL 即可直接在后端计算并过滤。
  2. 应付账款管理模块的前置数据缺口收敛：真实缺失项仅为 `suppliers.payment_terms_days`（供应商账期配置）基础数据回填及独立的 `payments`（付款核销流水的数据回填与核销闭环，表结构已存在，当前 0 行）。

### 8.2 P0 / P1 已落地 Filter 清单

依据第一性原则与生产场景需求，P0 与 P1 阶段共落地 16 项新 Filter 登记项（去重后 15 个登记名，`amount_range` 在采购入账台账与期间花销汇总表复用），全部遵循“后端统一计算过滤、前端白名单收敛渲染、三端出口（预览/CSV/打印）口径一致”的原则。详细技术设计与测试用例参见 [`EXPORT_CENTER_FILTERS_PLAN.md`](./EXPORT_CENTER_FILTERS_PLAN.md)。

| 报表名称 | 报表标识 | 新增 Filter 登记名 | 参数形式 | 控件类型 | 过滤语义与处理口径 |
|---|---|---|---|---|---|
| 采购入账明细台账 | `receipts_itemized_ledger` | `keyword` | `keyword: str` | 文本框（防抖 300ms） | 模糊匹配收据显示号（`#id`）、供应商名称（`supplier_name`）、行项目品名（`name`） |
| 采购入账明细台账 | `receipts_itemized_ledger` | `paid_status` | `paid_status: str` | 下拉选择 | `paid`（已付：`paid_at` 非空）、`unpaid`（未付：`paid_at` 为空）、`overdue`（逾期：未付且 `expected_pay_date` < 当前日期） |
| 采购入账明细台账 | `receipts_itemized_ledger` | `amount_range` | `amount_min`, `amount_max: float` | 双数字输入 | 按单据总金额（`total_amount`）闭区间过滤 |
| 采购入账明细台账 | `receipts_itemized_ledger` | `currency` | `currency: str` | 文本输入 | 按 `receipts.currency` 精确匹配过滤 |
| 菜品成本与毛利表 | `dish_cost_margin` | `margin_threshold` | `margin_pct_max: float` | 数字输入 | 仅保留实际毛利率 ≤ 指定百分比的行，用于低毛利/亏损菜品诊断 |
| 菜品成本与毛利表 | `dish_cost_margin` | `dish_status` | `dish_status: str` | 下拉选择 | 按餐品在售状态（实测 `dishes.status` 枚举为 `all` / `active` / `inactive`）过滤 |
| 菜品成本与毛利表 | `dish_cost_margin` | `dish_category` | `dish_category: str` | 下拉选择 | 按餐品分类（`dishes.category` 去重列表）过滤 |
| 采购补货建议清单 | `dish_ingredient_consumption` | `only_suggested` | `only_suggested: bool` | 复选框 | 计算建议补货量后，仅保留建议补货量 > 0 的行，直接作为采购下单依据 |
| 采购进价波动与比价表 | `inventory_price_anomaly` | `direction` | `direction: str` | 下拉选择 | 价格波动方向：`up`（涨价：`vs_pct > 0`）、`down`（降价：`vs_pct < 0`）、`all`（不过滤） |
| 采购进价波动与比价表 | `inventory_price_anomaly` | `threshold_override` | `threshold_pct: float` | 数字输入 | 临时覆盖租户 SSOT 异动阈值（仅本次查询生效，严禁持久化写回配置） |
| 采购进价波动与比价表 | `inventory_price_anomaly` | `window_days` | `window_days: int` | 下拉选择 | 历史均价计算窗口天数（支持 7 / 30 / 60 天，默认 30 天） |
| 期间花销汇总表 | `spend_summary` | `pivot_focus` | `supplier_id` / `department_id` | 下拉选择 | 与透视维度联动，聚焦指定供应商或部门，同时动态重算合计行 |
| 期间花销汇总表 | `spend_summary` | `amount_range` | `amount_min: float` | 数字输入 | 仅保留采购总额 ≥ 指定金额的明细行，合计行始终保留 |
| 出入库全量流水台账 | `inventory_stock_logs` | `receipt_ref` | `receipt_ref: str` | 文本框 | 按关联收据号过滤，自动兼容 `#1502` 与 `1502` 等多种输入形态 |
| 出入库全量流水台账 | `inventory_stock_logs` | `vendor_keyword` | `vendor: str` | 文本框 | 按出入库日志关联的供应商名称模糊匹配 |
| 库存货值结存表 | `inventory_valuation` | `value_min` | `value_min: float` | 数字输入 | 按行估算货值 ≥ 指定金额过滤行记录，顶部 Summary 关键指标仍保持全量口径 |

注：实时库存盘点底单（`inventory_stocktake`）本轮未新增 Filter，待 P2 阶段补齐物理库位模型后支持。

### 8.3 P2 阶段待办事项（依赖前置数据模型治理）

以下三项 Filter 需求因当前底层数据模型缺乏对应事实表与字段支撑，暂不列入 P0/P1 实现范围，列为 P2 阶段技术与数据治理待办：

1. **SKU ↔ 供应商映射关联（SKU-Supplier Mapping）**：
   - **业务诉求**：在盘点底单、补货清单与货值结存表中按特定供应商筛选 SKU。
   - **前置缺口**：当前系统中 SKU 与供应商之间缺乏规范的一对多/多对多主数据映射表，仅在单据明细层面有历史关联。
   - **规划方案**：待物料主数据模块建立 `sku_suppliers` 供应关系档案后接入。

2. **物理库位 / 仓区（Location / Storage Zone）**：
   - **业务诉求**：库管阿叔需要按“冷库”、“干货仓”、“前吧台”等物理区域分别导出并打印盘点底单与流水。
   - **前置缺口**：`skus` 与 `inventory_logs` 表均未定义 `location_id` / `storage_zone` 字段。
   - **规划方案**：待多仓/物理仓区模型建立并完成存量 SKU 仓位划分后引入对应筛选。

3. **操作人 / 经手人（Operator / Audited By）**：
   - **业务诉求**：出入库台账与盘点调账追溯时，支持按经手库管员、复核主管等责任人进行审计筛选。
   - **前置缺口**：库存日志与收据审核记录中尚未完整打通用户身份体系（`user_id` / `operator_name`）。
   - **规划方案**：待权限与审计日志体系完成全链路人员鉴权信息埋点后开放。


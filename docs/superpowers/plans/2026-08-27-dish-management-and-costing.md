# 餐品管理与每日餐品消耗库存联动核算系统实现计划（深度财务工程与架构修订版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建生产级「餐品管理」与「每日餐品消耗录入」子系统，打通餐品 BOM 配方与食材 SKU 实时库存，支持多级单位精准折算、自动生成入库批次，基于 FIFO 先进先出批次成本法精准核算连日进货价格波动、批次耗尽后新价承接及超卖暂估与冲销。

**Architecture:** 
- **数据与约束层**：新增餐品表（`dishes`）、配方表（`dish_ingredients`，带 `(dish_id, sku_id)` 唯一约束）、库存批次表（`inventory_batches`，带 `(sku_id, is_closed, inbound_date, id)` 复合索引）、每日消耗表（`daily_dish_consumptions`）与批次溯源明细表（`daily_consumption_details`）。
- **算法与财务工程层**：
  1. **多级单位换算引擎**：集成 `CurrencyUnitConverterTool`，将入库批次与 BOM 配方消耗统一对齐至基准单位，防御数值错位灾难。
  2. **FIFO 批次计价与耗尽承接**：按 `inbound_date ASC, id ASC` 严格排序扣减未关闭批次；当批次耗尽时自动关闭（`is_closed=1`）并以新进货批次单价实时计价。
  3. **超卖暂估与到货对冲**：无批次库存时生成暂估批次（`is_estimated=1`），新入库时自动对冲校准成本差异。
  4. **财务精度控制**：数量与单价采用 4 位小数运算，最终结算总金额采用 2 位小数四舍五入并处理尾差。
- **业务与触发器层**：
  1. 在收据审核入库（`apply_receipt_to_inventory`）与手动入库（`apply_stock_log`）核心链路埋设批次生成钩子（Batch Inbound Hook）。
  2. FastAPI 路由 `demo/app/api_dishes.py` 提供餐品 CRUD、配方管理、每日批量消耗扣减、反向冲销（Void/Rollback）及毛利成本分析接口。
- **前端交互层**：新增侧边栏「餐品与消耗管理」Tab，包含餐品配方库、每日消耗极速批量录入、FIFO 成本溯源穿透面板与全局库存 Tab 双向实时联动。

**Tech Stack:** Python 3.9+, FastAPI, SQLAlchemy, SQLite, Vanilla JS (ES6+), Playwright, Pytest.

---

## 核心财务核算模型：FIFO 先进先出与单位换算流水线

```
[收据审核/手动入库] ──> [单位统一对齐至 base_unit] ──> [写入 InventoryBatchRow (批次池)]
                                                                │
[前台录入每日餐品消耗] ──> [BOM 配方展开 (含单位换算)] ───────────┴──> [FIFO 批次扣减引擎]
                                                                        │
       ┌────────────────────────────────────────────────────────────────┴────────────────────────┐
       ▼                                                                                         ▼
【场景 A：多批次价格波动】                                                               【场景 B：批次耗尽后再重新进货】
批次1: 牛腩 10kg @ 40元/kg                                                               批次1耗尽 (remaining_qty=0, is_closed=1)
批次2: 牛腩 10kg @ 50元/kg                                                               自动承接新批次2: 牛腩 10kg @ 50元/kg
消耗 15kg: 10kg×40 + 5kg×50 = 650元                                                     后续消耗 100% 按新单价 50元/kg 精准核算
```

---

## 任务分解与开发步骤

### Task 1: 数据模型设计、索引约束与入库批次钩子集成
**Files:**
- Modify: `demo/app/db.py:50-200, 680-750`
- Modify: `demo/app/services/inventory.py:10-70`
- Create: `demo/app/services/costing_service.py`
- Test: `demo/tests/test_costing_fifo.py`

**Interfaces:**
- `DishRow`: `(id, name, category, price, description, status, created_at, updated_at)`
- `DishIngredientRow`: `(id, dish_id, sku_id, consumption_qty, unit, notes)` (带 `UniqueConstraint('dish_id', 'sku_id')`)
- `InventoryBatchRow`: `(id, sku_id, receipt_id, inbound_date, unit_cost, initial_qty, remaining_qty, unit, is_closed, is_estimated)` (带 `Index('idx_sku_batch', 'sku_id', 'is_closed', 'inbound_date', 'id')`)
- `DailyConsumptionRow`: `(id, date, dish_id, quantity, total_cost, unit_cost, notes, is_void, created_at)`
- `DailyConsumptionDetailRow`: `(id, consumption_id, sku_id, qty_consumed, unit, unit_cost, total_cost, batch_id, batch_date)`
- `CostingService.record_inbound_batch(session, sku_id, qty, unit_price, unit, date, receipt_id)`
- `CostingService.deduct_consumption_fifo(session, sku_id, qty_needed, unit_needed, date)` -> `(total_cost, details_list)`

- [x] **Step 1: 编写 FIFO 批次入库、扣减、多单位换算与跨批次价格波动单测**
- [x] **Step 2: 运行测试确认初始失败**
- [x] **Step 3: 在 `db.py` 中声明 5 张新数据表、外键索引与唯一性约束**
- [x] **Step 4: 在 `costing_service.py` 中实现多单位对齐换算、FIFO 批次扣减、耗尽自动标记、超卖暂估算法与事务原子性**
- [x] **Step 5: 在 `demo/app/services/inventory.py` 与 `db.py:apply_stock_log` 中挂载批次入库生成钩子**
- [x] **Step 6: 运行测试验证入库生成批次与 FIFO 扣减全部通过**

---

### Task 2: 餐品管理与每日批量消耗扣减/冲销后端 API
**Files:**
- Create: `demo/app/api_dishes.py`
- Modify: `demo/app/main.py:10-50`
- Test: `demo/tests/test_api_dishes.py`

**Interfaces:**
- `GET /api/dishes`: 获取餐品列表（含关联的 SKU BOM 配方、基准理论成本、当前毛利率）
- `POST /api/dishes`: 新建餐品及配方
- `PUT /api/dishes/{id}`: 更新餐品信息及配方
- `DELETE /api/dishes/{id}`: 停用/删除餐品
- `GET /api/dishes/daily_consumption?date=YYYY-MM-DD`: 查询指定日期的餐品消耗记录、扣减详情与当日成本汇总
- `POST /api/dishes/daily_consumption/batch`: 批量提交当日餐品消耗（`{"date": "...", "items": [{"dish_id": 1, "quantity": 30}]}`），原子执行 FIFO 扣减、更新 SKU 库存与流水
- `POST /api/dishes/daily_consumption/{id}/void`: 冲销/作废指定消耗记录，自动回滚批次剩余量与 SKU 库存
- `GET /api/dishes/cost_analysis`: 成本趋势分析（查看近 7/30 天各餐品由于食材价格波动引起的真实成本变化曲线）

- [x] **Step 1: 编写 API 接口端点测试（覆盖 CRUD、批量扣减、反向冲销回滚与成本分析）**
- [x] **Step 2: 运行测试确认端点未注册失败**
- [x] **Step 3: 编写 `demo/app/api_dishes.py` 实现全部路由逻辑与 RBAC 校验**
- [x] **Step 4: 将 `api_dishes` 路由挂载至 `demo/app/main.py`**
- [x] **Step 5: 运行 API 测试验证全部通过**

---

### Task 3: 前端「餐品与消耗管理」UI 界面与样式
**Files:**
- Modify: `demo/templates/index.html` (添加侧边栏按钮与 `tab-dishes` 容器结构)
- Modify: `demo/static/css/style.css` (添加餐品卡片、BOM 动态配方表、每日快速录入矩阵、成本波动分析样式)

**Interfaces:**
- 侧边栏按钮：`<button class="sidebar-btn" data-target="tab-dishes" data-title="餐品与消耗管理">`
- 视图区域：
  1. **餐品配方库 (Dish BOM Library)**：支持按分类过滤、新建/编辑餐品 Modal、食材 SKU 自动联想下拉与单份消耗量输入。
  2. **每日消耗极速录入 (Daily Fast Entry)**：支持日期选择器、平铺在售餐品表格、快速输入售出份数、实时计算预估成本、一键原子扣减。
  3. **成本穿透溯源面板 (Cost Traceability Modal)**：展示消耗食材详细批次来源（*“牛腩 10kg 来自 8-25 进货批次 @40元 + 5kg 来自 8-27 进货批次 @50元”*）。
  4. **成本趋势与毛利监控 (Cost Fluctuations & Insights)**：展示食材进价波动对各餐品成本及毛利率的影响曲线。

- [x] **Step 1: 在 `index.html` 中添加侧边栏项与 `tab-dishes` 容器及各个子面板**
- [x] **Step 2: 在 `index.html` 中添加新建/编辑餐品配方 Modal 与成本批次穿透溯源 Modal**
- [x] **Step 3: 在 `style.css` 中编写餐品卡片、BOM 徽章、批量录入表单与报表样式**

---

### Task 4: 前端交互、批次溯源与实时库存双向联动
**Files:**
- Modify: `demo/static/js/main.js` (添加餐品 CRUD、配方动态行、每日批量录入、成本穿透溯源与库存刷新联动)

**Interfaces:**
- `loadDishesList()`: 加载并渲染餐品列表与 BOM 配方理论成本
- `openDishModal(dishId)` / `saveDishModal()`: 打开/保存餐品配方
- `addDishIngredientRow(skuId, qty, unit)`: 动态添加配方食材（支持搜索库存已有 SKU）
- `loadDailyConsumption(date)`: 加载指定日期的消耗记录与成本
- `submitDailyConsumptionBatch()`: 提交当日消耗，触发 FIFO 扣减，弹窗展示批次溯源报告，并即时触发 `loadInventoryData()` 刷新实时库存表
- `voidDailyConsumption(consumptionId)`: 冲销错误录入并回滚库存

- [x] **Step 1: 实现餐品 CRUD 与 BOM 配方动态维护交互**
- [x] **Step 2: 实现每日消耗批量快速录入与一键提交**
- [x] **Step 3: 实现成本穿透溯源弹窗展示**
- [x] **Step 4: 实现与「实时库存与价格」Tab 的即时双向联动**

---

### Task 5: 财务工程与全链路 Playwright 自动化端到端验收实测
**Files:**
- Create: `scripts/test_dish_costing_e2e.py`

**Test Cases:**
1. **多单位换算折算测试**：入库 10斤 牛腩 @ 25元/斤，餐品配方消耗 250g，验证扣减数量为 0.5斤（或 0.25kg）且扣减金额为 12.50 元。
2. **价格波动与跨批次扣减测试**：第 1 天进货牛腩 10kg @ 40元/kg；第 2 天进货牛腩 10kg @ 50元/kg。录入消耗 15kg，验证前 10kg 按 40元、后 5kg 按 50元精准核算总成本为 650.00 元。
3. **前批次耗尽重新进货计价测试**：验证批次 1 耗尽后自动关闭（`is_closed=1`），后续消耗 100% 自动按新批次单价 50元/kg 计价。
4. **反向冲销与库存回滚测试**：对已录入的消耗执行冲销，验证批次剩余量与库存即时恢复。
5. **实时库存联动验证**：验证扣减后切换至「实时库存与价格」Tab，库存数量自动准确减少。

- [x] **Step 1: 编写全链路端到端自动化测试脚本**
- [x] **Step 2: 运行测试并生成端到端验收报告**

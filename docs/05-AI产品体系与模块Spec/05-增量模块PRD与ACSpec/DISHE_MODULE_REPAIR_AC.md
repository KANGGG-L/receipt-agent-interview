# 「餐品与消耗」模块缺陷修复与产品提升需求拆解与验收标准规范 (AC)

- 文档版本：v1.0.0
- 责任角色：Product_Agent
- 生效模块：餐品管理 (Dishes)、BOM 配方 (BOM Recipe)、每日消耗录入 (Daily Consumption)、FIFO 成本核算与库存扣减 (Costing & Batch Pool)
- 目标受众：后端开发工程师、前端开发工程师、QA 测试工程师、产品经理
- 状态：[APPROVED-FOR-IMPLEMENTATION]

---

## 1. 业务定位与设计原则

### 1.1 业务定位
「餐品与消耗」模块是餐饮 SaaS 系统连接前台营收与后台采购成本的核心枢纽。餐厅通过建立餐品与食材的 BOM（Bill of Materials）配方，录入每日实际售出份数，驱动系统基于真实采购入库单据生成的 FIFO（先进先出）批次池进行逐批次扣减，核算出无偏差的单品实际成本、当日理论毛利及大盘成本走势。

### 1.2 核心用户画像与操作环境
1. **店员（Staff - 主要前台录入者）**：
   - 用户特征：香港前线餐饮店员（阿叔/阿姨），未受过高等教育，电脑操作习惯依赖直觉与大色块点击；
   - 工作环境：开市备料与收市盘点高压嘈杂环境，手上可能有水渍/油渍，使用收银机触控屏或移动平板快速录入；
   - 容错要求：杜绝任何技术术语、生硬英文或 HTTP 状态码；按钮靶区必须宽大舒适；录入中断或网络波动时严禁静默清空已录数据。
2. **餐厅老板（Owner - 管理与决策者）**：
   - 关注核心：真实的单品毛利、食材涨价预警、库存损耗异常及租户数据绝对安全；
   - 权限边界：独占餐品建档、BOM 配方调整、历史消耗冲销作废、分类治理与跨期成本报表查看权限。

### 1.3 核心设计原则
- **[PR-1] 财务级记账闭环**：库存实物数量与批次池剩余数量必须绝对一致，禁止双轨分立与静默漂移。
- **[PR-2] 租户逻辑物理强隔离**：跨租户资产（SKU、批次、配方、流水）零泄漏、零引用、零跨扣。
- **[PR-3] 防呆防错与直白人话**：前端即时阻断非法输入，服务端严守门禁契约；所有提示均为通俗大白话。
- **[PR-4] 高压触控友好**：交互靶区高度不小于 38px，对比度达标 WCAG AA，支持逃生与原状保留。

---

## 2. 缺陷根因定位与影响矩阵

基于餐品模块初版缺陷走查（报告已移除）与代码静态/动态审查，梳理核心缺陷如下：

| 缺陷编号 | 严重级 | 问题描述 | 代码根因定位 | 业务影响 |
|---|---|---|---|---|
| P1-1 | **P1** | 双轨库存记账漂移：手动消耗/报损/盘点与 FIFO 批次池脱节 | [`api_inventory.py`](../../../demo/app/api_inventory.py#L256-L280) 与 [`db.py:1381`](../../../demo/app/db.py#L1381) 在 `consume`、`waste`、`stocktake` 时仅更新 `skus.current_stock`，未扣减/调整 `inventory_batches` 批次池；仅在 `kind == "in"` 时挂载入库批次钩子 | `current_stock` 与批次池剩余总量永久失真。餐品后续消耗时扣减早已报损或盘亏的幽灵批次，虚假拉低实际成本；盘盈库存无法进入批次池触发零成本暂估 |
| P1-2 | **P1** | BOM 配方跨租户引用与扣减漏洞 | [`api_dishes.py:550, 634`](../../../demo/app/api_dishes.py#L550) `create_dish` 与 `update_dish` 使用 `session.get(_SkuRow, ing.sku_id)`，未增加 `tenant_id` 过滤与归属校验 | 租户 A 可将租户 B 的食材 SKU 绑定至自身餐品；租户 A 售出消耗时，跨租户扣减租户 B 的库存与采购批次，造成严重数据污染 |
| P2-1 | **P2** | 负数消耗与零消耗未防御拦截 | [`api_inventory.py:251-280`](../../../demo/app/api_inventory.py#L251) 数量未限制 `> 0`；[`api_dishes.py:704`](../../../demo/app/api_dishes.py#L704) 静默 `continue` 丢弃 `quantity <= 0` 项 | 负数消耗反而增加库存；店员输入错误份数时系统静默跳过无任何提示，造成漏记漏扣 |
| P2-2 | **P2** | 小数/半份销售与消耗被截断静默丢失 | [`main.js:14592, 14702, 14716, 14736, 14776`](../../../demo/static/js/main.js#L14776) 统一使用 `parseInt`；输入框设置 `step="1"` | 香港餐饮高频场景（例牌半只烧鸭、0.5 份例汤）录入 0.5 份被截断为 0 份后丢弃，提示“请至少输入一种餐品”，店员无法理解 |
| P2-3 | **P2** | 跨量纲单位换算防御缺失导致原样错扣 | [`costing_service.py:77`](../../../demo/app/services/costing_service.py#L77) `convert_unit_quantity` 对无法换算单位执行 `return float(qty)` 原样返回 | 配方单位“份/碗”对应库存“kg”时，1 份直接扣减 1 kg 食材；200g 配方对应“件”直接扣 200 件，造成数量级记账灾难 |
| UX-1 | **P2** | 零成本暂估批次未明示，虚高大盘毛利率 | [`api_dishes.py:381-394`](../../../demo/app/api_dishes.py#L381) 未返回 `is_estimated`；[`main.js:14973`](../../../demo/static/js/main.js#L14973) 仅判定 `batch_id <= 0` | 暂估入库批次 id > 0 被识别为正常入库批次；大盘 88.2% 高毛利掩盖了菜心等食材成本为 ¥0 的数据缺失真相 |
| UX-2 | **P3** | 店员越权入口未屏蔽、交互靶区偏小 | [`main.js:13967`](../../../demo/static/js/main.js#L13967) 对 staff 展示“编辑配方”；按钮高度 30px、步进按钮宽度 28px 违背无障碍标准 | 店员点击编辑后提交遇 403 产生挫败感；触控靶区过窄在高压嘈杂环境下极易误触相邻输入框 |

---

## 3. 需求拆解与验收标准 (Acceptance Criteria)

### 3.1 [P1-1] 双轨库存记账漂移修复与统一扣减规范

#### 3.1.1 需求目标
彻底消灭库存台账（`skus.current_stock`）与批次池（`inventory_batches.remaining_qty`）的数据漂移。无论是前台销售餐品扣减、后厨手动领料消耗、食材报损弃置，还是月末盘点核销，必须全部通过统一的 FIFO 批次引擎进行原子扣减与增补，维持数学恒等式：
`sku.current_stock == SUM(active_batches.remaining_qty)`

#### 3.1.2 详细设计规范
1. **手动消耗与报损改写（`POST /api/inventory/{sku_id}/consume` 与 `waste`）**：
   - 禁止直接 `sku.current_stock -= qty`。
   - 统一调用 [`CostingService.deduct_consumption_fifo`](../../../demo/app/services/costing_service.py#L147)：
     ```python
     cost, details = CostingService.deduct_consumption_fifo(
         session=session,
         sku_id=sku_id,
         qty_needed=body.quantity,
         unit_needed=sku.base_unit,
         date=now_date,
     )
     ```
   - 扣减成功后扣减 `sku.current_stock -= body.quantity`，并在 `inventory_log` 中记录消耗明细与实际分摊成本 `amount=cost`。
2. **实物盘点双向对齐（`POST /api/inventory/{sku_id}/stocktake`）**：
   - 计算盘点差额 `diff = actual_qty - sku.current_stock`。
   - **若盘亏（`diff < 0`）**：调用 `CostingService.deduct_consumption_fifo` 按 FIFO 顺序自动冲减 `abs(diff)` 批次库存，记录流水 `kind="stocktake"`；
   - **若盘盈（`diff > 0`）**：调用 `CostingService.record_inbound_batch` 生成盘盈调整入库批次：
     - `initial_qty = diff`，`remaining_qty = diff`；
     - `unit_cost` 取该 SKU 最近一次采购价 `last_unit_price`（若无则取 0.0 并标记待确认）；
     - `receipt_id = None`，`notes = "盘点盘盈入库调整"`。
   - 更新 `sku.current_stock = actual_qty`。
3. **漂移巡检对账机制**：
   - 提供库存台账与批次池一致性核验逻辑，两端差额绝对值大于 `0.001` 时触发警示，禁止静默截断。

#### 3.1.3 验收标准 (AC)
- **[AC-1.1] 手动消耗扣减批次一致性**：
  - **GIVEN** SKU #1（牛腩）有采购批次 A（剩余 5kg@¥40/kg）、批次 B（剩余 10kg@¥45/kg），当前台账库存 15kg；
  - **WHEN** 库管员调用 `POST /api/inventory/1/consume` 消耗 7kg；
  - **THEN** 系统返回 HTTP 200 成功；批次 A 剩余量归 0 且标记 `is_closed=1`，批次 B 剩余量变更为 8kg；`sku.current_stock` 变更为 8kg；`inventory_log` 记录一条 `kind="consume"`、`qty=7`、`amount=290.00`（5×40 + 2×45）的流水。
- **[AC-1.2] 报损扣减批次一致性**：
  - **GIVEN** SKU #2（菜心）有批次剩余 3kg，当前台账库存 3kg；
  - **WHEN** 库管员调用 `POST /api/inventory/2/waste` 报损 1kg；
  - **THEN** 批次剩余量变更为 2kg，`sku.current_stock` 变更为 2kg，两处数据保持绝对一致。
- **[AC-1.3] 盘点盘亏自动按 FIFO 核销**：
  - **GIVEN** SKU #1 台账库存 8kg（批次 B 剩余 8kg@¥45）；
  - **WHEN** 执行盘点录入实际库存 `actual_qty = 6kg`（盘亏 2kg）；
  - **THEN** 批次 B 剩余量自动扣减至 6kg，`sku.current_stock` 更新为 6kg，流水记录盘亏损失金额 ¥90。
- **[AC-1.4] 盘点盘盈自动补入调整批次**：
  - **GIVEN** SKU #1 当前台账 6kg，批次池合计 6kg；
  - **WHEN** 执行盘点录入实际库存 `actual_qty = 9kg`（盘盈 3kg）；
  - **THEN** 批次池新增一条调整批次（`initial_qty=3kg, remaining_qty=3kg, unit_cost=45.0`）；`sku.current_stock` 变更为 9kg；批次池剩余总量与台账再次吻合（9kg）。
- **[AC-1.5] 混合操作幂等与无漂移回归**：
  - **GIVEN** 经过餐品销售扣减、手动报损、盘点交替发生 10 次；
  - **THEN** 断言 `abs(sku.current_stock - SUM(batch.remaining_qty)) < 1e-4` 恒成立。

---

### 3.2 [P1-2] BOM 配方跨租户强隔离门禁

#### 3.2.1 需求目标
严守多租户数据主权。在创建餐品、更新餐品配方、查询餐品详情及批量消耗全生命周期中，严格校验配方中的每一个 `sku_id` 均归属于当前租户上下文（`X-Tenant-Id`），阻断跨租户引用与窃取扣减。

#### 3.2.2 详细设计规范
1. **服务端接口加锁校验（`POST /api/dishes` 与 `PUT /api/dishes/{dish_id}`）**：
   - 废弃无租户过滤的 `session.get(db._SkuRow, ing.sku_id)`；
   - 统一改用作用域过滤查询：
     ```python
     sku = db.scoped(
         session.query(db._SkuRow).filter(db._SkuRow.id == ing.sku_id),
         db._SkuRow,
         tenant_id
     ).first()
     if not sku:
         return JSONResponse(
             status_code=400,
             content={
                 "status": "error",
                 "msg": f"配方中的食材 SKU #{ing.sku_id} 不存在或无权使用"
             }
         )
     ```
2. **消耗扣减防御性二次核验（`POST /api/dishes/daily_consumption/batch`）**：
   - 消耗执行前，验证餐品所属租户必须与请求头 `X-Tenant-Id` 吻合；
   - 若餐品已被篡改混入异构租户 SKU，事务整体回滚并抛出明确安全错误。
3. **报表与历史数据租户隔离（`GET /api/dishes/cost_analysis`）**：
   - 补齐 `cost_analysis` 中的行级租户过滤，使用 `_consumption_tenant_ok` 严格阻断跨租户流水透视。

#### 3.2.3 验收标准 (AC)
- **[AC-2.1] 跨租户创建餐品配方阻断**：
  - **GIVEN** 租户 `tenant_b` 拥有食材 SKU #99（黑松露酱）；
  - **WHEN** 租户 `tenant_a` 尝试发送 `POST /api/dishes`，配方包含 `{"sku_id": 99, "consumption_qty": 50, "unit": "g"}`；
  - **THEN** 服务端直接拦截并返回 HTTP 400，响应体内 `status="error"`，提示文案为纯中文直白人话：`配方中的食材 SKU #99 不存在或无权使用`；数据库未产生任何餐品记录。
- **[AC-2.2] 跨租户更新配方阻断**：
  - **GIVEN** 租户 `tenant_a` 拥有餐品 #10；
  - **WHEN** 租户 `tenant_a` 发送 `PUT /api/dishes/10`，企图追加 `tenant_b` 的食材 SKU #99；
  - **THEN** 服务端返回 HTTP 400 阻断更新，餐品 #10 原始配方保持不变。
- **[AC-2.3] 跨租户消耗扣减物理隔离**：
  - **GIVEN** 数据库底层若存在历史脏数据跨租户关联；
  - **WHEN** 租户 `tenant_a` 提交包含异构 SKU 的餐品消耗流水；
  - **THEN** 事务立即失败回滚，租户 `tenant_b` 的任何批次与库存数字严禁发生任何增减。
- **[AC-2.4] 成本大盘跨租户零泄漏**：
  - **GIVEN** 租户 `tenant_a` 与 `tenant_b` 当日均有消耗记录；
  - **WHEN** 租户 `tenant_a` 请求 `GET /api/dishes/cost_analysis`；
  - **THEN** 返回的聚合统计中仅包含 `tenant_a` 的数据，餐品列表无任何 `tenant_b` 的品名与金额。

---

### 3.3 [P2-1] 负数与零消耗输入全链路拦截

#### 3.3.1 需求目标
杜绝非法负数与零值消耗流入系统。建立前端即时防御拦截与后端契约强制校验双道防线，消灭“负数反向加库存”与“静默丢弃店员输入”的产品级缺陷。

#### 3.3.2 详细设计规范
1. **接口契约校验（Pydantic 模型与逻辑拦截）**：
   - `ConsumeBody`（`api_inventory.py`）：
     `quantity: float = Field(..., gt=0, description="消耗数量必须大于 0")`
   - `DailyConsumptionItem`（`api_dishes.py`）：
     `quantity: float = Field(..., gt=0, description="销售份数必须大于 0")`
   - `submit_daily_consumption_batch` 移除 `if item.quantity <= 0: continue` 静默跳过逻辑。若接收到 `quantity <= 0`，直接返回 HTTP 400：
     `餐品「{dish.name}」输入的售出份数必须大于 0，不能填负数或 0`。
   - 若提交的列表为空或所有项均无效，明确阻断并提示：`请至少输入一种餐品的有效份数（份数大于 0）`。
2. **前端交互与防呆（`main.js`）**：
   - 消耗输入框属性加固：`type="number"`、`min="0.1"`、`step="any"`；
   - 键盘事件监听：拦截键盘输入负号 `-`、加号 `+`、科学计数法字符 `e` / `E`；
   - 失焦（blur）防御性修复：若用户强行粘贴或通过输入法填入 `<= 0` 的内容，输入框边框标红，右侧小计显示 `¥0.00`，并在提交时给出直白浮层提示。

#### 3.3.3 验收标准 (AC)
- **[AC-3.1] 后端库存手动消耗负数拦截**：
  - **GIVEN** SKU #1 当前库存 10kg；
  - **WHEN** 客户端调用 `POST /api/inventory/1/consume`，payload 为 `{"quantity": -5.0}`；
  - **THEN** 接口返回 HTTP 400，响应说明：`消耗数量必须大于 0，不可输入负数或零`；`current_stock` 严禁变更为 15kg，保持 10kg 不变。
- **[AC-3.2] 后端餐品批量消耗负数拦截拒绝**：
  - **GIVEN** 前端提交批量消耗，其中餐品 #2 份数为 `-2`，餐品 #3 份数为 `5`；
  - **WHEN** 发起 `POST /api/dishes/daily_consumption/batch`；
  - **THEN** 后端直接拒绝整个批次并回滚，返回 HTTP 400，明确指出：`餐品「招牌牛腩煲」输入的售出份数必须大于 0，不能填负数或 0`；餐品 #3 亦不执行部分扣减（保证批次原子性）。
- **[AC-3.3] 前端负号键击物理阻断**：
  - **GIVEN** 店员在每日消耗表格的份数输入框中操作；
  - **WHEN** 店员按下键盘上的减号键 `-`；
  - **THEN** 文本框无任何字符上屏，光标位置不变。
- **[AC-3.4] 空输入与全零拦截提示**：
  - **GIVEN** 页面所有餐品份数均为 0 或空白；
  - **WHEN** 店员点击「一键扣减库存并核算真实成本」；
  - **THEN** 页面弹出警告级别 Toast：`请至少输入一种餐品的售出份数（份数必须大于 0）`，不向后端发送无效网络请求。

---

### 3.4 [P2-2] 小数/半份销售与消耗无损支持

#### 3.4.1 需求目标
全面支持香港餐饮高频的“半份”、“例牌半只”、“0.5 份”销售场景。消除前端所有 `parseInt` 截断逻辑，数据流与核算链路全程支持浮点小数，精度保留到小数点后 2~4 位，杜绝静默舍弃。

#### 3.4.2 详细设计规范
1. **前端数据采集与计算改写（`main.js`）**：
   - 彻底废除 `parseInt`，引入浮点安全解析函数：
     ```javascript
     function parseSafeQuantity(val) {
         if (val === null || val === undefined || val === '') return 0;
         const num = parseFloat(val);
         return isNaN(num) ? 0 : Math.max(0, Math.round(num * 100) / 100);
     }
     ```
   - 替换涉及点：
     - `onDishConsumeQtyInput`: 行小计计算 `const qty = parseSafeQuantity(val);`
     - `updateDishConsumeSummary`: 底部合计份数与预估营收
     - `submitDailyConsumptionBatch`: 打包待提交数组
     - `refreshDishKpiEstimates`: KPI 动态联动
   - 输入框步进微调：原生 `step="0.5"`；快速增加按钮保留 `+1`，新增或支持长按/输入小数。
2. **后端精度保持与尾差处理**：
   - 配方扣减用量：`total_qty_needed = round(ing.consumption_qty * item.quantity, 4)`；
   - 当 `item.quantity = 0.5` 时，若单份消耗 0.3kg，扣减量精确计算为 `0.15kg`；
   - 批次池 `remaining_qty` 扣减精度保持 4 位小数，金额取整到分（2 位小数），尾差平衡至明细末项。

#### 3.4.3 验收标准 (AC)
- **[AC-4.1] 前端 0.5 份输入与小计联动**：
  - **GIVEN** 餐品「招牌牛腩煲」单价 ¥68.00；
  - **WHEN** 店员在输入框键入 `0.5`；
  - **THEN** 输入框稳定展示 `0.5`，不自动跳变为 `0` 或 `1`；该行小计金额即时联动展示为 `¥34.00`。
- **[AC-4.2] 底部汇总栏小数正确相加**：
  - **GIVEN** 餐品 A 录入 `0.5` 份，餐品 B 录入 `2` 份；
  - **WHEN** 页面触发汇总联动；
  - **THEN** 底部汇总文字清晰显示：`已填 2 种餐品 ｜ 合计 2.5 份`。
- **[AC-4.3] 0.5 份消耗提交与后端无损接收**：
  - **GIVEN** 店员提交 0.5 份消耗；
  - **WHEN** `POST /api/dishes/daily_consumption/batch` 成功返回；
  - **THEN** 返回的消耗记录主表 `quantity` 值为 `0.5`；流水详情中各食材用量为单份用量的 `50%`，无任何整数强制截断。
- **[AC-4.4] 半份退回与冲销回滚精度**：
  - **GIVEN** 已入账 0.5 份餐品消耗，扣减牛腩 0.15kg；
  - **WHEN** 老板点击「冲销作废」；
  - **THEN** 批次剩余量精确回退 `+0.15kg`，库存台账精确回退 `+0.15kg`，不出现浮点多位尾数（如 `0.15000000002`）。

---

### 3.5 [P2-3] 跨量纲单位换算防御与拦截

#### 3.5.1 需求目标
彻底杜绝“1 份 = 1 kg”式的跨量纲荒唐原样错扣。对配方食材单位与库存基准单位建立严格的量纲兼容性门禁，无法直接换算时必须在建档与扣减环节坚决拦截并指导用户，禁止静默执行。

#### 3.5.2 详细设计规范
1. **度量衡体系量纲划分（`costing_service.py`）**：
   - 重量量纲（`DIMENSION_WEIGHT`）：`kg`, `公斤`, `千克`, `g`, `克`, `mg`, `毫克`, `斤`, `市斤`, `两`, `市两`, `司马斤`, `司馬斤`, `港斤`, `司马两`, `司馬兩`, `港两`, `港兩`, `磅`, `lb`, `lbs`, `t`, `吨`, `噸`；
   - 体积量纲（`DIMENSION_VOLUME`）：`l`, `升`, `liter`, `ml`, `毫升`；
   - 离散计数/件量纲（`DIMENSION_COUNT`）：`份`, `件`, `个`, `個`, `只`, `隻`, `条`, `條`, `包`, `瓶`, `罐`, `盒`, `碗`, `杯`, `碟`, `支`, `粒`。
2. **单位兼容性核验逻辑（`check_unit_compatibility`）**：
   ```python
   def check_unit_compatibility(u1: str, u2: str) -> Tuple[bool, str]:
       u1_norm = (u1 or "").strip().lower()
       u2_norm = (u2 or "").strip().lower()
       if not u1_norm or not u2_norm:
           return False, "单位不能为空"
       if u1_norm == u2_norm:
           return True, ""
       w1, w2 = u1_norm in UNIT_WEIGHT_TO_KG, u2_norm in UNIT_WEIGHT_TO_KG
       if w1 and w2:
           return True, ""
       v1, v2 = u1_norm in UNIT_VOLUME_TO_L, u2_norm in UNIT_VOLUME_TO_L
       if v1 and v2:
           return True, ""
       return False, f"单位「{u1}」与「{u2}」属于不同度量体系（如重量与计件），无法自动折算"
   ```
3. **改写 `convert_unit_quantity` 异常保护**：
   - 废除原有的 `# 无法换算则原样返回 return float(qty)`；
   - 若遇到无法折算的单位对，直接抛出 `ValueError(f"跨量纲单位不可换算: {from_unit} -> {to_unit}")`，阻断下游批次池扣减。
4. **BOM 配方建档/编辑前置门禁（`api_dishes.py`）**：
   - 在 `create_dish` / `update_dish` 遍历配料时，必须实时比对 `ing.unit` 与 `sku.base_unit`：
     - 若不兼容，直接返回 HTTP 400 人话报错：
       `食材「{sku.name}」的库存基准单位为「{sku.base_unit}」，与配方消耗单位「{ing.unit}」无法换算。请使用相匹配的单位（例如均为重量或同为体积），或前往库存管理调整该食材的基本单位。`

#### 3.5.3 验收标准 (AC)
- **[AC-5.1] 配方建档跨量纲前置拦截**：
  - **GIVEN** 食材 SKU #3（原只鲍鱼）基准单位为「只」（计件）；
  - **WHEN** 用户新建餐品配方，填写该食材用量为 `200`、单位为 `g`（重量）；
  - **THEN** 服务端直接返回 HTTP 400，提示文案清晰友好，明确阻断保存。
- **[AC-5.2] 计件与重量跨量纲阻断**：
  - **GIVEN** 食材 SKU #1（牛腩）基准单位为「kg」；
  - **WHEN** 用户修改配方企图将单位改为「份」；
  - **THEN** 界面阻断并提示：`食材「牛腩」的库存基准单位为「kg」，与配方消耗单位「份」无法换算`。
- **[AC-5.3] 运行时换算防穿透熔断**：
  - **GIVEN** 底层调用 `CostingService.convert_unit_quantity(5, "份", "kg")`；
  - **WHEN** 引擎执行换算；
  - **THEN** 坚决抛出明确异常，绝不返回原数值 `5.0`。
- **[AC-5.4] 同量纲合法单位精准折算**：
  - **GIVEN** 配方单位为「g」，批次库存单位为「kg」；
  - **WHEN** 消耗 500g；
  - **THEN** 系统正确换算为 0.5kg 并从批次中精确扣减，无报错。

---

### 3.6 [UX-1] 零成本暂估批次穿透溯源与数据完整性大盘警示

#### 3.6.1 需求目标
消除“虚高毛利率”对餐饮老板的决策误导。当某种食材因尚未录入进货收据而触发“超卖暂估”，且参考单价为 ¥0 时，系统必须在大盘、流水表及批次溯源弹窗中进行醒目标识与诚实警示，指出“当前成本被低估”。

#### 3.6.2 详细设计规范
1. **接口返回增加完整性与暂估标记**：
   - `GET /api/dishes/daily_consumption` 与 `GET /api/dishes/cost_analysis`：
     - 在 `details` 数组中为每段批次扣减明确输出：
       - `is_estimated: int`（1=暂估批次，0=正常采购批次）
       - `is_zero_cost: bool`（是否为 ¥0 成本扣减）
     - 在 `summary` 对象中追加汇总状态：
       - `has_zero_cost_batch: bool`
       - `estimated_batches_count: int`
       - `data_integrity_status: str`（"complete" ｜ "estimated_partial" ｜ "zero_cost_alert"）
       - `data_integrity_msg: str`（人话说明文案）
2. **FIFO 批次穿透溯源弹窗改写（`main.js`）**：
   - 修复前端暂估判定 Bug：
     `const isEst = (b.is_estimated === 1) || (!b.batch_id || b.batch_id <= 0);`
   - 若批次为暂估（`isEst == true`）：
     - 渲染鲜明的红色边框与警示色背景；
     - 徽章显示 `[超卖暂估批次]` 或 `[零成本暂估]`；
     - 附带直白解释说明：
       `该食材尚未录入真实进货单据，系统暂按 ¥0/暂估价计算。该餐品当前计算出的毛利偏高，待补录进货单据后系统将自动校准真实成本。`
3. **成本大盘与每日录入顶部警示横幅**：
   - 若当日流水存在 ¥0 成本或暂估批次，在大盘毛利率 KPI 卡片下方渲染一条警示横幅（Banner）：
     `提示：今日核算中包含 X 种未录入进货价的食材（暂估成本 ¥0）。当前大盘毛利率（xx%）可能偏高，请提醒老板尽快补录进货单据以还原真实利润。`

#### 3.6.3 验收标准 (AC)
- **[AC-6.1] 暂估批次身份识别无误**：
  - **GIVEN** 食材菜心无采购记录，前台提交消耗产生自增 id=26 的暂估批次；
  - **WHEN** 店员打开该流水的「批次溯源」弹窗；
  - **THEN** 菜心批次卡片带有红色 `[超卖暂估批次]` 标签，不展示伪装的“批次 #26 正常入库”。
- **[AC-6.2] ¥0 成本通俗说明展示**：
  - **GIVEN** 溯源弹窗中存在单价为 ¥0.00 的扣减批次；
  - **WHEN** 弹窗渲染明细；
  - **THEN** 明细金额旁展示黄色问号或醒目文字：`注意：暂无进货单价，按 ¥0 计入`。
- **[AC-6.3] 成本大盘完整性提示横幅**：
  - **GIVEN** 当日消耗包含 ¥0 成本食材；
  - **WHEN** 进入「餐品与消耗」主界面或成本大盘；
  - **THEN** 界面顶部出现警示横幅，明确说明毛利率虚高风险与补单指引。
- **[AC-6.4] 数据齐全后警示自动消退**：
  - **GIVEN** 老板录入采购单据并审核入库后；
  - **WHEN** 再次刷新大盘；
  - **THEN** 完整性状态恢复为 `complete`，虚高警示横幅自动隐藏。

---

### 3.7 [UX-2] 店员角色权限边界隔离与查阅模式

#### 3.7.1 需求目标
遵循系统角色隔离（RBAC）规范。彻底消除店员在界面上点击无权操作后触发 403 错误的情感挫败。店员视角下要么完全隐藏管理按钮，要么提供只读查看模式，杜绝假入口。

#### 3.7.2 详细设计规范
1. **餐品 BOM 库卡片与列表入口治理（`main.js`）**：
   - 现存代码：
     `+ ' <button type="button" class="btn btn-secondary" onclick="openDishModal(' + d.id + ')">编辑配方</button>'`
   - 改造规范：
     - 若当前用户角色为店员（`!isOwnerRoleNow()`）：
       - 将按钮文案变更为「查看配方」；
       - 按钮高度调整为标准高度 `38px`（严禁 `30px` 窄条）；
       - 彻底隐藏「停用/删除」按钮。
2. **弹窗只读态（Read-only Mode）控制**：
   - 当店员点击「查看配方」打开 `dishEditModal`：
     - 弹窗标题显示：`餐品配方详情（店员查阅）`；
     - 弹窗顶部增加温和提示条：`[提示] 店员仅支持查阅食材用量与规格。如需调整配方或售价，请联系老板。`
     - 餐品名称、分类、售价、描述输入框全部置为 `disabled="true"`；
     - 食材行下拉选择、删除行按钮、添加食材按钮全部隐藏或置灰；
     - 底部「保存修改」按钮直接隐藏，仅保留大号「关闭」按钮（高度 42px）。

#### 3.7.3 验收标准 (AC)
- **[AC-7.1] 店员视角消除越权编辑假入口**：
  - **GIVEN** 当前登录角色为店员（`staff`）；
  - **WHEN** 店员切换至「餐品 BOM 库」Tab；
  - **THEN** 所有卡片和表格行中，不存在任何名为「编辑配方」的按钮，取而代之的是「查看配方」；不存在任何「停用」或「删除」按钮。
- **[AC-7.2] 配方弹窗店员只读保护**：
  - **GIVEN** 店员点击「查看配方」打开弹窗；
  - **WHEN** 弹窗呈现；
  - **THEN** 所有表单元素为不可编辑状态，底部无「保存」按钮，店员无法触发任何引起 403 报错的提交操作。
- **[AC-7.3] 老板视角保留完整管理能力**：
  - **GIVEN** 切换角色为老板（`owner`）；
  - **WHEN** 浏览餐品库；
  - **THEN** 正常展示「编辑配方」、「新建餐品」、「停用/删除」及「管理分类」按钮，且按钮尺寸均符合无障碍标准。

---

### 3.8 [UX-3] 香港高压餐饮店员人机工程与直白人话保障

#### 3.8.1 需求目标
以“未受过高等教育、处于极度忙乱噪音环境下的前线阿叔/阿姨”为基准评测对象。消除所有技术术语、生硬英文、密集小字；确保触控靶区宽大防滑、提示文字通俗易懂。

#### 3.8.2 详细设计规范
1. **触控尺寸与无障碍指标（WCAG 2.1 AA 达标）**：
   - **操作按钮**：高度必须 `>= 38px`（推荐主按钮 42px~44px），水平内边距 `>= 14px`；
   - **步进按钮（`+` 与 `-`）**：尺寸由现有的 `28px×38px` 改为 **不小于 `38px×38px`**，按钮文字字号增加至 `18px`，加粗显示，杜绝误触；
   - **对比度**：关键文字与背景对比度必须 `>= 4.5:1`（如绿色小计 `#16a34a` 对白底为 4.58:1，警示色 `#dc2626` 对白底为 4.62:1）。
2. **通俗直白人话提示词典（去技术黑话映射）**：

| 现存生硬/技术文案 | 违规原因 | 规范通俗人话文案 |
|---|---|---|
| `当前值：空` | 数据库字段概念 | `未填写开单日期` / `未填写份数` |
| `Pydantic validation error: value is not a valid float` | 框架底层异常暴漏 | `输入的份数格式不正确，请填写数字（如 1 或 0.5）` |
| `HTTP 403 Forbidden` / `权限不足` | 网络协议术语 | `当前为店员账号，这项操作需要老板权限。请联系老板协助处理。` |
| `UnitConversionError` / `单位无法换算` | 编程类名 | `食材单位对不上（比如不能拿“份”换“斤”）。请统一改用重量或联系老板检查。` |
| `daily_consumption batch failed: dish_id not found` | 英文代码碎屑 | `找不到该餐品信息，可能已被老板停用。请刷新页面重试。` |
| `单据画质偏低或未识别成功（甩锅画质）` | 误导店员去重拍清晰图 | `单据上的数字对不上（如明细合计与总额相差 xx 元），AI 已暂停录入，请人工核对` |

3. **逃生通道与录入防丢（Escape Hatch）**：
   - **清空重置确认防误触**：点击「清空所有」按钮，必须弹出原生或自制快速确认框：`确定要清空刚才输入的所有数字吗？`，避免阿叔/阿姨忙乱中一键抹杀 10 分钟工作成果；
   - **网络异常保留原状**：若批量提交时网络断开或服务端报错，**严禁**调用 `resetDishConsumeInputs()`，保留店员已输入的所有数值，并高亮提示哪一行存在异常，方便直接修正后重试。
4. **货币符号本地化规范**：
   - 表头与数值展示严格统一：在香港餐饮语境下，统一使用 `$` 或 `HK$`（如 `$68.00`），杜绝表头写 `($)` 而内容展示 `¥` 的混乱搭配。

#### 3.8.3 验收标准 (AC)
- **[AC-8.1] 触控靶区尺寸实测达标**：
  - **GIVEN** 店员打开「每日消耗极速录入」表格；
  - **WHEN** 审查 DOM 计算样式；
  - **THEN** 所有数字步进按钮 `-` 与 `+` 的实际渲染宽度 `>= 38px`、高度 `>= 38px`；输入框高度 `>= 38px`；底部提交按钮高度 `>= 42px`。
- **[AC-8.2] 误触清空防灾机制**：
  - **GIVEN** 店员已在表格中录入了 8 道餐品的份数；
  - **WHEN** 店员不小心点击了「清空所有」按钮；
  - **THEN** 界面弹出确认对话框，若点击取消，所有已录入的份数与金额小计 100% 完整保留。
- **[AC-8.3] 提交失败现场无损保留**：
  - **GIVEN** 店员录入了 15 道餐品份数，其中第 12 项包含非法字符或网络短暂中断；
  - **WHEN** 点击提交消耗；
  - **THEN** 弹出直白人话错误提示，页面已填写的 15 项数据完好无损保留在输入框中，不需要重新录入。
- **[AC-8.4] 错误文案 100% 人话实测检查**：
  - **GIVEN** 触发任何表单拦截、权限阻断或门禁失败；
  - **WHEN** 捕获 Toast 弹窗及错误卡片文案；
  - **THEN** 全文不包含任何技术黑话（无 `NULL`, `NaN`, `undefined`, `400`, `403`, `500`, `Pydantic`, `SQL`）。
- **[AC-8.5] 货币符号一致性达标**：
  - **GIVEN** 检查餐品模块全界面（卡片、表格、溯源弹窗、KPI）；
  - **WHEN** 比对表头与行内金额标记；
  - **THEN** 货币单位前后端一致，无人民币符 `¥` 与港币符 `$` 混杂现象。

---

## 4. 接口与数据契约规范 (Data Contract)

### 4.1 接口错误码与人话响应结构
统一各接口响应结构，杜绝部分 200 `{"status":"error"}` 与部分 400 格式不一致问题。所有业务阻断与校验失败，采用标准 HTTP 状态码 + 统一人话 JSON 格式：

```json
{
  "status": "error",
  "code": "INVALID_CONSUMPTION_QUANTITY",
  "msg": "餐品「招牌牛腩煲」的售出份数必须大于 0，不可输入负数或零",
  "detail": {
    "field": "items[0].quantity",
    "rejected_value": -2.0,
    "user_action_guide": "请修改为实际售出的正数（如 1 或 0.5）后再点击提交"
  }
}
```

### 4.2 核心接口契约变更概览

#### 4.2.1 `POST /api/dishes` & `PUT /api/dishes/{dish_id}`
- **鉴权要求**：`require_role("owner")`
- **租户要求**：必须显式提取 `X-Tenant-Id`
- **校验强化**：
  1. 配料列表中的每个 `sku_id` 必须在当前租户存在；
  2. 每个配料的 `unit` 必须与该 SKU 的 `base_unit` 量纲兼容；
  3. `consumption_qty` 必须 `> 0`。
- **响应错误码**：
  - `400 TENANT_SKU_NOT_FOUND`: `配方中的食材 SKU #{id} 不存在或无权使用`
  - `400 UNIT_DIMENSION_MISMATCH`: `食材「{name}」的单位「{unit}」与库存单位「{base_unit}」无法换算`

#### 4.2.2 `POST /api/dishes/daily_consumption/batch`
- **鉴权要求**：`require_role("staff")`
- **请求体模型**：
  ```json
  {
    "date": "2026-09-03",
    "notes": "前台批量录入",
    "items": [
      {
        "dish_id": 1,
        "quantity": 0.5,
        "notes": "客退半份加单"
      }
    ]
  }
  ```
- **门禁拦截**：
  - 严禁静默丢弃任何项；
  - 若包含 `quantity <= 0` 项，返回 400 拒绝全单；
  - 执行事务原子扣减：全部成功或整体回滚。

#### 4.2.3 `GET /api/dishes/daily_consumption`
- **鉴权要求**：`require_role("staff")`
- **返回体模型追加**：
  - `details` 数组增加 `is_estimated: int`（0=采购批次，1=超卖暂估）与 `is_zero_cost: bool`；
  - `summary` 增加 `has_zero_cost_batch: bool` 与 `data_integrity_warning: str`。

---

## 5. 测试验收矩阵与回归指南

| 测试编号 | 类别 | 测试场景 | 执行工具 | 期望判定 |
|---|---|---|---|---|
| TC-D01 | 单元测试 | `test_fifo_dual_track_consistency`：手动消耗与餐品消耗混用后的库存一致性 | `pytest` | [PASS] 台账库存与批次剩余量差额为 0 |
| TC-D02 | 单元测试 | `test_stocktake_batch_reconciliation`：盘亏 FIFO 自动核销与盘盈自动补增批次 | `pytest` | [PASS] 批次池随盘点双向联动 |
| TC-D03 | 接口测试 | `test_cross_tenant_recipe_rejection`：租户 A 引用租户 B 的 SKU 建配方 | `pytest` / `TestClient` | [PASS] HTTP 400，文案为纯中文人话 |
| TC-D04 | 接口测试 | `test_cross_dimension_unit_rejection`：配方“份”对库存“kg” | `pytest` / `TestClient` | [PASS] HTTP 400，阻断创建并提示量纲冲突 |
| TC-D05 | 接口测试 | `test_negative_and_zero_quantity_batch`：提交负数/0 份数 | `pytest` / `TestClient` | [PASS] HTTP 400，拒绝并指出具体餐品 |
| TC-D06 | 接口测试 | `test_decimal_half_portion_costing`：提交 0.5 份餐品消耗 | `pytest` / `TestClient` | [PASS] HTTP 200，精确扣减 50% 配料与批次 |
| TC-GUI01 | 浏览器 E2E | 店员视角切换至餐品 Tab，审查按钮可见性与触控尺寸 | Playwright | [PASS] “编辑配方”隐藏为“查看配方”；按钮高 >= 38px |
| TC-GUI02 | 浏览器 E2E | 店员输入 0.5 份，查看小计与汇总计算，点击一键扣减 | Playwright | [PASS] 成功入账，不弹“请输入大于0”，溯源弹窗展示 |
| TC-GUI03 | 浏览器 E2E | 溯源弹窗审查超卖暂估批次与零成本批次视觉样式 | Playwright | [PASS] 红色警示徽章，通俗人话说明可见 |
| TC-GUI04 | 浏览器 E2E | 店员点击“清空所有”防误触弹窗确认与取消测试 | Playwright | [PASS] 取消后原数据完整保留 |
| TC-GUI05 | 视觉走查 | WCAG AA 对比度与字体规范抽检 | Playwright / axe | [PASS] 对比度 >= 4.5:1，字号与行距舒适 |

---

## 6. 实施路线与交付物确认清单

1. **Phase 1: 后端核心门禁与数据一致性修复**
   - 改写 `costing_service.py`：量纲划分、兼容性校验、跨量纲阻断。
   - 改写 `api_inventory.py`：手动消耗/报损/盘点接入 FIFO 批次池。
   - 改写 `api_dishes.py`：BOM 配方租户过滤强校验、负数拦截、小数无损支持、数据完整性标记透传。
2. **Phase 2: 前端触控人机工程与人话交互重构**
   - 改写 `main.js`：废除 `parseInt` 改用浮点解析、输入框加固 `step="0.5"`。
   - 步进按钮扩大为 `>= 38px×38px`，大号文字。
   - 店员视角隐藏越权入口，启用只读查阅模式。
   - 溯源弹窗识别真实暂估批次，大盘呈现完整性警告横幅。
   - 清空二次确认防误触弹窗。
3. **Phase 3: 自动化用例补齐与真机/浏览器闭环回归**
   - 补充 `test_costing_fifo.py` 与 `test_api_dishes.py` 专项回归测试集。
   - 运行 Playwright 模拟香港店员高压环境全流程实测，输出完整截图与断言日志。

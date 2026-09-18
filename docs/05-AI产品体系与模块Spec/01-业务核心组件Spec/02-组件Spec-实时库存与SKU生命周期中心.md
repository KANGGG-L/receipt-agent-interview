# 02 · 组件 Spec · 实时库存与 SKU 生命周期中心

> **模块定位**：餐饮后厨食材与资产流转中枢。承载品名核心词模糊匹配、新食材自动建档、Append-Only 只增不可篡改进销存台账、移动加权平均成本核算，以及 30 天食材价格异动风控预警。  
> **对标 14 步方案**：`step5-MVP定义`、`step8-产品方案`、`step11-PRD定稿`。  
> **实现代码**：[`api_inventory.py`](../../../demo/app/api_inventory.py)、[`services/inventory.py`](../../../demo/app/services/inventory.py)、[`templates/index.html`](../../../demo/templates/index.html) (Tab 2)  

---

## 1. 业务场景与核心痛点

- **食材称谓混乱**：
  - 同一种食材在不同供应商单据上写法各异（如“菜心”、“本地菜心苗”、“特级菜心”；“黑椒汁”、“黑椒牛扒汁”）。
  - 传统系统按纯字符串匹配会导致库房产生大量重复冗余 SKU，盘点时无法归类。
- **库存与成本糊涂账**：
  - 进货单价不断波动（如台风天蔬菜暴涨），传统手工 Excel 无法精准核算当前批次的加权单位成本。
  - 老板不知道哪些食材价格被供应商暗中提价，毛利率被蚕食。

---

## 2. 核心功能规格 (Functional Spec)

### 2.1 SKU 智能匹配与自动建档算法
当单据明细审批入库时，系统执行三级级联匹配策略：

```mermaid
flowchart TD
    Raw[单据提取的食材品名] --> ExactMatch{1. 数据库完全精准匹配?}
    ExactMatch -->|是| HitExact[关联已有 SKU]
    ExactMatch -->|否| AliasMatch{2. 供应商历史别名库匹配?}
    
    AliasMatch -->|是| HitAlias[命中别名 -> 关联已有 SKU]
    AliasMatch -->|否| CoreMatch{3. 核心词提取与停用词过滤<br/>阻断短词误合并}
    
    CoreMatch -->|相似度高且符合规则| HitCore[关联建议 SKU (带置信度)]
    CoreMatch -->|无合适候选| AutoCreate[4. 自动新建 SKU 档案<br/>写入 skus 表]
```

#### 关键防误伤规则（Anti-Collusion Rules）：
- **停用词过滤**：自动剔除“特级”、“顶级”、“新鲜”、“本地”、“特价”、“大”、“小”等修饰前缀；
- **短词防合并**：对于单字核心词（如“茶”、“油”、“蛋”），**严禁触发跨品类合并**（例如“乌龙茶”与“茶叶蛋”、“大豆油”与“蚝油”严禁合并）。

---

### 2.2 四大进销存动作与 Append-Only 台账机制

系统严格遵循财务记账标准，`inventory_logs` 表采取 **只增不改 (Append-Only)** 设计，禁止直接在数据库覆盖库存余额：

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               四大进销存业务动作流转表                                 │
├──────────────┬──────────────┬──────────────────────────────────────────────────────────┤
│ 动作类型     │ 变动符号 (±) │ 触发源与业务逻辑                                         │
├──────────────┼──────────────┼──────────────────────────────────────────────────────────┤
│ **入库 (IN)**│ `+ Quantity` │ 收据经 Owner 审批通过 (Approve) 触发；记录实际采购单价。 │
├──────────────┼──────────────┼──────────────────────────────────────────────────────────┤
│ **领料消耗** │ `- Quantity` │ 后厨/水吧日常领料出库登记；支持按菜品估算消耗。          │
├──────────────┼──────────────┼──────────────────────────────────────────────────────────┤
│ **损耗报废** │ `- Quantity` │ 变质、摔损、过期登记；计入损耗成本科目，记录损耗原因。  │
├──────────────┼──────────────┼──────────────────────────────────────────────────────────┤
│ **盘点校准** │ `± Delta`    │ 每周/月度盘点录入实物数，系统自动计算盘盈 (+) / 盘亏 (-)。│
└──────────────┴──────────────┴──────────────────────────────────────────────────────────┘
```

---

### 2.3 移动加权平均成本核算 (Weighted Average Cost)

每次新单据审批入库时，系统依据当前结存与新进批次，实时重算该 SKU 的基准加权成本：

$$\text{NewAvgCost} = \frac{(\text{CurrentStock} \times \text{CurrentAvgCost}) + (\text{InboundQty} \times \text{InboundPrice})}{\text{CurrentStock} + \text{InboundQty}}$$

---

### 2.4 食材价格异动风控预警 (Price Surge Alert)

- **基准比对**：将本次进货单价与该 SKU 过去 30 天的加权采购均价进行差值比对：
  $$\Delta_{\text{price}} = \frac{\text{本次单价} - \text{30日均价}}{\text{30日均价}} \times 100\%$$
- **触发告警**：
  - 当 $\Delta_{\text{price}} \ge +15\%$：在 Side-by-Side 界面打上红色 `Price Surge` 警示角标；
  - 在老板看板的“价格异动雷达”置顶展示，提供“查看该供应商历史报价”下钻视图。

---

## 3. 数据库表结构设计 (Technical Spec)

### 3.1 `skus`（商品基础表）
```sql
CREATE TABLE skus (
    sku_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    item_name VARCHAR(128) NOT NULL,
    category VARCHAR(64) DEFAULT 'food', -- 蔬菜/肉类/干货/水吧/耗材
    unit VARCHAR(32) NOT NULL,            -- 斤/公斤/磅/箱/包/罐
    current_stock NUMERIC(12, 3) DEFAULT 0.000,
    avg_cost NUMERIC(12, 2) DEFAULT 0.00,
    last_purchase_price NUMERIC(12, 2) DEFAULT 0.00,
    last_purchase_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 `inventory_logs`（只增流水台账表）
```sql
CREATE TABLE inventory_logs (
    log_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    sku_id VARCHAR(64) NOT NULL,
    receipt_id VARCHAR(64),               -- 关联单据 ID (入库动作必填)
    action_type VARCHAR(32) NOT NULL,     -- IN / OUT / WASTE / STOCKTAKE
    change_qty NUMERIC(12, 3) NOT NULL,   -- 正负变动量
    before_qty NUMERIC(12, 3) NOT NULL,   -- 变动前结存
    after_qty NUMERIC(12, 3) NOT NULL,    -- 变动后结存
    unit_cost NUMERIC(12, 2),             -- 本次批次单价
    operator_id VARCHAR(64) NOT NULL,     -- 操作人员 ID
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. API 接口契约一览

| 方法 | 路径 | 入参 | 返回 / 行为 | 权限 |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/api/inventory/skus` | `category, search, alert_only` | SKU 实时库存列表，支持按涨价预警筛选 | Staff / Owner |
| `GET` | `/api/inventory/skus/{sku_id}`| `sku_id: str` | SKU 详情、当前结存、加权成本、价格走势 | Staff / Owner |
| `POST` | `/api/inventory/adjust` | `sku_id, action_type, qty, reason` | 执行领料/损耗/盘点，写入 Append-Only 台账 | Staff / Owner |
| `POST` | `/api/inventory/skus/merge` | `source_sku_id, target_sku_id` | 合并冗余 SKU，迁移历史流水至主 SKU | **Owner 独占** |
| `GET` | `/api/inventory/price_history` | `sku_id: str, days: int` | 获取指定 SKU 历史采购价格折线图数据 | **Owner 独占** |

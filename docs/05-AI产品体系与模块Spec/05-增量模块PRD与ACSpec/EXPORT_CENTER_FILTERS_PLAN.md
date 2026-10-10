# 数据中心 Filter 缺口补全实施计划（P0 + P1）

- 文档版本：v1.0.0
- 文档性质：实施计划（可直接执行的 spec）
- 前置文档：`EXPORT_CENTER_TABLE_REFACTOR_NEEDS_ANALYSIS.md`（第 8 节 filter 缺口评估）、`EXPORT_CENTER_PRD_AC_SPEC.md` v2.0.0
- 实施状态：[IMPLEMENTED]（2026-10-06 完成）
- 日期：2026-10-06

---

## 0. 范围与原则

**做**：下列 8 张表中数据模型已就绪的 16 项新 filter 登记项（去重 15 个登记名；P0 四项 + P1 六表）。
**不做**：P2 三项（SKU↔供应商映射、库位/仓区、操作人）——需先补数据模型，仅在需求分析文档记录，不在本轮实现。

**原则**（沿用本轮重构的护栏）：
1. 所有筛选在**后端处理器内过滤**（参数进来后过滤 rows/查询集），禁止前端拿到全量再隐藏——预览、CSV 下载、打印三个出口必须同口径。
2. `REPORTS_META.filters` 数组是前端控件渲染的唯一依据：新 filter 必须先在该数组登记（新 filter 类型见 §8 命名），前端 `renderExportFilters()` / `collectExportParams()` 按白名单渲染与采集，不抄第二份清单。
3. 禁止造假数据兜底；所有用户输入进 SQL/比较前做好类型与空值处理；前端插值一律过 `esc()`。
4. 单位折算继续走 `app.services.unit_factors`；阈值默认值继续走租户 SSOT（新 filter 只做**临时覆盖**，不改变 SSOT 语义）。

## 1. ⑧ 采购入账明细台账 `receipts_itemized_ledger`（处理器 ~829 行起）

| 新 filter（登记名） | 参数 | 控件 | 语义 |
|---|---|---|---|
| `keyword` | `keyword: str` | 文本框（防抖 300ms） | 单字段模糊搜三处：收据显示号（`#id`）、`supplier_name`、行项目品名。大小写不敏感；空串=不过滤 |
| `paid_status` | `paid_status: all/unpaid/paid/overdue` | 下拉 | `paid`=`paid_at` 非空；`unpaid`=`paid_at` 空；`overdue`=未付且 `expected_pay_date` < 今天。字段已存在于 `receipts` |
| `amount_range` | `amount_min` / `amount_max: float` | 两个数字输入 | 按 `total_amount` 区间含端点过滤 |
| `currency` | `currency: str` | 文本输入（精确匹配） | 按 `receipts.currency` 过滤；空=不过滤 |

## 2. ⑤ 菜品成本与毛利表 `dish_cost_margin`（处理器 ~536 行起）

| 新 filter | 参数 | 控件 | 语义 |
|---|---|---|---|
| `margin_threshold` | `margin_pct_max: float` | 数字输入（0-100，后端校验钳制） | 只保留 实际毛利率 ≤ X% 的行；两种核算粒度都在计算后过滤；空=不过滤 |
| `dish_status` | `dish_status: all/active/inactive`（实测 `dishes.status` 枚举为 `active` / `inactive`） | 下拉 | 按餐品当前状态过滤；`all`=不过滤 |
| `dish_category` | `dish_category: str` | 下拉（选项=租户内去重分类） | 按 `dishes.category` 过滤 |

## 3. ② 采购补货建议清单 `dish_ingredient_consumption`（处理器 ~735 行起）

| 新 filter | 参数 | 控件 | 语义 |
|---|---|---|---|
| `only_suggested` | `only_suggested: bool` | 复选框「仅看建议补货 > 0」 | 计算完建议补货量后过滤 ≤0 的行。**本表第一优先**：输出直接可当下单清单 |

## 4. ④ 采购进价波动与比价表 `inventory_price_anomaly`（处理器 ~396 行起）

| 新 filter | 参数 | 控件 | 语义 |
|---|---|---|---|
| `direction` | `direction: all/up/down` | 下拉 | `up`=vs_pct > 0；`down`=< 0；`all`=不过滤 |
| `threshold_override` | `threshold_pct: float` | 数字输入，占位文案显示当前租户阈值 | 临时覆盖租户 SSOT 阈值（仅本次请求）；空=用 SSOT。**不得写回配置** |
| `window_days` | `window_days: 7/30/60` | 下拉 | 均价窗口天数，默认 30；`_compute_vs_avg_and_anomaly` 需接受窗口参数（默认值保持 30 不变味） |

## 5. ⑥ 期间花销汇总表 `spend_summary`（处理器 ~1000 行起）

| 新 filter | 参数 | 控件 | 语义 |
|---|---|---|---|
| `pivot_focus` | `supplier_id` / `department_id`（按当前 `spend_pivot` 二选一显示） | 下拉（复用现有 expSupplier / expDepartment 数据源） | 只保留该维度的行（合计行同步重算，不是全量合计砍掉）；空=全部 |
| `amount_range` | `amount_min: float` | 数字输入 | 本期采购额 ≥ X 的行；合计行始终保留 |

## 6. ⑦ 出入库全量流水台账 `inventory_stock_logs`（处理器 ~451 行起）

| 新 filter | 参数 | 控件 | 语义 |
|---|---|---|---|
| `receipt_ref` | `receipt_ref: str` | 文本框 | 按关联收据号过滤（`inventory_log.receipt_id` 存在）；支持 `#1502` / `1502` 两种输入（剥离 # 与非数字字符） |
| `vendor_keyword` | `vendor: str` | 文本框 | `inventory_log.vendor` 模糊匹配 |

## 7. ③ 库存货值结存表 `inventory_valuation`（处理器 ~331 行起）

| 新 filter | 参数 | 控件 | 语义 |
|---|---|---|---|
| `value_min` | `value_min: float` | 数字输入「货值 ≥ X 元」 | 按行货值估算过滤；**summary 合计仍按全量口径**（与预览行数解耦，像 total_rows 一样） |

> ① 盘点底单本轮不加 filter（缺库位字段，属 P2）。

## 8. 前端改造（`demo/static/js/export_center.js`）

1. `REPORTS_META.filters` 新登记名：`keyword`、`paid_status`、`amount_range`、`currency`、`margin_threshold`、`dish_status`、`dish_category`、`only_suggested`、`direction`、`threshold_override`、`window_days`、`pivot_focus`、`receipt_ref`、`vendor_keyword`、`value_min`（按各表归属登记）。
2. `renderExportFilters()` 新增控件分支，沿用现有 `export-control-input` 样式与 onchange 刷新模式；文本输入加 300ms 防抖（现有 `handleExportDateChange` 模式参考）。
3. `collectExportParams()` 白名单同步扩充；空值一律不进参数对象（保持现有惯例）。
4. `spend_pivot` 与 `pivot_focus` 联动：切换透视维度时重置并重渲染对应下拉。
5. 所有新控件插值过 `esc()`。

## 9. 测试（新增 `demo/tests/test_export_center_filters.py`）

沿用 `test_export_center_refactor.py` 的隔离临时库模式（`_staff_headers`、db.create_sku 等 fixture 惯例）。每个新 filter 至少一条**行为测试**（造数 → 带参请求 → 断言过滤结果）+ 一条**空参不过滤**反例：

- ledger：keyword 命中单号/品名/供应商三种形态；paid_status 四值；amount 区间含端点；currency 精确。
- cost_margin：margin_pct_max 只留低毛利行（period 粒度）；dish_status/off_sale 剔除；dish_category。
- consumption：only_suggested=true 后无 ≤0 行。
- price_anomaly：direction=up 只留涨价；threshold_pct 覆盖生效且**不写回**租户配置（再调一次不带参数仍是 SSOT 值）；window_days=7 与 30 结果可不同。
- spend_summary：pivot=supplier + supplier_id 聚焦后只有该供应商行 + 合计重算；amount_min。
- stock_logs：receipt_ref 兼容 `#` 前缀；vendor 模糊。
- valuation：value_min 过滤行但 summary 合计保持全量。
- 既有测试同步：`test_export_center_ac.py` 里 REPORTS_META 相关断言（报表数仍 8、分组不变）跑绿即可；如有 filters 断言需同步。

## 10. 文档

- `EXPORT_CENTER_TABLE_REFACTOR_NEEDS_ANALYSIS.md` 追加「第 8 节：Filter 缺口评估与落地记录」（评估表 + P2 待办 + 指向本计划）。
- 本计划文末补「实施状态」勾选。

## 11. 验证与交付

1. `cd demo && python -m pytest tests/test_export_center_filters.py tests/test_export_center_refactor.py tests/test_export_center_ac.py -q` 全绿；再跑 `tests/audit_export_tables.py tests/test_falsy_zero_passthrough.py tests/test_recipe_cost_caliber.py tests/test_unit_factors_ssot.py` 防回退。全量套件后台跑（约 30 分钟），失败逐文件归因（工作区另有无关工作流的已知失败）。
2. API 冒烟：起临时 uvicorn，curl 带 `X-Role: owner` 逐个验证新参数生效 + 空参不过滤；重点核对 ledger 的 1017 行全量数据上 keyword/paid_status 的正确性。
3. 浏览器冒烟：每张表切换新控件，确认渲染、防抖、联动（pivot_focus）正常；虚拟滚动在筛选后重置正常。
4. 禁止 commit；结束后清理自起进程；交付变更说明（新增参数清单 + 测试清单 + 未做项）。

---

## 12. 实施状态与完成记录

- 实施状态：[IMPLEMENTED]
- 完成日期：2026-10-06
- 实施结论：P0/P1 共 16 项新 Filter 登记项（去重 15 个登记名）已全部落地并经后端处理器与前端组件白名单收敛验证，P2 三项待办（SKU↔供应商映射、库位/仓区、操作人）留待数据模型就绪后迭代。


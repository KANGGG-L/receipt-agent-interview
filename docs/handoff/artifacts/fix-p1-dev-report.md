# FIX-P1 Dev 交付报告（收尾）

日期：2026-10-18（CST）　基线：HEAD ec0ecc6 + 未提交 FIX-P1 修复　执行者：Dev worker（续作）

## 范围

FIX-D01（后端数量上限 / NaN 清洗）、FIX-B001（报表日期行 768/1440 视口溢出）、FIX-A01（costTraceModal 关闭后 backdrop 残留拦截点击）。前三项由前任 worker 完成并验证；本报告记录续作部分与最终交付状态。

## 续作内容：FIX-A01 遗留断言

### 根因与判定

`test_cost_trace_modal_close_releases_clicks` 第二次提交后弹窗未重开。定位结论（dish_consumption.js）：

- `submitDailyConsumptionBatch` 的 `onSuccess`（`demo/static/js/dish_consumption.js:1086` 附近）在提交成功后**显式**执行 `inputs.forEach(inp => { inp.value = 0; })`（注释「仅在成功后清空输入框」），随后才 `await loadDailyConsumption(dateStr)` 并弹出溯源弹窗。
- 因此第二次点击提交时输入为 0，被「请至少输入一种餐品的售出份数」拦截——这是**产品预期行为**（每次提交完成即清空录入），不是 re-render 清空输入的产品缺陷。真实用户关闭溯源弹窗后再次提交，本就应重新录入份数。
- `renderDishConsumeEntryTable` 内新增的 `_prevQty` 保留逻辑（dish_consumption.js:868-886 附近）针对的是**未提交**份数被异步刷新清零的窗口，与本场景无关，保留作为防御。

**判定：测试脚本问题（用例未重填输入即期望二次提交成功），非产品缺陷。按方案 (b) 调整测试。**

### 测试修改（demo/tests/test_fix_p1_frontend_red.py）

1. `test_cost_trace_modal_close_releases_clicks`：移除遗留 DEBUG 代码；在二次提交前重新 `qty.fill("1")`（附判定注释）；测试末尾关闭二次弹出的弹窗并断言已 hide，消除对后续用例的 backdrop 残留污染（此前导致 `test_modal_open_close_cycle_no_residual` 与 `test_date_input_clickable_at_768` 顺序性失败）。
2. 前置数据 `_seed_dish_with_sku` 经真实 API 造数：SKU + 盘点 10 份 + 在售餐品；登录后显式调用 `loadDailyConsumption()` 补拉面板（首屏 DOMContentLoaded 时未登录，/api/dishes 401 拿不到行）。

产品代码本环节零改动（dish_consumption.js 的改动全部来自前任 worker 的防御性保留逻辑，见上）。

## 最终测试结果（真实执行）

- `tests/test_fix_p1_red.py`：9 passed（后端 422 数量上限、sanitize_nan、maintenance dry-run/备份/幂等）
- `tests/test_fix_p1_frontend_red.py`：6 passed（FIX-A01 modal 开关/二次提交、FIX-B001 三视口 + 768 可点性）
- 合跑：`pytest tests/test_fix_p1_red.py tests/test_fix_p1_frontend_red.py -q` → **15 passed, 24 warnings, 0 failed**（跑两遍稳定）。

## 变更文件清单

- demo/app/api_inventory.py：_qty_out_of_range / _MAX_QTY=1e9 → 422；logs 序列化 sanitize_nan
- demo/app/maintenance.py：sanitize_stock_logs（dry-run / 备份 / 幂等）
- demo/static/css/style.css：@media(max-width:1024px) wrap + date input flex 定宽（FIX-B001）
- demo/static/js/dish_consumption.js：re-render 保留未提交份数（防御）
- demo/static/js/finance_ops.js：NaN 清洗联动
- demo/tests/test_fix_p1_red.py、demo/tests/test_fix_p1_frontend_red.py：TDD 用例（新增）

## 遗留 / 风险

- 无已知阻塞。24 条 pytest warnings 为既有 startup on_event 弃用提示，不在本修复范围。

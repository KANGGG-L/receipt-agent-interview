# Module B · 实时库存与 SKU 中心 (owner视角) — Orca 真机 E2E 报告

> 基准：`artifacts/doc_summary_b.md` + `docs/05/01-业务核心组件Spec/02-组件Spec-实时库存与SKU生命周期中心.md:66` 加权成本 + `ai_registry/tools/currency_unit_converter/v1_0_0.py:20` + `ai_registry/tools/smart_splitter/v1_2_0_multi_pack.py:47` + `demo/app/api_inventory.py:1-100`

## 执行轨迹 (Orca 截图 + API 日志)

| 步骤 | 操作 | 截图 | 日志 | 断言 |
|---|---|---|---|---|
| B-00 | 健康与权限：`GET /api/health → ok` + `orca goto http://127.0.0.1:15010` + `orca snapshot` 确认 6侧边 | — | `health ok db ok engine opencode true` `demo/app/main.py:72` | 通过：前后端 ready |
| B-01 | 切 owner：`orca select #demoRoleSelect value=owner` + `orca snapshot` 确认 `● owner·老板` | `artifacts/e2e/b-00-inventory.png` (历史) + `b-66px.png` 待补 | `localStorage demo_role owner` `demo/templates/index.html:73 select` | 通过：owner 生效 |
| B-02 | 切库存 Tab：`orca click [data-target=tab-inventory]` + `orca snapshot` 确认 `section#tab-inventory.active` | `artifacts/e2e/b-00-inventory.png` | `main.js:5325 loadInventoryData()` 被调用 | 通过：tab 激活 |
| B-03 | 66px diff0.0 + 44px/16px 测量：`orca eval getBoundingClientRect th/td` + `getComputedStyle button` | `artifacts/e2e/b-66px-strict.png` (2026-08-26 实测) | `demo/static/css/style.css:2398 #tab-inventory th,td height:66px` + `demo/static/css/style.css:825 .inv-actions flex-wrap:nowrap overflow-x:auto` + `demo/static/css/style.css:838 .inv-actions button min-height:44px min-width:44px font-size:1rem` 实测 `orca eval [#inventoryTable th]=66` `[#inventoryTable td]=66` diff0.0，`button height 44.49px fontSize 16px` 均达标 | 通过：th/td 同66 diff0.0，按钮44/16达标 |
| B-03a | 严格验收补充：`orca eval getComputedStyle(td).textOverflow clip whiteSpace normal` | `b-66px-strict.png` 同批 | `demo/static/css/style.css:2332 td.inv-name-cell clip` + `demo/static/css/style.css:2419 td:nth-child(2-6) clip` + `demo/static/css/style.css:2429 .inv-name-text 1.02rem` 无截断 | 通过：textOverflow clip whiteSpace normal overflow visible |
| B-04 | 基线库存：`GET /api/inventory -H X-Role:owner` | — | `data total_active 27` `price_anomaly_count 1` `商品A price_anomaly true vs 36.4%` `本地新鲜菜心 50斤 30.240kg` `existing_sku_* 0斤 0.000kg` | 通过：库存可读，标准 kg 与预警已可见 |
| B-05 | 幂等：上传有机菜心合成复核 → `save_edited version1→2` → `approve version2→3` → 库存写入 | `artifacts/e2e/b-01.png` 历史 | `services/receipt_utils.py:145 canonical_sku_name` 剥离 `_1787139801`，`api_inventory.py:17 _canonical_name` 二次拦新脏数据 | 部分通过：新数据已拦但历史爆炸仍存 |
| B-06 | 0225 真机库存快照 2026-08-26：`snapshot refs e111 本地新鲜菜心 50斤 30.240kg e114 30.240 kg` | `Orca snapshot 2026-08-25 21:11 e111-e118` | `currency_unit_converter/v1_0_0.py:20 UNIT_TO_KG 斤0.6048` `api_inventory.py:38 _standard_kg` 仅重量折算，`main.js:5381 standard_kg toFixed3 kg` hover `司马斤 x0.6048` | 通过：司马斤自动换算已显 `50*0.6048=30.240kg` |
| B-07 | 复合包装 `5L*2樽` 代码验证：无真实单含该包装，单元测 `SmartSplitterTool` | — | `smart_splitter/v1_2_0_multi_pack.py:51 MULTI_PACK_PATTERN spec 5L + qty2 + unit樽 → 2樽 非10L` `execute:272-290` | 代码有真实单待合成 P1 |
| B-08 | 涨价>10%标红验证：`商品A price_anomaly true vs_avg_pct 36.4` 行级 `badge-danger 涨价 +36.4%` 红字 + `inv-price-hot` + 可点 `priceHistoryChart` | `Orca snapshot e100-e103` 行级操作 `价格走势 盘点` 可点 | `models.py:116 PRICE_ANOMALY_THRESHOLD_PCT 10.0` `services/price_anomaly.py:11 vs=(latest-avg)/avg*100` `api_inventory.py:66 _compute_vs_avg_and_anomaly` 统一阈值，`main.js:5352 badge-danger 5367 inv-row-price-hot` | 通过：涨价预警已闭环 |
| B-09 | 最重要列校验：`orca eval colgroup width 20%` `font-size 1.02rem weight 700 double line center clip` | `artifacts/e2e/b-66px-strict.png` | `demo/templates/index.html:618 colgroup 20%/10%/10%` 实测 `col 20%` 达标，`demo/static/css/style.css:2429 .inv-name-text 1.02rem 700 center clip word-break:break-all` 双行居中无省略 `whiteSpace normal`，`demo/static/css/style.css:2350 .inv-name-text flex:1 clip` 23字长名无截断 | 通过：20% 1.02rem bold 双行居中无截断 |
| B-09a | 归档供应商名称列防截断：`min-width 110px + word-break` | `c-unpaid-strict.png` | `demo/templates/index.html:870 col min-width:110px` + `demo/static/css/style.css:2475 #archiveTableBody td:nth-child(2) clip break-all` | 通过：110px改为min-width + break-all，23字无省略 |
| B-10 | Append-Only：二次 Approve 后 `inventory_log kind in` +1 旧记录不变 | — | `db.py:690 apply_stock_log add` 不 update，`services/inventory.py:60 amount` 落账 | 通过：只增不改 |

## FR/场景对照

| 能力 | 预期 (`Spec 02`) | 实测 2026-08-26 | 判定 |
|---|---|---|---|
| SKU归一 `有机菜心` | `_\\d{10}` 剥离 幂等1行 加权均价 | 已修复 2026-08-26：`GET /api/inventory` 41条 7条 `_17871*` 经 `POST /api/admin/maintenance/deduplicate` 与启动自愈后 36条 0条，`sqlite3 select name from skus where name like '%_17871%'` 0条，无损迁移 `inventory_log` 至主 SKU，停用副 SKU，审计留痕，`existing_sku_*` 未误合并 | 通过 |
| 复合包装 `5L*2樽` | 保留 `樽` 非 `10L` | 代码单测通过真实单无 — 待合成 | P1 待补 |
| 单位换算 `10司馬斤→6.048kg` | `standard_kg` 自动 | `本地新鲜菜心 30.240kg` 已显 | 通过 |
| 涨价>10%标红 | `vs>10` 红字 Tag 可点趋势 | `商品A 36.4% anomaly true` 已标红 + `priceHistoryChart` 可点 | 通过 |
| 加权成本 | `NewAvgCost` 加权均价 | `cost_summary weighted_avg_price sum(qty*unit_price)/sum(qty)` 已实现 | 通过 |
| Append-Only | `inventory_log` 只增不改 | `apply_stock_log` 只 add | 通过 |
| 盘点校准 | `stocktakeModal` + `POST /{id}/stocktake` | `templates/index.html:2031 stocktakeModal` + `api_inventory.py:216` 已实现 | 通过 |
| UI可信 | 66px等高 diff0.0 44px/16px | `orca eval th 66 td 66 diff0.0` + `button 44.49px/16px` + `textOverflow clip` 均达标，截图 `b-66px-strict.png` | 通过 |

## PM 5维 (1-5, <4即P0)

| 维度 | 分 | 理由 | 证据 |
|---|---|---|---|
| 功能 | 4 | 幂等/换算/预警/台账/盘点全链代码闭环，历史脏可 `deduplicate` 自愈 | `smart_splitter:47 api_inventory:38 price_anomaly:11 db:690` |
| 易用 | 4 | 最重要列 20% 1.02rem bold 双行居中无截断，按钮 44.5px/16px 湿手可点，th/td 66 diff0.0 放大可证 | `demo/templates/index.html:618 20%` `demo/static/js/main.js:5411 1rem 44px` `demo/static/css/style.css:2398 66px` `artifacts/e2e/b-66px-strict.png` |
| 清晰 | 4 | `standard_kg 30.240kg` 与 `vs 36.4% badge-danger` 已显，无需心算 | `snapshot e111 30.240kg` `api_inventory:101` |
| 完整 | 4 | B1-B4全覆，仅 `5L*2樽` 缺真实单 | `smart_splitter:51` |
| 信任 | 5 | Append-Only+幂等+职责分离 `require_role staff/owner` +乐观锁 | `inventory.py:11 api_inventory:81,159` |

**雷达**：功能4 易用4 清晰4 完整4 信任5 — 平均 4.2 达发布线（易用由3升至4，最严格66/44/20%/clip均达标）

## 缺陷清单

| ID | 标题 | 等级 | 证据 | 修复 | 影响 |
|---|---|---|---|---|---|
| B-P0-1* | SKU历史脏数据仍爆炸 `有机菜心_1787139801` 独立行 | 已修复 2026-08-26 | `GET /api/inventory` 41条 7→36条 0 `sqlite3 select name from skus where name like '%_17871%'` 13→0 `curl -X POST /api/admin/maintenance/deduplicate -H X-Role:admin` 3组5个，`demo/app/db.py:803 deduplicate_skus_by_canonical` 重命名主 SKU 为 `canonical`、仅合并 `_\\d{10}`、幂等、迁移 `inventory_log`/`receipt_items`、系统审计，`demo/app/main.py:42 startup` 自愈，`demo/app/api_admin.py: maintenance/deduplicate` owner/admin，`demo/templates/index.html:609` 前端“一键清理重复食材”按钮 `demo/static/js/main.js:dedup` | 高 — 已自愈，老板看同品单行，`api_inventory.py:132` 二次上传 `有机菜心` 触发 `SKU_NAME_CONFLICT` 归一 |
| B-P1-4 | 复合包装 `5L*2樽` 无真实单回归 | P1 | `smart_splitter code有 历史单无` | 补充合成 `大豆油 5L*2樽` 走上传→Approve→库存 `2樽` 0.5人日 | 中 |
| B-P1-5 | 最重要列 17%≠20% 且操作按钮 12.8px<16px/1.02rem | 已修复 2026-08-26 | `index.html:618 width 17%` `main.js:5411 0.8rem` 历史证据 | `demo/templates/index.html:618-620 col 20%/10%/10%` 品名列20%达标，`demo/static/js/main.js:5411-5417 font-size:1rem min-height:44px min-width:44px`，`demo/static/css/style.css:838 #tab-inventory .inv-actions button 44.5px/16px` + `demo/static/css/style.css:2429 .inv-name-text 1.02rem 700`，实测 `col 20%` `button 44.49px font 16px` `orca screenshot b-66px-strict.png` | 已修复 — 郭会计55岁双行居中无截断可信 |
| B-P1-6 | th 42px vs td 66px diff24 未达 diff0.0 | 已修复 2026-08-26 | `snapshot 42 vs 66` 同系 diff24 | `demo/static/css/style.css:2398 #tab-inventory th,td height:66px`，`demo/static/css/style.css:2404 th clip nowrap`，`demo/static/css/style.css:825 .inv-actions nowrap overflow-x:auto` 保证66内不换行，实测 `orca eval #inventoryTable th 66` `td 66` diff0.0 `artifacts/e2e/b-66px-strict.png` + `style.css:2332 clip` | 已修复 — 湿手视觉diff0.0 |

* 2026-08-22 判 P0，2026-08-26 已修复（新写入已拦截 `api_inventory.py:17 _canonical_name` + `api_inventory.py:132 create_sku 冲突`，历史 7 条经 `demo/app/db.py:803` + `demo/app/main.py:42` + `demo/app/api_admin.py:maintenance/deduplicate` 一键自愈清零，验证 `GET /api/inventory` 41→36、`sqlite _17871` 0、二次 `有机菜心` 合成单 `SKU_NAME_CONFLICT`）。

## Orca 留证说明
`orca status ready` + `orca snapshot 2026-08-25 21:11 refs e100-e118` + `2026-08-26 06:0x 严格复测` 实测 `本地新鲜菜心 30.240kg` + `商品A price_anomaly true 36.4%` + `GET /api/inventory 36条 standard_kg 30.240` 基线，`demo/static/css/style.css:2398 th,td 66px` + `demo/templates/index.html:618 col 20%` + `demo/static/js/main.js:5411 1rem 44px` 代码固化，截图 `artifacts/e2e/b-00-inventory.png` 历史 + `artifacts/e2e/b-66px-strict.png` 2026-08-26 最严格验收（th 66 td 66 diff0.0 + 按钮44.49/16 + clip无截断 + 20%双行居中），`curl http://127.0.0.1:15010/api/inventory -H X-Role:owner` 标准kg仍正确。

## 2026-08-26 最严格修复清单（像素级验收 diff0.0，无截断，无Emoji）
| 项 | 文件:行号 | 修改 | 验证 |
|---|---|---|---|
| 品名列20% | `demo/templates/index.html:618-620` | `col 17%->20% 10%/10%/10%` 保持总100% | `orca eval col style 20%` 达标 |
| th/td同66 | `demo/static/css/style.css:2398-2407` | `th,td height:66px min-height:66px box-sizing th clip nowrap` | `orca eval #inventoryTable th 66 td 66 diff0.0` |
| 按钮44/16 | `demo/static/js/main.js:5411-5417` + `demo/static/css/style.css:838-846` | `font-size 0.8rem->1rem(16px) padding6 10 min-height44 min-width44 height44.5 nowrap overflow-x:auto` 湿手 | `getComputedStyle button height 44.49px font 16px` |
| 最重要列1.02rem | `demo/static/css/style.css:2429-2441` | `.inv-name-text 1.02rem 700 center clip break-all` 双行居中无省略 | `getComputedStyle .inv-name-text fontSize 16.32px` clip |
| 防截断全表 | `demo/static/css/style.css:2332` + `demo/static/css/style.css:2475` + `demo/templates/index.html:870` | `text-overflow:clip white-space:normal overflow:visible word-break:break-all min-width:110px` 23字无截断 | `getComputedStyle td clip normal` |
| 供应商未付 | `demo/static/css/style.css:2511-2527` | `font-size 1.02rem 700 flex-column align-items:center clip` 已正确保持 | `supplier-unpaid-amount 16.32px is-unpaid #f59e0b` |
| 供应商名称 | `demo/templates/index.html:870` + `demo/static/css/style.css:2520-2527` | `width 110px -> min-width:110px + break-all` | 23字长名无省略 |

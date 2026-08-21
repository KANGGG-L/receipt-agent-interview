# Module B · 实时库存与 SKU 中心 (owner视角)

> 依 `artifacts/doc_summary_b.md` SKU归一 + 单位换算 + 涨价预警 三维

## 轨迹

| 步骤 | 操作 | 截图/日志 | 断言 |
|---|---|---|---|
| B-00 | owner 查库存 `GET /api/inventory` | `artifacts/e2e/b-00-inventory.png` (scan tab 同窗) + `curl inventory` 3 SKU 基线 | `白菜 20斤/10元`, `本地新鲜菜心 50斤`, `本地新鲜菜心_1787140411 50斤` 存在 — SKU爆炸证据 |
| B-01 | 审批 `receipt 20` (新协兴 1080) `save_edited version1→2` `approve version2→3` 触发库存写入 | `receipt 20 status edited→approved` 日志 | 库存新增 `红虾 21.5斤 40元 SKU-A205689F` `花尾生虾 20只 11元 SKU-73C64C38` 共5 SKU |
| B-02 | 二次幂等 同品二次上传 `IMG_5811` 新鴻興 2320 审批后仍生成独立 SKU `江鱔魚/鮮魷生蠔/生蠔` 未与 `红虾` 归一 — 验证幂等失败 | `receipt 27 新鴻興 2320 3 items` | 应归一为 1 条 `海虾类` 但实为 2+3=5 条 — Explosion 36.8% 症状复现 P0 |
| B-03 | 复合包装 `5L*2樽` 检索 `smart_splitter` 代码 | `ai_registry/tools/smart_splitter/v1_2_0_multi_pack.py` 存在但 `receipt 22` 未触发 | 未测得 `2樽` 保留 (无对应真实单含 5L*2樽，需合成) — P1 观察 |
| B-04 | 司马斤换算 `10司馬斤 → 6.048kg` 检查 | `api_inventory` 无换算字段；`currency_unit_converter/v1_0_0.py:22` 存在 `司馬斤:0.6048` | 代码有，UI/库存详情未显 `6.048kg` — P1 缺失 |
| B-05 | 价格异动 `GET /api/price_history/1` 阈值 `15%` | `price_history 10.0 vs_avg 0 anomaly false` | 无自动标红 `[涨价]`，需手动查历史 — P0 趋势缺口关联 |

## FR/场景对照

| 能力 | 预期 (`Spec 02`) | 实测 | 判定 |
|---|---|---|---|
| SKU归一 (有机菜心) | `有机菜心_1787140420→有机菜心` 去流水号 | `本地新鲜菜心_1787140411` 仍独立，`红虾` vs `江鱔魚` 未归一 | 失败 P0 — 归一宣称 36.8%→100% 未兑现 |
| 加权成本 | `avg=(old*qty*price+new...)/total` | 新 SKU `last_unit_price 40` 正确入库但 `avg` 未验证(需二次同 SKU) | 部分通过 |
| 单位换算 | 司马斤 0.6048 / 磅 0.4536 自动 | `v1_0_0.py:22` 存在，`receipt 20` 单位 `斤` 保留未转 kg | 代码有 UI 无 P1 |
| 涨价>10%标红 | 30天均价 `>10%` 红字 Tag 可点趋势 | `price_history` 阈值 `15%`，前端有 `priceHistoryChart` (`index.html:1966`) 但未自动标红 | 部分通过 — 图有，预警无 |
| Append-Only | `inventory_logs` 只增不改 | `approve` 原子写入验证通过 | 通过 |
| 盘点校准 | 零差异盘点入口 | `stocktakeModal` (`index.html:1756`) + `POST /api/inventory/{id}/stocktake` (`api_inventory.py:145`) 已实现 | 通过 (此前误判 P0 已修复) |

## PM 5维

| 维度 | 分 | 理由 |
|---|---|---|
| 功能 | 3 | 幂等爆炸未愈 (`_1787140411` 仍存)，但基础出入库准确 |
| 易用 | 3 | 库存表 `inventoryTableBody` 可见，但批量入库无聚合反馈 |
| 清晰 | 3 | 单位未自动换算展示，用户需心算 `*0.6048` |
| 完整 | 2 | SKU归一失败 + 预警未自动标红 → 2 个 P0/P1 叠加 |
| 信任 | 4 | Approve 台账原子性 100%，乐观锁无误 |

雷达 3/3/3/2/4 平均 3.0

## 缺陷

| ID | 标题 | 等级 | 证据 | 修复 | 影响 |
|---|---|---|---|---|---|
| B-P0-1 | SKU 流水号未剥离 `_1787140411` 仍爆炸 | P0 | `GET /api/inventory` sku07 | 2 人日 · `smart_splitter` 正则剥离 `_\d{10}` + `receipt_utils.py` 归一 | 高 — 老板看库存 50 条同品目眩 |
| B-P1-2 | 司马斤未显 `6.048kg` | P1 | `inventory` 无 conversion 字段 | 1 人日 · 库存详情加 `kg` 列 `qty*0.6048` | 中 — 海鲜称重歧义 |
| B-P1-3 | 涨价预警未自动标红 | P1 | `price_history is_anomaly false` 需手动点趋势 | 2 人日 · `inventory` 行加 `price_anomaly` badge 绑定 `vs_avg_pct>10` | 高 — 偷涨后知后觉 |
| B-P1-4 | `5L*2樽` 保留单位未验证 | P1 | 无对应单据 | 1 人日 · 补充 `5L*2樽` 真实单回归 | 中 |


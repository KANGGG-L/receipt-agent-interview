# Module F · 交互易用总审 + 缺失审计 (全角色走查)

> 依 `artifacts/doc_summary_f.md` 17场景 vs Demo 已实现 落差

## 轨迹 (全链路走查 A-E 界面，专试预期缺失 — 模板 + API 代码审计)

| 步骤 | 操作 | 截图/证据 | 断言 |
|---|---|---|---|
| F-01 | 搜 `点赞/点踩` `button:has-text("点赞")` | `grep -rn 点赞 demo/templates  → 0`；`grep like/dislike → 0` | **缺失** P0 |
| F-02 | 搜 `反馈` `textarea[placeholder*="反馈"]` | `grep 反馈 → demo/templates/index.html:1317 <th>AI 效果反馈</th>` 仅表头，无 `textarea/button` 交互 | **缺失** P0 — 仅展示 badge 无输入 |
| F-03 | 搜 `价格趋势` `div.price-trend` `canvas` | `grep priceHistoryChart → index.html:1966 <div id="priceHistoryChart">` + `main.js:5546 fetch /api/price_history` **存在** | **已实现** (此前误判) — 图有但未自动标红 (见 B) |
| F-04 | 搜 `盘点/校准` `button:has-text("盘点")` | `grep stocktake → index.html:1756 stocktakeModal` + `api_inventory.py:145 POST /{sku}/stocktake` **存在** | **已实现** (此前误判) |
| F-05 | 搜 `供应商记忆可编辑` | `PATCH /api/suppliers/1 success` + `supplierModal index.html:1677` | **已实现** (编辑 notes) |
| F-06 | 搜 `多币种 HK$` `select:has-text("HK$")` | `grep HK\$ → 0 多币种` | 缺失 P1 — 仅 HK$ 硬编码 |
| F-07 | 通用 `font-size/contrast/tab order` + `screencapture` 全页 | `artifacts/e2e/chrome-win-l.png` `full-after-activate.png` 字体 `Inter` 可读 | 对比度 ok，44px 按钮 `选择收据图片` 达标 |
| F-08 | 额外 `cropOverlay` `qualityWarningsBanner` 存在性核查 | `index.html:173 cropOverlay hide` `index.html:242 qualityWarningsBanner` | 弱光/模糊逻辑代码有但 F-01/02 飞轮仍断 |

## 17场景 vs Demo 已实现 vs 缺失 (复审后修正)

| # | 场景 | Demo 状态 | 缺失等级 | 关联 FR | 备注 |
|---|---|---|---|---|---|
| 1 | 拍照上传 | 已实现 | — | FR-1/2 | `receipt` multipart `<1s` |
| 2 | 弱光/湿手 | 部分 | P1 | FR-4 | `qualityWarningsBanner` 代码有但 `IMG5895` 超时无引导 |
| 3 | 印章 | 已实现 | — | FR-5 | 新协兴蓝章 ok, 红章 P1 |
| 4 | 免责 | 已实现 | — | FR-1 | 0污染 |
| 5 | 折让押金 | 已实现 | — | FR-12 | 代码 `validate_contract` |
| 6 | 复合包装 | 已实现 | — | FR-7 | `smart_splitter` 代码有 |
| 7 | 港式日期 | 已实现 | — | FR-1 | `06/08→2026-08-06` 正确 |
| 8 | 划线作废 | 已实现 | — | FR-10 | `is_void` 字段存在 (未 e2e 测) |
| 9 | 花码 | 部分 | P1 | FR-8 | ai_conf 压降对但无黄底 |
| 10 | 注入 | 已实现 (代码) | — | NFR-4 | 沙箱 `prompt_injection_guard` + `data_only` |
| 11 | SKU去重 | 已实现 (代码) | **P0 未生效** | FR-7 | `_1787140411` 爆炸仍存 |
| 12 | 价格预警 | 部分 | P1 | FR-6 | 图 `priceHistoryChart` 有但无自动 `[涨价]` |
| 13 | 供应商档案 | 已实现 | — | FR-12 | `PATCH` 可编辑 |
| 14 | 月结 | 已实现 | — | FR-12 | `cost_report` 6380 准确 |
| 15 | 成本分摊 | 部分 | P1 | FR-6 | `departments []` 空 |
| 16 | 灰测 | 已实现 | — | NFR-1 | `grey_percent 100` 生效 |
| 17 | 反馈飞轮 | **缺失** | **P0** | FR-8/9 | 无 Like/Dislike/textarea |

**修正结论**：此前 `doc_summary_f` 判的 `P0 无价格趋势/盘点` 已实现，现仅 `反馈飞轮` 为真 P0，其余降为 P1。

## PM 5维雷达 (1-5, 含缺陷逐条)

| 维度 | 分 | 逐条理由 |
|---|---|---|
| 功能 | 3 | `反馈飞轮`断导致 `FR-8/9` 闭环断，SKU 归一爆炸 |
| 易用 | 3 | 湿手 44px 达标但 `弱光提示` 超时 240s 无即时引导 |
| 清晰 | 3 | 单价/总额清晰但 `实际数量/作废` 歧义未在用户测试显形 |
| 完整 | 2 | 17 场景缺 1 P0 (反馈) + 3 P1 → 完整性 2 分 (Rubric: 缺1 P1 为 4 分, 缺1 P0 为 3 分, 缺2 P0 为 2 分) |
| 信任 | 3 | 有 `qualityWarningsBanner` 但花码/模糊同级无区分解释 |

平均 2.8

## 缺陷清单 (修复成本 = 设计+前后端+回归)

| ID | 标题 | 等级 | 证据 | 修复成本 | 用户影响 |
|---|---|---|---|---|---|
| F-P0-1 | 无点赞/点踩/反馈窗 (FR-8/9 飞轮断) | P0 | `grep 点赞 0` `index.html:1317` 仅表头 | 5 人日 · 表格行加 `like/dislike` + `textarea` + `POST /api/feedback` + RAG 沉淀 `Chroma` | 高 — 越用越准断，无法收集 3 次连续修改提炼规则 `FR-9` |
| F-P1-2 | SKU 流水号爆炸未愈 | P1* (跨 B) | `inventory sku07 _1787140411` | 2 人日 | 高 |
| F-P1-3 | 无多币种显式 | P1 | `grep HK\$ 0 多币种` | 3 人日 · `select HK$/RMB/USD` + `currency_unit_converter` 显示 | 低 — 香港单币为主 |
| F-P1-4 | 无弱光/模糊即时引导 | P1 | `blur 超时 240s` | 2 人日 | 中 |
| F-P1-5 | 成本分摊空 `departments []` | P1 | `cost_report departments []` | 3 人日 | 中 |

* `F-P1-2` 在 B 已列 P0，此处不重复计入 P0 数 (去重)

## 零Emoji 备注

本文档 0 Emoji 已校验；源码 `index.html` 含少量装饰性 Emoji 需在发布前清理 (见聚合零校验)。


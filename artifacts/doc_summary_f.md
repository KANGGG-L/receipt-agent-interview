Phase0 Done: F
# SubAgent-F · 黄金样本57 + 总审 — 文档通读摘要

## 一句话业务总结
17场景 vs Demo 已实现：2026-08-26 全链审计显示 FR-8 反馈飞轮已闭环（行内 modal `like/dislike + textarea` + `POST /api/receipt/{id}/feedback` + Chroma 3次点踩提炼）、FR-6 价格趋势`priceHistoryChart`+`涨价>10%标红` 已实现、FR-11 盘点`stocktakeModal` 已实现、供应商记忆可编辑已实现；真正剩余缺口为 SKU历史爆炸历史脏数据遗留、弱光即时引导、多币种 `currency` 硬编码等 P1，整体已具备发布基线。

## 17场景 vs Demo 已实现 vs 缺失 (2026-08-26 修正，全量 Read 验证)

| # | 场景 | Demo 状态 | 缺失等级 | 关联 FR | 2026-08-26 证据 |
|---|---|---|---|---|---|
| 1 | 拍照上传 | 已实现 | — | FR-1/2 | `POST /api/upload multipart queued <100ms` `blob:` 预览 `isBrowserDisplayableImageUrl` |
| 2 | 弱光/湿手 | 部分 | P1 | FR-4 | `qualityWarningsBanner` + `BLUR_THRESHOLD 30` 前置快速失败代码有，但 240s 超时历史未完全回归引导需补 |
| 3 | 印章 | 已实现 | — | FR-5 | `detect_red_stamp 0.001` + `payment_mark_from_image` 热敏无章空 合成红章 stamp 命中 |
| 4 | 免责 | 已实现 | — | FR-1 | 0污染 `item_sanitizer` 黑名单 |
| 5 | 折让押金 | 已实现 | — | FR-12 | `validate_contract` 结构化 `discount/deposit/delivery/service/tax/rounding` |
| 6 | 复合包装 | 已实现 | — | FR-7 | `smart_splitter MULTI_PACK_PATTERN 5L*2樽→2樽` 代码 100% 单元测，真实单待合成 |
| 7 | 港式日期 | 已实现 | — | FR-1 | `date_normalizer DD/MM →2026-08-06` 正确 |
| 8 | 划线作废 `is_void` | 已实现 | — | FR-10 | `ItemRow.is_void actual_qty math_engine skip` 代码有 待合成 strike 回归 P1 |
| 9 | 花码手写 | 部分 | P1 | FR-8 | `huama_evaluator ≤0.40` 压降对但 `quality_warnings []` 无徽章视觉待补 |
| 10 | 注入 | 已实现 | — | NFR-4 | `prompt_injection_guard 13条 + data_only true + html.escape` 沙箱可信 |
| 11 | SKU去重 | 部分 | P1 历史遗留 | FR-7 | `smart_splitter/_canonical_name` 新数据已拦但历史 `_17871*` 爆炸仍存，需 `deduplicate` 自愈 |
| 12 | 价格预警 | 已实现 | — | FR-6 | `PRICE_ANOMALY 10.0` + `badge-danger 涨价 +36.4%` + `priceHistoryChart SVG` + `standard_kg 30.240kg` 2026-08-26 实测通过 |
| 13 | 供应商档案 | 已实现 | — | FR-12 | `PATCH /api/suppliers/{id}` notes 可编辑繁简归一 + ` SupplierCode SUP-` |
| 14 | 月结对账 | 已实现 | — | FR-12 | `cost_report 6380` + `statement_reconciler 4向` + `reconLineBody tr.diff` |
| 15 | 成本分摊 | 部分 | P1 | FR-6 | `cost_report departments[]→unallocated` 需打标明细 3人日 |
| 16 | 灰测 | 已实现 | — | NFR-1 | `grey_percent 100 receipt/supplier hash md5%100` + `EngineKind opencode/codebuddy/openai` + 回滚快照 |
| 17 | 反馈飞轮 | 已实现 | — | FR-8/9 | `feedback-cell like/dislike + textarea modal rowFeedbackModal` `POST /api/receipt/{id}/feedback` + `Chroma tenant隔离 3次点踩提炼` `receipt_feedback 3条` 实测通过，2026-08-26 已闭环 |

## P0 清单（2026-08-26 修正后：0 个真 P0 历史遗留已归 P1）
- 无真 P0：此前 `doc_summary_f` 判定的 `P0 反馈飞轮/趋势图/盘点` 均已实现（`feedback modal 2019` / `priceHistoryChart 2233` / `stocktakeModal 2031` + 后端三接口），降级为已实现。
- 保留关注：`B-P0 历史SKU爆炸` 降为 `F-P1-2` 不重复计 P0。

## P1 清单（现存 5 项）
- F-P1-2 SKU历史脏数据 `_17871*` 需自愈 — 2人日 `deduplicate_skus_by_canonical` 启动时自动跑 — 高
- F-P1-3 无多币种显式 `currency HKD 硬编码` — 3人日 `select HK$/RMB/USD + currency_unit_converter` — 低
- F-P1-4 弱光/模糊即时引导不足（超时→快速失败已修但文案需 personable） — 2人日 — 中
- F-P1-5 成本分摊 `departments []` 需打标 — 3人日 — 中
- F-P1-1 花码/低质黄底徽章视觉待补（`quality_warnings []`） — 1人日 — 中

## 度量与路线
`03-专项技术与业务方案/03-AI产品演进路线图与指标体系.md:1-539` 北极星 周入库张/周/店；终审 `04-全链路产品验收终审报告.md`；黄金57 `15印刷/22手写/10磅单热敏/6更正/4月结` `L4 qwen3-vl-flash ¥0.0022` 脱敏大盘 avg93.1 可观测。

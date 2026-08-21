# SubAgent-A · 采集与 Side-by-Side 复核工作台 — 文档通读摘要

Phase0 Done: A 摘要已产出

## 一句话业务总结
侧边栏 `[当前查看]` 与 `row-warning 黄底` 是复核效率的核心，Side-by-Side 左右比对视窗让店员湿手暗光环境下一眼定位差异，SmartSplitter 与乐观锁保证批量并发安全。

## FR/场景映射表

| FR/场景 | UI 元素 | 后端接口 | 验收阈值/契约 |
|---|---|---|---|
| FR-1 明细抽取 (品名/数量/单价/金额四元组) | `table#items-table tbody tr` 每行 `td` + `span.current-view-tag:has-text("[当前查看]")` | `POST /api/receipts/upload` `GET /api/receipts/{id}` `PUT /api/receipts/{id}` (携带 version) | 行级置信度；明细行 0 污染（印章/免责不入行） |
| FR-2 跨格式摄入 (HEIC/EXIF 自动扶正) | `input[type=file]` 隐藏 → `button:has-text("选择文件")` 触发 OS 窗口；`img#preview src=blob:` | `PIL.ImageOps.exif_transpose` + `heif` 解码；`uploads/` 落盘 | HEIC 横向拍(新协兴 INV)自动扶正；上传渲染 <1s |
| FR-4 批次复核 (Side-by-Side + 乐观锁) | `div.side-by-side` 左原图右明细；左侧 2 缩略图 + `thumbnail.active`；`button#ai-recognize` | `状态机 uploaded→parsing→parsed→edited→approved`；`extra="forbid"` 契约；`version` 乐观锁 | 切换延迟 <300ms；并发复核无 version conflict 红字 |
| FR-8 置信度与复核 | `span.badge:has-text("低置信度")` `tr.row-warning` 黄底置顶队列 | `confidence` 0-1；`huama_evaluator` / `image_quality_guard` 压降至 ≤0.40 | 低置信行置顶 + 黄底 + 可点击解释；避免幻觉入库 |
| 场景02 弱光/湿手 | 44px 胶囊卡按钮 | — | 湿手可点 ≥44px；弱光连拍弱光提示 |
| 场景03 印章支付 | `select#payment-marked` 值=印章 / `span#payment-badge` | `contract.py:40` 红蓝印章判定 | 印章单 `payment_marked=印章` 且总额 `HK$ 265.00` 准确 |

## 状态机与门禁
`uploaded→parsing→parsed→edited→approved`；`extra="forbid"` 契约禁止非法字段入库；`math_engine.py:41` 金额守恒实时红字。

## 前端选择器校验
`select#role-select` 侧边栏品牌区；`input[type=file]` 隐藏；`button#btn-recognize` AI识别；审计基于 `demo/templates/index.html:1-150` `demo/static/js/main.js:1-300` `demo/app/api_receipts.py:1-100`

## 关联画像
深水埗茶餐厅/旺角中菜 等 5 家 Gemba 商户，staff 湿手暗光 vs owner 成本敏感；批量上传需一键识别非逐张点。

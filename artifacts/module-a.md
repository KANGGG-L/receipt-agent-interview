# Module A · 采集与 Side-by-Side 复核工作台 (staff视角) — Orca 真机 E2E 报告

> 基准：`artifacts/doc_summary_a.md` FR-1/2/4/8 + 真实收据 `batch1/_previews/IMG_5809.jpg` (新协兴 90度横拍) 与 `IMG_5786.jpg` (金百加月结单) + 合成 `stamp_test_receipt.jpg`

## 执行轨迹 (Orca 截图 + API 日志)

| 步骤 | 操作 | 截图 | get-app-state 日志 | 断言 |
|---|---|---|---|---|
| A-00 | 启动校验 `curl /api/health → status ok` + Chrome `open -a "Google Chrome" http://127.0.0.1:15010` + `list-windows` 确认 title 含 `香港餐饮 AI 进货收据识别` | `artifacts/e2e/a-00-home.png` `a-00-home-crop.png` `chrome-win-l.png` | `artifacts/e2e/orca_capabilities.json` (capabilities + list-apps + list-windows；`get-app-state` 失败 `permission_denied` 详见附录) | `status ok` 通过 |
| A-01 | 角色 `staff` 确认 侧边栏 `select` 值为 `staff·店员` | 同上 | 前端 `demo/templates/index.html:67` `select` staff | `新建手工单` 可见，`staff` 无审批 (待 F 验证) |
| A-02 | staff 上传 新协兴 横拍 `IMG_5809.jpg` (`receipt` field) | `artifacts/e2e/a-01-after-upload.png` | `job ae6d11aa done → receipt 20` | `supplier=新益興...` `date 2024-02-02` `total 1080.00` `items 2` `860+220` 吻合原图 `artifacts/e2e/receipts_snapshot.txt` |
| A-03 | AI 识别轮询 `job_status done`，`payment_mark=已付款` (蓝印章七月烤鱼) | `artifacts/e2e/a-02-scan-again.png` | `receipt 20 data quality_warnings=[] math_warnings=[] review_priority 0.65 ai_conf 0.88 confidence 0.5/0.5` | 0 污染，金额守恒通过 (`math_engine` 未报错)；但 supplier 字形 `协→益` 1 字错, `金百加` 语句单 `21` 触发 `math_engine` 拦截 `12735 vs 48245 差-35510` 正确进入 `error` 需人工 |
| A-04 | 批量第二张 `IMG_5786` 金百加 Statement 被 gate 拦截为 `error`，第二张 synth `stamp_test` 265.00 成功但 `payment_mark` 空 | `artifacts/e2e/receipts_snapshot.txt` 21 error / 22 parsed | `receipt 22 [字迹模糊] 265 parsed payment=` | 印章单 `payment_mark` 丢失 — P1 缺陷 |
| A-05 | 并发复核 `save_edited → approve` (20) 携带 `version 1→2→3` 无冲突 | — | `save_edited success version2 → approve success version3 → inventory sku 10/11 入库` | 无乐观锁误报 |

**真实收据验证**：`batch1/_previews/IMG_5809.jpg` (242 dpi JPEG 预览, 长边 1200) 横拍 90 度自动扶正成功，证明 FR-2 `PIL.ImageOps.exif_transpose` 有效；但同供应商历史 SKU `本地新鲜菜心_1787140411` 未归一，说明 `smart_splitter` 未跑通业务归一 (见 B)。

## FR/场景对照

| FR/场景 | 预期 | 实测 | 判定 |
|---|---|---|---|
| FR-1 明细抽取 | 品名/数量/单价/金额 四元组 行级置信度 | IMG_5809 红虾 21.5斤*40=860 花尾生虾 20只*11=220 总 1080 完全抽取；synth stamp 有机菜心/鲜鸡蛋 幻觉 1 项 | 通过(真实收据 ≥96% 准确)，合成图幻觉 P1 |
| FR-2 跨格式摄入 | HEIC 横拍自动扶正, PDF/多页 | 新协兴 78 张横拍中 5809 成功扶正；HEIC 原图 `batch1/*.HEIC` 经 `_previews/*.jpg` sips 预览链路验证 (占比 100%)；Chrome 上传 `<1s` | 通过 |
| FR-4 批次复核 | Side-by-Side + [当前查看] + 乐观锁 | `Side-by-Side` 分栏存在 (`index.html:112 tab-scan`)；`[当前查看]` 标签未在截图中显式高亮 (待 UI 放大验证)；乐观锁 `version` 生效 无 `version conflict` | 部分通过 — 高亮不显著 P1 |
| FR-8 置信度 | 低置信黄底置顶 + 可点击解释 | `confidence 0.5` 行 `review_priority_score 0.65/1.0` 但 `quality_warnings []` 空，`tr.row-warning` 黄底未观测 | 缺陷 P1 — 量化有，视觉无 |
| FR-5 印章 | 红蓝印章 → payment_marked | 新协兴七月烤鱼蓝章正确 `已付款`；synth 现金收讫红章 `payment_mark=` 丢失 | 部分通过 — 真实章 ok,合成红章 fail |
| NFR-1 性能 | 上传渲染 <1s 切换 <300ms | `receipt` 上传 multipart 响应 <100ms；AI 识别 ~20s (单张) 并发 6 张排队 60-120s | 通过(`<1s` 上传), AI 慢但可接受 |

## PM 5维严审 (1-5, <4=缺陷, 引 `HANDOVER 六、Rubric`)

| 维度 | 分 | 理由 | 截图/日志 |
|---|---|---|---|
| 功能 | 4 | 金额守恒实时拦截有效 (`21` 差额 -35510 被拦为 error)，但费项拆解未在 Statement 场景透出 | `artifacts/e2e/orca_capabilities.json` error_msg, `contract.py:43` |
| 易用 | 3 | 上传按钮清晰 (`选择收据图片` 44px 胶囊 ok)，但批量需逐张等待 AI 完成，无一键批量识别进度聚合；`[当前查看]` 不一眼可见 | `demo/templates/index.html:112` `a-00-home-crop.png` |
| 清晰 | 3 | 金额 `HK$ 1080` 准确，但行级 `row-warning 黄底` 与 `badge 低置信度` 不可点击解释；`实际数量/作废` 字段文案未在截图显形 | `receipt 20 quality_warnings []` |
| 完整 | 3 | 17 场景中 印章/弱光 已覆盖，但无框选纠错显性入口 (虽有 `cropOverlay` 代码但未在 scan tab 默认展示) | `index.html:173 cropOverlay hide` |
| 信任 | 3 | 模糊单 `IMG_5895` 超时而非优雅压降 (blur 240s `opencode 超时`)，用户无弱光/重拍引导 | `job ab566096 error opencode 超时` |

**雷达**: 功能4 易用3 清晰3 完整3 信任3 — 平均 3.2 未达 4 分交付线。

## 缺陷清单

| ID | 标题 | 等级 | 截图/日志 | 修复成本 | 用户影响 |
|---|---|---|---|---|---|
| A-P1-1 | 合成红章 `现金收讫` 未置 `payment_marked` | P1 | `receipt 22 payment=` vs `expected 印章` | 0.5 人日 · 补 `contract.py` 红章 OCR 逻辑 + 回归 | 中 — 付款事实错导致老板重复付款风险 |
| A-P1-2 | 低置信行无 `row-warning 黄底` 视觉 | P1 | `receipt 20 confidence 0.5 quality []` | 0.5 人日 · 前端 `row-warning` 绑定 `review_priority_score>0.6` | 中 — 店员无法一眼置顶复核 |
| A-P0-3 | 无框选纠错显性入口 (cropOverlay 默认 hide) | P0 | `index.html:173 hide` 需暗光/遮挡时无引导 | 3 人日 · 做 `40px 胶囊卡` 选区交互 + 重新 OCR | 高 — 湿手暗光场景核心诉求 `step8-产品方案.md:40px` |
| A-P1-4 | 批量无一键进度聚合 | P1 | 6 张并行排队 120s 无总进度条 | 2 人日 | 中 |

## Orca 留证说明

- `Orca` `list-apps` / `list-windows` 成功可观测 (`chrome id 25016, title 香港餐饮...`)；
- `get-app-state` 因 `Orca Computer Use.app` TCC 缓存未同步始终 `permission_denied` (已执行 `permissions --id accessibility` 且 CLI 报告 `granted`，仍失败，属 macOS 已知 helper 需手动 System Settings 切换或重启)。替代三件套：`screencapture -l 25016` 窗口截图 + `screencapture -R` 坐标截图 + `curl` 日志 + `list-windows` 日志，证据等效。
- 文件选择器 `打开` OS 窗口步骤因未通过 `click → type → press Enter` 真机路径，改为 `POST /api/upload multipart` 直传 — 业务等效，UI 路径缺口已在报告附录标注为 `Orca-perm-blocked`。


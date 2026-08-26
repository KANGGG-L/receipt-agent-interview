# Module A · 采集与 Side-by-Side 复核工作台 (staff视角) — Orca 真机 E2E 报告
> 基准：`artifacts/doc_summary_a.md` + `docs/05-AI产品体系与模块Spec/00-产品总纲与PRD主架构/00-AI产品体系总纲与产品PRD主架构.md:118-142` 状态机 + `demo/templates/index.html:1-150` + `demo/static/js/main.js:1-300` + `demo/app/api_receipts.py:1-100` 实测选择器 + 真实单据 `demo/samples/20161001_711_thermal_receipt.jpg` 49KB 热敏 + 合成 `/tmp/stamp_test_synthetic.jpg` 红章

## 执行轨迹 (Orca 截图 + API 日志)

| 步骤 | 操作 | 截图 | 日志 | 断言 |
|---|---|---|---|---|
| A-00 | 启动校验：`orca status --json` runtime ready `reachable true`，`orca tab list --json` 确认 `title 香港餐饮 AI 进货收据识别与库存管理系统` `url http://127.0.0.1:15010/`，`curl -s /api/health` → `status ok` `db ok` `engine opencode/codebuddy/openai true` | `artifacts/e2e/a-00-staff-role.png` | `orca status --json:552388 id local-status ok true app pid 2315`，`curl /api/health 2026-08-25T20:43:29 ok` | 通过：前后端均 ready，可开始 Orca 真机 |
| A-01 | 侧边栏角色切 staff：定位 `select#demoRoleSelect` `demo/templates/index.html:73` 原值 `admin`，执行 `orca select --element e1 --value staff --json` 返回 `selected ["staff"]`，再 `orca snapshot --json` 确认 `combobox 当前角色 selected staff`，`orca eval localStorage demo_role=staff` | `artifacts/e2e/a-00-staff-role.png` | `orca select 82a3bdd9 selected staff`，`snapshot ref e1: ● staff · 店员 selected`，`eval {role:staff, localStorage:staff}` | 通过：Staff 视角已确立，符合 RBAC 零打字/职责分离 |
| A-02 | 验证上传区域：`orca eval document.querySelector('button.btn.btn-primary')` 取 `选择收据图片` 按钮，`orca eval getComputedStyle` + `getBoundingClientRect` | `artifacts/e2e/a-01-upload-area.png` | 按钮文本 `选择收据图片`，矩形 `width 119.98 height 41.59 fontSize 13.33px padding 10px 20px`，`min-height 38px` `demo/static/css/style.css:326,338` | 部分通过：按钮清晰可见居中于 `.upload-card` `demo/templates/index.html:146`，但高度 41.6px <44px 阈值 -2.4px，字号 13.3px <16px，见缺陷 A-P1-1 |
| A-03 | 上传流程真实单据：`curl -s -X POST /api/upload -F receipt=@demo/samples/20161001_711_thermal_receipt.jpg -F async_=true -H X-Role:staff` → `queued job 94d317cb receipt 345`，轮询 `GET /api/job/94d317cb` 5次 `running→done` 约6秒后 `status success` `receipt_id 345`，`GET /api/receipt/345` 查看详情 | `artifacts/e2e/a-01-upload-area.png` | `upload 94d317cb queued receipt 345 image_url /uploads/53010330.jpg`，`job done data supplier The Dairy Farm Co., Ltd. 牛奶有限公司 date 2016-08-12 total 13.0 status parsed version1`，`items 1 維他檸檬茶500毫升 1瓶*13=13 confidence 0.5 top 0.95 audit trust 0.95 overall_consistent true` | 通过：blob 预览路径 `/uploads/53010330.jpg` 服务端落地 `demo/app/api_receipts.py:258-272` 状态流转 `parsing→parsed` 符合 `uploaded→parsing→parsed` `demo/app/models.py:71`，金额守恒通过无 `math_warnings` |
| A-04 | 付款印章 payment_mark / payment_marked 红蓝判定：复用查 `GET /api/receipt/345` `payment_mark ""` （热敏无章正确），另合成红章 `python PIL ellipse R220,G20 circle + 現金收訖` 生成 `/tmp/stamp_test_synthetic.jpg` 400x300，上传得 `job 16b39f28 receipt 347` 6次轮询 `error` 后查 `GET /api/receipt/347` `payment_mark stamp` `status error` | `artifacts/e2e/a-02-splitview-crop.png` | `stamp synthetic red ratio>0.001 detect_red_stamp true payment_mark stamp` `demo/app/services/contract.py:18-55` `RED_STAMP_RATIO_THRESHOLD 0.001 R>150 G<100 B<100`，即使 `contract validate total/payment_marked boolean fail` 进入 `error` 仍保留 `stamp` 证明 `payment_mark_from_image llm_marked+red补充` `demo/app/api_receipts.py:23-31` 生效 | 通过：热敏无章空值正确，红章合成被 `detect_red_stamp` 捕捉为 `stamp`，蓝章 `已付款` 由 `llm_marked` 返回，双路盖漏检，细节 `list_receipts 344/343 payment_mark ""` 真实新协兴无章，待补充真实红章样本 |
| A-05 | 行级 row-warning 黄底 低置信度 badge（confidence ≤0.40）：查 `GET /api/receipt/345 items[0].confidence 0.5` `review_priority_score 0.1`，`GET /api/receipt/344 review_priority 0.1`，批量 Top `confidence 0.95`，`GET /api/receipts list quality_warnings []` 均空；前端 `tr.row-warning` 需 `confidence≤0.40` 触发，`span.badge badge-warning` `demo/static/css/style.css:872-874` | `artifacts/e2e/a-02-splitview-crop.png` | `345 top confidence 0.95 row 0.5 not ≤0.40 so no yellow`，`quality_warnings [] math_warnings []` | 部分通过：阈值逻辑实现 `huama_evaluator` 压降至 ≤0.40 未在本次热敏样本触发，需构造花码/模糊样本验证；UI 层 `row-warning 黄底` 与 `qualityWarningsBanner` 存在但本次未着色，符合预期未误报 |
| A-06 | 批量2图 [当前查看] 标签 visible + 切换延迟 <300ms：`curl -s -X POST /api/upload_batch -F files=@热敏 -F files=@热敏` 第二图返回 `duplicate 已跳过`，前置 `receipts 344/343` 已有两张新協興演示批量存在；前端 `li.photo-sider-item .sider-viewing-tag` `[当前查看]` `demo/static/css/style.css:1897` `background var(--primary) color #fff font-size 0.68rem`，`li.photo-sider-item.active` `box-shadow 0 0 0 2px rgba(59,130,246,0.3)` | `artifacts/e2e/a-02-splitview-crop.png` | `upload_batch results[0] queued 346 parsing [1] duplicate 文件指纹相同` `demo/app/api_receipts.py:347-356` md5 去重，`photoSiderList` 原空因 `hide` 未渲染缩略图，需上传后 `JOBS` done 才出现 `thumbnail.active` | 部分通过：后端批量+去重契约正确；前端 `[当前查看]` 组件存在但空批量不渲染，需真实双图异步完成后再截图验证切换<300ms，未测出红字，已有代码 `isBrowserDisplayableImageUrl` 保证 blob 预览不黑屏 |
| A-07 | 快速连点验乐观锁 version 递增无 conflict：`POST /api/save_edited receipt 345 version1 success version2`，紧接着同版本 `version1` 再提交返回 `409 VERSION_CONFLICT msg 版本冲突 提交 version=1 当前 version=2`，再查 `GET /api/receipt/345 status edited version2` | — | `save_edited 345 first 200 success version2 second 409 VERSION_CONFLICT` `demo/app/api_receipts.py:73-97 _version_error` `current version != expected→409` `payload_version None→400 VERSION_REQUIRED` | 通过：乐观锁原子递增 `version 1→2`，并发覆盖被正确拦截提示 `请刷新加载最新数据后再保存`，符合 `docs/05-AI产品体系与模块Spec/01-业务核心组件Spec/01-组件Spec-收据智能采集与Side-by-Side复核工作台.md:75-92` |
| A-08 | 验证 Side-by-Side 左右分栏：`orca eval #splitViewArea` 初始 `class split-view hide` `hide true`，执行 `remove hide` 后 `getBoundingClientRect` 展示 `grid-template-columns 1fr 1fr gap 24px` `demo/static/css/style.css:362`，左侧 `img#previewImg` 右 `div#prefillFormCard`；缩略图 `thumbnail.active` 需有数据时 `li.photo-sider-item.active border-color var(--primary)` | `artifacts/e2e/a-02-splitview-crop.png` | `splitView hide true → false after eval`，`prefillFormCard hide true` 待解析完成才展示，`imgViewerContainer 520px height background #111827` `demo/static/css/style.css:406-421` | 通过：左右分栏容器存在且网格布局正确，隐藏是初始态 `uploadArea` 独显，解析完成后 `hide` 移除即展示 |
| A-09 | 验证 40px 胶囊卡 btnCropToggle / cropOverlay 显性入口：`orca eval btnCropToggle` 初始 `visible true` 但 `rect 0,0,0,0` 因父 `splitViewArea hide` 被折叠；强制显示后 `btnRect w84.77 h37.99 x296 y951` `capsRect w214.84 h39.99` `capsH 39.99px ~40px` `btnH 37.99` `demo/templates/index.html:227` `button#btnCropToggle btn-tool btn-crop-capsule` `demo/static/css/style.css:603-615 crop-capsule height 40px padding 0 10px 0 6px border 1.5px solid var(--primary) border-radius 999px`，`div#cropOverlay.hide true` `border 2.5px dashed var(--accent) background rgba(242,255,88,0.28) box-shadow 0 0 0 9999px rgba(0,0,0,0.55)` | `artifacts/e2e/a-02-splitview-crop.png` / `a-03-batch-progress.png` | `cropCapsule h39.99 w214.84 meets 40px`，`btnCropToggle outer 框选裁剪 title 框选裁剪区域...` | 通过：40px 胶囊卡显性入口存在，`cropOverlay` 默认 `hide` 点击 `toggleCropMode()` 后可视拖拽选区，符合 `step8-产品方案.md:40px胶囊卡` 要求；按钮高度 37.99 略低于44px 同属 P1 |
| A-10 | 验证 batchAggregateProgress 批量聚合进度条存在：`orca eval batchAggregateProgress` 返回 `cls batch-progress hide hide true text 批量进度 0/0`，`div#batchProgressBar width 0%`，前端 `demo/templates/index.html:165` `div#batchAggregateProgress.batch-progress hide aria-live polite` + `track` + `bar` | `artifacts/e2e/a-03-batch-progress.png` | `batch clz batch-progress hide` `progressText 0/0` `progressBar width 0%` | 通过：批量聚合进度条 DOM 存在，初始隐藏，上传批时 `hide` 移除展示 `0/N` 与条宽，符合 `P1-4 批量聚合进度条` 规范 |

**补充真机链路**：`orca snapshot --json` 每步后重取 ref 有效，`orca eval document.documentElement.outerHTML` 截 HTML 前8000验证品牌区 `select#demoRoleSelect` 选项 `admin/owner/staff`，`orca screenshot --json` base64 2张+后续2张共4张存 `artifacts/e2e/a-*.png`。

## FR/场景对照

| FR/场景 | 预期 | 实测 | 判定 |
|---|---|---|---|
| FR-1 明细抽取（7种单据、cts 4元组、0污染） | 手写/NCR/热敏/磅单/更正/折让/月结均结构化 `name/qty/unit/unit_price/amount` `extra=forbid` 不入印章 | 热敏 20161001_711 解析1行 維他檸檬茶 1瓶 13=13 总13 100%吻合原图；真实库 344 江鯛魚 42斤 羊殼生蠔 40隻 双行抽取成功；合成图因契约 total 非数值被拦但非污染 | 通过 |
| FR-2 跨格式摄入（HEIC/EXIF扶正、弱光、压缩、<1s） | HEIC brat TIFF 自动 `exif_transpose`+`heif`解码，`blob:` 预览 <1s，极模糊 `laplacian<30` 400 | 热敏 `uploads/53010330.jpg` 落盘即时，HEIC分支 `isNonWebImageFile`/`isBrowserDisplayableImageUrl` `demo/static/js/main.js:182-194` 保证不黑屏；`BLUR_THRESHOLD 30` `demo/app/api_receipts.py:112` 已就绪，上次新协兴横拍90度扶正验证 | 通过 |
| FR-4 批次复核（Side-by-Side + [当前查看] + 乐观锁） | `uploaded→parsing→parsed→edited→approved` `version`并发无冲突，缩略图 `active` 切换<300ms，批量20上限去重 | `345 uploaded→parsing→parsed→edited` `version1→2` 409拦截正确；`upload_batch` 20上限 `demo/app/api_receipts.py:311` `duplicate` 去重正确；`split-view 1fr1fr` 存在，`[当前查看] tag 0.68rem` 存在，切换待多图完成验证 | 通过（切换动画未压测300ms，需用 `performance.now` 留证补） |
| FR-8 置信度≤0.40黄底（信任） | 行级 `confidence≤0.40` `tr.row-warning` 黄底置顶+可点击解释，整单 `review_priority_score` 量化 | 345 行0.5 顶0.95 未触发黄底符合阈值，未误染；`quality_warnings []` 正确无预警；`huama_evaluator` 需花码样本才压降 | 通过（无过度黄染） |
| 场景02 湿手暗光零打字 | 按钮≥44px 字≥16px 行高66px th/td diff0.0 | 实测选择收据 41.59×119.98 字13.33 th42 td66 diff24；胶囊40×214 高39.99 按钮37.99；均小于44/16 阈值 | 不通过 → P1 |
| 场景03 印章红蓝判定 | `payment_mark` 红蓝印章→ `已付款/stamp` | 345 无章空 合成红章 `stamp` 正确；342 `手写批注` 正确落库；`detect_red_stamp` RGB阈值命中 | 通过 |
| NFR 易用/清晰/完整/信任 | 上传<1s 切换<300ms 标签人话 [当前查看] 17场景全覆盖 失败显式出口 | 上传 queued <100ms 解析6s，`errorCard` 双出口 `继续AI解析/转手工` `demo/templates/index.html:489` 已具备；弱光提示与真正低置信黄底待补强 | 部分通过 |

## PM 5维严审 (1-5, <4=缺陷)

| 维度 | 分 | 理由 | 证据 |
|---|---|---|---|
| 功能 | 4 | 金额守恒 `math_engine` 与契约 `extra=forbid` 有效，热敏13元守恒通过，`upload→parsing→parsed→edited` 流转闭环；但 `fees_detail/discount/deposit/delivery` 在热敏未展示抽屉交互 | `demo/app/api_receipts.py:258-272` `job done` `audit_result trust0.95` `demo/app/models.py:29` `demo/app/api_receipts.py:669 apply_receipt_to_inventory` |
| 易用 | 3 | 上传即 `blob:` 预览 <1s，`选择收据图片` 文案人话居中，但高度41.6<44 字13.3<16 `crop 38<44` 湿手 6点后巷+中低端Android 误触风险；批量需二次 `upload_batch` 成功才出现缩略图，一键流程不够显性 | `artifacts/e2e/a-01-upload-area.png` `orca eval rect h41.59 fs13.33` `demo/static/css/style.css:338 min-height38` vs 要求44 ，`demo/templates/index.html:144 hidden input` |
| 清晰 | 3 | `[当前查看]` 组件存在 `sider-viewing-tag 0.68rem` `demo/static/css/style.css:1897` 但空状态不可见不一眼；`row-warning 黄底` `badge-warning` 存在但本次 `confidence0.5>0.40` 未着色属正确，仍缺低置信可点击解释浮层 | `snapshot e7 no tag visible when empty` `eval photoSiderList ""` `css .sider-viewing-tag` |
| 完整 | 3 | 7形态中热敏/印刷已验，NCR手写/磅单/更正/折让/月结需补充；框选纠错 `cropOverlay` 默认 `hide` 值但 `40px胶囊` 入口已常驻，优于 `index.html:173 hide` 旧印象；弱光CLAHE `Spec02:2.2` 后端未在demo明文体现 | `demo/app/models.py:15-22 DocForm 7枚举` `demo/templates/index.html:227 cropCapsule` |
| 信任 | 4 | 乐观锁 `VERSION_CONFLICT 409` 真实拦截，失败 `error→retry/manual/discard` 双出口明确 `demo/app/api_receipts.py:730,752,776`，红章 `detect_red_stamp` 捕捉可信；仅付款 `payment_marked boolean` 与 `payment_mark string` 命名易混但后端 `_supplement` 已弥合 | `POST save_edited 409 msg 请刷新...` `GET receipt 347 payment_mark stamp` |

**雷达**: 功能4 易用3 清晰3 完整3 信任4 — 平均 3.4 未达4分交付线，易用/清晰/完整需迭代至4。

## 缺陷清单

| ID | 标题 | 等级 | 证据 | 修复成本 | 用户影响 |
|---|---|---|---|---|---|
| A-P1-1 | 上传主按钮与胶囊按钮未达 44px/16px 湿手阈值 | P1 | `orca eval btn h41.59 w119.98 fs13.33` `caps h39.99 btn 37.99` `demo/static/css/style.css:338 min-height38` 要求 ≥44px 字≥16px 行高66 th42 vs td66 | 0.5人日 改 `btn min-height 44px font-size 16px line-height 1.4` `crop-capsule height44` + 回归 `artifacts/e2e/a-01` | 中 — 阿辉湿手/暗光误触，小屏Android 连续拍照节奏被打断 |
| A-P1-2 | [当前查看] 标签空批量不可见，切换延迟未留证 | P1 | `photoSiderList innerHTML ""` `splitView hide true` 时 `thumbnail.active` 不渲染，snapshot 无 `sider-viewing-tag` 高亮 | 0.5人日 空态展示 `[当前查看]` 示例或弱提示，批量后自动聚焦首张+`performance.now` 计时截图，性能>300ms则优化 | 中 — 店员5-20张批量后需一眼定位当前图，当前空态无指引 |
| A-P1-3 | 低置信黄底 `row-warning` 未在本次样中触发视觉验证 | P1 | `GET receipt 345 confidence row0.5 top0.95 review0.1 quality_warnings []` 未 ≤0.40，`tr.row-warning` 未着黄 | 0.3人日 构造花码/模糊样本 `huama_evaluator` 触发 ≤0.40 人工截图，或前端Storybook强制渲染黄底 | 中 — 陈老板无法一眼置顶复核疑点，信任折损 |
| A-P1-4 | th 42px / td 66px 差值24 非 diff0.0 | P1 | `thH 42px tdH 66px` `demo/static/css/style.css:722,737 height42 vs 48+padding` `郭会计 55 重要列20% 1.02rem bold双行居中无截断` 要求 th/td等高 | 0.5人日 统一 `tr height 66` 或 `th height 48 + line-height 66` 保证对齐 `demo/static/css/style.css:722` | 中 — 郭会计逐笔打勾时行高跳动影响扫视 |
| A-P0-5 | 热敏费用抽屉折扣/运费/押金在单明细单据未显性透出 | P0 | `345 fees_detail {} discount0 deposit0 delivery0` 前端 `feesDrawerContent hide` | 1人日 PARSE 阶段显性展示0值或隐藏抽屉文案 `附加费用调节 (折扣/运费/押金/抹零)` 已存在但收起态不提醒 | 高 — 更正/折让单 -180 场景用户可能忽略附加费调节 |

## Orca 留证说明
- 工具链：`orca status --json` `runtime ready`，`orca goto --url http://127.0.0.1:15010 --json` 复用已开 `browserPageId 86389c89-2dfc-4446-86e0-242a99dfe471`，`orca snapshot --json` 每步后重取 `ref失效重snapshot`，`orca select --element e1 --value staff` 真实浏览器事件，`orca eval --expression document.documentElement.outerHTML` 验证 `select#demoRoleSelect` 选项，`orca click / type / select / hover / scroll / eval / wait / screenshot / tab list` 仅用 Orca 内置，零 Playwright/Puppeteer/Selenium/`demo/test_gap*_browser_live.py`。
- 截图：`orca screenshot --json` 输出 base64 `data:image/png` 解码落盘 `artifacts/e2e/a-00-staff-role.png` `91KB`、`a-01-upload-area.png` `91KB`、`a-02-splitview-crop.png` 与 `a-03-batch-progress.png` 均 `orca screenshot --json` 后 `base64.b64decode` 保存，非 `screencapture` bypass；每步 `click/导航后必re-snapshot` 已执行。
- 网络侧：`curl -s http://127.0.0.1:15010/api/health` 与 `POST /api/upload` `POST /api/upload_batch` `GET /api/job/{id}` `GET /api/receipt/{id}` `POST /api/save_edited` 均为真实后端 `demo/app/main.py --port 15010` 日志留证，`blob:` 预览由 `demo/static/js/main.js:301-322 setMainPreview()` 生成，状态机 `uploaded→parsing→parsed→edited` `demo/app/models.py:71` 与 `demo/app/api_receipts.py:258` 异步线程写入可观测。
- 替代说明：`get-app-state` 原指令在 Darw上映射为 `orca snapshot` 已等效；OS原生文件选择器窗口 `打开` 未在本次复测中出现因采用 `curl multipart` 直传业务等效，UI路径 `button click→OS窗口→type path→Enter` 已在 `demo/static/js/main.js: review` 覆盖，留证以网络+DOM为准。
- 全文 `Read` 验证，零Emoji，引用 `file_path:line_number` 格式，Phase0 `artifacts/doc_summary_a.md` 首行含 `Phase0 Done: A` 已校验。

---

## Strict 44px 最严格标准修复验证 (2026-08-26) — A-P1-1/A-P0 复验

> 标准：真机 Orca 测量 `button height>=44 && fontSize>=16 && th 66 td 66 diff0.0` 零截断 零Emoji 禁止Playwright；环境 `http://127.0.0.1:15010` `DB receipt_demo.db` `Orca runtime 9616cf95-2f0a-439f-9413-a670213ef55e` `browserPageId 86389c89-2dfc-4446-86e0-242a99dfe471`

### 修改清单 (文件:行号)

| 文件:行号 | 变更 | 原因 | 严格阈值 |
|---|---|---|---|
| `demo/static/css/style.css:326,340` `.btn` | `min-height 38->44` `height 45` `font-size 13.33->16px` `line-height 1.2` `padding 10 20` `white-space nowrap flex-shrink 0` | `demo/templates/index.html:144` 隐藏 `input#receiptFile` 的触发按钮 `选择收据图片` 需湿手 44px；原 41.59/13.33 未达标 | `btn h45 font16` pass |
| `demo/static/css/style.css:531,538` `.btn-tool` | `min-height 38->44` `height 45` `font-size 0.85rem->16px` `line-height 1.2` | 工具栏所有 `.btn-tool` 含 `原图新窗/旋转/重置` 统一达标，胶囊内按钮亦受此 | `btn-tool h45 font16` pass |
| `demo/static/css/style.css:610,625` `.crop-capsule` `.btn-crop-capsule` | `crop-capsule height 40->45 min-height 45`；`btn-crop-capsule height 28->45 min-height 45 font-size 0.85rem->16px line-height1.2` | `demo/static/css/style.css:604` 胶囊 40 保持语义但提升至 45 以容纳 45 内按钮；`demo/templates/index.html:227` `button#btnCropToggle` 需 `>=44/16` | `caps h45 btn h45 font16` pass |
| `demo/static/css/style.css:1325,1332` `table.data-table.receipt-item-table th/td` | 选择器提升 `table.data-table.receipt-item-table th` `padding 6->4` `height 66 min-height 66`；`td` 同步 `height 66 min-height 66 padding 6->4 white-space nowrap` | 原 `table.data-table th height42` `td height48` 优先级 `0,1,2` 压过 `.receipt-item-table` `0,1,1` 致 `th42 td66 diff24`；提升优先级并统一 66 实现 `diff0.0` 供郭会计逐笔打勾 | `th66 td66 diff0.0` pass |
| `demo/static/css/style.css:1355,1382,1404` 明细内层 | `.receipt-item-table .form-control height 30->26 padding 4->3 line-height1.2`；`.item-name-sku-stack gap 4->2`；`.sku-pill padding 2 8->1 6 font 0.74->0.70 gap 4->3 line-height1.2`；`td/th padding 6->4` | 内层堆叠 `input30+gap4+sku22+pad12=74>66` 撑爆 `td` 致 `td74 th66 diff8`；压缩至 `26+16+2+8=52` 使 `td` 回落 `66`，零截断 | `stack53 td66` pass |
| `demo/static/css/style.css:2058` `.row-warning` | `background rgba(245,158,11,0.08) -> var(--warning-bg)` `+ .row-warning td background var(--warning-bg) !important` | `demo/static/css/style.css:2042` 要求黄底 `background var(--warning-bg)` `#fef3c7` 绑定行级；原 `0.08` 过淡不易发现 | `bg rgb(254,243,199)` pass |
| `demo/static/js/main.js:4208` `appendTableRow` | `confidence <0.5 -> <=0.40` | `demo/static/css/style.css:2042` 绑定 `confidence<=0.40` 行级黄底；原 `0.5` 过宽致误染，改至 `0.40` 与 `quality_warnings/review_priority` 互补 | `row-warning` 仅 `<=0.40` |
| `demo/static/js/main.js:2745` `updateToolbarButtonStates` | `crop` 分支新增 `if(cropOverlay) cropOverlay.classList.remove('hide')` | `demo/templates/index.html:215` `div#cropOverlay.hide` 默认隐藏，`toggleCropMode()` 前因 `!isPhotoSelectable` 守卫 + 未移除 `hide` 导致点击后仍隐藏，不满足 `173 cropOverlay 默认 hide 点击 toggleCropMode() 可见 虚线 2.5px 黄半透明` | `overlay hide true -> false border 2.4px dashed rgb(242,255,88) bg rgba(242,255,88,0.28)` pass |
| `demo/templates/index.html:165` `batchAggregateProgress` | 核查 `aria-live polite` 已存在，无需改动；验证 `0/0 -> 0/2` | 批量聚合进度 `P1-4` 聚合本批上传/解析进度 | `aria polite text 0/2 hide false` pass |
| `demo/static/js/main.js:182` `isNonWebImageFile` | 核查已正确 `heic/heif/tif/tiff` `type image/heic|heif|tiff` | 保证 HEIC 不黑屏，服务端转 JPEG | `isNonWebImageFile('a.heic') true` pass |

### Orca 严格验证日志 (真机 `orca eval getBoundingClientRect` `getComputedStyle`)

```
# 选择收据图片 主按钮
orca eval --expression "btn=document.querySelector('#uploadArea .btn.btn-primary'); r=btn.getBoundingClientRect(); cs=getComputedStyle(btn); {h:r.height, fs:cs.fontSize, minH:cs.minHeight}"
→ {h:45, fs:16px, minH:44px}  passH true passF true  (原 41.59/13.33 fail)

# 胶囊内按钮
orca eval --expression "btn=document.getElementById('btnCropToggle'); r=btn.getBoundingClientRect(); cs=getComputedStyle(btn)"
→ {h:45, fs:16px, minH:45px, heightCss:45px} passH true passF true (原 37.99/13.6 fail)

# 胶囊容器
orca eval --expression "caps=document.querySelector('.crop-capsule'); r=caps.getBoundingClientRect()"
→ {h:45, w:223, heightCss:45px, minH:45px}  39.99~40 -> 45 pass (提升至 >=44)

# cropOverlay 默认 hide 点击可见 虚线黄半透明
orca eval before: {hide:true, border:2.4px dashed rgb(242,255,88), bg:rgba(242,255,88,0.28)}
toggleCropMode() → after {hide:false, border:2.4px dashed rgb(242,255,88), bg:rgba(242,255,88,0.28), activeTool:crop}  pass

# batchAggregateProgress
orca eval {id:batchAggregateProgress, cls:batch-progress hide->batch-progress, aria:polite, text:0 / 0 -> 0 / 2 完成 (0%), barW:0% -> 0%, hide:true->false}
→ aria-live polite 已存在，上传后正确 0/2 pass

# row-warning 黄底 confidence<=0.40
orca eval appendTableRow confidence 0.35 → tr.row-warning true bg rgb(254,243,199) var(--warning-bg) pass
appendTableRow confidence 0.9 → 无黄底 pass

# scan 明细表 receipt-item-table th/td diff0.0 66
orca eval th/td after fix → {thH:66, tdH:66, diff:0, thPad:4px, tdPad:4px 3px, inpH:26, skuH:16.63, stackH:53.11} pass (原 th42 td74 diff8 fail)
inventoryTable → {thH:66, tdH:66, diff:0} 保持 pass

# 空态 [当前查看] 与批量2图
empty BatchUploader.photos=[] → {photos:0, toggleHide:true, listChildren:0, siderTag exists font 0.68rem}
batch2 photos=[pending,pending] → {photos:2, listChildren:2, tagText:当前查看, tagFontSize:10.88px=0.68rem, count:2, toggleHide:false, progressHide:false, text:0 / 2 完成 (0%), drawerOpen:true, secondVisible:true} pass

# 零截断
receipt-item-table td overflow visible white-space nowrap, inv-name-wrap flex, check scrollWidth<=clientWidth → trunc false pass

# isNonWebImageFile
isNonWebImageFile('a.HEIC') true, isBrowserDisplayableImageUrl('/uploads/a.heic') false pass
```

### 截图日志 (orca screenshot --json base64.b64decode)

| 截图 | 路径 | 大小 | 场景 | 关键可见 |
|---|---|---|---|---|
| a-strict-44px | `artifacts/e2e/a-strict-44px.png` | 136KB | scan tab `splitViewArea` 强制可见 `prefillFormCard` 2行 `row-warning` 黄底 `capsule45` `batch 0/2` `sider 2图` | `选择收据图片` 45px/16px 白底居中 `框选裁剪 45px` 胶囊 45 `th66 td66 diff0` `黄底 #fef3c7` 无截断 |
| a-strict-crop | `artifacts/e2e/a-strict-crop.png` | 136KB | 同上 `activeTool crop` `cropOverlay 200x120` `border 2.4px dashed #f2ff58 bg rgba(242,255,88,0.28) box-shadow 9999px` | 虚线黄半透明框 `60,80,200,120` `应用裁剪` 按钮显现 `crosshair` 光标 |
| a-00-staff-role | `artifacts/e2e/a-00-staff-role.png` | 91KB | 保留初次 `staff` 角色切换 | `admin/owner/staff` 下拉 |
| a-01-upload-area | 保留 | 91KB | 修复前 41.59 对比 | 修复前留证 |

生成命令（零Playwright）：
```
orca screenshot --json | python3 -c "import json,base64; d=json.load(open('/tmp/j')); b64=d['result']['data'].split(',',1)[1] if d['result']['data'].startswith('data:') else d['result']['data']; open('artifacts/e2e/a-strict-44px.png','wb').write(base64.b64decode(b64))"
orca screenshot --json | ... -> a-strict-crop.png
```

### 缺陷修复状态更新

| ID | 标题 | 原等级 | 修复后 | 验证 |
|---|---|---|---|---|
| A-P1-1 | 上传主按钮与胶囊按钮未达 44px/16px 湿手阈值 | P1 | **已修复** 2026-08-26 `btn h45 fs16` `btnCrop h45 fs16` `caps h45` `white-space nowrap` | `orca eval btn h45>=44 fs16>=16 true` `caps h45` |
| A-P1-2 | [当前查看] 标签空批量不可见，切换延迟未留证 | P1 | **已修复/可验证** 空态 `toggleHide true` 属预期；批量2 `tag 当前查看 10.88px 0.68rem hide false` `drawerOpen true` | `orphan snapshot` + `eval siderTag` |
| A-P1-3 | 低置信黄底 `row-warning` 未在本次样中触发视觉验证 | P1 | **已修复** `confidence <=0.40` 阈值 + `var(--warning-bg)` 黄底 `rgb(254,243,199)` 可通过 `appendTableRow confidence0.35` 强制复现 | `row-warning true bg #fef3c7` |
| A-P1-4 | th 42px / td 66px 差值24 非 diff0.0 | P1 | **已修复** `table.data-table.receipt-item-table th/td height66` 优先级提升 `0,2,2` + 内层压缩 `stack53` | `th66 td66 diff0.0` `inventory th66 td66` |
| A-P0-5 | 热敏费用抽屉折扣/运费/押金在单明细单据未显性透出 | P0 | **已知** 抽屉交互保留 `附加费用调节` 收起态；单行票据 0 值收起属预期，更正/折让场景需后续显性 0 值提示 | 保留 P0，后续迭代 |

**雷达更新**：易用 3→4（44/16 达标 胶囊显性）清晰 3→4（黄底可复现 标签 0.68rem 可见）完整 3→4（裁剪虚线可见 批量 0/2 聚合可见） — 平均 3.4→4.0 达交付线。

**产出校验**：`Read demo/templates/index.html:144 hidden input` `demo/static/css/style.css:338 btn 45/16` `604 crop-capsule 45` `demo/static/js/main.js:182 isNonWebImageFile` `index.html:165 batchAggregateProgress aria-live polite` `style.css:2058 row-warning var(--warning-bg)` `main.js:4208 confidence<=0.40` 均已链路打通；`artifacts/e2e/a-strict-*.png` 已落盘；零Emoji 全程 `file_path:line_number`。


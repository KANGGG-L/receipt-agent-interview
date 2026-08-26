Phase0 Done: A
# SubAgent-A · 采集与 Side-by-Side 复核工作台 — 文档通读摘要

## 一句话业务总结
让湾仔烧腊阿辉在清晨6点湿手暗光、货车齐催的后巷，用44px大按钮1分钟批量连拍5-20张单据，Side-by-Side 左右原图高亮对照一眼定位差异，SmartSplitter 一键解耦复合规格，乐观锁保障并发无覆盖，低置信黄底与付款印章显式可信背书，老板2分钟内完成审批入账，解决店员5分钟手抄放弃导致ERP空转的GIGO死穴。

## FR/场景映射表

| FR/场景 | UI元素 | 后端接口 | 验收阈值/契约 |
|---|---|---|---|
| FR-1 明细抽取（品名/数量/单价/金额四元组，7种单据混用） | `demo/templates/index.html:449-474` `table.receipt-item-table` `tbody#itemTableBody` 每行 `input.inp-name` / `inp-qty` / `unit` / `inp-price` / `inp-amount`，`span.current-view-tag` [当前查看] 缩略图 `li.photo-sider-item.active` 高亮 | `POST /api/upload` `demo/app/api_receipts.py:205` 创建 `status=uploaded`，`GET /api/receipt/{id}` `demo/app/api_receipts.py:458`，`PUT /api/save_edited` `demo/app/api_receipts.py:514` 携带 `version` | 行级置信度 `confidence 0-1`，`is_void`/`actual_qty` 划线作废可视，印章/免责声明0污染（`app/services/contract.py:29` `extra=forbid`），数量×单价=金额 误差>0.05触发拦截 |
| FR-2 跨格式摄入（HEIC/EXIF 扶正、弱光 CLAHE、压缩 1600px） | `demo/templates/index.html:144` `input#receiptFile` `accept="image/*" multiple` 隐藏通过 `button.btn-primary:contains("选择收据图片")` 触发展示 `img#previewImg src=blob:`，`demo/static/js/main.js:182-306` `isNonWebImageFile()`/`setMainPreview()` 处理 HEIC | `demo/app/api_receipts.py:100` `_save_upload()` `UPLOAD_DIR` 落盘，`PIL.ImageOps.exif_transpose` 自动扶正，`heif` 解码，最大边1000px快速预检 `BLUR_THRESHOLD=30.0` `demo/app/api_receipts.py:112` | HEIC横拍自动扶正，上传渲染<1s，极模糊 `Laplacian var<30` 400快速失败（`IMAGE_QUALITY_ERROR`），不进入 `opencode` 管线 |
| FR-4 批次复核（Side-by-Side + 缩略图切换 + 乐观锁） | `demo/templates/index.html:177` `div#splitViewArea.split-view` 左 `div.img-viewer-container` 右 `div#prefillFormCard`，`#photoSiderList li.photo-sider-item` 2缩略图+`thumbnail.active`+`sider-viewing-tag` [当前查看]，`demo/static/js/main.js:588` 侧栏抽屉 | 状态机 `docs/05-AI产品体系与模块Spec/00-产品总纲与PRD主架构/00-AI产品体系总纲与产品PRD主架构.md:118-142` `uploaded→parsing→parsed→edited→approved` `extra="forbid"` `demo/app/models.py:29,42`，`version`乐观锁 `demo/app/api_receipts.py:73` 409 `VERSION_CONFLICT` | 切换延迟<300ms，批量1-20张 `POST /api/upload_batch` `demo/app/api_receipts.py:302` `len>20` 限流400，pHash去重 `md5` 去重提示 `duplicate`，并发快速连点无 `version conflict` 红字 |
| FR-8 置信度与复核（行级黄底/低置信badge/反馈飞轮） | `span.badge` `badge-warning` 低置信度，`tr.row-warning` 黄底（`demo/static/css/style.css:883` `background:var(--warning-bg)`），`div#qualityWarningsBanner` 质量警告，`div.feedback-cell` 点赞/点踩 `btn-like`/`btn-dislike` | `confidence` 顶层与行级 `demo/app/api_receipts.py:559` `confidence:0.5` 默认，`huama_evaluator`/`image_quality_guard` 压降至≤0.40，`POST /api/receipt/{id}/feedback` `demo/app/api_receipts.py:909` `threshold=3` 连续点踩沉淀 | 低置信行 `confidence≤0.40` 黄底置顶+可点击解释，整单 `review_priority_score` 量化（实测 345: 0.1），避免幻觉入库；反馈 `FEEDBACK_COMMENT_MAXLEN=2000` |
| 场景 P0-清晨后厨连拍（Staff湿手暗光） | `demo/static/css/style.css:326-340` `.btn` `min-height:38px` 实测 `button` 41.6px，`div.crop-capsule` 高度39.99px 宽度214px，`demo/templates/index.html:146` 选择收据图片按钮 | `POST /api/upload_batch` 异步 `async=true` 立即返回 `job_id` `queue`，前端显示 `div#batchAggregateProgress` 进度条 | 按钮≥44px、字≥16px、行高≥66px `th 42px` `td 66px`，弱光下 `CLAHE` 增强（Spec 01:2.2），批量拍摄1-20张0等待返回备料 |
| 场景 P0-付款印章判定（红蓝印章） | `demo/templates/index.html:406-413` `select#inpPaymentMark` 选项 `印章/手写批注/仅签名/已付款`，`select#inpSettlementType` `cash/credit` 现结/月结 | `demo/app/services/contract.py:18-55` `detect_red_stamp()` RGB阈值 `R>150 G<100 B<100` 占比>0.001，`payment_mark_from_image()` 综合 `llm_marked` 与红章，`demo/app/api_receipts.py:23` `_supplement_payment_mark()` | 红蓝章正确落 `payment_mark` `stamp`/`已付款`（实测合成红章 347 返回 `stamp` 正确），区分 `paid_at_delivery`/`unpaid` 防止重复付款 |
| 场景 P0-失败显式出口（重拍/转手工） | `demo/templates/index.html:489-511` `div#errorCard` 双出口 `button#btnForceRetry` 继续AI解析 vs `button#btnConvertManual` 转手工补录，`div#manualEntryNoImage` 无原图占位 | `POST /api/receipt/{id}/retry` `demo/app/api_receipts.py:730` 仅 `uploaded/parsed/error`可重试，`POST /api/receipt/{id}/convert_manual` `demo/app/api_receipts.py:752` 仅 `owner` 可转 `manual_entry`，`POST /api/receipt/{id}/discard` 软删回收站 | 图像过暗/过曝（灰度<20|>240）`error`态，显式401/409/400码，不静默篡改；超时120s熔断重试1次仍超时保留原图手工补录 |

## 状态机与门禁
严格白名单 `uploaded→parsing→parsed→edited→approved` 与 `→flagged/error`，副作用仅挂 `→approved` 唯一写 `Append-Only InventoryLog` `demo/app/api_receipts.py:669-672` `apply_receipt_to_inventory`，`extra="forbid"` `demo/app/models.py:29` 拒绝非法字段，`math_engine` 守恒 `Σitems -discount+deposit+delivery=total` 误差>0.05拦截，`Pydantic` + `validate_contract` `demo/app/services/contract.py:59` 打回重试≤3轮。

## 前端选择器校验（Read 实测）
- 角色切换：`demo/templates/index.html:73` `select#demoRoleSelect` 侧边栏品牌区，值 `admin/owner/staff` 存 `localStorage demo_role`，`X-Role` 头 `demo/static/js/main.js:373-381` `buildAuthHeaders()` 实测 `orca select --element e1 --value staff` 成功
- 上传：`input#receiptFile` `demo/templates/index.html:144` `hidden` 通过按钮 `document.getElementById('receiptFile').click()` 触发OS窗口，`img#previewImg src=blob:` 本地预览
- AI识别：按钮文案 `选择收据图片` `demo/templates/index.html:147` + `开始AI智能解析` `demo/templates/index.html:259`，4阶段 Loading `demo/templates/index.html:270-319`
- Side-by-Side：`div#splitViewArea.split-view` `demo/templates/index.html:177` `grid-template-columns 1fr 1fr`，左侧 `img-viewer-container` 520px 深色画布
- 胶囊卡：`button#btnCropToggle.btn-crop-capsule` `demo/templates/index.html:227` + `div#cropOverlay.crop-overlay-box.hide` `demo/static/css/style.css:592-601` 2.5px虚线+黄半透明+9999px遮罩，显性入口高度39.99px
- 进度条：`div#batchAggregateProgress.batch-progress` `demo/templates/index.html:165` `aria-live=polite`，`div#batchProgressBar` width 0-100%

## 关联画像与Gemba
`docs/01-产品方案(14步)/step2-问题验证.md:30-53` 5家：商户A深水埗茶餐厅陈老板48 夫妻店15年 日均8-12张 70%手写、商户E湾仔烧腊阿辉28 清晨6:00后巷湿手验货、商户B旺角中菜郭经理55 管15家；`docs/01-产品方案(14步)/step4-用户画像.md:1-250` Staff零打字 vs Owner成本敏感>10%标红。168张真实单据163张+57张黄金 `demo/app/api_admin.py:57` 灰测/Memory，幂等归一三层互不信任 `docs/05-AI产品体系与模块Spec/04-OCR专项治理与面试攻防体系/01-OCR全链路Workflow与架构全景Spec.md:10-54` 6阶段。

## 文档通读日志
- `docs/05-AI产品体系与模块Spec/00-产品总纲与PRD主架构/00-AI产品体系总纲与产品PRD主架构.md:1-211` FR/状态机
- `docs/05-AI产品体系与模块Spec/01-业务核心组件Spec/01-组件Spec-收据智能采集与Side-by-Side复核工作台.md:1-116` 弱光连拍/乐观锁
- `docs/05-AI产品体系与模块Spec/04-OCR专项治理与面试攻防体系/01-OCR全链路Workflow与架构全景Spec.md:1-97` 三层零信任6阶段
- `docs/01-产品方案(14步)/step8-产品方案.md:1-276` 40px胶囊
- `docs/01-产品方案(14步)/step2-问题验证.md:30-53` 5家Gemba
- `docs/01-产品方案(14步)/step4-用户画像.md:1-250` 湿手暗光
- `demo/templates/index.html:1-150` + `demo/static/js/main.js:1-300` + `demo/app/api_receipts.py:1-100` 选择器

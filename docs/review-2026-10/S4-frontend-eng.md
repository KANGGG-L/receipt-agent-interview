# S4 前端工程质量审查报告

审查对象：KANGGG-L/receipt-agent-interview @ bb7ff17，服务 http://127.0.0.1:15010（AUTH_ENABLED=0）。
临时脚本、日志与截图：/tmp/indent/01a12a4e-a627-78c0-a7f6-25a1147af3ec/S4/；关键截图另存于 artifacts/review/screens/S4-*.png。

## 1. 审查范围与方法

已看的文件：
- demo/templates/index.html（5158 行）、evalset.html；demo/static/js/main.js（25128 行）、export_center.js、evalset.js、eval_workbench.js、app.js（0 字节）；demo/static/css/style.css（6126 行）；demo/static/test_strict.html；demo/static/images/；demo/app/main.py:247-325（静态资源与模板的服务方式）。
- demo/tests 下 15 个前端 harness（*.js），以及 4 个前端相关 pytest 文件。
- 历史文档：docs/05-AI产品体系与模块Spec/05-增量模块PRD与ACSpec/08-Codebase全景架构与统一化重构蓝图.md（第 23/25 项）、docs/01-产品方案(14步)/step13-安全合规.md:129，用来核对之前声称已修的问题是否真的修了。

静态分析（Node/Python 脚本）：
- innerHTML、模板插值和字符串拼接 sink 扫描（interp.js、concat.js），转义函数使用统计。
- 全局符号与死代码统计（deadcode.py），8 行窗口重复代码检测（dup.py）。
- CSS token、硬编码颜色/字号、!important、断点、z-index 统计（css.py）。
- 全局事件监听扫描（listeners.js），apiFetch 链缺 catch 的启发式扫描。

动态验证（Playwright Chromium）：
- 依次打开 8 个 Tab，再直接调用 15 个详情/弹窗入口函数，用无害 payload `<img src=x onerror=console.log('S4XSS-<字段>')>` 检测是否执行（pw_xss.py、pw_xss2.py）。
- 用 HTML 实体绕过 jsStr，并实际点击按钮验证（pw_jsstr.py）。
- 409 版本冲突和双击重复提交（pw_409.py）；上传后 Job 轮询行为（pw_poll.py）。
- 故障注入：500 纯文本、断网、接口挂起 30 秒（pw_fail.py）。
- 用路由拦截注入 1600 张收据测大表渲染，不写库（pw_big.py）。
- Fast 3G + 4 倍 CPU 节流下的移动端冷启动；360-1280 共 8 个宽度的布局遮挡（pw_misc.py、pw_w.py）；可访问性 DOM 统计和模态框焦点循环（pw_misc.py）。
- 跑了全部前端 harness 和 4 个前端 pytest。

本次创建的测试数据（没有删除任何已有数据）：
| 类型 | ID | 内容 |
|---|---|---|
| 供应商 | 3 | 名称、notes、电话都含 XSS payload |
| SKU | 3 | name/category/base_unit 含 XSS payload |
| SKU | 4 | name 为 `S4jsstr &quot;+console.log(&quot;S4XSS-jsstr&quot;)+&quot;` |
| 收据 | 3 | 手工单，供应商、品名、单位、备注含 payload；409 测试时版本被带外从 v1 改到 v2 |
| 收据 | 17 | 上传 demo/uploads/cb057ad0.jpg 触发 Job；因为没有 LLM key 识别失败，前端自动转成手工单 |

没覆盖的部分：
- i18n 归 S5，全流程 UX 归 S3。
- AUTH_ENABLED=1 模式下的前端行为没有起实例实测，只做了代码阅读。
- 没有执行 approve，避免往共享库写库存流水，所以「识别结果单位经 approve 落到 SKU.base_unit」这条链路只核对了代码。

## 2. 维度评分表

| 维度 | 分数 | 理由 |
|---|---|---|
| 安全（XSS/存储/CSP） | 3 | 转义覆盖面广（w2Escape 321 处、escapeHtml 57 处，toast 只走 innerText），但仍有 2 个已实测的存储型 XSS；没有 CSP；token 放在 localStorage |
| 架构与模块边界 | 2 | 25k 行单文件，运行期向 window 挂了 804 个全局符号，同一份收据列表在 4 个模块各存一份 |
| API 层与错误处理 | 3 | apiFetch 统一处理鉴权头、401、403，409 冲突弹窗做得好；但没有超时，有多处缺 catch，失败或加载中时显示的 0 会误导用户 |
| 性能 | 2 | 首屏约 2.4 MB 全部未压缩，开发态 no-store 导致每次重新下载；28px 的 logo 用了 583 KB 的 PNG；1600 行全量渲染时出现 744 ms 长任务 |
| CSS 与设计体系 | 2 | 只有 35 个颜色/圆角 token，没有间距、字号、z-index 刻度；硬编码颜色约 1600 处；!important 211 处；z-index 从 1 到 999999 共 18 档 |
| 响应式 | 2 | 宽度小于 768px 时照片抽屉常驻遮挡主内容，360-414px 下「选择收据图片」按钮点不到 |
| 可访问性实现 | 3 | 有 openAccessibleModal 焦点循环和归还焦点；但约 100 个模态框里只有 3 个声明 role=dialog，96/255 个输入框没有可访问名称，页面没有 h1/h2 |
| 可测试性与工程化 | 2 | 没有 lint/格式化/CI；harness 把整个 main.js 塞进 vm 执行，很脆，3 个已经坏了也没人发现；?v= 版本号漏改 |

## 3. 问题清单

| ID | 严重度 | 类别 | 位置 | 问题与证据 | 对用户/业务的影响 | 修复建议 | 工作量 |
|---|---|---|---|---|---|---|---|
| S4-01 | P0 | 安全/XSS | main.js:443-445 `jsStr`；调用点 8587、8589、8924、8926、21730、21756、21767 | jsStr 只转义 `<` 和 `'`，但结果被放进 HTML 属性 `onclick='...'`。浏览器会先解码属性里的实体，`&quot;` 还原成 `"` 后就跳出了 JS 字符串。实测：staff 角色 `POST /api/inventory/skus` 创建 SKU 4，名称见上表；owner 打开库存页后按钮属性变成 `openStocktakeModal(4, "S4jsstr "+console.log("S4XSS-jsstr")+"", 0, "kg")`，点「盘点」后控制台输出 S4XSS-jsstr（pw_jsstr.py，截图 S4-jsstr_after_click.png） | 店员（或被投毒的识别结果，经 approve 自动建 SKU，见 services/inventory.py:118）可以在老板会话里执行任意脚本：以 owner 身份调接口审批、改付款；开启鉴权后还能读走 localStorage 里的 token | 这 7 处改成 `data-sku-id` 加事件委托，从数据缓存取名称，不再把字符串写进内联 JS；最低限度也要让 jsStr 的结果再过一次 w2Escape | S |
| S4-02 | P0 | 安全/XSS | main.js:9160（合并重复食材弹窗 `updateSecondaryCheckboxes`） | `${s.base_unit}` 和 `${s.current_stock}` 没转义就拼进 innerHTML。实测：SKU 3 的 base_unit 设为 payload 后，调用 openSkuMergeModalFromInv() 时控制台输出 S4XSS-skuunit（pw_xss2.py，截图 S4-d_openSkuMergeModalFromInv.png）。base_unit 有两个来源：staff 可写的 /api/inventory/skus，以及 approve 时由识别出的明细 unit 自动建档（services/inventory.py:118-120 `base_unit=it.get("unit")`） | 同 S4-01，而且有 VLM 输出这条间接注入链 | 改成 `w2Escape(s.base_unit)` 和 `Number(s.current_stock)` | S |
| S4-03 | P1 | 响应式/CSS | style.css:2116-2140、2552-2563；index.html:760 `#photoSider` | 抽屉的「隐藏」靠 `left:240px` 加 `translateX(-100%)`，前提是有 240px 的侧栏挡在它下面。宽度小于 768px 时侧栏 display:none，抽屉实际露在 0-240px 区域。实测 360/390/414px 下 `elementFromPoint` 命中的是抽屉，「选择收据图片」被遮挡（pw_w.py；截图 S4-m_3s.png：「本批照片 0」浮层加 8px 模糊盖住上传卡片） | 店员手机拍照上传是主流程，窄屏下主按钮点不到，内容被模糊浮层盖住 | 关闭状态加 `visibility:hidden; pointer-events:none`（或直接 `display:none`），隐藏偏移改成 `translateX(calc(-100% - 240px))`；小于 768px 时改成从左侧全宽滑出 | S |
| S4-04 | P1 | 性能/工程化 | main.py:258-325；index.html:9-10、181、5156-5157 | uvicorn 没开 gzip：main.js 实测传输 1,168,225 字节（gzip 后 291 KB），首屏合计约 2.41 MB（HTML 461 KB、JS 1.22 MB、CSS 143 KB、logo 583 KB）。开发态（只要 ENV 不是 production 就算）全部 `no-store`，每次打开都重新下载。Fast 3G + 4 倍 CPU 下 DCL 为 12.2 s（pw_misc.json）。logo_icon_perfect.png 是 810x840、583 KB，实际按 28px 显示（style.css:113） | 香港店员用 4G 现场拍照时每次打开要多花数秒，流量也白白消耗 | 加 `GZipMiddleware(minimum_size=1024)`；logo 换成 2-4 KB 的 SVG/WebP；生产态静态资源按内容 hash 加 `immutable`，HTML 设 `no-cache` | S |
| S4-05 | P1 | 健壮性 | main.js:9551 `loadReceiptsHistory`、13189、13459、13827（都没有 .catch）；8432 `loadInventoryData` | 故障注入（pw_fail.py）：(a) /api/receipts 断网时出现 2 个未捕获 rejection「Failed to fetch」，归档页 KPI 显示「待审核 0 / 识别失败 0 / 逾期赊单 0」，没有任何错误提示；(b) /api/inventory 挂起 30 秒期间，KPI 显示「库存食材种数 0 / 低库存 0」，主表没有加载态（截图 S4-fail_inventory_hang_30s.png）；(c) 500 纯文本时有 toast 加旧数据提示，这条处理得对。启发式扫描 143 个 apiFetch 调用点，约 20-34 处看不到 catch（已人工确认上面 4 处） | 老板会把「0 张待审」当真，漏掉待审单或逾期赊单；高压环境下的店员分不清「没数据」和「没加载出来」 | apiFetch 统一加 `AbortSignal.timeout(15000)` 和 `fetchJson()`（检查 res.ok、解析失败兜底）；KPI 初始显示「-」或骨架屏，失败时显示「加载失败，点此重试」，不要显示 0 | M |
| S4-06 | P1 | 性能/状态管理 | main.js:1111、7336、9551、12833、13189（5 处各自 `apiFetch('/api/receipts')`）；api_receipts.py:1197 不分页 | 首屏 /api/receipts 请求 4 次，切到归档页再请求 2 次；/api/suppliers 首屏 2 次、归档页 3 次（pw_xss.json dup_api）。结果分别存在 cachedWorkbenchQueueData（6741）、allArchiveReceipts（9548）、finState.receipts 等处，多处各有一份真相。用 1600 行模拟数据（后端注释称 live 库约 1600 条）点开归档页：长任务 744 ms + 621 ms + 166 ms，DOM 从 3.5k 涨到 59.5k 节点（pw_big.py） | 数据一多，低端手机切 Tab 会卡 2-5 秒（4 倍 CPU 估算）；一个模块审批后，另一个模块还显示旧状态 | 加 `ReceiptStore`（一次拉取、带 TTL、`invalidate()` 后广播）；归档列表分页或做窗口化（每页 50 条）；后端 /api/receipts 支持 limit/offset/status | M |
| S4-07 | P2 | 安全/CSP | index.html 内联事件 487 个、style 属性 1501 个；main.js 字符串里内联 onX= 139 个；main.py 没有任何安全响应头 | 没有 CSP、X-Frame-Options、X-Content-Type-Options。有这么多内联 handler，严格 CSP 无法落地，只能加 `'unsafe-inline'`，等于失去 XSS 的第二道防线（S4-01/02 就是证据） | 一旦漏掉一处转义就直接能利用 | 第一步先加 `X-Frame-Options: DENY`、`nosniff` 和 `frame-ancestors`；随 Tab 拆分逐步把 onclick 换成委托，最后上 `script-src 'self'` | M（分阶段） |
| S4-08 | P2 | 安全/存储 | main.js:458-473（token 存在 localStorage `receipt_auth_token`）；evalset.js:36、eval_workbench.js:36 各自重复读取 | 开启鉴权后 Bearer token 长期放在 localStorage，任何 XSS 都能读到；三个脚本各自硬编码同一个键名 | 和 S4-01/02 叠加后就是账号接管 | 中期改成 HttpOnly + SameSite=Strict 的 cookie 会话；短期先修 XSS，token 设短 TTL | M |
| S4-09 | P2 | 工程化/测试 | demo/tests/test_memory_metrics_panel.js、test_vendor_memory_governance_panel.js、frontend_pending_memory_badge_harness.js | 这 3 个 harness 当前失败：报 `window.addEventListener is not a function`，之后的 const 落进 TDZ，报 `Cannot access 'MM_STYLE_NO_DATA' before initialization`。原因是 harness 用 vm 把 25k 行整文件跑一遍，stub 稍有不全，后面的代码就全部不可用。另外 frontend_auth_headers_test.js 依赖 cwd，只能在 demo/ 下运行；这 4 个文件都没有 pytest 包装（引用 test_vendor_memory_governance_panel.js 的只有一个目录卫生测试），所以回归时没人发现 | 记忆治理面板、待处理角标已经没有自动化覆盖了，测试报绿但实际有缺口 | 统一由一个 pytest 参数化跑全部 *.js 并断言退出码为 0；长期按模块拆文件，让 harness 只 import 被测模块 | S |
| S4-10 | P2 | 工程化/缓存 | index.html:10、5156-5157；evalset.html:214；main.py:312 | 最后一次改 ?v=20261005_combobox_v5 是在 bc260e7（10-10 19:40），此后 main.js 又提交了 5 次（6aefee9…d206133），版本号没动；export_center.js 10-10 改过，版本号还是 20260930；evalset.js 没有版本号；eval_workbench.js 的版本号硬编码在 Python 里。生产态不设 no-store，浏览器靠启发式缓存，可能跑旧 JS 配新 HTML | 上线后部分用户「改了没效果」，或者新 HTML 调了旧 JS 里不存在的函数 | 启动时计算文件 mtime 或 hash，由服务端注入 `?v=`（index 已经走 FileResponse，改成读文本替换即可），删掉手工版本号 | S |
| S4-11 | P2 | 架构 | main.js 全文 | 文件顶层 736 个 function 声明、205 个 const/let；运行期 window 上新增 804 个符号（782 个函数）。显式 `window.X = X` 286 处，只是为了让内联 onclick 能找到函数。没有模块边界：鉴权、toast、复核台、库存、财务、引擎配置、观测中心都在同一作用域里互相直接调用 | 改一处容易影响别处；重名会静默覆盖（目前 0 处重名，靠自觉）；新人难上手 | 见第 6 节重构路线 | L |
| S4-12 | P2 | 重复实现 | main.js:260 `w2Escape`、18302 `escapeHtml`；export_center.js:25、evalset.js:72 各有一个 `esc` | 4 个转义函数，语义不一样：两个 esc 不转义引号，放进属性上下文就不安全，见 export_center.js:860 `err.message` 直接拼进 innerHTML。两套确认框并存：原生 confirm/prompt/alert 共 39 处，自研 showCustomConfirmModal 13 处。日期区间校验 4 份拷贝（19290、20077、24206 等）。三套 API 封装：apiFetch、evalset.js:52 `api`、eval_workbench.js:30 `headers()`。8 行窗口的逐字重复只占 0.5%，问题主要是同一件事有多套实现 | 修一处漏三处（S4-02 就是 base_unit 没走统一渲染的结果）；原生 confirm 在手机上样式不可控 | 抽出 `shared/dom.js`（escape、html 模板标签）、`shared/api.js`、`shared/dialog.js`，旧实现改成这些的别名 | M |
| S4-13 | P2 | 可访问性 | index.html 中 id 含 Modal/Dialog/Overlay/Drawer 的容器 101 个，带 role="dialog" 的只有 3 个（5048、5079、5113）；main.js:10107 `archiveDetailModal`、13212 `paymentModal`、13257 `reconCreateModal` 直接 `classList.remove('hide')` | openAccessibleModal（7664）实现得对：实测 skuModal 按 25 次 Tab 焦点都留在弹窗内，Esc 能关闭。但只有 5 个调用点加 11 个 openModalById，归档详情、付款这些主流程弹窗绕过了它，没有 role、焦点陷阱和焦点归还。DOM 统计：96/255 个输入框只有 placeholder，没有 label 或 aria-label（例：#inpSupplier、#invSearchInput、#supSearch）；页面 h1/h2 为 0，标题从 h3 开始；164/330 个 button 没写 type（页面只有 1 个 form，影响小）；img 全部有 alt | 读屏和键盘用户在主流程弹窗里会迷失；焦点落到背景页，误操作风险高 | 所有弹窗统一走 openModalById 或 openAccessibleModal；给主要输入补 aria-label；侧栏标题用 h1，各 Tab 标题用 h2 | S |
| S4-14 | P2 | CSS 设计体系 | style.css:1-50（:root）；css.py 输出 | token 只有 35 个（颜色、圆角、字体），没有 spacing、font-size、z-index 刻度。硬编码颜色：style.css 632 处（191 种）、index.html style 属性 372 处、main.js 字符串 567 处，合计约 1600 处。字号 30 种 rem 值。!important：style.css 169、index.html 42。z-index 共 18 档（1 到 999999，其中 9999 出现 9 次、10000 出现 4 次、999999 出现 4 次）。有 7 个 var() 引用了未定义变量（--text-primary 26 处、--text-secondary 5 处没有兜底值，颜色退回继承值）。--font-mono 里的 JetBrains Mono 没有加载。断点 11 种（420/640/767/768/899/900/980/1024/1100/1120/1280） | 视觉一致性只能靠人盯；改品牌色要动约 1600 处；弹层叠放顺序不可预测 | 补 `--space-1..6`、`--fs-xs..xl`、`--z-dropdown/drawer/modal/toast` 四档；把 --text-primary/--text-secondary 定义为现有 token 的别名；断点收敛到 3 个（640/1024/1280） | M |
| S4-15 | P2 | 安全/部署 | demo/static/test_strict.html（GET /static/test_strict.html 返回 200）；demo/static/js/app.js（0 字节，返回 200） | 生产静态目录里放着验收用页面（标题「D-P1 Strict Validation」、内部验收术语）和空文件 | 暴露内部开发信息，显得不专业 | 移到 demo/tests/fixtures 或 docs，删掉 app.js | S |
| S4-16 | P3 | 安全/第三方 | index.html:7-9 | Google Fonts 样式表会阻塞渲染，且没有 SRI（Google Fonts 的 CSS 是动态生成的，本来就无法加 SRI）。外链不可达时（比如部分内地网络）首屏渲染要等超时 | 首屏变慢，还有隐私和合规外联 | 自托管 woff2 子集，或者直接用系统字体栈（PingFang TC / Noto Sans TC 已在 fallback 里） | S |
| S4-17 | P3 | 安全/XSS（低可利用） | main.js:15864-15865 `renderEnginePresetMenuItems`；19234、20602；export_center.js:860 | 预设模板 key 只转义了单引号，就放进 `onmousedown="...('${safeKey}')"`，含 `"` 就能跳出属性。重构蓝图第 23 项早已登记，至今没修。key 来自 admin 自己命名（main.js:16076），属于自我 XSS。19234/20602 用 w2Escape 处理 JS 字符串上下文，`&#39;` 会被解码回 `'`，目前数据源是内部常量或日期，不可利用。export_center.js:860 把后端 msg 拼进 innerHTML | 现阶段风险低，但沿用的是 S4-01 同一种错误写法 | 随事件委托改造一起修 | S |
| S4-18 | P3 | 事件监听泄漏 | main.js:8747 `viewInventoryDetail` | keydown 监听只在按 Esc 时移除，点「关闭」按钮或遮罩关闭时不移除，每开关一次就多一个残留监听。其他全局监听（initFocalZoomAndCrop 等）只在 init 时绑一次；bindAdvParamListeners 已有 dataset 去重（蓝图第 25 项已修） | 长时间使用后有轻微泄漏 | 把移除逻辑放进 close() | S |
| S4-19 | P3 | 健壮性/轮询 | main.js:1997-2074 `pollReceiptJob` | 固定 800 ms 轮询、总超时 5 分钟（最多约 375 次），没有退避，页面隐藏时也不暂停；批量上传 N 张就同时开 N 条轮询；单次网络抖动就直接判失败（2069-2071），没有重试，而后端 Job 可能仍在跑。好的方面：done/error/取消令牌/超时都会结束轮询，实测收据 17 识别失败后 1.1 s 停止轮询并自动转手工（pw_poll.py）。超时文案「请重试或检查后端服务状态」是技术用语 | 弱网下误报「识别失败」，店员重复上传就会产生重复单 | 退避到 2-5 s；连续失败 3 次才判失败；document.hidden 时暂停；文案改成「网络不稳，系统仍在识别，请稍后在队列查看」 | S |
| S4-20 | P3 | 健壮性 | main.js:1699、1728 | 先 escapeHtml(file.name) 再交给只用 innerText 的 showToast，结果被双重转义：文件名 `a&b.heic` 会显示成 `a&amp;b.heic` | 小的显示错误 | 去掉这里的 escapeHtml | S |
| S4-21 | P3 | 布局健壮性 | 库存表 #inventoryTable（style 中带 nowrap 的列） | 品名或单位是一长串不可断开的字符时（本次由测试数据触发），1440px 视口下表格宽 1488px，超出 1102px 的容器，「品名」列被挤到 55px，变成一个字一行（截图 S4-shot_tab-inventory.png） | 遇到异常长的数据时表格不可读 | 带 nowrap 的列加 `max-width` 和 `text-overflow:ellipsis`，品名列设 min-width | S |
| S4-22 | P3 | 死代码/资产 | main.js:16775 `saveAdminEngineConfig`（约 60 行）、17919 `saveAdminSettings`、19466 `_fmtLastTs`；static/images 下 4 张没用到的 logo（合计约 3.6 MB） | 静态引用计数为 0（deadcode.py）。死代码本身不多，但仓库和镜像体积被大图拖大 | 可维护性 | 删除 | S |

## 4. 做得好的地方

- showToast（main.js:5432-5508）只用 innerText 和 createElement，不走 innerHTML，还有去重和条数上限。后端报错先经 humanizeBackendError 翻译成人话再展示，杜绝了通过 toast 注入。
- 大部分渲染点都转义了：w2Escape 321 处、escapeHtml 57 处。8 个 Tab 和 15 个详情入口的 payload 扫描中，供应商名、品名、备注、付款标记、调整备注都安全显示为文本（pw_xss.py、pw_xss2.py）。
- apiFetch（main.js:528-573）在脚本加载时捕获原生 fetch，统一注入 X-Role/Bearer，统一处理 401 回登录面板、403 人话提示；其中的纯函数有 node 单测（frontend_auth_headers_test.js 12/12 通过）。
- 乐观锁前端闭环完整：实测带外改版本后点保存，弹出字段级差异对比框（截图 S4-conflict_409.png）；isSavingReview 互斥，双击只发 1 次 POST（pw_409.py）。
- Job 轮询有取消令牌和总超时，删除照片时会作废在途轮询；识别失败后自动转手工录入并保留原图（收据 17 实测）。
- openAccessibleModal 实现了焦点进入、Tab 循环和关闭后归还焦点，是可以推广到全站的基础设施。

## 5. 优先级最高的 5 项修复建议（按投入产出比排序）

1. 修掉 2 个存储型 XSS（S4-01、S4-02）：main.js:9160 补两处转义；7 处 jsStr 内联 onclick 改成 data-* 加委托。合计约半天，消除 P0。
2. 修移动端抽屉遮挡（S4-03）：关闭状态加 `visibility:hidden; pointer-events:none`，几行 CSS 就能恢复手机主流程。
3. 开 gzip、换 logo（S4-04）：一行 GZipMiddleware 加一张 SVG，首屏传输从约 2.4 MB 降到约 0.45 MB。
4. 加固 API 层（S4-05）：apiFetch 加超时，抽出 fetchJson；KPI 初始显示「-」，失败时显示可重试状态；补上已确认的 4 处 catch。约 1 天，消除「显示 0 误导决策」。
5. 收敛收据列表（S4-06）加自动版本号（S4-10）：ReceiptStore 单例去掉 4 次重复请求和多处数据副本，归档页分页；服务端注入文件 hash 当 `?v=`，去掉手工维护。

## 6. 务实的重构路线（不引入框架、不加构建步骤）

前提：这是面试和单店落地项目，目标是能放心改、能测，不追求架构完美。全程不引入 bundler，直接用浏览器原生 ES modules。

阶段 0（约 1 天，先止血）：
- 完成第 5 节第 1-4 项。
- 加 `eslint`（只开 no-undef、no-unused-vars、no-implicit-globals）和 `prettier --check`，通过 `npx` 运行，不提交 node_modules。
- 写一个 pytest 跑全部 *.js harness。

阶段 1（约 2-3 天，抽出共享层，行为不变）：
- 新建 `static/js/shared/`，放 `dom.js`（escape、`html` 标签模板、`delegate(root, selector, handler)`）、`api.js`（apiFetch、fetchJson、超时、401/403/409 统一处理）、`toast.js`、`dialog.js`（合并原生 confirm 和 showCustomConfirmModal，弹窗统一走 openAccessibleModal）、`store.js`（ReceiptStore、SupplierStore、SkuStore，带 TTL 和变更事件）。
- main.js 顶部用 `<script type="module">` 引入，同时保留 `window.X = X` 兼容层，旧的内联 onclick 照常工作。

阶段 2（按 Tab 渐进拆分，每个 Tab 1-2 天，一次一个 PR）：
- 拆分顺序按风险与收益排：inventory（含 S4-01 热点）、archive/finance、scan/workbench、dishes、report、export（已经是独立文件）、engine/analytics（只有 admin 用，最后拆）。
- 每个 Tab 一个 `tabs/<name>.js`，只导出 `init(root)` 和 `refresh()`；本 Tab 的 onclick 全部改成 root 上的委托，删掉对应的 window 导出。
- 进度用两个可量化指标跟踪：window 全局符号数从 804 逐步下降，index.html 的内联 handler 从 487 逐步下降。
- harness 改为 import 单个模块，不再把 25k 行整文件塞进 vm。

阶段 3（可选）：
- 内联 handler 清零后上 CSP `script-src 'self'`。
- CSS 按 Tab 拆成 `@layer`，补 spacing、字号、z-index token。
- 生产态静态资源用 hash 文件名加 immutable 缓存。

不建议做的：引入 React/Vue 或 TypeScript 全量迁移、上微前端、为 25k 行一次性重写。这些对单店场景收益低，回归风险高。

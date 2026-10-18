# 浏览器端到端（E2E）审查最终报告

- 仓库：/home/agentuser/receipt-agent-interview（HEAD ec0ecc6，未改任何产品代码）
- 报告日期：2026-10-18
- 方法：四个独立子代理（A 功能流程 / B UI-UX 多视口 / C Console-Network / D 边界异常）分别用真实 Chrome（Playwright，executable_path=/usr/bin/google-chrome）测试；本报告由第五方对其发现做交叉合并，并对全部 P1 用真实浏览器独立复现验证。
- 证据目录（临时，建议归档至仓库 docs/qa/evidence/ 或对象存储）：
  - A：/tmp/e2e-a/evidence/（154 个文件）
  - B：/tmp/e2e-b/evidence/（38 张截图）
  - C：/tmp/e2e-c/evidence/（截图 + report_*.json）
  - D：/tmp/e2e-d/evidence/（截图 + server_e2e_d.log）
  - 本轮交叉复现新增：/tmp/e2e-final/evidence/（a1-*.png、b001-*.png、d01_api.json、a1_b001.json、a1_retry.json）

---

## 1. Executive Summary

核心业务闭环（登录 → 建单/识别降级 → 复核校验 → 审批入账 → 库存/FIFO → 消耗冲销 → 应付支付 → 对账确认 → 报表 → 导出 → 设置）在真实浏览器中全部走通，RBAC 角色隔离、乐观锁/幂等、上传校验、XSS 防护、登录节流等安全防线经主动攻击性测试均未被绕过。**无 P0。**

合并后共 26 条缺陷：P1=3、P2=12、P3=11，另有 2 条体验建议（UX-SUGGESTION）与 2 项 Environment 项（不计缺陷）。三条 P1 均已由本轮独立复现确认：

1. **A-1 弹窗遮罩拦截点击**（已证实）：FIFO 溯源弹窗打开后其 backdrop 覆盖全视口且不随关闭回收，同屏提交按钮不可再点。
2. **B-001 部门花销日期行视口溢出**（已证实）：768 视口下两个日期输入框右缘实测 x=781 / x=1213，远超视口被硬裁，第二个完全不可见不可点。
3. **D-01 1e308 毒化库存流水**（已证实）：盘点接受 1e308 后 /api/inventory/logs 永久 500，盘回正常值后仍 500，用户无法再查看该 SKU 任何流水。

主要风险面集中在：CSP 与前端内联样式写法不匹配导致动态组件样式大面积失效（C-01）、未登录阶段 11×401 请求风暴（C-02）、后端数值/长度边界校验缺失（D-01/D-02/D-03/D-07）。

---

## 2. Test Environment

| 项 | 值 |
|---|---|
| 被测对象 | demo/ FastAPI + 静态前端（HEAD ec0ecc6） |
| 浏览器 | 真实 Chrome /usr/bin/google-chrome（Playwright 驱动，headless / Xvfb） |
| 视口 | 桌面 1440x900、平板 768x1024、手机 390x844 |
| 鉴权 | AUTH_ENABLED=1，各实例独立 48 字节随机 DEMO_TOKEN_SECRET |
| 数据 | 各 worker 独立 DB 副本（/tmp/e2e-{a,b,c,d}/，本轮 /tmp/e2e-final/receipt.db） |
| 账号 | boss@demo.hk (owner)、staff@demo.hk (staff)、admin@demo.hk (admin)（预置演示账号） |
| 本轮交叉复现实例 | http://127.0.0.1:15301（DB_PATH=/tmp/e2e-final/receipt.db，独立 secret） |

环境限制：无 AI 引擎密钥（仅验证降级路径）、单租户、无 iOS 真机、Chroma 向量库路径未随 DB_PATH 迁移（见 Environment 项）。

---

## 3. Coverage Matrix

图例：PASS=通过 / FAIL=存在缺陷 / BLOCKED=受阻 / NT=Not Tested。

| 页面(tab) | 核心流程/功能 | A(功能) | B(视觉) | C(运行) | D(边界) | 综合 |
|---|---|---|---|---|---|---|
| 登录 | 正确/错误凭据、会话、退出 | PASS | PASS | PASS | PASS(节流/伪造令牌/CSRF) | PASS |
| 今日待办 | 队列计数、跳转 | PASS | PASS | PASS | — | PASS |
| 识别复核 | 手工建单→复核→审批入账 | PASS | PASS | NT(写流) | PASS(乐观锁/幂等/草稿丢失 FAIL) | FAIL(D-04) |
| 识别复核 | 队列分页/筛选 | PASS(空态) | FAIL(B-005/B-006) | PASS | — | FAIL |
| AI 识别 | 上传→识别→复核 | BLOCKED(降级路径 PASS) | NT | NT | NT(降级 PASS) | BLOCKED（无引擎密钥） |
| 实时库存 | 列表/盘点/流水/FIFO 扣减/冲销 | PASS | PASS | PASS | FAIL(D-01) | FAIL(D-01) |
| 餐品成本 | BOM 建品/每日消耗/成本大盘 | PASS | PASS | PASS | — | PASS |
| 归档对账 | 应付→支付→对账确认 | PASS | PASS | NT | PASS(回收站/保护) | PASS |
| 部门花销 | 部门管理/报表钻取 | PASS | FAIL(B-001/B-002) | PASS | PASS(重名 D-05) | FAIL(B-001) |
| 数据中心 | 导出 CSV | PASS | PASS | PASS | — | PASS |
| 设置 | 单位/系统配置 | PASS | PASS | PASS | FAIL(D-03 灰测) | FAIL(D-03) |
| 系统与引擎配置(admin) | 配置读写 | PASS | FAIL(B-003) | PASS | PASS(越权) | FAIL(B-003) |
| 观测中心(admin) | 报表渲染 | PASS | FAIL(B-003/B-004) | PASS | — | FAIL |
| 全局 | 刷新状态/路由 | FAIL(A-3) | PASS | FAIL(C-01/C-02) | FAIL(D-04) | FAIL |
| 全局 | 故障注入（断网/慢响应/连点） | — | — | FAIL(C-03/C-04) | — | FAIL |

Not Tested 汇总（按 worker 归并去重）：
- AI 真实识别全链路（VLM/LLM 引擎未配置，仅验证 ENGINE_NOT_CONFIGURED 降级）
- 多页分页（数据量不足 1 页，控件空态正常）
- 多租户隔离（仅 default 租户）
- 灰测子页签 02-04（灰测命中分配/分流逻辑）
- iOS 真机与安全区
- 供应商合并/食材合并、盘点调整深测、价格异动确认、价格历史弹窗
- 繁體切换全量回归（切换按钮验证通过，未做全量断言）
- 浏览器前进/后退下的未保存修改三选一弹窗（headless 未稳定触发）
- 交叉审核（audit 腿）
- tab 切换竞态下旧响应覆盖新响应的深测（C-04 已证请求放大，竞态未深测）

---

## 4. Defect Register（按 P0→P3，同根因已合并）

统计：本轮合并后 **P0=0、P1=3、P2=12、P3=11**（原始四 worker：P0=0、P1=3、P2=13、P3=12；合并动作：B-002 并入 B-001；C-06 移出计为 Environment；A-6 判定 Not a bug 不计入）。

### P1（全部经本轮独立复现确认）

#### FINAL-01（原 E2E-A-1）| 识别复核/餐品成本 | 弹窗遮罩打开后拦截同屏按钮点击
- 来源：Subagent A；交叉验证：本轮复现
- 复现：owner 登录 → 餐品成本 → 每日消耗录入 → 份数填 1 → 点「一键扣减库存并核算真实成本」→ FIFO 溯源弹窗(costTraceModal)出现 → 再次点同一按钮
- 预期：弹窗可关闭、关闭后按钮恢复可点；或 backdrop 不拦截
- 实际（本轮复现）：提交成功后 costTraceModal className=`modal-backdrop`（无 hide）、display:flex、矩形覆盖全视口 (0,0,1440,900)、z-index=1000、pointer-events=auto；Playwright 二次点击报 "intercepts pointer events" 超时。关闭前同屏按钮全部不可点
- 证据：/tmp/e2e-final/evidence/a1-12-after-submit.png、a1-13-final.png、a1_retry.json；原始 /tmp/e2e-a/evidence/consume-confirm-modal.png
- 根因：已证实——弹窗内容关闭后 backdrop 类/显示状态未同步回收（「审核入账」确认弹窗路径未复现，非全局）
- 修复建议：关闭弹窗时同步给 backdrop 加 hide/移除；或 backdrop pointer-events:none
- 关联：与 C 的弹窗/Toast 层叠问题同属「弹层生命周期管理」主题（见 C-01/C-03）

#### FINAL-02（原 E2E-B-001，合并 E2E-B-002）| 部门花销 | 起止日期行在 768/1440 溢出视口被硬裁
- 来源：Subagent B；交叉验证：本轮复现
- 复现：768x1024 视口 owner 登录 → 部门花销 → 查看「起止日期」筛选行
- 预期：控件随容器换行/收缩，全部位于视口内
- 实际（本轮复现，768 视口实测）：两个 date input 右缘 x=781、x=1213（视口 768），width=410 定宽不可收缩；`.main-wrapper` overflow-x:hidden 将溢出硬裁，第二个日期框完全不可见不可点。1440 视口同根因（原 B-002：右缘 1461>1440，裁 21px）
- 证据：/tmp/e2e-final/evidence/b001-01-report-768.png、b001-02-report-768-after.png、a1_b001.json；原始 /tmp/e2e-b/evidence/tab-report_tablet2.png、tab-report_tablet_card.png、tab-report_desktop.png
- 根因：已证实——style.css:6406 `#tab-report .report-date-cluster { flex-wrap: nowrap }` 全视口生效；csp-html-styles.css:209 `.csp-s-207` 定宽；768 断点修正（style.css:6476）只命中部分选择器
- 修复建议：<=1024px 断点允许 wrap；date input 改 `flex:1 1 125px; min-width:0; max-width:100%`；核查 `--page-max-width` 与工具栏 nowrap 约束

#### FINAL-03（原 E2E-D-01）| 实时库存 | 1e308 盘点被接受并永久毒化 /api/inventory/logs
- 来源：Subagent D；交叉验证：本轮复现
- 复现：owner 登录 → 对任一库存 SKU POST /api/inventory/{id}/stocktake {"actual_qty": 1e308} → GET /api/inventory/logs
- 预期：极端数值被范围校验拒绝，或日志接口容错
- 实际（本轮复现）：盘点 200 成功（current_stock=1e308）；logs 立即 500 INTERNAL_ERROR；随后盘点回 2.0 后 logs 仍 500（历史流水含 inf，json 序列化崩溃）。UI 流水页对该 SKU 永久不可用。原报告另证 1e308 save_edited 触发 500 但不写库、文案正确
- 证据：/tmp/e2e-final/evidence/d01_api.json（stocktake=200、logs_after=500、logs_after_restore=500）；原始 /tmp/e2e-d/evidence/server_e2e_d.log（行 420/497/566/643/708/785）
- 根因：已证实——stocktake 只拦负数不拦超界浮点；logs 用默认 json.dumps（NaN/inf 不合规）
- 修复建议：数量加有限性+上界校验（如 <=1e9）；logs 序列化接 sanitize（app/services/contract.sanitize_nan）并对历史行一次性清洗

### P2

#### FINAL-04（原 E2E-A-2）| 识别复核 | 手工单三处金额必须精确手工对齐（UX-SUGGESTION 候选，按行为缺陷记录）
- 页面：识别复核 → 手工录入弹窗
- 复现：数量 3、单价 0、金额 60、总额 60 → 保存
- 预期：可保存或自动反算
- 实际：后端拒绝「按数量单价应是 0 元，相差 20 元」；单价/金额/总额三处必须一致，错误只报一行
- 证据：/tmp/e2e-a/evidence/credit9-approved.png、credit11-saved.png
- 根因：已证实（行为）；推测为强制对照原图的有意设计
- 修复建议：金额输入后自动反算单价（可覆盖）；或提示同时给出三处当前值

#### FINAL-05（原 E2E-A-7）| 识别复核 | 审批入账成功后复核面板残留旧单内容
- 复现：审批入账后观察右侧步骤 2/2 区域
- 实际：队列清零但复核表单仍显示已入账单据与「审核入账」按钮，可对已入账单发起无效操作（后端会拒）
- 证据：/tmp/e2e-a/evidence/after-approve-final.png
- 根因：已证实（UI 状态未清理）
- 修复建议：审批成功回调关闭复核面板并刷新队列

#### FINAL-06（原 E2E-B-003）| 观测中心/引擎配置 | 平板 768 工具栏溢出且无滚动容器
- 实测：tab-engine `.engine-preset-toolbar` right=965（视口 768）且无滚动祖先，按钮被硬裁不可点；tab-analytics 表格有 overflow-x:auto 属可接受降级
- 证据：/tmp/e2e-b/evidence/tab-engine_tablet.png、tab-analytics_tablet.png
- 根因：已证实——f1b.css 收敛规则全在 @media(max-width:640px)（f1b.css:103-113），641-768 区间无覆盖
- 修复建议：断点提升到 max-width:1024px

#### FINAL-07（原 E2E-B-004）| 观测中心 | 1440 桌面表格横向溢出 711px 且无滚动提示
- 实测：.csp-s-362 right=2151（视口 1440），可滚动但无渐隐/提示，右半列易被忽略
- 证据：/tmp/e2e-b/evidence/tab-analytics_desktop.png
- 根因：已证实（列数多无压缩策略）
- 修复建议：滚动容器加右侧渐隐遮罩或提示；压缩日期列宽

#### FINAL-08（原 E2E-B-005）| 识别复核 | 分页 disabled 按钮无 disabled 视觉
- 实测：button[disabled] opacity=1、cursor:pointer，与同页既有 disabled 规范（op .55 + not-allowed）不符
- 证据：/tmp/e2e-b/evidence/state_disabled_desktop.png
- 根因：已证实（通用 disabled 样式未覆盖分页按钮 class）
- 修复建议：补 disabled 态样式，统一现有规范

#### FINAL-09（原 E2E-B-006）| 识别复核 | 手机 390 筛选胶囊纵向堆叠，刷新按钮被 tabbar 遮挡约 20px
- 实测：「刷新」bottom=803 > tabbar top=783，初始视口半遮挡需滚动
- 证据：/tmp/e2e-b/evidence/tab-scan_mobile.png
- 根因：已证实（窄屏未允许换行；pb=72px 不足）
- 修复建议：胶囊行窄屏改横向滚动或两行网格；底部 padding >=96px

#### FINAL-10（原 E2E-C-01）| 全站 | CSP 拦截内联样式，JS 动态组件样式被丢弃
- 复现：加载任意页面看 console，每次加载 15 条 CSP error
- 实际：common.js 用 style.cssText 注入样式，CSP `style-src 'self'`（无 nonce/hash）全部拦截；登录面板/对话框/Toast 等仅靠类名兜底
- 证据：/tmp/e2e-c/evidence/report_traverse.json、01_initial.png
- 根因：已证实（CSP 策略与前端写法不匹配）
- 修复建议：动态样式加 nonce 或移入静态 CSS/类名

#### FINAL-11（原 E2E-C-02）| 全站 | 未登录阶段各模块并发抢跑 API，11×401 风暴
- 复现：清 cookie 打开首页
- 实际：鉴权判定完成前 workbench/archive/inventory/dishes/finance 已发请求，全部 401，>=4 条 console error 噪音
- 证据：/tmp/e2e-c/evidence/report_auth.json、report_traverse.json
- 根因：已证实（初始化时序缺 auth gate；后端 401 行为正确）
- 修复建议：鉴权就绪前不调度首屏加载（全局 ready promise）；401 静默降级

#### FINAL-12（原 E2E-C-03，与 FINAL-13 交叉关联）| 实时库存 | 接口失败 Toast 重复堆叠（同一错误弹 3 次）
- 复现：route abort /api/inventory 后点库存 tab
- 实测：同帧 3 个请求各自弹 Toast，无去重；连点场景（FINAL-13）会放大
- 证据：/tmp/e2e-c/evidence/report_fault.json、x_fail_inventory.png
- 根因：已证实（无去重/合并）
- 修复建议：Toast 按 key 去重或合并计数

#### FINAL-13（原 E2E-C-04）| 实时库存 | 快速连点无请求去重（3 连点 = 9 个请求）
- 复现：对库存侧边栏按钮连点 3 次
- 实测：inventory / price_threshold / logs / ai-insights 各×3；与 FINAL-12 叠加造成告警风暴；存在旧响应覆盖竞态窗口（未深测）
- 证据：/tmp/e2e-c/evidence/report_fault.json
- 根因：已证实（无 in-flight 守卫/节流）
- 修复建议：切换 tab abort 上次请求或复用 in-flight Promise

#### FINAL-14（原 E2E-D-02）| 供应商 | 超长文本无长度上限（5,000,000 字符直存 DB）
- 复现：staff POST /api/save_edited {"supplier_name": "A"*5_000_000} → 200
- 实际：写库成功并全量回传浏览器，拖垮页面、放大 DB 体积
- 证据：/tmp/e2e-d/evidence/server_e2e_d.log（receipt id=6）
- 根因：已证实（无 max_length 约束）
- 修复建议：Pydantic max_length（建议 <=200），超限返回 VALIDATION_ERROR

#### FINAL-15（原 E2E-D-03）| 设置/引擎配置 | grey_percent=-5 被静默接受
- 复现：admin PUT /api/admin/engine-config {"grey_enabled":true,"grey_percent":-5} → 200
- 实际：落库为 0 但响应 success，展示值与提交值不一致；150/100.5 却被类型校验拦截，行为不一致
- 证据：/tmp/e2e-d/evidence/server_e2e_d.log
- 根因：已证实（负值 clamp 为 0 且报成功）
- 修复建议：越界显式 422

### P3

#### FINAL-16（原 E2E-A-3）| 全局 | 刷新后不回到当前 tab（SPA 无路由状态）
- 实际：URL 恒为 /，刷新回今日待办；浏览器返回键直接离开站点
- 证据：/tmp/e2e-a/evidence/reload-state.png
- 根因：已证实（不写 history/hash）
- 修复建议：location.hash 或 pushState 同步 tab

#### FINAL-17（原 E2E-A-4）| 餐品成本 | 新建餐品弹窗初始多渲染一行空配方行
- 实际：初始 1 行 + 新增 1 行，空行触发校验报错需手工删除
- 证据：/tmp/e2e-a/evidence/dish-saved3.png
- 根因：已证实（初始行与新增计数错位）
- 修复建议：保存前忽略全空行，或不预置空行

#### FINAL-18（原 E2E-A-5）| 识别复核 | 「放弃上传」在无金额时被保存校验拦截
- 实际：不填总额点「放弃上传」先弹「请填写整单总金额」，放弃确认未弹出
- 证据：/tmp/e2e-a/evidence/discard-done.png
- 根因：已证实（放弃复用保存前校验路径）
- 修复建议：放弃操作跳过表单校验

#### FINAL-19（原 E2E-B-007）| 实时库存 | 手机 390 空态标题单字孤行，筛选区 4 行占首屏 40%
- 证据：/tmp/e2e-b/evidence/tab-inventory_mobile.png
- 根因：已证实（标题容器恰容 6 字；按钮未做窄屏合并）
- 修复建议：text-wrap:balance 或缩短文案；窄屏合并工具行

#### FINAL-20（原 E2E-B-008）| 观测中心 | 「自定义：」标签与日期框断行分离，输入框拉伸近整行
- 根因：推测（label 与 input 不在同一 flex 项）
- 证据：/tmp/e2e-b/evidence/tab-analytics_desktop.png
- 修复建议：包入同一 nowrap cluster 并限 input max-width

#### FINAL-21（原 E2E-B-009）| 数据中心/餐品成本 | 手机 390 子页签按钮超宽（有滚动容器，风险低）
- 实测：.export-tab-btn right=455、.dish-subtab-btn right=471（>390），可滑动到达但无视觉提示
- 证据：/tmp/e2e-b/evidence/tab-export_mobile.png、tab-dishes_mobile.png
- 根因：已证实（按钮行不收缩，依赖横向滚动）
- 修复建议：加渐隐遮罩或允许 wrap

#### FINAL-22（原 E2E-C-05）| 全站 | /favicon.ico 404
- 证据：/tmp/e2e-c/evidence/report_traverse.json
- 根因：已证实（未提供 favicon 路由；static/images/logo_icon_64.png 存在未映射）
- 修复建议：加路由或在 head 指向 logo

#### FINAL-23（原 E2E-D-04）| 识别复核 | 手工单新建（无 receipt_id）时刷新/关页，已填内容静默丢失
- 实际：version_conflict.js 的 beforeunload 草稿仅覆盖 currentReceiptId 非空场景
- 证据：/tmp/e2e-d/evidence/ui_manual_entry.png
- 根因：已证实（startManualEntry 置 null，草稿逻辑不生效）
- 修复建议：空态也写草稿键并在恢复时提示；或 dirty 时弹原生确认

#### FINAL-24（原 E2E-D-05）| 部门 | 相同名称部门可重复创建
- 证据：/tmp/e2e-d/evidence/server_e2e_d.log（两个「厨房」id=1、id=2）
- 根因：已证实（无重名校验）
- 修复建议：同租户重名拒绝或提示确认

#### FINAL-25（原 E2E-D-06）| 报表 | cost_report 周期参数完全不校验，非法月份静默返回空报表
- 复现：GET /api/cost_report?month=9999-99 / notamonth → 均 200，报表为空，用户误以为无开销
- 根因：已证实（解析失败静默降级空区间）
- 修复建议：月份/日期校验，非法 422

#### FINAL-26（原 E2E-D-07）| 遥测 | /api/track 无白名单/限流，10KB event_type + 100KB properties 入库
- 复现：30 次连发 spam 事件全部 200
- 根因：已证实（无白名单与限流）
- 修复建议：事件类型白名单 + 字段限长 + 简单限流

### Environment（不计入产品缺陷）

- ENV-01（原 E2E-C-06）| 启动告警「记忆表与向量库不一致：孤儿向量」（本轮复现环境同样出现：孤儿向量=7）。根因推测：DB_PATH 外置时 Chroma 向量库仍指向 demo/.rag_chroma，隔离不完整；只读巡检不自动修复，行为符合设计。建议：向量库路径随 DB_PATH 派生或提供 CHROMA_PATH 环境变量。证据：/tmp/e2e-final/server.log、/tmp/e2e-c/evidence（uvicorn 日志）
- ENV-02 | 测试环境限制：无 AI 引擎密钥、单租户、无 iOS 真机（影响 Coverage，非产品问题）

### UX-SUGGESTION（体验建议，与缺陷分开）

- SUG-01 | 手工单明细金额列与单价列自动联动/反算（与 FINAL-04 相关，若产品定位为「强制对照原图」则保留现状但完善提示）
- SUG-02 | 可滚动表格与横向裁切处统一加渐隐遮罩/「可横向滚动」提示（覆盖 FINAL-07/21）

### 判定为 Not a bug（防误报记录）

- 原 E2E-A-6：已付款单据在「未付」筛选下显示空列表属正确行为，仅布局观感（/tmp/e2e-a/evidence/recon7-full.png）
- 原 E2E-B-003 中 analytics 表格部分：有 overflow-x:auto 祖先，可滚动，属可接受降级

---

## 5. Reproduction Evidence（P1 独立交叉复现）

本轮在全新隔离实例 http://127.0.0.1:15301（独立 DB 与 secret）上对三条 P1 逐一复现：

| Issue | 复现结果 | 关键实测 | 证据 |
|---|---|---|---|
| A-1 / FINAL-01 | 确认 | FIFO 提交成功后 costTraceModal 为 `modal-backdrop`（无 hide）、display:flex、覆盖 (0,0,1440,900)、z-index 1000；二次点击提交按钮 "intercepts pointer events" 超时 | /tmp/e2e-final/evidence/a1-12-after-submit.png、a1-13-final.png、a1_retry.json |
| B-001 / FINAL-02 | 确认 | 768 视口两个 date input right=781/1213（width 410），被 overflow-x:hidden 硬裁，第二个不可见不可点 | /tmp/e2e-final/evidence/b001-01-report-768.png、a1_b001.json |
| D-01 / FINAL-03 | 确认 | stocktake 1e308 → 200；/api/inventory/logs → 500；盘回 2.0 后 logs 仍 500 | /tmp/e2e-final/evidence/d01_api.json（stocktake=200、logs_after=500、logs_after_restore=500） |

复现脚本：/tmp/e2e-final/crosscheck.py、a1_retry.py、d01.py（建议随报告归档）。注：复现过程中出现的 401/CSRF 首试失败均为脚本认证方式问题（改用 /api/auth/login + x-requested-with 头后通过），非产品缺陷。

---

## 6. Root Cause Analysis

已证实（有代码/实测定位）：
- CSS：nowrap + 定宽在 641-768 区间与桌面工具栏的断点覆盖缺口（FINAL-02/06）；disabled 样式选择器缺口（FINAL-08）
- 弹层生命周期：backdrop 与弹窗内容显隐不同步（FINAL-01）；审批回调未清理面板（FINAL-05）
- CSP：style-src 'self' 与 style.cssText 注入方式冲突（FINAL-10）
- 初始化时序：缺 auth gate（FINAL-11）
- 请求层：无 in-flight 守卫、Toast 无去重（FINAL-12/13）
- 后端校验：stocktake 数值只拦负数 + logs 默认 json.dumps（FINAL-03）；save_edited/suppliers 无 max_length（FINAL-14）；grey_percent clamp 后仍报成功（FINAL-15）；cost_report 周期静默降级（FINAL-25）；departments 无重名校验（FINAL-24）；/api/track 无白名单/限流（FINAL-26）；草稿逻辑仅覆盖有 receipt_id 场景（FINAL-23）
- 前端状态：SPA 无路由持久化（FINAL-16）；空行计数错位（FINAL-17）；放弃操作复用保存校验（FINAL-18）

待验证（推测）：
- FINAL-20：「自定义：」label 与 input 不在同一 flex 项（未做 DOM 级定位）
- FINAL-13 的旧响应覆盖竞态：请求放大已证实，数据错乱竞态窗口未深测
- ENV-01 的向量库路径派生逻辑（需读 db/maintenance 配置确认）

---

## 7. Recommended Fix Order（P0→P3 + 依赖）

1. FINAL-03（D-01）：先做输入上界校验止血，再做 logs 序列化 sanitize + 历史行清洗（两步有依赖：先堵源头，否则清洗后可再被毒化）
2. FINAL-01（A-1）：backdrop 生命周期修复（改动小、阻塞主流程操作）
3. FINAL-02（B-001 合并 B-002）：一处断点修复同时解决 768/1440
4. FINAL-10（C-01）→ FINAL-11（C-02）：CSP 修复后动态组件样式恢复，再做 auth gate（二者都会改动全局启动路径，建议同批回归）
5. FINAL-12 + FINAL-13：Toast 去重与 in-flight 守卫同属请求层，一并做
6. FINAL-14、FINAL-15、FINAL-25、FINAL-26、FINAL-24：后端校验类批量补齐（模式一致：Pydantic/范围校验/白名单）
7. FINAL-23（草稿丢失）、FINAL-05（面板残留）、FINAL-18（放弃被拦）：状态管理类
8. FINAL-06、FINAL-09、FINAL-19、FINAL-21：窄屏/断点类 CSS（可与 3 同批）
9. FINAL-16（路由 hash）、FINAL-17、FINAL-08、FINAL-22、FINAL-07、FINAL-20：低风险体验项
10. SUG-01/SUG-02：与产品确认定位后排期

---

## 8. Residual Risks（不可复现/未覆盖）

- AI 真实识别全链路完全未测（含识别准确率、Job 失败重试、原图签名链路深测）
- 多页分页、多租户隔离、灰测命中分流、交叉审核（audit 腿）未覆盖
- iOS 真机（安全区、Safari 行为差异）未测；本轮全部为 Chromium
- FINAL-13 的旧响应覆盖竞态未深测（窗口存在但未观察到数据错乱）
- FINAL-20 根因为推测，未做 DOM 级定位
- 浏览器前进/后退的未保存修改弹窗（AC-06-04）headless 未稳定触发
- 证据目录 /tmp/e2e-{a,b,c,d,e2e-final} 均为临时目录，72 小时后可能被清理，建议尽快归档到仓库内长期路径

---

## 附：原 worker 缺陷编号对照

| 原编号 | 最终编号 | 等级 | 说明 |
|---|---|---|---|
| E2E-A-1 | FINAL-01 | P1 | 复现确认 |
| E2E-A-2 | FINAL-04 | P2 | |
| E2E-A-3 | FINAL-16 | P3 | |
| E2E-A-4 | FINAL-17 | P3 | |
| E2E-A-5 | FINAL-18 | P3 | |
| E2E-A-6 | （Not a bug） | — | 不计入 |
| E2E-A-7 | FINAL-05 | P2 | |
| E2E-B-001 | FINAL-02 | P1 | 复现确认；合并 B-002 |
| E2E-B-002 | FINAL-02 | — | 并入（同根因 1440 变体） |
| E2E-B-003 | FINAL-06 | P2 | |
| E2E-B-004 | FINAL-07 | P2 | |
| E2E-B-005 | FINAL-08 | P2 | |
| E2E-B-006 | FINAL-09 | P2 | |
| E2E-B-007 | FINAL-19 | P3 | |
| E2E-B-008 | FINAL-20 | P3 | |
| E2E-B-009 | FINAL-21 | P3 | |
| E2E-C-01 | FINAL-10 | P2 | |
| E2E-C-02 | FINAL-11 | P2 | |
| E2E-C-03 | FINAL-12 | P2 | 与 C-04 交叉引用 |
| E2E-C-04 | FINAL-13 | P3 | 与 C-03 交叉引用 |
| E2E-C-05 | FINAL-22 | P3 | |
| E2E-C-06 | ENV-01 | — | 环境问题，不计缺陷 |
| E2E-D-01 | FINAL-03 | P1 | 复现确认 |
| E2E-D-02 | FINAL-14 | P2 | |
| E2E-D-03 | FINAL-15 | P2 | |
| E2E-D-04 | FINAL-23 | P2 | |
| E2E-D-05 | FINAL-24 | P3 | |
| E2E-D-06 | FINAL-25 | P3 | |
| E2E-D-07 | FINAL-26 | P3 | |

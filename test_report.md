# Staff 角色全流程 E2E 浏览器测试报告

- **日期**：2026-08-24（05:54–06:21，共 5 轮浏览器实测）
- **环境**：macOS darwin 24.6.0 · Playwright 1.60 chromium **可见模式**（headless=false, slow_mo=400ms）· viewport 1440×900
- **服务**：`demo/demo.sh` 同源 uvicorn 热重载实例，http://127.0.0.1:15010
- **测试脚本**：`scripts/staff_e2e_full.py`（full / p4p5 分段）+ `scripts/staff_e2e_p4_patch.py`；原始证据：`artifacts/staff_e2e/`（run*.log、results*.json、screens/×19）
- **测试数据**：
  - PIL 合成图（可重复）：`receipt_clear / receipt_blur（拉普拉斯方差 0.4）/ receipt_dark（亮度均值 39.3）/ receipt_matherr / receipt_extra_fields`
  - 真实数据：`~/Desktop/hk/My Drive/Receipts/batch1`（163 张 iPhone HEIC，12 供应商，含 classification_manifest.csv）；实测使用 祥興×2、德利行×4、金百加×1（预览 JPEG + 原始 HEIC）
- **识别引擎（当前配置）**：识别=openai、审核=opencode（`audit_enabled=false`，交叉审核处于关闭状态）

> 评级口径：✅ Pass ｜ ⚠️ Pass-with-findings（通过但有发现）｜ ❌ Fail ｜ ℹ️ 记录性观察

---

## 一、各维度测试结果表

### 维度一：照片质量前置拦截（`_laplacian_variance` 前端链路）

| # | 测试项 | 预期 | 实际 | 评级 | 备注 |
|---|--------|------|------|------|------|
| 1a-UI | 极模糊图走店员界面上传 | 有画质提示，不静默 | preConfirm 卡温和提示：**「画质提示：检测到该照片疑似模糊，已自动加载就绪，点击『继续 AI 智能解析』将由模型尽力识别。」** | ✅ | 文案人话+给出行动路径 |
| 1a-API | 极模糊图无 force 直传 | 400 + `quality_warnings=["image_blur"]`，不进管线 | **400** `IMAGE_QUALITY_ERROR`，`quality_warnings=["image_blur"]`，msg「图像模糊度过高，请重新拍摄清晰单据」 | ✅ | 门禁本身正确且 <1s 快速失败 |
| 1a-设计 | 店员单人上传路径是否被 400 硬拦截 | — | 「开始 AI 智能解析」按钮 `onclick=triggerAnalysisNow(true)` **恒带 force=true**，服务端硬拦截在该路径形同虚设，模糊图会进 LLM（本次以引擎失败收场，错误卡误导归因"画质偏低"） | ⚠️ | D21"温和不阻断"与服务端 P0-1"400 硬拦截"互相打架，需产品决策统一 |
| 1b | 过暗图（亮度 39.3）亮度提示 | 提示过暗 | 「画质提示：检测到该照片**可能过暗**…由模型尽力识别」 | ✅ | 阈值 45 实测生效；不阻断 |
| 1b-HEIC | iPhone HEIC 原图预览链路 | 可预览 | `/api/convert-image` 自动转换，直接显示真实预览+正常确认卡，**用户全程无感知**，无格式术语 | ✅ | 优于"提示转换"的预期 |
| 1c | 正常清晰图进入识别流程 | loading 出现 | loading 卡 4 阶段进度+计时器，45s 后预填成功 | ✅ | |

### 维度二：店员全流程核心操作

| # | 测试项 | 实际 | 评级 | 备注 |
|---|--------|------|------|------|
| 2a | 上传→识别→左预览展开/右表单预填 | splitView 自动展开；供应商「鴻運餐飲」、总额 265.00、明细 2 行全部正确（合成图 GT 100%） | ✅ | 识别耗时 45s |
| 2b | 改明细数量(10→12)→提交→edited | 保存确认弹窗→归档行「待处理(已编辑 tooltip)」总额联动 $282.00=12×8.5+180；后端 status=edited、version=3、履历 4 条 save_edited | ✅ | 表单校验即时，算术联动正确 |
| 2c | staff 审批隔离 | 「审核通过」按钮**对店员仍可见**，点击→confirm→**403**，toast：「权限不足：当前角色为店员（staff），此操作需老板（owner）及以上权限。请切换角色或联系管理员。」 | ⚠️ | 后端兜底+人话指引到位；但按钮应按角色隐藏（前端只对 admin 入口做了 U-01 隐藏，owner 域按钮未隐藏） |
| 2d | owner 域入口隔离 | 部门花销报表/AI 洞察横幅/引擎配置/黄金样本 nav 全部隐藏；归档页「登记支付」「发起对账」不存在（staff 视角隐藏）；详情弹窗「成本分摊」按钮仍存在 | ⚠️ | 同 2c：导航级隔离好，弹窗内按钮级未隔离 |

### 维度三：Agent 编排（supervisor 门禁与决策履历）

| # | 测试项 | 实际 | 评级 | 备注 |
|---|--------|------|------|------|
| 3a-合成 | 算术错误图（4×45 写 240、总额 355）应触发 gate_reject 重试 | **VLM 静默把算术"改对"**（输出 180/265），math_warnings=[]，门禁无错可检 | ⚠️ | LLM 幻觉性纠错让门禁失效——若原图真是错账，系统会掩盖；门禁本身在真实数据上有拦截（见下） |
| 3a-真实 | 真实单据上的门禁 | **算术门禁活体拦截 ×3**：德利行#40 首轮「明细合计=3291.5 预期=3291.5，但总额=2616.5（差675）」→ 重试后通过；德利行#42（差-1131.5）、#43（差1805.5）重试耗尽报错 | ✅ | 门禁+重试阶梯+errorCard 链路完整工作 |
| 3b-图内杂字段 | 配送員編號/門市代碼/優惠積分 不进结构化 | 明细零泄漏，只提取真实品名 | ✅ | |
| 3b-契约 | schema 外/类型非法字段被契约门禁拒绝 | **活体证据**：#44 上传（带 hint）VLM 输出 `payment_marked` 非 bool →「契约校验失败: payment_marked: Input should be a valid boolean」重试耗尽 error | ✅ | 契约门禁真实拒绝类型违规 |
| 3c-决策履历 | ai_decision_log 按时间记录 extract/audit 节点 | **断链**：当日 4 条 audit 决策 `receipt_id` 全为 **NULL**（`start_recognition_job` 调 `run_pipeline` 未传 receipt_id）；extract 级决策完全缺失；前端无 "#log 决策履历"区域，仅归档弹窗有操作履历（action 映射正确） | ❌ | 履历"有日志、无关联"，单据级可审计性不成立 |
| 3d-交叉审核 | audit_result 一致/分歧展示 | `audit_enabled=false`（audit_disabled skipped），全链路无一致/分歧可验证，UI 也无展示位 | ℹ️ | 配置问题；启用后建议在详情弹窗展示审核结论 |

### 维度四：VendorMemory RAG 飞轮

| # | 测试项 | 实际 | 评级 | 备注 |
|---|--------|------|------|------|
| 4a-冷启动 | 首次识别（真实长单） | 祥興→误识「七月餐室」(总额 19818 vs GT 1258)；德利行#40→供应商对、总额 2616.5 vs GT 3400、日期错 | ⚠️ | 真实手写长单冷启动质量差，正是飞轮要解决的场景 |
| 4b-写入 | owner 审批→ingest_memory | UI 切 owner→详情→审核通过→「已审核通过单据 #40」；vendor_memory 新增「德利行 Tak Lee Hong」（含 14 行明细先验）；`chroma.sqlite3` mtime=审批时刻（单 sqlite 持久化，文件数不变属正常） | ✅ | 写路径完整 |
| 4c-注入 | 二次识别注入 vendor_context | **断链×2**：① `extract_chain.py:125` 仅当 `vendor_hint` 非空才检索，而店员上传 UI 无 hint 入口；② 即使 API 带 hint 且 `retrieve_context` 能命中（直接调用返回完整记忆✓），`supervisor.state` 无 `vendor_context` 字段不透传 → 落库 `rag_context` 恒为空（#45 实证：hint 采纳进供应商名，rag_context 仍 ''） | ❌ | **飞轮只写不读**——本次测试最重要的架构发现 |
| 4d-反馈飞轮 | 同供应商连续 3 次点踩→ingest_feedback_memory（FR-9） | U-08 行反馈弹层：点踩+粤语评语「呢行品名認錯咗」×3 → receipt_feedback 3 条 dislike(vendor=德利行 Tak Lee Hong, tenant=default) → **vendor_memory notes 追加「反馈纠偏：呢行品名認錯咗…」**，Chroma 同步更新 | ✅ | FR-9 阈值 3 实测触发，反馈记忆可被 `retrieve_context('德利行')` 检索到 |
| 4d-租户隔离 | Chroma tenant 隔离 | 结构性验证：`rag.py` 按 `tenant_<id>_vendor_memory` 独立 collection 物理隔离；本次 default 租户反馈 tenant_id=default | ℹ️ | 未做多租户对抗测试 |
| 4-效果对比 | 首次 vs 后续准确率 | **无法对比**——读路径断链导致先验从未进入识别 prompt | ❌ | 修复断链后需重测 |

### 维度五：高压低教育人群 UX 专项（香港餐饮店员视角）

| # | 测试点 | 实际 | 评级 | 建议 |
|---|--------|------|------|------|
| 5a | 口语化文案 | 状态徽章「待处理/已入账/失败」、权限指引带角色与动作、「画质提示…点击『继续 AI 智能解析』将由模型尽力识别」均为人话中文；无粤语口语（如「未得」） | Good（可更好） | 全面粤语本地化是加分项非必须 |
| 5b | 上传后进度反馈 | loading 卡 4 阶段（视觉语义读取中 25% → 契约提取与字段归一 50% → … → SKU智能匹配 95%）+ 实时计时器「AI 分析耗时 00:04.6」 | Good | **缺预计等待时间**（"大约还需 30 秒"）；45s 等待对高压环境偏长且无 onCancel |
| 5c | 错误容忍/校验文案 | 空表单保存：「手工录入必须提供合法开单日期，**当前值：空**」「必须提供真实供应商名称」「必须包含至少一条消费明细」——具体到字段 | Good | 「当前值：空」偏技术；日期组件已从源头防错 |
| 5d | 视觉压力 | 主操作按钮 16px/高43px/padding 10×24；对比度：开始解析 6.29、转手工 16.05、侧栏导航 14.69、上传按钮 16.37（全部 ≥4.5 WCAG AA） | Good | 次按钮 13.3px 偏小；整体间距充足 |
| 5e | 连续 3 次出错递进引导 | 3 次模糊图提示**逐字相同**，无递进、无分步教学（如"试试把手机放平、离单据一臂距离"） | **NeedsImprovement** | 连续 N 次同类失败应升级为图文引导或"转手工"推荐 |
| 5-误导 | 错误归因准确性 | 引擎失败（配额/超时）时错误卡固定文案「收据画质偏低或未识别成功…原图已在左侧保留」——但左图清晰可见（祥興#38 案例），误导店员去重拍 | **Bad** | 应区分"画质问题/引擎繁忙/内容无法解析"三种归因 |
| 5-自动落库 | 识别完成即自动 save_edited（`autoSaveParsedPhoto`，无"全部保存"入口） | AI 结果未经人工确认即变「已编辑」状态进入待审池 | NeedsImprovement | 语义上"已编辑"应保留给人工修改；建议状态改"AI预填"或加确认步 |

---

## 二、编排性能数据（13 次上传尝试，全部真实浏览器/API 路径）

| 指标 | 数值 | 明细 |
|------|------|------|
| 识别成功 | 8/13（61.5%） | #33,35,36,37(合成图 4/4)、#39(祥兴·误名)、#40,41(德利行)、#45(合成+hint) |
| 识别失败 | 5/13 | #34(模糊图引擎失败)、#38(祥興·引擎异常)、#42,43(德利行·算术门禁重试耗尽)、#44(契约门禁重试耗尽) |
| 门禁活体拦截 | 4 次（算术×3、契约×1） | #40 首轮(差675)→重试通过；#42(差1131.5)、#43(差1805.5) 耗尽；#44 payment_marked 类型违规 |
| VLM 静默纠错 | 1 次 | #36 matherr 图 240/355 → 输出 180/265 |
| 单次识别耗时（成功） | 8–45s | 合成图 9–45s；真实长单 8–40s；含门禁重试的最长链路 81s（#40 两轮 40.5+40.5s） |
| 重试率 | 4/13 首轮被拒（30.8%） | 其中 1/4 经重试自愈 |
| 门禁拦截后恢复路径 | UI「重试」按钮可用且有效 | #40 实测一轮重试后成功 |

**真实数据识别质量（3 张可比对 GT 的成功样本）**：供应商名 1/3 正确（德利行#40 ✓；祥興→「七月餐室」✗；德利行#41→「樑油批發有限公司」✗）；总额 1/3 基本正确（#41 2387 vs 2360，-1.1%；#40 2616.5 vs 3400；#39 19818 vs 1258）。

## 三、VendorMemory 效果对比

| 环节 | 状态 | 证据 |
|------|------|------|
| 写入（approve→ingest_memory） | ✅ 通 | vendor_memory 新增行 + chroma.sqlite3 更新（06:13=审批时刻） |
| 反馈沉淀（3×点踩→ingest_feedback_memory） | ✅ 通 | notes 追加「反馈纠偏：呢行品名認錯咗」，可被检索 |
| 检索（retrieve_context） | ✅ 通 | 全名/简名（'德利行'）均返回含反馈纠偏+明细先验的上下文 |
| 注入识别（vendor_context → prompt） | ❌ 断 | 断点①前端/UI 无 vendor_hint 入口（extract_chain L125 空检索）；断点②supervisor state 不透传 vendor_context → rag_context 落库恒空（#45 实证） |
| 首次 vs 后续准确率 | 无法评估 | 需先修复断链①② |

## 四、UX/工程问题清单（按严重度排序）

| # | 严重度 | 问题 | 位置 | 建议 |
|---|--------|------|------|------|
| U-1 | **P0** | RAG 飞轮只写不读：vendor_context 双重断链，供应商记忆从不反哺识别 | `extract_chain.py:125`、`supervisor.py`(state 无 vendor_context) | ① 识别首轮后用识别出的供应商名做二轮检索注入，或上传时从照片 EXIF/最近供应商联想 hint；② supervisor 透传 vendor_context 并落库 |
| U-2 | **P0** | 决策履历断链：audit 决策 receipt_id 全 NULL、extract 决策缺失，单据级可审计性不成立 | `receipt_utils.py start_recognition_job`（未传 receipt_id）| run_pipeline 调用传入 receipt_id；补 extract 节点写入；前端详情弹窗加"AI 决策履历"区 |
| U-3 | **P1** | 引擎失败被错误卡固定归因"画质偏低"，误导店员重拍（祥興#38：图清晰仍报画质） | 前端 errorCard 固定文案 | 按 error_msg 分类：画质/引擎繁忙/门禁拒绝（#42 的算术门禁错误其实已带回，应展示） |
| U-4 | **P1** | VLM 静默纠正原图算术错误（#36），错账被"帮对"后入待审池，owner 无从察觉 | 管线行为 | 在 prompt 中要求"逐字转录，禁止自行修正算术"；math gate 已在但需 VLM 配合才有输入 |
| U-5 | **P1** | 真实手写长单识别质量差（供应商 1/3、总额 1/3），叠加 45s 等待，高压场景不可用 | 引擎/管线 | 修 U-1 让先验生效；手写单优先走"转手工"引导 |
| U-6 | **P2** | staff 可见「审核通过」「成本分摊」等 owner 域按钮，点击才 403 | 前端弹窗 | 按 isOwnerRole() 隐藏（U-01 已做导航级，补按钮级） |
| U-7 | **P2** | 模糊图单人上传恒 force=true，服务端 400 硬拦截路径形同虚设，与 P0-1 设计目标冲突 | `index.html` onclick 传 true | 默认不 force，客户端提示后由用户选择"仍然继续"（errorCard 已有该按钮） |
| U-8 | **P2** | 连续 3 次同类失败无递进引导（5e）；loading 无预计等待时间、不可取消 | 前端 | 计数升级引导；>30s 给"转手工"捷径 |
| U-9 | **P3** | 自动落库使 AI 结果即成「已编辑」，与人工编辑履历混淆 | `autoSaveParsedPhoto` | 状态语义拆分（AI预填 vs 已编辑） |
| U-10 | **P3** | 交叉审核整体关闭（audit_disabled），3d 无从验证；校验文案「当前值：空」偏技术 | 配置/文案 | 启用审核；文案改「未填写日期」 |

**做得好的**（高压低教育场景）：主按钮 16px/43px 高、对比度全部 ≥6.3；口语化状态与人话权限指引；HEIC 无感转换；4 阶段进度+计时器；校验文案具体到字段；HEIC/画质/门禁的"转手工补录"逃生门始终存在。

## 五、截图附录（`artifacts/staff_e2e/screens/`）

| 截图 | 内容 |
|------|------|
| p0_staff_home.png | staff 首页：admin/报表/AI 洞察入口全部隐藏 |
| p1a_blur_warn.png / p1a_blur_outcome.png | 模糊图温和提示 / 识别失败错误卡 |
| p1b_dark_warn.png / p1b_heic_hint.png | 过暗提示 / HEIC 自动转换后真实预览 |
| p1c_loading.png / p1c_prefill.png | 4 阶段进度条 / 预填成功（265.00 两行明细） |
| p2b_saved.png | 数量改 12 后保存，总额联动 $282.00 |
| p2c_staff_modal.png / p2c_after_click.png | staff 可见审核按钮 / 点击后 403 人话指引 |
| p3a_math_outcome.png | matherr 图识别结果（VLM 纠错后 265） |
| p4a_first_result.png / p4b_owner_approve.png | 德利行首轮（含门禁重试）/ owner 审批入库 |
| p4c_second_result.png / p4c_rag_context_card.png | 二次上传 / RAG 调试卡「（空）无 RAG 上下文」断链实证 |
| p4d_feedback_modal.png | U-08 行反馈弹层点踩×3 → vendor_memory 写入反馈纠偏 |
| p5c_validation.png / p5e_repeat_blur.png | 手工单校验文案 / 三次相同模糊提示 |

## 六、结论

店员主链路（上传→质量提示→识别→复核→保存→受控审批）**功能完整、权限兜底可靠、视觉与文案对低教育用户总体友好**。但测试揭示两个 P0 架构断链（RAG 飞轮只写不读、决策履历无单据关联）和一组真实数据识别质量问题（手写长单供应商误识、VLM 静默纠错、引擎失败误导归因），在修复 U-1/U-2 之前，"越用越准"的飞轮叙事与"可审计"的合规叙事均不成立。原始数据与日志见 `artifacts/staff_e2e/`。

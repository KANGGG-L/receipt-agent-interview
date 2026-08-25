# E2E 测试与全流程实测 U-1~U-14 综合实施方案
> 实施原则：分 4 批实施，每批完成即跑自动化验收；所有改动集中在 `demo/app/`、`demo/static/js/main.js`、`demo/templates/index.html`，无需数据库迁移。

## 批 1 · P0 架构断链修复（RAG 读断链 + 决策履历断链）

### U-1 RAG 飞轮“只写不读”修复
1. **Supervisor 状态透传**：`app/chains/supervisor.py` 中 state 增加 `vendor_context` 字段；每轮 `_run_extract` 后将 `result["vendor_context"]` 存入 state（跨重试轮保留）；首轮 extract 成功后若 context 为空，用识别出的 `data.vendor` 调 `retrieve_context()` 补检索并最终透传至 `receipt_utils.py`，完成 `receipts.rag_context_json` 落库。
2. **两处真飞轮注入**（复用 `wrap_vendor_context_sandbox` XML 沙箱）：
   - **Parse 阶段**：VLM 读图后、解析 LLM 规范化前，从 VLM 输出抽取供应商名 → `retrieve_context()` → 非空则注入 parse prompt（先验含品名规格/单位别名）；
   - **重试轮**：`extract_receipt` 增加可选参数 `vendor_prior`，supervisor 在 `gate_reject` 重试时透传 `state["vendor_context"]`，与 `retry_feedback` 并行注入。
3. **验收标准**：无 hint 上传德利行/祥兴单据 → `receipts.rag_context_json` 非空（包含版式与反馈纠偏先验）；`staff_e2e_full.py` P4c 翻绿。

### U-2 决策履历 receipt_id=NULL 断链修复
1. **API 强关联**：`app/services/receipt_utils.py` 中 `start_recognition_job` 调 `supervisor.run_pipeline` 时透传 `receipt_id=receipt_id`。
2. **多节点落库**：`app/chains/supervisor.py` 每轮 extract 决策写 `db.log_ai_decision(receipt_id=..., decision_type="extract", ai_value={attempt, engine, status, gate_err}...)`，`receipt_id=None` 时安全跳过。
3. **前端呈现**：`app/db.py` 补充 `list_ai_decisions(receipt_id)`；`build_detail` 增 `ai_decisions` 字段；归档详情弹窗在操作履历上方新增「AI 决策履历」容器（展示 extract 轮次、gate 拦截原因及 audit 结论）。
4. **验收标准**：上传单据后，`ai_decision_log` 表中该 `receipt_id` 至少有 extract + audit ≥ 2 行，无新增 NULL 行；详情弹窗可见清晰节点。

---

## 批 2 · P1 准确率与容错归因（错误归因 / VLM 逐字转录 / 长单逃生）

### U-3 错误卡归因三分类（不甩锅画质）
- **分类逻辑**：`main.js` `showErrorCard` 依优先级分为三类：
  1. **画质异常**（`IMAGE_QUALITY_ERROR` / 模糊 / 过暗）→ 标题「照片有点模糊」+ 显示「继续 AI 解析」与「转手工录入」；
  2. **门禁拦截**（`/算术门禁|契约校验|门禁/`）→ 标题「单据上的数字对不上，AI 已暂停录入」+ 提取具体差额人话说明（如“相差 165”）；
  3. **服务繁忙**（轮询超时 / 引擎报错）→ 标题「AI 服务现在很忙，这张单据还没识别完」+ 提示稍后重试，全卡不含“画质/模糊”。
- **验收标准**：56 项自动化用例全部通过，算术拦截与超时不再误导店员去重拍清晰单据。

### U-4 VLM 逐字转录 + 门禁硬裁决（拒绝静默帮对）
- **Prompt 纠偏**：修改 `PARSE_SYSTEM_PROMPT` 与 `SYSTEM_PROMPT`，明确指令：*“金额与数量必须逐字转录图面所见，严禁自行重算修正”*。
- **门禁守门**：错账由 `math_engine` 算术门禁在 3 轮内拦截并报错，将真实矛盾留给人工确认，避免老板看到“被改对的假账”。
- **验收标准**：上传合成算术错误图 `receipt_matherr.jpg`，系统准确触发 `gate_reject`，不再静默输出 265。

### U-5 真实手写长单识别质量优化
- 依赖 U-1 的 RAG 先验注入，在长单多明细冷启动后，通过先验知识库提升后续同供应商单据的品名识别率与总额匹配率。

### U-11 [本次实测新增·P1] 识别过程随时转手工逃生通道
- **问题**：真实长单（如德利行 14 行明细）解析耗时 ~34s，送货员在场催单时店员无法主动终止。
- **方案**：在 `loadingCard` 进度条右上角及计时器旁，新增醒目的 **「等不及？点击取消并转手工补录」** 按钮，点击即刻终止轮询并保留左侧原图直接切入右侧空表单。
- **验收标准**：在识别进行中点击该按钮，Loading 即刻中止并展开左右分栏手工录入模式。

---

## 批 3 · P2 角色隔离 / 模糊策略 / 交互防抖

### U-6 弹窗内老板级按钮的视觉级隐藏
- **方案**：在 `index.html` 中为 `#archiveDetailModal` 弹窗底部的「审核通过」、「标记为异常」、「成本分摊」按钮绑定独立 ID；`loadReceiptDetail` 打开弹窗时调用 `applyModalRoleVisibility()`，若当前为 `staff` 角色则直接 `classList.add('hide')`。
- **验收标准**：店员打开详情弹窗时，底部仅可见「保存单据修改」与「关闭/取消」，彻底消除越权误点击。

### U-7 模糊图默认拦截与用户显式确认
- **方案**：`index.html` 中「开始 AI 智能解析」默认传参 `triggerAnalysisNow(false)`；普通上传不带强制参数，遇极模糊图由服务端 400 返回 `IMAGE_QUALITY_ERROR`；店员在错误卡中显式点击「忽略画质警告，继续 AI 智能解析」时才携带 `force=true` 进入管线。
- **验收标准**：极模糊图首次点击解析快速收到 400 提示卡，点击卡内绿色重试按钮后才进识别。

### U-8 连续出错递进引导 + 等待预期提示
- **方案**：
  1. `sessionStorage` 记录连续画质拦截次数：第 1 次温和提示；第 2 次提示拍摄技巧（“请把手机放平、避免反光”）；第 3 次自动高亮推荐「转手工补录」按钮；
  2. Loading 耗时超过 30s 时，自动将提示语变为 *“本单明细较多，AI 正在加紧核对，预计还需 10 秒…”*。
- **验收标准**：连续 3 次上传模糊图，提示卡片呈现阶梯式递进文案。

### U-12 [本次实测新增·P2] iPhone 大图 (>1.5MB HEIC) 连续拖放防抖与并发锁
- **问题**：快速连续拖放多张 HEIC 原图时，异步 Canvas 预检与 `/api/convert-image` 返回时序偶发错位，造成预览图闪烁。
- **方案**：在 `handleSingleUploadSelection` 中加入 150ms 防抖，并在转码期间对拖拽区增加轻量 loading 占位，确保仅渲染最后一次选择的有效图片。
- **验收标准**：连续快速拖入 3 张 HEIC 单据，界面无白屏闪烁，左侧稳定展示最后一张。

---

## 批 4 · P3 状态语义 / 审计开关 / 本地化体验

### U-9 自动落库与人工编辑状态语义拆分
- **方案**：
  - `autoSaveParsedPhoto` 调用时的 payload 增加 `source: 'auto'`；店员手动点击「确认上传单据」增加 `source: 'manual'`；
  - 后端接收并在 `ai_decision_log` 与审计履历中区分记录 `auto_save`（AI预填入库）与 `save_edited`（店员人工修改保存）。
- **验收标准**：归档履历中能清晰区分哪些是 AI 自动初稿、哪些是店员实际修改。

### U-10 交叉审核开关启用与文案去技术化
- **方案**：
  - 通过 `/api/admin/engine-config` 启用交叉审核（`audit_enabled=true`）；
  - 将表单校验中的残存技术术语全面口语化（如「当前值：空」统一替换为「未填写开单日期」）。
- **验收标准**：表单拦截 Toast 呈现完全通俗的中文提示。

### U-13 & U-14 [本次实测新增·P3] 本地化与极端异常兜底
- **方案**：
  - **U-13**：多币种切换（HKD/CNY/USD）与付款标记（印章/手写/签名）在归档修改后实时与后端同步；
  - **U-14**：在界面设置中增加香港餐饮口语别名映射（如“現結”、“未得”、“對唔齊”），提升前线阿叔/阿姨的使用亲和力。

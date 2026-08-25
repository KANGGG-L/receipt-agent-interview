# Agent Memory & Behavioral Directives

## 核心指令与格式规范

1. **绝对禁止使用 Emoji**：
   - 在所有回复、用户界面（UI）、前端模板、日志、注释、说明文档中，一律严禁出现任何 Emoji 图标（例如禁止出现表情符号、手势、状态图标等）。
   - 必须使用严谨、专业的纯文本或标准 Markdown / CSS 类名（如 Badge、Tag）展示状态。

2. **绝对禁止附加说明/PS/附注废话**：
   - 严禁在回复末尾或界面中添加任何类似于“ 隐私保护中：真实供应商名称、私有底价、联系电话及香港身份证已由 DataMasker 自动脱敏，供算法及运维团队安全评估模型实战效果。”或“PS：...”之类的说教式、免责式、注解式废话。
   - 所有输出必须直奔主题、严谨、专业、克制，只呈现核心数据、架构与业务逻辑。

3. **数据脱敏标准**：
   - Admin 观测数据中，真实供应商名称脱敏为类别别名（如“蔬菜批发商_#V741”），采购单价与总额进行部分掩码（如“HK$ 2**.*0”），敏感联系方式与香港身份证号一律正则脱敏。

4. **严格的独立 Subagent 编排与质量门禁规范**：
   - **独立 Subagent 体系**：必须且仅能通过独立的 Subagent 完成各个环节，严禁主代理自行跨阶段修改代码或篡改结论：
     - `Product_Agent`：独立负责需求拆解与 AC 标准定义。
     - `Dev_Agent`：独立负责代码实现与提示词改造。
     - `QA_Agent`：独立负责真实浏览器 Playwright 实操、功能门禁核验与高压低教育 UX 专项评测。
     - `Reviewer_Agent`：独立负责代码质量、安全沙箱、反向回归与体验一致性严格终审。
   - **所有 Subagent 的强制前置约束（每次派发必须原样携带并严格执行）**：
     ```
     【CRITICAL INSTRUCTION】
     Before running tests / starting work, you MUST read:
     1. /Users/ethan/Documents/GitHub/receipt-agent-interview/docs/E2E_REPAIR_PLAN_U1_U14.md
     2. /Users/ethan/Documents/GitHub/receipt-agent-interview/test_report.md

     【MANDATORY TESTING & EVALUATION REQUIREMENTS】
     1. 必须操作浏览器（Playwright）模拟真实店员用户的全流程操作体验。
     2. 除了功能契约与门禁核验外，必须深度评估 UI 与前端整洁度、易用性、交互流畅度。
     3. 必须以「高压环境下没受过高等教育的香港餐饮店员（阿叔/阿姨）」视角进行 UX 专项评估：
        - 报错与门禁提示是否 100% 人话直白（如“单据上的数字对不上”、“相差 xx 元”），杜绝任何技术黑话或生硬英文；
        - 按钮尺寸是否足够大（高度 >= 38px）、对比度是否达标（符合 WCAG AA）、点击靶区是否舒适；
        - 逃生通道（转手工录入）是否随时可用且保留原图；
        - 算术拦截时是否清晰给出差额指引，而不是冷冰冰报错或甩锅给画质。
     ```
   - **一票否决制**：QA 与 Reviewer 阶段凡未操作真实浏览器、未进行阿叔/阿姨视角高压 UX 评估、或存在技术黑话/甩锅画质的，直接判定不及格（FAIL / REJECTED）并重回 Dev 阶段重新返工。


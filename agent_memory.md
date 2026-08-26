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

## 解析记忆落盘规范（最严格 · 零 Emoji）

本章节落实“每次解析图片都要记录 token 消耗、耗时、解析是否成功”，同时满足 DB 可查 + 文件可追溯双通道。

### 1. 落盘路径

- **DB 层**：`ai_decision_log` 表（`demo/app/db.py:184 DecisionLogRow` `1845 log_ai_decision`）  
  `extra` 列（`Text`）存结构化 JSON：`{tokens_prompt, tokens_completion, tokens_total, cost_hkd, elapsed_ms:{extract,parse,rag,audit,total}, success:true/false, image_path, supplier, doc_form}`  
  `ai_value` 亦冗余 `tokens_total/cost_hkd/elapsed_ms/success` 便于前端直接读取（`docs/04-AI技术选型与评测/02-L0-L9选型决策档案/L4-多模态VLM直识(定稿冠军).md:21`）。
- **文件层**：`artifacts/memory/parse_log.jsonl`（JSONL，每行一图，`demo/app/chains/supervisor.py:357 _finalize` 并发安全 `threading.Lock` + `a` 追加）  
  目录自动创建，Git 忽略需保留（`.gitkeep` 可选）。
- **日志层**：`RECEIPT_LATENCY` 与 `TOKENS` 双行结构化日志同时打印到 stdout（uvicorn 捕获），便于 `docker logs` / `orca eval` 追踪。

### 2. 字段定义

**DB `extra` / JSONL 单行公共字段：**

| 字段 | 类型 | 来源与说明 |
|---|---|---|
| `ts` | string ISO | 写入时间 `YYYY-MM-DDTHH:MM:SS`（`demo/app/db.py:now_iso`） |
| `receipt_id` | int \| null | 收据主键（`receipts.id`），冒烟无 id 时 null |
| `image_path` | string | 原图路径（截断 500） |
| `supplier` | string | 识别供应商（截断 120，`ReceiptData.vendor`） |
| `doc_form` | string | 单据形态枚举（`DocForm` 7 种，截断 60） |
| `engine` | string | 真实引擎 kind（`opencode`/`codebuddy`/`openai`/`qwen`，`demo/app/llm.py:build_recognition_model` 透传 `model.kind`） |
| `model` | string | 模型名（`EngineConfig.recognition_model` / `grey_*`） |
| `tokens_prompt` | int | 输入 tokens（OpenAI `usage.prompt_tokens` 或 `input_tokens`） |
| `tokens_completion` | int | 输出 tokens（`completion_tokens` / `output_tokens`） |
| `tokens_total` | int | 总 tokens（`total_tokens` 或 prompt+completion） |
| `cost_hkd` | float | 按成本表计费：输入 ¥0.15/1M + 输出 ¥1.50/1M（`L4` qwen3-vl-flash 单张约 ¥0.0022，`docs/03-评测平台/02-163张真实收据成本核算.md:17,40`）；本地 CLI 无 token 记 0 |
| `elapsed_ms` | object | `{extract, parse, rag, audit, total}`，单位 ms（`demo/app/chains/supervisor.py:202 elapsed_ms` `358 _finalize`） |
| `success` | bool | 解析是否成功（`data != None` 且门禁通过） |
| `error_msg` | string | 失败原因（截断 500，`contract_error`/`last_error`） |
| `use_grey` | int | 灰测标记 0/1 |

### 3. 代码埋点

- `demo/app/llm.py:154 CodeBuddyChatModel._run_cli` / `~260 OpencodeChatModel` / `371 OpenAIChatModel._generate`：返回 `ChatResult` 前提取 `response.json().usage`，存入 `ChatGeneration.message.response_metadata['token_usage']`（归一 `prompt_tokens/completion_tokens/total_tokens`，本地 0）。
- `demo/app/chains/extract_chain.py:262 vlm_elapsed` `300 elapsed_ms`：捕获 `token_usage` 存 `result["token_usage"]` 并透传 `cost_hkd`。
- `demo/app/chains/supervisor.py:92 _log_extract_decision`：增加 `tokens_total/cost_hkd/elapsed_ms/success` 入 `ai_value` 与 `extra`；`_finalize:357` 打印 `RECEIPT_LATENCY` 同时追加 `TOKENS` 行；落盘 `artifacts/memory/parse_log.jsonl`（`threading.Lock`）。
- `demo/app/db.py:1845 log_ai_decision`：`extra` 为 `Text` 已支持长 JSON；`list_ai_decisions` 解析 `extra` 暴露 `tokens_total/cost_hkd`。
- `demo/app/api_receipts.py:458 GET /api/receipt/{id}`：返回 `ai_decisions` 已含 `tokens_total/cost_hkd/elapsed_ms/success`；`demo/app/api_admin.py GET /api/admin/metrics` 聚合 `avg_tokens, avg_elapsed, success_rate`。

### 4. 验证指令（最严格）

```bash
# 单图解析后文件落盘含 tokens
~/.pyenv/versions/3.9.6/bin/python -c "from app.chains import supervisor; ..."
# DB 可查
sqlite3 demo/receipt_demo.db "select ai_value, extra from ai_decision_log order by id desc limit 1;" | grep -c tokens_total
# API 返回
curl -s http://127.0.0.1:15010/api/receipt/{id} -H X-Role:staff | grep tokens
curl -s http://127.0.0.1:15010/api/admin/metrics -H X-Role:owner | grep avg_tokens
```

`orca eval` 可读：本文件新增章节即为可观测记忆规范，`artifacts/memory/parse_log.jsonl` 示例行见同目录。


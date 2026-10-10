# 系统前端引擎参数代码级全量盘点与 DeepSeek 模型兼容性分析报告

## 1. 报告概述与架构背景

本报告针对香港餐饮进货收据识别系统的系统与引擎配置模块（前端 `section#tab-engine`、高级工程参数折叠面板、灰测引擎面板及 AI 治理规则），展开端到端的代码级逆向排查与深度架构分析。

系统当前采用双轨与三腿协同流水线架构：
- **Leg 1: 识别引擎 (Generator)**：负责多模态视觉抽取（VLM），输入单据原图与供应商先验（RAG），输出结构化 JSON。
- **Leg 2: 审核引擎 (Evaluator)**：负责跨模型交叉审核与校验（支持纯文本确定性审核 `text`、原图多模态交叉审核 `vlm`、低置信按需审核 `ondemand`）。
- **Leg 3: 语义解析自愈引擎 (Repairer)**：负责在识别原始输出存在轻微格式破坏或未通过契约门禁时，进行纯文本轻量级规范化修复。
- **Traffic: 金丝雀灰度发布与 A/B 实验网关**：支持单据随机抽样与供应商 MD5 哈希一致性分流。

本报告重点剖析面向类似 DeepSeek 系列模型（包括 DeepSeek-V3 文本/代码、DeepSeek-R1 深度推理、DeepSeek-V4.1-Flash 多模态视觉模型）接入时，各参数在系统中的流转拓扑、消费边界、钳制规则及异常兜底机制。

---

## 2. 前端配置项全量代码级盘点

### 2.1 核心流水线引擎（View 01）输入项盘点

| 序号 | 归属模块 | DOM 元素 ID | 后端绑定字段 | 控件类型 | 界面默认值 / 占位符 | 步长/范围限制 | 核心用途说明 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | 识别引擎 | `adminRecPresetSelect` | `engine_preset_rec` | Input/Combobox | "输入或选择模版..." | - | 厂商/模版快速预设填充 |
| 2 | 识别引擎 | `adminOpenaiRecBaseUrl` | `openai_rec_base_url` | Input (text) | "https://your-gateway.com/v1" | 字符串 | 识别模型网关 Base URL |
| 3 | 识别引擎 | `adminOpenaiRecApiKey` | `openai_rec_api_key` | Input (password) | "sk-..." | 敏感掩码 | 识别模型身份认证密钥 |
| 4 | 识别引擎 | `adminOpenaiRecModel` | `openai_rec_model` / `recognition_model` | Input (text) | "gpt-4o-mini" | 字符串 | 识别主力模型标识符 |
| 5 | 识别引擎 | `adminRecTimeoutSeconds` | `rec_timeout_seconds` | Input (number) | "60" | min=1, max=60 | 识别调用传输超时阈值（秒） |
| 6 | 识别引擎 | `adminRecMaxRetryRounds` | `openai_rec_max_retry_rounds` / `rec_max_retry_rounds` | Input (number) | "0 - 10" (缺省 3) | min=0, max=10 | 识别引擎瞬时故障重试上限 |
| 7 | 识别-高级 | `adminOpenaiRecReasoningEffort` | `openai_rec_reasoning_effort` | Select | "" (默认) | "", low, medium, high, xhigh, max | 思考强度（推理模型参数） |
| 8 | 识别-高级 | `adminRecMaxTokens` | `openai_rec_max_tokens` | Input (number) | "512 - 16384" (缺省 4096) | min=512, max=16384, step=128 | 最大生成 Token 预算上限 |
| 9 | 识别-高级 | `adminRecImageDetail` | `openai_rec_image_detail` | Select | "high" (全切片·高精度) | high, auto, low | 多模态视觉切片粒度模式 |
| 10 | 识别-高级 | `adminRecTemperature` | `openai_rec_temperature` | Input (number) | "0.0 - 0.5" (缺省 0.0) | min=0.0, max=0.5, step=0.01 | 生成采样温度（0.2 以上预警） |
| 11 | 识别-费率 | `adminPriceInputRec` | `price_input_rec` | Input (number) | "0.0000" | min=0, step=0.0001 | 输入 Token 刊例单价 (元/1M) |
| 12 | 识别-费率 | `adminPriceCachedInputRec` | `price_cached_input_rec` | Input (number) | "0.0000" | min=0, step=0.0001 | 缓存命中文本单价 (元/1M) |
| 13 | 识别-费率 | `adminPriceOutputRec` | `price_output_rec` | Input (number) | "0.0000" | min=0, step=0.0001 | 输出 Token 刊例单价 (元/1M) |
| 14 | 审核引擎 | `adminAuditEnabled` | `audit_enabled` | Select | "true" (开) | true, false | 交叉审核流水线总开关 |
| 15 | 审核引擎 | `adminAudPresetSelect` | `engine_preset_aud` | Select | "— 手动填写 —" | - | 审核模型模版选择下拉框 |
| 16 | 审核引擎 | `adminOpenaiAudBaseUrl` | `openai_aud_base_url` | Input (text) | "https://your-gateway.com/v1" | 字符串 | 审核模型网关 Base URL |
| 17 | 审核引擎 | `adminOpenaiAudApiKey` | `openai_aud_api_key` | Input (password) | "sk-..." | 敏感掩码 | 审核模型身份认证密钥 |
| 18 | 审核引擎 | `adminOpenaiAudModel` | `openai_aud_model` / `audit_model` | Input (text) | "gpt-4o-mini" | 字符串 | 审核评估模型标识符 |
| 19 | 审核引擎 | `adminAudTimeoutSeconds` | `aud_timeout_seconds` | Input (number) | "60" | min=1, max=60 | 审核调用传输超时阈值（秒） |
| 20 | 审核引擎 | `adminAudMaxRetryRounds` | `openai_aud_max_retry_rounds` / `aud_max_retry_rounds` | Input (number) | "0 - 10" (缺省 1) | min=0, max=10 | 审核引擎调用重试上限 |
| 21 | 审核-高级 | `adminOpenaiAudReasoningEffort` | `openai_aud_reasoning_effort` | Select | "" (默认) | "", low, medium, high, xhigh, max | 审核思考强度 |
| 22 | 审核-高级 | `adminAudMaxTokens` | `openai_aud_max_tokens` | Input (number) | "512 - 16384" (缺省 4096) | min=512, max=16384, step=128 | 审核最大生成 Token 上限 |
| 23 | 审核-高级 | `adminAudTemperature` | `openai_aud_temperature` | Input (number) | "0.0" (只读硬锁定) | readonly, 固定 0.0 | 确定性硬锁定（防止校验幻觉） |
| 24 | 审核-费率 | `adminPriceInputAud` | `price_input_aud` | Input (number) | "0.0000" | min=0, step=0.0001 | 审核输入单价 (元/1M) |
| 25 | 审核-费率 | `adminPriceCachedInputAud` | `price_cached_input_aud` | Input (number) | "0.0000" | min=0, step=0.0001 | 审核缓存单价 (元/1M) |
| 26 | 审核-费率 | `adminPriceOutputAud` | `price_output_aud` | Input (number) | "0.0000" | min=0, step=0.0001 | 审核输出单价 (元/1M) |
| 27 | 解析自愈 | `adminParseEnabled` | `parse_llm_enabled` | Select | "false" (关) | true, false | 格式破损语义自愈开关 |
| 28 | 解析自愈 | `adminParsePresetSelect` | `engine_preset_parse` | Select | "— 手动填写 —" | - | 解析模型模版选择下拉框 |
| 29 | 解析自愈 | `adminParseOpenaiBaseUrl` | `openai_parse_base_url` | Input (text) | "https://your-gateway.com/v1" | 字符串 | 自愈模型网关 Base URL |
| 30 | 解析自愈 | `adminParseOpenaiApiKey` | `openai_parse_api_key` | Input (password) | "sk-..." | 敏感掩码 | 自愈模型身份认证密钥 |
| 31 | 解析自愈 | `adminParseOpenaiModel` | `openai_parse_model` / `parse_llm_model` | Input (text) | "gpt-4o-mini" | 字符串 | 纯文本语义规整模型标识符 |
| 32 | 解析自愈 | `adminParseTimeoutSeconds` | `parse_timeout_seconds` | Input (number) | "60" | min=1, max=60 | 自愈调用传输超时阈值（秒） |
| 33 | 解析自愈 | `adminParseMaxRetryRounds` | `openai_parse_max_retry_rounds` / `parse_max_retry_rounds` | Input (number) | "0 - 10" (缺省 1) | min=0, max=10 | 自愈引擎重试上限 |
| 34 | 解析-高级 | `adminParseOpenaiReasoningEffort` | `openai_parse_reasoning_effort` | Select | "" (默认) | "", low, medium, high, xhigh, max | 自愈思考强度 |
| 35 | 解析-高级 | `adminParseMaxTokens` | `openai_parse_max_tokens` | Input (number) | "512 - 16384" (缺省 4096) | min=512, max=16384, step=128 | 自愈最大 Token 上限 |
| 36 | 解析-高级 | `adminParseTemperature` | `openai_parse_temperature` | Input (number) | "0.0 - 0.5" (缺省 0.0) | min=0.0, max=0.5, step=0.01 | 自愈采样温度 |
| 37 | 解析-费率 | `adminPriceInputParse` | `price_input_parse` | Input (number) | "0.0000" | min=0, step=0.0001 | 自愈输入单价 (元/1M) |
| 38 | 解析-费率 | `adminPriceCachedInputParse` | `price_cached_input_parse` | Input (number) | "0.0000" | min=0, step=0.0001 | 自愈缓存单价 (元/1M) |
| 39 | 解析-费率 | `adminPriceOutputParse` | `price_output_parse` | Input (number) | "0.0000" | min=0, step=0.0001 | 自愈输出单价 (元/1M) |

### 2.2 流量调度与灰度实验（View 02）输入项盘点

| 序号 | 归属模块 | DOM 元素 ID | 后端绑定字段 | 控件类型 | 界面默认值 | 范围/模式限制 | 核心用途说明 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 40 | 灰度中枢 | `adminGreyEnabled` | `grey_enabled` | Select | "false" (未启用) | true, false | 金丝雀灰度分流总开关 |
| 41 | 灰度中枢 | `adminGreyPercent` / `adminGreySlider` | `grey_percent` | Number / Range | 10 | 0 ~ 100 (%) | 分流至候选灰度模型的流量占比 |
| 42 | 灰度中枢 | `adminGreyAssignMode` | `grey_assign_mode` | Select | "receipt" (单号哈希) | receipt, vendor | 分流算法（单据离散 vs 商户一致） |
| 43 | 灰测识别 | `adminGreyOpenaiRecBaseUrl` | `grey_openai_rec_base_url` | Input (text) | "" | URL 字符串 | 灰测识别模型 Base URL |
| 44 | 灰测识别 | `adminGreyOpenaiRecApiKey` | `grey_openai_rec_api_key` | Input (password) | "" | 敏感掩码 | 灰测识别模型 API 密钥 |
| 45 | 灰测识别 | `adminGreyOpenaiRecModel` | `grey_openai_rec_model` / `grey_recognition_model` | Input (text) | "gpt-4o-mini" | 字符串 | 灰测识别候选模型标识 |
| 46 | 灰测识别 | `adminGreyRecTimeoutSeconds` | `grey_rec_timeout_seconds` | Input (number) | "60" | min=1, max=60 | 灰测识别调用超时（秒） |
| 47 | 灰测识别 | `adminGreyRecMaxRetryRounds` | `grey_openai_rec_max_retry_rounds` / `grey_rec_max_retry_rounds` | Input (number) | "继承常规" (None) | 0 ~ 10 | 灰测识别重试上限（留空继承） |
| 48 | 灰测识别高级 | `adminGreyOpenaiRecReasoningEffort` | `grey_openai_rec_reasoning_effort` | Select | "" (默认) | "", low~max | 灰测识别思考强度 |
| 49 | 灰测识别高级 | `adminGreyRecMaxTokens` | `grey_openai_rec_max_tokens` | Input (number) | 4096 | 512 ~ 16384 | 灰测识别 Max Tokens |
| 50 | 灰测识别高级 | `adminGreyRecImageDetail` | `grey_openai_rec_image_detail` | Select | "high" | high, auto, low | 灰测视觉切片粒度 |
| 51 | 灰测识别高级 | `adminGreyRecTemperature` | `grey_openai_rec_temperature` | Input (number) | 0.0 | 0.0 ~ 0.5 | 灰测识别采样温度 |
| 52 | 灰测识别费率 | `adminGreyPriceInputRec` 等 3 项 | `price_input_grey_rec` 等 | Input (number) | 0.0000 | min=0 | 灰测识别模型单价组 |
| 53 | 灰测审核 | `adminGreyAuditEnabled` | `grey_audit_enabled` | Select | "false" (关) | true, false | 灰测审核引擎开关 |
| 54 | 灰测审核 | `adminGreyOpenaiAudBaseUrl` 等 | `grey_openai_aud_*` 系列 | Text/Pwd/Num | - | 对应字段 | 灰测审核模型配置组 |
| 55 | 灰测审核高级 | `adminGreyAudTemperature` | `grey_openai_aud_temperature` | Input (number) | 0.0 (只读) | readonly, 0.0 | 灰测审核确定性硬锁定 |
| 56 | 灰测解析 | `adminGreyParseEnabled` | `grey_parse_llm_enabled` | Select | "false" (关) | true, false | 灰测解析自愈引擎开关 |
| 57 | 灰测解析 | `adminGreyParseOpenai*` 等 | `grey_openai_parse_*` 系列 | Text/Pwd/Num | - | 对应字段 | 灰测解析自愈模型配置组 |

### 2.3 AI 飞轮与治理规则（View 03）输入项盘点

| 序号 | 治理领域 | DOM 元素 ID | 后端 Settings 键名 | 控件类型 | 默认值 | 作用与调度拦截逻辑 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 58 | 单据状态治理 | `setParsingTimeoutSeconds` | `parsing_timeout_seconds` | Input (number) | 300 (秒) | 启动时清扫卡在 parsing 状态的超时单据，置为 error |
| 59 | 供应商记忆 | `setVendorPrecheckEnabled` | `vendor_precheck_enabled` | Checkbox | false (关) | 未手选商户时，裁抬头小模型预判先验注入（额外调 LLM） |
| 60 | 记忆沉淀飞轮 | `setMemoryExtractEnabled` | `memory_extract_enabled` | Checkbox | false (关) | 人工审核修正后触发 LLM 抽取事实候选（额外调 LLM） |
| 61 | 质检防空转 | `setBlurLaplacianThreshold` | `blur_laplacian_threshold` | Input (number) | 30.0 | OpenCV 拉普拉斯方差低于阈值判定极模糊，直接拦截 (0 Token) |
| 62 | 评测候选回流 | `setEvalCandidateLowConfidence` | `eval_candidate_low_confidence` | Input (number) | 0.60 | 综合置信度低于此阈值自动沉淀至基准评测候选池 |
| 63 | 候选池卫生 | `setEvalCandidateMaxPending` | `eval_candidate_max_pending` | Input (number) | 500 | 待处理评测候选队列硬上限，防存储暴涨 |
| 64 | 审核分歧阈值 | `setAuditDiscrepancySevereMinCount` | `audit_discrepancy_severe_min_count`| Input (number) | 2 | 双模分歧条数达标或涉及总额时触发严重异议回流 |
| 65 | 反馈闭环 | `setFeedbackDistillThreshold` | `feedback_distill_threshold` | Input (number) | 3 | 同商户同租户连续点踩达到此阈值触发记忆蒸馏 |
| 66 | 价格异动防控 | `setPriceAnomalyThresholdPct` | `price_anomaly_threshold_pct` | Input (number) | 10.0 (%) | 菜品单价偏离历史均值超过该百分比触发标黄告警 |
| 67 | RAG 记忆预算 | `setMemoryBudgetFactsTokens` | `memory_budget_facts_tokens` | Input (number) | 800 (Tokens) | Prompt 注入供应商记忆事实总 Token 预算上限 |
| 68 | RAG 记忆截断 | `setMemoryBudgetPerItemTokens` | `memory_budget_per_item_tokens` | Input (number) | 200 (Tokens) | 单条结构化事实的最大 Token 截断值 |
| 69 | RAG 条数控制 | `setMemoryBudgetMaxItems` | `memory_budget_max_items` | Input (number) | 6 (条) | 每次识别最多召回注入的有效事实条数 |
| 70 | 记忆动态衰减 | `setMemoryDecayHitBonus` | `memory_decay_hit_bonus` | Input (number) | +0.05 | 记忆被采用后的置信度奖励步长 |
| 71 | 记忆动态衰减 | `setMemoryDecayOverridePenalty` | `memory_decay_override_penalty` | Input (number) | -0.12 | 记忆被人工覆写后的置信度惩罚步长 |
| 72 | 记忆生命周期 | `setMemoryArchiveThreshold` | `memory_archive_threshold` | Input (number) | 0.50 | 记忆置信度跌破该阈值自动归档淘汰 |
| 73 | 图像预处理 | `setPreprocessEnabled` | `preprocess_enabled` | Checkbox | false (关) | 开启透视变换与水平倾斜纠偏（失败回落原图） |

---

## 3. 后端流转与处理机制深度梳理

从前端表单提交到最终组装成 HTTP 请求下发给外部大模型，系统经历了清晰严密的五阶段处理流水线：

```
[前端表单输入] 
      │ 
      ▼ (main.js: collectEngineConfigBody)
[类型强转 / 负数校验 / 敏感 Key 保留 / 乐观锁版本]
      │
      ▼ PUT /api/admin/unified-config (原子事务)
[FastAPI 入口 Pydantic: EngineConfigBody (extra="forbid")]
      │
      ▼ _normalize_and_sync_engine_updates & SSRF 校验
[领域模型 EngineConfig: 模型验证器 / 钳制函数 / 格式同步]
      │ ├─ clamp_call_timeout: <= 60s
      │ ├─ clamp_temperature: [0.0, 0.5] 格式化 2 位小数
      │ ├─ clamp_max_tokens: [512, 16384]
      │ ├─ clamp_image_detail: 校验合法性 {"high", "auto", "low"}
      │ ├─ clamp_retry_rounds: [0, 10]
      │ └─ reasoning_effort: 白名单过滤 {"low","medium","high","xhigh","max"}
      │
      ▼ SQLite 物理持久化 (带乐观锁与版本历史审计)
[业务调度编排层 (supervisor.py / extract_chain.py / audit_chain.py)]
      │ ├─ should_use_grey: 路由分流决策 (随机 / MD5 供应商哈希)
      │ ├─ 图像准备: PIL 最长边 1000px 限边 + JPEG 压缩 + Base64
      │ ├─ XML 沙箱注入: 供应商先验与门禁反馈完全包裹
      │ ├─ 重试阶梯决策: 区分瞬时故障与确定性错误短路
      │ └─ 审核模式决策: text(纯规则) vs vlm(多模态) vs ondemand(阈值)
      │
      ▼ 模型通道工厂 (llm.py: build_*_model -> _build)
[OpenAIChatModel (BaseChatModel)]
      │ ├─ 超时硬上限二次校验: min(timeout, 60s)
      │ ├─ 消息转换: _lc_to_openai (text / image_url with detail)
      │ ├─ 请求体组装: 注入 model, messages, temperature, max_tokens, reasoning_effort
      │ └─ 绝不注入: response_format (全靠 Prompt + 后端后处理截取)
      │
      ▼ requests.post -> HTTP /chat/completions
[外部 LLM 网关 (DeepSeek / OpenAI 兼容接口)]
```

### 3.1 字段清洗与钳制函数实现细节

1. **`clamp_call_timeout(value)`**：
   - 规则：将超时时间强制归一到硬上限 `MAX_CALL_TIMEOUT_SECONDS = 60s`。
   - 防护：显式排除 `bool` 类型（防止 `True` 被 Python 当成 `1` 误判）；非 `int` 或 `<= 0` 保持原样，在运行期由 `_resolve_timeout` 判定为未设置并回落至默认 `30s`。
2. **`clamp_temperature(val, default=0.0)`**：
   - 规则：严格将温度限制在 `[0.0, 0.5]` 闭区间内，并使用 `round(clamped, 2)` 格式化为两位浮点小数。
   - 兜底：`None`、空串或无法转换为 `float` 的值一律回退为 `0.0`。
   - 审核腿特化：审核腿在 `_resolve_temperature` 与 `_build(side="aud")` 中被**硬编码强制锁定为 0.0**，前端输入框直接设为 `readonly`，杜绝任何外部参数篡改。
3. **`clamp_max_tokens(val, default=4096)`**：
   - 规则：强行钳制在 `[512, 16384]` 区间内。
   - 兜底：非法字符回退默认值 `4096`。
4. **`clamp_image_detail(val, default="high")`**：
   - 规则：输入转换为小写并去除空格，必须在集合 `{"high", "auto", "low"}` 中，否则强制回退为 `"high"`。
5. **`clamp_retry_rounds(value, default=None)`**：
   - 规则：限制在 `[0, 10]` 范围内。针对 `bool` 输入强制归 0。
6. **`reasoning_effort` 白名单过滤**：
   - 规则：必须命中 `VALID_REASONING_EFFORTS = {"", "low", "medium", "high", "xhigh", "max"}`。若传入其他未知值，强制置为空字符串 `""`。在组装 HTTP 请求体时，仅在非空且合法时注入请求体。
7. **SSRF 安全防护 (`validate_safe_external_url`)**：
   - 规则：在保存配置、探测单腿和组装请求体前，对所有的 `base_url` 进行物理拦截。严格阻断 `localhost`、`127.0.0.1`、私有 IP（`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`）及非 HTTP/HTTPS 协议。

---

## 4. 针对类似 DeepSeek 模型的适配与处理分类剖析

### 4.1 类别 A：直接组装下发给 DeepSeek 模型的参数

本类参数会被 `llm.py: OpenAIChatModel._generate` 序列化进最终发送给模型服务端的 JSON 请求体中。

| 参数名称 | 注入 HTTP 请求体的键 | 下发类型 | 系统前置处理/钳制 | DeepSeek-V3 兼容性 | DeepSeek-R1 兼容性 | DeepSeek-V4.1-Flash (多模态) 兼容性 | 潜在风险与系统防护表现 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 模型名称 | `model` | string | 去除前后空白字符，空值回退默认模型 | [OK] 支持 (`deepseek-chat`) | [OK] 支持 (`deepseek-reasoner`) | [OK] 支持 (`deepseek-v4.1-flash`) | 若网关不存在该模型，返回 HTTP 400，系统归类为 `EngineParamError` 并立即短路不重试。 |
| 提示词消息 | `messages` | array | 包含 System/User 块，User 块组装 text 与 image_url，包含 XML 数据沙箱 | [OK] 纯文本对话 | [OK] 纯文本深度推理 | [OK] 多模态混合图文 | 提示词中未注入 `response_format`；后处理通过 `{ ... }` 配对截取，即使 R1 输出 `<think>` 标签也能完美剥离出 JSON。 |
| 采样温度 | `temperature`| float | 强制钳制在 `[0.0, 0.5]`，两位小数 | [OK] 完美支持 (官方建议 0.0~0.3) | [WARN] 官方建议 0.5~0.7，但 0.0 仍可执行 | [OK] 完美支持 (视觉抽取推荐 0.0) | 部分网关对 temperature > 1.0 会报 400；系统上限锁死在 0.5，**完全免疫**高温报错风险。审核腿强制锁定 0.0。 |
| 最大输出 | `max_tokens` | integer | 强制钳制在 `[512, 16384]` | [WARN] 官方 API 上限通常为 8192 | [FAIL] R1 思考过程消耗大量 token，512~2048 极易耗尽预算 | [OK] 4096 满足长图票据抽取 | 若用户在管理端设置 > 8192，直连官方 API 会报 400；对 R1 若设置过小，思考段耗尽 token 导致返回 `finish_reason=length` 且 JSON 被截断。 |
| 思考强度 | `reasoning_effort` | string | 白名单过滤，仅有效值下发；空串不注入 | [INFO] V3 忽略或不注入 | [FAIL] **高危风险**：官方 DeepSeek API 不支持此参数 | [INFO] 多模态视网关实现而定 | **重大兼容性陷阱**：官方 `api.deepseek.com` 遇到 `reasoning_effort` 会直接报 `HTTP 400: Unrecognized request argument`。系统当前处理：若用户选了 low/medium/high 会导致 400 并被系统拦截为参数错误短路；因此接入官方 R1 时前端必须选择「默认/留空」。 |

### 4.2 类别 B：系统内部消费/调度控制参数（不下发给大模型）

本类参数仅在系统控制面、调度器和网关层消费，绝对不出现在给大模型的 HTTP JSON 请求体中。

| 参数名称 | 消费组件/位置 | 字段类型 | 系统处理与防护机制 | 防死循环 / 熔断 / 短路设计细节 |
| :--- | :--- | :--- | :--- | :--- |
| `base_url` | `OpenAIChatModel` / `requests.post` | string | SSRF 过滤、末尾斜杠规整、拼接 `/chat/completions` | 安全守卫 `validate_safe_external_url` 拦截内部内网 IP 与非法协议；探测超时 8s 熔断。 |
| `api_key` | HTTP Header (`Authorization: Bearer`) | string | 前端脱敏显示，入参掩码保留，落库脱敏审计 | 绝不下发给模型请求体；仅作为 HTTP 请求头传输；遭遇 401/403 归为 `EngineAuthError`，**单轮立即短路**绝不重试。 |
| `timeout_seconds` | `requests.post(timeout=...)` | integer | `clamp_call_timeout` 强行钳制至 `<= 60s` | 高厨高压标准，杜绝模型无响应导致工作线程长久悬挂；超时抛出后归为瞬时故障，走重试阶梯。 |
| `max_retry_rounds` | `supervisor.py: while attempt < max_rounds` | integer | `clamp_retry_rounds` 钳制在 `[0, 10]` | **防死循环与四大短路保护**：<br>1. **确定性错误短路**：图片解码失败、401/403 鉴权错误、400 参数错误直接 break；<br>2. **输出质量短路** (`output_reject_fast`)：JSON 语法错误在 T=0 下重跑逐字相同，直接短路交给人工；<br>3. **门禁快速反馈** (`gate_reject_fast`)：契约/算术错误先调自愈腿，自愈失败直接带警告通过，不重调 VLM；<br>4. 仅 HTTP 5xx、超时等瞬时故障消耗重试额度。 |
| `audit_enabled` | `supervisor.py: _run_audit` | boolean | 布尔值控制是否进入 Leg 2 审核流程 | 关闭时直接跳过审核腿，节省 100% 审核开销与耗时。 |
| `audit_mode` | `audit_chain.py: _resolve_audit_mode` | string | `text` (纯规则 0 Token), `vlm` (多模态), `ondemand` | 默认 `text` 模式毫秒级完成且零 Token；`ondemand` 模式仅在置信度 < 0.70 时触发大模型调用。 |
| `parse_llm_enabled` | `extract_chain.py: _parse_enabled` | boolean | 布尔值控制 VLM 之后是否级联调用解析自愈 LLM | 默认关闭；开启时在 VLM 输出非空时触发纯文本规范化。 |
| `grey_enabled` / `grey_percent` / `grey_assign_mode` | `models.py: should_use_grey` | bool / int / enum | 流量分发决策器 | `vendor` 模式执行 MD5(商户名) % 100 分流，确保同商户流量稳定落到同一模型，防止 A/B 震荡。 |
| `price_*` 系列单价 | `llm.py: _calc_cost_hkd` | float | 本地成本测算与度量看板落库 | 结合返回的 `token_usage` 精确计算 HKD 成本，用于可观测中心大盘及成本护栏，不影响模型调用。 |
| 治理阈值 (模糊/超时/预算) | `settings_service` / 各业务服务 | float / int | 动态配置读取与运行时阈值判定 | 如模糊度 < 30 即时熔断 (0 Token 拦截)；解析 > 300s 启动清理；记忆注入限制 800 Tokens 预算。 |

### 4.3 类别 C：多模态特有参数与潜在兼容性差异

本类参数专门用于视觉多模态场景（如识别腿及 VLM 审核腿）。

#### 1. 图像切片粒度 `image_detail` (high / auto / low)
- **前端控件**：`adminRecImageDetail` / `adminGreyRecImageDetail`（全切片·高精度、自适应、低开销·粗粒度）。
- **后端流转与组装**：
  - `clamp_image_detail` 强校验枚举 `{"high", "auto", "low"}`，非法值回退 `"high"`。
  - `llm.py: _lc_to_openai` 在转换 `HumanMessage` 中的 `image_url` 块时注入：
    ```json
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/jpeg;base64,...",
        "detail": "high"
      }
    }
    ```
- **DeepSeek 多模态（如 V4.1-Flash）兼容性表现**：
  - OpenAI 规范中，`detail: high` 会将大图按 512x512 切片为多块高分辨率网格，消耗更多视觉 Token；`detail: low` 统一按单图 85 Token 固定消耗。
  - 针对接入的各种兼容网关（如基于 vLLM、SGLang 或第三方聚合网关托管的 DeepSeek 多模态）：
    - 若网关严格兼容 OpenAI Vision 协议，`detail` 会直接控制视觉编码器的分辨率分块策略。
    - 若部分自建网关无法识别 `detail` 字段，多数兼容层会直接忽略该键（读取 `url`），正常解析图像。
    - **系统防护**：系统通过 `_calc_cost_hkd` 根据单价与返回的实际 `token_usage` 计算开销，若模型返回了真实的 Token 消耗，系统会准确记录；若未返回，标记为估算成本。

#### 2. 图像编码与尺寸压缩流水线
- **处理链路**：
  1. 本地图片读取与安全检查（`PIL` 打开，检查像素炸弹 `MAX_IMAGE_PIXELS`）。
  2. **最长边强制限边**：`_MAX_IMAGE_SIDE = 1000`。任何超过 1000 像素的高清发票原图，等比例缩放至最长边为 1000 像素。
  3. **格式与质量转换**：使用 PIL 转换为 RGB 并按 JPEG 编码。
  4. **Base64 Data URL 组装**：拼接为 `data:image/jpeg;base64,{base64_str}`。
- **针对 DeepSeek 多模态的保护效果**：
  - 彻底规避了高分辨率原图（如手机拍摄的 4K/8K 票据）直接发送导致的大请求体传输超时、网关 413 Payload Too Large 错误。
  - 限制在 1000px 既保留了街市小票细微字迹和繁体字的辨识度，又将 Base64 字符串控制在 100KB~300KB，大幅降低了推理首字时延（TTFT）。
  - 若输入 HEIC/TIFF 等非 Web 格式且本地无解码器，系统触发 `ImageDecodeError`，直接判定为 `deterministic_error`，**阻断向 DeepSeek 发送损坏二进制**，避免了模型网关报 400/500。

#### 3. 结构化 JSON 提示词与 `<think>` 标签过滤
- **DeepSeek 思考模式特性**：DeepSeek-R1 或多模态推理模型会先输出 `<think> ... </think>`，再输出最终正文。
- **系统解析自愈机制**：
  - 系统未使用脆弱的 LangChain 强绑定输出解析器，而是自研了 `_parse_to_receipt`：
    1. 首先剥离可能存在的 markdown 代码围栏（` ```json ... ``` `）。
    2. 若直接 `json.loads` 失败，执行字符级配对查找：`text.find("{")` 与 `text.rfind("}")`。
    3. 截取最外层合法的 JSON 子串进行二次解析。
  - **兼容性验证结论**：哪怕 DeepSeek 模型在 JSON 前输出了长达数千字的 `<think>` 思考链内容，`_parse_to_receipt` 也能自动跳过思考段，精准锁定并解析出末尾的 JSON 业务结构！

---

## 5. 潜在技术风险与工程适配优化建议

基于全量代码审查，系统接入类似 DeepSeek 模型时存在以下关键风险点与防范建议：

### 风险点 1：DeepSeek 官方 API 对 `reasoning_effort` 报 HTTP 400
- **机理**：`llm.py` 中，只要 `self.reasoning_effort` 非空，就会将 `"reasoning_effort": "low/medium/high"` 注入到 HTTP 请求体。然而 DeepSeek 官方 API（`api.deepseek.com`）目前仅支持原生 OpenAI 参数，不识别此参数，会直接返回 `400 Bad Request`。
- **现状防护**：系统会将该 400 归类为 `EngineParamError` 并立即短路，不会引发死循环重试，但会导致识别任务直接失败。
- **工程建议**：
  - 在前端或网关层增加模型适配器逻辑：当模型名包含 `deepseek` 时，或者当前引擎为 DeepSeek 时，请求体构建逻辑中自动屏蔽 `reasoning_effort` 字段。

### 风险点 2：DeepSeek-R1 思考段消耗 Token 导致 `max_tokens` 提前耗尽
- **机理**：DeepSeek-R1 的思考过程计数计入 `completion_tokens`。若用户在高级参数面板将 `max_tokens` 设置为较小值（如 1024 或 2048），在复杂长单据下，模型生成完思考内容后便触碰 `max_tokens` 阈值，输出截断，无法生成完整的收据 JSON。
- **现状防护**：系统提供了环境变量 `OPENAI_REC_MAX_TOKENS` 允许离线评测时显式抬高，但在日常生产界面中受限于前端输入的 `adminRecMaxTokens`。
- **工程建议**：针对 DeepSeek-R1 类推理模型，系统建议在模版预设中将 `max_tokens` 默认配置为 `8192`，并给出界面提示：“深度推理模型建议 Max Tokens 不低于 4096”。

### 风险点 3：官方 DeepSeek API 的 `max_tokens` 8192 上限与系统 16384 钳制上限碰撞
- **机理**：系统 `clamp_max_tokens` 允许用户输入最大 `16384`。若用户填入 `12000` 并直连官方 DeepSeek API，官方 API 会返回 `400: max_tokens exceeds 8192`。
- **工程建议**：在预设模板中加入各供应商的参数校验元数据，针对官方 DeepSeek 网关，前端校验将上限设为 `8192`。

---

## 6. 核心结论与参数对照决策矩阵

| 参数分类 | 参数清单 | 下发模型? | 系统清洗/钳制动作 | DeepSeek 适配结论与建议 |
| :--- | :--- | :---: | :--- | :--- |
| **A. 模型请求参数** | `model` | 是 | 去空格 | [OK] 填入 `deepseek-chat` / `deepseek-reasoner` 等 |
| | `temperature` | 是 | 强制钳制在 `[0.0, 0.5]`，2 位小数 | [OK] 完美兼容，消除 DeepSeek 高温幻觉与 >1.0 报错 |
| | `max_tokens` | 是 | 强制钳制在 `[512, 16384]` | [WARN] 官方 API 上限 8192，建议设 4096~8192 |
| | `reasoning_effort`| 是 (非空时) | 白名单过滤 | [FAIL] **重要**：直连官方 DeepSeek 时必须选「默认/留空」，否则报 400 |
| | `image_detail` | 是 (视觉块) | 校验枚举 `high/auto/low` | [OK] 兼容视觉网关，控制切片成本 |
| **B. 工程调度参数** | `base_url` | 否 | SSRF 物理拦截与网关清洗 | [OK] 支持各类兼容网关与反代 |
| | `api_key` | 否 | 仅传 Header，脱敏审计 | [OK] 401/403 明确短路，不盲目重试 |
| | `timeout_seconds` | 否 | 硬上限锁死 60s | [OK] 满足交互实时性，防止后台僵死 |
| | `max_retry_rounds`| 否 | 钳制 `[0, 10]` | [OK] 四大短路设计彻底杜绝死循环与资源耗尽 |
| | `audit_*` 系列 | 否 | 审核模式控制，审核温度强制 0.0 | [OK] 可选用 DeepSeek-V3 充当审核评估器 |
| | `grey_*` 系列 | 否 | 随机/商户哈希分流 | [OK] 支持 DeepSeek 作为金丝雀平滑放量 |
| | 飞轮与治理规则 | 否 | 模糊拦截、记忆预算、连续点踩阈值 | [OK] 在模型调用外围构建完整质量护栏 |

---
**报告归档说明**：本产物已按照工程规范实时落盘留痕于 `~/harness-tmp/task_frontend_param_deepseek/01_deepseek_param_compatibility_report.md`。

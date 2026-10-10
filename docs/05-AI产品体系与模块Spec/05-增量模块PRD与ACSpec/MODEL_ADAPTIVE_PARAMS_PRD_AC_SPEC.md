# 多模型自适应参数与智能降级自愈引擎规格说明书 (Model-Adaptive Parameters & Self-Healing Fallback Spec)

**文档版本**：v1.0.0  
**所属阶段**：Phase 1: Requirements & Spec  
**发布时间**：2026-10-06  
**密级状态**：内部工程规范 (Internal Engineering Specification)  
**文档编号**：SPEC-20261006-MODEL-ADAPTIVE-PARAMS  

---

## 1. 业务背景与产品价值 (Executive Summary & Business Value)

### 1.1 业务痛点与现状分析
在香港餐饮进货单据与手写收据智能识别系统的实际运行与交付中，系统依托双轨及三腿协同架构（Leg 1: 视觉识别提取、Leg 2: 多模态/确定性交叉审核、Leg 3: 语义破损自愈解析）。为了赋予管理员和算法工程师根据成本与性能极致调优的能力，系统在管理端（`section#tab-engine`）引入了高级工程参数面板（`div#adminAdvBody_rec`、`div#adminAdvBody_aud`、`div#adminAdvBody_parse`、`div#adminAdvBody_grey_rec` 等），开放了以下关键调优维度：
1. **思考强度 (`reasoning_effort`)**：针对新一代深度推理模型调节思维链深度（low / medium / high / xhigh / max）；
2. **生成预算上限 (`max_tokens`)**：控制模型单次调用的最大 Token 预算（512 ~ 16384）；
3. **视觉切片粒度 (`image_detail`)**：控制多模态视觉网格切片精细度（high / auto / low）；
4. **采样温度 (`temperature`)**：调节模型输出随机性（0.0 ~ 0.5，审核腿硬锁定 0.0）。

然而，当前底层大模型生态严重碎片化，各主流模型提供商（如 DeepSeek 官方 API、SiliconFlow/OpenRouter 等中转网关、OpenAI 官方、阿里云 DashScope 等）对 OpenAI 兼容参数的解析规范存在显著差异：
- **DeepSeek 官方 API 严苛拒收**：直连 DeepSeek 官方网关（`api.deepseek.com`）时，若 HTTP 请求体携带 `reasoning_effort`，官方网关直接返回 `HTTP 400: Unrecognized request argument: reasoning_effort`；
- **Max Tokens 物理上限碰撞**：DeepSeek 官方 API 规定 `max_tokens` 最大不得超过 8192，若用户在前端配置为 12000，官方网关直接返回 `HTTP 400: max_tokens exceeds 8192`；
- **OpenAI 推理模型参数排他性**：OpenAI `o1` / `o3-mini` 模型强行废弃 `max_tokens` 并改用 `max_completion_tokens`，且禁止传入自定义采样温度（`temperature != 1.0` 会报 400）；
- **非标多模态代理网关阻断**：部分开源推理引擎（如老版本 vLLM / SGLang 或自定义反向代理）在解析 `image_url` 块时，若携带 `detail` 字段会因 Pydantic 校验失败报 400。

**致命后果**：系统原先将所有 HTTP 400 归类为 `EngineParamError(category="param")`，在 `supervisor.py` 中被 `_is_deterministic_engine_error` 判定为“不可重试的确定性配置错误”，直接熔断跳出重试阶梯，导致识别任务瞬间失败并强行切入手工输入界面，给餐厅日常验货带来极大的操作阻断与人工负担。

### 1.2 核心业务目标与预期收益
1. **零感知平滑热切 (Seamless Multi-Model Switching)**：运维人员与店长在界面切换模型或模版时，无需记忆各模型的 API 规范；后端自适应适配各模型特性，消除参数配置门槛。
2. **双层韧性防御护栏 (Two-Tier Defensive Resilience)**：
   - **Layer 1（静态前置适配）**：在发送网络请求前，根据模型名称与 Base URL 自动识别模型画像，主动剥离不兼容参数、钳制非法超限值、转换规范字段。
   - **Layer 2（动态就地自愈）**：面对未收录的新型网关或非标代理，若上游返回参数不支持的 HTTP 400，核心通道能智能解析错误模式，自动剥离不兼容参数并就地执行同步自愈重试，保证流水线 100% 连贯性。
3. **消除无效熔断，提升系统可用性**：彻底消除由于 `reasoning_effort`、`max_tokens` 超限等边缘参数导致的虚假“确定性配置错误”，单据自动化处理率与鲁棒性显著提升。
4. **前端交互透明可控**：前端提供模型感知动态提示（纯文本 `[INFO]`），展示该模型特性及合法输入范围，提升交互确定性。

---

## 2. 多模型参数特征与差异矩阵 (Multi-Model Parameter Matrix)

下表系统梳理了主流模型厂商与关键架构系列在核心高级工程参数上的真实行为与报错特征：

| 模型厂商 / 架构系列 | 典型代表模型 | 思考强度 (`reasoning_effort`) | Max Tokens 处理与上限 | 采样温度 (`temperature`) | 视觉切片 (`detail`) | 典型 400 报错体模式 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **DeepSeek 官方 API** (`api.deepseek.com`) | `deepseek-chat`<br>`deepseek-reasoner` (R1) | [FAIL] **严格不支持**<br>下发即报 400 | [WARN] **硬上限 8192**<br>若 > 8192 则报 400 | [OK] 支持 0.0~2.0<br>系统锁 0.0~0.5 完美契合 | [INFO] 纯文本模型<br>不支持 image_url | `Unrecognized request argument: reasoning_effort`<br>`max_tokens must be less than or equal to 8192` |
| **DeepSeek 中转 / 开源网关** (SiliconFlow / vLLM) | `deepseek-ai/DeepSeek-V3`<br>`deepseek-ai/DeepSeek-R1` | [INFO] 多数忽略<br>少数报 400 | [OK] 依据后端显存设定<br>通常支持 16384 | [OK] 支持 0.0~1.0 | [INFO] 视部署版本而定 | `Extra inputs are not permitted: reasoning_effort` |
| **DeepSeek 多模态** (兼容代理 / V4.1-Flash) | `deepseek-v4.1-flash`<br>`deepseek-vl2` | [INFO] 视网关而定 | [WARN] 建议 4096~8192<br>思考消耗预算 | [OK] 视觉抽取推荐 0.0 | [OK] 多数支持 high/low<br>极少数老代理报 400 | `detail is not supported in image_url` |
| **OpenAI 推理系列** (官方 API) | `o1`<br>`o1-mini`<br>`o3-mini` | [OK] **原生支持**<br>支持 low/medium/high | [FAIL] **废弃 max_tokens**<br>必须传 max_completion_tokens | [FAIL] **不支持自定义**<br>仅支持默认 1.0 (传 0.0 报 400) | [OK] o1 视觉多模态支持 detail | `Unsupported parameter: 'max_tokens' is not supported... Use 'max_completion_tokens'`<br>`Unsupported parameter: 'temperature'` |
| **OpenAI 常规多模态** (官方 API) | `gpt-4o`<br>`gpt-4o-mini` | [WARN] 官方忽略或报 400<br>仅推理模型消费 | [OK] 支持至 16384 | [OK] 支持 0.0~2.0<br>系统锁 0.0~0.5 表现优异 | [OK] **原生规范定义者**<br>完美支持 high/auto/low | `Unrecognized request argument: reasoning_effort` |
| **Qwen / 阿里百炼** (`dashscope.aliyuncs.com`) | `qwen3-vl-flash`<br>`qwen-vl-max` | [INFO] 不支持但兼容层自动忽略 | [OK] 支持至 8192 | [OK] 支持 0.0~1.0 | [OK] 完美支持 high/low/auto | 极少报错，兼容性好 |
| **Anthropic 兼容网关** (Claude via OneAPI) | `claude-3-5-sonnet`<br>`claude-3-7-sonnet` | [INFO] Claude 原生用 thinking 字典，OpenAI 桥接层若未适配会报 400 | [WARN] 强制要求 max_tokens 且通常 <= 8192 | [OK] 支持 0.0~1.0 | [OK] 桥接层支持 detail | `unknown field: reasoning_effort` |

---

## 3. 双重防御自适应引擎架构设计 (Architecture & Technical Spec)

系统采用 **Layer 1: 静态前置适配** 与 **Layer 2: 动态 400 就地剥离自愈** 的双重防御机制，确保发出的每一个模型请求均合法合规；即便遭遇未知兼容网关阻断，亦能在 50 毫秒内就地自愈，保障主流水线零感知通畅。

```
[上层业务发起调用: supervisor / extract_chain / audit_chain]
                         │
                         ▼
        [OpenAIChatModel._generate (llm.py)]
                         │
                         ▼
        ┌──────────────────────────────────────────────────┐
        │  Layer 1: 静态前置适配 (Static Pre-adaptation)   │
        │  1. 模型画像探测 (classify_model_profile)        │
        │  2. DeepSeek 官方: 剥离 reasoning_effort,        │
        │     钳制 max_tokens <= 8192                      │
        │  3. OpenAI o1/o3: max_tokens 桥接为              │
        │     max_completion_tokens, 剔除 temperature      │
        │  4. 常规多模态: 保持 detail, temperature=0.0     │
        └──────────────────────────────────────────────────┘
                         │
                         ▼
             [初次发起 HTTP POST 请求]
                         │
           ┌─────────────┴─────────────┐
           │                           │
      [HTTP 200]                  [HTTP 400 Bad Request]
           │                           │
           │                           ▼
           │          ┌──────────────────────────────────────────────────┐
           │          │  Layer 2: 动态 400 就地自愈 (Dynamic Fallback)   │
           │          │  1. 错误签名正则匹配 (Error Signature Match)     │
           │          │     - reasoning_effort 不支持                    │
           │          │     - max_tokens 超限 (如 > 8192)                │
           │          │     - max_tokens 需替换为 max_completion_tokens │
           │          │     - temperature 不受支持                       │
           │          │     - detail 不受支持                            │
           │          │  2. 就地剥离 / 钳制对应不兼容字段                │
           │          │  3. 记录 adapted_params 结构化警告日志           │
           │          │  4. 就地重发 HTTP POST (最多重试 2 轮)           │
           │          └──────────────────────────────────────────────────┘
           │                           │
           │            ┌──────────────┴──────────────┐
           │            │                             │
           │       [重试 HTTP 200]             [重试仍 400 或未识别]
           │            │                             │
           ▼            ▼                             ▼
       [正常解析 Content 与 Usage]            [抛出 EngineParamError 短路]
```

### 3.1 Layer 1: 静态前置自适应与请求体智能归一 (Static Pre-adaptation)

在 `llm.py: OpenAIChatModel._generate` 组装 payload 前，执行模型画像识别：

#### 3.1.1 模型画像探测规则 (`classify_model_profile`)
定义纯函数探测模型特征：
```python
def classify_model_profile(model: str, base_url: str) -> dict:
    m = (model or "").strip().lower()
    u = (base_url or "").strip().lower()
    
    is_deepseek_official = ("api.deepseek.com" in u) or (
        ("deepseek" in m) and not any(gw in u for gw in ["siliconflow", "openrouter", "aliyun", "dashscope"])
    )
    is_deepseek_any = ("deepseek" in m) or ("deepseek" in u)
    is_openai_reasoning = m.startswith("o1") or m.startswith("o3") or ("-o1" in m) or ("-o3" in m)
    
    return {
        "is_deepseek_official": is_deepseek_official,
        "is_deepseek_any": is_deepseek_any,
        "is_openai_reasoning": is_openai_reasoning,
    }
```

#### 3.1.2 静态组装规则 (Static Payload Construction)
1. **`reasoning_effort` 注入逻辑**：
   - 仅当配置了有效的档位（`low`, `medium`, `high`, `xhigh`, `max`）时才考虑下发；
   - **静态前置剔除**：若 `is_deepseek_official` 为 True，**绝不注入 `reasoning_effort`**（哪怕管理员在前端勾选了 high），静默剥离以杜绝 400 报错。
2. **`max_tokens` 钳制与桥接**：
   - 若 `is_openai_reasoning` 为 True：将键名转换为 `max_completion_tokens`，不传递 `max_tokens`。
   - 若 `is_deepseek_official` 为 True：`max_tokens = min(configured_max_tokens, 8192)`，确保绝对不触发 8192 上限报错。
   - 其余情况：下发标准的 `max_tokens`。
3. **`temperature` 过滤**：
   - 若 `is_openai_reasoning` 为 True：**不注入 `temperature`**（避免 o1/o3 因 temperature=0.0 返回 400）。
   - 其余情况：按原有规范注入 `self.temperature`（审核腿强制 0.0）。
4. **`image_detail`**：
   - 保持在 `image_url` 块中注入 `{"url": "...", "detail": self.image_detail}`。

---

### 3.2 Layer 2: 动态 400 异常捕获与就地参数剥离自愈 (Dynamic Self-Healing)

针对第三方非标聚合网关、自建中转站或未被静态规则命中的模型，当 `requests.post` 返回 `status_code == 400` 时，启动动态捕获与就地自愈。

#### 3.2.1 错误模式签名特征库 (Error Signatures)
系统建立针对大模型网关常见参数报错的正则表达式模式库：

```python
_PARAM_ERROR_SIGNATURES = {
    "strip_reasoning_effort": [
        re.compile(r"reasoning_effort", re.IGNORECASE),
        re.compile(r"unrecognized request argument:?\s*reasoning_effort", re.IGNORECASE),
        re.compile(r"extra inputs are not permitted.*reasoning_effort", re.IGNORECASE),
        re.compile(r"unsupported parameter:?\s*['\"]?reasoning_effort['\"]?", re.IGNORECASE),
    ],
    "strip_temperature": [
        re.compile(r"unsupported parameter:?\s*['\"]?temperature['\"]?", re.IGNORECASE),
        re.compile(r"temperature is not supported", re.IGNORECASE),
        re.compile(r"temperature must be 1", re.IGNORECASE),
    ],
    "bridge_max_completion_tokens": [
        re.compile(r"unsupported parameter:?\s*['\"]?max_tokens['\"]?.*use ['\"]?max_completion_tokens['\"]?", re.IGNORECASE),
        re.compile(r"max_tokens is not supported.*max_completion_tokens", re.IGNORECASE),
    ],
    "clamp_max_tokens_8192": [
        re.compile(r"max_tokens.*exceeds?.*8192", re.IGNORECASE),
        re.compile(r"max_tokens.*less than or equal to 8192", re.IGNORECASE),
        re.compile(r"max_tokens.*must be <= 8192", re.IGNORECASE),
    ],
    "strip_image_detail": [
        re.compile(r"detail is not supported", re.IGNORECASE),
        re.compile(r"unrecognized request argument:?\s*detail", re.IGNORECASE),
        re.compile(r"unexpected.*['\"]?detail['\"]?", re.IGNORECASE),
    ]
}
```

#### 3.2.2 剥离自愈循环算法 (Self-Healing Stripping Algorithm)
- **单次调用允许的最大自愈重试轮次**：`MAX_SELF_HEALING_ROUNDS = 2`。
- **执行流程**：
  1. `requests.post` 返回 `resp.status_code == 400`。
  2. 读取 `err_text = resp.text`。
  3. 依序匹配错误特征：
     - 若命中 `strip_reasoning_effort` 且当前 payload 包含 `reasoning_effort`：从 payload 中 `del payload["reasoning_effort"]`，记录修复动作。
     - 若命中 `bridge_max_completion_tokens` 且 payload 包含 `max_tokens`：`payload["max_completion_tokens"] = payload.pop("max_tokens")`，记录修复动作。
     - 若命中 `clamp_max_tokens_8192` 且 payload.get("max_tokens", 0) > 8192：`payload["max_tokens"] = 8192`，记录修复动作。
     - 若命中 `strip_temperature` 且 payload 包含 `temperature`：`del payload["temperature"]`，记录修复动作。
     - 若命中 `strip_image_detail`：遍历 `payload["messages"]`，将所有 `image_url` 字典中的 `detail` 键剥离，仅保留 `{"url": "..."}`。
  4. 若有至少一项修复动作被执行：
     - 打印标准日志：`[WARN] 检测到上游网关不兼容参数，已自动执行参数自愈剥离并就地重试: {actions}`；
     - 立即在当前代码块内重新执行 `requests.post`；
     - 若重试返回 200，将自愈修补事件记录在 `ChatResult.generations[0].message.response_metadata["adapted_params"]` 中，并上报 `llm_logger`；
     - 返回正常结果，**流水线对外表现为 100% 成功，无需触发外围 Supervisor 重试**！
  5. 若未能匹配任何已知特征，或自愈重试超过 2 次后仍报 400，抛出原有的 `EngineParamError`。

---

## 4. 前端交互与提示优化方案 (Frontend UX & Progressive Guidance)

在前端管理界面 `templates/index.html` 与 `static/js/main.js` 中，优化高级参数折叠面板：

### 4.1 控件与容器布局
涉及的 4 类卡片折叠面板：
1. `adminAdvBody_rec`（常规视觉识别）
2. `adminAdvBody_aud`（常规交叉审核）
3. `adminAdvBody_parse`（语义解析自愈）
4. `adminAdvBody_grey_rec`（灰测视觉识别）

### 4.2 模型联动动态提示 (Model-Aware Dynamic Hints)
在各引擎腿中，监听对应模型输入框（如 `adminOpenaiRecModel`、`adminOpenaiAudModel` 等）的 `input` 与 `change` 事件：
- **DeepSeek 模型**：
  - 检测规则：模型名包含 `deepseek`（忽略大小写）。
  - 在 `Max Tokens` 输入框下方动态展示纯文本提示：
    `<div class="model-trait-hint text-muted">[INFO] DeepSeek 官方 API 最大支持 8192 Tokens（超过将自动钳制为 8192）</div>`
  - 在 `思考强度` 下拉框下方动态展示纯文本提示：
    `<div class="model-trait-hint text-muted">[INFO] DeepSeek 官方 API 无需传思考强度参数；直连官方网关时建议保持「默认」</div>`
- **OpenAI o1 / o3 推理模型**：
  - 检测规则：模型名以 `o1`、`o3` 开头或包含 `-o1-`、`-o3-`。
  - 在 `采样温度` 输入框下方展示提示：
    `<div class="model-trait-hint text-muted">[INFO] OpenAI o1/o3 推理模型由服务商统一管理采样温度，系统将自动适配</div>`
  - 在 `Max Tokens` 输入框下方展示提示：
    `<div class="model-trait-hint text-muted">[INFO] 系统将自动为您桥接 max_completion_tokens 规范</div>`
- **常规多模态模型 (GPT-4o, Qwen-VL 等)**：
  - 隐藏上述特定提示，保留默认简洁界面。

### 4.3 标题栏动态 Badge 标签联动
保持折叠面板头部 Badge 的实时准确同步：
- 缺省状态：`[4096 tokens | high detail | T=0.0]`
- 选择了思考强度（如 `medium`）：自动呈现 `[4096 tokens | high detail | T=0.0 | medium effort]`
- 审核腿硬锁定状态：始终呈现 `[4096 tokens | T=0.0]`

---

## 5. 详细验收标准清单 (Acceptance Criteria, AC-01 至 AC-16)

本清单作为 Phase 2 开发交付、Phase 3 QA 自动化验证及 Phase 4 UAT 验收的唯一法定基准。

### 维度 1：文档与知识资产归档 (Archival)
- [ ] **AC-01 (调研报告双重落盘)**：
  - 完整的前端参数盘点与 DeepSeek 兼容性分析报告已规范落盘至 `~/harness-tmp/task_model_adaptive_params/00_investigation_report.md`；
  - 仓库内已同步沉淀至 `docs/04-AI技术选型与评测/01-AI方案与规格/06-前端引擎参数盘点与DeepSeek兼容性分析报告.md`；
  - 报告内容 100% 无 Emoji，全部采用 `[OK]`, `[WARN]`, `[FAIL]`, `[INFO]` 等纯文本标记。
- [ ] **AC-02 (Spec 规范落盘)**：
  - 本规格说明书已规范落盘至 `~/harness-tmp/task_model_adaptive_params/01_spec.md`；
  - 仓库内已同步归档至 `docs/05-AI产品体系与模块Spec/05-增量模块PRD与ACSpec/MODEL_ADAPTIVE_PARAMS_PRD_AC_SPEC.md`。

### 维度 2：后端静态前置适配 (Static Pre-adaptation)
- [ ] **AC-03 (DeepSeek 官方 API 思考强度静默剥离)**：
  - 当模型 Base URL 指向 `api.deepseek.com` 或模型为 `deepseek-*`（且非三方中转），即使配置了 `reasoning_effort="high"`，`OpenAIChatModel` 发出的 HTTP 请求体中 **绝不包含 `reasoning_effort` 键**。
- [ ] **AC-04 (DeepSeek 官方 API Max Tokens 自动钳制)**：
  - 当模型为 DeepSeek 且配置 `max_tokens > 8192`（例如 12000）时，发出请求的 payload 中 `max_tokens` 自动钳制为 `8192`。
- [ ] **AC-05 (OpenAI o1/o3 推理模型参数桥接)**：
  - 当模型名称命中 `o1` 或 `o3-mini` 时，payload 中自动使用 `max_completion_tokens` 代替 `max_tokens`；且自动剥离 `temperature` 字段。
- [ ] **AC-06 (OpenAI 常规多模态正常传递)**：
  - 当模型为 `gpt-4o` 时，正常携带 `max_tokens`、`temperature` 与 `detail` 参数。

### 维度 3：后端动态 400 就地剥离自愈 (Dynamic Self-Healing)
- [ ] **AC-07 (动态捕获 reasoning_effort 400 并剥离重试成功)**：
  - 模拟外部网关对包含 `reasoning_effort` 的请求返回 HTTP 400（错误信息包含 `Unrecognized request argument: reasoning_effort`）；
  - `OpenAIChatModel` 能够自动捕获该错误，剥离 `reasoning_effort` 字段，并在单次 `_generate` 调用内就地重试；
  - 重试返回 200，调用方正常收到解析后的 JSON 响应，**不抛出任何异常，不中断 Supervisor 流水线**。
- [ ] **AC-08 (动态捕获 max_tokens 超限并降级重试成功)**：
  - 模拟网关返回 `400: max_tokens must be less than or equal to 8192`；
  - 系统自动将 `max_tokens` 修正为 8192 并重试成功。
- [ ] **AC-09 (动态捕获 temperature 不支持并剥离重试成功)**：
  - 模拟网关返回 `400: Unsupported parameter: 'temperature'`；
  - 系统自动剥离 `temperature` 字段并重试成功。
- [ ] **AC-10 (动态捕获 detail 不支持并剥离重试成功)**：
  - 模拟非标视觉网关返回 `400: detail is not supported`；
  - 系统自动从 `image_url` 结构中剔除 `detail` 并重试成功。
- [ ] **AC-11 (自愈重试次数防护与真实错误透传)**：
  - 若网关返回无法识别的 400 错误（如 `400: model_not_found`）或剥离后重试仍然失败，重试轮次不超过 2 轮，并规范抛出 `EngineParamError`。

### 维度 4：可观测性与日志审计 (Observability & Logging)
- [ ] **AC-12 (自愈元数据记录)**：
  - 当发生参数自愈剥离重试时，`llm_logger.record_llm_call` 及决策日志元数据中需清晰记录 `adapted_params` 列表（例如 `["strip_reasoning_effort"]`）。
- [ ] **AC-13 (成本计算连贯性)**：
  - 参数自愈重试成功后，基于最终成功的 token_usage 精确核算成本，不影响大盘计费。

### 维度 5：前端交互与防呆设计 (Frontend UX)
- [ ] **AC-14 (模型变动即时动态提示)**：
  - 在 `adminAdvBody_rec` 面板中，输入 `deepseek-reasoner` 时，界面自动显现 DeepSeek 专属参数说明标签；输入 `o1-mini` 时自动显现 o1 专属提示；输入 `gpt-4o` 时自动恢复默认。
- [ ] **AC-15 (Badge 状态对齐)**：
  - 当修改思考强度、Max Tokens、切片粒度或采样温度时，折叠面板标题栏 Badge 实时同步更新，无闪烁。

### 维度 6：自动化测试与回归保障 (Testing & Verification)
- [ ] **AC-16 (全量单元与集成测试)**：
  - 编写专门的自动化测试套件 `demo/tests/test_model_adaptive_params.py`，完整覆盖 AC-03 至 AC-13 对应场景；
  - 现有测试用例（`test_advanced_engine_params.py`、`test_upstream_error_classification.py`）100% 保持通过，无任何回归缺陷。

---

## 6. 工程规范与红线核对表 (Directives & Guardrails Checklist)

| 规范条目 | 严格要求 | 本 Spec 遵循说明 |
| :--- | :--- | :--- |
| **1. 全局禁止使用 Emoji** | 严禁使用任何 Emoji 表情符号，统一使用纯文本标签 | 界面提示与代码全部使用 `[INFO]`, `[WARN]`, `[OK]`, `[FAIL]`。 |
| **2. Git 操作红线** | 严禁在用户未明确要求时执行 commit/push 等版本控制写操作 | Phase 1 及后续阶段严禁调用 git commit/push。 |
| **3. 数据脱敏标准** | 敏感数值与 API Key 严格掩码 | 报告中 Base URL 与 API Key 均采用标准脱敏示例。 |
| **4. Subagent 闭环验证** | 严禁主 Agent 自行宣布完成，必须派发独立 Subagent | 严格按照四阶段流水线（产品 -> 开发 -> QA -> 验收）推进。 |
| **5. 常驻服务保护** | 严禁终止本地 15010 开发常驻服务 | 所有测试与调试均不打断 background 进程。 |

---
**规格说明书签发确认**：Phase 1 产品规格已正式锁定，准出条件满足，可无缝流转至 Phase 2 开发阶段。

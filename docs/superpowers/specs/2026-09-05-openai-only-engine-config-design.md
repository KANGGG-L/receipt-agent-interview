# 纯 OpenAI 兼容引擎配置重构与过期选项移除设计规范

- 制定日期：2026-09-05
- 模块归属：系统管理 / 算法与引擎配置 (`tab-engine`)
- 状态：APPROVED (待执行实施计划)

---

## 1. 业务痛点与重构动机

1. **历史技术债务堆叠**：系统早期支持本地 CLI（`opencode` / `codebuddy`）及多种非标准通道，界面上长期保留了「识别引擎」、「识别模型」、「解析引擎」、「审核引擎」等下拉选择框，而当前系统已全面完成 API 原生化与 SiliconFlow 默认集成，本地 CLI 已经彻底废弃。
2. **界面认知负荷过重**：原有界面在下拉框之外又额外嵌套了「OpenAI 兼容参数（Base URL、API Key、模型名）」展开块，用户既要在顶部选模型，又要在底部填模型，存在严重的认知冗余与配置混乱。
3. **安全与易用性统一**：系统刚刚完成不可信 API 接入的四重纵深安全加固（SSRF 阻断、Canary Token 握手、XML 数据沙箱与 XSS 免疫），需要一个与之完全对齐的原生、简洁、直观的纯 OpenAI 兼容配置工作台。

---

## 2. 界面布局重构规范 (UI Structure)

将原本堆叠混乱的单页，重构成“**主干精简双列 + 高级抽屉折叠**”的现代控制台形态：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            系统与引擎配置 (OpenAI 原生)                       │
├──────────────────────────────────────┬──────────────────────────────────────┤
│ 【常规识别引擎】 (VLM Extraction)     │ 【常规审核引擎】 (Cross Audit)        │
│ · 预设网关快捷选择 (SiliconFlow/...)  │ · 审核开关 (开/关) · 审核模式 (text/vlm) │
│ · Base URL                           │ · 预设网关快捷选择                   │
│ · API Key (安全掩码 + 密码框)         │ · Base URL                          │
│ · 识别模型名 (如 Qwen3-VL-32B)       │ · API Key                           │
│                                      │ · 审核模型名 (如 GLM-4.5V 异构模型)  │
├──────────────────────────────────────┴──────────────────────────────────────┤
│ [▼ 高级抽屉 1]：解析 LLM 规范化增强 (可选纯文本通道，默认收起)               │
│   - 解析开关 (开/关) · 预设网关 · Base URL · API Key · 模型名                │
├─────────────────────────────────────────────────────────────────────────────┤
│ [▼ 高级抽屉 2]：分组灰测实验配置 (A/B Testing，默认收起)                      │
│   - 灰测开关 · 流量比例 (0-100%) · 分配模式 (按单据/按供应商)                │
│   - 灰测识别参数 (Base URL, API Key, 模型名)                                │
│   - 灰测审核参数 (开关, Base URL, API Key, 模型名)                          │
│   - 灰测解析参数 (开关, Base URL, API Key, 模型名)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ 全局配置与操作栏：                                                           │
│ · 单引擎超时 (秒) · 保存前一键连通性探测 (复选框)                            │
│ [测试连通性] [保存配置]                                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 彻底移除的废弃 DOM 控件清单
- `#adminRecognitionEngine`（识别引擎选择框）
- `#adminRecognitionModel`（识别模型下拉及自定义删除按钮）
- `#adminParseEngine`（解析引擎选择框）
- `#adminParseModel`（解析模型下拉及自定义删除按钮）
- `#adminAuditEngine`（审核引擎选择框）
- `#adminAuditModel`（审核模型下拉及自定义删除按钮）
- 灰测区域旧控件：`#adminGreyRecEngine`, `#adminGreyRecModel`, `#adminGreyAudEngine`, `#adminGreyAudModel`, `#adminGreyParseEngine`, `#adminGreyParseModel`。

---

## 3. 前端交互与数据契约设计 (`demo/static/js/main.js`)

1. **废弃逻辑安全下线**：
   - 清理所有面向本地 CLI 模型的动态渲染方法（`fillModelOptions`, `handleModelSelectChange`, `handleDeleteCurrentModel`）；
   - 彻底解除对已删除 DOM 节点的事件监听，消除潜在 `null.addEventListener` 运行时异常。
2. **数据绑定与回显 (`loadAdminEngineConfig`)**：
   - 直接且只读取 `openai_*` 系列字段与各开关状态，绑定至对应输入框；
   - 保留各输入区的预设网关选择（`applyBoxPreset`），支持 Agnes AI、阿里云百炼 DashScope 与硅基流动 SiliconFlow 一键带入 Base URL 与推荐模型。
3. **提交构造与字段双向兼容 (`saveAdminEngineConfig`)**：
   - 前端从原生输入框获取各参数，组装提交体；
   - 提交时前端对旧字段赋予安全固定值，后端亦自动执行兜底双向同步：
     - `recognition_engine = "openai"`，`recognition_model = openai_rec_model`
     - `audit_engine = "openai"`，`audit_model = openai_aud_model`
     - `parse_llm_engine = "openai"`，`parse_llm_model = openai_parse_model`
     - 灰测对应参数同理映射。
4. **抽屉折叠交互**：
   - 原生 CSS + JS 实现抽屉折叠/展开动画与指示箭头图标联动，无需外部重量级依赖。

---

## 4. 后端数据契约与接口处理 (`demo/app/`)

### 4.1 模型契约维护 (`demo/app/models.py`)
- `EngineConfig` 保持定义向下兼容，旧字段 `recognition_engine`, `audit_engine` 缺省统一为 `EngineKind.OPENAI`；
- 保留字段结构以保证 SQLite/Postgres 数据平滑过渡与现有单元测试通过。

### 4.2 管理接口层强化 (`demo/app/api_admin.py`)
- `set_engine_config`：
  - 接收到 `openai_rec_model`、`openai_aud_model`、`openai_parse_model` 时，自动同步覆盖回填对应的 `recognition_model`、`audit_model`、`parse_llm_model`，保证单一事实源。
  - 所有各引擎类型统一归一化为 `"openai"`。
- `test_engine_config` & `_quick_test_engine`：
  - 剔除遗留的本地 CLI 探测逻辑，专注于对 OpenAI 兼容 Base URL 和 API Key 发起轻量 `/models` 连通性探测。
  - 严格通过 `validate_safe_external_url` 执行 SSRF 拦截防御。
- 接口响应出站脱敏：
  - 继续由 `mask_engine_config_dict` 对所有接口返回的 `api_key` 执行强脱敏（前3后4掩码）。

---

## 5. 测试与验证方案

1. **自动化端到端契约测试 (`tests/test_openai_only_engine_config.py`)**：
   - 测试纯 OpenAI 格式提交：只提供 `openai_*` 字段，断言保存成功并自动同步 legacy 字段；
   - 测试预设网关数据结构合法性；
   - 测试 SSRF 阻断仍然有效（非法 URL 拦截）；
   - 测试敏感 Key 响应掩码保护仍然生效。
2. **浏览器 UI 交互与回归测试**：
   - 验证双列主干与抽屉正常渲染，无前端异常报错；
   - 验证预设切换、开关联动与参数输入；
   - 验证全量核心业务测试套件（68 个测试）100% 保持通过。

---

## 6. 约束与零 Emoji 规范
- 全量代码、注释、文档与测试用例严格遵循零 Emoji 规范；
- 实施全程保证现有单据流水线与导出功能稳定可用。

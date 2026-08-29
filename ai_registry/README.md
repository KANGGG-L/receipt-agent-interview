# AI 工程化能力资产库与评测中心 (AI Assets Registry & Benchmarks)

> **定位**：面向 receipt_agent 系统全生命周期的 AI 资产集中治理中心。  
> **纳管对象**：**Prompt（提示词）、Tool（确定性工具）、Skill（Agent 技能）、MCP（上下文协议服务）、Plugin（扩展插件）**。  
> **核心特性**：语义化版本（SemVer）、全链路效果与评测指标追踪（Evaluation-Driven）、多租户防穿透、多模型热插拔。

---

## 目录结构全景

```
ai_registry/
├── README.md                      # 本文档：工程化管理总纲与规范
├── registry.py                    # 统一资产加载与路由注册中心 (Python API)
├── eval_reporter.py               # 资产效果分析与版本对比报告生成器
│
├── prompts/                       # 1. 提示词资产库 (分场景、分版本、含评测元数据)
│   ├── extract/                   # 视觉提取 VLM Prompts，11 版：v1_0_0 → v1_2_8_anti_injection（active）
│   ├── parse/                     # 文本规范化 Prompts，4 版：v1_0_0 / v2_0_0_hk_units /
│   │                              #   v2_1_0_sku_clean（active）/ v2_2_0_structured_json
│   ├── correct/                   # 门禁反馈驱动的解析级修正 Prompts（新场景，v1_0_0 active）
│   ├── audit/                     # 交叉审核 Prompts，3 版：v1_0_0 / v2_0_0_reason /
│   │                              #   v2_1_0_overall_schema（active）
│   ├── review/                    # 采购复盘与议价 Prompts，2 版：v2_0_0_cards（active）/ v2_0_1_inline_parity
│   ├── query/                     # 自然语言查账 Prompts (v1.0.0)
│   └── memory/                    # 供应商记忆提炼 Prompts (v1.0.0)
│
├── tools/                         # 2. 确定性工具资产库 (Tool Registry，12 个)
│   ├── math_engine/               # 确定性算术校验工具 (零 Token 守恒计算, v2_0_0)
│   ├── smart_splitter/            # 品名规格智能正则剥离工具
│   ├── item_sanitizer/            # 明细条目清洗工具（免责条款/杂项行剔除）
│   ├── sku_matcher/               # SKU 核心词匹配工具（精确 → 核心词 → 建档）
│   ├── date_normalizer/           # 港式日期归一工具
│   ├── currency_unit_converter/   # 港式单位 (司马斤/磅/板) 换算工具
│   ├── huama_evaluator/           # 花码（苏州码）与草书置信度评估工具
│   ├── image_quality_guard/       # 图像质量守卫工具（模糊/过曝检测）
│   ├── prompt_injection_guard/    # 提示注入防护与 XML 沙箱工具
│   ├── pii_masker/                # 敏感信息 (HKID/银行卡/手机) 脱敏工具
│   ├── query_sql_gen/             # 只读安全 SQL 动态生成器
│   └── statement_reconciler/      # 月结 Statement 对账差异比对工具
│
├── skills/                        # 3. Agent 技能资产库 (Standard SKILL.md)
│   ├── receipt_auditing/          # 收据原图交叉审核技能
│   ├── price_negotiation_review/  # 食材暴涨谈判与复盘卡片生成技能
│   ├── supplier_reconciliation/   # 供应商月结 Statement 差异比对技能
│   └── natural_nl_query/          # 粤语/多维跨表经营问答技能
│
├── mcp/                           # 4. Model Context Protocol 服务与端点
│   ├── servers/                   # MCP 独立服务 (Chroma 记忆服务, DB 只读服务等)
│   └── configs/                   # MCP 配置文件与环境变量映射
│
├── plugins/                       # 5. 扩展插件库 (Plugin Architecture)
│   ├── vlm_engines/               # 多模态引擎插件 (Opencode, CodeBuddy, OpenAI, Ollama)
│   ├── notification/              # 异动通知插件 (WhatsApp/Telegram/Email)
│   └── pos_connectors/            # POS 销项数据打通插件 (Eats365, StoreHub)
│
└── benchmarks/                    # 6. 历史基准评测报告与版本效果追踪
    ├── benchmark_matrix.json      # 全量资产版本效果总表
    ├── prompt_eval_history.json   # 提示词效果演化指标追踪 (准确率/CER/Token)
    ├── tool_eval_history.json     # 确定性工具执行时延与拦截率统计
    ├── meta_eval_set.json         # 元评测集（评估器可信度自检，25 条已确证二元样本）
    ├── meta_eval_runs/            # run_meta_eval.py 的评估器可信度报告留档
    └── eval_runs/                 # run_eval.py 的评测报告落盘目录（首次运行后生成，
                                   #   命名 <ts>_<split>_<prompt_ver>.json，可追溯 raw report）
```

---

## 快速使用指引 (Python API)

### 1. 加载并使用特定版本的 Prompt
```python
from ai_registry.registry import ai_registry

# 获取生产默认激活版 Prompt
prompt_text = ai_registry.get_prompt("extract")

# 指定版本并获取元数据及评测指标
prompt_text, meta = ai_registry.get_prompt("extract", version="v1_1_0_hk", with_metadata=True)
print(f"准确率: {meta['metrics']['accuracy']}, 平均 Token: {meta['metrics']['avg_tokens']}")
```

### 2. 调用确定性工具 (Tools)
```python
# 获取算术门禁工具
math_tool = ai_registry.get_tool("math_engine", version="v2_0_0")
is_valid, diffs, suggestions = math_tool.execute(receipt_items, total_amount)

# 获取品名智能剥离工具
splitter = ai_registry.get_tool("smart_splitter")
res = splitter.execute("大豆油 5L*2樽")
# -> {"item_name": "大豆油 5L", "quantity": 2.0, "unit": "樽"}
```

### 3. 查看资产效果与版本对比
```bash
# 查看全量资产评测矩阵
python -m ai_registry.eval_reporter --summary

# 对比 prompt extract 两个版本的指标变化
python -m ai_registry.eval_reporter --diff --type prompt --name extract --v1 v1_0_0 --v2 v1_1_0_hk
```

---

## 版本规范与准入规则 (SemVer & Production Gate)

1. **版本命名规范**：遵循 `v<Major>_<Minor>_<Patch>[_<FeatureTag>]`（如 `v1_1_0_hk`、`v2_0_0_cards`）。
2. **生产准入硬性门槛**：
   - 提取类 Prompt：在 57 张黄金样本集上的准确率 $\ge 95\%$，CER $\le 4.0\%$；
   - 门禁类 Tool：对算术差错拦截率必须达到 $100.0\%$，单次执行时延 $\le 10\text{ms}$；
   - 技能 Skill：需具备标准 `SKILL.md`，并通过结构化自愈用例回归。
3. **生成器-评估器强制异构（Gap A3）**：审核腿（评估器，`audit_engine`/`audit_model`）必须与识别腿（生成器，`recognition_engine`/`recognition_model`）**跨厂商异构**，禁止同引擎同家族——识别腿为 Qwen/GLM 系时审核腿必须走 opencode 等异构系；识别腿为 opencode 系时审核腿必须换 Qwen/GLM 系。灰测组 `grey_audit_model` 同此约束。**GT 生成腿例外（经用户决策）**：GT 生成腿可用百炼 Qwen 系（与识别腿同家族），异构性由人工抽检兜底——候选 GT 逐张经人工确认后方可作为评测基准；`gt_source_model` 必须照实记录，不得虚标。
4. **评估调用 temperature=0（Gap A4）**：审核/评估调用必须以 temperature=0 执行（`demo/app/llm.py` 的 `_build(side="aud")` 已强制注入），保证评估结论确定性、可复现；禁止在调用侧覆盖为非零采样。
5. **上线前必跑元评测集（Gap A4）**：任何引擎/模型/Prompt 变更上线前，必须运行 `python demo/scripts/run_meta_eval.py`（真实模型用 `--judge audit` 显式触发），且报告结论 `evaluator_trustworthy=true`（对「绝对正确」样本判对率 100% 且对「绝对错误」样本判错率 100%）方可准入；报告落盘 `benchmarks/meta_eval_runs/` 留档。注意：mock judge 仅用于离线自检 harness 本身，**生产准入必须跑 `--judge audit` 模式（真实异构审核模型）并以该模式的报告为准**。

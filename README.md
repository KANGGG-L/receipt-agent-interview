# 香港餐饮进货收据识别与库存管理系统

> **订货收据 → 实时库存 → 运营成本**，面向香港餐饮（F&B）市场。
> AI 多模态识别收据 + 确定性代码引擎混合架构，人工复核（Human-in-the-Loop）把关。
> 本文档介绍该系统中「收据识别与库存」部分的落地实现。

---

## 一、这是什么项目

一个**面向香港餐饮店老板的进货收据识别与库存管理系统**：

老板每天收到大量进货收据（印刷送货单、街市手写单、热敏机打、磅单、月结账单……香港餐饮实际存在**七种单据形态**并存），手工录入繁琐易错、月底对账加班。这个系统让老板「**拍照 → AI 预填 → 核对不到一分钟 → 入库 → 月底成本自动算**」。

本仓库承载该项目中「收据识别 → 实时库存 → 成本核算」核心模块的完整实现：完整产品前端 + LangChain 编排的后端，是系统面向单店落地的最小闭环版本。

## 二、希望解决什么问题

香港餐饮行业的**账单管理之痛**（真实走访调研，八条痛点）：

| 痛点 | 说明 |
| :--- | :--- |
| 手抄易错 | 进货单据靠人工录入 Excel，数量/单价/金额常有抄错 |
| 七种单据形态 | 印刷单/手写单/热敏单/磅单/更正单/CN/月结单并存，无法一套规则处理 |
| 印章付款难判 | 「已付款」靠印章/手写标记识别，纯文本 OCR 识别不了 |
| 月底对账加班 | 应付账款要对着几十张单据手工核对 |
| 涨价后知后觉 | 食材涨价（如菜心从 4.2 → 5 元/斤）没有自动提醒 |
| 库存糊涂账 | 进货量 ≠ 真实结余，盘店靠数 |
| 单位迷宫 | 司马斤/公斤/磅/箱/包混用，换算全靠人工 |
| 归档备查 | 纸质单据堆积，查一张历史单子翻半天 |

**核心价值主张**：让老板放下对账单的操心——拍照、核对不到一分钟，账就自己进系统；往后想知道成本问一句就行。

## 三、做了什么

### 3.1 产品层（完整前端，4 个 Tab 全部可用）

| Tab | 功能 |
| :--- | :--- |
| 收据识别 | 上传 → AI 预填 → Side-by-Side 复核 → owner approve 入库 |
| 实时库存 | SKU 列表/编辑/盘点/消耗/损耗/价格历史/价格异动 |
| 供应商归档 | 供应商 CRUD/合并 + 应付账款 + 对账引擎 |
| 部门花销报表 | 部门管理 + 月度成本报表 |

### 3.2 AI 层（LangChain 编排）

**「三层互不信任」架构**——这是整个系统的可信度底线：

| 层 | 执行者 | 规则 |
| :--- | :--- | :--- |
| 图像 → 结构化 | VLM（可插拔模型） | 允许出错，但自报置信度 |
| 契约 + 算术 | Pydantic 契约门禁 + 代码引擎 | AI 的金额必须过校验，不静默改 |
| 人工背书 | Side-by-Side 复核 → approve | 库存只在 approve 时写入 |

LangChain 模型封装 + 确定性线性编排（`supervisor.py`）驱动完整识别管线：

```
upload → VLM 识别 → 契约门禁 → 算术门禁 → 交叉审核 → VendorMemory RAG
  → parsed → 人工复核(save_edited 乐观锁) → approve → SKU 库存 + 供应商建档
```

> 编排方式：识别流程是**确定性线性 + 重试阶梯**，用普通 Python 函数表达（可读、易测）；
> LangChain 负责模型层抽象（多模态封装/可插拔引擎/向量检索）。

关键设计：
- **重试阶梯**：识别失败 → 契约反馈重试 → 引擎切换 → 显式 error（绝不静默兜底）
- **交叉审核 Agent**：一模型识别、一模型审核（不同模型家族盲点互补）
- **VendorMemory RAG**：按供应商积累别称/单位/版式记忆，越用越准（护城河）
- **算术不交给 LLM**：数量×单价、Σ总额用确定性代码校验，校验+建议不静默改

### 3.3 平台能力

- **三层 RBAC**：admin / owner / staff，无密码下拉切换，权限即时生效
- **引擎可插拔**：opencode CLI / CodeBuddy CLI / 任意 OpenAI 兼容接口（自填 base_url + api_key + 模型名）
- **灰度发布**：分组测试按概率 0-100% 随机分配，常规与灰测组引擎完全隔离；支持按单据/按供应商分配
- **解析 LLM**：VLM 识别后可选交给 LLM 规范化（可开关，常规/灰测独立）
- **AI 复盘**：价格异动检测 + 供应商洞察 + 采购建议

### 3.4 测试体系

- **确定性单测**：契约门禁/算术门禁/RBAC/乐观锁/幂等入库 —— 15 passed，秒级
- **完整业务流**：上传→识别→复核→审批→库存→成本→复盘→对账→支付 全链路
- **黄金样本**：57 张真实香港收据 + 人工标注，可导入平台做回归
- **实测数据**：导入黄金样本后形成 55 SKU / 5 供应商 / 成本 5.9 万 的完整环境

### 3.5 AI 资产工程化治理（ai_registry 唯一来源）

所有 AI 资产——**Prompt / Skills / Tools / MCP**——统一收口在 [`ai_registry/`](ai_registry/README.md)（AI 工程化能力资产库与评测中心）进行工程化管理，它是本项目 AI 资产的**唯一来源（Single Source of Truth）**：

- **版本化**：Prompt 按 SemVer 独立成文件（如 `prompts/extract/v1_2_8_sku_clean.py`）+ `metadata.json` 记录评测指标与准入状态，经 `registry.py` 加载激活版本，迭代不覆盖旧版
- **确定性工具**：算术门禁/品名剥离/日期归一/花码识别等 12 个 Tools 收口在 `ai_registry/tools/`，业务代码经包导入复用，不在链路里内联复制逻辑
- **Skills 能力矩阵**：收据审核/供应商对账/自然语言查价等 Skills 带独立 `eval.json`，效果可对比
- **MCP 收口**：MCP servers 与 configs 统一放 `ai_registry/mcp/`
- **评测中心**：`benchmarks/` 存放全量资产评测矩阵与版本对比，改动可回溯；生产准入走 SemVer + 准入规则（准确率/拦截率门槛）

工程纪律：新增或修改任何 Prompt/Skill/Tool/MCP 必须先检索 ai_registry（有则复用，无则按规范新建版本并评测），禁止在业务代码里散落硬编码提示词或内联工具逻辑。

## 四、达到什么效果

### 实际跑通的核心链路（免费模型，可复现）

```
上传真实收据图
  → 识别出供应商/日期/金额/明细（如祥興快餐用品，1343.0 元，6 条明细）
  → 算术门禁当场抓到 AI 算错（10×130=1300 但写 130）→ 反馈重试修正
  → 交叉审核通过 → 人工复核提交 → owner approve 入账
  → 自动建 SKU + 供应商建档 → 成本报表累计 → AI 复盘
```

### 关键数字

- **识别链路**：40s~4min 走通全流程（免费模型），多引擎可切换
- **可信度**：三层门禁（契约/算术/人工）拦截错误，AI 只预填、人工背书
- **可测性**：15 个确定性单测 + 完整业务流 + 57 张黄金样本回归

## 五、Workflow 详解与实现

### 5.1 端到端业务流（用户在界面里看到什么）

一条进货收据从拍照到变成库存/成本，经过 **8 个阶段**、**7 种状态**：

```
uploaded → parsing → parsed → edited → approved
                        └── error（识别失败/门禁不过，显式双出口）
                              └── flagged（人工标记可疑，不入账）
```

| 阶段 | 状态 | 谁触发 | 说明 |
| :--- | :--- | :--- | :--- |
| 1. 上传 | `uploaded` | staff | 上传收据图，创建异步识别 Job |
| 2. 识别 | `parsing` | 后端 Job 线程 | LangChain 多模态 VLM 读图 → 结构化 JSON（40s~4min） |
| 3. 门禁校验 | `parsed` / `error` | 代码引擎 | 契约校验 + 算术校验，不过 → 反馈重试，仍败 → error |
| 4. 人工复核 | `edited` | staff | Side-by-Side 对照原图修正，乐观锁防冲突 |
| 5. 审批入账 | `approved` | owner | 幂等写入 SKU 库存 + 供应商自动建档 + VendorMemory 回写 |
| 6. 标记异常 | `flagged` | owner | 可疑单据标记，不入账 |
| 7. 成本核算 | — | owner | 按 SKU 累计成本，月度报表 |
| 8. 对账/支付 | — | owner | 应付账款对账 + 付款登记 |

### 5.2 AI 识别管线（后端如何实现）

`app/chains/supervisor.py` —— 线性编排 + 重试阶梯（纯 Python，LangChain 管模型）：

```
run_pipeline(image_path, config)
  │
  ├─ 灰测分配：should_use_grey(config, supplier) → 本单走常规 or 灰测组引擎
  │
  ├─ [重试阶梯 ≤ 3 轮]
  │    └─ extract_chain.extract_receipt()      ← VLM 读图 → 原始 JSON
  │         ├─ VendorMemory RAG 注入 <vendor_context>（按供应商记忆）
  │         ├─ 可选「解析 LLM」二次规范化（纯文本，可开关）
  │         └─ 契约校验（Pydantic extra=forbid，拒绝 schema 外字段）
  │    └─ _run_gates()                          ← 契约 + 算术双门禁（零 token）
  │         ├─ 数量×单价 == 小计？Σ明细 == 总额？
  │         └─ 不过 → 把错误喂回 prompt 重试（"上一轮被门禁打回，请修正"）
  │
  ├─ 交叉审核：audit_chain.run_audit()         ← 另一引擎对照原图复核
  │         ├─ 一致 → pass；有分歧 → flag（供人工复核排序）
  │         └─ 审核失败 → graceful skip（不阻断主链路）
  │
  └─ 结果：data + 决策履历 log（每个选择可审计）
```

**为什么用「线性编排 + 重试循环」而不是图编排框架**：识别流程是**确定性的线性链路**——识别 → 门禁 → 审核，唯一的"分支"就是重试（带反馈循环）。用普通 Python 函数表达：可读、易单测；LangChain 承担真正有价值的部分——**模型层抽象**（多模态封装、引擎可插拔、向量检索）。框架选型为需求服务。

### 5.3 三层互不信任（可信度从哪来）

| 层 | 执行者 | 文件 | 干什么 | 失败怎么办 |
| :--- | :--- | :--- | :--- | :--- |
| VLM 预填 | 多模态模型（可插拔） | `extract_chain.py` | 读图 → 结构化 JSON，自报置信度 | 允许出错，交给下一层 |
| 契约门禁 | Pydantic | `services/contract.py` | 拒绝 schema 外字段/非法枚举/NaN | 打回重试（≤3 轮） |
| 算术门禁 | 确定性代码 | `services/math_engine.py` | 数量×单价、Σ总额，零 token | 打回重试 + 差异清单 |
| 交叉审核 | 另一引擎 | `audit_chain.py` | 对照原图逐字段复核 | 分歧 → flag 提示人工 |
| 人工背书 | 老板 | `api_receipts.py` approve | Side-by-Side 复核后审批 | 库存只在 approve 写入 |

核心纪律：**AI 只预填、人工背书；校验+建议、不静默改写**。AI 算错金额不会悄悄改掉，而是报出来等人工裁决——错误模式因此可见、可审计。

### 5.4 模型引擎怎么做到可插拔

`app/llm.py` —— 三个 `BaseChatModel` 子类，统一 LangChain 接口：

| 引擎 | 类 | 特点 |
| :--- | :--- | :--- |
| opencode CLI | `OpencodeChatModel` | 本机免费，MiMo-V2.5 Free 可读图，`opencode run` 子进程 |
| CodeBuddy CLI | `CodeBuddyChatModel` | 本机免费，minimax-m3-pay 视觉 |
| OpenAI 兼容 | `OpenAIChatModel` | 任意网关，base_url + api_key + model，支持多模态 |

`build_recognition_model(cfg, use_grey)` 工厂按 `EngineConfig` 路由到对应引擎——
识别管线只依赖 `BaseChatModel` 接口，**换引擎不改管线代码**。

### 5.5 灰度发布怎么实现

`models.should_use_grey(cfg, supplier)` + `supervisor` 的 `use_grey` 标志：

```
admin 配置：grey_enabled + grey_percent(0-100) + grey_assign_mode
  ├─ receipt 模式：每单 random() < 概率% → 命中灰测组
  └─ supplier 模式：供应商名 hash 落 [0,100) → 同供应商一致命中（确定性可复现）

命中 → 本单识别/审核/解析全部走「灰测组引擎」（grey_* 独立配置）
未命中 → 走常规引擎（recognition_*/audit_*）
```

两组引擎在 `EngineConfig` 里**完全隔离**（各自独立的引擎类型 + 模型 + OpenAI 参数），
命中灰测的单据整单用新模型，识别率达标后再把新模型设为常规——标准的灰度发布。

### 5.6 幂等入账与数据飞轮

- **幂等**：`approve` 只在 `edited` 状态生效；入库写 `inventory_log` 流水（append-only）；
  重复 approve 被状态机拦截（乐观锁 version 校验）
- **SKU 匹配**：明细按商品名精确匹配 → 核心词匹配 → 无匹配则 approve 时自动建 SKU；
  错配（如单字"茶"误配）会被核心词校验拦截
- **VendorMemory 飞轮**：approve 后回写该供应商的版式/单位/明细记忆到 Chroma（RAG），
  下次识别同供应商单据时注入 `<vendor_context>` → **越用越准，竞品难抄**

### 5.7 关键实现文件地图

```
demo/app/
├── llm.py                 # 三个 BaseChatModel 子类 + 模型工厂（引擎可插拔）
├── chains/
│   ├── supervisor.py      # 识别管线编排：线性流程 + 重试阶梯 + 灰测分配
│   ├── extract_chain.py   # VLM 读图 → 结构化（RAG 注入 + 可选解析 LLM）
│   ├── audit_chain.py     # 交叉审核 Agent（对照原图逐字段复核）
│   └── review_chain.py    # AI 复盘（价格异动/供应商洞察）
├── services/
│   ├── contract.py        # Pydantic 契约门禁（拒绝 schema 外字段/NaN）
│   ├── math_engine.py     # 算术门禁（数量×单价、Σ总额，零 token）
│   ├── rag.py             # VendorMemory RAG（Chroma，按供应商记忆）
│   ├── inventory.py       # approve 幂等入账 + SKU 匹配/建档
│   └── receipt_utils.py   # AI 结果 ↔ 前端契约桥接 + 异步 Job
├── api_*.py               # 44 个 API 端点（匹配完整版前端契约）
├── auth.py                # 三层 RBAC + HMAC token（admin/owner/staff）
└── db.py                  # SQLite + SQLAlchemy（无迁移）
```

## 六、快速开始

```bash
cd demo
cp .env.example .env        # 默认 opencode 免费模型，零配置可跑
pip install -r requirements.txt

./demo.sh run               # 启动 http://127.0.0.1:15010
./demo.sh test              # 确定性单测（15 passed）
./demo.sh workflow          # 完整业务流测试
./demo.sh smoke <图路径>     # 单图冒烟
```

详细说明见 [demo/README.md](demo/README.md)。

## 七、技术栈

- **LangChain**：模型层抽象（多模态封装/可插拔引擎/Chroma 向量检索）+ 确定性编排
- **FastAPI + SQLite**：~40 个 API 端点匹配完整版前端契约
- **多模型通道**：opencode（MiMo-V2.5 Free 免费）、CodeBuddy、OpenAI 兼容（SiliconFlow Qwen3-VL 等）
- **Pydantic**：输出契约门禁（拒绝 schema 外字段）
- **前端**：完整版产品 UI（纯静态，4 Tab）

## 八、目录结构

```
receipt-agent-interview/
├── README.md              # 本文件（系统说明）
├── .gitignore             # 忽略密钥/运行时数据
├── ai_registry/           # AI 资产唯一来源：prompts/skills/tools/mcp + 版本化 + 评测中心
│   ├── prompts/           # 各域 Prompt（SemVer 独立文件 + metadata）
│   ├── skills/            # Skills（带 eval.json 能力矩阵）
│   ├── tools/             # 确定性工具（算术门禁/品名剥离等 12 个）
│   ├── mcp/               # MCP servers 与 configs
│   ├── canary/            # 灰度路由与 A/B 评估
│   └── benchmarks/        # 资产评测矩阵与历史记录
└── demo/                  # 单店落地实现
    ├── app/               # FastAPI + LangChain 后端
    │   ├── chains/        # 识别/审核/复盘链
    │   ├── services/      # 契约门禁/算术门禁/RAG/入账
    │   └── api_*.py       # 各业务域 API
    ├── templates/         # 前端 HTML
    ├── static/            # 前端 CSS/JS/图片
    ├── scripts/           # 黄金样本导入
    ├── tests/             # 单测 + workflow 测试
    └── demo.sh            # 一键命令
```

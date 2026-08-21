# 13 · 组件 Spec：OCR 全链路工作流与架构全景 (End-to-End OCR Architecture & Workflow)

> **文档定位**：面向 AI 产品经理/技术面试的 OCR 核心架构与端到端工作流解析。  
> **核心思想**：三层互不信任体系 (3-Tier Zero-Trust)、双重保险防御（Prompt 结构化感知 + Python 确定性硬门禁）、闭环自愈与人机协同。

---

## 一、OCR 全链路端到端工作流 (End-to-End Workflow)

```mermaid
flowchart TD
    subgraph S1["阶段 1：摄入与图像预处理 (Ingestion & Preprocessing)"]
        A1["店员上传图片 (JPG/PNG/HEIC/TIFF)"] --> A2["格式探测与 pillow_heif 转码 (消除浏览器黑屏)"]
        A2 --> A3["EXIF 旋转元数据自动扶正 (Auto-Orientation)"]
        A3 --> A4["pHash 图像去重与感知哈希计算"]
    end

    subgraph S2["阶段 2：供应商记忆检索与安全沙箱注入 (RAG & Security Sandbox)"]
        A4 --> B1["检索 VendorMemory (别称惯式/排版习惯)"]
        B1 --> B2["PromptInjectionGuard: 写时敏感词清洗"]
        B2 --> B3["封装入 <vendor_context data_only='true'> XML 沙箱"]
    end

    subgraph S3["阶段 3：多模态 VLM 感知抽取 (Probabilistic Perception)"]
        B3 --> C1["视觉大模型感知抽取 (Qwen-VL / GPT-4o / MiniMax)"]
        C1 --> C2["输出 Raw JSON 不可信草稿 (含 items, fees, notes, confidence)"]
    end

    subgraph S4["阶段 4：确定性代码层与多重门禁过滤 (Deterministic Code Gate)"]
        C2 --> D1["ItemSanitizer: 过滤印章/签名/免责声明/联系方式"]
        D1 --> D2["SmartSplitter: 剥离流水号/条形码 + 复合包装乘数解耦"]
        D2 --> D3["DateNormalizer: 港式 DD/MM/YYYY 归一化为 YYYY-MM-DD"]
        D3 --> D4["HuamaEvaluator: 街市花码检测 + 置信度强制压降"]
        D4 --> D5["Pydantic Contract 契约门禁 (extra='forbid')"]
        D5 --> D6["math_engine: 算术守恒门禁 (Σitems - 折扣 + 押金 + 运费 = 总额)"]
    end

    subgraph S5["阶段 5：交叉审核与门控路由 (Audit Agent & Routing)"]
        D6 --> E1{"算术/契约是否完全守恒?"}
        E1 -->|守恒 & 高置信| E2["Audit Chain: 第二模型抽检复核"]
        E1 -->|不守恒/有差额| E3["触发自愈重试阶梯 (≤3次) 或生成差异警示"]
        E2 --> E4["置信度分流: 高置信直通 / 中低置信人工高亮复核"]
        E3 --> E4
    end

    subgraph S6["阶段 6：交互复核与台账落库 (Human-in-the-Loop & Ledger)"]
        E4 --> F1["前端 Side-by-Side 左右原图比对工作台"]
        F1 --> F2["店员/老板确认并点击 Approve"]
        F2 --> F3["乐观锁 (Version 409) 并发防冲突"]
        F3 --> F4["触发唯一的 Append-Only InventoryLog 台账写入"]
    end

    S1 --> S2 --> S3 --> S4 --> S5 --> S6
```

---

## 二、架构哲学：三层互不信任体系 (3-Tier Zero-Trust)

在涉及真金白银的餐饮供应链、库存台账与月结对账场景中，**“概率模型绝不能单独决定账目”**。

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                     receipt_agent 三层互不信任安全架构体系 (Zero-Trust)                │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│   【第 1 层：多模态 VLM 概率感知层 (Probabilistic Perception)】                         │
│    · 执行者：Qwen3-VL / MiMo-V2.5 / MiniMax-M3 / GPT-4o                                │
│    · 职责：像素级空间布局理解、手写字与繁体识别、红色已付款印章检测                   │
│    · 纪律：允许自报置信度，但输出全部视为“不可信草稿”，绝不直接写库                   │
│                                                                                        │
│                                          │ （输出 Raw JSON 草稿）                      │
│                                          ▼                                             │
│   【第 2 层：确定性代码契约与算术门禁层 (Deterministic Gate)】                          │
│    · 执行者：Pydantic Schema (contract.py) + 纯 Python 算术引擎 (math_engine.py)       │
│    · 职责：① 阻断 Schema 外部字段；② 强制 `数量 × 单价 = 金额` 与 `∑明细 = 总额` 守恒 │
│    · 纪律：校验并提供建议，不静默改写；发现不一致反馈至 Prompt 触发自愈重试            │
│                                                                                        │
│                                          │ （输出 Verified Draft）                     │
│                                          ▼                                             │
│   【第 3 层：人类背书与可信持久化层 (Human-In-The-Loop & Ledger)】                      │
│    · 执行者：前线店员/老板 (Side-by-Side 核对) + 乐观锁 (Version 409) + Approve 按钮   │
│    · 职责：左右原图比对确认；老板点击 Approve 触发唯一的台账写入                       │
│    · 纪律：只有 approve 状态能写入 Append-Only `InventoryLog`；修改必须留痕冲销        │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 三、确定性线性编排对比自主图编排 (Why Deterministic Linear Pipeline?)

### 面试核心论点：
- **为什么不使用 AutoGPT / LangGraph 等自主循环图 Agent？**
  1. **状态可解释性与单测保障**：餐饮进货业务是高吞吐、强确定性的流水线，线性流水线每一阶段的输入输出完全可验证、可写单元测试。
  2. **消除不可控死循环与 Token 意外爆炸**：自主图 Agent 在遇到模糊手写单时容易陷入发散思考或无限反思，导致单据识别耗时突破 2 分钟、API 成本失控。
  3. **线上故障秒级定界**：线性编排中的每个 Gate（契约门禁、算术门禁、清洗工具）职责单一，出错时能立刻定位是模型感知层（S3）还是代码后处理层（S4）的问题。

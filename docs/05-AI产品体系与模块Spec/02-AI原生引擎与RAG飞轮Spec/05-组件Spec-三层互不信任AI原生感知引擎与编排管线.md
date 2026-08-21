# 05 · 组件 Spec · 三层互不信任 AI 原生感知引擎与编排管线

> **模块定位**：系统 AI 感知与算法安全核心中枢。承载多模态 VLM 感知抽取、Pydantic 契约门禁、确定性代码算术硬校验、双模型交叉审核 Agent，以及“确定性线性编排 + 自愈重试阶梯”管线。  
> **对标 14 步方案**：`step6-技术可行性`、`step9-AI技术方案`、`step10-评审对齐`、`step12-测试验收`。  
> **实现代码**：[`chains/supervisor.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/chains/supervisor.py)、[`chains/extract_chain.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/chains/extract_chain.py)、[`chains/audit_chain.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/chains/audit_chain.py)、[`services/contract.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/services/contract.py)、[`services/math_engine.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/services/math_engine.py)  

---

## 1. 架构哲学：三层互不信任体系 (3-Tier Zero-Trust)

在涉及真金白银的餐饮财务与库存场景中，**“概率模型绝不能单独决定账目”**。系统确立了如下严密防线：

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

## 2. 识别管线编排与自愈重试阶梯 ([`supervisor.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/chains/supervisor.py))

### 2.1 为什么采用“确定性线性编排”而非图编排 Agent
进货单据的识别流程在本质上是 **“确定性的流水线 + 局部的错误反馈自愈”**。使用纯 Python 函数实现线性编排，相较于复杂的自主 Agent 图框架具有极大的工程优势：
- **完全可解释、可单测**；
- **消除不可控死循环与 Token 意外爆炸**；
- **状态流转确定，排查故障秒级定界**。

### 2.2 编排流程全景图

```mermaid
flowchart TD
    In[收到识别请求 image_path] --> Step0{灰度路由分流<br/>should_use_grey}
    
    Step0 -->|灰测组| CfgGrey[加载灰测模型参数]
    Step0 -->|常规组| CfgProd[加载线上生产模型参数]
    
    CfgGrey --> Loop[进入重试循环 loop ≤ 3 次]
    CfgProd --> Loop
    
    Loop --> RAG[1. VendorMemory 检索供应商历史先验]
    RAG --> VLM[2. ExtractChain: 多模态 VLM 提取]
    VLM --> ParseLLM{可选文本规范化 LLM?}
    ParseLLM -->|是| NormLLM[文本标准化]
    ParseLLM -->|否| Gate1
    NormLLM --> Gate1
    
    Gate1[3. 契约门禁: Pydantic Schema 校验] --> Chk1{契约是否合法?}
    Chk1 -->|否| PushErr1[记录契约错误] --> LoopNext
    
    Chk1 -->|是| Gate2[4. 算术门禁: math_engine.py 零Token计算]
    Gate2 --> Chk2{算术是否守恒?}
    Chk2 -->|否| PushErr2[记录算术差额清单] --> LoopNext{已达最大重试轮数?}
    
    LoopNext -->|未达上限 (≤3)| FeedbackPrompt[将错误信息注入下一轮 Prompt<br/>触发针对性自愈重试] --> Loop
    LoopNext -->|已超上限| ErrorExit[标记为 status='error'<br/>保留原图, 引导手工补录]
    
    Chk2 -->|是| CrossAudit[5. 交叉审核 Agent: audit_chain.py<br/>调独立第二模型复核]
    CrossAudit --> CheckDiff{两模型存在分歧?}
    CheckDiff -->|有分歧| FlagItem[打上 Flagged 标签提示人工] --> SuccessExit
    CheckDiff -->|无分歧| PassItem[全字段高置信度] --> SuccessExit
    
    SuccessExit([输出 Verified Draft<br/>更新单据状态为 parsed])
```

---

## 3. 核心门禁组件技术规格

### 3.1 Pydantic 契约门禁 ([`services/contract.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/services/contract.py))
- **`extra = "forbid"` 强约束**：杜绝大模型在 JSON 中自由发挥注入多余字段；
- **类型与格式门禁**：
  - 日期格式强制正则校验 `^\d{4}-\d{2}-\d{2}$`；
  - 数量 `quantity > 0`，单价 `unit_price >= 0`；
  - 拒绝 `NaN`、`Infinity` 或空明细列表。

### 3.2 确定性算术校验引擎 ([`services/math_engine.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/services/math_engine.py))
**算术计算坚决不消耗 LLM Token，全部由 Python 确定性代码执行**：
1. **明细行乘法校验**：
   $$|\text{quantity} \times \text{unit\_price} - \text{amount}| \le 0.05$$
2. **整单总额求和校验**：
   $$\left|\sum_{i=1}^{n} \text{amount}_i - \text{total\_amount}\right| \le 0.05$$
3. **建议值生成而非静默覆盖**：
   - 若校验不通过，引擎计算出推荐修正值并生成差额说明（如“第 2 行 10 × 130 票面为 130，计算建议为 1300”），将该信息注入重试 Prompt 或在前端 UI 呈现。

---

### 3.3 双模型交叉审核 Agent ([`chains/audit_chain.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/chains/audit_chain.py))
- **设计原理**：不同大模型家族（如 Qwen 系列 vs GPT 系列 vs MiniMax 系列）具有不同的空间感知盲点与识别偏置。
- **审核逻辑**：
  - 主模型完成初次提取后，审核 Agent 调用第二模型对照原图对**供应商名称、总金额、关键明细**进行独立二次审验；
  - 若两模型一致，置信度标记为高；若两模型发生分歧，系统**绝不武断裁决，而是在前端明细行打上 Flag 标记**，将争议项置顶提醒人类复核。

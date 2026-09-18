# 09 · 组件 Spec · A/B 测试与全链路灰测分流体系

> **模块定位**：系统 AI 算法迭代与模型平滑升级的实验科学底座。承载多变量 A/B 实验管理、四级精细化流量分流路由（单据随机 / 供应商 Hash / 白名单 Allowlist / 租户分群）、实验组与对照组完全隔离设计、统计学假设检验（p-value & 置信区间），以及一键推全与秒级快照回滚机制。  
> **对标 14 步方案**：`step7-指标体系`、`step9-AI技术方案`、`step12-测试验收`、`step14-上线迭代`。  
> **实现代码**：[`app/models.py`](../../../demo/app/models.py) (`EngineConfig`, `should_use_grey`)、[`app/api_admin.py`](../../../demo/app/api_admin.py)、[`ai_registry/canary/`](../../../ai_registry/canary/)  

---

## 1. 业务背景与设计动机

### 1.1 为什么财务级 AI 系统必须依赖严格的 A/B 测试与灰测？
进货单据直接关联餐厅老板的真实资金与库存台账：
- **模型退化风险**：新模型或新 Prompt 哪怕在通用公开基准上有提升，在香港街市特定供应商的手写单据上可能产生未知的边界退化（如司马斤漏转、红蓝印章误判）；
- **Token 与时延成本平衡**：高精度模型（如 GPT-4o）成本较高，轻量级模型（如 Qwen-VL-Flash）成本低；通过 A/B 测试可在**准确率不降的前提下探索极致性价比**；
- **零中断灰度上线**：新引擎上线严禁全量一刀切，必须经历“单据抽样 5% $\to$ 20% $\to$ 50% $\to$ 100%”的灰度爬坡，并具备秒级一键回滚能力。

---

## 2. A/B 实验组与对照组参数隔离设计

系统在配置层将 **对照组（Control Group / 常规组）** 与 **实验组（Treatment Group / 灰测组）** 彻底物理隔离：

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        对照组 (Group A) vs 实验组 (Group B) 参数矩阵                    │
├──────────────────────┬──────────────────────────────────┬──────────────────────────────┤
│ 隔离参数维度         │ 对照组 A (常规线上生产组)        │ 实验组 B (Canary 灰测试验组) │
├──────────────────────┼──────────────────────────────────┼──────────────────────────────┤
│ **主识别模型**       │ `recognition_engine / model`     │ `grey_recognition_engine / model` │
│ **主识别网关配置**   │ `openai_rec_base_url / api_key`  │ `grey_openai_rec_base_url / api_key`│
│ **审核模型**         │ `audit_engine / model`           │ `grey_audit_engine / model`  │
│ **Prompt 版本**      │ `prompts/extract/v1_0_0`         │ `prompts/extract/v1_1_0_hk`  │
│ **二次解析 LLM**     │ `parse_llm_enabled` (True/False) │ `grey_parse_llm_enabled`     │
│ **生效流量比例**     │ $100\% - \text{grey\_percent}$   │ $\text{grey\_percent}\%$     │
└──────────────────────┴──────────────────────────────────┴──────────────────────────────┘
```

---

## 3. 引擎配置多规则优先级与流量仲裁矩阵 (Rule Priority & Routing Hierarchy)

当系统中同时存在**运行中的 A/B 科学实验**、**开启的金丝雀灰度发布**、**常规生产引擎配置**、**审核引擎**、**解析 LLM** 以及**系统阈值规则**时，系统按照明确的优先级与阶段分工进行仲裁执行。

### 3.1 流量路由仲裁层级 (Traffic Routing Hierarchy)

```mermaid
flowchart TD
    Req[进货单据识别请求<br/>入参: image_path, supplier_name, receipt_id] --> CheckExp{1. 是否存在 running 状态的 A/B 实验?<br/>get_running_experiment}
    
    CheckExp -->|存在运行中实验| RouteP0[【P0 最高优先级】A/B 科学实验<br/>· 强制优先接管分流<br/>· 按 target_percent 划分 Control/Treatment<br/>· 自动冻结金丝雀分流以防样本污染]
    RouteP0 --> AssignDB[落库 experiment_assign 表<br/>写入 ai_decision_log(experiment_id, grp)]
    
    CheckExp -->|无运行中实验| CheckGrey{2. 金丝雀灰度是否启用?<br/>grey_enabled == true}
    
    CheckGrey -->|开启灰度| CheckMode{3. 灰度分配模式<br/>grey_assign_mode}
    CheckMode -->|模式 A: receipt 单据随机| CalcRand[计算 random.random * 100]
    CalcRand --> CheckRand{数值 < grey_percent?}
    CheckRand -->|是| RouteCanary[【P1 次高优先级】金丝雀灰测组<br/>执行灰测识别/审核/解析配置]
    CheckRand -->|否| RouteProd[【P2 兜底基准】常规生产组<br/>执行生产主力引擎]
    
    CheckMode -->|模式 B: supplier 供应商Hash| CalcHash[计算 MD5 supplier_name % 100]
    CalcHash --> CheckHash{Hash桶位 < grey_percent?}
    CheckHash -->|是| RouteCanary
    CheckHash -->|否| RouteProd
    
    CheckGrey -->|未开启灰度| RouteProd
```

#### 分流仲裁层级详述：
1. **P0 最高优先级 · A/B 科学实验（A/B Experimentation - Running）**
   - **生效条件**：管理员在后台启动了某个实验（数据库中状态为 `running`）。
   - **分流机制**：基于 `receipt_id % 100` 或随机单据比例与 `target_percent` 比较，确定分配给 `control`（对照组基线）或 `treatment`（实验组候选）。
   - **冲突裁决**：**强制优先并挂起金丝雀灰测分流**。避免双重概率叠加造成样本群体漂移，确保后续双侧 z 检验（p-value）具有纯净的统计学因果推断基础。
2. **P1 次高优先级 · 金丝雀灰度发布（Canary Rollout）**
   - **生效条件**：无运行中的 A/B 实验，且 `grey_enabled == true`。
   - **分流机制**：支持单据随机抽样模式（`receipt`）或供应商 Hash 确定性模式（`supplier`）。
   - **运维闭环**：支持与线上参数 Diff 对比、一键推全（Promote）及秒级快照回滚（Rollback）。
3. **P2 兜底基准 · 常规生产识别引擎（Production Baseline）**
   - **生效条件**：无处于运行态的实验，且未开启灰测（或未命中灰测流量）。
   - **生效效果**：全量请求平稳路由至常规主力识别引擎（如 `gpt-4o-mini` 或已推全的主力多模态模型）。

---

### 3.2 单据流水线执行阶段协同矩阵 (Pipeline Execution Stages)

不论单据命中哪个路由组别（实验组、灰测组或生产组），单张单据在后端处理管线中均严格按照如下 6 个阶段顺序协同流转：

| 流水线阶段 | 对应配置项与门槛 | 触发时机与优先级 | 预期效果与冲突处理 |
| :--- | :--- | :--- | :--- |
| **Stage 0 预处理与极模糊质检** | `setPreprocessEnabled`<br/>`setBlurLaplacianThreshold` (缺省 30) | **最高前置优先级**，在发起任何 LLM API 调用前执行 | ① 预处理开启时先自动倾斜矫正与对比度增强；<br/>② 若 Laplacian 方差 < 30，**直接熔断拦截**，提示人工重拍。**零 Token 消耗、零模型费用**。 |
| **Stage 1 动态 RAG 经验检索** | `setMemoryBudgetFactsTokens` (缺省 800)<br/>`setMemoryBudgetMaxItems` (缺省 6) | 模型推理前 | 检索该供应商历史纠偏先验，严格受字符数与条数上限约束，避免上下文超长与幻觉。 |
| **Stage 2 多模态 VLM 主识别** | 由 P0~P2 仲裁出的具体模型标识与 Base URL | 前置门禁通过后 | 负责单据图像的整体排版解析与字段结构化抽取，输出不可信 JSON 草稿。 |
| **Stage 3 二次解析 LLM 自愈** | `adminParseEnabled` / `grey_parse_enabled` | **条件触发**：主模型输出 JSON 格式截断或 Markdown 块异常时 | 仅在语法/结构异常且开关打开时触发解析 LLM 进行修复；格式正常时**自动跳过**，消除多余时延。 |
| **Stage 4 交叉审核引擎复核** | `adminAuditEnabled` / `grey_audit_enabled`<br/>`setAuditDiscrepancySevereMinCount` (缺省 2) | 主识别/自愈完成后 | 调取独立的第二模型双盲校验单据总额与单价乘法。若严重分歧 $\ge 2$ 条，单据标记为黄色预警并扣减置信度。 |
| **Stage 5 治理飞轮回流沉淀** | `setEvalCandidateLowConfidence` (缺省 0.6)<br/>`setFeedbackDistillThreshold` (缺省 3) | 结果持久化与用户交互后 | ① 整体置信度 < 0.6 自动进入 GT 抽检确权池；<br/>② 用户连续点踩 $\ge 3$ 次触发供应商记忆蒸馏候选，经人工批准后写入 RAG 库。 |

---

## 4. A/B 实验核心观测指标体系 (Metrics & Hypothesis)

实验系统在单据生命周期全链路自动打标埋点（`experiment_id`, `variant: 'A' | 'B'`），多维统计核心指标：

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        A/B 实验全链路观测指标与评判准则                                │
├──────────────────────┬──────────────────────┬──────────────────────────────────────────┤
│ 指标类型             │ 具体度量指标         │ 优劣判定标准                             │
├──────────────────────┼──────────────────────┼──────────────────────────────────────────┤
│ **业务体验指标**     │ 人工复核修改率       │ 实验组人工修改字段数显著降低 ($\ge -30\%$)│
│                      │ 老板审批耗时 (s)     │ 单张单据平均审批停留时间缩短             │
├──────────────────────┼──────────────────────┼──────────────────────────────────────────┤
│ **算法质量指标**     │ 算术门禁自愈重试率   │ 一次性通过门禁比例显著提升               │
│                      │ 交叉审核分歧率       │ 双模型交叉复核的分歧率下降               │
│                      │ 字段提取召回率 / CER │ 字符错误率 (CER) 显著降低                │
├──────────────────────┼──────────────────────┼──────────────────────────────────────────┤
│ **系统与成本指标**   │ 单据平均端到端时延   │ P95 响应时间 $\le 45\text{s}$            │
│                      │ 单据平均 Token 成本  │ 每单 API 成本降低或在可控预算内          │
└──────────────────────┴──────────────────────┴──────────────────────────────────────────┘
```

### 统计学显著性检验 (Statistical Significance)
系统内置双样本 T 检验（Welch's t-test）与双比例 Z 检验（Two-proportion z-test）：
- **置信度设定**：$95\%$（$p\text{-value} < 0.05$）；
- **最小样本量门槛**：单实验组样本量 $N \ge 100$ 单，方可触发显著性判定。

---

## 5. 推全与秒级快照回滚机制 (Rollout & Rollback Spec)

### 5.1 一键推全 (Promote to Production)
当灰测试验组各项指标显著优于对照组且样本量充足时，Admin 可点击 **“一键推全”**：
1. 系统自动将当前的对照组配置保存至 `rollback_snapshot` 快照；
2. 将实验组参数全量赋值给常规组；
3. 将 `grey_enabled` 置为 `False`，流量 100% 切换至新配置；
4. 记录全链路审计日志。

### 5.2 秒级快照回滚 (Instant Rollback)
若推全后发生突发线上异常，Admin 可点击 **“一键回滚”**：
- 系统原子化从 `rollback_snapshot` 恢复此前稳定的生产配置，耗时 $< 50\text{ms}$，瞬间止血。

---

## 6. API 接口契约一览

| 方法 | 路径 | 入参 | 返回 / 行为 | 权限 |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/api/admin/canary/config` | 无 | 获取当前灰度策略、分流比例及对照组/实验组配置 | **Admin 独占** |
| `POST` | `/api/admin/canary/config` | `CanaryConfigPayload` | 动态调整灰度比例 (0~100%) 与分流模式 | **Admin 独占** |
| `GET` | `/api/admin/canary/report` | `experiment_id: str` | 输出 A/B 实验指标比对报告与显著性 p-value | **Admin 独占** |
| `POST` | `/api/admin/canary/promote`| `experiment_id: str` | 将灰测组配置一键推全至常规生产组 (自动生成快照) | **Admin 独占** |
| `POST` | `/api/admin/canary/rollback`| 无 | 从最近一次快照一键回滚生产配置 | **Admin 独占** |

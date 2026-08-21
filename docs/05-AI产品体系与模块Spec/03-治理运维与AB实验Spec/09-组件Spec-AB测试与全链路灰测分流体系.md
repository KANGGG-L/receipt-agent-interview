# 09 · 组件 Spec · A/B 测试与全链路灰测分流体系

> **模块定位**：系统 AI 算法迭代与模型平滑升级的实验科学底座。承载多变量 A/B 实验管理、四级精细化流量分流路由（单据随机 / 供应商 Hash / 白名单 Allowlist / 租户分群）、实验组与对照组完全隔离设计、统计学假设检验（p-value & 置信区间），以及一键推全与秒级快照回滚机制。  
> **对标 14 步方案**：`step7-指标体系`、`step9-AI技术方案`、`step12-测试验收`、`step14-上线迭代`。  
> **实现代码**：[`app/models.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/models.py) (`EngineConfig`, `should_use_grey`)、[`app/api_admin.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/api_admin.py)、[`ai_registry/canary/`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/ai_registry/canary/)  

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

## 3. 四级流量分流路由架构 (Traffic Routing Hierarchy)

```mermaid
flowchart TD
    Req[进货单据识别请求<br/>入参: image_path, supplier_name, tenant_id] --> Step1{1. 是否开启灰测?<br/>grey_enabled == true}
    
    Step1 -->|否| RouteA[分配至 对照组 A<br/>常规线上生产引擎]
    
    Step1 -->|是| Step2{2. 是否命中供应商白名单?<br/>supplier_id in grey_supplier_ids}
    Step2 -->|命中白名单| RouteB[强制分配至 实验组 B<br/>Canary 灰测新引擎]
    
    Step2 -->|未命中| Step3{3. 判断灰度分流模式<br/>grey_assign_mode}
    
    Step3 -->|模式 A: receipt 单据概率| CalcRand[计算 random.random * 100]
    CalcRand --> CheckRand{数值 < grey_percent?}
    CheckRand -->|是| RouteB
    CheckRand -->|否| RouteA
    
    Step3 -->|模式 B: supplier 供应商Hash| CalcHash[计算 MD5(supplier_name) % 100]
    CalcHash --> CheckHash{Hash桶位 < grey_percent?}
    CheckHash -->|是| RouteB
    CheckHash -->|否| RouteA
```

#### 四级分流策略详述：
1. **全局总控开关 (`grey_enabled`)**：一键开启或关闭灰测体系；
2. **定向白名单模式 (`allowlist`)**：指定特定的种子供应商（如“新记蔬菜批发”）全量走灰测试验组，便于产研精准排查特定版式单据；
3. **单据随机抽样模式 (`receipt`)**：每张上传单据独立执行随机数分配，适合大流量下无偏探索模型性能；
4. **供应商 Hash 确定性模式 (`supplier`)**：将供应商名称哈希到 100 个桶，确保同一商户的单据始终走同一模型，消除历史上下文与 VendorMemory 记忆的抖动。

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

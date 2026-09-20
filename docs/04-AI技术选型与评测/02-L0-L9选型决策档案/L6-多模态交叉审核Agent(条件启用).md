# L6 · 多模态 + 交叉审核 Agent

> 状态：**[PASS]  已评测（batch1 实测 82.2%；审核腿修复后跑通）**
> 依据：`../ai/02-方案.md` §12.2 `L6 多模态+交叉审核Agent R5 降幻觉`
> 代码：`eval/adapters/r5_audit.py`；生产链路 `services/audit_agent.py`（AUDIT_ENABLED 默认开，D35）

## 选型论证

- L5 的增量：生成器-审核器——输入原图 + 识别结果，输出逐字段一致性判定 + 分歧清单
- 验证命题：**审核的价值**（R5 vs R4 的 Δ = 降幻觉贡献）
- 落地价值：分歧进 D27 复合复核优先级分；探索期每单必审 + 交叉引擎（历史为 codebuddy↔qwen；2026-09-02 起为 DashScope Qwen 系 ↔ SiliconFlow GLM-4.5V，Gap A3）
- 设计约束：位置口径 R5——S3 契约门禁之后、S4 算术门禁之前；契约不过不进审核

## 评测记录（batch1 真实 163 张 · qwen3-vl-flash-2026-01-22）

| 时间 | 报告 | testset | 结果 |
| :--- | :--- | :--- | :--- |
| 2026-08-09 | `eval/reports/20260809_*_r5_audit_batch1.*` | batch1 163 张 | 见下 |

### 四维指标

| 维度 | 值 |
| :--- | :--- |
| 识别成功率 | **134 / 163（82.2%）** |
| 字段级 F1（54 张 samples 草稿 GT） | 0.11 |
| 时长 P50 | 17.2 s/张 |
| token | 965,185（生成+审核两腿，tokens 最高） |

### 评测修正（重要）

- 原始实现：审核腿走 codebuddy CLI（交叉引擎），但 **codebuddy 不支持看图** → 审核全失败
- 修复：评测台审核腿统一用 qwen 多模态同引擎（生成 qwen3-vl-flash → 审核同模型），`pick_audit_engine` 语义改为生成器传 codebuddy 使审核返回 qwen
- 修复后审核正常（verdict agree/disagree，分歧 11-36 条）

## 结论

- 成功率 82.2%（略低于 L4 的 100% 和 L5 的 78.5% 之间的预期位置）
- **审核腿使 token 消耗翻倍多**（965k vs L4 的 421k）——是成本最高层级
- 审核分歧可并入 D27 复核优先级分（reiview_priority_score）

## 决策

- **定论**：审核 Agent 生产默认开（降幻觉价值 > 成本），但**应条件审**（仅无表格/低分/高额单据），避免全量审核的 token 翻倍。
- 历史结论：交叉引擎（codebuddy↔qwen）在 codebuddy 无法看图时不可用（codebuddy CLI 已于 2026-09-02 弃用）；现行交叉审核为 DashScope Qwen 系 ↔ SiliconFlow GLM-4.5V。
- 评测校准（LLM-as-judge vs 人工）待标注 GT 后补。

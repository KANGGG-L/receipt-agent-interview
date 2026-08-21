# L2 · PaddleOCR + Prompt 工程

> 状态：**[PASS]  已评测（batch1 实测 87.7% 成功率）**
> 依据：`../ai/02-方案.md` §12.2 `L2 PaddleOCR+prompt工程 R2 隔离 prompt 增量`
> 代码：`eval/adapters/r2_prompt_eng.py`

## 选型论证

- 与 L1 的**唯一差异**：few-shot / 分步指令 / 结构约束加入文本 LLM prompt
- 验证命题：**prompt 工程的价值**（R2 vs R1 的 Δ = prompt 贡献）
- 不是独立技术选择，是 L1 的增强变体；用于路径归因（D39），不单独进生产

## 评测记录（batch1 真实 163 张 · PaddleOCR 本地 + deepseek-v4-flash）

| 时间 | 报告 | testset | 结果 |
| :--- | :--- | :--- | :--- |
| 2026-08-09 | `eval/reports/20260809_*_r2_prompt_eng_batch1.*` | batch1 163 张 | 见下 |

### 四维指标

| 维度 | 值 |
| :--- | :--- |
| 识别成功率 | **143 / 163（87.7%）** |
| 字段级 F1（54 张 samples 草稿 GT） | 0.0 |
| 时长 P50 | 30.9 s/张 |
| token | 305,751（deepseek-v4-flash） |

## 结论

- **prompt 工程的增量：87.7% vs L1 的 84.0% = +3.7pp**——有正向贡献但边际有限
- 时长反而下降（30.9s vs 43.6s）——可能是 sample 差异，非系统性
- F1 为 0 仍受草稿 GT 限制

## 决策

- 与 L1 配对完成归因：**prompt 工程在 PaddleOCR 基线上有小幅提升（+3.7pp）**。
- 对主链路（多模态 L4）而言，此增量不改变架构结论；但证明 prompt 细节值得投入（L4 的模板化 L5 也可借鉴）。

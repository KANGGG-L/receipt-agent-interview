# L4 · 云端多模态 VLM 直识（图 → JSON）

> 状态：**[PASS]  已定稿（主选路径；batch1 三模型对比评测完成）**
> 依据：`../ai/02-方案.md` §12.2 `L4 多模态VLM直识(云端) R3 模态升级价值 主选`
> 代码：`eval/adapters/r3_multimodal.py`；生产链路 `services/ocr_service.py` + `services/codebuddy_runner.py`

## 评测记录（batch1 真实 163 张 · 三模型对比）

| 模型 | 报告 | 成功率 | 时长P50 | tokens |
| :--- | :--- | ---: | ---: | ---: |
| qwen-vl-plus | `eval/reports/20260808_*_r3_multimodal_batch1.*` | 97.5% | 8.9s | 407k |
| qwen3-vl-flash | `eval/reports/20260808_*_r3_multimodal_batch1.*` | **100%** | 9.0s | 421k |
| qwen3-vl-plus | `eval/reports/20260809_*_r3_multimodal_batch1.*` | 99.4% | 10.3s | 420k |

### 四维指标（qwen3-vl-flash 最优配置）

| 维度 | 值 |
| :--- | :--- |
| 识别成功率 | **163 / 163（100%）** |
| 字段级 F1（54 张 samples 草稿 GT） | 0.15 |
| 时长 P50 | 9.0 s/张 |
| token / 成本 | 421,390 tokens / 原价 ≈ 0.37 元 |

### 结论

- 相对 L0（成功率 3.1%、F1=0）：模态升级带来**数量级提升**（100% vs 3.1%）
- **三模型均 ≥97.5%**：qwen3-vl-flash 全成功且最便宜（0.15/1.5 元/M）
- 输出完整契约（supplier/date/total/items/payment_mark）
- F1 受限于 GT 为 AI 草稿（未人工覆核）；**真实 F1 需标注平台写出人工 GT 后重估**
- 模型对比（D40 A 阶段）：flash 100% > plus 99.4% > vl-plus 97.5%，且 flash 最便宜——**性价比冠军**

## 决策

- **定论**：多模态 VLM 直识为主路径，**qwen3-vl-flash 为性价比首选**。
- 标注起点建议：用 r3_multimodal（qwen3-vl-flash）结果为起点（100% 成功率）。
- 生产建议：qwen3-vl-flash 主路径 + qwen3-vl-plus 质量兜底（高价值单据升级）。

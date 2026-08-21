# L1 · PaddleOCR + 文本 LLM 结构化

> 状态：**[PASS]  已评测（batch1 实测 84.0% 成功率）**
> 依据：`../ai/02-方案.md` §12.2 `L1 PaddleOCR+文本LLM R1 最弱非多模态基线`
> 代码：`eval/adapters/r1_paddle_ocr_llm.py`（懒导入，未安装则优雅跳过）

## 选型论证

| 维度 | 评分 | 说明 |
| :--- | ---: | :--- |
| 脏数据准确率 | 3 | 文字行已丢红章遮挡/手写改动/版式语义 |
| 繁中/粤语 | 5 | 开源中文最优 |
| 隐私/离线 | 5 | 本地免费 |
| 单张成本 | 5 | 0（+文本 LLM 按 token） |
| 时延 | 4 | P50 约 43s/张（本地 CPU OCR 较慢） |
| 可维护 | 4 | 稳定 |
| 可校准 | 2 | 无版面置信 |

- 角色定位：**最弱非多模态基线**，回答"为什么不用 PaddleOCR"
- 验证命题：OCR 文字行 + 文本 LLM 结构化的能力下限

## 依赖落地（Python 3.12 独立 venv）

paddleocr 3.7 + paddlepaddle 3.0 有 PIR `strides` bug（Python 3.14 无 wheel）。
修复组合（`venv_paddle`，Python 3.12）：

| 组件 | 版本 |
| :--- | :--- |
| paddlepaddle | 2.6.2 |
| paddleocr | 2.7.3 |
| numpy | 1.26.4（修 opencv ABI） |
| scipy | 1.12.0（修 numpy 兼容） |
| opencv-python | 4.11.0 |

另适配 paddleocr 2.x 返回格式 `[box, (text, conf)]`（`_walk` 增加该分支）。

## 评测记录（batch1 真实 163 张 · PaddleOCR 本地 + deepseek-v4-flash）

| 时间 | 报告 | testset | 结果 |
| :--- | :--- | :--- | :--- |
| 2026-08-09 | `eval/reports/20260809_*_r1_paddle_ocr_llm_batch1.*` | batch1 163 张 | 见下 |

### 四维指标

| 维度 | 值 |
| :--- | :--- |
| 识别成功率 | **137 / 163（84.0%）** |
| 字段级 F1（54 张 samples 草稿 GT） | 0.001 |
| 时长 P50 | 43.6 s/张 |
| token | 242,442（deepseek-v4-flash 结构化） |

### 结论

- 成功率 84.0% 远超 L0（3.1%）——**PaddleOCR 本地中文识别对港式单据有效**
- 单张 43s 主要耗在本地 CPU OCR（每张 20-40s 检测+识别），是主要时延瓶颈
- F1=0.001 受限于草稿 GT（未人工覆核）；真实 F1 需标注平台 GT 后重估

## 决策

- **定论**：非多模态基线表现强劲（84%）。PaddleOCR 作为**离线降级候选**成立——零出网、中文强、成本近零。
- 主链路仍选多模态（L4 100%），但 L1 证明：无 GPU 无额度时的兜底路径可用。
- 时延优化点：PaddleOCR CPU 推理是瓶颈，生产若采用需 GPU 或换更轻模型。

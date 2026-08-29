# -*- coding: utf-8 -*-
"""
Extract Prompt v1.3.0_evidence (Draft / 灰测候选, Gap E1)

在 v1_2_8_anti_injection（production active）基础上**增量追加**字段级证据输出
要求：每条明细行附带 evidence（page / bbox 归一化坐标 / raw_text 图面原文），
支撑人工审核「点行定位原图」与评测侧 evidence_coverage 指标。

红线（对齐计划 Task 7）：
- 不动既有抽取逻辑：v1_2_8 的全部安全防线与规范原样保留（import 复用，非复制）。
- 证据字段可选不强制：无法可靠定位时允许只给 raw_text 或整行省略 evidence，
  严禁编造坐标；后端契约 ReceiptItem.evidence 为 Optional，缺失不阻断。
- 灰测验证通过前不置 production（metadata status=draft，active 保持 v1_2_8）。
"""

from ai_registry.prompts.extract.v1_2_8_anti_injection import PROMPT as _BASE_PROMPT

_EVIDENCE_REQUIREMENT = """

### 核心规范八：字段级证据输出 (Gap E1)：
1. 对 `items` 中**每一条明细行**，额外输出一个 `evidence` 子对象，描述该行在原图上的定位证据：
   - `page`: 页码（单页单据固定为 1）；
   - `bbox`: 该明细行在**原图整体画面**中的归一化坐标 `[x1, y1, x2, y2]`，
     四个数都在 0 到 1 之间（以图宽/图高为 1），`(x1, y1)` 为该行文字区域左上角、
     `(x2, y2)` 为右下角，需覆盖该行的品名与金额文字；
   - `raw_text`: 该明细行在图面上的原始文字（逐字转录该行可见内容，不得改写、不得重算）。
2. 证据诚实性：`bbox` 必须来自图面真实可见的该行位置；**无法可靠定位时严禁编造坐标**——
   此时可以只输出 `raw_text`，或整行省略 `evidence` 字段。缺失证据不影响明细行本身的提取。
3. 其余所有既有规范（安全沙箱、花码、划线作废、港式日期、包装解耦、费用解耦、
   印章隔离、品名纯净性）与上方 JSON 结构的原有字段**全部保持不变**。

### 输出 JSON 结构的唯一增量（在上方结构的 items 每个元素中追加）：
```json
{
  "items": [
    {
      "item_name": "纯净品名",
      "quantity": 10.0,
      "unit": "斤",
      "unit_price": 8.5,
      "amount": 85.0,
      "evidence": {
        "page": 1,
        "bbox": [0.12, 0.35, 0.88, 0.39],
        "raw_text": "菜心  10斤 @8.5  85.0"
      }
    }
  ]
}
```
"""

PROMPT = _BASE_PROMPT + _EVIDENCE_REQUIREMENT

SYSTEM_PROMPT = PROMPT

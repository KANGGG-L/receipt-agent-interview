# UAT-030 交付引用准确性独立审计与错引更正规格

## 1. 审计概述
本审计针对 33-uat030-dev 与 34-uat030-qa 阶段产物中全部形如 `文件:行号` 的引用进行独立逐条核验，查明所有引用的真实指向、权威定义与潜在错引，为上线审计（CLOSE-139）提供唯一事实源。

---

## 2. 独立复现与错引裁决

### 错引一：dev-result.json 断头跨测试错引
- **引用出处**：`33-uat030-dev/dev-result.json:39`
- **引用文本**：`demo/tests/test_review_s2_01.py:39-81`
- **实际代码结构排查**（`demo/tests/test_review_s2_01.py`）：
  - 第 36–45 行：`def test_ac1_qty_three_stays_and_gate_does_not_invent_qty_two()`（准则 2 权威测试）
  - 第 47–59 行：`def test_ac2_qty_one_stays_and_warning_does_not_claim_a_rewrite()`
  - 第 61–67 行：`def test_ac3_float_one_and_string_one_stay_one()`
  - 第 69–81 行：`def test_ac4_missing_qty_is_not_filled_from_pack_count()`（准则 3 权威测试）
- **独立裁决**：
  - 第 39 行为 `assert data.items[0].amount == 300`，属于 `test_ac1` 内部第 4 行，缺少函数定义头，属于**断头引用**；
  - 该区间从第 39 行一路延伸至第 81 行，跨越了 4 个完整的独立测试函数，将“数量非1保护”（准则 2）与“缺失数量提示”（准则 3）混杂在一起；
  - **更正建议**：
    - 准则 2 权威单测行更正为：`demo/tests/test_review_s2_01.py:36-45`
    - 准则 3 权威单测行更正为：`demo/tests/test_review_s2_01.py:69-81`

### 错引二：dev-report.md 被删代码行号错引
- **引用出处**：`33-uat030-dev/dev-report.md:9`
- **引用文本**：`前端 demo/static/js/review_core.js 原第 1916–1925 行在 appendTableRow 渲染明细行时...`
- **实际代码结构排查**（`git show HEAD:demo/static/js/review_core.js`）：
  - 在修改前 HEAD 版本中，被删除的自动拆名覆盖逻辑实际位于第 **1792–1801** 行：
    ```javascript
    1792:    let finalRawName = rawName;
    1793:    let finalQty = qty;
    1794:    let finalUnit = unit;
    1795:    let finalPrice = price;
    1796:    const splitRes = smartSplitItemName(rawName);
    1797:    if (splitRes && (qty === 1 || !item.quantity)) {
    1798:        finalRawName = splitRes.cleanName;
    1799:        finalQty = splitRes.quantity;
    1800:        finalUnit = splitRes.unit || unit;
    1801:        if (amount > 0 && finalQty > 0) { ... }
    1802:    }
    ```
  - 修改前 HEAD 第 1916–1925 行实际属于 `recalcRow` 函数及其前置注释。
- **独立裁决**：
  - 开发人员在编写报告时，误将后续其它函数位置当作被修改位置；
  - **更正建议**：权威被删代码定位行更正为：`demo/static/js/review_core.js:1792-1801 (HEAD)`。

---

## 3. 全量引用独立核查对照表

| 序号 | 引用条目 | 引用文件及位置 | 实际内容核验 | 独立裁决 | 审计说明 |
|---|---|---|---|---|---|
| 1 | `01-acceptance-checklist.md:358-375` | dev-report.md:6 | [UAT-030] 需求与 5 项验收准则定义 | CORRECT | 范围与行号 100% 精确匹配 |
| 2 | `docs/review-2026-10/S2-ai-pipeline.md:52` | dev-report.md:22,40,102; dev-result.json:40; qa-report.md:41 | S2-01 权威表格行 | CORRECT | 50表头，51分隔，52为S2-01定义行 |
| 3 | `demo/app/chains/extract_chain.py:1025-1055` | dev-report.md:100; dev-result.json:38; qa-report.md:43 | 后端 S2-01 包装规格保护与提示分支 | CORRECT | 核心逻辑覆盖完整 |
| 4 | `demo/app/chains/extract_chain.py:1025-1026` | dev-report.md:22; qa-report.md:44 | 核心权威注释行 | CORRECT | 不写入 qty / 1 不是未填哨兵 |
| 5 | `demo/app/chains/extract_chain.py:1052-1055` | dev-report.md:113 | 缺 qty 提示文案分支 | CORRECT | 准确对应缺失分支 |
| 6 | `demo/tests/test_review_s2_01.py:36-45` | dev-report.md:101; qa-report.md:47 | test_ac1 数量保持 3 测试 | CORRECT | 完整测试函数边界 |
| 7 | `demo/tests/test_review_s2_01.py:69-81` | dev-report.md:113 | test_ac4 缺失数量测试 | CORRECT | 完整测试函数边界 |
| 8 | `demo/tests/test_review_s2_01.py:39-81` | dev-result.json:39 | 断头跨 4 测试混杂区间 | DEFECT | 详见错引一分析 |
| 9 | `demo/static/js/review_core.js:1916-1925` | dev-report.md:9 | 实际位于 HEAD:1792-1801 | DEFECT | 详见错引二分析 |

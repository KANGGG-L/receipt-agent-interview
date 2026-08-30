# Playbook：extract 提取 Prompt 八次迭代补录（v1_2_0 → v1_2_8）

> **背景**：这八次迭代是「飞轮转过一圈」的最早实证——每一版都由一个具体
> Gap 的失败样本触发，改动后由专项回归测试集把关。本文按
> `README.md` 六栏模板补录（此前只有版本文件名，无决策档案）。
>
> **版本出处**：`ai_registry/prompts/extract/metadata.json`
> **评测出处**：`ai_registry/benchmarks/prompt_eval_history.json`
> **测试出处**：`tests/test_gap1..8_*.py`（合计 27 条确定性用例）
>
> **GT 口径警示**：下文的 98.2% 为 **AI 草稿 GT**（`gt_status=draft`）
> 口径，人工确认分数待出；v1_2_1~v1_2_7 未在当时落量化指标，
> 相应栏位如实标「待补」，不回填、不估算。

---

## 起点：v1_2_0_sku_clean（基线）

- **触发评测**：黄金样本扩充至 95 张后的品名纯净性评测
- **假设**：剥离下划线流水号/井号编码/前缀货号/条形码，可显著提升品名纯净度
- **改动 diff**：`v1_2_0_sku_clean.py`；新增品名纯净性提取规范 + 正反例 Few-Shot
- **val 结果**：准确率 98.2%、CER 1.8、品名纯净率 99.1、编码召回 98.6、均 820 token
  （出处：`prompt_eval_history.json` 2026-08-21，dataset_size=95，**AI 草稿 GT**）
- **结论**：有效（成为后续八版的基线）
- **下次别再试**：不要在无 Few-Shot 反例的情况下做剥离规则，容易误删
  M7/规格/头数/净重等合法数字（过切率须钉在 0）

---

## v1_2_1_anti_stamp_pollution（Gap 1：印章污染）

- **触发评测**：真实单据实测发现「已付款」红章/签名/手写批注被误当成明细行
- **假设**：印章/签名仅代表付款状态，语义隔离后不会再进入 items
- **改动 diff**：`v1_2_1_anti_stamp_pollution.py`；新增「印章/签名严禁进入 items」
  约束 + Few-Shot 强化；配套确定性工具 `tools/item_sanitizer`
- **val 结果**：待补（当时未落量化指标）；回归测试集
  `tests/test_gap1_stamp_anti_pollution.py` 4 条用例全绿
- **结论**：有效
- **下次别再试**：不要靠模型「自觉」忽略印章，必须同时给确定性清洗工具兜底

## v1_2_2_anti_disclaimer_pollution（Gap 2：免责条款污染）

- **触发评测**：表头表尾的免责条款/联系电话/地址/银行账户混入明细
- **假设**：非商品杂质行（联系方式/免责/账户）可被规则 + 提示词双重隔离
- **改动 diff**：`v1_2_2_anti_disclaimer_pollution.py`；配套 `item_sanitizer` 扩充杂质模式
- **val 结果**：待补；回归测试集 `tests/test_gap2_disclaimer_anti_pollution.py` 4 条用例全绿
- **结论**：有效
- **下次别再试**：杂质清单不要写死在 prompt 里，沉淀进可测试的工具层

## v1_2_3_anti_fee_confusion（Gap 3：折让/押金/费用混淆）

- **触发评测**：整单折让、胶筐押金、运费/服务费被误计入明细或总额
- **假设**：把折让/押金/费用从明细解耦为独立结构化字段，算术门禁才能对账
- **改动 diff**：`v1_2_3_anti_fee_confusion.py`；折让→`discount_amount`、
  押金→`deposit_amount`、运费/服务费独立字段；配套 `tests/test_gap3` 的算术用例
- **val 结果**：待补；回归测试集 `tests/test_gap3_discount_deposit_math.py` 3 条用例全绿
- **结论**：有效
- **下次别再试**：不要让费用项留在明细行里「靠总额兜底」，门禁会算不平

## v1_2_4_multi_pack（Gap 4：复合包装乘数）

- **触发评测**：「大豆油 5L*2樽」类复合规格被当成单一品名，数量与单位错乱
- **假设**：复合包装规格可解耦为 品名/数量/单位 的标准结构化输出
- **改动 diff**：`v1_2_4_multi_pack.py`；配套确定性工具 `tools/smart_splitter`
- **val 结果**：待补；回归测试集 `tests/test_gap4_multi_pack_units.py` 3 条用例全绿
- **结论**：有效
- **下次别再试**：乘数拆分交给模型裸做不稳，需 `smart_splitter` 正则兜底 + 单测钉住

## v1_2_5_hk_date（Gap 5：港式日期）

- **触发评测**：港式单据默认日/月/年（DD/MM/YYYY）被误读成月/日/年
- **假设**：显式声明港式日月年惯例并归一化为 YYYY-MM-DD 可消除歧义
- **改动 diff**：`v1_2_5_hk_date.py`；配套确定性工具 `tools/date_normalizer`
- **val 结果**：待补；回归测试集 `tests/test_gap5_hk_date_normalizer.py` 4 条用例全绿
- **结论**：有效
- **下次别再试**：不要假设日期格式，缺省一律按港式日月年处理后再归一

## v1_2_6_strike_notes（Gap 6：划线拒收）

- **触发评测**：手写划线作废/拒收/短装商品仍被计入实付小计
- **假设**：划线行标 `is_void` 不计有效小计，手写验货批注抽到 `adjustment_notes`
- **改动 diff**：`v1_2_6_strike_notes.py`；契约 `ReceiptItem.is_void` / `actual_qty`
  （Optional，不阻断）
- **val 结果**：待补；回归测试集 `tests/test_gap6_strikethrough_notes.py` 3 条用例全绿
- **结论**：有效
- **下次别再试**：划线行不能直接丢弃，要保留 `is_void` 痕迹供人工复核溯源

## v1_2_7_huama_humility（Gap 7：花码虚高）

- **触发评测**：街市花码（苏州码子）/草书连笔识别置信度虚高，错误被当真
- **假设**：对花码/草书做置信度硬性校准压降，含糊字段留空并降低 confidence，
  比「硬猜一个高置信答案」更利于下游门禁与人工复核
- **改动 diff**：`v1_2_7_huama_humility.py`；配套确定性工具 `tools/huama_evaluator`
- **val 结果**：待补；回归测试集 `tests/test_gap7_huama_confidence.py` 3 条用例全绿
- **结论**：有效
- **下次别再试**：不要为了准确率数字给含糊字段虚标置信度，宁可留空降信

## v1_2_8_anti_injection（Gap 8：RAG 提示注入，当前生产版）

- **触发评测**：供应商记忆（RAG 先验）注入后存在间接提示词注入风险
- **假设**：供应商记忆先验一律经 XML 数据沙箱包裹、严禁作为指令执行，可在
  保留飞轮先验收益的同时阻断注入
- **改动 diff**：`v1_2_8_anti_injection.py`（Gap 1–8 聚合版）；配套
  `tools/prompt_injection_guard`；metadata 注明「文本零改动的补登记」
- **val 结果**：注入防御率 100%（出处：`metadata.json` v1_2_8.metrics）；
  回归测试集 `tests/test_gap8_prompt_injection_defense.py` 3 条用例全绿
- **结论**：有效（置 production，active）
- **下次别再试**：不要把外部记忆原文直接拼进系统提示词，必须先过沙箱

---

## 小结：这一圈飞轮教会了什么

1. **每个 Gap 都先有失败样本、后有改动**——评测驱动，不是拍脑袋加规则。
2. **模型约束 + 确定性工具双保险**：提示词负责「尽量做对」，工具层负责
   「错了能拦住」，两者都要有可复跑的回归测试集钉住。
3. **诚实记录**：v1_2_1~v1_2_7 当时没有落量化 val 指标，本档案如实标「待补」。
   从 T1/T4 之后，新改动必须先过 `run_eval` 出分再晋升，不再产生新的「待补」。

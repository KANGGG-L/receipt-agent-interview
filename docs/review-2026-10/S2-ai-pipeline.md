# S2 审查报告：AI 识别管线（Reviewer_Agent）

## 1. 审查范围与方法

**阅读的代码**
- 编排与链：`demo/app/chains/supervisor.py`（全文）、`extract_chain.py`（全文）、`audit_chain.py`（全文）；`review_chain.py`/`query_chain.py` 只核对了 prompt 来源
- 模型层：`demo/app/llm.py`（超时解析、错误归类、OpenAIChatModel、QwenChatModel、成本档位、`_build`）
- 服务：`contract.py`、`math_engine.py` → `ai_registry/tools/math_engine/v2_1_0.py`、`rag.py`（写入、归一、相关性门）、`receipt_utils.py`（Job、`save_parsed_data`、`build_detail`、parsing sweep）、`vendor_precheck.py`、`llm_logger.py`、`security_guard.validate_safe_external_url`、`canary_guard.verify_canary_in_text`、`prompt_injection_guard/v1_1_0_normalize.wrap_untrusted_input_sandbox`
- 相关的 API 片段：`api_receipts.py` 中的 upload/job/approve/feedback/save_edited，`api_admin.py` 中的 engine-config 和 metrics，`db.py` 中的 `get_experiment_metrics`
- `models.py` 中的 `should_use_grey`/`resolve_max_retry`/超时常量；`ai_registry` 的 prompts metadata、README 准入规则、`benchmarks/meta_eval_runs`、`eval_baselines`；`demo/scripts/run_eval.py`；前端 `main.js` 中的 `humanizeGateMsg`/`routeRecognitionFailure`/`renderFeeRowsFromData`

**跑过的流程（证据目录 `/tmp/indent/01a12a4e-a627-78c0-a7f6-25a1147af3ec/S2/`）**
1. 15010 实传样例图 `demo/samples/20161001_711_thermal_receipt.jpg`（无 key），轮询 Job 并查看单据详情。新增了 receipt_id=9，状态为 error，未删除。
2. 在 15033 另起一个独立实例（临时 DB/RAG/ARTIFACTS 目录，结束时已关闭），把识别 base_url 配成黑洞地址 `http://example.com:81/v1`，模拟网络超时：
   - 异步上传 1 次，记录耗时和决策履历
   - 同步上传（`async_=false`）1 次，同时探测 `/api/health` 的延迟
   - 核对 API key 的回显和存储
   - 上传原图写在 `demo/uploads/`，该目录已在 gitignore 中
3. 写了离线桩脚本：
   - `t_parse.py`：对 `_parse_to_receipt` 做边界探测（千分位、多包装、未知字段、NaN/Inf、超大值、贷项单、折扣行、负抹零）
   - `t_pipe.py`：用假模型跑 `run_pipeline` 的各分支（算术不符、日期非法、5xx、401、超时、多包装、未知费用字段）
   - 算术容差扫描：1806 组 qty×price 按 HALF_UP 印刷金额的组合
   - 用 node 执行前端 `humanizeGateMsg`，检查真实门禁文案
   - 用独立 DB 跑 `save_parsed_data`，核对折扣是否落库
4. 抽样单测（`-p no:cacheprovider`）：
   - 根 `tests/`：`test_canary_boundary`、`test_gap3_discount_deposit_math`、`test_per_engine_timeout_and_reasoning`、`test_round6_contract_fixes`、`test_rag_structured_fact_payload`，38 passed
   - `demo/tests/`：`test_deterministic_error_no_retry`、`test_cost_token_price_tiers`、`test_distributed_retry_limits`、`test_grey_leg_provider_alignment`，32 passed
   - 跑完后 `git status` 为空

**未覆盖及原因**
- 没有真实 LLM key：VLM/审核模型的真实输出质量、DashScope 原生通道的实际超时行为、vlm 审核模式都未实测，相关结论标注为“待验证”或仅以代码为证。
- 没有用 Playwright 走浏览器。本人负责纯后端/AI 管线，前端文案只通过 node 执行前端函数和读代码核对，浏览器实操由前端审查者负责。
- `memory_consolidate`/`memory_extract` 的 LLM 链路、`ai_registry/mcp`、`skills` 只核对了 SSOT，没有核对行为。

## 2. 维度评分表

| 维度 | 分数 | 理由 |
|---|---|---|
| 管线正确性（重试阶梯/决策履历） | 2 | 两个问题：README 所称“门禁反馈重试”和“引擎切换”在默认配置下实际不会执行；构建期异常会绕过整个阶梯和履历。 |
| 契约与算术门禁 | 2 | 后处理会直接改 AI 的数量；`extra=forbid` 被前置白名单架空；折扣等费用在落库时丢失；贷项单的任何写法都过不了门禁。 |
| 超时与降级 | 2 | 前端转手工兜得住，但实测 90 秒才失败（文档写 30/60 秒）；同步上传会冻结整个服务；AI 已解析的结果在 error 时被丢弃。 |
| 灰度发布 / A/B | 2 | 好的一面是 md5 分桶跨进程稳定。问题是：supplier 模式实际退化为随机；白名单是死代码；实验指标按灰测标记分组，结构上不可能出现有效结论。 |
| RAG / VendorMemory | 3 | 有租户硬门、待审队列和注入拦截。但“带警告”的单据可以直接 approve 并写入记忆；子串匹配的相关性门存在串商风险。 |
| 安全 | 3 | 沙箱、canary、SSRF 校验、key 掩码都在。不足：DNS 重绑定未真正防住；`llm_call_log` 不脱敏香港身份证；数据沙箱会改写数据本身。 |
| 成本与可观测性 | 3 | 估算标记做得细。但修正腿、预判腿、审核腿的成本没有计入单张成本；RMB 按 1:1 记作 HKD；parse_log 不分租户且每次请求全量读取。 |
| 代码质量 / SSOT | 2 | 降级回退的死代码约 77 处引用；多处 prompt 硬编码绕开 registry；agent_memory 文档行号指向已删除的类；生产 prompt 不满足自家准入门槛。 |

## 3. 问题清单（按严重度排序）

| ID | 严重度 | 类别 | 位置 | 问题与证据 | 对用户/业务的影响 | 修复建议 | 工作量 |
|---|---|---|---|---|---|---|---|
| S2-01 | P0 | 只报不改 / 数据错误 | `demo/app/chains/extract_chain.py:996-1000` | 多包装拆分的条件写成 `it.get("quantity") in [1.0,1,None] or it.get("qty") in [...]`。第 926 行已把 `quantity` 改名为 `qty`，所以前半句恒为 True，任何数量都会被覆盖。桩测：AI 输出 `大豆油 5L*2樽 qty=3 单价100 小计300`，最终数据变成 `('大豆油 5L', 2.0, 100.0, 300.0)`。门禁随后报“数量2.0×单价100=200，但小计=300”，把代码改出来的错算在 AI 头上（`t_pipe.py` 的 pack_qty_override 用例）。根 `tests/test_gap4_multi_pack_units.py` 只测工具本身，没测集成时的覆盖条件。 | 店员看到一个 AI 和单据上都没有的数量，审核员会以为是 AI 看错；approve 后库存数量会写错。违反 README“AI 的金额必须过校验，不静默改”。 | 删除自动覆盖，改为在原值旁给出“可能是 2 樽/箱”的建议字段，由人确认；至少把条件改成 `it.get("qty") in (1, 1.0, None)`，并补一条集成用例。 | S |
| S2-02 | P0 | 契约 / 数据丢失 | `receipt_utils.py:820-848`（`update_receipt` 只写 service_fee/tax_amount），`build_detail` 第 959-1019 行；`main.js:4840-4850` 的 `renderFeeRowsFromData(data)` 读 `data.discount_amount` | AI 识别出的 `discount_amount`/`delivery_fee`/`deposit_amount`/`rounding_adjustment` 只存进了 `ai_prefill_json`，详情顶层没有这几个字段。实测：识别“菜心 100 + 折扣 -10，总额 90”，门禁通过（90=100-10）；`save_parsed_data` 之后 detail 中 `discount_amount` 不存在，`ai_prefill.discount_amount=10.0`，items 只剩菜心 100。 | 复核界面显示“明细 100、总额 90、没有费用行”，店员看到“相差 10”，以为 AI 读错。按当前界面提交时折扣为 0，`save_edited` 会重算出门禁警告，入账的应付金额与单据不一致。 | receipts 表增加这四列，并在 `save_parsed_data`/`build_detail` 中全部回读；或让前端从 `ai_prefill` 回填。补一条“识别→详情”费用往返的单测。 | M |
| S2-03 | P1 | 管线正确性 / 文档不符 | `supervisor.py:75-85`（`_is_fast_feedback_gate` 覆盖“算术门禁/契约/总额/明细为空”），第 1084 行（只有非快速类才设 `retry_feedback`），第 974-984 行（输出质量失败直接短路）；`extract_chain.py:843-844`（解析 LLM 关闭时修正腿返回 None）；线上配置 `parse_llm_enabled=false` | README §3.2/§5.2 写的是“契约反馈重试 → 引擎切换 → 显式 error”和“不过 → 把错误喂回 prompt 重试”。实测：算术不符时调用 1 次模型，任何一轮的 prompt 都不含“门禁校验反馈”；契约类错误也是 1 次就 error。`_run_gates` 的契约校验与 `_parse_to_receipt` 是同一套，在门禁处不可能再失败，所以 `retry_feedback` 分支事实上不可达。引擎切换已“永久禁用”（`extract_chain.py:465-471`）。 | 对外宣称的自愈能力并不存在；评审和面试材料与系统实际行为不一致。真正消耗第 2、3 轮的只有上游故障和超时。 | 二选一：要么改文档，如实描述为“门禁不过即带警告交人工”；要么恢复一次带反馈的整图重识别（限 1 轮，并把 feedback 写进决策履历）。同时删除不可达分支。 | S |
| S2-04 | P1 | 降级 / 决策履历缺失 | `llm.py:1326-1329`（`_build` 中 base_url 校验失败直接 raise），`supervisor.py:870`（`_run_extract` 不在 try 内），`receipt_utils.py:404-410` | 15010 实测：上传后不到 1 秒 Job 就 error，`error_msg="安全阻断: 非法外部 API Base URL: URL 不能为空"`；receipt 9 的 `ai_decisions=[]`、`audit_logs=[]`，`artifacts/memory/parse_log.jsonl` 没有新行，`ocr_error` 事件里也没有 attempts/gate_rejects。原因是异常在 `build_recognition_model` 阶段抛出，绕过了 `extract_receipt` 的统一错误结构、重试阶梯和 `_finalize`。 | 系统“未配置引擎”这种最常见的故障完全不可审计：管理台看不到失败的原因和频次，成功率大盘漏计。文案把配置缺失说成“安全阻断”，误导排查方向。 | 在 `_run_extract` 外层捕获构建异常，转成 `error_category="param"` 的确定性失败；由 supervisor 照常写履历并走 `_finalize`。配置缺失单列 `engine_not_configured`，并在健康页提示。 | S |
| S2-05 | P1 | 超时与降级 | `llm.py:566-578`（每次调用最多 3 次 POST），`llm.py:549-552`（`OPENAI_REC_CALL_TIMEOUT` 可把所有腿抬到 600 秒），`supervisor.py:985`（未归类的超时会进入下一轮），`models.py:246`；health 文案“缺省30s 硬上限60s 超即杀进程转手工” | 15033 实测（黑洞 base_url）：Job 从 running 到 error 用了 91.2 秒，`retry_count=2`，3 轮各约 30 秒。最终 error 前用户一直在等。`requests` 的 `ReadTimeout` 没有 category，按“瞬时故障”整图重跑，理论上限 3 轮 × 3 次 POST × 60 秒。另外 Job 没有总时长看门狗，卡住的 parsing 只在服务重启时才被 sweep。 | 高压场景下店员等一分半钟才被转手工，与“超 30 秒即转手工”的承诺不符。没有 key 或网络断开时每张单都要等这么久。 | 增加管线总时长预算（如 45 秒），超出后不再开新一轮；`Timeout`/`ConnectionError` 只重试 1 次；环境变量覆盖只允许在离线评测进程中使用；sweep 改为定时执行（如每分钟）。 | M |
| S2-06 | P1 | 可用性 | `api_receipts.py:767-936`（`async def upload_receipt` 内同步执行模糊检测、图像守卫、预处理、`vendor_precheck.predict_vendor` 最长 15 秒，以及同步模式 `time.sleep(1)` × 600） | 15033 实测：发出 `async_=false` 上传后，并发请求 `/api/health` 60 秒超时（code=000），下一次请求耗时 26 秒，上传返回后才恢复为 5ms。前端不使用同步模式，但接口对 staff 开放。管理台一旦开启 `vendor_precheck_enabled`，每次上传都会在事件循环上阻塞最多 15 秒。 | 一个店员的一次请求就能让全店所有人的界面卡死；开启预判后高峰期整站变慢。 | 把 `upload_receipt` 改为 `def`（走线程池），或用 `run_in_threadpool` 包住阻塞段；删除同步模式，或改用 `await asyncio.sleep`。 | S |
| S2-07 | P1 | A/B 指标错误 | `receipt_utils.py:242-244`（实验分组 = `receipt_id % 100`，写入 `add_assignment`），`supervisor.py:478,713`（决策日志 `grp` 取自 `use_grey`），`db.py:7117-7124`（`get_experiment_metrics` 按 `r.grp` 分组），`api_receipts.py:72-80`（approve 决策 `grp` 恒为 control，且不带 experiment_id） | 实验的 treatment/control 分组与决策日志的 grp 用的是两套独立机制。灰测关闭时，某实验的全部日志都是 control，treatment 的 n 恒为 0。`accuracy` 依赖的 `adopted=1` 只写在 approve 行上，而 approve 行没有 experiment_id，所以两组 accuracy 恒为 0。z 检验的样本单位是“决策行”（每张单有多轮 extract 加 audit），不是单据，样本不独立。 | 实验看板的结论在结构上必然无效，按它做的模型上线决策没有依据。 | 决策日志的 grp 改为读取 `experiment_assignments`；approve 行写入 experiment_id；按 receipt 聚合后再做检验。灰测与实验二选一，或在日志中分两列存。 | M |
| S2-08 | P1 | 契约 / 全有或全无 | `contract.py:138-145`（日期、doc_form 枚举、单行数量非法都会让整单 None），`supervisor.py:974-984` + `receipt_utils.py:313-325`（error 时只写 `status=error`，不保存 raw 和已解析字段） | 桩测：日期为 `10/01` 时 status=error，模型只调用 1 次，全部明细被丢弃。`doc_form="thermal_receipt"`（模型很自然的写法）也会整单 error。supervisor 第 982 行日志写“已保留原始输出交人工复核”，但 Job 的 error 分支既没有写 `raw_llm`，也没有写 `ai_prefill_json`。 | 一个字段的小瑕疵就让 15 行明细全部作废，店员只能从零手工录入，逃生通道等于重来。 | 契约分为“结构致命”（非 JSON、明细缺失）和“字段可疑”（日期、doc_form、单行数值）两级：字段可疑时落 `parsed_with_warnings` 并标红该字段，doc_form 未知时回落默认值并记警告。error 时也要写 `raw_llm` 和部分字段供人工预填。 | M |
| S2-09 | P1 | 门禁 / 贷项单 | `contract.py:138`（`qty <= 0 or unit_price < 0` 一律拒绝），`math_engine/v2_1_0.py:34-36` | 实测 doc_form=correction_note、总额 -50，三种写法全部失败：负数量 → “第1行数量/单价非法”；负单价 → 同样被拒；正数量正单价负小计 → 算术门禁报“差100”。`contract.py:133` 允许负总额，但行级规则把这条路堵死了。 | 退货单、更正单永远走不通识别路径，要么整单 error，要么带着错误警告进入复核，应付账款的冲减容易出错。 | 对 CREDIT/CORRECTION 形态允许 `amount<0` 且按 `qty×unit_price×(-1)` 校验，或允许 `qty<0`；补贷项单的门禁单测。 | S |
| S2-10 | P1 | 交叉审核掩盖问题 | `receipt_utils.py:788-796`（审核 skipped 时 `discrepancies=None`，`review_priority_score=0.0`）；`audit_chain.py:187-200`（canary 握手失败也返回 skipped） | 审核超时、解析失败，甚至“审核腿 Canary 握手失败（疑似上游被篡改）”，都会得到最低复核优先级 0.0，与“审核一致”的单据同级。决策日志只记 `skipped=True`，单据状态仍是干净的 parsed。 | 安全事件和审核故障会被排到复核队列最末，店员和老板感知不到这张单少了一道检查。 | skipped 时把 `review_priority_score` 提到中高（如 0.6），在详情中提示“这张单没有经过第二次核对”；`audit_canary_blocked` 记安全事件并置 flagged。 | S |
| S2-11 | P1 | 记忆污染 | `api_receipts.py:2065`（`parsed_with_warnings` 可直接 approve），`api_receipts.py:2205-2212`（approve 后 `ingest_memory` 写入整单明细文本） | 算术门禁未通过的单据可以不经编辑直接 approve，其 items 原样作为“已确认进货明细”写入 VendorMemory 的 sample 向量，之后同一供应商的识别会把它当 few-shot 先验注入 prompt。与 S2-01、S2-02 叠加时，被代码改坏的数量和丢失折扣后的明细也会进入记忆。 | 错误会被固化为先验并越用越偏，形成“护城河”的反面。 | approve 时若 `math_warnings` 非空且未被 acknowledge，则不写记忆或只进待审队列；记忆 sample 只取 `edited` 后的人工版本。 | S |
| S2-12 | P1 | 门禁文案（人话） | `main.js:2583-2600` `humanizeGateMsg`；后端 `contract.py:157-162` `_format_validation_error` | node 实测输出：`items.0.unit_price: 填写的内容格式不对 a valid number, unable to parse string as a number`、`doc_form: 填写的内容格式不对 'printed_delivery_note', ...`、`第1行 菜心: 数量1.35×单价38.5=51.98，但小计=52.0，相差 -0.02`、`明细合计100.0 应为 100.0，但单据写着90.0，相差 10.0`、`安全阻断: Canary 令牌缺失`。字段路径、英文残句、负号差额都直接出现在 Toast 中。 | 阿叔阿姨看不懂，也不知道该核对哪个数，违反项目强制要求“100% 人话、给出差额指引”。 | 由后端产出结构化门禁结果（行号、字段、AI 值、应为值、差额绝对值），前端模板化渲染，例如“第 1 行 菜心：单据写 52 元，按数量单价算应是 51.98 元，相差 0.02 元”。契约错误一律映射为“某某一栏 AI 没读清，请对照原图填写”。 | M |
| S2-13 | P2 | 契约覆盖面 | `extract_chain.py:948-956`（item/top-level 白名单逐键 pop），`contract.py` 无上限校验 | 桩测：AI 输出 `tip=10`、`subtotal`、`credit_note` 等 schema 外字段时被静默丢弃，`err=None`，随后只看到“总额差 10”，看不到原因是小费。`qty=1e9, amount=1e15` 能通过契约。README 所说的“Pydantic extra=forbid 拒绝 schema 外字段”对 VLM 输出不成立。 | 隐藏费用无提示消失，金额异常大的单据（小数点错位）不被拦截。 | 被丢弃的键记入 `math_warnings`/决策履历（如“AI 识别到未知费用 tip=10”）；契约增加金额、数量上限（如单行 ≤ 100 万、总额 ≤ 500 万），超限判为可疑。 | S |
| S2-14 | P2 | 算术容差 | `math_engine/v2_1_0.py:34-36`（`round(qty*price,2)` + 绝对容差 0.01） | 1806 组 HALF_UP 印刷金额扫描中有 47 组误报（2.6%），例如 `1.01×18.5=18.685`，单据印 18.69，引擎算 18.68，浮点差 0.0100000x 大于 0.01。街市常见的“行金额取整到元”（1.35×38.5=52）也被报为错误。 | 收据本身没错却被标黄，店员对门禁失去信任，误报会掩盖真问题。 | 用 `Decimal` 加 ROUND_HALF_UP，容差改为 `0.01 + 1e-9`；行级允许“取整到 0.1/1 元”的模式，命中时只提示不报错。 | S |
| S2-15 | P2 | 算术语义 | `math_engine/v2_1_0.py:26-27` | 设置 `actual_qty` 时行金额直接用 `qty×price` 重算，AI 给的 `amount` 被忽略：`qty10 价10 amount=999 actual_qty=8 total=80` 能通过。`actual_qty=0`（全拒收）因为 `>0` 判断回落为原数量，100 元仍计入合计。 | AI 金额读错的行在有验货调整时完全不被发现；全拒收行仍计成本。 | 行级校验始终用 AI 的 amount 与 `qty×price` 比对；有效金额用 `actual_qty`（含 0）另行计算并在提示中说明。 | S |
| S2-16 | P2 | 灰度发布 | `models.py:884-906`；`receipt_utils.py:299-301`（`supplier_name=vendor_hint`）；`supervisor.py:759`（未传 supplier_id） | md5 分桶跨进程稳定，这一点没问题。但 supplier 模式取决于 `supplier_name`，而上传时多数单据没有 vendor_hint（预判默认关闭），于是落入 `random.random()`，“同供应商一致命中”失效。名称未归一（繁简、空格、后缀不同会进不同桶）。`grey_supplier_ids` 白名单只认 supplier_id，而调用方从不传，属于死功能。 | 按供应商灰测时同一家供应商的单据在两组之间跳来跳去，对比不可信；配置了白名单也不生效。 | supplier 模式无供应商时归入常规组（不随机）；名称先经 `rag._norm_vendor_name` 归一再 hash；白名单支持按名称匹配，或在识别后二次判定。 | S |
| S2-17 | P2 | 成本口径 | `supervisor.py:1017-1022`（修正腿不取 token），`vendor_precheck.py:185-200`（预判腿未计成本），`supervisor.py:1170-1174`（审核成本只进 audit 行，不进 state/parse_log）；`llm.py:818`（RMB≈HKD 1:1） | parse_log 和 Job 中的“单张成本”只含识别腿；开启解析修正或 vlm 审核后单张真实成本被低估。`cost_hkd` 实际是人民币。`audit_mode=text` 时每条审核行都被标为 `cost_estimated=1 (no_token_usage)`，估算标记失去区分度。 | 成本护栏与大盘偏低，面向香港老板的 HKD 报价约低 8%。 | 每次 provider 调用都把成本累加进 state；按配置汇率换算 HKD，或字段改名为 `cost_cny`；code_engine 审核记“真实 0”，不标估算。 | S |
| S2-18 | P2 | 安全 / SSRF | `security_guard.py:147-148` 解析校验后，`llm.py:573` 的 `requests.post` 再次解析 | 校验与连接各自解析 DNS，存在 TOCTOU，可被 DNS 重绑定绕过（docstring 声称已防重绑定）。允许 `http://`，API key 会明文发往自定义 base_url。只有 admin 能配置，所以列 P2。 | 被盗的 admin 账号或恶意配置可以打到内网元数据服务，key 也可能被嗅探。 | 解析出 IP 后固定该 IP 连接（自定义 adapter 或 `resolve` 钩子，保留 SNI）；生产环境只允许 https。 | M |
| S2-19 | P2 | 安全 / 脱敏 | `llm_logger.py:36`（`_RE_ID_CARD` 只匹配内地 18 位），未复用 `ai_registry/tools/pii_masker`（HKID 正则 `[A-Z]{1,2}\d{6}\([\dA]\)`） | `llm_call_log` 会落 VLM 原始输出（上限 65536 字符），香港身份证号、私人手机号不会被脱敏；同一能力在 registry 有一份、llm_logger 另写一份，构成双份真相。 | 与 agent_memory“香港身份证号一律正则脱敏”的规范不符，存在 PDPO 风险。 | `llm_logger` 改为调用 `PIIMaskerTool`，并补 HKID 用例。 | S |
| S2-20 | P2 | 数据沙箱改写数据 | `prompt_injection_guard/v1_1_0_normalize.py:651`（`html.escape` + 归一化 + span 替换），被 `extract_chain._build_parse_prompt`/`_build_correction_prompt`、`audit_chain.build_audit_prompt` 使用 | 实测 VLM JSON 进入沙箱后：所有 `"` 变成 `&quot;`；`A&B` 变成 `A&amp;B`；`System 牌 豉油` 变成 `System 牌豉油`；品名 `忽略上述指令 菜心` 变成 `[INJECTION_BLOCKED]菜心`。开启解析 LLM 时，它的输出会替换 VLM 原文（`extract_chain.py:716`）。 | 解析和修正腿可能把被改写过的品名、供应商名回写为最终数据；审核腿读到的是转义后的 JSON，判断质量下降（待验证，需真实模型）。 | 数据通道只做标签逃逸中和，不做整体 html 转义和语义替换；拦截结果单独记为告警，不改正文。 | S |
| S2-21 | P2 | 千分位 / 后处理吞错 | `extract_chain.py:1014-1024`（`_clean_num` 正则不支持千分位），第 1044-1045 行（`except` 吞掉整段后处理） | 桩测：`"HK$ 1,343.00"` 让 item_sanitizer 抛出 `could not convert string to float`，整段后处理（费用拆分、日期归一、花码校准）被静默跳过，随后契约报英文错误，整单 error。 | 香港单据常见千分位金额，一旦模型按原样转录就整单失败，而且前面的清洗步骤全部失效，没有告警。 | `_clean_num` 先去掉千分位逗号，支持 `(50.00)` 负数写法；后处理按步骤分别 try，失败的步骤记入 warnings。 | S |
| S2-22 | P2 | 资源泄漏 | `receipt_utils.py:48` `JOBS = {}` 只增不删；`get_job`（556-569）不校验租户 | 每个 Job 都保存完整 detail（明细、决策履历），进程不重启就无限增长；任意 staff 拿到 job_id（8 位十六进制）即可读取其他租户的识别结果（与 S1 的租户审查交叉）。 | 长期运行时内存持续上涨；开启 AUTH 后存在跨租户读取面。 | 终态 Job 保留 N 分钟后淘汰（TTL/LRU）；`get_job` 比对 Job 的租户。 | S |
| S2-23 | P2 | 可观测性 | `supervisor.py:1264-1289`（parse_log 不含 tenant_id）；`api_admin.py:2518-2545`（每次请求逐行读全文件） | parse_log 只受进程内 `threading.Lock` 保护，多 worker 部署时没有互斥；没有轮转；不分租户，管理端 metrics 的文件层统计混合了所有租户；`ai_value.elapsed_ms.extract` 写的是累计值（实测 3 轮分别为 30058/60092/90104），会被误读为单轮耗时。 | 大盘数据不分租户且越来越慢，单轮耗时被误读。 | 增加 tenant_id 并按日轮转（或只以 DB 为准）；metrics 改为 DB 聚合；履历同时写单轮耗时和累计耗时两个字段。 | S |
| S2-24 | P2 | SSOT / 资产治理 | `vendor_precheck.py:47`、`memory_consolidate.py:119`、`memory_extract.py:115` 内联 system prompt；`ai_registry/prompts/extract/metadata.json` 的 production 版本 v1_2_8 没有任何准确率/CER 指标；`ai_registry/benchmarks/meta_eval_runs/*_audit.json` 三份均为 `evaluator_trustworthy=false` | agent_memory 规定“禁止在 demo/app 内散落硬编码系统提示词”。vendor_precheck 完全没有进 registry；另外两处保留了与 registry 等价的回退副本。`ai_registry/README.md:113-119` 的准入规则（准确率 ≥95%、CER ≤4%、真实 judge 元评测 trustworthy=true）当前生产版本一条都不满足。 | 资产治理只停留在纸面，生产 prompt 没有可追溯的质量数据。 | 预判 prompt 迁入 registry；删除回退副本（registry 不可用时显式失败并告警）；生产 prompt 补跑 test 集或降级为 draft 状态。 | M |
| S2-25 | P2 | 死代码 / 文档漂移 | `extract_chain.py:447-471`（`_describe_engine_failure` 无调用方，回退已“永久禁用”）；supervisor/receipt_utils/main.js 共约 77 处 fallback 引用；`agent_memory.md` 第 3 节引用的 `llm.py:154 CodeBuddyChatModel`/`~260 OpencodeChatModel` 已删除；health 的 `timeout_policy` 写“超即杀进程转手工” | 回退字段恒为 False，前端仍保留“AI 模型与备用模型均调用异常”的文案分支。文档行号指向不存在的类，“杀进程”描述的是已删除的 CLI 引擎。 | 维护者被误导，测试和文档维护成本上升。 | 一次性清理回退字段链路（后端、前端、测试）；更新 agent_memory 和 health 文案。 | M |
| S2-26 | P2 | 模型层超时 | `llm.py:941-953` `QwenChatModel._generate` 调用 `client.call(**call_kwargs)` 时没有传超时 | 走 DashScope 原生 SDK 的路径（qwen 前缀 + 非 openai 引擎）不受 30/60 秒管控，可能长时间挂起（待验证，取决于 SDK 默认超时）。 | Job 线程可能卡死，单据停在 parsing 直到服务重启。 | 用带超时的 future 包裹，或给 SDK 传 `timeout` 参数；与 OpenAI 通道共用 `_resolve_timeout`。 | S |
| S2-27 | P2 | 评测有效性 | `demo/scripts/run_eval.py:610-639`（`_real_predictor` 只调 `extract_receipt`，用默认 `EngineConfig()`，不传 vendor_hint） | 评测衡量的是“单次抽取 + 后处理”，不含门禁、记忆注入、线上的 max_tokens/temperature/image_detail 配置，与生产 `run_pipeline` 不是同一个系统。好的一面：test 默认 `--require-confirmed`，没有发现调参脚本读取 test 集（未见数据泄漏）。另外 `eval_baselines/test_weekly_baseline.json` 记载 2026-10-11 曾重冻结，S090/S136 的 GT 丢失且无备份。 | 评测分数无法说明线上效果，记忆功能的收益没有被度量。 | 增加 `--pipeline` 模式，走 `run_pipeline` 并读取部署配置；记忆开和关各跑一次 A/B；GT 目录纳入备份。 | M |
| S2-28 | P3 | 审核结果类型 | `audit_chain.py:312-315` | 原样采用 LLM 返回的 `overall_consistent`、`trust`：字符串 `"false"` 为真值，会被 supervisor 第 1160 行判为 audit_pass；`trust` 不做范围校验。 | vlm 审核模式下可能把分歧误判为一致（待验证）。 | 严格转换为 bool，`trust` 限制在 [0,1]，非法时判为 skipped。 | S |
| S2-29 | P3 | 文档不符 | README §3.2.1“图内隐藏文字检测只告警、不拦截”与 `api_receipts.py:818-861`（高置信命中时直接置 flagged，不发起识别） | 代码实际会拦截，文档说不拦截。 | 运维对单据为什么不识别有错误预期。 | 改文档或改行为，二者保持一致。 | S |
| S2-30 | P3 | API key 存储 | 15033 实测：GET/PUT 响应中 key 已掩码（`sk-s2f***...fXYZ`），但 SQLite 中是明文 `sk-s2fakekey1234567890abcdefXYZ` | 演示环境可以接受，生产 SaaS 不可接受。 | 库文件泄露即 key 泄露。 | 用 KMS 或环境主密钥加密存储。 | M |
| S2-31 | P3 | 测试后门 | `llm.py:484-524` `MOCK_QWEN_FLASH=1` | 生产代码路径中，模型名包含 `qwen3-vl-flash` 时，任意图片都返回固定的“新協興 1080”结果。 | 环境变量误设会把假数据写进账本。 | 移到测试桩或测试专用 transport，生产启动时检测到该变量即拒绝启动。 | S |

## 4. 做得好的地方

- 重试阶梯对失败类别有明确区分：401/403/400、图片解码失败不重试，5xx/超时才重跑（`supervisor.py:104-113`，桩测已验证 auth 只调用 1 次、5xx 调用 3 次），注释写清了每类失败是否会消耗额度。
- 门禁失败时不静默当成功：有 `parsed_with_warnings` 状态，`_finalize` 中 `success=False`，`ocr_parsed` 事件单列 parse_status，P14/P15 的修复是到位的。
- 成本可信度标记 `cost_estimated` 和 reason 贯穿 extract → supervisor → DB → parse_log，能区分“真实 0 成本”和“算不出来”。
- 灰测 supplier 分桶用的是 md5，不是 Python `hash()`，跨进程、跨重启稳定。
- 记忆写入有多道治理：feedback 只进待审队列、写入侧做注入检测、租户为必填、DB 写入失败时不产生孤儿向量。
- 前端兜底：只要不是画质问题，失败都会自动转手工补录并保留原图（error 单据 9 的 `image_url` 仍然可用）。

## 5. 优先级最高的 5 项修复建议（按投入产出比）

1. **S2-01 + S2-02（S+M）**：删除多包装数量的自动覆盖；四个费用字段落库并回读。改动小，直接消除“系统改了 AI 的数”和“折扣凭空消失”两类账目错误，同时切断 S2-11 的记忆污染来源。
2. **S2-04 + S2-06（S）**：引擎构建异常收进统一错误结构，保证有决策履历；`upload_receipt` 改为线程池执行，并删除同步轮询。半天工作量可以去掉“整站冻结”和“未配置引擎时无迹可查”两个线上风险。
3. **S2-10 + S2-11（S）**：审核 skipped 或 canary 失败时提高复核优先级并提示用户；带警告未确认的单据 approve 时不写 VendorMemory。用很小的改动守住“交叉审核”和“记忆飞轮”两道防线。
4. **S2-05 + S2-08（M）**：增加管线总时长预算（如 45 秒），网络类错误只重试 1 次；契约分级，字段可疑时保留明细，降级为 `parsed_with_warnings`。直接改善阿叔阿姨的等待时长，也避免他们从零重录。
5. **S2-07 + S2-12（M）**：实验指标改为按实验分组并按单据聚合；门禁结果结构化、文案模板化（“单据写 X 元，按数量单价应是 Y 元，相差 Z 元”）。前者让模型上线决策有依据，后者满足项目对人话门禁提示的强制要求。

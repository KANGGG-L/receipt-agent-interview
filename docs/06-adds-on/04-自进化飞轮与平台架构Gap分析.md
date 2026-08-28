# Gap Analysis：自进化飞轮 × Document AI 平台架构 对照现有设计

> 对照基准：
> A. `Obsidian Vault/00-Inbox/Agent自进化飞轮：评测→记忆→落地→控制.md`（四齿飞轮：信号 / 积累 / 落地 / 控制）
> B. `docs/06-adds-on/document-ai-system-architecture.html`（单据识别 + 系统管控平台五层架构）
>
> 审视对象：`demo/`（可运行系统）+ `ai_registry/`（资产与评测中心）+ `docs/04-AI技术选型与评测/`（评测与样本库规范）
>
> 分析日期：2026-08-28　方法：逐条要求 → 代码/文档实证 → 判定覆盖度。判定均给出 `file:line` 证据。

---

## 一、结论总览

**一句话结论：飞轮的「骨架」已经搭起来了，而且骨架质量高于多数同规模项目——但飞轮目前是「人推着转」的，不是自己在转。**

| 维度 | 覆盖度 | 判定 |
| :--- | :--- | :--- |
| 信号（评测） | **60%** | 有工具、有指标、有 A/B、有显著性检验；但**评测信号本身的可信度未受保护**（GT 污染 + 无三分法 + 无元评测集） |
| 积累（记忆） | **35%** | 有写入策展的正信号原则与租户隔离；但**治理能力（版本/TTL/冲突/遗忘/预算）几乎全缺** |
| 落地（工程化） | **45%** | 有版本化、灰度、回滚、准入门槛；但**八环节中「生成候选 / 独立评测 / 自动回滚 / Dreaming / Playbook」缺失**，改进全靠人手 |
| 控制（人机协作） | **30%** | 有三层确定性门禁与人工审核闭环；但**无分级自主、无规则级记忆写入闸、无对齐漂移防护** |
| 平台架构（五层） | **50%** | 五层概念齐全且分层清晰；但**证据存储、多租户、模板/规则配置化、预处理 Pipeline 四处硬缺口**，Infra 层为单机 Demo 形态 |

**最关键的三条（如果只补三件事）：**
1. **评测可信度**：57 张黄金集中 54 张 GT 是 AI 草稿，等于用模型评模型 —— 飞轮第一原则是「评测的可信度 > 系统的复杂度」，这是当前最致命的一条。（详见 A2）
2. **证据存储**：字段级证据（页码/坐标/原始文本）完全缺失，架构文档把它列为风险 #1，当前无法实现「可审计 + 人工审核提效」。（详见 E2）
3. **规则级记忆写入无人工闸**：连续 3 次点踩自动沉淀进全局生效的供应商记忆，正踩中「五个必须有人节点」的第 ① 条红线。（详见 D2）

---

## 二、飞轮第一齿：信号（评测）

### 已有（做得好的部分）

| 要求 | 现状 | 证据 |
| :--- | :--- | :--- |
| 底层规则校验（零成本、无偏差） | 契约门禁（Pydantic `extra=forbid`）+ 算术门禁（零 token、拦截率 100%） | `demo/app/models.py:29,42`；`ai_registry/tools/math_engine/v2_0_0.py`；`benchmarks/tool_eval_history.json` |
| 细粒度/轨迹级评测 | `ai_decision_log` 记录 `field_path` / `ai_value` / `user_value` / `adopted` / `is_hallucination`，用户修正是**字段级**的 | `demo/app/db.py:197-217`；`demo/app/api_receipts.py:645,707` |
| 质量门控 + 统计显著性 | `ABEvaluator` 双比例 Z 检验 + p-value + `min_sample` 门槛；`experiment.guardrail_metrics` 字段 | `ai_registry/canary/ab_evaluator.py:28-45`；`demo/app/db.py:254-271` |
| 预算公平性（防"用钱砸分"） | 跨版本显式跟踪 `avg_tokens`（620→780→820）与 `latency`/`cost_hkd` | `ai_registry/benchmarks/prompt_eval_history.json`；`demo/app/db.py:646-720` |
| 小样本不可信 | `ai_metric_snapshot` 带 `sample_size`，<30 仪表盘标灰 | `demo/app/db.py:222-239` |
| 小样本/方向指引的看板 | `/api/admin/metrics`、`/api/admin/experiments/{id}/pvalue`、`/api/analytics/recognition-summary` 分组对比 | `demo/app/api_admin.py:646,1209,1247` |
| 资产版本化 + 生产准入门槛 | SemVer + `metadata.json` + 提取类准确率 ≥95%/CER ≤4%、门禁类拦截率 100% | `ai_registry/README.md:92-98` |

### Gap A1　评测集无 train / val / test 三分法（P0）

- **要求**：train 用于诊断（可暴露给 LLM）、val 用于筛选（**绝不暴露**给生成修复的 LLM）、test 用于终验，三者互不泄漏；val 每 2-4 周换 20-30%。
- **现状**：黄金样本库是**单一集合 57 张**，诊断、改 prompt、验收共用同一批。`docs/01-产品方案(14步)/step12` 的 Dev46 / Holdout11 是**功能测试集**划分，不是模型评测集划分。
- **证据**：`docs/04-AI技术选型与评测/04-黄金样本库/01-样本集规范与结构.md`（单集，无 split 字段）；`manifest.csv` 字段里没有 split 列。
- **风险**：v1_2_0 → v1_2_8 八次 prompt 迭代全部对着同一批样本调，accuracy 98.2% 是**刷出来的分数**，不是泛化能力。这是评测集过拟合的典型形态。
- **建议**：给 `manifest.csv` 加 `split` 列，按 doc_form 分层抽样切 57 张为 train 30 / val 12 / test 15；把 v1_2_x 系列的效果在 test 上重跑一次，拿到"干净的"数字。这是低成本高收益的第一步。

### Gap A2　Ground Truth 本身是 AI 草稿 —— 自洽偏差（P0，最致命）

- **要求**：生成模型与评估模型必须分离，否则触发自洽偏差；错误的正反馈比没有反馈更可怕。
- **现状**：57 张中 **54 张的 expected 是 AI 转录草稿待人工覆核**，仅 3 张网络热敏已人工覆核。也就是说：评测的"标准答案"是模型自己产出的。
- **证据**：`docs/04-AI技术选型与评测/04-黄金样本库/01-样本集规范与结构.md:8-11` 自述「expected 为 AI 转录草稿，待人工覆核」；同库 `03-GroundTruth标注填充报告.md`。项目自己的粗糙基线测试诚实承认 PASS 仅 7/57，原因是「GT 是 AI 草稿被双惩罚压低」。
- **风险**：这不是"分数不准"，是**飞轮有可能朝错误方向加速**。用 A 模型的输出当标准答案去评 B 模型，B 会学到 A 的系统性错误并写进 prompt 与记忆。
- **建议**：优先人工覆核 test 集那 15 张（工作量可控），把 test 集先变成"人认证的可信标尺"；train/val 可以晚一点。任何对外的准确率数字只引用 test 集。

### Gap A3　审核腿（评估器）与识别腿未强制分离（P1）

- **要求**：让 Agent 评价自己的输出会触发自洽偏差，需独立模型实例或不同模型；评估 temperature 设 0；输出结构化；分维度独立评估。
- **现状**：`audit_engine` / `audit_model` 与 `recognition_engine` / `recognition_model` 结构上**已经分开配置**（这点设计是对的），但默认值是**同一个引擎同一个模型**（`opencode` / `opencode/mimo-v2.5-free`）；且 `audit_mode` 默认 `text`（纯文本确定性校验，不重读原图）。
- **证据**：`demo/app/models.py:167-183`（`audit_engine` 默认 `opencode`，`audit_model` 默认与识别同源）；`demo/app/chains/supervisor.py:112-121`。
- **风险**：默认配置下 = 同模型自评。且 text 模式看不到版面，抓不到"字段抄对了但抄错行"这类错误 —— 恰好是 VLM 相对优势所在的场景。
- **建议**：把生产默认改为**跨厂商异构**（识别用 A 厂 VLM、审核用 B 厂 VLM），并把"评估器异构 + temperature=0"写进 `ai_registry/README.md` 的生产准入规则。

### Gap A4　评估器本身没有被评测（P1）

- **要求**：维护一小批"元评测集"（几十条明确标注绝对正确/绝对错误），定期校验评估器；评估器连极端 case 都判错则告警。
- **现状**：无元评测集，无评估器人工一致性抽检，无 temperature 约定。
- **证据**：全仓 grep `元评测\|meta_eval\|judge` 无结果；`ai_registry/benchmarks/` 只有效果历史，没有评估器可信度记录。
- **建议**：从已有 24 个对抗性测试（`tests/test_hallucination_adversarial.py`、`test_gap1..8`）中抽出 20-30 条已确证的"必错/必对"样本固化成 `benchmarks/meta_eval_set.json`，每次改评估器或换模型先跑它。

### Gap A5　评测集漂移 + 配额缺口（P1）

- **要求**：评测集定期更新，把线上新失败样本回流替换已刷过的旧样本。
- **现状**：57 张静态库，无刷新节奏；且**街市手写单 `docket_handwritten` 与磅单 `weight_note` 实际库存为 0**，而规范自定配额 ≥18 张手写单。
- **证据**：`01-样本集规范与结构.md`「目标分布」表与「当前库存」段（形态以 delivery_note / statement 为主，街市手写/磅单配额仍缺）。
- **风险**：评测集分布与真实业务分布背离，且缺失的恰好是产品差异化场景（VLM 相对 OCR 的优势区）。线上跑得最多的单型，评测集里一张都没有。
- **建议**：把"线上低置信样本自动入候选池"做成一条通路（现有 `ai_decision_log.confidence` 已具备筛选条件，缺的是入库动作），每月人工挑选入 val/test。

### Gap A6　Skill 四层验证只做了第 0 层（P2）

- **要求**：①有无 Skill 对照实验 ②难度梯度 ③轨迹追踪（运行时真的 follow 了吗）④路径验证（结果对但路径对吗）。
- **现状**：`ai_registry/skills/*/eval.json` 只有 `benchmark_score` / `pass_rate` / `test_cases_passed` —— 纯**结果层**打分。
- **证据**：`ai_registry/skills/receipt_auditing/eval.json`（仅 4 个指标，无 ablation、无 trace 字段）。
- **建议**：至少补 ①：`eval.json` 增加 `ablation: {with_skill, without_skill}` 两组通过率。这是"证明 Skill 真的有用"最便宜的一层。

---

## 三、飞轮第二齿：积累（记忆）

### 已有

| 要求 | 现状 | 证据 |
| :--- | :--- | :--- |
| 策展而非全存（正信号才写） | `ingest_memory` 明确"人工 approve 后回写，只认正向信号" | `demo/app/services/rag.py:97-119` |
| 失败也存，但存法不同 | 连续 3 次点踩才沉淀为 `feedback_distilled`；单次点踩不沉淀（防错误记忆自我强化） | `demo/app/services/rag.py:249-275`；`demo/app/models.py:109` |
| 防检索噪声 | `_vendor_related` 相关性门：归一后需包含/共享别名组才放行，防他商记忆泄漏 | `demo/app/services/rag.py:176-200` |
| 租户硬隔离 | 每租户独立 Chroma collection + 独立实例 | `demo/app/services/rag.py:64-94` |

### Gap B1　记忆无治理元数据 —— 记忆是"存储"不是"治理系统"（P0）

- **要求**：版本控制（版本号 + 来源标记：哪个会话/谁触发/因何写入）、演化、主动遗忘、冲突解决。
- **现状**：`VendorMemory` 只有 `vendor / notes / sample` 三个字段，**无版本、无来源、无时间戳、无淘汰标记**；Chroma metadata 只有 `vendor / kind / tenant_id`。
- **证据**：`demo/app/models.py:97-103`；`demo/app/services/rag.py:104-112, 259-261`。
- **风险**：对应十大难点中的 ⑤污染 / ⑥时效衰减 / ⑦冲突 / ⑧成本失控 **同时命中**。一条错误记忆写入后无法定位来源、无法单独撤销、无法过期，在被发现前会持续影响该供应商后续所有单据。
- **建议**：`vendor_memory` 表加 `version / source_receipt_id / source_kind(approve|feedback|manual) / created_at / hit_count / last_hit_at / decay_score / status`；Chroma metadata 同步带 `memory_id` 以便回溯删除。这是记忆治理的地基，其他几条都依赖它。

### Gap B2　无三层晋升与非对称淘汰（P1）

- **要求**：低门槛先解决"忘记记录"，再逐层晋升；好经验强化 (+0.05) 远慢于坏经验淘汰 (-0.12)。
- **现状**：二元写入 —— approve 即写 / 连续 3 踩即写。**没有"被证伪则快速淘汰"的回路**：一条沉淀记忆即使后续一直被用户改掉，也不会被降权或移除。
- **证据**：`ingest_feedback_memory` 只做 append（`rag.py:268-274` 是 notes 字符串累加，不是评分更新）。
- **建议**：引入 `decay_score`，被采纳 +0.05、被用户覆写 -0.12，低于阈值自动 `status=archived` 不再注入。

### Gap B3　无分层架构（P2）

- **要求**：L0 原始对话 → L1 原子事实 → L2 场景块 → L3 用户画像，默认用顶层、按需钻取。
- **现状**：单层 —— Chroma 一个池子 + `vendor_memory` 表一条记录。
- **证据**：`demo/app/models.py:97-103`。
- **影响**：记忆条数增长后，检索质量与注入成本都会线性恶化。

### Gap B4　读取无预算控制（P1）

- **要求**：分层 Token 预算（Skill ≤2000 / Facts ≤800 / Profile ≤500）、单条 ≤200 Token、最多 6 条、按任务复杂度动态调整。
- **现状**：`retrieve_context(top_k=3)` **无任何 token/字符上限**；精确匹配分支把 `notes + sample` 全量拼接注入；`ingest_feedback_memory` 把 notes 累积到 **4000 字符**上限。
- **证据**：`demo/app/services/rag.py:203-246`（top_k=3，无截断）；`rag.py:271`（`[-4000:]` 累积截断）。
- **风险**：供应商记忆越攒越长 → 注入上下文越长 → 挤压 VLM 工作窗口、抬高 token 成本、分散注意力。这是"不治理的记忆不如没有记忆"的典型触发路径。
- **建议**：注入前按 budget 截断（建议 Facts ≤800 token / 单条 ≤200 token / 最多 6 条），并把 4000 字符累积改为按条存储 + 检索时取 Top-K，而非全量拼接。

### Gap B5　反例记忆未结构化（P2）

- **要求**：最有价值的知识是"什么不能做"，一个明确的反例比一个模糊的正例更有价值。
- **现状**：`feedback_distilled` 存的是自由文本 comment，**没有结构化的"陷阱条目"**，检索时也不优先召回反例。
- **证据**：`demo/app/services/rag.py:249-262`。
- **建议**：沉淀时抽一条结构化 `{"trap": "...", "why": "...", "evidence_receipt_id": ...}`，检索时对 `kind=trap` 提权。

---

## 四、飞轮第三齿：落地（工程化）

### 已有

| 要求 | 现状 | 证据 |
| :--- | :--- | :--- |
| 版本化一切 | Prompt/Tool SemVer + `metadata.json` + active_version 切换 | `ai_registry/prompts/*/metadata.json`；`registry.py:27-54` |
| 灰度发布 | Canary 分流（白名单 / 单据随机 / 供应商 Hash 三模式）+ 灰测组与常规组配置完全隔离 | `ai_registry/canary/router.py`；`demo/app/models.py:227-263` |
| 可回滚 | `rollback_snapshot` 一次快照 + `/api/admin/engine-config/rollback` + `promote` + `diff` | `demo/app/models.py:262`；`demo/app/api_admin.py:206,314,346` |
| 变更日志 | `audit_logs_json` + `ai_decision_log` | `demo/app/db.py:79,197` |
| 三路信号之"本轮诊断" | `ai_decision_log` + snapshot 聚合 | `demo/app/db.py:1548-1600` |
| Diff 式改动的意识 | Prompt 演进是小版本叠加（v1_2_0 → v1_2_8 各自独立文件），不动旧版本 | `ai_registry/prompts/extract/` 目录 |

### Gap C1　无 Playbook（怎么改 Agent 的经验库）（P1）

- **要求**：本轮试了哪些方向、哪些有效/无效、为什么，写入 Playbook，供下一轮参考，避免重复踩坑。
- **现状**：**完全缺失**（全仓 grep 无 `playbook`）。v1_2_1 → v1_2_8 八次迭代的动机与结论只存在于文件名和 commit message。
- **风险**：每一轮改进都从零开始；同样的弯路反复走。而且这段演化史是面试时最有说服力的素材，目前散落不可复用。
- **建议**：建 `ai_registry/playbook/YYYY-MM-<change>.md`，模板固定为「触发评测 / 假设 / 改动 diff / val 结果 / 结论（有效|无效|回归）/ 下次别再试」。八次历史迭代先补录进去，成本很低、叙事价值很高。

### Gap C2　无 Dreaming 异步巡检（P2）

- **要求**：异步进程定期审阅一批历史轨迹，发现跨会话的系统性模式（反复失败模式 / 低效模式 / 知识缺口），只产出建议、由人决定。
- **现状**：完全缺失。
- **但已有数据基础**：`ai_decision_log` 已有 `decision_type` / `gate_err` / `confidence` / `is_hallucination` / `adopted`，足够跑"某供应商连续低置信""某 doc_form 反复 math retry"这类聚合。
- **建议**：先做一个**只读的周报脚本**（不自动改任何东西），输出 Top-5 系统性模式 + 建议，人工审阅后进入流水线。这正好落在 Dreaming「只建议不改」的定位上。

### Gap C3　无自动回滚（P0）

- **要求**：灰度出现 P0 退化 → **一键自动回滚，不需要等人判断**。自动回滚是灰度机制的灵魂。
- **现状**：`rollback` 是**人工调用的管理 API**；无指标阈值监控、无自动触发。`experiment` 表有 `start_ts / end_ts / min_sample / conclusion` 字段，但**没有执行器**去按观测期判定并自动处置。
- **证据**：`demo/app/api_admin.py:314-345`（人工 PUT）；全仓无 `auto_rollback`。
- **风险**：半夜出问题没人兜底 —— 文档明确指出这是灰度失效的主要形态。
- **建议**：补一个守护任务：按 `min_sample` 与 guardrail 阈值周期检查运行中的实验，触发 P0（成功率下降 / 成本暴涨 / 延迟恶化）即自动 `rollback` 并冻结实验。

### Gap C4　无「生成候选 → 独立评测」环节（P1，但可接受）

- **要求**：LLM 根据汇聚信号生成 N 个候选修复（Diff 模式），每个候选在**隔离环境**独立评测。
- **现状**：无。prompt 迭代是人手写文件 + 人工跑黄金集。
- **判断**：**这一条在 Interview/Demo 阶段是可以合理化接受的** —— 人工写 prompt 的质量可控性远高于 LLM 自动生成，且项目规模（57 样本、8 次迭代）还不需要自动化生成候选。
- **但要注意口径**：对外叙事时不能说"我们实现了自进化闭环"，准确说法是"**我们搭好了闭环的骨架与门控，改进仍由人驱动**"。这是设计取舍，不是缺陷 —— 但要能讲清楚为什么（LLM 生成修复的质量不可控 + 缺乏全局视角，见原文 3.1）。

### Gap C5　无观测期制度化（P2）

- **要求**：10% 流量 × 7 天观测期，监控成功率 / Token 消耗 / 延迟 / 用户反馈四项。
- **现状**：`experiment` 表有时间字段与 `guardrail_metrics`，但**没有"观测期 + 四项核心指标阈值 + 到期自动判定"的执行逻辑**。`/api/admin/experiments/{id}/pvalue` 只能人工去看。
- **建议**：与 C3 合并实现（同一个守护任务）。

### Gap C6　诊断归因未自动化（P2）

- **要求**：评测完必须按失败类型分组、分析失败模式、定位根因，再分流到不同处理链路（系统性→改 Prompt；偶发→写记忆；能力缺失→补工具；回归→回滚）。
- **现状**：失败样本是人写的对抗集（`test_gap1..8`、`test_hallucination_adversarial`），归因靠人。
- **已有基础**：`ai_decision_log` 有 `gate_err` 摘要与 `decision_type`，可做自动分组。
- **原文血泪教训值得记**：「某场景连续 3 轮失败率 80%+ 不下降 → 先查工具实现层，别在配置层打转」。项目历史上有过同类教训（CodeBuddy CLI 长 prompt 超时 22 次调用 0 成功，根因在工具层不在配置层）。

---

## 五、飞轮第四齿：控制（人机协作）

### 已有

| 要求 | 现状 | 证据 |
| :--- | :--- | :--- |
| 人的节点（部分） | 低置信进人工队列；审核工作台人工校验/标注；approve 才回写记忆 | `demo/app/services/rag.py:98`；审核工作台 |
| 安全边界（部分） | `prompt_injection_guard` 工具 + 角色鉴权 + 只读 SQL 生成 | `ai_registry/tools/prompt_injection_guard/`；`query_sql_gen/` |
| 不可逆红线意识 | 付费/权限类操作需 admin | `require_admin` 系列装饰器 |

### Gap D1　规则级记忆写入无人工闸（P0，红线）

- **要求**：规则级记忆（"遇到这类问题永远用方案 A"）**写入前必须人工确认**。它会被无条件遵循、影响面是全局的，且 Agent 遵循规则时不会再质疑规则本身。
- **现状**：`POST /api/receipt/{id}/feedback` 第 3 次连续点踩 → **自动**调 `ingest_feedback_memory` 写入 Chroma + `vendor_memory` 表，该记忆后续对该供应商**无条件注入**。全过程无人工确认。
- **证据**：`tests/test_feedback_flywheel.py:160-202`（API 第 3 次点踩返回 `distilled=True`）；`demo/app/services/rag.py:249-275`。
- **风险**：这正好是原文列的五条红线第 ① 条。而且触发条件本身很脆弱 —— 3 次点踩可能来自同一个有偏见的标注者，也可能源于 GT 本身错误（见 A2）。**错误记忆 + 全局生效 + 无撤销机制（B1）= 记忆污染的完整路径**。
- **建议**：把"蒸馏后直接写入"改为"蒸馏后进入待确认队列"，管理台一条确认才生效；同时写入即带 `source_receipt_ids`（可回溯是哪三次点踩触发的）。

### Gap D2　无分级自主与动态升降级（P1）

- **要求**：按场景分级自主（L1 逐条审批 / L2 事后抽检 / L3 高度自主），稳定场景可升级，出现 P0 自动降级；安全边界/拒答/付费永远不能 L3。
- **现状**：完全无此概念。所有变更一律人工，没有"某类场景已稳定 → 放权"的机制。
- **判断**：Demo 阶段合理（保守优于激进）。但**设计文档里应该写明这个分层模型**，否则会被认为是没想到。
- **建议**：在 `ai_registry/README.md` 补一节「自主分级矩阵」：单据类型 × 变更类型 → 允许的自主级别，并声明不可逆红线。

### Gap D3　无批量异步审核（P2）

- **要求**：攒一批一次性呈现（附评测数据、影响面、2-3 个对比示例、AI 推荐理由），让人做批量选择题而非逐条填空题。
- **现状**：审核是逐单实时工作台。
- **风险**：对应原文「审核疲劳是落地中最常见的失败模式」—— 目前单次审核成本低所以还没暴露，规模化后会成为瓶颈。

### Gap D4　无对齐漂移防护（P1）

- **要求**：三层防护 —— ① 定期方向性审计 ② 方向性约束指标（输出长度、工具调用次数、拒答率不能持续单向漂移）③ 评测集显式包含"意图对齐"维度。
- **现状**：`ai_metric_snapshot` 有 `trust_score`，但**只评结果不评方向**；无方向性约束指标，无对齐审计。
- **证据**：`demo/app/db.py:222-239`（snapshot 字段均为结果指标）。
- **建议**：至少补 ②：在 snapshot 加 `avg_output_tokens` / `avg_tool_calls` / `avg_retry_rounds` 三个方向性指标 + 阈值告警。成本极低，且是"我知道有这个问题"的强信号。

---

## 六、平台架构（五层）对照

### 已有（分层设计是到位的）

五层在概念上完整落地：入口（Web 管理台 / API / 上传）→ 应用服务（单据中心 / 审核工作台 / 管控后台 / 报表监控）→ Document PaaS（任务队列 + 抽取引擎 + 质量评估）→ 模型服务（VLM 多引擎插件 + 审核腿 + 灰测路由）→ 数据层（SQLite + Chroma + 本地存储）。模型路由、成本控制、灰度回滚、PII 脱敏、审计日志均已实现。

### Gap E1　字段级证据完全缺失（P0，架构文档风险 #1）

- **要求**：每个字段记录**来源页码、坐标、原始文本、模型版本、置信度、修正记录**；否则无法审计，也无法提升人工审核效率。
- **现状**：`ReceiptItem` / `ReceiptData` **无任何 evidence 字段**（无 page / bbox / 原文片段 / 模型版本）；`ReceiptItem` 只有 `raw_name` 保留了原始品名。落库层面只有整单的 `raw_llm`（VLM 原始输出）与整单 `confidence`。
- **证据**：`demo/app/models.py:28-59`（契约模型无 evidence 字段）；全仓 grep `evidence|bbox|坐标|polygon` 在 `demo/app/` 下无命中。
- **风险**：三重 —— ① 人工审核无法定位到原图位置，审核提效做不出来；② 无法审计"这个值是怎么来的"；③ 架构文档明确把它列为风险 #1。
- **建议**：在 `ReceiptItem` 增加可选 `evidence: {page, bbox:[x1,y1,x2,y2], raw_text, model}`；VLM prompt 要求输出证据框（对 qwen3-vl-flash 这类模型是可行的）。考虑到 VLM 直识方案的版面理解能力，这是**能拉开与纯 OCR 方案差距的关键差异点**，优先级建议最高。

### Gap E2　多租户不彻底（P0，架构文档风险 #4）

- **要求**：文件、任务、配置、模型权限、日志查询都要带 `tenant_id`；对象存储路径与加密 Key 也要隔离。
- **现状**：`tenant_id` **只存在于 `receipt_feedback` 一张表**。`receipts` / `receipt_items` / `suppliers` / `skus` / `dishes` / `inventory_log` 全部无租户键。`db.py` 文件头自述「精简版，无迁移/多租户」。
- **证据**：`demo/app/db.py:294`（仅 feedback 表有 tenant_id）；`demo/app/db.py:2-3`（自述无多租户）。
- **风险**：RAG 层做了硬隔离，但业务数据层没有 —— 隔离是**半截的**。对外讲"多租户"时这是硬伤。
- **建议**：至少给 `receipts` / `receipt_items` / `suppliers` 加 `tenant_id` 并贯穿查询路径。RAG 层已经做对的那套（相关性门 + collection 隔离）作为样板复用。

### Gap E3　模板中心 / 规则引擎未配置化（P1）

- **要求**：字段 schema、校验规则、阈值、审核策略应可配置，避免每加一种单据都改代码。
- **现状**：字段 schema 硬编码为 Pydantic 契约（`models.py:28-59`）；阈值硬编码为常量（`PRICE_ANOMALY_THRESHOLD_PCT`、`FEEDBACK_DISTILL_THRESHOLD`、模糊拦截 Laplacian 阈值）；管理台**无**模板/规则/阈值的配置入口。
- **证据**：`demo/app/models.py:109,116`；`demo/app/api_receipts.py:111`；`api_admin.py` 中无 template/rule/threshold 相关端点。
- **风险**：架构文档明确警告「过早承诺 100% 自动化」的解法是配置化 —— 当前新增一种单据形态必须改代码。
- **建议**：把阈值与 doc_form 相关规则抽到 `app_settings` 表 + 管理台配置页。schema 本身可暂不配置化（改动成本大，收益在支持第 4+ 种单据时才显现）。

### Gap E4　预处理 Pipeline 缺失（P1）

- **要求**：去噪、旋转纠正、切页、表格检测；PDF 渲染、质量评分。
- **现状**：只有**上传时的 Laplacian 方差模糊前置拦截**；无去噪/纠偏/切页/表格检测。`image_quality_guard` 工具存在于 `ai_registry/tools/` 但**未纳入 pipeline 环节**，只在 API 层调用。
- **证据**：`demo/app/api_receipts.py:111`（唯一一处）；`ai_registry/tools/image_quality_guard/v1_0_0.py` 无调用方。
- **建议**：把已有工具正式接进链路，并补纠偏（香港街市手写单拍摄角度普遍倾斜，纠偏对准确率的正向影响可能大于换模型）。

### Gap E5　结果四版本不完整（P2）

- **要求**：原始识别 / 规则修正 / 人工修正 / 最终提交都要留版本，不要只存最终值。
- **现状**：有 `raw_llm`（原始）、`items_json`（修正后）、`ai_prefill_json`、`audit_json`，但**人工修正前后没有分离成两个版本**，`audit_logs_json` 是操作日志不是版本链。
- **证据**：`demo/app/db.py:70-80`。
- **影响**：无法回答"AI 给的原始值是什么、人改成了什么、改了多少" —— 而这正是 `edit_rate` 与字段级归因的数据源（目前靠 `ai_decision_log` 旁路记录，主表反而没有版本）。

### Gap E6　无抽检策略（P2）

- **要求**：质量评估需含置信度、异常检测、**抽检策略**（高置信也要抽，否则自动通过率虚高）。
- **现状**：无抽检机制，全量人工 or 全量自动，二选一。
- **证据**：全仓 grep `抽检|sampling` 无结果（仅有 `api_admin.py:923` 的"灰测脱敏单据抽样观测"接口，语义不同）。

### Gap E7　Infra 层为单机 Demo 形态（合理取舍，但需明说）

- **现状 vs 要求**：SQLite（非 PostgreSQL + Redis）、`uploads/` 本地目录（非对象存储）、无 MQ/K8s（进程内 job 队列 + engine 常驻进程）、无 Prometheus/Grafana/OTel（应用内 `ai_metric_snapshot` + admin API 替代）、无 KMS/WAF/VPC。
- **判断**：**这是 Demo 的合理取舍，不是设计缺陷**。但要能说清演进路径：SQLite → PostgreSQL（租户与并发）、本地目录 → S3（原图与切片）、进程队列 → MQ（按任务类型拆队列避免互相阻塞）、应用内指标 → OTel + Prometheus。
- **注意**：`ai_metric_snapshot` 这个设计其实很好（预聚合、避免仪表盘全表扫描、带 sample_size），上 Prometheus 时可作为指标语义的蓝本。

### Gap E8　北极星指标缺两项（P2）

- **架构文档 5 项**：字段级准确率 / 自动通过率 / 平均处理时长 / 人工修改率 / 单据处理成本。
- **现状**：有平均处理时长（`avg_elapsed`）、人工修改率（`edit_rate`）、单据处理成本（`avg_cost`）、整单准确率（`accuracy`）；**缺"字段级准确率"与"自动通过率"**。
- **证据**：`demo/app/api_admin.py:700-720`（只聚合 4 项）；`db.py:1548-1600`（snapshot 无字段级维度）。
- **说明**：`ai_decision_log.field_path` 已为字段级指标留好口子（用户修正事件记录了 `items[i].unit_price`），缺的是聚合 —— 补一个按 `field_path` 分组的准确率聚合即可，成本很低。

### Gap E9　模型编排：单一 VLM 直识，无轻/重分流（已知取舍）

- **要求**：OCR + Layout + VLM + LLM + 规则协同；简单票据走轻模型，复杂才触发 VLM。
- **现状**：L4 多模态 VLM 直识为定案冠军（163/163 成功、P50 9.0s、约 ¥0.0022/张）；成本路由只体现在"是否跑审核腿"（`audit_mode: text|vlm|ondemand`）与"是否跑 parse LLM"，**没有按单据复杂度分流的实际执行**。L3 商业云 OCR 仍是"最重要缺项"未测。
- **判断**：这是**有实测依据的决策**（L5 模板路由 78.5%、L7 看想分离 26.4% 已被否决），不是疏漏。但架构文档的建议（轻/重分流）指向的是规模化后的单位成本，值得作为下一步。

---

## 七、优先级与最小补齐路线

### P0（建议先做，直接决定飞轮能不能"可信地转"）

| # | Gap | 动作 | 依赖 |
| :-- | :--- | :--- | :--- |
| 1 | A2 GT 污染 | 人工覆核 test 集 15 张，对外数字只引用 test | 无 |
| 2 | A1 无三分法 | `manifest.csv` 加 `split` 列，分层切 30/12/15 | 无 |
| 3 | D1 规则级记忆无闸 | 蒸馏结果改入待确认队列，管理台确认后生效 | B1 的 `source_receipt_ids` |
| 4 | B1 记忆无治理元数据 | `vendor_memory` 加 version/source/hit_count/decay_score/status | 无 |
| 5 | E1 无字段级证据 | `ReceiptItem` 加可选 `evidence{page,bbox,raw_text,model}`，prompt 要求输出证据框 | VLM 能力确认 |
| 6 | C3 无自动回滚 | 守护任务按 guardrail 阈值自动 rollback 并冻结实验 | 无 |
| 7 | E2 多租户半截 | `receipts`/`receipt_items`/`suppliers` 加 `tenant_id` 并贯穿查询 | 无 |

### P1（骨架补完）

- A3 评估器与生成器强制异构（默认配置改为跨厂商）
- A4 元评测集（从已有 24 个对抗测试抽 20-30 条固化）
- A5 评测集刷新通路（线上低置信样本自动入候选池）
- B2 三层晋升 + 非对称淘汰（+0.05 / -0.12）
- B4 读取预算（Facts ≤800 token / 单条 ≤200 / 最多 6 条）
- C1 Playbook（先补录 v1_2_0→v1_2_8 八次迭代）
- D2 自主分级矩阵（写入设计文档）
- D4 方向性约束指标（输出长度 / 工具调用次数 / 重试轮数）
- E3 阈值与规则配置化
- E4 预处理 Pipeline（纠偏优先）

### P2（规模化时才显现价值）

- A6 Skill 消融实验（有无 Skill 对照）
- B3 记忆分层（L0-L3）
- B5 反例结构化
- C2 Dreaming 周报（先只读）
- C5 观测期制度化
- C6 诊断归因自动化
- D3 批量异步审核
- E5 结果四版本
- E6 抽检策略
- E7 Infra 演进（Postgres / S3 / MQ / OTel）
- E8 字段级准确率 + 自动通过率聚合

---

## 八、叙事口径提醒

对照这两篇材料时，有几条**口径要在对外讲述时保持一致**（避免被追问时自相矛盾）：

1. **不要说"我们实现了自进化闭环"**。准确说法：「我们搭好了四齿飞轮的**骨架与门控**——评测信号进了决策日志、反馈进了记忆、变更有版本与灰度回滚；但改进的生成与最终确认仍在人手上，这是当前阶段的**主动设计**而非能力缺失」。理由引用原文 3.1（LLM 生成修复的质量不可控 + 单点修复缺乏全局视角）。

2. **准确率数字必须带 GT 状态说明**。当前 98.2% 是对着 AI 草稿 GT 刷出来的；人工覆核后的数字大概率更低，但**可信度更高**。主动说明这一点，比被追问出来要好得多 —— 而且这恰好呼应「评测的可信度 > 系统的复杂度」这条第一原则，是有加分的表述。

3. **多租户要分两层讲**：RAG 记忆层是物理硬隔离（collection 级 + 相关性门，已实现且有测试）；业务数据层是待办（P0-7）。不要笼统说"支持多租户"。

4. **Infra 的精简是 Demo 取舍**，要主动给出演进路径（见 E7），而不是被问到才解释。

5. **八次 Prompt 迭代（v1_2_0 → v1_2_8）是最好的"飞轮转过一圈"的证据**，但目前只是文件名。补成 Playbook（P1-C1）后，这就是"我知道怎么让飞轮转，也转过"的实证材料。

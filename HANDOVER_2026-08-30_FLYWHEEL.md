# 接手文档: 自进化飞轮 Gap 补全 — Wave A/B/C/D/E 全历程与剩余任务

> 本文档自洽:不依赖任何对话历史,读完即可接管 e2e 测试与剩余任务。
> 接手 prompt 在文末第 10 节。前序交接 HANDOVER_FLYWHEEL_PROMPT.md 已被用户删除,本文档是其完整继任者。
> 撰写时间 2026-08-30,撰写者为主会话 agent(因 token 耗尽交接,验收欠账已如实标注)。

---

## 0. 一句话现状

「自进化飞轮 Gap 补全计划」(docs/superpowers/plans/2026-08-28-flywheel-gap-closure.md,唯一权威)
的 T1-T7、T12 与收口包已全部实现并通过验收;**剩余 = 用户人工 GT 抽检(0/33)+ 三件等它解锁的评测动作
+ 主会话未完成的 T8/T9/T10/T11 验收欠账(第 7 节)**。

---

## 1. 叙事红线(对外表述,违反即翻车)

- 禁止说「实现了自进化闭环」;准确口径:「飞轮骨架与门控已打通,评测信号能自动流进记忆与改进链路;
  改进方案的生成与最终确认由人工把关,这是当前阶段的主动设计」
- 历史分数 98.2% 为 AI 草稿 GT 口径,引用必须带警示;人工确认分数尚未产出(见 5.1)
- 历史口径陷阱:旧文档写的「587 passed」复现不出来,禁止引用;当前实测数字见第 4 节
- 禁 emoji(所有输出/代码/文档)

## 2. 环境事实卡(实测,直接信任)

- **Python 唯一可用解释器**: `$HOME/.pyenv/versions/3.9.6/bin/python`(本机无其他可用 pytest 环境)
- 测试(仓库根): `pytest tests/ -q` 与 `pytest demo/tests/ -q`
- **当前基线: 383 passed(tests/ 183 + demo/tests/ 200)+ 1 skipped(demo 评测用例需 --run-eval)**;零 failed
- 服务: `cd demo && python -m app.main`,端口 15010,--reload 模式(改 demo/ 下文件会自动重载);
  启动日志 grep "engine-env" 可见双腿装配结果
- demo/.env: OPENAI_API_KEY(实为 SiliconFlow key)/ OPENAI_BASE_URL(注意含 /chat/completions 后缀,代码有容错去重)/ DASHSCOPE_API_KEY / OPENCODE_*(已非默认)
- **15010 可能正被用户用于 GT 抽检——接管 agent 禁止随意启停**;验证用 TestClient 或 15011+ 临时实例(DB_PATH/EVALSET_DIR 指 /tmp)
- 本机 demo/receipt_demo.db 含真实业务数据: 只读对待(PRAGMA/SELECT 允许,禁写);迁移实验一律 DB_PATH 指 /tmp

## 3. 提交历史(8e90066 → HEAD,每 commit 干什么)

| commit | 内容 |
| :-- | :-- |
| 8557a97 | docs: Gap 分析/实施计划/面试叙事/交接 Prompt |
| 2f0bdf2 | T1 评测集三分法 + eval harness(build_evalset/run_eval/conftest×2/--run-eval skip) |
| 9ddfcc2 | 前端打磨 + 点赞/点踩反馈埋点(用户指示保留 emoji 按钮埋点)+ 全链路埋点 Spec |
| b4d0b4e | T2 租户隔离(7 表 tenant_id/scoped/全链路透传)+ T3 记忆治理(10 治理列/MemoryBudget 800/200/6/notes[-4000:] 移除) |
| 1a46e85 | 治理规范: agent_memory.md「ai_registry 唯一来源」+ README 3.5 节;Wave B 新增 T12 |
| 16f0097 | T12 registry SSOT 收敛: 五场景 prompt 改走 registry(golden 逐字节等价,tests/golden/)/镜像退役/metadata 补登 v1_2_1~v1_2_8 且 active=v1_2_8/math_engine v2_1_0 回灌/mcp 如实化/pii_masker 修复 |
| 3f90372 | T4 GT 生成 + 抽检台: gen_gt_candidates(provider 可插拔)/api_evalset 四端点/--require-confirmed 闸门 |
| edad926 | T5 元评测集 25 条 + run_meta_eval + 审核腿 temperature=0 强制 + README 准入规则 +3 |
| 92fec08 | T6 低置信回流: eval_candidate 表/四信号钩子(supervisor._finalize 单点)/promote-reject API |
| 697e379 | 抽检台修复: 缩略图(14MB→0.2MB)/quantity→qty 归一/翻页按钮 ReferenceError 修复 |
| b00a568 | GT schema v2 对齐 ReceiptData 契约(payment_marked/payment_evidence/currency/6 费用/adjustment_notes)+ 工作台复用店员真实复核界面(/evalset/workbench/<sid> 注入标志+eval_workbench.js 适配器) |
| 5f5ff93 | 产品缺口: 费用抽屉补服务费/税额 + 手写注记编辑器 + 付款证据 + save_edited 落库(修复存量 bug: 4 项费用从未落库/门禁不重算) |
| 03de0ca | 费用区改动态添加行(原因+金额,六宫格移除) |
| d4a25ce | 手写注记同款动态行 |
| 9270454 | 阶段检查收口: **P1 修复(.gitignore 裸 review/ 吞掉 ai_registry/prompts/review/,补交)+ 文档对账 19 条 + 杂散产物清理** |
| 80df042 | 付款标记改点击切换徽章(绿=已付/红=未付),证据自动带出禁手输 |
| d22429b | T7 字段级证据: Evidence 契约/v1_3_0_evidence prompt 登记(draft)/evidence_json 迁移/前端高亮/evidence_coverage;附带 auth 加固(匿名不再默认 owner,401/403 分离) |
| 64325bd | SiliconFlow 默认识别 provider(重启强制装配) |
| fa572ab + bacab8c + 1170333 | **审核腿默认迁 SF;opencode 移出默认路径;双腿强制装配**(注意: fa572ab/1170333 被 git add -A 带入了 T8/T9/T10/T11 全量推进的文件,commit message 未提——历史文档账,第 7 节验收即补) |
| 554614a | 队列页收敛纯导航(删内联表单),工作台唯一编辑表面 |

## 4. 当前系统状态

- **引擎双腿(每次重启强制装配,管理台临时切换不跨重启)**: 识别 = SF `Qwen/Qwen3-VL-32B-Thinking`;审核 = SF `zai-org/GLM-4.5V`(视觉模型,支持 text/vlm/ondemand 审核)。降级链: SF 不健康 → DashScope(识别 qwen3-vl-flash/审核 qwen3-vl-plus,同家族弱化有日志)。opencode/CLI 已移出默认路径(依据: 两次拖死服务 + 元评测 8/25 超时)
- **评测集**: demo/evalsets/(gitignored,真实商户数据)——manifest 163 样本(train 90/val 40/test 33),test 集 33 份 GT 候选(百炼 qwen3-vl-plus 生成,gt_status=draft);**用户人工抽检进行中,约 0-3/33 confirmed**(以 /api/evalset/stats 实时为准)
- **T8 记忆人工闸/T9 守护/T10 settings+预处理/T11 Playbook**: 代码已全部入库(见 fa572ab/1170333 提交),T9 守护线程已随重启生效;但**主会话验收未完成**(第 7 节)
- **L4 元评测**: 三轮报告在 ai_registry/benchmarks/meta_eval_runs/——前两轮(opencode 审核腿)trustworthy=False(11/25 miss,8 次 CLI 30s 超时;实际响应 14 项全对);第三轮(GLM-4.5V 审核腿)**正在后台运行**,接手后先查 meta_eval_runs/ 最新文件读结论

## 5. 剩余任务(按执行顺序)

### 5.1 用户动作(无法代劳)
1. **test 集 GT 抽检**: 队列页 /evalset → 每行「在工作台核对」→ 店员同款界面逐字段校正(含付款徽章/费用动态行/手写注记)→ 确认自动跳下一张;重点 S049(总额 null,需人工填或显式确认留空——confirm 带 confirm_blank_total=true)
2. **灰测决策**: 抽检完成后,真实灰测 v1_2_8 vs v1_3_0_evidence 同批对比;达标线 evidence_coverage>=80%/成功率降幅<2pp/P50 增幅<2s → 决定 v1_3_0 晋升 production 还是回退(保留 schema 与前端)

### 5.2 接手 agent 动作(按序)
1. **先读 L4 第三轮报告**(meta_eval_runs/ 最新 _audit.json): trustworthy 转绿与否;若 False,miss 定性(超时/解析/真实误判)
2. **补主会话验收欠账**(第 7 节,含具体命令)
3. **出分**: `$HOME/.pyenv/versions/3.9.6/bin/python demo/scripts/run_eval.py --split test --require-confirmed`(test split 默认强制,--no-require-confirmed 可关但标 partial)——第一份人认证分数,与 prompt_eval_history 的 98.2%(AI 草稿口径)对照,差异写入 Playbook(即 T1 Step 6 补做)
4. **收口**: 更新计划文件 checkbox(T7 灰测后)、playbook 2026-08-gap-closure.md 补最终分数与对照
5. 若用户要求继续: T8/T9/T10 验收通过后按需迭代;Wave D/E 无剩余未实现任务

## 6. 接管 e2e 必测清单(最小集,全部实测过的方法)

```bash
# 1. 全量回归(基线 383+1s,零 failed)
$HOME/.pyenv/versions/3.9.6/bin/python -m pytest tests/ -q
$HOME/.pyenv/versions/3.9.6/bin/python -m pytest demo/tests/ -q
# 2. SSOT(五场景 prompt 走 registry,镜像零残留)
$HOME/.pyenv/versions/3.9.6/bin/python -m pytest tests/test_registry_ssot.py -q   # 21 passed
grep -rn "from ai_registry.prompts" demo/app/ --include="*.py"   # 零命中
# 3. 租户隔离 + 评测台鉴权/穿越
$HOME/.pyenv/versions/3.9.6/bin/python -m pytest tests/test_tenant_isolation.py demo/tests/test_gt_review_api.py -q
# 4. 迁移幂等: DB_PATH=/tmp/x.db 两次 import app.db,第二次零 WARNING
# 5. 服务健康: curl 127.0.0.1:15010/ 与 /evalset 与 /api/evalset/stats(X-Role: admin)
# 6. T8 端到端(见 7.1)/T10 settings 生效/T9 guardian 测试逐条(见 7)
```

## 7. 主会话验收欠账(接手 agent 必须补做并出报告)

### 7.1 T8 记忆人工闸端到端(临时环境)
- 3 连踩(POST /api/receipt/{id}/feedback 连续 dislike×3)→ GET /api/memory/pending 出现记录
- 此刻 retrieve_context(该 vendor)注入内容**不含**该记忆;approve 后注入出现;reject 后不出现
- 衰减数值: 命中 +0.05/覆写 -0.12/<=0.5 archived(rag.py/db.py 常量,现应走 settings)
- source_receipt_ids 回溯到触发点踩的 receipt_id
- test_feedback_flywheel.py 语义修改对照(git log -p 该文件,判定合法适配)
### 7.2 T10 settings 生效
- 改 settings 键(如 memory_budget_facts_tokens)→ 行为即时变化 → 改回;deskew ±1° 实测;纠偏默认 OFF
### 7.3 T9 guardian
- pytest demo/tests/test_experiment_guardian.py -q 逐条;构造成功率下降假数据 → rollback+冻结+guardrail_event;min_sample 不足不触发;漂移只 alert;POST /api/admin/guardian/check admin 鉴权
### 7.4 T11 Playbook 反编造抽查
- 抽 5 个事实断言去 prompt 文件/metadata/changelog/git show 核对;出现无出处 accuracy 数字即 REJECT;叙事红线 grep;「待补」标记是否诚实
### 7.5 前端保护回归
- 工作台冒烟: /evalset/workbench/<sid> 复核表单/付款徽章/费用动态行/注记动态行/确认提交回队列——用户抽检入口不可破坏

## 8. 已知坑(全部踩过)

1. subagent 派发偶发 "Model request failed"/看门狗 10 分钟无活动即杀(长 bash 用 run_in_background + 轮询);**失败的派发可能实际已完成工作**——先查工作树再重试
2. .gitignore 裸模式(如 review/)会吞深层同名目录——新增顶层目录一律锚定 /
3. load_dotenv 会在 hydrate 内把 demo/.env 装回 os.environ——测试双密钥不可用分支需打桩 dotenv.load_dotenv
4. hydrate 模型解析**不读 DB 旧值**(env → 内置默认),否则管理台旧配置压过新默认(踩过)
5. SQLite ALTER 不能改主键/唯一约束——vendor_memory/skus 走数据保全式重建(用户已解锁,仅此两表);旧库物理主键残留已被重建逻辑消化
6. GLM-4.5V 输出带 <|begin_of_box|> 标记——_parse_audit 花码括号提取已兼容,新解析器注意
7. uvicorn --reload: 改 demo/ 文件自动重载(用户会话内改前端文件 = 热影响用户);opencode CLI 挂起会耗尽线程池拖死整个服务(已移出默认路径,但 opencode 仍装在机器上)
8. demo/tests 共享 DB_PATH——新测试按 receipt_id 过滤断言,防跨测试污染
9. meta_eval audit_judge 只收 item["input"](防偷看,签名级断言);禁止为绿灯改样本/换模型/删历史报告

## 9. 统一遗留台账(全部已核实状态)

| 项 | 优先级 | 状态 |
| :-- | :-- | :-- |
| test 集 GT confirmed 0/33 | 高 | 用户进行中,唯一堵点 |
| L4 元评测第三轮(GLM-4.5V) | 高 | 后台运行中,接手先读结果 |
| T7 灰测 + v1_3_0 晋升决策 | 高 | 等 GT confirmed |
| T8/T9/T10/T11 验收欠账 | 高 | 第 7 节,接手必做 |
| T1 Step 6 98.2% 对照 | 中 | 等 GT confirmed(计划 checkbox 已诚实回退) |
| 候选池 sha1 去重/上限已做;audit_discrepancy 严重度门槛已做 | - | 已闭环 |
| meta_eval 仅算术确定性维度 | 中 | 设计取舍已文档化,扩充待评测数据积累 |
| 根目录 receipt* 产物 | 低 | 已清理 + gitignore |
| 观测/实验表(ai_decision_log 等)租户化 | 低 | 明确排期 Wave C/D 之后,非缺陷 |

## 10. 接手 prompt(复制给新 agent)

见用户持有的交接消息;核心 = 「读本文档 → 按 5.2 顺序执行 → 按 6/7 做 e2e → 汇报格式逐条贴真实命令与输出」。

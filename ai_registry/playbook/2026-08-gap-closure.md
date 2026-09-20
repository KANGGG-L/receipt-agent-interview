# Playbook：2026-08 自进化飞轮 Gap 补全记录（17 项收口）

> **计划出处**：`docs/superpowers/plans/2026-08-flywheel-gap-closure.md`
> **Gap 编号出处**：`docs/06-adds-on/04-自进化飞轮与平台架构Gap分析.md`
> **叙事红线**：本轮做完成立的说法是——「闭环骨架与门控已全部打通，评测信号
> 能自动流进记忆与改进链路；但改进方案的生成与最终确认仍在人手上，这是当前
> 阶段的主动设计」。**不得表述为「实现了自进化闭环」。**

---

## 一、总账：17 项状态与证据

### Wave A：数据与 Schema 地基

| Task | Gap | 状态 | 证据 / 数字出处 |
| :--- | :--- | :--- | :--- |
| T1 评测集三分法 + harness | A1+A5a | 完成（条件性） | `demo/scripts/build_evalset.py`、`run_eval.py`、`demo/tests/test_eval_harness.py`；**v1_2_0 复现对照未执行**（`eval_runs/` 无报告），待人工确认 GT 后出分 |
| T2 tenant_id 贯穿 | E2 | 完成 | 7 张主表 + `vendor_memory` 幂等 ALTER；`tests/test_tenant_isolation.py` |
| T3 记忆治理元数据 + 读取预算 | B1+B4 | 完成 | `vendor_memory` 10 列；预算 800/200/6；`tests/test_memory_governance.py` |

### Wave B：评测可信度

| Task | Gap | 状态 | 证据 / 数字出处 |
| :--- | :--- | :--- | :--- |
| T12 registry SSOT 收敛 | 治理 | 完成 | `tests/test_registry_ssot.py`；chains 全部经 `registry.get_prompt` 取词 |
| T4 GT 异构生成 + 抽检台 | A2 | 完成（人工确认进行中） | `gen_gt_candidates.py`、`/evalset` 队列页 + `/evalset/workbench` 店员同款工作台；GT 仍为 draft/confirmed 混合，**对外数字只允许引用 confirmed** |
| T5 评估器异构 + 元评测 | A3+A4 | 完成（含一条已知遗留，已于 2026-09-02 收口） | 元评测集 25 条（`benchmarks/meta_eval_set.json`）；mock judge 判对/判错率 100%；**遗留（当时口径）：纯默认（无 env）时识别/审核模型同为 opencode 系**，当时生产运行配置已异构（识别=SiliconFlow Qwen 系，审核=opencode 系）；该遗留已随 2026-09-02 opencode 弃用收口（现行识别=DashScope Qwen 系，审核=SiliconFlow GLM-4.5V），见计划文档 T5 口径注记 |
| T6 低置信样本回流 | A5 | 完成 | `eval_candidate` 表 + 四类钩子 + 候选池卫生（上限/去重）；`demo/tests/test_eval_reflow.py`、`test_eval_candidate_hygiene.py` |

### Wave C：可信产物与写入闸门

| Task | Gap | 状态 | 证据 / 数字出处 |
| :--- | :--- | :--- | :--- |
| T7 字段级证据 | E1 | 落地待灰测 | `v1_3_0_evidence` 为 draft（未置 production）；解析/落库/前端高亮已就绪；**晋升决策待灰测数据**（覆盖率 ≥80%、成功率降幅 <2pp、P50 增幅 <2s） |
| T8 记忆写入人工闸 + 非对称淘汰 | D1+B2 | 完成 | `pending_memory` 表 + `/api/memory/pending` 三端点；蒸馏先入队、approve 才注入；衰减 +0.05/-0.12（淘汰速度=强化速度 2.4 倍）、≤0.5 归档停注；`demo/tests/test_memory_lifecycle.py` 8 条、`tests/test_feedback_flywheel.py` 语义改判 |

### Wave D：自动化守护与平台化

| Task | Gap | 状态 | 证据 / 数字出处 |
| :--- | :--- | :--- | :--- |
| T9 实验守护：自动回滚 + 方向性约束 | C3+D4 | 完成 | `services/guardian.py`：成功率降 >5pp / 成本涨 >50% / P95 >20000ms 触发回滚并冻结实验；方向性指标连续两期同向漂移 >20% 只告警；回滚复用 `rollback_snapshot` 还原；`demo/tests/test_experiment_guardian.py` 6 条；后台线程随服务重启生效 |
| T10 阈值配置化 + 预处理纠偏 | E3+E4 | 完成（2026-08-31 增补正交） | `services/settings_service.py`（28 键，新增 `preprocess_orthogonal_enabled=true` 默认必纠）+ 管理台「系统配置」区；`services/preprocess.py`（assess/deskew/enhance + `orthogonal_correct` 四假设 0/90/180/270，`cv2.rotate` 换边，正交不受 `OFF` 限制）+ 前端 `static/js/main.js:3311` `bakeCurrentRotation` 烤入；全仓 TODO(T10) 常量收口；`tests/test_settings_and_preprocess.py` 11 条（另合成 90/180/270 验证通过） |

### Wave E：文档收口

| Task | Gap | 状态 | 证据 |
| :--- | :--- | :--- | :--- |
| T11 Playbook + 自主分级矩阵 | C1+D2 | 完成 | 本目录三份档案 + `ai_registry/README.md` 自主分级矩阵与 P2 演进路径章节 |

---

## 二、测试总量变化（出处：本机两套 pytest 全量实跑）

| 套件 | 收口前基线 | 收口后 | 增量出处 |
| :--- | :--- | :--- | :--- |
| `tests/` | 171 passed | 182 passed | +11 `test_settings_and_preprocess.py`；`test_feedback_flywheel.py`/`test_memory_governance.py` 语义随 T8 更新（数量不变） |
| `demo/tests/` | 186 passed + 1 skipped | 200 passed + 1 skipped | +8 `test_memory_lifecycle.py`、+6 `test_experiment_guardian.py` |

---

## 三、元评测（评估器可信度）档案

- **离线自检**（`--judge mock`）：判对率/判错率 100%，`evaluator_trustworthy=true`
  （出处：`benchmarks/meta_eval_runs/20260829_*_mock.json`）。
- **真实审核模型**（`--judge audit`）：历史两轮均 `trustworthy=false`
  （出处：`meta_eval_runs/20260830_023332` 与 `20260830_031209`）。
  后一轮 11/25 miss 的构成：opencode CLI 30s 超时 8 次、输出不可解析 2 次、
  真实误判 1 次——**量测基建噪声淹没了判定能力信号**。
- **L4 量测修复**：`run_meta_eval.py` 的 audit 单条超时 30s→120s（仅量测脚本，
  生产审核腿超时口径不变）+ 解析失败重试 1 次。
- **修复后重测结果**（出处：`meta_eval_runs/20260830_063840_meta_eval_audit.json`，
  judge=audit）：判对率 0.8462、判错率 0.9167、miss 3/25，
  `evaluator_trustworthy` **仍为 False**——但 miss 构成已从「超时 8 + 解析失败 2 +
  真实误判 1」收敛为 **3 条全部是真实误判**（超时 0、解析失败 0），量测基建噪声
  已消除，剩余差距是评估器的真实判定能力问题：
  1. ME-012（expected=wrong，judged=correct）：未识别「划线拒收行
     （is_void=true）不应计入应付总额」，算术自洽但语义漏扣；
  2. ME-015 / ME-020（expected=correct，judged=wrong）：审核腿以「未提供原图 /
     疑似测试数据」为由拒判——纯文本元评测模式下的能力缺口。
  结论：评估器尚不可作为自动准入闸（维持人工审核兜底）；历史两轮
  `trustworthy=false` 报告原样保留于 `meta_eval_runs/`，未删除未修改。

---

## 四、98.2% 口径警示（引用准确率时必读）

- 98.2% 出自 `prompt_eval_history.json`：v1_2_0_sku_clean、dataset_size=95、
  模型 qwen3-vl-flash——**对 AI 草稿 GT（gt_status=draft）的口径**。
- 人工逐张确认（`/evalset/workbench`）仍在进行；确认完成并经
  `run_eval --require-confirmed` 出分之前，**不得把 98.2% 当作对外结论**。
- v1_2_8 vs v1_3_0_evidence 的最终对比同样待 GT 确认后执行（T7 晋升前置）。

---

## 五、本轮「下次别再试」（反面经验沉淀）

1. **不要让量测基建噪声冒充模型能力结论**：元评测两轮 false 的教训是先把
   超时/解析噪声修掉（L4），再谈评估器可不可信。
2. **不要在没有人工闸的情况下让负反馈自动进记忆**：T8 之前「3 连踩自动生效」
   会把误报固化成先验；入队 + approve 是红线，不再回退。
3. **不要回填不存在的评测数字**：v1_2_1~v1_2_7 的 val 指标在
   `2026-08-v1_2_x-extract-iterations.md` 中如实标「待补」。
4. **不要把阈值散落硬编码**：T10 之后新阈值一律走 `app_settings`，
   管理台可改、即时生效、无需重启。

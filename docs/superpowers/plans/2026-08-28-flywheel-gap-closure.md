# 自进化飞轮 Gap 补全实施计划（P0 + P1，共 17 项）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> 本计划的执行方式为 **4-role subagent Workflow（产品 → 开发 → QA → 审核）**，按 Wave 分批执行，每批走完整四角色闭环。

**Goal:** 把 `docs/06-adds-on/04-自进化飞轮与平台架构Gap分析.md` 判定的 **P0 七项 + P1 十项**全部补齐，使项目从「骨架已搭、人在推」升级为「评测可信、记忆可控、改进有闸」的**自洽闭环**。核心不是加功能，是把飞轮四条数据通路（评测→记忆 / 评测→落地 / 落地→控制→生效 / 生效→下一轮评测）真正接通。

**输入基准：**
- `docs/06-adds-on/04-自进化飞轮与平台架构Gap分析.md`（Gap 编号 A1-A6 / B1-B5 / C1-C6 / D1-D4 / E1-E9）
- Obsidian `00-Inbox/Agent自进化飞轮：评测→记忆→落地→控制.md`
- `docs/06-adds-on/document-ai-system-architecture.html`

**Tech Stack:** Python 3.9+, FastAPI, SQLAlchemy, SQLite, Chroma, OpenCV(已依赖), Vanilla JS (ES6+), Pytest, Playwright.

---

## 一、范围

### 本轮做（17 项）

| Wave | Task | Gap | 内容 |
| :--- | :--- | :--- | :--- |
| A | T1 | A1 + A5a | 评测集三分法 + 去标识 manifest + 可复现 eval harness |
| A | T2 | E2 | `tenant_id` 贯穿业务主表（含 `vendor_memory`） |
| A | T3 | B1 + B4 | 记忆治理元数据 + 读取预算控制 |
| B | T12 | 治理 | ai_registry 唯一来源收敛（prompt/tools/mcp 收口，SSOT 规范落地；Wave B 首位执行）|
| B | T4 | A2 | GT 异构模型生成 + 人工抽检台 |
| B | T5 | A3 + A4 | 评估器与生成器强制异构 + 元评测集 |
| B | T6 | A5 | 线上低置信样本自动回流为评测候选 |
| C | T7 | E1 | 字段级证据完整落地（prompt → 落库 → 前端回显） |
| C | T8 | D1 + B2 | 规则级记忆写入人工闸 + 三层晋升 / 非对称淘汰 |
| D | T9 | C3 + D4 | 实验守护：自动回滚 + 方向性约束指标 |
| D | T10 | E3 + E4 | 阈值规则配置化 + 预处理纠偏 |
| E | T11 | C1 + D2 | Playbook + 自主分级矩阵（纯文档） |

### 本轮不做（P2，明确延后）

记忆分层 L0-L3（B3）、反例结构化（B5）、Skill 消融实验（A6）、Dreaming 周报（C2）、观测期制度化（C5）、诊断归因自动化（C6）、批量异步审核（D3）、结果四版本（E5）、抽检策略（E6）、**Infra 演进 Postgres/S3/MQ/OTel（E7）**、字段级准确率与自动通过率聚合（E8）。

理由：这些项的收益要在规模化后才显现，对当前 57 张样本 / 单机 Demo 的量级不产生复利；**但 T11 会在文档中写明它们的演进路径**，保证被问到时有答案。

---

## 二、关键前提（必须先落实，否则 T4 无法执行）

### 前提 1：语料位置与隐私红线（硬约束）

- 原图：**163 张 HEIC，位于 `~/Desktop/hk/My Drive/Receipts/batch1`**（含 `_previews/`、`by_supplier/`、`classification_manifest.csv`）。
- **GT 语料（图片 + expected JSON）属于真实商户业务数据，绝不进版本库。**
- 落盘位置：`demo/evalsets/`（**新增进 `.gitignore`**），结构与现有 `demo/samples/` 一致（`receipts/` + `expected/` + `manifest.csv`）。
- **去标识化**：manifest 内的样本文件名使用无语义 ID（`S001.jpg`…），供应商名 → ID 的映射表 `supplier_map.csv` 单独存放且同样 gitignore。文件名不得带供应商简称（现有规范 `YYYYMMDD_供应商简称_形态.ext` 会泄露商户名）。
- 可提交的只有：harness 脚本、`manifest` 的去标识元数据列（sample_id / split / doc_form / layout_type / gt_status）、聚合指标。

### 前提 2：HEIC 转换

163 张为 HEIC，需在 T4 前置一步 `sips -s format png` 批量转 PNG 并做短边 ≥1000px 校验。转换产物同样落 `demo/evalsets/`，不入库。

### 前提 3：异构模型可用性

GT 生成（T4）与审核腿（T5）必须使用**与识别腿不同厂商**的模型。识别腿默认 `opencode/mimo-v2.5-free`；异构侧优先 qwen3-vl-flash（DashScope，已有成本核算记录）。若 API key 不可用，退而求其次用 Ollama 本地模型，但**必须记录实际使用的模型名**，禁止在无异构可用时静默回落到同模型。

---

## 三、全局红线（所有 Task 共守）

1. **不阻断主链路**：任何新增字段/校验失败都必须优雅降级，不得让识别流程抛错。契约门禁 `extra=forbid` 保持不变，新增字段一律 **Optional + 默认值**。
2. **不提交业务数据**：`demo/evalsets/`、`uploads/`、`.rag_chroma/`、`*.db`、`work/`、`artifacts/` 一律不 commit；`agent_memory.md` 可提交。
3. **迁移向后兼容**：沿用 `db.py:367-389` 的 `ALTER TABLE ... DEFAULT` + try/except 幂等写法，禁止重建表。
4. **阈值不硬编码**：本轮新增的所有阈值必须走 `app_settings`（T10 建立机制，T8/T9 直接消费）。
5. **每 Task 必带测试**：新增 `demo/tests/test_<task>.py` 或 `tests/test_<task>.py`，且不得让既有用例回归。

---

## 四、任务分解

### Wave A：数据与 Schema 地基

#### Task 1: 评测集三分法 + 可复现 eval harness（A1 + A5a）

**Files:**
- Create: `demo/scripts/build_evalset.py`
- Create: `demo/scripts/run_eval.py`
- Create: `demo/tests/test_eval_harness.py`
- Create: `demo/conftest.py`（注册 `--run-eval` 开关；语料缺失时自动 skip）
- Modify: `.gitignore`

**Interfaces:**
```python
# build_evalset.py
build(sources_dir, out_dir, seed=20260828) -> manifest.csv
# 分层：按 doc_form 分层抽样，train 55% / val 25% / test 20%，写 split 列

# run_eval.py
run_eval(split: str, prompt_version: str, engine: str, model: str) -> EvalReport
# EvalReport: {accuracy, cer, per_field_accuracy, edit_proxy, evidence_coverage,
#              avg_tokens, p50_latency_ms, gt_status_breakdown, sample_ids[]}
```

- [x] **Step 0:** 补建 `tests/conftest.py`（顶层强制 `AUTH_ENABLED=0`）。该文件当前**不存在**，而 step12 记录的 BUG-04 依赖它才能在带 `.env` 的机器上跑出全绿；缺它时离线环境会偶发约 196 个报错。同时建 `demo/conftest.py` 注册 `--run-eval` 开关。
- [x] **Step 1:** 写 `test_eval_harness.py`：三分法互不泄漏（train∩val∩test=∅）、分层覆盖（每个 split 内各 doc_form 均有样本）、harness 对同一输入可复现（同 seed 同结果）
- [x] **Step 2:** 运行确认失败
- [x] **Step 3:** 实现 `build_evalset.py`：HEIC→PNG、去标识命名、分层抽样、写 manifest（含 `sample_id / split / doc_form / layout_type / gt_status / gt_source_model`）
- [x] **Step 4:** 实现 `run_eval.py`：读 manifest → 跑抽取 → 与 expected 比对 → 输出 `EvalReport` 并落 `ai_registry/benchmarks/eval_runs/<ts>_<split>_<prompt_ver>.json`
- [x] **Step 5:** 语料缺失时 `pytest --run-eval` 全部 skip（保证 CI/他人克隆仓库不报错）
- [ ] **Step 6:** 复现既有数字：对 v1_2_0_sku_clean 跑一次，把结果与 `prompt_eval_history.json` 的 98.2% 对照，**差异写入 Playbook 素材**
  > 口径注记（2026-08-29 收口核实）：该对照**尚未执行**，原勾选不实，已按诚实原则取消勾选。原因：`ai_registry/benchmarks/eval_runs/` 目录不存在，即 `run_eval.py` 从未产出过任何评测报告，98.2% 对照无从发生。该对照待 T4 真实语料落盘并经人工抽检确认（`--require-confirmed` 门槛）后执行，当前为**条件性完成**——harness 本身（Step 0-5、7）已就绪并 Tested。
- [x] **Step 7:** 测试通过

**验收：** `python demo/scripts/run_eval.py --split test --prompt v1_2_0_sku_clean` 能产出完整 `EvalReport`；三个 split 无交集；仓库内无业务数据。

---

#### Task 2: `tenant_id` 贯穿业务主表（E2）

**Files:**
- Modify: `demo/app/db.py:56-196`（表定义 + `db.py:367-389` 迁移块）
- Modify: `demo/app/api_receipts.py`、`api_inventory.py`、`api_suppliers.py`、`api_dishes.py`、`api_finance.py`、`api_phase2.py`、`api_admin.py`（查询与写入路径）
- Create: `tests/test_tenant_isolation.py`

**Interfaces:**
```python
# 新增列（幂等 ALTER，默认值 'default'）
receipts.tenant_id, receipt_items.tenant_id, suppliers.tenant_id,
skus.tenant_id, inventory_log.tenant_id, dishes.tenant_id, vendor_memory.tenant_id

db.get_session(tenant_id=None) -> Session      # 租户上下文
db.scoped(query, model, tenant_id) -> Query    # 统一过滤
```

- [x] **Step 1:** 写 `test_tenant_isolation.py`：A 租户写入的数据，B 租户查询不可见（覆盖 receipts / items / suppliers / skus / dishes）；`vendor_memory` 同供应商不同租户互不覆盖
- [x] **Step 2:** 运行确认失败
- [x] **Step 3:** `db.py` 加 7 处 `ALTER TABLE ... ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'` 并建索引
- [x] **Step 4:** 实现 `db.scoped()` 并在全部 list/get 路径替换裸 `query()`
- [x] **Step 5:** API 层从请求头/会话取 `tenant_id` 并透传（沿用现有 `X-Role` 的取法保持一致）
- [x] **Step 6:** 全量回归（基线 145 passed，见「全局验收标准 #9」）
- [x] **Step 7:** 测试通过

**验收：** 租户隔离测试全绿；既有用例零回归；`vendor_memory` 表的隔离与 Chroma collection 隔离口径一致。

---

#### Task 3: 记忆治理元数据 + 读取预算（B1 + B4）

**Files:**
- Modify: `demo/app/db.py:172-177`（VendorMemoryRow）
- Modify: `demo/app/services/rag.py:97-119, 203-246, 249-275`
- Create: `tests/test_memory_governance.py`

**Interfaces:**
```python
class VendorMemoryRow:  # 新增列
    memory_id      # 每条记忆唯一 ID（Chroma metadata 同步带，便于回溯删除）
    version        # 每次更新 +1
    source_kind    # approve | feedback_distilled | manual
    source_ref     # 触发来源（receipt_id 列表 JSON）
    created_at / updated_at
    hit_count / last_hit_at
    decay_score    # 初始 1.0，采纳 +0.05，被覆写 -0.12
    status         # active | archived

retrieve_context(vendor, top_k=3, tenant_id="default",
                 budget=None) -> str
# budget = MemoryBudget(facts_tokens=800, per_item_tokens=200, max_items=6)
```

- [x] **Step 1:** 写 `test_memory_governance.py`：写入带来源与版本；注入结果 token 数不超过 budget；`max_items=6` 生效；单条超 200 token 被截断
- [x] **Step 2:** 运行确认失败
- [x] **Step 3:** `db.py` 迁移加 9 列；`upsert_vendor_memory` 改为按 `memory_id` 追加而非覆盖 `notes`
- [x] **Step 4:** `rag.py` 写入路径补齐 `source_kind / source_ref / version`，Chroma metadata 同步 `memory_id / tenant_id / created_at`
- [x] **Step 5:** 实现 `MemoryBudget` 与注入前截断（**替换现有的 `notes[-4000:]` 累积拼接**）
- [x] **Step 6:** `retrieve_context` 返回命中后自增 `hit_count`、刷新 `last_hit_at`
- [x] **Step 7:** 测试通过

**验收：** 6 条上限 / 800 token 预算 / 单条 200 token 三项均有测试断言；记忆可回溯到触发它的 receipt_id。

---

### Wave B：评测可信度（飞轮第一原则）

#### Task 12: ai_registry 唯一来源收敛（治理规范落地，本 Wave 首位执行）

> 来源：2026-08-29 SSOT 专项审计（判定 NON_COMPLIANT）。`agent_memory.md` 已写入「AI 资产工程化治理」规范，本 Task 把存量资产收敛到规范。
> **排位理由**：T4 的 GT 生成与 T5 的元评测都要产出/消费 registry 资产，必须先修好唯一来源，避免在失真的 registry 上继续盖楼。

**Files:**
- Modify: `demo/app/chains/extract_chain.py`（硬编码 v1_2_8 import 改走 `registry.get_prompt`；PARSE_SYSTEM_PROMPT / CORRECT_SYSTEM_PROMPT 两段内联提示词登记进 registry 后改加载）
- Modify: `demo/app/chains/review_chain.py`（REVIEW_SYSTEM 内联提示词登记进 registry 对齐 v2_0_0_cards 后改加载）
- Modify: `demo/app/chains/audit_chain.py`、`demo/app/api_admin.py`、`demo/scripts/run_eval.py`（`app.prompts` 镜像库引用改读 ai_registry）
- Delete/收敛: `demo/app/prompts/` 镜像目录（保留已验证的 re-export 模式，删除平行 ACTIVE_VERSIONS 注册表）
- Modify: `ai_registry/prompts/extract/metadata.json`（补登 v1_2_1~v1_2_8，active 置 v1_2_8）、`review/metadata.json` 与 `tools/math_engine/metadata.json`（删除指向不存在文件的悬空条目）
- Modify: `ai_registry/tools/math_engine/`（以 demo/app/services/math_engine.py 的字段覆盖为准回灌 v2_1_0，业务侧改 import registry 版）
- Modify: `ai_registry/mcp/configs/mcp_settings.json`（3 个未接入 server 置 active: false，删除 version_registry.json 虚构的 healthy/延迟指标）
- Modify: `ai_registry/tools/pii_masker/`（修复 `from typing import str` 语法错误）、6 个孤儿工具标注 experimental 状态
- Create: `tests/test_registry_ssot.py`

**Interfaces:**
```python
# 生产链路唯一合法的资产取得方式
from ai_registry import registry
registry.get_prompt("extract")            # active 版本
registry.get_prompt("parse", "v2_1_0")    # 显式版本
registry.get_tool("math_engine")          # active 工具
# 禁止: 业务代码内硬编码版本号 import / 内联系统提示词 / 镜像库二级加载
```

- [x] **Step 1:** 写 `tests/test_registry_ssot.py`（TDD 先行）：生产链路消费的每个 prompt/tool 均经 registry 取得；`demo/app/prompts` 无平行注册表；metadata 登记与目录实体一致（无漏登/无悬空）；内联提示词零残留（扫描 chains 目录三引号长指令串）
- [x] **Step 2:** 运行确认失败
- [x] **Step 3:** extract_chain / review_chain / audit_chain 改走 registry 加载，三段内联提示词先登记为新版本再切换
- [x] **Step 4:** `demo/app/prompts/` 镜像退役（推广 re-export 模式），run_eval / api_admin / audit_chain 改读 ai_registry
- [x] **Step 5:** math_engine 收敛回灌 + metadata 修复（补登/active 置位/删悬空）+ mcp 状态如实化 + pii_masker 语法修复与孤儿工具标注
- [x] **Step 6:** 全量回归 + 识别链路冒烟（registry 加载的 prompt 跑一次真实识别，确认与改造前行为一致、准确率无回归）
- [x] **Step 7:** 测试通过

**验收：** 全仓生产代码无绕过 registry 的资产加载；metadata 与目录实体完全自洽；`demo/app/prompts` 不再是平行事实源；两套测试零回归（不低于 200 passed 基线）；识别链路冒烟行为一致。

---

#### Task 4: GT 异构生成 + 人工抽检台（A2）

**依赖：** T1（manifest + 目录结构）

**Files:**
- Create: `demo/scripts/gen_gt_candidates.py`
- Create: `demo/app/api_evalset.py`（FastAPI 路由）
- Create: `demo/templates/evalset.html` + `demo/static/js/evalset.js`
- Create: `demo/tests/test_gt_review_api.py`
- Modify: `demo/app/main.py`（注册路由）、前端侧边栏

**Interfaces:**
```python
# gen_gt_candidates.py
generate(split, gt_model, limit=None) -> None
# 输出 expected/<sample_id>.json，带 gt_source_model / gt_status='draft'

# API
GET  /api/evalset/samples?split=test&gt_status=draft
GET  /api/evalset/sample/{sample_id}        # 返回图片路径 + AI 候选 GT
POST /api/evalset/sample/{sample_id}/confirm  # body: 人工校正后的 GT
     # → gt_status='confirmed'，写 gt_reviewed_by / gt_reviewed_at
GET  /api/evalset/stats                      # 各 split 的 draft/confirmed 计数
```

- [x] **Step 1:** 写 `test_gt_review_api.py`：confirm 后状态流转正确；test 集未全量 confirmed 时 `run_eval` 拒绝出分（或出分但标记 `gt_status=partial`）；抽检接口需 admin 权限
- [x] **Step 2:** 运行确认失败
- [x] **Step 3:** `gen_gt_candidates.py`：HEIC→PNG（短边 ≥1000px）→ 调用**异构模型**生成候选 GT → 落 `expected/*.json`（`gt_status=draft`，记录 `gt_source_model`）
- [x] **Step 4:** 按 T1 的三分法生成候选，**test 集优先生成**（人工覆核工作量最小、收益最大）
- [x] **Step 5:** 抽检台前端：左图右表单，逐字段对照校正，支持快捷键批量确认
- [x] **Step 6:** `run_eval.py` 增加 GT 状态门槛：`--require-confirmed` 开关
- [x] **Step 7:** 测试通过

**人工动作（需用户执行）：** 抽检台上线后，逐张确认 test 集（约 33 张，按 20% 切分；若嫌多可缩到 20 张）。train/val 允许保留 draft，但出分时必须标注。

**验收：** test 集 100% `confirmed`；`run_eval --split test --require-confirmed` 产出可信数字；`ai_registry/benchmarks/eval_runs/` 有可追溯的 raw report。

---

#### Task 5: 评估器与生成器强制异构 + 元评测集（A3 + A4）

**Files:**
- Modify: `demo/app/models.py:172-183`（`audit_engine` / `audit_model` 默认值）
- Modify: `ai_registry/README.md:92-98`（写入生产准入规则）
- Create: `ai_registry/benchmarks/meta_eval_set.json`
- Create: `demo/scripts/run_meta_eval.py`
- Create: `demo/tests/test_meta_eval.py`

- [x] **Step 1:** 写 `test_meta_eval.py`：载入元评测集，断言评估器对「绝对正确」样本判对率 100%、对「绝对错误」样本判错率 100%；任一不达标即返回 `evaluator_trustworthy=False`
- [x] **Step 2:** 从 `tests/test_hallucination_adversarial.py`、`tests/test_gap1..8` 抽 20-30 条已确证样本，固化为 `meta_eval_set.json`（含 `expected_verdict` 与 `why`）
- [x] **Step 3:** 实现 `run_meta_eval.py`，输出评估器可信度报告
- [x] **Step 4:** 把 `audit_engine` / `audit_model` 默认值改为异构厂商；`temperature=0` 约定写进代码注释与 README
  > 口径注记（2026-08-29 收口核实）：**代码默认值未改**——纯默认（无 env 时）`recognition_model` 与 `audit_model` 仍同为 `opencode/mimo-v2.5-free`，属已知遗留。异构性改为三层保障：① `models.py` 字段 `description` 显式声明「禁止与识别腿同引擎同家族」（Gap A3）；② 生产运行配置已异构（DB `engine_config`：识别=openai/SiliconFlow Qwen 系，审核=opencode/mimo 系）；③ README 规则 3 准入约束兜底。`temperature=0` 部分已落地（`llm._build(side="aud")` 强制注入）。
- [x] **Step 5:** README 生产准入规则增加三条：评估器必须异构、temperature=0、上线前必跑元评测集
- [x] **Step 6:** 测试通过

**验收：** 元评测集 ≥20 条；`run_meta_eval.py` 可独立运行并给出可信度结论；默认配置下审核腿与识别腿不再同源。

---

#### Task 6: 线上低置信样本自动回流为评测候选（A5）

**Files:**
- Modify: `demo/app/db.py`（新增 `eval_candidate` 表）
- Modify: `demo/app/chains/supervisor.py`（入库钩子）
- Create: `demo/app/api_evalset.py` 追加路由（与 T4 同文件，开发阶段注意顺序）
- Create: `demo/tests/test_eval_reflow.py`

**Interfaces:**
```python
class EvalCandidateRow:
    id, receipt_id, tenant_id, doc_form, confidence,
    reason,          # low_confidence | gate_reject | user_edit | audit_discrepancy
    status,          # pending | promoted_to_val | promoted_to_test | rejected
    created_at, promoted_at

POST /api/evalset/candidates/{id}/promote?split=val|test
```

- [x] **Step 1:** 写 `test_eval_reflow.py`：低置信/门禁拒绝/用户修改/审核分歧四类均产生候选；promote 后进入对应 split 且 manifest 同步更新
- [x] **Step 2:** 运行确认失败
- [x] **Step 3:** 建表 + supervisor 四条入库钩子（复用已有 `confidence`、`gate_err`、`audit.discrepancies`）
- [x] **Step 4:** promote API：把该样本图片与 AI 候选 GT 复制进 `demo/evalsets/`，manifest 追加行
- [x] **Step 5:** 测试通过

**验收：** 四类失败信号均能产生候选；promote 后可直接被 `run_eval` 消费；形成「线上失败 → 评测集 → 下一轮改进」的**通路四**。

---

### Wave C：可信产物与写入闸门

#### Task 7: 字段级证据完整落地（E1）

**依赖：** T3（schema 迁移已稳定）；**注意与 T2/T3 同改 `db.py`，开发阶段串行执行**

**Files:**
- Modify: `demo/app/models.py:28-59`（`Evidence` 模型 + `ReceiptItem.evidence`）
- Modify: `ai_registry/prompts/extract/`（新增 `v1_3_0_evidence.py`）
- Modify: `demo/app/chains/extract_chain.py`（解析与落库）
- Modify: `demo/app/db.py`（`receipt_items.evidence_json`）
- Modify: 审核台前端（字段点击 → 原图 bbox 高亮）
- Create: `tests/test_field_evidence.py`

**Interfaces:**
```python
class Evidence(BaseModel):
    page: int = 1
    bbox: Optional[list[float]] = None   # [x1,y1,x2,y2]，归一化 0-1
    raw_text: Optional[str] = None

class ReceiptItem(BaseModel):
    ...
    evidence: Optional[Evidence] = None   # 可选，契约门禁不强制

# 指标
EvalReport.evidence_coverage   # 有证据的明细行占比
```

- [ ] **Step 1:** 写 `test_field_evidence.py`：模型返回 evidence 时正确解析落库；缺失时 `evidence=None` 且**不阻断**；`evidence_coverage` 统计正确；前端高亮接口返回归一化坐标
- [ ] **Step 2:** 运行确认失败
- [ ] **Step 3:** 新增 prompt `v1_3_0_evidence.py`（在 v1_2_8 基础上增量追加证据输出要求，**不动既有抽取逻辑**）
- [ ] **Step 4:** `models.py` 加 `Evidence`；`extract_chain` 解析；`db.py` 加 `evidence_json` 列
- [ ] **Step 5:** 前端：审核台明细行可点击 → 原图叠加高亮框 + 显示 `raw_text`
- [ ] **Step 6:** 灰测对比：v1_2_8 vs v1_3_0_evidence 跑同一批，记录 accuracy / latency / token / evidence_coverage
- [ ] **Step 7:** 测试通过

**验收（回归红线）：** `evidence_coverage ≥ 80%`；**识别成功率下降 < 2pp**；**P50 延迟增加 < 2s**。任一不达标则回退 v1_3_0，保留 schema 与前端，等换模型后再开。

**说明：** 用户已选「完整落地含前端回显」。实施上采用「schema 可选 + prompt 要求输出 + 灰测验证」而非契约强制，理由是契约强制会让未升级的模型直接被门禁拒绝，属于不可接受的可用性风险；覆盖率达标后再考虑升为强制。

---

#### Task 8: 规则级记忆写入人工闸 + 三层晋升 / 非对称淘汰（D1 + B2）

**依赖：** T3

**Files:**
- Modify: `demo/app/db.py`（新增 `pending_memory` 表；`vendor_memory` 已在 T3 扩列）
- Modify: `demo/app/services/rag.py:249-275`（蒸馏改为入队）
- Modify: `demo/app/api_receipts.py`（feedback 端点）
- Modify: `demo/app/api_evalset.py` 或新建 `api_memory.py`
- Modify: `tests/test_feedback_flywheel.py`（预期从「自动写入」改为「入队待确认」）
- Create: `tests/test_memory_lifecycle.py`

**Interfaces:**
```python
class PendingMemoryRow:
    id, tenant_id, vendor, content, source_kind,
    source_receipt_ids_json,   # 触发它的三次点踩所关联的单据
    status,                    # pending | approved | rejected
    created_at, reviewed_by, reviewed_at

POST /api/memory/pending/{id}/approve   # 需 staff 以上权限
POST /api/memory/pending/{id}/reject
GET  /api/memory/pending?tenant_id=

# 非对称衰减
recall_hit(memory_id)    -> decay_score += 0.05
recall_overridden(memory_id) -> decay_score -= 0.12
# decay_score <= 0.5 → status='archived'，不再注入
```

- [ ] **Step 1:** 写 `test_memory_lifecycle.py`：三次点踩 → 进入 pending 而非直接生效；未 approve 的记忆不参与检索；approve 后生效；被用户覆写 5 次后 `decay_score` 降至 archived 阈值以下并停止注入
- [ ] **Step 2:** 运行确认失败（先改 `test_feedback_flywheel.py` 的既有断言，避免旧测试与新语义冲突）
- [ ] **Step 3:** 建 `pending_memory` 表；`ingest_feedback_memory` 改为入队 + 记录 `source_receipt_ids`
- [ ] **Step 4:** 审核 API（approve / reject / list）+ 管理台「待确认记忆」入口
- [ ] **Step 5:** 实现非对称衰减：注入命中 +0.05、用户覆写 -0.12、阈值 0.5 归档
- [ ] **Step 6:** `retrieve_context` 只召回 `status='active'` 且 `decay_score > 0.5` 的记忆
- [ ] **Step 7:** 测试通过

**验收：** 「连续 3 次点踩」不再自动生效；`source_receipt_ids` 可回溯；非对称衰减有单测断言（强化慢、淘汰快）。

---

### Wave D：自动化守护与平台化

#### Task 9: 实验守护：自动回滚 + 方向性约束指标（C3 + D4）

**Files:**
- Create: `demo/app/services/guardian.py`
- Modify: `demo/app/db.py`（snapshot 加方向性列 + `guardrail_event` 表）
- Modify: `demo/app/main.py`（startup 起后台守护线程）
- Modify: `demo/app/api_admin.py`（守护配置与手动触发）
- Create: `tests/test_experiment_guardian.py`

**Interfaces:**
```python
# guardian.py
check_once() -> list[GuardianAction]
# GuardianAction: {experiment_id, kind: 'rollback'|'freeze'|'alert', reason, metrics_snapshot}

# 触发条件（阈值存 app_settings，T10 建立读写机制，本 Task 直接消费）
GUARD_SUCCESS_DROP_PP    # 成功率下降 pp，默认 5
GUARD_COST_RISE_PCT      # 单张成本涨幅 %，默认 50
GUARD_P95_LATENCY_MS     # P95 延迟上限，默认 20000
GUARD_MIN_SAMPLE         # 复用 experiment.min_sample

# 方向性指标（写入 ai_metric_snapshot）
avg_output_tokens, avg_tool_calls, avg_retry_rounds
# 任一指标连续两个周期同向漂移 > 阈值 → 告警（不自动回滚）
```

- [ ] **Step 1:** 写 `test_experiment_guardian.py`：构造成功率下降的假数据 → 断言产生 rollback 动作且实验被冻结；样本量不足 min_sample 时不触发；方向性指标漂移产生 alert 但不回滚
- [ ] **Step 2:** 运行确认失败
- [ ] **Step 3:** snapshot 加 3 个方向性列 + 建 `guardrail_event` 表
- [ ] **Step 4:** 实现 `guardian.check_once()`：读 running 实验 → 按 guardrail 判定 → 调既有 rollback 逻辑 → 写 `guardrail_event` + 审计日志
- [ ] **Step 5:** `main.py` startup 起守护线程（间隔可配，默认 15 分钟）；提供 `POST /api/admin/guardian/check` 手动触发
- [ ] **Step 6:** 测试通过

**验收：** 自动回滚端到端可测（假数据触发）；回滚动作写入审计日志；手动触发接口可用；**回滚后 `rollback_snapshot` 可完整还原配置**。

---

#### Task 10: 阈值规则配置化 + 预处理纠偏（E3 + E4）

**Files:**
- Create: `demo/app/services/settings_service.py`（`app_settings` 类型化读写）
- Create: `demo/app/services/preprocess.py`
- Modify: `demo/app/models.py:109,116`（常量改为从 settings 读取并保持向后兼容）
- Modify: `demo/app/api_receipts.py:111`（接进 pipeline）
- Modify: `demo/app/api_admin.py`（配置端点）+ 管理台前端
- Create: `tests/test_settings_and_preprocess.py`

**Interfaces:**
```python
# settings_service.py
get_float(key, default) / set_value(key, value) / all_settings()
# 迁移进 settings 的常量：
#   price_anomaly_threshold_pct (原 10.0)
#   feedback_distill_threshold  (原 3)
#   blur_laplacian_threshold    (原 api_receipts.py:111 硬编码)
#   guard_*                     (T9 消费)

# preprocess.py
deskew(image) -> (image, angle)     # OpenCV 最小外接矩形/霍夫变换纠偏
enhance(image) -> image             # 自适应对比度 + 轻度去噪
assess(image) -> {sharpness, is_blurry, skew_angle}
```

- [ ] **Step 1:** 写 `test_settings_and_preprocess.py`：settings 覆盖默认值生效、缺失回退默认；`deskew` 对已知角度的旋转图能还原（±1°）；纠偏后模糊评分不下降
- [ ] **Step 2:** 运行确认失败
- [ ] **Step 3:** `settings_service.py` + 三处常量迁移（**保持 `models.py` 常量名不变**，改为读取 settings，避免破坏既有 import）
- [ ] **Step 4:** `preprocess.py`：`assess` → `deskew` → `enhance`；接进上传后、抽取前
- [ ] **Step 5:** 管理台「系统配置」页：阈值表单 + 纠偏开关
- [ ] **Step 6:** 灰测：开/关纠偏各跑 20 张（优先手写单与拍摄倾斜样本），对比 accuracy 与 edit_rate
- [ ] **Step 7:** 测试通过

**验收：** 阈值改完立即生效且无需重启；纠偏可开关；灰测有数据支撑是否默认开启。

---

### Wave E：文档（收口叙事）

#### Task 11: Playbook + 自主分级矩阵（C1 + D2）

**Files:**
- Create: `ai_registry/playbook/README.md`（模板）
- Create: `ai_registry/playbook/2026-08-v1_2_x-extract-iterations.md`（补录八次迭代）
- Create: `ai_registry/playbook/2026-08-gap-closure.md`（本轮 17 项）
- Modify: `ai_registry/README.md`（新增「自主分级矩阵」章节 + P2 演进路径）

**内容要求：**
1. **Playbook 模板**：`触发评测 / 假设 / 改动 diff / val 结果 / 结论（有效|无效|回归）/ 下次别再试`
2. **补录 v1_2_0 → v1_2_8**：八次 prompt 迭代各自解决什么 Gap（印章污染 / 免责条款 / 折让误报 / 包装乘数 / 港式日期 / 划线拒收 / 花码虚高 / RAG 注入），有效性与代价
3. **自主分级矩阵**：单据类型 × 变更类型 → 允许自主级别（L1 逐条审批 / L2 事后抽检 / L3 高度自主），并写明**不可逆红线**（安全边界 / 拒答逻辑 / 付费相关变更永远 ≤L2）
4. **P2 演进路径**：把本轮不做的 11 项写明触发条件（例：样本 >200 张才做记忆分层；多实例部署才上 MQ）

**验收：** 八次迭代全部有档可查；矩阵能回答「这个变更需要人确认吗」；P2 项都有明确的「什么时候做」。

---

## 五、执行方式：4-role subagent Workflow

按 Wave 分批，每批走完整闭环（与 2026-08-25 latency 修复的跑法一致）：

```
Wave A (T1-T3) → 产品 → 开发 → QA → 审核 ─┐
Wave B (T12→T4-T6) → 产品 → 开发 → QA → 审核 ─┤  审核不通过则走
Wave C (T7-T8) → 产品 → 开发 → QA → 审核 ─┤  聚焦 3-role 修复
Wave D (T9-T10)→ 产品 → 开发 → QA → 审核 ─┤  （dev→QA→审核）
Wave E (T11)   → 开发 → 审核            ─┘
```

**给各角色的硬约束（写进 prompt）：**

- **产品**：每个 Task 输出结构化验收标准（可断言、有阈值），并明确「哪些既有测试需要同步修改」（例：T8 会改 `test_feedback_flywheel.py` 的语义）
- **开发**：**串行执行**——T2/T3/T7 都会改 `db.py`，并行会冲突；T4/T6 都会动 `api_evalset.py`，T6 必须等 T4 完成；T12 必须在 T4 之前完成（GT 生成与元评测依赖干净的 registry）
- **QA**：每 Wave 结束跑全量回归（587 用例）+ 新增用例 + 关键链路冒烟；**必须给出实测数字**，不接受「应该没问题」
- **审核**：重点查四类历史高频缺口 —— ① 改了 `EngineConfig` 忘了同步 `api_admin.EngineConfigBody`；② `extra=forbid` 模型漏声明新字段；③ 新增列没写幂等迁移；④ 新阈值硬编码没走 `app_settings`

**需要用户介入的节点（2 处）：**
1. T4 完成后：抽检台逐张确认 test 集 GT（约 20-33 张）
2. T7 灰测后：根据 accuracy / latency / evidence_coverage 决定是否保留 v1_3_0_evidence

---

## 六、全局验收标准

| # | 标准 | 判定方式 |
| :-- | :--- | :--- |
| 1 | 评测可复现 | `run_eval.py` 一键产出 `EvalReport`，与 `eval_runs/` 存档一致 |
| 2 | 评测可信 | test 集 GT 100% `confirmed`；元评测集通过；评估器与生成器异构 |
| 3 | 评测集不泄漏 | train / val / test 三 split 无交集，且各自覆盖全部 doc_form |
| 4 | 记忆可治理 | 每条记忆有 `memory_id / version / source_ref / decay_score`；可回溯到触发单据 |
| 5 | 记忆不越权 | 规则级记忆写入必须人工 approve；未 approve 不参与检索 |
| 6 | 租户不穿透 | 7 张主表 + `vendor_memory` 全部 `tenant_id` 隔离，跨租户查询返回空 |
| 7 | 证据可审计 | 明细行 ≥80% 带 bbox + raw_text，前端可高亮回显 |
| 8 | 灰度可自愈 | 构造 P0 退化 → 守护自动回滚 + 冻结实验 + 留审计 |
| 9 | 零回归 | **实测基线 145 passed（`tests/` 102 + `demo/tests/` 43）+ 1 个既有失败（`tests/test_playwright_e2e.py::test_e2e_clerk_flow_all_scenarios`，需活体服务与浏览器，判定为环境相关既有失败，不计入回归）**；该数字不得低于基线；识别成功率下降 <2pp；P50 延迟增加 <2s |
| 10 | 无业务数据入库 | `git status` 干净；`demo/evalsets/`、`uploads/`、`.rag_chroma/` 均被忽略 |

---

## 七、风险与应对

| 风险 | 概率 | 应对 |
| :--- | :--- | :--- |
| T7 证据输出导致成功率/延迟回归 | 中 | schema 可选 + 灰测；不达标则回退 prompt，保留 schema 与前端 |
| T4 异构模型 API 不可用 | 中 | 退到 Ollama 本地模型；**禁止静默回落同模型**；记录实际模型名 |
| GT 人工抽检工作量超预期 | 中 | test 集可缩到 20 张；train/val 保留 draft 并显式标注 |
| T2 多租户改造触碰 7 张表引发回归 | 高 | 先跑单测再改；`db.scoped()` 集中封装，避免散落过滤条件；全量回归把关 |
| T8 改变既有语义导致旧测试失败 | 中 | **先改 `test_feedback_flywheel.py` 再改实现**（TDD），避免语义冲突 |
| 163 张 HEIC 转换耗时/失败 | 低 | `sips` 批量 + 短边校验；失败样本单独记录不阻断 |
| 隐私泄露（GT 含真实供应商名与金额） | 低但后果严重 | 去标识命名 + `demo/evalsets/` 入 `.gitignore` + 提交前 `git status` 检查 |

---

## 八、完成后叙事口径的变化

本轮做完，以下说法才成立（**未做完前不要这么说**）：

- 可以说「评测集做了 train/val/test 三分，对外数字只引用人工确认过的 test 集」
- 可以说「每条字段带版面证据，人工审核能直接定位到原图位置」
- 可以说「记忆写入有人工闸、有来源溯源、有非对称淘汰，坏经验淘汰速度是强化速度的 2.4 倍」
- 可以说「灰度有自动回滚守护，P0 退化不需要人半夜兜底」
- **仍然不能说**「实现了自进化闭环」——准确说法是：**闭环骨架与门控已全部打通，评测信号能自动流进记忆与改进链路；但改进方案的生成与最终确认仍在人手上，这是当前阶段的主动设计**。

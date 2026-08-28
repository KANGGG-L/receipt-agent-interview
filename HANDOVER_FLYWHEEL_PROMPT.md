# 接手 Prompt：自进化飞轮 Gap 补全 — Wave A 剩余（T2/T3）及后续 Waves

> 你是一名接手的工程 Agent。上一个会话已完成了 Wave A 的 T1 并通过了全量回归，随后编排 workflow 中断。
> 本文件是**完整自洽**的交接说明：不依赖任何对话历史，读完即可开工。
> 动手前请先读仓库根目录 `agent_memory.md`（行为规范：禁 emoji、禁 PS 废话、独立 Subagent 编排、数据脱敏）。

---

## 0. 一句话使命

按 `docs/superpowers/plans/2026-08-28-flywheel-gap-closure.md` 这份已批准的计划，把自进化飞轮 Gap 分析（`docs/06-adds-on/04-自进化飞轮与平台架构Gap分析.md`）判定的 **P0 七项 + P1 十项**补齐。当前进行到 **Wave A**：T1 已完成，**T2、T3 待做**。

---

## 1. 必读文件（按此顺序）

| 顺序 | 文件 | 用途 |
| :-- | :-- | :-- |
| 1 | `agent_memory.md`（仓库根） | 行为规范，必读 |
| 2 | `docs/superpowers/plans/2026-08-28-flywheel-gap-closure.md` | **本计划的唯一权威**，含每个 Task 的 Files/Interfaces/Steps/验收 |
| 3 | `docs/06-adds-on/04-自进化飞轮与平台架构Gap分析.md` | Gap 编号（A1-A6/B1-B5/C1-C6/D1-D4/E1-E9）的判定依据与 file:line 证据 |
| 4 | `docs/06-adds-on/document-ai-system-architecture.html` | Gap 分析的两篇基准之一（平台五层架构蓝图，见第 9 节背景） |
| 5 | `.codebuddy/workflows/flywheel-gap-closure.js` | 可复用四角色 workflow 脚本（可选执行方式，见第 6 节） |

> 飞轮方法论原文（Gap 分析的另一篇基准）在仓库外：`/Users/ethan/Documents/Obsidian Vault/00-Inbox/Agent自进化飞轮：评测→记忆→落地→控制.md`。属用户的个人 Obsidian 库，仅供理解 Gap 编号的出处，实施不需要读它——计划与 Gap 分析文档已把其要求全部转译成任务。

---

## 2. 环境事实卡（全部实测过，直接信任）

- **Python**：固定用 `$HOME/.pyenv/versions/3.9.6/bin/python`（3.9.6）。禁用系统 `python`/`python3`。
- **测试命令**（在仓库根跑）：
  ```bash
  $HOME/.pyenv/versions/3.9.6/bin/python -m pytest tests/ -q
  $HOME/.pyenv/versions/3.9.6/bin/python -m pytest demo/tests/ -q
  ```
- **脚本目录有两个**：根 `scripts/` 与 `demo/scripts/` 并存，找脚本两边都查。
- **当前测试基线（T1 完成后实测）**：
  - `tests/`：**102 passed, 1 failed**
  - `demo/tests/`：**58 passed, 1 skipped**
  - 合计 **160 passed**（T1 之前是 145）
  - 唯一失败 `tests/test_playwright_e2e.py::test_e2e_clerk_flow_all_scenarios` 需活体服务 + 浏览器，**环境相关既有失败，不计入回归**。除它之外出现任何新失败都算回归。
- **历史口径陷阱**：docs 写「587 passed」，实际复现不出来。不要引用 587。
- **收据原图**（真实商户数据，绝不入库）：163 张 HEIC 在 `~/Desktop/hk/My Drive/Receipts/batch1`。
- **黄金集语料不在库里**：`demo/samples/` 只有 1 张。T1 已建好去标识 evalset 生成器，但真实语料要等 T4 阶段再产。

---

## 3. 当前精确状态（2026-08-28 20:45 实测）

### 已完成：T1（评测集三分法 + eval harness）

交付物（全部已存在、已实测）：
- `tests/conftest.py`（27 行）：在 `import app.*` 之前锁 `AUTH_ENABLED=0`（补上 step12 BUG-04 记录但从未落盘的那道锁），并把 `demo/` 与仓库根加入 `sys.path`
- `demo/conftest.py`（59 行）：注册 `--run-eval` 开关，语料缺失自动 skip
- `demo/scripts/build_evalset.py`（338 行）：HEIC→PNG、去标识命名（`S001.jpg`）、按 doc_form 分层抽样、写 manifest
- `demo/scripts/run_eval.py`（492 行）：读 manifest → 跑抽取 → 比对 expected → 产出 EvalReport
- `demo/tests/test_eval_harness.py`（355 行）：**15 passed, 1 skipped（实测）**
- `.gitignore`：已加 `demo/evalsets/`

### 未动工：T2、T3

实证（2026-08-28 20:45）：
- `demo/app/db.py` 的 receipts 表**无** `tenant_id` 列定义、迁移块**无** `ADD COLUMN tenant_id`（现有 14 处 tenant_id 全是既有的 `receipt_feedback` 代码，别误判为已做）
- `tests/test_tenant_isolation.py`、`tests/test_memory_governance.py` 均**不存在**
- `db.py` **无** `memory_id` / `decay_score`

### 危险区：12 个既有的未提交改动（不是你的，别碰别提交）

`git status` 里这些 `M` 文件是**此前前端打磨留下的未提交改动**，与本计划无关：
`demo/static/css/style.css`、`demo/static/js/main.js`、`demo/templates/index.html`、`demo/app/chains/supervisor.py`、`demo/app/services/inventory.py`、`demo/app/services/receipt_utils.py`、`demo/app/api_admin.py`、`demo/app/api_phase2.py`、`demo/app/api_receipts.py`、`demo/app/db.py`（部分）、`demo/README.md`、`demo/tests/test_demo.py`、`docs/05-AI产品体系与模块Spec/` 下两个文件。

**注意 `db.py` 同时含有既有改动**——你改它时是在既有未提交改动之上叠加，这不是你的错，但：
1. 不要 `git checkout -- demo/app/db.py` 之类的恢复操作（会毁掉别人的未提交工作）
2. 不要执行任何 `git commit`（除非用户明确要求）
3. 汇报改动时说明「db.py 含既有未提交改动，本轮新增的是哪些块」

---

## 4. 本轮任务：Wave A 剩余（T2 → T3，必须串行）

两个 Task 都会改 `demo/app/db.py`，**必须 T2 完成后再做 T3**。

### T2: tenant_id 贯穿业务主表（Gap E2）

**Files:** `demo/app/db.py`（表定义 + 迁移块 367-389 行）、`demo/app/api_receipts.py`、`api_inventory.py`、`api_suppliers.py`、`api_dishes.py`、`api_finance.py`、`api_phase2.py`、`api_admin.py`、新建 `tests/test_tenant_isolation.py`

**要做：**
1. 7 张表加 `tenant_id` 列（幂等 ALTER，沿用 db.py:367-389 的 `ALTER TABLE ... ADD COLUMN ... DEFAULT 'default'` + try/except 写法）：`receipts` / `receipt_items` / `suppliers` / `skus` / `inventory_log` / `dishes` / `vendor_memory`
2. 实现 `db.scoped(query, model, tenant_id)` 统一过滤封装，替换所有裸 `query()` 的 list/get 路径
3. API 层从请求头/会话取 `tenant_id` 并透传（取法与现有 `X-Role` 一致）
4. `tests/test_tenant_isolation.py`：A 租户写入 B 租户不可见（覆盖 receipts/items/suppliers/skus/dishes）；`vendor_memory` 同供应商不同租户互不覆盖

**完成判定：** 租户隔离测试全绿；既有用例零回归（160 基线不得跌破）；`vendor_memory` 的 RDBMS 层隔离与 Chroma collection 隔离口径一致。

### T3: 记忆治理元数据 + 读取预算（Gap B1 + B4）

**Files:** `demo/app/db.py:172-177`（VendorMemoryRow）、`demo/app/services/rag.py:97-119, 203-246, 249-275`、新建 `tests/test_memory_governance.py`

**要做：**
1. `vendor_memory` 加 9 列：`memory_id / version / source_kind(approve|feedback_distilled|manual) / source_ref(触发 receipt_id 列表 JSON) / created_at / updated_at / hit_count / last_hit_at / decay_score(初始 1.0) / status(active|archived)`
2. `upsert_vendor_memory` 改为按 `memory_id` **追加**而非覆盖 `notes`
3. `rag.py` 写入路径补齐来源元数据；Chroma metadata 同步 `memory_id / tenant_id / created_at`
4. 实现 `MemoryBudget(facts_tokens=800, per_item_tokens=200, max_items=6)`，注入前截断 —— **替换现有 `rag.py:271` 的 `notes[-4000:]` 累积拼接**
5. `retrieve_context` 命中后自增 `hit_count`、刷新 `last_hit_at`
6. `tests/test_memory_governance.py`：写入带来源与版本；注入 token 不超 budget；`max_items=6` 生效；单条超 200 token 被截断

**完成判定：** 6 条上限 / 800 token 预算 / 单条 200 token 三项均有测试断言；记忆可回溯到触发它的 receipt_id。

### T2/T3 之后的 Wave 概览（本轮不用做，做完 A 再说）

- Wave B：T4 GT 异构生成+人工抽检台 / T5 评估器异构+元评测集 / T6 低置信样本回流
- Wave C：T7 字段级证据完整落地 / T8 记忆写入人工闸+非对称淘汰（**会改 `tests/test_feedback_flywheel.py` 的语义，必须先改测试再改实现**）
- Wave D：T9 自动回滚守护+方向性指标 / T10 阈值配置化+纠偏
- Wave E：T11 Playbook+自主分级矩阵（纯文档）
- Wave B 之后需要**用户本人介入**两次：T4 后抽检确认 test 集 GT；T7 灰测后决定是否保留 v1_3_0_evidence

---

## 5. 工程约束（违反任意一条即任务未完成）

1. Python 固定 `$HOME/.pyenv/versions/3.9.6/bin/python`
2. 数据库迁移幂等：`ALTER TABLE <t> ADD COLUMN <c> ... DEFAULT ...` + try/except，禁重建表
3. 契约模型 `ReceiptData / ReceiptItem` 是 `extra="forbid"`，新增字段一律 Optional + 默认值
4. 新阈值/开关走 `app_settings`；`settings_service.py` 要到 Wave D 的 T10 才建，本 Wave 允许暂用模块级常量 + TODO 注释指向 T10
5. 绝不提交业务数据：`demo/evalsets/`、`uploads/`、`.rag_chroma/`、`*.db`、`work/`、`artifacts/` 不得 git add
6. 真实商户数据（图片/供应商名/金额）禁止写入任何会被提交的文件（含测试 fixture 与文档示例）
7. 改完在 `demo/` 下 import 自检：`$HOME/.pyenv/versions/3.9.6/bin/python -c "import app.main; import app.db"`
8. 只做任务范围内改动，不顺手重构、不引入新依赖（OpenCV/Pydantic/Chroma 已在依赖内）
9. 改变既有测试语义时，先改测试再改实现（TDD）
10. 不执行任何 `git commit`
11. **禁 emoji**（代码/注释/UI/文档/回复全部适用，箭头 →↔↑↓ 不算）
12. 审核角色重点查四类本项目踩过的坑：改配置模型忘同步 `api_admin.EngineConfigBody`（`extra="forbid"` 会 422）/ Pydantic 模型漏声明新字段 / 新列没写幂等迁移 / 阈值硬编码不走 app_settings

---

## 6. 执行方式（二选一）

### 方式 A（首选）：复用四角色 workflow 脚本

脚本：`.codebuddy/workflows/flywheel-gap-closure.js`，按 `args.wave` + `args.tasks` 参数化。
任务清单由 args 传入（**不要让产品角色自己拆任务**——上一轮就是这么挂的）。

历史教训（两次失败都要避开）：
- 第 1 次失败：产品角色自己拆任务，结构化输出 `files` 返回成字符串、`id` 缺失，schema 校验 3 次重试后中断
- 第 2 次失败：T1 完成后、QA 阶段附近进程死亡（输出文件 0 字节，原因不明）

**T2+T3 的启动 args（直接复制使用）**——注意 schema 全是字符串，`files`/`done` 等都填字符串：

```json
{"wave":"A2","planPath":"docs/superpowers/plans/2026-08-28-flywheel-gap-closure.md","projectDir":"/Users/ethan/Documents/GitHub/receipt-agent-interview","baseline":"160 passed (tests/ 102 + demo/tests/ 58); 1 既有失败 tests/test_playwright_e2e.py::test_e2e_clerk_flow_all_scenarios 需活体服务与浏览器，不计入回归","tasks":[{"id":"T2","title":"tenant_id 贯穿业务主表","gap":"E2","files":"demo/app/db.py, demo/app/api_receipts.py, demo/app/api_inventory.py, demo/app/api_suppliers.py, demo/app/api_dishes.py, demo/app/api_finance.py, demo/app/api_phase2.py, demo/app/api_admin.py, tests/test_tenant_isolation.py","done":"七张表均新增 tenant_id 列且迁移幂等；db.scoped() 统一封装并替换全部裸 query；跨租户查询返回空；既有用例零回归（160 基线不得跌破）","rewritesTests":false,"testsToUpdate":""},{"id":"T3","title":"记忆治理元数据 + 读取预算","gap":"B1 + B4","files":"demo/app/db.py, demo/app/services/rag.py, tests/test_memory_governance.py","done":"vendor_memory 新增九列治理元数据且迁移幂等；upsert 按 memory_id 追加；MemoryBudget(800/200/6) 生效并替换 notes[-4000:] 拼接；Chroma metadata 同步 memory_id/tenant_id/created_at；命中自增 hit_count；记忆可回溯到 receipt_id","rewritesTests":false,"testsToUpdate":""}]}
```

### 方式 B（workflow 再挂时的兜底）：直接派 subagent 逐 Task 实施

用 Agent 工具（general-purpose）串行派发：
1. 开发 subagent × 1：把第 4 节 T2 的全部内容 + 第 5 节约束 12 条写进 prompt，要求返回改动文件清单 + 自测命令与真实输出
2. 开发 subagent × 2：同上做 T3
3. QA subagent × 1：跑第 7 节的验收清单，必须贴真实数字
4. 审核 subagent × 1：`git diff` 对照计划第 4 节 + 第 5 节第 12 条的四类坑

---

## 7. 完成判定与证据要求（QA 必须逐条给出实测输出）

1. `pytest tests/ -q` 与 `pytest demo/tests/ -q`：passed **不低于 160 基线**，且除 playwright 那条外无新失败
2. `demo/` 下 import 自检通过
3. `git status --porcelain | grep -E "evalsets|uploads|\.rag_chroma|\.db$"` 无输出（业务数据未入库）
4. T2 验收：跨租户查询返回空（测试断言）；7 张表全有 tenant_id 且迁移幂等
5. T3 验收：800/200/6 三项预算有断言；memory 可回溯 receipt_id
6. **汇报纪律：任何「已完成/已验证」都必须附真实命令与输出数字，禁止无证据的完成声明**

---

## 8. 完成后的动作

1. 把计划文件 `docs/superpowers/plans/2026-08-28-flywheel-gap-closure.md` 中 T2/T3 的 checkbox 从 `- [ ]` 改为 `- [x]`
2. 按此格式汇报：每个 Task → 改动文件清单（区分本轮新增 vs 既有未提交改动）→ 自测命令与真实输出 → QA 数字 → 审核结论（approved/riskLevel/issues）→ 遗留问题
3. 等用户确认后再启动 Wave B（T4 起需要用户介入准备异构模型 API key 与 GT 抽检）

---

## 9. 背景速览（为什么做这些）

### Gap 分析的由来

2026-08-28，用户要求拿**两篇外部基准**审视当前项目设计是否 cover，产物落盘为 Gap 分析文档：

- **基准一（仓库外）**：`/Users/ethan/Documents/Obsidian Vault/00-Inbox/Agent自进化飞轮：评测→记忆→落地→控制.md`
  基于 Anthropic 递归自改进报告、斯坦福 CS329A、EvoAgentX 的方法论长文。核心框架 = 自进化四齿飞轮：
  信号（评测）→ 积累（记忆）→ 落地（工程化）→ 控制（人机协作）。三条第一原则：
  评测的可信度 > 系统的复杂度；记忆是治理问题不是存储问题；闭环的价值在环节之间的衔接。
  Gap 编号 A/B/C/D 四组即对应这四齿。
- **基准二（仓库内）**：`docs/06-adds-on/document-ai-system-architecture.html`
  「单据识别 + 系统管控平台」五层架构蓝图（业务应用层 / Document PaaS / 模型服务层 / 数据治理层 / 云原生 Infra）。
  来源背景：行业资深人士（MiniMax 架构师）2026-05-27 给的咨询蓝图，原件在 `/Users/ethan/Downloads/document-ai-system-architecture.html`，仓库内这份是拷贝。
  Gap 编号 E 组对应这篇。
- **分析产物**：`docs/06-adds-on/04-自进化飞轮与平台架构Gap分析.md`
  逐条要求 → 代码实证（含 file:line）→ 覆盖度判定。结论：信号 60% / 记忆 35% / 落地 45% / 控制 30% / 平台架构 50%；
  总判断「飞轮骨架已搭且质量高于同规模项目，但是人在推、不是自己在转」。
  列出 P0 七项 + P1 十项 + P2 十一项，本计划只做 P0+P1。

### 本计划的位置

本计划（`docs/superpowers/plans/2026-08-28-flywheel-gap-closure.md`）是该 Gap 分析的补全实施方案，11 个 Task 分 5 个 Wave。
项目本质是「单据识别 + 系统管控平台」（AI 产品经理面试作品集）。
Wave A 是地基：评测可复现（T1 已完成）、多租户贯穿（T2）、记忆治理（T3）。

**叙事口径提醒**：闭环骨架与门控搭好后，仍不能说「实现了自进化闭环」——准确说法是
「骨架与门控已全部打通，评测信号能自动流进记忆与改进链路；但改进方案的生成与最终确认仍在人手上，这是当前阶段的主动设计」。

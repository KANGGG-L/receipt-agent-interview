# 05 · VendorMemory 现状调查与大厂标准 Gap 分析

> **调查日期**：2026-09-22 ｜ **台账化修订**：2026-09-23
> **调查方式**：只读。未修改任何代码；未写 live 库与 live 向量库；未跑目录级 pytest（隔离探针一律 `DB_PATH`/`RAG_DIR` 指向临时目录）。
> **调查对象**：`demo/app/services/rag.py`（原 438 行，现 905 行）、`demo/app/db.py` 的 VendorMemory 段（现 6255 行）、`demo/app/api_memory.py`（217 行）、写入方 `api_receipts.py` / `api_inventory.py`、读取方 `extract_chain.py` / `supervisor.py`，以及 live 库 `demo/receipt_demo.db` 与 live 向量库 `demo/.rag_chroma`。
> **对照基准**：AWS Bedrock AgentCore Memory、Google Vertex AI Memory Bank / ADK、OpenAI ChatGPT memory、Anthropic Claude memory tool、mem0、Zep/Graphiti、Letta(MemGPT)、LangMem / LangGraph Store；治理基线取 OWASP Top 10 for Agentic Applications 2026（ASI06）与 GDPR / 香港 PDPO。
> **与 `04-自进化飞轮与平台架构Gap分析.md` 的关系**：04 是 2026-08-28 的四齿飞轮整体覆盖度评估（其中记忆齿判 35%）。本文件是其**记忆齿的专项复评与下沉**，覆盖 08-28 之后的 T3/T8/T10 演进，并给出新数字。

---

## 【2026-09-23 台账化修订】本文档的性质变了 —— 请按「台账」读，不要按「诊断书」读

**为什么必须改**：本文档 2026-09-22 是一份**诊断快照**。此后按 `docs/superpowers/plans/2026-09-22-vendormemory-convergence.md`（v2，8 项决策已拍板）推进了 20 余个波次，H1–H4 / M1–M11 / L1–L8 里**大量条目已修**。一份写得准确的诊断清单，在修复推进到一半时会**反转成新的误导源** —— 读者（面试官、接手人、下一个 agent）会以为那些缺陷还在。所以本文本次的修订方式不是"重写"，而是**逐条加状态列 + 加证据 + 重算成熟度**。

**读法（三条）**：

1. 本文所有 `file:line` 与状态**均由本轮修订者独立实测或读码得到**（不是照抄计划自述）。凡状态标「已修」的，都能由 §4 的台账表逐条复算。
2. **逐波证据的权威出处是计划文件的 [§10 滚动收口状态](../superpowers/plans/2026-09-22-vendormemory-convergence.md)**（`docs/superpowers/plans/2026-09-22-vendormemory-convergence.md`，主 agent 亲自验证、非 subagent 自述）。本文只做**状态汇总**，**不复制第二份逐波表**（避免两处互相矛盾）。
3. **「代码具备能力」与「在线生效」仍是两套口径，不可混读**（见 §6）。本次修订后前者的提升幅度远大于后者，原因见 §6.1。

**本次修订**：`docs/06-adds-on/05-...Gap分析.md` 的 §0 / §1.6 / §3 / §4 / §6 / §7 逐条加状态；新增 §8「原文 → 新文」对照与「已被证伪的段落」清单。**未删减任何已知遗留项**（未上线、北极星基线 0、A/B 真实口径样本不足等一律保留，见 §8.3）。

---

## 0. 一页结论

> **本节是 2026-09-22 的诊断快照，原文保留（它准确描述了当时的形态与判断依据）**。**2026-09-23 的状态更新见下方 §0.5**：四条 high 里 H1/H2/H3 已修、H4 部分修，判断"记忆是护城河"叙事的落差已显著收窄，但**"在线生效"口径仍未达标**（北极星与卡点见 §0.5）。

**（2026-09-22 快照 · 仍准确的部分）当前 VendorMemory 的真实形态，不是"供应商知识图谱"，而是一条"approve 时把单据明细文本追加进表 + 双写一份向量，检索时按供应商名精确取最新几条注入 prompt"的轻量先验链路。** 治理元数据、人工闸、读取预算三块在 08-28 之后确实补齐了（这是真进步，且"写入前人工闸"在行业中属**超配项**），但四件事使它距离"大厂级记忆系统"还很远：

1. **记忆几乎进不了主路径**。前端从不发送 `vendor_hint`（`demo/static/js/` 与 `demo/templates/` 零命中），无 hint 时首轮不检索（`extract_chain.py:502`）；首轮通过即 `break`（`supervisor.py:966-980`）→ 记忆实际只在「非快速门禁打回的第 2+ 轮」生效。live 库里 701 张单据只有 12 张（1.7%）落下了非空 `rag_context_json`。
2. **向量层已经和关系库彻底脱钩**。live 上 DB 9 行记忆、向量 179 条，**memory_id 交集为 0**：12 个向量 memory_id 全是孤儿（其中带 mid 的 24 条向量 vendor 全是测试夹具名 `测试供应商`），DB 的 9 个 memory_id 一条向量都没有；叠加 T8 的 `allowed_mids` 可回溯门 → **fuzzy 语义通道在 live 上结构性 0 产出**，179 条向量是"只写不读"的存储。
3. **多租户在写侧隔离、读侧分叉**。写入传真实 `tenant_id`，两个生产读点都不传、落默认租户（`extract_chain.py:503`、`supervisor.py:853`）。**反向危害已是现实**：default 桶里已有 9 条真实供应商记忆，它们会被注入给**任意租户**的识别请求；**正向危害目前是 latent**（非默认租户记忆取不到，一上多租户立即踩中 —— 两个向量库里 `tenant_*` collection 数为 0，说明隔离路径从未被行使过）。被「demo 三个账号 tenant 全是 default + `AUTH_ENABLED=0`」现状掩盖，零测试覆盖。
4. **治理是"只写不读"的半截治理**。`hit_count` / `last_hit_at` / `version` 三列零消费方；无删除、无编辑、无 TTL、无容量上限、无 DB↔向量对账；衰减只有惩罚一侧生效（命中强化被 `min(1.0, cur+0.05)` 与起始 1.0 封顶成恒等操作），且归档**不可逆**。其中**"无删除能力"已经不只是工程缺口**：香港 PDPO 第 26 条把"未删除已不再需要的个人数据"定为罪行（最高罚 HK$10,000，已核实官方原文），而记忆里存的是含品名与单价的明细。

**成熟度复评：记忆齿 35%（2026-08-28 快照）→ 58%（2026-09-22 复评，区间 55–62）**；控制齿 30% → 57%（区间 55–60%）：第①条红线从"无闸"变为"端到端有闸且有人可用界面"。两项上调的理由与抵消项见 §6。**注意"已实现"不等于"在跑"**：按"在线生效"口径，记忆齿应低于 45%。

### 0.5 状态更新（2026-09-23，本次修订）

**四条 high 的现状**：H1 租户分叉 **已修**、H2 向量脱钩（原形态）**已修且巡检 `consistent=true`**、H3 测试污染 **已修**、H4 记忆进主路径 **部分修**（入口已补齐，首轮注入本身未改造）。

**指标（2026-09-23 实测，`demo/scripts/memory_metrics.py --json` 只读 live）**：

| 指标 | 2026-09-22 | 2026-09-23 实测 | 目标 |
|---|---|---|---|
| 旧口径命中率（分母=全部单据） | 1.71%（12/701） | 1.71%（12/701，**已标注"不可用于判定"**） | — |
| **有效命中率**（分母=供应商可确定且有可用记忆） | `null`（分母 0） | **5.03%**（9/179） | **≥80%** |
| SKU 匹配率 | 23.73%（163/687） | 23.73%（163/687，未动） | 显著提升 |
| A/B 代理口径（供应商是否有记忆行） | +4.71pp | +4.71pp（27.93% vs 23.22%） | **翻正** |
| A/B 真实口径（本次是否真注入） | -7.42pp（n=12 不足以判定） | 未重测 | 翻正且给置信区间 |
| live `vendor_memory` | 9 行全 archived | **19 行（10 active + 9 archived）** | — |

**"现在真正卡住指标的是什么"（2026-09-23 dev_gaplog 复核后：两件）**：① **有效命中率的分母太小**（179，其中 701 单里 345 单连 `supplier_name` 都没有 → 注入入口根本没机会尝试）；② **首轮注入未改造**（H4：`extract_chain.py:507` 仍是 `if vendor_hint:` 才检索，`supervisor.py:1010` 首轮通过即 `break`。**2026-09-25 注**：首轮注入已由待办 W-1 于 09-24 落地改造——首轮注入分支现位于 `supervisor.py:796-803`（开关 `memory_first_round_inject_enabled`，`first_round_inject_enabled()` :138，**缺省关**），本句"未改造"的判断已过时，见 §7.2 W-1 注记）。**原列的第 ③ 件「混合召回 + rerank 未做」已不成立** —— P7·Y2 已落地（`memory_recall.py` 三腿混合召回 + 治理信号 rerank，见 §4.6）。→ 剩下两条不解决之前，命中率不会接近 ≥80%。

### 0.6 六条需要拍板的事项（速览 · 2026-09-22 快照，处置状态见 §0.7）

| # | 事项 | 性质 | 建议 |
|---|---|---|---|
| P0-1 | 读路径租户口径分叉（H1） | 缺陷 + 安全 | 立即修；把 `tenant_id` 改为必填以钉死 |
| P0-2 | 向量层与 DB 脱钩、fuzzy 通道死掉（H2） | 缺陷 + 数据 | 先判定存量向量清退或回填，再补对账闸门 |
| P0-3 | 测试从 demo/ 跑会写 live 向量库（H3） | 测试卫生 | conftest 补 `RAG_DIR` 隔离；判定三处残留向量目录 |
| P1-1 | 记忆进不了主路径（H4） | 产品决策 | 二选一：检索提前到首轮 / 叙事如实降级 |
| P1-2 | 无删除/编辑/上限，衰减不可逆（M2/M3） | 治理 + 合规 | 补删除端点（含级联删向量）+ 归档恢复通道 + 容量上限 |
| P1-3 | 对外叙事与代码不一致（3 处） | 口径风险 | 按 §5.3 改写；`03-实现现状与演进.md` 的 [PASS] 需撤回 |

### 0.7 六项拍板的处置状态（2026-09-23）

| # | 状态 | 落地证据 |
|---|---|---|
| P0-1 | **已交付** | H1 已修（见 §4 台账）；端到端探针（计划 §10「P4a 落地」行）证明「同租户检索返回内容 / 异租户返回空」 |
| P0-2 | **已交付** | D5 清退 + `_vector_write_allowed` 闸门 + 巡检工具 `memory_consistency.py`；本次实测 `consistent=true`（15 向量 / 10 mid / 孤儿 0 / 缺向量 0） |
| P0-3 | **已交付** | 两处 conftest 各 4 处 `RAG_DIR` 隔离 |
| P1-1 | **未完成（最高优先）** | 入口已补齐（前端 hand-pick + 预判 + 重试派生），但"首轮注入"本身未改造 → 见 §7 待办 W-1 |
| P1-2 | **大部分已交付** | 删除 / 恢复 / supersede / TTL / 巡检均已落地；**容量上限与"记忆编辑"未交付**（查看面已交付：`GET /api/admin/vendor-memory`，`api_admin.py:1148` + `main.js:13272`） → §7 待办 W-4 / W-5 |
| P1-3 | **未完成（属对外叙事，独立复核）** | `05` 的 §5.3 改写建议仍在；`03-实现现状与演进.md` 的 FR-9 `[PASS]` 行**未撤回** → 见 §8.2 |

---

## 1. 现状：VendorMemory 的五段事实

### 1.1 数据模型

| 层 | 实体 | 关键字段 |
|---|---|---|
| 关系库 | `vendor_memory`（一记忆一行，追加式） | `memory_id`(主键 UUID) / vendor / tenant_id / notes / sample / version / source_kind / source_ref / created_at / updated_at / hit_count / last_hit_at / decay_score / status |
| 关系库 | `pending_memory`（人工闸队列） | id / tenant_id / vendor / content / source_kind / source_receipt_ids_json / status / created_at / reviewed_by / reviewed_at |
| 向量库 | Chroma collection `vendor_memory` 或 `tenant_<tid>_vendor_memory` | metadata: vendor / kind(notes\|sample\|feedback_distilled) / tenant_id / memory_id / source_kind / created_at |

定义位置：`demo/app/db.py:241-266`（表模型）、`db.py:631-645`（幂等 ALTER 补 10 列）、`db.py:678-700`（`pending_memory` 建表）、`db.py:744`（`memory_id` 唯一索引）。

一个容易被忽略的事实：**live 库那 9 行的 `created_at` / `updated_at` 全为空串** → 它们不是 T3 之后经 `upsert_vendor_memory` 写入的行，而是旧库物理重建（`db.py:950-1030`）保留下来的历史行。也就是说，**live 上现存的记忆全部是 T3 之前的遗留数据**；T3 之后 approve 写入的行已在 09-20 那轮数据清理中被删除，而它们的向量留在了 Chroma 里 —— 这正是 1.3 节"交集为 0"的来历。

### 1.2 写入路径

**入口一：approve 回写**（`demo/app/api_receipts.py:1149-1157`）

```python
items_text = "\n".join(f"- {i['name']} {i['quantity']}{i['unit']} @{i['unit_price']} = {i['amount']}"
                       for i in db.get_receipt_items(receipt_id, tenant_id=_tenant_id(request)))
ingest_memory(row.supplier_name, items_text, notes=f"版式：{row.doc_form}",
              tenant_id=_tenant_id(request), receipt_id=receipt_id)
```

- `notes` = **仅** `f"版式：{doc_form}"` 一行（`doc_form` 是枚举，如 `printed_delivery_note`；为空时退化成 `"版式："`）。
- `sample` = 该单据全部明细拼成的文本，一行一条。
- **没有任何"提炼"环节**：不抽别名映射、不抽单位习惯、不抽版式特征、不算价格区间。`ai_registry/prompts/memory/v1_0_0.py` 里那份"供应商知识提炼引擎"的 prompt **零调用**（全仓只有 `metadata.json` 声明与 `tests/test_registry_ssot.py:523` 的目录检查）。
- 每次写入产生 **2 条向量**（`kind=notes` + `kind=sample`，`rag.py:171-182`），同 `memory_id`。

**入口二：SKU 别名合并"自学习"**（`demo/app/api_inventory.py:209-215`）

```python
ingest_memory("全局品类库", alias_note, notes=f"SKU别名合并学习：{primary_name}")
```

- vendor 是固定字面量 `全局品类库`，而检索入口是"供应商名"，`_vendor_related`（`rag.py:248-272`）对任何真实供应商名都返回 False → **永不召回的死写入**（实测 `_vendor_related('德利行 Tak Lee Hong','全局品类库')=False`；只有查询名含"全局"二字才放行）。
- 且**未传 `tenant_id`** → 永远落 `default` 租户。

**入口三（间接）：反馈蒸馏 → 人工闸 → approve**（`rag.py:381-402` → `api_memory.py:40-85` → `rag.py:405-438`）

- 触发条件：同供应商同租户**连续 3 次点踩**（`db.py:4932-4956`，阈值 `feedback_distill_threshold` 可配）。
- 蒸馏产物**先入 `pending_memory`（status=pending）**，人工批准后才落 `vendor_memory` + **1 条**向量（不是 2 条 —— 两类记忆的向量条数不一致）。
- 这是全仓**唯一**能往队列投递的路径（`grep enqueue_pending_memory` 仅 `rag.py:396` 一处）→ 人工无法主动沉淀一条记忆。

**去重与容量**

- 幂等键 = `(vendor, tenant_id, status='active', notes, sample)` **完全相等**（`db.py:2675-2681`），无归一化：`sample` 尾部多一个空格即视为新记忆。
- **无容量上限、无 TTL、无按供应商的行数上限**。live 里 `金百加咖啡(已入账)` 已有 32 条向量（=16 条记忆）。

**失败行为（本链路最要紧的一处）**（`rag.py:162-170`）

```python
stored_new = True
try:
    mid, stored_new = db.upsert_vendor_memory(...)
except Exception:
    stored_new = True          # 无日志
if not stored_new:
    return vendor
docs = [...]                    # 继续构造
_store(tid).add_documents(docs)  # 继续写向量
```

DB 写失败 **不留任何日志、不中断**，且照样写入 2 条向量（带着一个 DB 里不存在的 `memory_id`）。隔离复现：DB 抛错时新增 2 条向量、DB 0 行、无任何日志。这是 live 那批孤儿向量的**生成器**。反向的"DB 成功、向量失败"分支（`rag.py:185-190`）有 warning 但**无重试、无补偿、无对账**。

### 1.3 存储一致性（live 实测，2026-09-22）

| 指标 | 实测值 | 复现方式 |
|---|---|---|
| `vendor_memory` 行数 | 9（全部 `tenant_id=default`，`created_at`/`updated_at` 全空，`decay_score` 全 1.0） | `sqlite3 -readonly demo/receipt_demo.db` |
| Chroma collection 数 | 1（`vendor_memory`）；**无任何 `tenant_*_vendor_memory`** | `select id,name from collections` |
| 仓库根 `.rag_chroma` 同项复核 | 1 个 collection（`vendor_memory`）、338 条文档、121 个 distinct mid；同样**无 `tenant_*`** | 同上 |
| 向量条数 | 179 | `select count(*) from embeddings` |
| 带 `memory_id` 元数据的向量 | 24（12 个不同 mid） | `embedding_metadata where key='memory_id'` |
| **DB mid ∩ Chroma mid** | **0** | `comm -12` |
| Chroma 有 / DB 无（孤儿） | 12 个 mid / 24 条文档 | `comm -23` |
| DB 有 / Chroma 无 | 9 个 mid 全部 | `comm -13` |
| 无 `memory_id` 元数据的向量 | 155（分层：20 条连 `tenant_id` 也没有） | 对应 T2 前 / T2 后 B1 前两代写入 |
| 带 mid 的 24 条向量的 vendor | **全是 `测试供应商`**（测试夹具名） | `group by key,string_value` |

**由此得到的两个结构性结论：**

1. **fuzzy（语义）通道在 live 上是死的。** `rag.py:327-346` 要求 `dmid` 存在且 `dmid in allowed_mids`（`allowed_mids` 取自 DB 的 active 行）；155 条无 mid 直接丢弃，24 条有 mid 但不在 DB → 实测「fuzzy 通道可注入文档数 = 0」。179 条向量只占磁盘、不参与检索。**精确通道仍可用**（走 DB，不走向量），所以"记忆功能看起来是通的"。
2. **测试污染了 live 向量库。** `rag.PERSIST_DIR` 是 CWD 相对路径（`rag.py:24`），而两处 conftest 都**不隔离 `RAG_DIR`**（`grep -c RAG_DIR tests/conftest.py demo/conftest.py` → 0 / 0），DB 与图片目录却有 autouse 兜底 → 从 `demo/` 跑测试时，DB 被隔离到临时库、**向量却写进了 live 向量库**。磁盘上还有另两处：仓库根 `.rag_chroma`（338 条文档、121 个 mid、同样只有 `vendor_memory` 一个 collection）与 `demo/tests/.rag_chroma`（0 条），三者均被 `.gitignore:25` 忽略。
3. **租户隔离 collection 从未被创建过。** 两个向量库都只有 `vendor_memory` 一个 collection（`select count(*) from collections where name like 'tenant_%'` = 0），且向量 metadata 里 `tenant_id` 的**取值只有 `default`**（demo 库 159/179、根库 324/338）→ 说明**至今没有任何非默认租户的记忆写入发生过**。这与 H1（读侧恒取 default）叠加后，多租户记忆隔离在工程上是"代码能力在、路径从未行使、读侧还是错的"三件事叠加。

### 1.4 检索路径

`retrieve_context(vendor, top_k=3, tenant_id="default", budget=None)`（`rag.py:275-378`）

- **通道 1（精确/别名）**：`db.list_vendor_memory(vendor, tenant_id)` → 先 `vendor == v`，再 `_vendor_related(v, r.vendor)` 别名相关性 → 整体 `ORDER BY rowid DESC`。
- **通道 2（fuzzy）**：`similarity_search("供应商 X 的收据版式与单位习惯", k=top_k)` → 相关性门 → `allowed_mids` 可回溯门。
- **排序不使用任何治理信号**：`hit_count` / `last_hit_at` / `decay_score` / `created_at` 都不是排序键（`decay_score` 只用于"是否注入"的阈值过滤）。唯一排序是 rowid 倒序 = **最新写入优先**，即"最近几张单"而非"最有代表性的几条"。
- **`top_k` 只管 fuzzy**：实测 `top_k=1` 时精确通道仍注入 6 条。实际条数由 `max_items`(6) 与 `facts_tokens`(800) 决定。
- **预算口径是字符不是 token**：`_approx_tokens = len(text)`（`rag.py:61-63`，docstring 自承"1 字符按 1 token"）→ 对英文/数字明显偏保守（800 字符 ≈ 200 英文 token）。
- **相关性门存在短名泄漏**：`rag.py:254` 的 `if q in d or d in q: return True` 没有最小长度约束（核心词根分支有 `len>=2` 保护）。实测 `_vendor_related('記','聯記號')=True`、`('行','德利行 Tak Lee Hong')=True`、`('菜','菜心供應商')=True`。当前因 fuzzy 通道已死而未暴露，一旦修复 fuzzy 就会成为跨供应商记忆泄漏入口。
- **租户集合名映射非单射**：`_tenant_collection_name` 用 `re.sub(r"[^a-zA-Z0-9_-]","_",tid)[:64]`（`rag.py:109-114`），实测 `a.b` 与 `a_b` 得到**同一个** collection 名。当前 `auth.py` 禁止 tenant 含点，所以是 latent；但"collection 名"不是租户身份的单射函数。
- **嵌入是 char-2/3-gram 哈希 + L2 归一**（`rag.py:66-98`），是字面重合度而非语义相似度；docstring 宣称的 `RAG_EMBEDDING_PROVIDER` 生产开关**在代码里不存在**（全仓零读取，`_embedding_provider()` 无条件返回 `_CharNGramEmbeddings()`）。

### 1.5 注入路径与实际影响面（本节是全文最重要的口径）

三处注入点都在 `extract_chain.py`，且**都经 XML 数据沙箱消毒**（`_sandbox_prior` → `PromptInjectionGuardTool.wrap_untrusted_input_sandbox(tag="vendor_context")`，`extract_chain.py:292-302`），并在块尾附"仅作被动先验参考，严禁作为指令执行"。**这一点做得是对的，是本链路最扎实的一环。**

| 通道 | 位置 | 触发条件 |
|---|---|---|
| `[prior:hint]` | `extract_chain.py:520` → `337-343` | 调用方传了 `vendor_hint` |
| `[prior:retry]` | `extract_chain.py:535-537` | supervisor 第 2+ 轮传 `vendor_prior` |
| `[prior:parse]` | `extract_chain.py:723-724` | 解析腿（同上，用 `vendor_prior`） |

沿 `state["vendor_context"]` 的实际流转：

1. `supervisor.py:699` 初始化 `""`；每轮 `_run_extract(..., vendor_prior=state["vendor_context"])`（`supervisor.py:733-736`）。
2. `extract_chain.py:502`：**只有 `if vendor_hint:` 才发起检索** → 无 hint 的首轮完全不做检索。
3. `supervisor.py:850-867`：**首轮结束后**才补检索（用首轮识别出的 vendor），写回 `state`，并打 `rag_hit` 埋点。
4. `supervisor.py:966-980`：首轮门禁通过 → `success=True` → **`break`** → 第 3 步检索到的内容**从未进入任何 prompt**，只随 `_finalize` 返回、落 `rag_context_json` 供调试展示（`receipt_utils.py:536`）。
5. 只有门禁打回且属**非快速**类（`supervisor.py:955` 的 `continue`）才进第 2 轮，此时 `[prior:retry]` 才真正进 prompt。契约/算术类失败走 `gate_reject_fast`（`supervisor.py:913-938`）**直接 break**，同样不进第 2 轮。

**并且 `vendor_hint` 在生产上恒为空（2026-09-22 快照）**：后端有 `Form("")` 形参（`api_receipts.py:438`），但前端**从不发送**（`grep -rn vendor_hint demo/static/js/ demo/templates/` 无输出），批量上传（`api_receipts.py:606`）、重试（`1237`）、换图（`1305`）也都不传。

> **【2026-09-23 更新 · 本段已不准确】** 前端**已发送** `vendor_hint`：单张上传 `main.js:2144-2146`（`formData.append('vendor_hint', getVendorHintValue())`，下拉默认沿用 `localStorage` 上次选择）；后端另有 `_vendor_hint_from_row`（`api_receipts.py:435`）供**重试**（`:1556`）与**换图重解析**（`:1628`）从已落库单据行派生（不调预判模型），以及 `vendor_precheck.predict_vendor`（`:538`，开关缺省关）。**但"首轮注入"本身未改造**：`extract_chain.py:507` 仍是 `if vendor_hint:` 才检索、`supervisor.py:1010` 首轮通过即 `break` → **无 hint 的单据首轮仍不检索**（H4 部分修）。**（2026-09-25 注：首轮注入已由 W-1 于 09-24 落地改造——`supervisor.py:796-803` 首轮注入分支 + `extract_chain.py:532-537` W1-a/W1-b hint 与记忆并存；开关缺省关。本句"未改造"结论已过时，见 §7.2 W-1 注记。）**

**综合结论：**

| 路径 | 记忆是否进 prompt（2026-09-22） | 2026-09-23 变化 |
|---|---|---|
| 用户手填 vendor_hint | 进（首轮 `[prior:hint]`）—— **但前端不可达** | **前端已可达**（`main.js:2153-2155`） |
| 真实产品路径（无 hint） | **首轮不进**；首轮通过则整轮不进；仅"非快速门禁打回"的第 2+ 轮才进 | **未变**（首轮门与 `break` 未改） |
| 检索本身 | 首轮结束后**总会执行**，但结果只落 `rag_context_json` | **未变** |

live 侧的量：701 张单据中 12 张（1.7%）有非空 `rag_context_json`，其中 8 张是 `[prior:retry]` / `[prior:parse]`（来自 09-18 那批补测）、1 张来自 hint 通道。**这就是"供应商记忆是护城河"叙事与工程现实之间的全部落差。**（2026-09-23 复核：该数字**未变**，仍 12/701 —— 印证"入口补齐 ≠ 首轮注入已改造"。）

### 1.6 治理能力现状（2026-09-23 加状态列）

> 「2026-09-22 现状」列是原快照原文；「状态」列是本轮复核。逐条权威证据见 §4 台账。

| 能力 | 2026-09-22 现状 | **状态** | 2026-09-23 证据 |
|---|---|---|---|
| 人工闸（规则级写入） | **有且可用**：蒸馏产物入 `pending_memory`，admin 批准才生效；拒绝留档可查（`?status=rejected`）；approve/reject 都写系统审计日志（`rag.py:381-402`、`api_memory.py:40-117`） | 已修 | 另：**规则级候选一律进闸**（`api_receipts.py:1185`，单一实现 `_extract_and_enqueue_memory_facts`）；拒绝理由已落 `reject_reason`（`api_receipts.py:1123/1153`） |
| 闸门的界面 | 审批表格**存在且接线**在 `evalset.js:386-431` + `evalset.html:195,205`，页面由 `main.py:224` 提供 | 已修 | 仍为同一表格（`evalset` 页）；本项原本即达标 |
| 闸门的可发现性 | **入口挂错页面且无记忆语义**：主台两个入口文案全是 GT 相关；`main.js:5764` toast 不给跳转链接 | **已修** | `main.js:5973-5975` toast 现带 `action: {label:'查看待确认记忆'}`；`index.html:2377-2381` 新增「待确认记忆 N 条」角标（只读 `GET /api/memory/pending?status=pending`） |
| 闸门的覆盖范围 | **只有一条触发路径**（连续 3 次点踩）；人工无法主动往队列塞一条记忆 | **部分修** | 新增第二条来源：用户修正 → LLM 抽取出候选 fact 入队列（`api_receipts.py:1185` 单点实现 `_extract_and_enqueue_memory_facts`，被 `save_edited`/`approve_receipt` 调用）；冷启动种子工具亦可入队（`seed_memory_facts_from_history.py`）。**人工"主动塞一条"仍无 UI** |
| 衰减（非对称） | 覆写 -0.12 / 命中 +0.05，阈值 0.5 归档 | 已修（参数化） | 步长/阈值走 settings（`memory_decay_override_penalty` / `memory_decay_hit_bonus` / `memory_archive_threshold`） |
| 衰减的实际形态 | **只有惩罚一侧生效**：新行起始 `decay_score=1.0`，强化 `min(1.0, cur+0.05)` → **恒等操作** | **已修（原判定已失效）** | 新行起始分 `memory_initial_decay_score()`（`db.py:4469`，写入侧 `:3480` 消费，缺省 **0.7**）→ 强化产生实际上升 |
| 衰减的粒度 | 扣**该供应商全部 active 行**（无 `memory_id` 过滤） | 未修 | `db.py:4426-4430`（`apply_vendor_memory_override_penalty`，:4399 起）仍按 vendor + tenant + status 过滤 |
| 归档临界 | 1.0 → 0.5 共 5 次覆写 | 设计变更 | 起始分改 0.7 后临界次数随之变化（属有意调整，非缺陷） |
| 归档可逆性 | **不可逆**：归档行不在检索范围、`bump` 只改分数不改 status、无 API 恢复 | **已修（原判定已失效）** | `POST /api/memory/{id}/revive`（`api_memory.py:298`）+ `db.revive_vendor_memory:4621` |
| 扣分触发面 | 「保存时 unit_price/amount/supplier_name/total_amount 任一与旧值不同」**即扣**，不区分"AI 错"与"业务合理变更" | 未修 | `api_receipts.py:1400-1427` 仍按 old/new 全量 diff；该处注释自称"店员真的改了 AI 预填值"但**代码未对比 `ai_prefill_json`**（`ai_prefill_json` 仅用于埋点 `:1433`） |
| 删除 / 编辑单条记忆 | **三层全无** | **部分修** | 删除：`DELETE /api/memory/{id}`（`api_memory.py:268`）+ 按供应商（`:224`）+ `db.delete_vendor_memory`（`db.py:3914`）；编辑：仍无 |
| 查看已生效记忆 | **无**：`list_vendor_memory` 无 API 消费方、`get_vendor_memory` 零生产调用方 | **部分修** | `list_vendor_memory` 已有生产消费方（`rag.py:1097`、`api_admin.py:1201`）；**管理台查看面已交付**（`GET /api/admin/vendor-memory`，`api_admin.py:1148` + `loadVendorMemoryGovernance()`，`main.js:13272`）；**编辑面仍无**（D5） |
| TTL / 容量上限 / DB↔向量对账 | **全无** | **部分修** | TTL：`valid_to`（`db.py:286`）+ settings `memory_ttl_days`（`db.memory_ttl_days`，`db.py:3511`）；对账：`memory_consistency.py` + `GET /api/admin/memory-consistency`（`api_admin.py:923`）；**容量上限仍无** |
| 冲突检测 | **无**：无比较两条记忆内容的代码 | **部分修** | `memory_consolidate.py` 的 `decide_action` → ADD/UPDATE/DELETE/NOOP（含低可信不得覆盖高可信的守卫）；**但生成侧注入链路未接通**（P2b） |
| 治理元数据的消费 | `hit_count` / `last_hit_at` / `version` **三列零消费方** | **已修** | 三列均已真消费：写入侧 `db.bump_vendor_memory_hit`（`rag.py:1178` → `db.py:3983`）；**排序侧已消费** —— `memory_recall.rerank` 把 `decay_score` / `hit_count` / `last_hit_at` 并入排序分（`rag.py:1113`、`memory_recall.py:437-456`）。`version` 仍恒 1（有意设计，非缺陷） |
| 可观测 | 唯一埋点 `rag_hit` 事件；**无 per-memory 命中/贡献度报表** | **已修** | `get_retrieve_failure_counts()`（`rag.py:65`）；`GET /api/admin/memory-metrics`（`api_admin.py:1096`，口径 = `memory_metrics.py` 唯一实现）+ 看板 |

---

## 2. 行业基准与逐条对表

### 2.0 证据等级与来源说明

矩阵取值来自 2026-09-21/22 的联网调研，按证据等级标注：

- **[O]** 官方文档 / 官方博客 / 官方 API 参考；**[P]** 一手论文；**[3]** 第三方复述（需注意失真）；**[U]** 未公开或未查到（不推测）。
- 主线程**独立抓取复核**过的一手来源：AWS AgentCore Memory `memory-types.html`（异步 extraction+consolidation、长期记录语义）、mem0 `memory-types`（`ADD/UPDATE/DELETE/NOOP` 三段式、scope 标识、PII 警告）、OWASP Agent Memory Guard 项目页（原文"the attack survives context resets"）、香港 PCPD 官方 `ordinance.html`（**第 26 条属罪行，最高罚 HK$10,000**）。
- Vertex / OpenAI / Anthropic / Letta / Zep / LangMem 的取值来自本次调研并附官方链接（见附录 B）；其文档站对自动抓取多返回空内容，**本次未二次抓取**，引用时以附录 B 链接为准。
- 未公开项一律标 [U]：OpenAI 的存储结构/检索算法/TTL；AWS 的 record 总量上限与内部 rerank；Vertex 的 rerank/时间衰减；Letta 的 TTL。

### 2.1 行业九维度速览

| 维度 | 行业主流做法 | 本项目 VendorMemory |
|---|---|---|
| 1 写入触发 | 后台异步抽取+整合（AWS AgentCore）或显式触发（Vertex `GenerateMemories`）；hot path 同步写（LangMem、Letta） | 人审后 hot path 写入（行业中无一家以人审为主路径 → **超配项**，代价是覆盖率与时效） |
| 2 原子单位与 schema | 结构化 record/edge/block，可有 metadataSchema、profile 严格 schema（AWS/Vertex/Letta/LangMem） | 自由文本两段：`notes="版式：<枚举>"` + `sample=明细行文本`；无 schema |
| 3 来源证据 | Vertex revision 含 `extracted_memories` 中间产物；OpenAI 有 Sources；Zep 有有效期；Letta 有 git | **有**：`memory_id`/`source_kind`/`source_ref`(receipt_id)/`created_at`，向量 metadata 同步携带 |
| 4 整合（consolidation） | Extract + Consolidate 两段（AWS）；Vertex 官方措辞"先检查是否重复或矛盾再合并"；mem0 LLM 四选一 | **无**：每次 approve 追加一行两向量，无摘要、无合并、无跨单据归纳 |
| 5 冲突处理 | LLM 显式判矛盾，旧事实**失效不删除**（Zep bi-temporal `invalid_at`；Vertex revision 可回滚） | **无**：矛盾记忆并存，靠整供应商衰减 |
| 6 检索 | 语义召回（全员）；混合召回 cosine+BM25+图（Zep、mem0）；rerank（Zep、mem0） | 字面 char-ngram 哈希向量（非语义）+ 供应商名硬门；无 rerank、无混合检索 |
| 7 排序与预算 | 相关性 + 重要性/频率/时间衰减（LangMem 明示"relevance = similarity + importance + strength"）；显式 token 预算（C5） | 只有 rowid 倒序；预算按**字符数**近似 |
| 8 遗忘与淘汰 | TTL（AWS 事件 3–365 天必填；Vertex revision 默认 365 天；mem0 `expiration_date` 且**过期默认不召回**）；显式删除；失效而非删除 | 只有单向衰减；**无 TTL、无容量上限、无删除、归档不可逆** |
| 9 隔离与强制校验 | 多维 scope（mem0 `user_id`/`agent_id`/`run_id`/`app_id`；AWS namespace 模板）**+ IAM 条件键强制**读写路径隔离；Vertex scope 精确匹配 | 单一 `tenant_id`；写侧分 collection、**读侧落默认租户**（H1）；无强制校验；集合名映射非单射 |

### 2.2 行业最小集逐条对表（编号前缀 `必` = 大厂级最小集必需项）

> **【2026-09-23 提示】** 下表「本项目」与「判定」两列是 **2026-09-22 快照**。本轮**未逐行重写**这张行业矩阵（它标的是"离标准有多远"，重写属独立工作量），但下列条目**判定已变**，读表时请对照 §4 台账：**A2/A3（抽取）** → 模块已建（`memory_extract.py`，未接生成侧）；**B1/B2/B6（整合/冲突）** → 模块已建（`memory_consolidate.py`，含 ADD/UPDATE/DELETE/NOOP，未接生成侧）；**C1（语义召回）** → embedding provider 已可真切（`embeddings.py`），但**混合召回/rerank 仍无**；**D1（TTL）** → **已有**（`valid_to` + `memory_ttl_days`）；**D3（按主体显式删除）** → **已有**（`DELETE /api/memory/{id}` 与按供应商）；**D4（级联删向量）** → **已有**；**D6/D7（审计/版本）** → 审计已有（`written_by`），版本回滚仍无；**D5（查看/编辑界面）** → **仍未交付**；**D9/E3（隔离强制校验）** → 读侧租户口径已修但仍是"靠调用方传参"，**无机制强制**；**E2（信任元数据）** → `trust_level` 列已建、PII 最小化未做。

判定口径：**"有/没有"必须能指出实现位置**。

**A. 写入与抽取**

| 编号 | 能力项 | 行业 | 本项目 | 判定 |
|---|---|---|---|---|
| 必A1 | 是否区分"同步 hot path"与"后台异步"两条路径？ | 多数具备 | 只有同步（approve 请求内写完 DB+向量） | 缺失 |
| 必A2 | 是否有独立于原始数据的**抽取**步骤？ | 普遍具备 | 无（直接拼明细文本） | 缺失 |
| 必A3 | 抽取是否有"什么值得记"白名单（topic/strategy）？ | AWS strategy / Vertex topic | 无（approve 即全量落） | 缺失 |
| 必A4 | 记忆原子单位是否有 schema？ | 普遍具备 | 无（自由文本） | 缺失 |
| 必A5 | 是否记录每条记忆来源？ | 部分（Vertex/OpenAI/Zep/Letta） | **有**（`source_kind` + `source_ref` + `memory_id`） | **达标** |
| A6 | 是否支持"仅直传、禁 LLM 推断"的确定性字段？ | AWS `STRICTLY_CONSISTENT` | 不适用（本就没有 LLM 抽取） | n/a |
| 必A7 | 写入前是否存在**人审/闸门**？ | 行业中基本没有 | **有**（`pending_memory` + 审批端点 + 界面） | **超配** |
| A8 | 抽取/整合的 prompt 与模型可否覆盖？ | 普遍具备 | 无抽取环节可覆盖；`ai_registry/prompts/memory` 的 prompt 零调用 | 缺失（无落点） |

**B. 整合与冲突**

| 编号 | 能力项 | 行业 | 本项目 | 判定 |
|---|---|---|---|---|
| 必B1 | 是否有自动整合（增/改/删/忽略）？ | 普遍具备 | 无（只有"完全相等才去重"的追加） | 缺失 |
| 必B2 | 冲突是否由 LLM 显式判定？ | 普遍具备 | 无 | 缺失 |
| 必B3 | 旧事实被推翻时是否保留历史？ | Vertex revision / Zep 失效 | 无（只有"追加载新行"，旧行留在库里但无失效语义） | 部分（形式上有历史，无失效标记） |
| B4 | 可配置"整合时比较多少历史版本"？ | Vertex `revisions_per_candidate_count` | 无 | 缺失 |
| B5 | 可限制"哪些记忆有资格参与整合"？ | AWS namespace / Vertex `REQUIRE_EXACT_MATCH` | 无（本就没有整合） | 缺失 |
| 必B6 | 整合是否可观测（失败可重跑 / 看中间产物）？ | AWS CloudWatch + Redrive / Vertex revision | 无（写入无中间产物、无重跑） | 缺失 |

**C. 检索与上下文预算**

| 编号 | 能力项 | 行业 | 本项目 | 判定 |
|---|---|---|---|---|
| 必C1 | 是否支持语义（向量）召回？ | 普遍具备 | 有代码路径但 **live 上 0 产出**（H2）；且嵌入是 char-ngram 非语义 | 形式有、实际无 |
| C2 | 是否有混合召回？ | Zep / mem0 | 无 | 缺失 |
| C3 | 是否有 rerank？ | Zep / mem0 | 无 | 缺失 |
| C4 | 是否有重要性/频率/时间衰减加权？ | LangMem / Zep | 无（排序只用 rowid；`hit_count` 零消费） | 缺失 |
| 必C5 | 是否有显式 token 预算控制？ | 普遍具备 | **有**（`MemoryBudget` 800/200/6，管理台可配），但口径是字符数 | **部分达标** |
| 必C6 | 检索是否强制按 scope 收窄？ | 普遍具备 + IAM/ABAC 强制 | 名义上有（按 vendor + tenant），但**读路径不传租户**（H1）且相关性门有短名泄漏（L1） | **有缺陷** |

**D. 遗忘与治理**

| 编号 | 能力项 | 行业 | 本项目 | 判定 |
|---|---|---|---|---|
| 必D1 | 是否有 TTL / 过期机制？ | AWS 3–365 天必填 / Vertex revision 365 天 / mem0 `expiration_date` | **无** | 缺失 |
| 必D2 | 过期/失效项是否默认不召回？ | mem0 `show_expired` / Zep 失效 | n/a（无过期概念）；归档项确实默认不召回（`status='active'` 过滤） | 部分 |
| 必D3 | 是否支持按主体显式删除（含删全部）？ | 全员具备 | **无** | 缺失 |
| D4 | 删除是否级联到派生物（向量/摘要/图）？ | OpenAI 明示需逐源删；Zep 支持 | n/a（无删除能力） | 缺失 |
| 必D5 | 是否有记忆查看/编辑界面？ | 多数具备（OpenAI 最强；Vertex revisions；mem0 Dashboard） | **已生效记忆无任何查看/编辑界面**；待确认队列有界面但入口挂在评测页（1.6） | 缺失（队列除外） |
| 必D6 | 是否有审计日志（谁改了哪条记忆）？ | AWS/Vertex/Zep | 部分：pending 的 approve/reject 写系统审计（`api_memory.py:76-83`）；记忆本体的变更无审计（因无变更能力） | **部分** |
| D7 | 是否有版本历史/回滚？ | Vertex 不可变 revision + rollback；Letta git | 无（`version` 列恒为 1 且零消费） | 缺失 |
| D8 | 是否有访问计数/使用指标？ | AWS 用量指标 | 有 `hit_count`/`last_hit_at` 但**零消费方**；有 `rag_hit` 事件计数 | 部分 |
| 必D9 | 隔离是否有强制校验（非靠调用方自觉）？ | AWS IAM 条件键 / Zep ABAC / Vertex IAM | 无（靠调用方传参——而读侧恰好没传，H1） | **缺失（已被 H1 证实）** |

**E. 安全与合规专项**

| 编号 | 能力项 | 行业 | 本项目 | 判定 |
|---|---|---|---|---|
| 必E1 | 写入路径是否有策略筛查（防投毒，ASI06 建议 1）？ | 行业普遍缺失，OWASP 已出专门项目 | 无写入时筛查（来源是 OCR 结果 + 人审，天然压制外部注入，但无显式筛查） | 缺失（风险被前置环节削弱） |
| 必E2 | 每条记忆是否带来源与信任元数据（ASI06 建议 4）？ | Vertex/Zep/Letta/OpenAI 有 | **部分有**：有 `source_kind`/`source_ref`/`memory_id`，但**无信任等级、无写入者身份** | 部分 |
| 必E3 | 是否防止跨主体 scope 提升？ | AWS/Vertex/Zep 有机制 | 无强制校验；H1 的反向方向**已是真实的跨 scope 注入**（default 桶记忆注入任意租户） | **缺失** |
| 必E4 | PII 写入前是否最小化，且不把责任外包给模型？ | 两家官方明确"不能只依赖我们过滤" | 无（记忆里存完整品名+单价明细；无 PII 识别、无脱敏） | 缺失 |
| 必E5 | 是否可响应删除权/查阅权？ | 产品级仅 OpenAI 有完整闭环 | **无**（无删除、无按主体导出）—— 而 PDPO s.26 属罪行 | **缺失（合规风险）** |
| 必E6 | 是否记录记忆变更审计？ | AWS/Vertex/Zep | 部分（同 D6） | 部分 |
| 必E7 | 记忆是否加密（静态）+ 可 BYOK？ | AWS KMS / Vertex CMEK / Zep VPC 内 | 依赖部署（本地 SQLite + Chroma 无静态加密、无 KMS） | 缺失 |

### 2.3 定位差异：哪些"gap"不该当成缺陷

大厂方案解决的是「跨会话对话记忆」，本项目解决的是「单据识别的领域先验注入」，**两者不是同一问题**。以下几点是本项目的**合理取舍或超配**，不应计为 gap：

- **只认正向信号（approve 才写）+ 写入前人工闸（A7）**：行业最小集里几乎无人具备。这是本项目在"识别正确性"这种高风险场景下的有意保守设计，代价是覆盖率与时效，收益是**天然压制了 ASI06 最大的攻击面（外部文本注入记忆）**。
- **注入前一律 XML 数据沙箱 + system 声明**：比多数方案的"直接拼接进 prompt"更严谨；配合图面侧 canary 另成一套。
- **冷启动不携带他商记忆**的设计意图（`rag.py:284`）本身是对的 —— 只是读侧租户分叉（H1）把它在默认租户读路径上违反了。

反过来，行业标准里最值得抄的三条是：**TLL/过期不召回（D1/D2）、LLM 参与的冲突判定（B1/B2）、记忆变更的审计与可回滚（D6/D7）**，以及合规侧的**按主体删除闭环（E5）**。

---

## 3. Gap 清单

> 每条给出：行业标准要求 → 本项目现状（附证据）→ 差距性质 → 影响 → 修复方向。P0 = 会导致功能/安全实际失效；P1 = 治理与效果缺口；P2 = 工程整洁与叙事口径。

### P0-1　读路径租户口径分叉（安全 + 功能双重失效；对应 D9 / E3 / C6）　【状态：已交付 · 2026-09-22】

> 本节其余内容为 2026-09-22 快照（准确的当时证据）。**落地证据见 §4.1 H1**：`tenant_id` 已改必填 keyword-only，两处生产读点均已传参，锁定测试在 `demo/tests/test_memory_tenant_retrieval.py`（注意在 `demo/tests/`，不在 `tests/`）。

- **标准**：多租户记忆的读写必须使用同一作用域键，且应由机制强制（AWS 用 IAM 条件键、Zep 用 ABAC），而不是靠调用方记得传参。
- **现状**：写入传真实 `tenant_id`（`api_receipts.py:1156`），两个生产读点都不传（`extract_chain.py:503`、`supervisor.py:853`），`retrieve_context` 默认 `tenant_id="default"`（`rag.py:275`）。
- **实测**：写入 `tenant=t2` 后，`retrieve_context(v)` 返回 `''`，`retrieve_context(v, tenant_id="t2")` 才返回内容（隔离库，`DB_PATH`/`RAG_DIR` 指向临时目录）。
- **危害（两个方向分开定性，避免夸大与缩小）**：
  - **反向（已是现实）**：default 桶里已有 9 条真实供应商记忆，读路径恒取 default → 它们会被注入给**任意租户**的识别请求。含"全局品类库"这类也被写进 default 的共享数据。
  - **正向（latent，一上多租户立刻踩中）**：非默认租户 approve 写入 `tenant=t2` 的记忆，生产读路径永远取不到 → 该租户的记忆功能整体静默失效。
  - **隔离承诺零实证**：两个向量库里 `tenant_*_vendor_memory` collection 数为 0，说明 `rag.py:120` 自述的"每个 tenant_id 物理硬隔离（FR-9）"**在 live 上从未被执行过一次**。叙事侧应写"隔离承诺零实证"，不要写"已造成数据丢失"。
- **为何至今没被发现**：`auth.py` 三个预置账号 tenant 全是 `default`，且 `AUTH_ENABLED` 缺省 0 → demo 里读写两侧恰好都是 default。**零测试覆盖**（所有测试都显式传且读写同租户）。
- **修复**：读点透传 `tenant_id`（`start_recognition_job` 已有该形参，`run_pipeline` 需新增）；并把 `retrieve_context` 的 `tenant_id` 默认值改为**必填**，让"忘记传"在开发期炸出来；补一条锁定测试（写 t2 → 生产调用形态能读到；default 的记忆在 t2 视角读不到）。

### P0-2　向量层与 DB 脱钩、fuzzy 通道结构性死亡（对应 C1）　【状态：已交付（原形态）· 2026-09-22；残留 R7/R8 见 §4.5】

> 本节其余内容为 2026-09-22 快照。**落地证据见 §4.1 H2**：D5 清退 179+338 条、`_vector_write_allowed` 闸门、巡检工具 `memory_consistency.py`；本次实测 `consistent=true`。

- **标准**：记忆的关系视图与向量视图必须一致，写入/删除需成对（AgentCore 提供 `GetMemoryRecord`/`ListMemoryRecords`/`RetrieveMemoryRecords` 三种一致访问）。
- **现状**：live 上 DB 9 行 / 向量 179 条 / **memory_id 交集 0**；155 条向量连 `memory_id` 元数据都没有；`allowed_mids` 门（`rag.py:341`）把它们全部挡掉 → **fuzzy 可注入文档数 = 0**。全仓无任何对账/补偿代码。
- **成因链**：① DB 写失败仍写向量（`rag.py:165-168`，无日志）→ 制造孤儿；② 09-20 数据清理删了 DB 行但没删向量；③ conftest 不隔离 `RAG_DIR`，测试写入 live 向量库（见 P0-3）。
- **影响**：语义兜底能力实际为 0（冷启动/名字微变场景无先验）；179 条向量是"只写不读"的存储；且**未来任何依赖 fuzzy 的修复都会踩在污染语料上**。
- **修复**：先人工判定存量向量的清退或回填（涉及删数据，需拍板）；再给向量层把 `memory_id` 设为必需元数据（无 mid 不写）；补一个对账入口（DB 缺向量告警 / 向量缺 DB 行清理）。

### P0-3　测试会写 live 向量库（测试卫生 + 数据污染）　【状态：已交付 · 2026-09-22】

> 本节其余内容为 2026-09-22 快照。**落地证据见 §4.1 H3**：两处 conftest 各 4 处 `RAG_DIR`。

- **标准**：测试必须完全隔离外部状态（本项目 DB 与图片目录已做到 autouse 兜底，是既有正确实践）。
- **现状**：两处 conftest **零 `RAG_DIR` 处理**（`grep -c RAG_DIR tests/conftest.py demo/conftest.py` → 0 / 0），而 `rag.PERSIST_DIR` 是 CWD 相对路径（`rag.py:24`）→ 从 `demo/` 跑测试，DB 进临时库、**向量进 live 向量库**。
- **直接证据**：live 向量库里 24 条带 `memory_id` 的孤儿，vendor **全是 `测试供应商`**（`demo/tests/` 夹具名），且这些测试文件均未设 `RAG_DIR`；仓库根另有 `.rag_chroma`(338 条) 与 `demo/tests/.rag_chroma`(0 条) 两处残留。
- **影响**：live 记忆库被测试数据污染；清理动作（09-20）只清了 DB 没清向量 → 直接喂出 P0-2。
- **修复**：conftest 加 `_redirect_rag_dir` autouse 夹具（与既有 `_redirect_image_output_dirs` 并列），把 `RAG_DIR` 与 `rag.PERSIST_DIR` 指向 `tmp_path` 并清 `rag._vs`/`rag._vs_tenant` 缓存；三处残留目录的清理需拍板。

### P1-1　记忆进不了主路径（能力已实现但未接线到主路径）　【状态：部分修（入口已交付，首轮注入未改造）· 2026-09-23】

> 本节其余内容为 2026-09-22 快照。**现状见 §4.1 H4**：`vendor_hint` 三条来源已补齐（前端手选 / 预判 / 重试派生），~~但 `extract_chain.py:507` 的首轮检索门与 `supervisor.py:1010` 的首轮 `break` 未改造~~ → **2026-09-25 注：首轮注入已由 W-1 于 09-24 落地改造**（`supervisor.py:796-803` 首轮注入分支，开关 `memory_first_round_inject_enabled` 缺省关；`extract_chain.py:507` 的 `if vendor_hint:` 门保留，首轮检索经 supervisor 预判传入的 vendor_hint 走同门）。**这是当前卡住命中率指标的第一顺位原因**（§7 待办 W-1）。

- **标准**：记忆的价值来自"在决策发生前提供上下文"（AgentCore 为每次交互先检索再生成）。
- **现状**：前端从不发 `vendor_hint`（零命中）→ hint 通道不可达；无 hint 时首轮不检索（`extract_chain.py:502`）；首轮结束后的补检索结果只落库（`supervisor.py:850-867`）；首轮通过即 `break`。契约/算术类失败走不重试的 fast 分支。**记忆实际只作用于"非快速门禁打回的第 2+ 轮"**。
- **量化**：701 张单据中 12 张（1.7%）有非空 `rag_context_json`。
- **影响**：飞轮的核心叙事（"录入越多越懂这家供应商"）在工程上不成立；改进只发生在原本就要重试的少数单据上。
- **修复**：产品决策项 —— ① 把"先猜供应商再检索"提到首轮之前（需解决冷启动与猜错供应商的风险）；② 或接受现状并在叙事中如实降级。若保留 hint 通道，需补前端输入；同时修 `effective_hint = vendor_ctx or vendor_hint`（`extract_chain.py:518`）的二选一语义 —— 记忆与用户 hint 应当**并存**。

### P1-2　记忆生命周期治理缺失（对应 D1/D2/D3/D5，并触及合规）　【状态：大部分已交付（删除/恢复/supersede/TTL/对账）；容量上限与查看界面未交付】

> 本节其余内容为 2026-09-22 快照。**现状见 §4.2 M2 与 §7 待办 W-4/W-5**：PDPO s.26 的落地路径（按主体删除 + 级联删向量）**已具备**；缺的是容量上限与「已生效记忆的查看/编辑界面」（行业最小集 D5）。

- **标准**：记忆必须可删除、可编辑、有容量边界、有保留期（AWS 事件过期 3–365 天必填、Vertex revision 默认 365 天、mem0 `expiration_date` 且过期默认不召回）。
- **现状**：三层删除能力全无；无 TTL、无容量上限；归档不可逆且 `bump` 救不回；`hit_count`/`last_hit_at`/`version` 零消费方。
- **合规面（已核实官方原文）**：香港 PDPO **第 26 条**要求"采取一切切实可行步骤删除已不再需要的个人数据"，**违反属罪行，最高罚 HK$10,000**；DPP2 要求保存不超过必要时间、DPP6 赋予查阅与更正权。本项目记忆里存的是含品名与单价的明细（可能含员工/联系人等个人数据），而代码里**没有任何"按供应商删除全部记忆"的能力** → 收到删除请求时无法响应；也没有"按主体导出全部记忆"的查阅能力。
- **其他影响**：一条错误记忆只能靠"整供应商被覆写 5 次后连带归档"来消失；而扣分触发面包含"价格合理变动/补填供应商名"，**高频供应商更可能被误归档全部历史记忆且不可恢复**。
- **修复**：`DELETE /api/memory/{memory_id}` 与 `DELETE /api/memory/vendor/{vendor}`（先删 DB 行，再 `where={"memory_id": mid}` 或按 vendor 删向量，正好用上现有元数据）+ 归档恢复通道 + 每 (vendor, tenant) 行数上限 + 保留期策略；把扣分触发面收窄为"AI 填了值且用户改成了别的值"（可由 `ai_prefill_json` 对比得出）。

### P1-3　衰减设计只有负面证据（对应 C4 / D8）　【状态：部分修 · 2026-09-23】

> 本节其余内容为 2026-09-22 快照。**现状见 §4.2 M3/M4**：强化已不再恒等（起始分 0.7）、归档已可逆；但扣分触发面未收窄（仍不区分 AI 错与业务合理变更），排序仍未纳入治理信号。

- **标准**：强化与惩罚应双向可生效，且应基于"记忆是否真的帮到了结果"（LangMem 明确 `relevance = similarity + importance + strength`）。
- **现状**：起始 `decay_score=1.0` + 封顶 `min(1.0, cur+0.05)` → 强化恒等（实测）；`hit_count` 记录的是**注入次数**而非**有用次数**。
- **影响**：系统只能通过"用户改了什么"怀疑记忆，无法通过"命中后结果正确"确认记忆。统计上偏保守，但好记忆与坏记忆被同一把尺子惩罚。
- **修复**：新行起始分留出上升空间（如 0.7）或按命中次数抬高上限；把"命中后该单被 approve 且未修改"作为正向信号（本项目独有、比大厂对话场景更容易拿到的标签）。

### P1-4　写入无筛查、无信任元数据、无 PII 最小化（对应 E1/E2/E4）　【状态：部分修 · 2026-09-23】

> 本节其余内容为 2026-09-22 快照。**已交付**：`written_by` 审计链接通，且 live 10 条 active 记忆全部落 `owner@demo.hk`（计划 §10「P4a 落地」行）；`trust_level` 列已建并参与冲突整合的低可信守卫。**未交付**：写入侧 PII 扫描/脱敏。

- **标准**：OWASP ASI06 建议 1（写入时策略筛查）与建议 4（每条记忆带来源与信任元数据）；Vertex 与 Anthropic 官方都明确"不能只依赖模型过滤敏感信息"。
- **现状**：写入路径无任何策略筛查；记忆的元数据有 `source_kind`/`source_ref`，但**无信任等级、无写入者身份**；无 PII 识别与脱敏。
- **缓解事实**：记忆来源是 OCR 结果 + **人工 approve**，天然压制了"外部文本投毒"这一最大攻击面（这也是本项目最值得保留的设计）。但"人工 approve 不等于内容正确"—— 已批准的明细里同样可能含员工姓名、电话等个人数据，且会被长期保存、反复注入。
- **修复**：写入前加最小化的 PII 扫描（命中即脱敏或拒收并告警）；记忆条目补 `written_by` / `trust_level` 字段；把"记忆里的单元格级文本"纳入既有 PDPO 红线清单（`api_receipts.py:495` 已有 `vendor_hint` 只落布尔位的先例，可复用同一思路）。

### P2-1　`全局品类库` 死写入　【状态：已交付 · 2026-09-22（选「删除」，接替者 `vendor_sku_alias`）】

> 落地证据见 §4.2 M8。

- 标准：写入的记忆必须在某个检索路径上可达。
- 现状：固定 vendor 名 `全局品类库` + `_vendor_related` 供应商名门 → 永不召回；且未传 `tenant_id`。
- 修复：给它一个真正的全局通道（检索时额外读该 vendor 并注入），或删掉这次写入（连同 `sync_vendor_memory` 形参）。

### P2-2　检索质量基础设施薄弱（对应 C1/C2/C3）　【状态：部分修 · 2026-09-23】

> 本节其余内容为 2026-09-22 快照。**已交付**：`RAG_EMBEDDING_PROVIDER` 已是真开关（三档 hash / siliconflow / dashscope，`embeddings.py` 唯一实现），集合名带 identity 后缀（§4.2 M7）。**未交付**：混合召回（C2）、rerank（C3）、排序纳入治理信号、`_vendor_related` 短名门补最小长度（§4.3 L1）。

- 嵌入是 char-ngram 哈希（非语义），docstring 承诺的 `RAG_EMBEDDING_PROVIDER` 开关**不存在**；无 rerank、无混合检索、无时间/频次加权；`_vendor_related` 短名子串门无最小长度约束（实测 1 字供应商名可放行他商记忆）。
- 修复：实现或删除 `RAG_EMBEDDING_PROVIDER` 承诺；相关性门补最小长度；排序纳入 `hit_count`/`decay_score` 或明确声明"最新优先"是设计意图。

### P2-3　静默失败面（≥11 处）　【状态：部分修 · 2026-09-23（读路径已可观测）】

> 本节其余内容为 2026-09-22 快照。**现状见 §4.2 M6**：读路径 6 个阶段已留痕 + `get_retrieve_failure_counts()`；写 / approve 侧部分分支仍未纳入计数。

- 其中 3 处会导致「记忆整体静默消失且零日志」：`rag.py:321-322`（精确通道整段 `except: pass`）、`rag.py:345-346`（fuzzy 整段）、`supervisor.py:866-867`（检索失败静默置空）。`extract_chain.py:506-513` 同类。
- 另有 2 处会制造孤儿向量（`rag.py:165-168`、`422-423`）而无日志。
- 修复：这些点至少要 `logger.warning` + 结构化计数；DB 写失败时必须**跳过**向量写入。

### P2-4　整合与抽取环节整体缺失（对应 A2/A3/B1/B2/B6）　【状态：设计变更 —— 由「明确不做」改为「阶段四路线图」】

> **处置已变**：D1 决策是「不改写叙事，而是补齐 gap」，D8 把 LLM 抽取 / 冲突整合 / 语义检索纳入阶段四（计划 §8）。至 2026-09-23，纯逻辑模块已建（`memory_facts.py` / `memory_extract.py` / `memory_consolidate.py` / `embeddings.py`），抽取触发两处已接（`api_receipts.py:1185` 单点实现 `_extract_and_enqueue_memory_facts`，被 `:1581` `save_edited` 与 `:1652` `approve_receipt` 调用）；**DB 落库列已补齐，但生成侧注入尚未接通**（属 P2b 波）。

- 这是与行业最小集差距最"结构性"的一组，但**不建议现在补**：当前记忆的主路径都没接通（P1-1）、向量层还是坏的（P0-2），此时引入 LLM 抽取+冲突判定只会放大不可观测的风险。
- 顺序建议：先把 P0/P1 做完，再评估"是否有必要引入 LLM 抽取与整合"—— 若引入，应同时引入 B6（可观测/可重跑）与 revision（B3/D7）。

---

## 4. 明显问题清单（缺陷）· 台账版（2026-09-23）

> 与第 3 节的标准对照不同，本节是"不管大厂怎么做，这里就是错的/坏的"。均带可复算证据。
> **状态列取值**：`已修` / `部分修` / `未修` / `设计变更`。每条的「证据（2026-09-23 复核）」均为本轮修订者独立读码或实测所得；`file:line` 已按当前代码重新校正（原快照里的行号多已漂移）。

### 4.1 four high

| 编号 | 严重度 | 2026-09-22 结论 | **状态** | 证据（2026-09-23 复核） |
|---|---|---|---|---|
| H1 | high | 读路径不传 tenant_id → default 桶记忆注入任意租户 + 非默认租户记忆取不到；隔离 collection 从未创建 | **已修** | `rag.py:976` 签名改为 `retrieve_context(vendor, top_k=3, *, tenant_id: str, budget=None)`（**必填、keyword-only、无默认值**，docstring 明写"刻意不给默认值"）；`extract_chain.py:517` 与 `supervisor.py:936` 均传 `tenant_id=tenant_id`；锁定测试 `demo/tests/test_memory_tenant_retrieval.py`（**注意在 `demo/tests/`，不在 `tests/`**）。端到端探针（计划 §10「P4a 落地」行）：同租户检索返回内容、**异租户返回空串** |
| H2 | high | live 向量层与 DB memory_id 交集为 0，fuzzy 通道 0 产出 | **已修（原形态）** | D5 清退 179+338 条；写入侧 `_vector_write_allowed`（`rag.py:279`）强制 memory_id/tenant_id 齐备才写向量，DB 写失败即跳过（`rag.py:267-276`）。本次实测巡检 `consistent=true`：**15 向量 / 10 distinct mid / 无 mid 0 / 孤儿 0 / 缺向量 0**，集合名 `vendor_memory_hash_char_ngram_hash_256`（identity 后缀生效）。**残留见 §7 待办 W-6/W-7**（R7 `PERSIST_DIR` 仍 CWD 相对、R8 幂等复用跳过向量写入） |
| H3 | high | conftest 不隔离 `RAG_DIR`，测试写 live 向量库 | **已修** | `tests/conftest.py` 与 `demo/conftest.py` **各 4 处 `RAG_DIR`**（原为 0/0）；`demo/conftest.py:49` 还把 `RAG_EMBEDDING_PROVIDER` 钉成 `hash` 避免用例真发 HTTP |
| H4 | high | 记忆几乎进不了 prompt（只在非快速重试轮） | **部分修** | **入口已补齐**：前端单张上传发送 `vendor_hint`（`main.js:2153-2155`，`localStorage` 记上次选择）；后端 `_vendor_hint_from_row`（`api_receipts.py:435`）供重试（`:1720`）/换图（`:1792`）派生；`vendor_precheck.predict_vendor`（`:538`，开关 `vendor_precheck_enabled` 缺省关）。~~未改造的是"首轮注入"本身~~ → **2026-09-25 注：W-1 已落地——`supervisor.py:796-803` 首轮注入分支（开关 `memory_first_round_inject_enabled` 缺省关），首轮检索经预判供应商传入既有 hint 通道；详见 §4.6 待办表 W-1 注记**。旧口径命中率仍 1.71% |

### 4.2 mid

| 编号 | 严重度 | 2026-09-22 结论 | **状态** | 证据（2026-09-23 复核） |
|---|---|---|---|---|
| M1 | mid | DB 写失败 `except: pass` 且继续写向量 → 孤儿向量生成器 | **已修** | `rag.py:267-276` DB 落库失败 → `logger.error("[rag] vendor memory persist failed, vector write skipped (orphan vector avoided) ...")` 且 `_persist_memory_row` 返回 `None` → 调用方直接 `return vendor`（不构造 docs）；approve 侧 `rag.py:1302` 同款留痕 |
| M2 | mid | 无删除/编辑、无 TTL、无上限、无对账；`get_vendor_memory` 零调用 | **部分修** | **已交付**：删除 `DELETE /api/memory/{id}`（`api_memory.py:268`）+ 按供应商（`:224`）+ `db.delete_vendor_memory`（`db.py:3914`）；恢复 `/api/memory/{id}/revive`（`api_memory.py:298`）+ `db.revive_vendor_memory`（`db.py:4621`）；TTL `valid_to`（`db.py:286/3511`）+ settings `memory_ttl_days`；DB↔向量对账 `memory_consistency.py` + `GET /api/admin/memory-consistency`（`api_admin.py:923`）；**已生效记忆查看面已交付**（`GET /api/admin/vendor-memory`，`api_admin.py:1148` + `loadVendorMemoryGovernance()`，`main.js:13272`）。`list_vendor_memory` 已有生产消费方（`rag.py:1097`、`api_admin.py:1201`）。**未交付**：每 (vendor, tenant) 容量上限（全仓无）；记忆**编辑**（查看/删除已有，编辑仍无） |
| M3 | mid | 衰减扣该供应商全部 active 行，5 次覆写归档全部且不可逆；强化恒等 | **部分修** | **已修**：强化不再恒等 —— 新行起始分 `memory_initial_decay_score()`（`db.py:4469`，写入侧 `:3480` 消费，缺省 **0.7**）；归档可逆（`revive_vendor_memory`）。**未修**：① 扣分触发面**未收窄** —— `api_receipts.py:1400-1427` 仍按 old/new 全量 diff 扣分，**未对比 `ai_prefill_json`**（该处注释自称"店员真的改了 AI 预填值"，但代码里没有这个对比，属注释与代码不一致）；② 扣分粒度仍是该供应商**全部 active 行**（`db.py:4426-4430` 无 memory_id 过滤） |
| M4 | mid | `hit_count`/`last_hit_at`/`version` 零消费方 | **已修** | 三列均已真消费：写入侧 `db.bump_vendor_memory_hit`（`rag.py:1178` → `db.py:3983`，自增 hit_count + 刷新 last_hit_at + decay bonus）；**排序侧已消费** —— `memory_recall.rerank` 把 `decay_score`（权重 0.3）、`hit_count`（log 饱和，0.05）、`last_hit_at` 新鲜度（0.05）并入排序分（`rag.py:1113`、`memory_recall.py:437-456`）。`version` 仍恒 1（**有意设计**：追加式模型永不就地改内容，非缺陷） |
| M5 | mid | 排序只用 rowid；`top_k` 对精确通道无效 | **已修（P7·Y2）** | 检索排序已改由 `memory_recall.rerank` 决定（`rag.py:1113`）：三腿（精确/关键词 BM25/向量）合并去重后按「腿基础分 + 相关度 + 精确匹配 + 治理信号」重排；`db.list_vendor_memory` 的 `rowid DESC`（`db.py:3818`）降级为**同分破平的输入序**。`top_k` 仍只作用于向量腿（最终截断口径是预算 `max_items`，属设计） |
| M6 | mid | ≥11 处静默失败，3 处会让记忆静默消失且零日志 | **部分修** | **读路径已可观测且已接线**：`get_retrieve_failure_counts()`（`rag.py:65`）+ `_note_retrieve_failure`（`:76`），现覆盖精确/关键词/fuzzy/预算/阈值/衰减解析/rerank 权重/命中记账等阶段（`:928` 关键词、`:972` fuzzy、`:1106` 精确等）；**残债 R10 已闭** —— `GET /api/admin/memory-metrics` 已并入 `retrieve_failures`（`api_admin.py:1070/1126`）。**未修**：approve/写路径仍有多处仅 `logger.warning` 的分支（未纳入计数） |
| M7 | mid | docstring 宣称的 `RAG_EMBEDDING_PROVIDER` 开关不存在 | **已修** | 新模块 `demo/app/services/embeddings.py`（唯一实现）；`rag.py:153` `_embedding_provider()` 改为 `return embeddings.get_embeddings()`；集合名拼 `identity.collection_suffix()`（`rag.py:190`）。`RAG_EMBEDDING_PROVIDER` 现在**真被读取**（`embeddings.py:53`），`.env.example:110` 有登记 |
| M8 | mid | `全局品类库` 死写入 + 未传 tenant | **已修** | 该写入已删除，`api_inventory.py:195-200` 仅余解释"为什么不写"的注释；接替者 = `vendor_sku_alias` 别名表 + 合并钩子；判据用例 `demo/tests/test_merge_skus_no_dead_memory_write.py` |
| M9 | mid | 空 approve（明细为空）仍写 2 条向量并注入无信息文本 | **未修** | `rag.py:498-502` 仍无条件构造 `sample` doc（`page_content=f"供应商 {vendor} 的已确认进货明细：\n{items_text}"`）→ `items_text` 为空时该字符串仍非空，仍会写入并可能注入（调用点 `api_receipts.py:1557`） |
| M10 | mid | **实体 RAG 空槽**：`entity_candidates` 有列/读写/透传/前端槽，但全仓无生产者 | **未修** | 唯一赋值仍是 `[]`（`receipt_utils.py:688`、`scripts/import_golden.py:149`）；DB 列 `db.py:173`、透传 `api_receipts.py:1423`、读 `db.py:2075`、前端槽 `main.js:5452` 均在，但**无生产者** |
| M11 | mid | 人工闸入口可发现性缺失 | **已修** | `main.js:5975` 的 toast 现带 `action: { label: '查看待确认记忆', onClick: openPendingMemoryQueue }`；`index.html:2379` 新增「待确认记忆 N 条」角标（条数取自只读 `GET /api/memory/pending?status=pending`，0 条/403/网络失败静默隐藏） |

### 4.3 low

| 编号 | 严重度 | 2026-09-22 结论 | **状态** | 证据（2026-09-23 复核） |
|---|---|---|---|---|
| L1 | low | `_vendor_related` 短名子串门无最小长度（1 字可放行） | **未修** | `rag.py:575` 仍是 `if q in d or d in q: return True`，无长度约束（核心词根分支 `:581-582` 才有 `len>=2`） |
| L2 | low | 集合名映射非单射（`a.b` ≡ `a_b`），超 64 字截断碰撞 | **部分修** | 新增 identity 后缀解决"维度混写"（`rag.py:170/190`）；但 tid→名 部分仍是 `re.sub(r"[^a-zA-Z0-9_-]","_",tid)[:64]`（`rag.py:196`），`a.b` 与 `a_b` 仍得同一名（latent，`auth.py` 禁 tenant 含点） |
| L3 | low | pending 队列无容量上限；rejected 行无"拒绝理由"字段 | **部分修** | `reject_reason` 列已建（`db.py:543` + ALTER `:948`）且**已有写入方** `_record_refused_candidate`（`api_receipts.py:1123`，写入 `:1153`）、`GET /api/memory/pending` 可读。**未修**：每租户 pending 容量上限（全仓无） |
| L4 | low | `ai_registry/prompts/memory` 的 `alias_extraction_accuracy=94.2` 等指标无 `reproduce_command`、无代码跑它 | **已修** | `ai_registry/prompts/memory/metadata.json:14-15` 三项已置 `null` 并写 `claim_unsupported: true` + `claim_unsupported_note`（指向本文 L4） |

### 4.4 三条会被误认为"记忆能力"的事实（2026-09-23 复核后更新）

- MCP `chroma_vendor_memory` 是**诚实的 WIP**：只有 `list_tools` 声明、无 `call_tool`/无执行实现，`mcp_settings.json` 与 `version_registry.json` 都明确标 `active:false` / `未接入`。**不计缺陷**，但叙事上不能算作"已实现的 MCP 能力"。**（本段仍准确）**
- ~~`pending_memory` 在 live 库里是空表~~ → **本段已不准确**：live 现为 `pending_memory` 11 行（10 approved + 1 pending）→ 人工闸**已在真实批准路径上被行使过**（见计划 §10「里程碑：指标首次可判定」行，5→10 条 active 记忆全部 `written_by=owner@demo.hk`）。旧的"从未被真实触发"结论**已失效**。
- 记忆里已固化了**不再存在于任何单据的重复行**（`德利行 Tak Lee Hong` 的 sample 中 `ANIKAR A雞粉` 出现两次）→ **本段仍准确**（错误内容一旦写入即长期被当作"已确认明细"注入的机制未变；不过**现在已有删除手段**，`DELETE /api/memory/vendor/{vendor}` 可清理，见 M2）。

### 4.5 残债（2026-09-23 新发现，不在原快照内）

> 这两条是原快照之后、在推进过程中**新暴露**的，记在此处以免"修好旧账、留下新账"。权威出处：计划 §11.1。

| 编号 | 结论 | 证据（本轮复核） |
|---|---|---|
| R7 | **已修（P8a-R7）**：`rag.PERSIST_DIR` 改为消费 `services/rag_paths.resolve_rag_dir()`（**应用根基准，与 cwd 无关**），不再用 cwd 相对的 `os.environ.get("RAG_DIR", "./.rag_chroma")` | `rag.py:107` = `rag_paths.resolve_rag_dir()`；`rag_paths.py:47`（优先级 显式参数 > `RAG_DIR` > 缺省 `<demo>/.rag_chroma`）；巡检工具委托同一函数；`RAG_DIR` 显式覆盖仍优先（测试隔离依赖它） |
| R8 | **已修（P8a-R8）**：`ingest_memory` 幂等复用分支不再直接 `return` 跳过向量写入，改由 `_sync_memory_vectors(reusing=True)` 先只读查该 mid 是否在目标 collection，缺则用同一套 docs 补写并留痕（DB 有行 ⇒ 向量存在） | `rag.py:320-356`（`_sync_memory_vectors` 单点实现）；`rag.py:525`（`ingest_memory` 调用点 `reusing=not stored_new`）；`rag.py:1334`（`approve_pending_to_memory` 同口径） |

### 4.6 2026-09-24 复核（dev_gaplog 波）

> **性质**：交接文档 §5.3 纪律「报『还剩多少』前，把台账的『未修』逐条回代码核」的落地。本节对 §4.1–§4.5 与 §7.2 全部标 `未修`/`部分修` 的条目**回当前代码逐条复核**，凡已被后续波次覆盖的一律改状态并注明覆盖波次/符号。**只改状态与证据，未改 §2 行业矩阵正文**。
> **复核方法**：`Grep`/`Read` 实测当前代码，`file:line` 为本轮实测值（原快照行号多已漂移）。

**§4 台账逐条（只列本轮复核后有变化的条目；未列的 已修 项经复核仍成立）**

| 编号 | 文档原状态 | 复核后状态 | file:line 证据 | 一句话还差什么 |
|---|---|---|---|---|
| H4 | 部分修 | **部分修（不变）→ 2026-09-25 注：W-1 已落地（开关 `memory_first_round_inject_enabled` 缺省关），首轮注入已改造，见 §7.2 W-1 注记** | `extract_chain.py:507` `if vendor_hint:`（门保留，首轮由 supervisor 预判传入 vendor_hint）；`supervisor.py:1010` 首轮通过即 `break`（现位于成功收口处；首轮注入分支已落地 `supervisor.py:796-803`）；`effective_hint = vendor_ctx or vendor_hint` 已改并存（`extract_chain.py:532-537`）；入口已补：`main.js:2155` 发送 `vendor_hint` | 首轮注入本身未改造：无 hint 单据首轮仍完全不检索（**2026-09-25 注：开关打开后已可首轮检索；缺省关时行为不变**） |
| M2 | 部分修 | **部分修（收窄）** | 删除/恢复/TTL/对账/查看面均在（`api_memory.py:224/268/298`、`db.py:3914/4621`、`api_admin.py:1148`、`main.js:13272`） | 每 (vendor, tenant) 容量上限仍无；记忆**编辑**仍无 |
| M3 | 部分修 | **部分修（不变）→ 2026-09-25 注：①扣分触发面已由 W-8 落地收窄（`api_receipts.py:1065` `_ai_prefill_corrected_fields` + 触发 `:1493`，审计 §1 证实）；②粒度（全 active 行扣分）仍如旧** | 扣分触发面 `api_receipts.py:1400-1427` 仍 old/new 全量 diff（未比 `ai_prefill_json`）；粒度 `db.py:4426-4430` 仍 vendor+tenant+status 无 mid 过滤 | 扣分触发面未收窄 + 粒度未细化（见 W-8） |
| M4 | 部分修 | **已修（改判）** | `memory_recall.rerank` 已消费 `decay_score`/`hit_count`/`last_hit_at`（`rag.py:1113`、`memory_recall.py:437-456`）；`bump` 写入侧 `db.py:3983` | `version` 恒 1 属有意设计，非缺陷 |
| M5 | **未修** | **已修（改判 · 被 P7·Y2 覆盖）** | `rag.py:1113` 调 `memory_recall.rerank`；`memory_recall.py:459` 三腿混合召回 + 治理信号重排 | `db.py:3818` 的 rowid DESC 降级为同分破平输入序；`top_k` 仍只作用向量腿（预算 `max_items` 才是截断口径，属设计） |
| M6 | 部分修 | **部分修（收窄）** | `rag.py:65/76`；**R10 已闭**：`retrieve_failures` 已并入 `GET /api/admin/memory-metrics`（`api_admin.py:1070/1126`） | approve/写路径分支仍未纳入计数 |
| M9 | 未修 | **未修（不变）→ 2026-09-25 注：已由 W-11 落地（rag.py:510 空明细不写 sample doc），审计 A5 证实** | `rag.py:498-502` 仍无条件构造 `sample` doc；调用点 `api_receipts.py:1557` | 明细为空时仍写向量/注入无信息文本（见 W-11） |
| M10 | 未修 | **未修（不变）** | 唯一赋值仍 `[]`（`receipt_utils.py:688`、`scripts/import_golden.py:149`） | `entity_candidates` 无生产者（见 W-11） |
| L1 | 未修 | **未修（不变）→ 2026-09-25 注：已由 W-11 落地（rag.py:596-597 单字门 `len<2` 不放行），审计 B3 证实** | `rag.py:575` 仍无长度约束 | 短名子串门未补最小长度（见 W-11） |
| L2 | 部分修 | **部分修（不变）** | `rag.py:170/190` identity 后缀已加；`rag.py:196` tid→名仍非单射 | 集合名映射仍非单射（latent） |
| L3 | 部分修 | **部分修（不变）** | `reject_reason` 已建且已有写入方（`db.py:543/948`、`api_receipts.py:1123`） | 每租户 pending 容量上限仍无 |
| R7 | 未修（残债） | **已修（P8a-R7）** | `rag.py:107`、`rag_paths.py:47` | 无（cwd 无关，`RAG_DIR` 覆盖仍优先） |
| R8 | 未修（残债） | **已修（P8a-R8）** | `rag.py:320-356`、`rag.py:525`、`rag.py:1334` | 无 |

**已修项复核（仍成立，不改状态）**：H1（`rag.py:976` 签名 `tenant_id` 必填 keyword-only）、H2（`_vector_write_allowed` `rag.py:279`）、H3（两处 conftest `RAG_DIR` 隔离）、M1（`rag.py:267-276` DB 失败跳过向量）、M7（`embeddings.py`）、M8（`api_inventory.py:195` 死写入已删）、M11（`main.js:5975`）、L4（`metadata.json` 三项置 null）。

**§7.2 待办逐条现状**

> **2026-09-25 注记（FP-3 文档台账刷新）**：下表 W-1/W-4/W-8/W-9/W-10/W-11/W-12 已于 **2026-09-24 落地**，并经 **2026-09-25 独立审计（AUDIT_REPORT，AUDIT_VERDICT: PASS）行为级验证证实**（对应判据号见各行）。各「已落地」行所附 file:line 为 **2026-09-25 FP-3 时点实测**（同日 FP-1/FP-2/FP-4 修复波并行落地中，`db.py`/`supervisor.py`/`api_memory.py` 行号在其后仍可能漂移，届时以所引符号名为准）。本注记只刷新状态与证据，不改写 2026-09-22/23 快照正文原义。

| 编号 | 文档原状 | 复核后 | 证据 |
|---|---|---|---|
| W-1 首轮注入改造 | 待办（最高优先） | **已落地（2026-09-24）＋ 审计证实（2026-09-25 AUDIT C3/C4/C5 PASS）** | `supervisor.py:796-803` 首轮注入分支（`first_round_inject_enabled` `:138`，开关 `memory_first_round_inject_enabled` **缺省关**；`_resolve_first_round_vendor` `:147`；`state["rag_hint_source"]="first_round"` `:801`）；hint 与记忆并存 `extract_chain.py:532-542`（W1-a/W1-b）；`extract_chain.py:507` 的 `if vendor_hint:` 门**保留**，首轮检索由 supervisor 预判出的供应商以 vendor_hint 走同一通道（原 `supervisor.py:1010` 的「首轮通过即 break」引用已被本改造取代） |
| W-2 混合召回 + rerank | 待办 | **已交付（P7·Y2）** | `memory_recall.py`（三腿 BM25/精确/向量 + rerank）；`rag.py:1113` |
| W-3 排序纳入治理信号 | 待办 | **已交付（P7·Y2）** | `memory_recall.py:437-456`（decay/hit/recency 入分）；`db.py:3818` 的 rowid DESC 降级为同分破平输入序 |
| W-4 每 (vendor, tenant) 容量上限 | 待办 | **已落地（2026-09-24）＋ 审计证实（2026-09-25 AUDIT D5 PASS）** | `db.py` `_evict_vendor_memory_over_capacity`（FP-3 时点 `:4511`；审计时点 :4391，按最低分/最旧淘汰、只归档不物理删、写 `capacity_eviction` 审计）+ settings 键 `memory_max_active_per_vendor`（`settings_service.py:74`） |
| W-5 已生效记忆查看/编辑界面 | 待办 | **部分交付**：查看面已交付，**编辑仍无** | `api_admin.py:1148`（GET 列表/详情）+ `main.js:13272 loadVendorMemoryGovernance()`（只读 + 调删除端点） |
| W-6 `PERSIST_DIR` 应用根基准（R7） | 待办 | **已交付（P8a-R7）** | `rag_paths.py:47`、`rag.py:107` |
| W-7 幂等复用确保向量存在（R8） | 待办 | **已交付（P8a-R8）** | `rag.py:320-356/525/1334` |
| W-8 扣分触发面收窄 | 待办 | **已落地（2026-09-24）＋ 审计证实（2026-09-25 AUDIT_REPORT §1「12 项改动行为级验证」；定点用例 `test_decay_narrow_ai_prefill.py`）** | `api_receipts.py:1065` `_ai_prefill_corrected_fields`（对比 `ai_prefill_json` 判定「AI 填了值且用户改成别的值」）+ 扣分触发 `:1493`（原 `:1400-1427` 的 old/new 全量 diff 已收窄；ai_prefill 缺失/损坏 → 不扣分 + warning） |
| W-9 写入侧 PII 最小化 | 待办 | **已落地（2026-09-24）＋ 审计证实（2026-09-25 AUDIT A6 PASS：掩码开不漏原文、关逐字节不变）** | `rag.py:649-668`（settings 键 `memory_pii_mask_enabled` 缺省 False + `PIIMaskerTool` 唯一实现）+ 写入侧掩码应用 `:701` 起（HKID/手机/银行卡三类）；`settings_service.py:110` |
| W-10 审核界面字段级 diff | 待办 | **已落地（2026-09-24）＋ 审计证实（2026-09-25 AUDIT D7 PASS）** | `api_memory.py:56` `_attach_existing_fact_values`（`:119` 接线进 `GET /api/memory/pending`，附 `existing_fact_value` 旧值侧）+ `evalset.js:390-418` `pendingFactDiffHtml` 字段级 diff 渲染（kind/key/既有值/候选值） |
| W-11 M9/L1/L2/M10 收尾 | 待办 | **部分已落地（2026-09-24：M9、L1）＋ 审计证实（AUDIT A5/B3 PASS）；L2/M10 仍遗留** | M9：`rag.py:510` 明细为空不写 sample doc（空明细仅 notes 向量）；L1：`rag.py:596-597` 单字门（归一后 `len<2` 一律不放行，单点收口子串/词根/别名三分支）。**仍遗留**：L2（集合名映射非单射，`rag.py:196`）、M10（`entity_candidates` 无生产者，`receipt_utils.py:688`） |
| W-12 文档纠偏 | 待办 | **已落地（2026-09-24）＋ 审计证实（2026-09-25 AUDIT E3 PASS）** | `03-实现现状与演进.md:9` 纠偏注记 + `:139` FR-9 已改 `[部分实现]`、代码地图按真实模块重列；组件Spec pending 标注与 SQL 实测精确一致。`01/02` 两份叙事文档的 §5.3 原文改写仍未做（改法属用户决策，保持待办） |

---

## 5. 设计文档 vs 实现 vs 对外叙事的偏差

### 5.1 Spec 06 的四维沉淀模型：4 维落地 1.5 维

`docs/05-AI产品体系与模块Spec/02-AI原生引擎与RAG飞轮Spec/06-组件Spec-动态RAG与供应商记忆知识飞轮.md:45-50` 承诺"每次 approve 提炼并沉淀 4 个维度"：

| 承诺维度 | 代码现状 | 判定 |
|---|---|---|
| 别名映射（手写缩写 → 标准 SKU） | 无。写入的是识别结果原文；唯一的"标准名"映射来自全局静态表 / SKU 合并，**与供应商无关** | 未实现 |
| 习惯计量单位（箱/包/扎/樽 → 标准单位） | 无结构化抽取，单位只是 `sample` 文本里的一个字；换算是全局表 `unit_factors.py` | 未实现 |
| 版式特征（单号位置/印章习惯） | 退化成 `notes = "版式：" + doc_form 枚举`（如 `printed_delivery_note`），是一个**常量** | 形式实现、无信息量 |
| 历史协议价格区间 | 无。价格异常检测走 `price_anomaly.py` 读 SKU 30 天 DB，**与记忆无关** | 未实现 |

同文档 `:72-98` 的 `VendorMemoryManager` 类与 `get_or_create_collection(name=f"tenant_{tenant_id}_vendor_memory", metadata={"hnsw:space":"cosine"})` 是**示例代码**，全仓不存在该类；实际实现用物理分 collection 但**读侧不传租户**（H1）。
同文档 `:102-108` 的飞轮指标表（78% → 92% → ≥97%、日均修改 3.2 → 0.8 → ≤0.2）在代码里**没有任何埋点或口径实现**。

### 5.2 `03-实现现状与演进.md`：把 FR-9 标成 [PASS]，且证据文件不存在

`docs/04-AI技术选型与评测/01-AI方案与规格/03-实现现状与演进.md`：

- `:16`、`:25`、`:26` 的"代码地图"给出 `services/ocr_service.py`、`services/memory_service.py`、`services/rag_service.py` —— **这三个文件在仓库里都不存在**（实际是 `demo/app/services/rag.py` 等），整张地图（11 个文件）与实际结构不符。
- `:128`：`FR-9 记忆注入与进化 [PASS] 全闭环`，并列举 `build_vendor_context`、`写时净化`、`统计规则（同字段 ≥3 次 → hidden_unit/layout_notes）`、`flag → accuracy_score -0.1（下限 0.2）`、`is_reshot 降权`、`few_shots 结构化白名单` 等机制。
- **核实结果**：`accuracy_score` / `is_reshot` / `few_shots` 全仓 **0 命中**；`build_vendor_context` 不存在；`hidden_unit` / `layout_notes` 不存在。
- **结论**：该 `[PASS]` 描述的是**另一套未落地的设计**（外部平台蓝图里的 `memory_service.py`），不是本仓库的实现。这是"文档声称能力、代码不存在"的典型，建议**撤回或改写该行**，否则任何以 docs 为依据的审计都会得出错误结论。

### 5.3 对外叙事的三处必须改写

| 位置 | 现在的说法 | 代码事实 | 建议改法（可验证、不夸大） |
|---|---|---|---|
| `01-OCR项目面试讲述手册.md:67` | "只认正向信号三条纪律：同供应商**同字段**连续修正 ≥3 次才沉淀规则；重拍 `is_reshot` 不作 few-shot 正样本；flag 触发供应商可信度衰减 **-0.1（下限 0.2）**；`few_shots` 结构化白名单 + 写时关键词净化双保险" | 真实触发是**同供应商连续 3 次点踩**（与字段无关，`db.py:4932`）；真实衰减是**人工覆写 -0.12 / 归档阈值 0.5**（`db.py:2964`）；`is_reshot`/`few_shots`/写时净化 不存在；存在的是**注入侧 XML 沙箱** | "只认正向信号：approve 才写记忆、连续 3 次点踩的蒸馏产物必须过人工闸；注入侧用 XML 数据沙箱 + system 声明防注入；覆写触发非对称衰减（+0.05 / -0.12，阈值 0.5 归档）" |
| `01-OCR项目面试讲述手册.md:69` | "多租户四层隔离：Chroma 强制 `tenant_id` metadata filter …；越权请求 100% 返回 403" | 写入侧按租户分 collection（**不是 metadata filter**）；**读侧两个生产调用点都不传 tenant_id**（H1）；且两个向量库里 `tenant_*` collection 数为 0（隔离路径从未行使）；`pending` 端点有 403/404/409 用例，但检索链路租户口径零覆盖 | "记忆层在**代码能力**上做了按租户 collection 隔离 + 相关性门 + 表层 tenant 过滤；**当前 demo 是单租户，多租户读路径尚未行使**（已定位为读写口径分叉，见 gap 05 P0-1）"。不要声称"越权请求 100% 403"覆盖记忆检索 |
| `02-PGM实习背景叙事包.md:169` | "VendorMemory 供应商记忆 RAG（**别名/单位/版式先验** + XML 沙箱防注入）" | 只有"已确认明细 + 单据形态枚举"进语料；别名/单位先验无实现（别名靠全局静态表与 SKU 合并） | "VendorMemory：把人工确认过的单据明细与单据形态沉淀为识别先验，注入前做 XML 数据沙箱隔离；别名归一与单位习惯的结构化沉淀是下一步" |

**叙事总口径建议**（与 `04` 文件 `:361` 的双层口径一致，但要把记忆齿的现状说准）：

> "飞轮的四齿骨架都在，记忆齿在 8 月底之后补齐了治理元数据、人工闸与读取预算三块，这几块确实是我们自己加的；**人工 approve 才写记忆这一条，在同规模产品里属于更保守的设计**（行业中少见）。但**记忆目前只在门禁打回的重试轮生效、语义检索通道因数据脱节尚未跑起来、且没有删除与效果度量能力** —— 这三条是我们已经定位、正在修的下一步，不是已完成能力。"

---

### 5.4 对外叙事逐条复核（2026-09-23 第三次复核：现在可以怎么讲 / 不可以怎么讲）

> **复核性质**：`01-OCR项目面试讲述手册.md` / `02-PGM实习背景叙事包.md` / `03-实现现状与演进.md` 三份对外文档**本次仍不改字**（改法属用户决策）。本节只做一件事：**代码局面已经变了，逐条重算哪些说法现在成立**。
> **复核方法（三条纪律）**：① 每条结论必须附 **file:line 或测试文件名**，不接受印象判断；② 否命题（"不存在"）必须写明扫描范围；③ 所有 live 数字为本次**只读实测**（DB 以 `mode=ro` 打开，指标脚本跑 live 的 `/tmp` 副本）。
> **live 快照（2026-09-23 实测）**：`vendor_memory` 19 行（active 10 / archived 9；active 的 `fact_kind` = alias 3 / layout 5 / price_range 2，**无 unit**）；`pending_memory` 11 行（approved 10 / pending 1）；`vendor_sku_alias` 0 行；`receipts` 701（`supplier_name` 非空 356、`rag_context_json` 非空 12）；`tenant_id` distinct = 1（全部 `default`）。
> **md5 口径（本次实测的一个重要旁证，不要省略）**：本节全部数字取自 2026-09-23 03:43 的 live 快照，当时 live DB md5 = `c216fc2fa3575f5eff8805af114c0168`；此后 **03:46:40 live DB 与 `demo/.rag_chroma` 被并发写者改动**（DB md5 变为 `4386580e4ea913b0057c4506bff0c23b`），但**行数完全一致**（`receipts` 701 / max id 1494 / `receipt_items` 687 / `vendor_memory` 19（active 10）/ `pending_memory` 11 / `vendor_sku_alias` 0），属于加法迁移或页级重排，**不是本次复核造成**。本节的只读探针与 `import app.main` 自检前后 live md5 均未变（自检用 `DB_PATH=/tmp/... RAG_DIR=/tmp/...`，未触碰 live）。
> **指标（`memory_metrics.py` 只读、跑 live 副本）**：有效命中率 `eligible_denominator=179 / injected=9 / effective_rate_pct=5.03%`；旧口径 12/701 = 1.71%；SKU 匹配 163/687 = 23.73%；A/B 真实口径 **-7.42pp**（处理组 2/12 vs 对照 166/689，**n<30 不足以判定**）、代理口径 +4.71pp（p=0.286 不显著）。
> **两个开关的真实取值（决定"能力已实现"与"已在跑"的分界）**：`memory_enabled` 缺省 **True**（`demo/app/services/settings_service.py:81`，live 未覆盖）→ **检索在跑**；`memory_extract_enabled` 缺省 **False**（`settings_service.py:99`，消费方 `demo/app/api_receipts.py:989-990`，live 未覆盖）→ **LLM 抽取与冲突整合不在跑**。

#### A. 逐条复核结论

| 编号 | 文档位置 | 现在的说法（摘） | 结论 | 支持 / 反驳证据 |
|---|---|---|---|---|
| A1 | `01:67` | "同供应商**同字段**连续修正 ≥3 次才沉淀规则" | **[措辞需微调]** | 触发条件不是"同字段"，而是**同供应商同租户连续 3 次点踩**：`demo/app/models.py:135 FEEDBACK_DISTILL_THRESHOLD = 3`、`demo/app/db.py:6615 should_distill_vendor_memory`、`demo/app/services/rag.py:1187 ingest_feedback_memory`。机制在跑，但描述的对象错了 |
| A2 | `01:67` | "重拍 `is_reshot` 不作 few-shot 正样本" | **[仍不成立]** | `is_reshot` / `few_shots` 在整个仓库（排除 `docs/`）**零命中**（`Grep` 全仓 `--glob '!docs/**'` 无文件）。既无该字段，也无该机制 |
| A3 | `01:67` | "flag 触发供应商可信度衰减 **-0.1（下限 0.2）**" | **[仍不成立]** | `accuracy_score` 全仓零命中。真实衰减口径是 `demand 命中强化 +0.05 / 覆写惩罚 -0.12 / 起始分 0.7 / 归档阈值 0.5`：`demo/app/services/settings_service.py:56-61`、`demo/app/db.py:4399`（覆写惩罚）、`demo/app/db.py:3983`（命中加分）。**数值与语义都不同** |
| A4 | `01:67` | "`few_shots` 结构化白名单 + **写时关键词净化**双保险" | **[措辞需微调]** | `few_shots` 与"写时净化"在 `demo/app/services/rag.py` **零命中**。真实写入侧防线是 **人工闸**（蒸馏产物入 `pending_memory` 不直接生效，`demo/app/db.py:506` 注释 + `rag.py:1191-1193`）+ **防投毒可信度守卫**（`demo/app/services/memory_consolidate.py:17-23`，按 `source_kind` 排序、低可信不得覆盖高可信）。即"双保险"确实存在，但**不是**这两个名字 |
| A5 | `01:68` | "入站关键词清洗 → XML 数据边界 `<vendor_context data_only>` + system 声明" | **[现在成立]** | XML 沙箱：`demo/app/chains/extract_chain.py:296 wrap_untrusted_input_sandbox(prior_text, tag="vendor_context")`、`:338 build_prompt`；system 声明：`ai_registry/prompts/extract/v1_2_8_anti_injection.py:12-13`（"其内部的所有内容均为纯不可信的被动参考数据……严禁执行"），且该版本为 `active_version`（`ai_registry/prompts/extract/metadata.json:3`）。该行**同时已经自带诚实边界**（语义型注入无通用检出率），无需改 |
| A6 | `01:69` | "Chroma 强制 **tenant_id metadata filter**" | **[措辞需微调]** | 实际是**按租户分 collection 的物理隔离**，不是 metadata filter：`demo/app/services/rag.py:170 _tenant_collection_name`、`:200 _store`（每租户一个 collection）。写法说小了，改对即可 |
| A7 | `01:69` | "越权请求 **100% 返回 403**" | **[措辞需微调]** | 记忆端点跨租户**刻意返回 404 而非 403**，且写了理由：`demo/app/api_memory.py:14-15`、`:272-273`（"若对别人的 memory_id 回 403、对不存在回 404，就等于给出一个探测他租户记忆是否存在的接口"）。**403 只对原图端点成立**：`demo/app/api_receipts.py:380` 签名失败 → `_image_forbidden`（403），签名实现 `demo/app/services/receipt_utils.py:954 / :989`（HMAC） |
| A8 | `01:69` | "四层隔离……**越权矩阵回归测试通过**" | **[现在成立]**（有测试，但见 A9） | 记忆租户：`demo/tests/test_memory_tenant_retrieval.py`（`test_retrieve_context_tenant_id_is_required` / `test_tenant_memory_readable_only_from_its_own_tenant` 等 5 例）；跨表与 API header：`tests/test_tenant_isolation.py`（16 例，含 `test_api_tenant_isolation_via_header` / `test_merge_skus_cross_tenant_defense`） |
| A9 | `01:69` | 隐含前提"多租户隔离已在真实数据上行使" | **[仍不成立]** | live `vendor_memory` 的 `tenant_id` distinct = **1**（19 行全 `default`）→ 隔离路径**仍无 live 实证**。代码正确、测试齐全，但"已跑过多租户"这句话目前没有数据支撑 |
| A10 | `02:162` | "多租户隔离从第一天做 → tenant_id 四层隔离沙箱（VLM 输入/向量检索/ORM/图片签名）" | **[措辞需微调]** | 四层在代码里都能指到（VLM 输入 `extract_chain.py:337-356`；向量 `rag.py:170/200`；ORM `db.py:3777 list_vendor_memory(tenant_id=…)`；图片 HMAC `receipt_utils.py:954/989`），但同样受 A9 限制：**代码具备能力 ≠ 已在跑** |
| A11 | `02:169` | "VendorMemory 供应商记忆 RAG（**别名/单位/版式先验** + XML 沙箱防注入）" | **[措辞需微调]** | 三者**都已是结构化 fact 的 kind**（`demo/app/services/memory_facts.py:44-48`：`alias/unit/layout/price_range`），且已接到注入侧（`rag.py:736 _is_structured_fact_row` / `:768 _render_fact_row`，生成侧 P2b 见 `rag.py:718` 注释）。**但 live 生效情况分三种**：alias 3 条 active、layout 5 条 active、**unit 0 条 active**（详见 B3）。另可加"价格区间"（live 2 条 active） |
| A12 | `03:25` | 代码地图列出 `services/memory_service.py` | **[仍不成立]** | 该文件全仓不存在（`find . -name memory_service.py` 空） |
| A13 | `03:26` | 代码地图列出 `services/rag_service.py` | **[仍不成立]** | 全仓不存在；实际是 `demo/app/services/rag.py` |
| A14 | `03:16` | 代码地图列出 `services/ocr_service.py` | **[仍不成立]** | 全仓不存在；实际是 `demo/app/chains/extract_chain.py` + `supervisor.py` |
| A15 | `03:70-71` | "approve 副作用：few-shot 回写 + RAG 案例 ingest；save_edited 副作用：EvalLog diff → 统计规则触发；flag 副作用：供应商 `accuracy_score` 衰减" | **[仍不成立]**（前半"RAG 案例 ingest"除外） | `few_shot` / `统计规则` / `hidden_unit` / `layout_notes` 在 `demo/app` 下**均无实现**（`Grep demo/app` 唯一命中是 `models.py:126` 的一句注释 `# layout_notes / 别称 / 单位基准`，描述的是 `notes` 字段的用途，不是机制）；`accuracy_score` 全仓零命中（同 A3）。"RAG 案例 ingest"对应的是 `approve_pending_to_memory`（`rag.py:1255`），口径已换成"人审批准的 fact 落库" |
| A16 | `03:128` | `FR-9 记忆注入与进化 [PASS] 全闭环` | **[仍不成立]** 作为 `[PASS] 全闭环` 这一结论 | 见 C 节（含两种修法） |
| A17 | `03:128` | "已确认供应商经 `supplier_hint` 透传**首轮即注入**（精确门槛复核）" | **[措辞需微调]** | 机制确实通了：单张上传 `demo/app/api_receipts.py:473`（Form）→ `:544` 透传；重试 `:1720`、换图重解析 `:1792` 从单据行派生（`_vendor_hint_from_row` `:435`）；无 hint 时 `vendor_precheck.predict_vendor` 兜底（`:529-538`）；前端手选 `demo/static/js/main.js:1298-1306 / :2153-2155`。**但**：批量入口**显式范围外**（`api_receipts.py:599-609`），且 live 只有 12 张单据 `rag_context_json` 非空（701 张里 345 张连 `supplier_name` 都没有）→ "首轮即注入"是**能力已接通**，不是**命中率已达标** |

#### B. 三条指定偏差的复核（任务点名）

**B1 `01:67`「同字段连续修正 ≥3 次 / is_reshot / flag 衰减 / few_shots 白名单」**

结论：**七项里只有一项（人工闸 + 蒸馏触发）成立，且对象写错**。

| 文档写的 | 代码实际 | 判定 |
|---|---|---|
| 同供应商**同字段**连续修正 ≥3 次 | 同供应商同租户**连续 3 次点踩**（`models.py:135` / `db.py:6615` / `rag.py:1187`） | 对象错（不是字段修正，是点踩），阈值 3 相同 |
| `is_reshot` 不作 few-shot 正样本 | 无该字段 | 不成立 |
| flag → 可信度衰减 **-0.1（下限 0.2）** | 覆写惩罚 **-0.12**、起始分 0.7、归档阈值 0.5（`settings_service.py:56-61`） | 数值与机制都不成立 |
| `few_shots` 结构化白名单 | 无 | 不成立 |
| 写时关键词净化 | 无；写侧真实防线是**人工闸**（`db.py:506` / `rag.py:1191-1193`）+ **防投毒可信度守卫**（`memory_consolidate.py:17-23`） | 名字不成立，防线存在但换了形态 |
| （文档未提）注入侧 **XML 沙箱** | `extract_chain.py:296` + `v1_2_8_anti_injection.py:12-13` | 应补进去 |

可验证的一点：`v1_2_8_anti_injection` 的 metadata 已经**主动把虚假指标纠正为 null**（`ai_registry/prompts/extract/metadata.json` 的 `claim_unsupported_note`）—— 这正是本节建议的诚实口径的范例，可以在面试里当成"我们连自己的历史指标都复核"的加分点。

**B2 `01:69`「Chroma 强制 tenant_id filter … 越权 100% 403」**

结论：**结构比文档写的更强，但两个具体说法都不准**。

- **"metadata filter" 说小了**：实现是**按租户分 collection**（`rag.py:170 _tenant_collection_name` / `:200 _store`），物理隔离强于 metadata 过滤。
- **读侧租户已强制**（这是 09-22 之后真修的一条）：`rag.py:976-978 retrieve_context(vendor, top_k=3, *, tenant_id, ...)` 的 `tenant_id` 是**必填 keyword-only、无默认值**（docstring `:981-984` 明说"刻意不给默认值"，改必填后"忘记传"在开发期直接 TypeError）；两个生产读点都传值：`demo/app/chains/extract_chain.py:517`、`demo/app/chains/supervisor.py:936`。
- **"越权 100% 403" 对记忆层不成立**：`api_memory.py:14-15 / :272-273` 明确**选择 404 而非 403**，理由是 403/404 的差异会变成"探测他租户记忆是否存在"的 oracle。403 只对**原图端点**成立（`api_receipts.py:380`）。
- **仍无 live 实证**：`tenant_id` distinct = 1（A9）。

**B3 `02:169`「别名/单位/版式先验」**

结论：**[措辞需微调]**，且**必须区分"能力已实现"、"已在 live 生效"、"覆盖多少"三层**。

| 先验种类 | 能力（代码） | live 生效（active 行） | 判定 |
|---|---|---|---|
| 别名 alias | `memory_facts.py:44 KIND_ALIAS`；注入 `rag.py:768 _render_fact_row` | **3 条**（如 `鴻運餐飲` 的 `有機菜心→有机菜心`） | 成立（覆盖面小） |
| 单位 unit | `memory_facts.py:45 KIND_UNIT` | **0 条**（冷启动种子阶段因 `key==value` 退化项被过滤丢弃） | **能力已实现、live 未生效** |
| 版式 layout | `memory_facts.py:46 KIND_LAYOUT` | **5 条**（`layout:doc_form` = `printed_delivery_note` / `ncr_handwritten`） | 成立 |
| 价格区间 price_range | `memory_facts.py:47 KIND_PRICE_RANGE` | **2 条** | 成立（原句未提，可加） |

另：`02:162` 的"四层隔离沙箱"与 A10 同结论；`02:167` 的"三级重试阶梯"已自带 CLI 弃用注记，不在本节范围。

#### C. `03-实现现状与演进.md` 的 FR-9 硬伤复核（最敏感一条）

**核实结果：三个被引用的文件到现在仍然不存在**，该行仍是硬伤。

| 被引用文件 | 出现在 | 现状 | 实际模块 |
|---|---|---|---|
| `services/memory_service.py` | `03:25`（代码地图）、`03:128`（FR-9 证据） | 全仓不存在（`find . -name memory_service.py` 空） | `demo/app/services/memory_facts.py` / `memory_consolidate.py` / `memory_extract.py` / `memory_store.py` / `memory_consistency.py` |
| `services/rag_service.py` | `03:26`、`03:128` | 全仓不存在 | `demo/app/services/rag.py` |
| `services/ocr_service.py` | `03:16`、`03:128` | 全仓不存在 | `demo/app/chains/extract_chain.py` + `demo/app/chains/supervisor.py` |

同时 FR-9 行列举的机制里，`build_vendor_context` / `写时净化` / `统计规则（同字段 ≥3 次 → hidden_unit/layout_notes）` / `flag → accuracy_score -0.1` / `is_reshot 降权` / `few_shots` **均无实现**：`is_reshot` / `few_shots` / `accuracy_score` 在整个仓库（排除 `docs/`）零命中；`few_shot` / `统计规则` / `hidden_unit` / `layout_notes` 在 `demo/app` 下唯一命中是 `demo/app/models.py:126` 的一句注释（`# layout_notes / 别称 / 单位基准`，描述 `notes` 字段用途，不是机制）—— 即该 `[PASS]` 描述的是**另一套未落地的设计**，不是本仓库的实现。

**两种可选修法（择一，改法属用户决策）**：

- **修法 A（最小、最诚实）：删掉这一行的 `[PASS]` 结论，改为据实标注。** 把 `03:128` 的 FR-9 单元格改成 `[部分实现]`，并在"代码证据"列换成真实模块名（`demo/app/services/rag.py`、`memory_facts.py`、`memory_consolidate.py`、`demo/app/chains/extract_chain.py:296`、`demo/app/api_receipts.py:1099`），同时删掉 `:16 / :25 / :26` 三行不存在的文件（该代码地图 11 个文件与实际结构不符，整张表都需要按真实路径重列）。**优点**：不引入新的未验证断言；**缺点**：代码地图整表返工。
- **修法 B（保留结论、换成真实证据）：把 `[PASS] 全闭环` 降级为带条件的分档描述**，例如"读链路（XML 沙箱 + 检索注入）已实现并端到端验证；写链路（人审闸 + 结构化 fact 落库 + 删除/恢复/TTL）已实现；LLM 抽取与冲突整合已实现但**缺省关闭**（`memory_extract_enabled=False`）；有效命中率当前 5.03%，目标 ≥80% 未达成"。**优点**：保留了"记忆能力"的正面信息；**缺点**：必须同步维护这些数字，否则会再次过期。

**共同底线（两条修法都适用）**：① 引用前先核文件是否存在；② **不得为了好讲而把"代码具备能力"写成"已在跑"** —— 本行的 `[PASS] 全闭环` 与 `05:§0` 的 `effective_rate_pct=5.03%` 直接冲突，面试官一旦追问命中率，`[PASS]` 会当场自相矛盾。

#### D. 可替代表述（直接可用）

> 用法：以下每段给 **建议原句 + 为何这么说（附证据）+ 还差什么才能说得更强**。三条通用红线：**不把"能力已实现"说成"已在跑"**；**不用"100%"这类没有分母的词**；**凡引用指标必带口径**。

**D1 供应商记忆怎么防止错误自我强化（替换 `01:67`）**

建议原句：
> "写入侧只认**人**：approve 时才落记忆；系统自己从反馈里蒸馏出来的规则不直接生效，先进**待确认队列**等人工批准。触发条件是同供应商**连续 3 次点踩**，不是模型的自我判断。注入侧做 XML 数据沙箱 + system 声明，把记忆一律当**不可信数据**而不是指令。治理上还有一条防投毒守卫：低可信来源（如模型抽取）不允许覆盖人工确认过的高可信事实。"

为何这么说：蒸馏触发与提前入库见 `models.py:135` / `db.py:6615` / `rag.py:1187` / `db.py:506`；人工闸端点在 `demo/app/api_memory.py:102/123/199`（前端在 `demo/static/js/evalset.js:386-433`）；XML 沙箱 `extract_chain.py:296`；防投毒守卫 `memory_consolidate.py:17-23`。
还差什么才能说得更强：**live 只有 10 条 active 记忆、其中 alias 3 / layout 5 / price_range 2**，说出来时不要暗示规模；`is_reshot` / `few_shots` / `accuracy_score` / 写时净化这四项**一律不要提**（不存在）。

**D2 多租户隔离（替换 `01:69` / `02:162`）**

建议原句：
> "记忆层在**代码能力**上是按租户分 collection 的物理隔离，读侧 `tenant_id` 是必填参数（刻意不给默认值，避免忘记传时静默落到默认桶）；ORM 与枚举层同样按租户过滤。原图出口是 HMAC 短时签名 URL，签名无效返回 403。记忆端点跨租户刻意统一返回 **404 而不是 403**，避免用状态码差异探测他租户的记忆是否存在。**当前 demo 是单租户，多租户读路径有完整测试但还没有真实多租户数据**。"

为何这么说：`rag.py:170/200`（分 collection）、`rag.py:976-978`（必填 keyword-only）、`extract_chain.py:517` + `supervisor.py:936`（两个生产读点都传）、`api_memory.py:14-15/272-273`（404 的理由）、`api_receipts.py:380` + `receipt_utils.py:954/989`（原图 403）、`demo/tests/test_memory_tenant_retrieval.py` + `tests/test_tenant_isolation.py`（测试）。
还差什么才能说得更强：需要**真实多租户数据**（live `tenant_id` distinct 目前 = 1）；此前不建议说"越权 100% 403"覆盖记忆检索。

**D3 别名/单位/版式先验（替换 `02:169`）**

建议原句：
> "供应商记忆沉淀成**结构化事实**，分四类：别名映射、单位习惯、版式先验、历史价格区间，每条都带来源与可回溯的单据引用，进人工确认队列。生成侧只注入这些**确定性事实**，整单明细不再进 prompt（避免模型照抄上一条单的 anchoring 风险）。注入前统一过 XML 数据沙箱。"

为何这么说：四种 kind `memory_facts.py:44-48`；生成侧结构化优先 `rag.py:718`（P2b 注释）/ `:736`、`:768`（实现）；沙箱 `extract_chain.py:296/338`。
还差什么才能说得更强：**"四类都已在跑"现在不成立** —— live active 只有 alias 3 / layout 5 / price_range 2，**unit 为 0**。安全讲法是"四类都已实现，其中别名/版式/价格区间已有生效实例，单位习惯的沉淀还在冷启动阶段"。

**D4 自进化飞轮的整体口径（用于被追问"飞轮转起来了吗"）**

建议原句：
> "飞轮的四齿**骨架**都在，但**不是四齿都转到了目标转速**。积累侧：写入有人工闸、治理有删除/恢复/过期/一致性巡检；控制侧：人工闸入口已收进评测台，规则级候选一律进闸；落地侧：抽取触发已接在两个真实动作上（复核保存与入账）；信号侧：已经能算出**有效命中率**（分母只含"供应商可确定且该供应商有可用记忆"的单据）。**当前实测有效命中率 5.03%（9/179），距离目标 ≥80% 很远；LLM 抽取与冲突整合已实现但缺省关闭；混合召回与 rerank 还没做。** 这三条是下一步，不是已完成能力。"

为何这么说：治理三件套 `api_memory.py:224/268/298` + `memory_consistency.py`；抽取两处触发 `api_receipts.py:1581 / :1652`（单一实现 `:1185`）；有效命中率口径与数字 `demo/scripts/memory_metrics.py`（`eligible_denominator=179 / injected=9 / 5.03%`）；开关缺省 `settings_service.py:99`。
还差什么才能说得更强：要能把命中率抬上去，需要 ① 扩大被覆盖的供应商主体集合（live 345/701 张单据连 `supplier_name` 都没有）② 混合召回 + rerank ③ 打开并验证 LLM 抽取。**在数字动之前，不要说"越用越准"。**

**D5 `03-实现现状与演进.md` 的 FR-9 行（替换 `03:128`）**

建议原句（修法 B 版）：
> "FR-9 记忆注入与进化 —— **[部分实现]**。读链路：`<vendor_context>` XML 数据沙箱注入（`demo/app/chains/extract_chain.py:296/338`），供应商先验经 `supplier_hint` 在单张上传 / 重试 / 重解析三个入口注入首轮，批量入口明确范围外。写链路：approve 回写 + 连续 3 次点踩蒸馏，两者都经人工闸（`demo/app/api_memory.py:54-124`）；结构化 fact 四类 kind 落库（`demo/app/services/memory_facts.py:44-48`）。治理：删除 / 恢复 / supersede / TTL / DB-向量一致性巡检均已具备。**未达成**：LLM 抽取与冲突整合缺省关闭（`memory_extract_enabled=False`）；有效命中率 5.03%（目标 ≥80%）；混合召回与 rerank 未做。"

为何这么说：每一条都能指到 file:line（见 A 节），且**主动列出未达成项**，与 `05:§0` 的指标一致，不会被追问击穿。
还差什么才能说得更强：把 `effective_rate_pct` 抬到目标区间、并把 `memory_extract_enabled` 打开后拿到抽取准确率数据。

#### E. 现在**绝对不能**这么讲（本次复核新增的"负向清单"）

| 不能讲 | 为什么 | 现状数字 |
|---|---|---|
| "记忆命中率 ≥80%" | goal 的目标值，从未达成 | 有效命中率 **5.03%**（9/179） |
| "越用越准 / 飞轮已转起来" | 分子那 9 条里含历史遗留注入；机制刚接通且覆盖面小 | 同上 |
| "多租户隔离已上线运行" | 代码与测试齐备，但 live 无多租户数据 | `tenant_id` distinct = 1 |
| "LLM 自动抽取 / 记忆自动整合已在跑" | 开关缺省关闭，live 未开 | `memory_extract_enabled=False`（`settings_service.py:99`） |
| "is_reshot / few_shots / accuracy_score / 写时净化" | 全仓零命中 | `Grep` 全仓无文件 |
| "越权请求 100% 返回 403"（覆盖记忆检索） | 记忆端点刻意用 404 | `api_memory.py:14-15/272-273` |

---

## 6. 修订后的成熟度评分（2026-09-23 第二次复评）

> 权威的逐波证据出处：计划文件 **§10 滚动收口状态**（`docs/superpowers/plans/2026-09-22-vendormemory-convergence.md`）。本节只给评分与理由，**不另抄一份逐波表**。

| 齿 | 04 文件（2026-08-28） | 09-22 首次复评 | **09-23 本次复评** | 两次变化理由（09-23 的新增项） |
|---|---|---|---|---|
| 积累（记忆） | 35% | 58%（55–62） | **68%（区间 64–72）** | **09-23 上调项**：① 租户隔离从"降到 40%"→**回到满分**（H1 已真修 + `tenant_id` 必填，端到端探针证明异租户取不到）；② 治理元数据从"零消费"→**大部分**（`hit_count`/`last_hit_at` 被 `rag.py:1178` 真消费）；③ **删除 / 恢复 / supersede / TTL / DB↔向量对账从"全无"→全部具备**，live 巡检 `consistent=true`；④ 审计链打通（`written_by` 落库）；⑤ **M7 假开关解除**（`RAG_EMBEDDING_PROVIDER` 三档可切换）。**未给分的抵消项**：首轮注入未改造（H4）、混合召回 / rerank 仍无（C2/C3）、容量上限与"已生效记忆查看/编辑界面"仍无（D5）、M10 实体 RAG 仍空槽 |
| 控制（人机协作） | 30% | 57%（55–60） | **64%（区间 60–68）** | 新增：人工闸**入口发现性已修**（toast 带跳转 + 未审条数角标，`main.js:5973/13858`）；**规则级候选一律进闸**（`api_receipts.py:1185`）；拒绝理由已落库（`reject_reason`）；人可删除 / 恢复记忆（`api_memory.py:224/268/298`）。扣分项：**字段级 diff 审核视图仍未做**（`evalset.js` 无 `fact_key` 渲染）、**已生效记忆的治理视图仍未做**（D5） |
| 落地（改进生成） | 45% | 45% | **50%（区间 46–54）** | 新增：抽取触发已在**两处**接通（`save_edited` / `approve_receipt`，单一实现 `api_receipts.py:1185`）；复核侧「供应商×SKU 历史价格区间」提示已上线（`api_admin.py:1580` + `main.js:5292`）。**仍未算落地**：生成侧的结构化事实注入未接（P2b），首轮注入未改造 → 记忆与识别主路径**尚未闭环** |
| 信号（评测） | 60% | 60%（未复评） | **63%（部分复评）** | 新增：`memory_metrics.py` 落地**有效命中率**口径（分母＝有条件命中）、A/B 双口径（真实注入口径 vs 代理口径，且声明不可混读）、管理台看板（`api_admin.py:1096`）、一致性巡检端点。**完整复评仍待 `04` 文件**，故只小幅上调 |

### 6.1 口径提醒（本次仍然适用，且比上次更重要）

**上表的数字是「代码具备的能力」评分，不是「在线生效的能力」。** 本次修订后两者的差距**变大了**，因为 09-23 落地的多是"能力"（可删除、可恢复、可切 embedding、可观测），而**在线效果仍被两件事压着**（原第三件「混合召回 + rerank 未做」已由 P7·Y2 修复，见 §4.6）：

1. **首轮注入未改造**（H4）→ 无 hint 的单据首轮不检索；
2. **有效命中率的分母过小**（179，且 701 单里 345 单连 `supplier_name` 都没有）；
3. ~~**混合召回 + rerank 未做**（C2/C3）→ 召回质量只有字面 char-ngram。~~ → **已修（P7·Y2）**：`memory_recall.py` 三腿混合召回 + 治理信号 rerank，本项**不再构成"在线效果被压住"的理由**（见 §4.6）。

按"在线生效"口径，记忆齿约 **45–50%**（首次复评给的是"低于 45%"，本次因 live 已有 10 条 active 记忆并产出 5.03% 有效命中率而略有上移，但仍远低于"代码具备能力"的 68%）。**汇报时这两套口径不要混用。**

---

## 7. 修复优先级建议（2026-09-23 重排）

> 原 §7 的 15 条是 2026-09-22 的计划式清单。本次按"已交付 / 待办"重排。**逐波落地证据与日期以计划 §10 为准**。

### 7.1 已交付（不再作为待办；按批次列，附本文件对应状态）

| 原编号 | 事项 | 交付日期 | 本文件状态 |
|---|---|---|---|
| 1 | 读路径租户透传（P0-1 / H1） | 2026-09-22 | 已交付（§4.1 H1） |
| 2 | conftest 隔离 `RAG_DIR`（P0-3 / H3） | 2026-09-22 | 已交付（§4.1 H3） |
| 3 | 存量向量清退（P0-2 / H2，D5 拍板"全部清退"） | 2026-09-22 | 已交付（§4.1 H2） |
| 4 | 修孤儿生成器 + metadata 闸门（M1） | 2026-09-22 | 已交付（§4.2 M1） |
| 5 | 删除与恢复能力（P1-2 的删除 / 恢复 / 级联删向量 / 审计） | 2026-09-22 | 部分交付（容量上限未做，见 §7.2 W-4） |
| 6 | 衰减双向生效（P1-3 的强化侧） | 2026-09-22 | 已交付（起始分 0.7，§4.2 M3） |
| 7 | 写入侧信任元数据（P1-4 的 `written_by` / `trust_level`） | 2026-09-23 | 部分交付（PII 扫描未做，§4.2 P1-4） |
| 8 | 静默失败补日志（P2-3，读路径） | 2026-09-23 | 部分交付（§4.2 M6） |
| 9 | 人工闸入口发现性（M11） | 2026-09-22 | 已交付（§4.2 M11） |
| 10 | `RAG_EMBEDDING_PROVIDER` 真开关 + 集合名带 identity（P2-2 前半 / M7） | 2026-09-23 | 已交付（§4.2 M7） |
| 11 | `全局品类库` 死写入（P2-1） | 2026-09-23 | 已交付（选"删除"，§4.2 M8） |
| 12 | 冲突 supersede + TTL（P1-T3）+ 冷启动种子 + 人审批准 | 2026-09-23 | 已交付（live 10 条 active） |
| 13 | 阶段四地基模块（`memory_facts` / `memory_extract` / `memory_consolidate` / `embeddings`） | 2026-09-23 | 已建但**部分未接线**（§4.2 P2-4） |

### 7.2 待办（重排后）

**W-1（最高优先，直接卡住命中率指标）｜记忆作用面 —— 首轮注入改造（原 P1-1 / H4）**
把检索提到首轮之前（上传时手选 / 预判供应商 → 首轮即注入），或改掉 `extract_chain.py:507` 的 `if vendor_hint:` 门与 `supervisor.py:1010` 首轮通过即 `break` 的行为。**这是"命中率 ≥80%"唯一的前置条件**：不改造它，分母再大、记忆再多，首轮也拿不到先验。同时修 `effective_hint = vendor_ctx or vendor_hint`（`extract_chain.py:526`）的"二选一"语义 —— 记忆与用户 hint 应当并存。
**2026-09-25 注记：已落地（2026-09-24），2026-09-25 独立审计证实（AUDIT C3/C4/C5 PASS）。** 实现方式：`supervisor.py:796-803` 首轮注入分支——开关 `memory_first_round_inject_enabled`（`first_round_inject_enabled()`，`supervisor.py:138`，**缺省关 = 零行为变化**）开启且无显式 hint 时，经 `_resolve_first_round_vendor`（`:147`，调用既有 `vendor_precheck.predict_vendor`）确定供应商并作为 `vendor_hint` 传入既有 hint 通道，首轮即检索即注入；`state["rag_hint_source"]="first_round"`（`:801`）。原「`effective_hint` 二选一」语义已改为**并存**（`extract_chain.py:532-542`，W1-a/W1-b：`[prior:hint]` 与 `[prior:memory]` 同进 prompt）。上文 `extract_chain.py:507` 的 `if vendor_hint:` 门保留（首轮由 supervisor 预判传入 vendor_hint 走同门）；`supervisor.py:1010` 引用已被本改造取代。落库侧 `rag_context_json` 以 `[prior:hint]` 区分于重试轮 `[prior:retry]`。

**W-2｜混合召回 + rerank（原 P2-2 / C2/C3）　【已交付 · 2026-09-23（P7·Y2）】**
原状：召回只有字面 char-ngram 向量 + 供应商名硬门，无语义、无 BM25、无 rerank。**现状**：`memory_recall.py` 已落地三腿混合召回（精确/别名腿 + 关键词 BM25 近似腿 + 向量腿）+ rerank（权重可配可审计），`rag.py:1113` 调用；验收用例 `tests/test_memory_hybrid_recall.py`。

**W-3｜排序纳入治理信号（原 P1-3 后半 / M4/M5）　【已交付 · 2026-09-23（P7·Y2）】**
原状：`db.py` 仍 `rowid DESC`、三列未被排序消费。**现状**：`memory_recall.rerank` 已消费 `decay_score` / `hit_count` / `last_hit_at`（`memory_recall.py:437-456`）；`db.py:3818` 的 rowid DESC 降级为同分破平的输入序。

**W-4｜每 (vendor, tenant) 容量上限（原 P1-2 后半 / M2）**
全仓无容量边界；一条高频供应商可以无限追加记忆行。~~（本轮复核：仍待办）~~（**2026-09-25 注：已落地，见下方注记**）
**2026-09-25 注记：已落地（2026-09-24），2026-09-25 独立审计证实（AUDIT D5 PASS）。** `db._evict_vendor_memory_over_capacity`（FP-3 时点 `db.py:4511`；审计时点 :4391）+ settings 键 `memory_max_active_per_vendor`（`settings_service.py:74`，0/负值 = 不限制）：超出上限时按最低 decay 分、同分更旧者优先**只归档不物理删**，归档级联删向量（复用 `memory_store.archive_memory_vectors` 单一实现），并写 `capacity_eviction` 审计（同分淘汰更旧者、冲突时保最旧高分，审计载荷含 memory_ids 序）。

**W-5｜已生效记忆的查看 / 编辑界面（原 P1-2 / 行业最小集 D5）　【部分交付 · 2026-09-23】**
**查看面已交付**：`GET /api/admin/vendor-memory`（列表 + 详情，`api_admin.py:1148/1216`）+ 前端面板 `loadVendorMemoryGovernance()`（`main.js:13272`，只读 + 复用既有删除端点）。**仍缺**：管理员**编辑**记忆的能力（编辑仍无）。

**W-6｜`PERSIST_DIR` 改为以应用根为基准（残债 R7）　【已交付 · 2026-09-23（P8a-R7）】**
原状：未显式设 `RAG_DIR` 时口径为 cwd 相对。**现状**：`rag.py:107` 消费 `rag_paths.resolve_rag_dir()`（应用根基准），`RAG_DIR` 显式覆盖仍优先（`rag_paths.py:47`）。

**W-7｜幂等复用分支确保向量存在（残债 R8）　【已交付 · 2026-09-23（P8a-R8）】**
原状：复用既有 memory_id 时跳过向量写入。**现状**：`_sync_memory_vectors(reusing=True)` 先查后补（`rag.py:320-356/525/1334`）。

**W-8｜扣分触发面收窄（原 P1-3 / M3）**
`api_receipts.py:1400-1427` 仍按 old/new 全量 diff 扣分，未对比 `ai_prefill_json`（该处注释自称"店员真的改了 AI 预填值"但代码没有这个对比）。收窄为"AI 填了值且用户改成别的值"，可避免价格合理变动误伤高频供应商的全部记忆。
**2026-09-25 注记：已落地（2026-09-24），2026-09-25 独立审计证实（AUDIT_REPORT §1 十二项改动行为级验证；定点用例 `demo/tests/test_decay_narrow_ai_prefill.py`）。** 判定改为 `api_receipts.py:1065` `_ai_prefill_corrected_fields`（对比 `receipts.ai_prefill_json`：仅「AI 预填过该字段且店员改成别的值」才扣分；ai_prefill 缺失/损坏 → 不扣分 + warning，不静默全量扣分），扣分触发点现位于 `:1493`（原 `:1400-1427` 的全量 diff 路径已收窄）。

**W-9｜写入侧 PII 最小化（原 P1-4 后半）**
记忆里存完整品名 + 单价明细；无 PII 识别 / 脱敏。
**2026-09-25 注记：已落地（2026-09-24），2026-09-25 独立审计证实（AUDIT A6 PASS：掩码开时 DB 行与向量文本零命中电话/HKID/卡号；掩码关时与 pre-W-9 逐字节一致）。** `rag.py:649-668`：settings 键 `memory_pii_mask_enabled`（`settings_service.py:110`，**缺省 False** = 零行为变化）+ `ai_registry.tools.pii_masker.v1_0_0` 为唯一脱敏实现（HKID/私人手机/银行卡三类）；写入侧在记忆语料（notes/sample）落库与写向量前统一过掩码（`rag.py:701` 起），并按命中类型计数留痕、不打印原文。

**W-10｜审核界面字段级 diff（原 P1-T5 后半）**
`evalset.js` 尚未渲染 `fact_key` / `fact_value` → 审核者目前只能整段接受 / 拒绝。
**2026-09-25 注记：已落地（2026-09-24），2026-09-25 独立审计证实（AUDIT D7 PASS）。** 旧值侧：`api_memory.py:56` `_attach_existing_fact_values`（`:119` 接线进 `GET /api/memory/pending`，为带 `fact_key` 的候选附同 vendor+fact_key 的当前 active 值 `existing_fact_value`）；前端：`evalset.js:390-418` `pendingFactDiffHtml` 逐字段渲染 kind / key / 既有值 / 候选值（无既有值显示「新增」，遗留纯文本候选退回中性说明）。

**W-11｜M9 空 approve 仍写向量、L1 短名门、L2 非单射、M10 实体 RAG 空槽**
四类 low/mid 收尾项（§4.2/§4.3 各条）。
**2026-09-25 注记：其中 M9、L1 已落地（2026-09-24），2026-09-25 独立审计证实（AUDIT A5 / B3 PASS）；L2、M10 仍遗留。** M9：`rag.py:510` 明细为空时不再构造 sample doc（仅 notes 向量；明细非空时逐字节不变）；L1：`rag.py:596-597` 归一后两侧 `len<2` 一律不放行（单字门收口子串/核心词根/别名组三分支，空串返回 False）。**仍遗留**：L2（`rag.py:196` 集合名 tid→名映射仍非单射，latent）、M10（`entity_candidates` 仍无生产者，`receipt_utils.py:688`）。

**W-12｜文档纠偏（原 §7.14，属对外叙事，独立复核）**
撤回 `03-实现现状与演进.md:16/25/26/128` 的 FR-9 `[PASS]`；改写 `05-Spec 06` 的四维模型与指标表（标"设计目标"）；按 §5.3 改写两处对外叙事；`entity_candidates`（M10）要么接线要么标注为预留。
**2026-09-25 注记：主要项已落地（2026-09-24），2026-09-25 独立审计证实（AUDIT E3 PASS）。** `03-实现现状与演进.md`：`:9` 已加纠偏注记、`:139` FR-9 已由 `[PASS] 全闭环` 改为 `[部分实现]`（真实模块证据），不存在的 `services/*_service.py` 代码地图已按真实路径重列；`05-Spec 06` 的 pending-as-active 残留已清（组件Spec:46 与 SQL 实测精确一致）。**仍待办**：`01-OCR项目面试讲述手册.md` / `02-PGM实习背景叙事包.md` 两处 §5.3 叙事原文改写（改法属用户决策）。

### 7.3 「现在真正卡住指标的是什么」（一句话）

**不是记忆的存储与治理，而是「记忆进不了首轮」+「分母太小」。** 前三批（P0/P1）把"记忆能存、能删、能审计、能对账"补齐了，指标数字却几乎没动 —— 因为**决定命中率的 W-2（召回质量：混合召回 + rerank）已在 P7·Y2 落地，真正剩下的是 W-1（首轮注入，`extract_chain.py:507` 仍是 `if vendor_hint:` 才检索）与分母本身（701 单里 345 单无 `supplier_name`，属数据层限制）**。汇报时不要把"已交付 13 项"读成"指标会动"。（**2026-09-25 注**：W-1 已于 09-24 落地——`supervisor.py:796-803` 首轮注入分支，开关 `memory_first_round_inject_enabled` **缺省关**，故"缺省不检索"的运行时事实暂未变，但能力缺口本身已闭合，见 §7.2 W-1 注记。）

---

## 8. 本次修订对照（原文 → 新文）与已被证伪的段落

### 8.1 逐条对照（只列被改动的判定，不列新增章节）

| # | 位置 | 原文（2026-09-22） | 新文（2026-09-23） | 依据 |
|---|---|---|---|---|
| 1 | §0 结论段 | 「四件事使它距离大厂级记忆系统还很远」（隐含"这些都还坏着"） | 保留原文为**快照**，新增 §0.5「四条 high 现状」+ 当前指标表 | H1 已修 / H2 原形态已修 / H3 已修 / H4 部分修（§4.1） |
| 2 | §0 速览表 | 六条"需要拍板"（无状态） | 保留原表，新增 §0.6「六项拍板的处置状态」 | 计划 §5 D1–D8 已拍板；P0-1/2/3 已交付、P1-1 未完成、P1-2 大部分交付、P1-3 未完成 |
| 3 | §1.6 治理表「删除/编辑单条记忆：三层全无」 | 全无 | 已在 §4.2 M2 记为**部分修**（删除 / 恢复已具备；编辑与容量上限仍无） | `api_memory.py:224/268/298`、`db.py:3914/4621` |
| 4 | §1.6「衰减的实际形态：强化恒等」 | 恒等操作 | **已不准确**：起始分改 `memory_initial_decay_score()`（缺省 0.7）→ 强化不再恒等 | `db.py:4469/3480` |
| 5 | §1.6「归档可逆性：不可逆」 | 不可逆 | **已不准确**：`revive_vendor_memory` + `POST /api/memory/{id}/revive` | `api_memory.py:298`、`db.py:4621` |
| 6 | §1.4「嵌入是 char-ngram…宣称的开关不存在」 | 开关不存在 | **已不准确**：`RAG_EMBEDDING_PROVIDER` 已是真开关（三档），集合名带 identity | `embeddings.py:53`、`rag.py:153/190` |
| 7 | §1.2 入口二「全局品类库 死写入」 | 存在死写入 | **已不准确**：该写入已删除（仅余解释性注释） | `api_inventory.py:195-200` |
| 8 | §4 表 H1–L4 | 4 列（无状态） | **6 列**（+状态 +本轮复核证据），行号全部按当前代码校正 | §4.1–§4.3 |
| 9 | §4 三条"会被误认为记忆能力的事实"之二（pending 空表 → 闸门从未触发） | 从未被真实触发 | **已不准确**：live `pending_memory` 11 行（10 approved + 1 pending），人工闸已被真实行使 | live 只读 SQL；计划 §10「里程碑」行 |
| 10 | §6 成熟度 | 记忆 58% / 控制 57% / 落地 45% / 信号 60% | 记忆 **68%** / 控制 **64%** / 落地 **50%** / 信号 **63%**（并放大两套口径差距的说明） | §6 |
| 11 | §7 优先级 | 15 条计划式清单（P0/P1/P2 三批） | 拆成 **§7.1 已交付（13 项，附日期）** + **§7.2 待办 W-1…W-12（重排）** + **§7.3 真正卡住指标的是什么** | §7 |

### 8.2 未改动但已被证伪的段落（明说）

- **§5.2 `03-实现现状与演进.md` 的 FR-9 `[PASS]` 行**：**本段仍准确** —— 该文档**至今未改**，`accuracy_score` / `is_reshot` / `few_shots` 在全仓仍 0 命中，被引用的 `services/memory_service.py` / `rag_service.py` / `ocr_service.py` **仍不存在**。→ 属待办 **W-12**（对外叙事独立复核）。
- **§5.3 三处对外叙事**（`01-讲述手册:67/69`、`02-叙事包:169`）：**本段仍准确**（这三处本轮未改）。注意其中 `:69` 的「越权请求 100% 返回 403」**已经比 09-22 更不成立**：记忆层删除/恢复端点已存在，租户口径也更完整，但"100% 403"仍不覆盖记忆检索（H1 是读写口径问题而非 403 问题）。
- **§5.1 `05-Spec 06` 的四维沉淀模型**（4 维落地 1.5 维）：**部分已变化** —— 别名 / 单位 / 价格区间现在有**结构化事实的落点**（`vendor_sku_alias`、`MemoryFact` 的 `unit` / `price_range` kind、`memory_metrics` 的价格提示端点），但**生成侧注入未接通**，故仍不能说"四维已落地"。
- **§2.3 定位差异（哪些 gap 不该当缺陷）**：**本段仍准确**，且结论更强 —— 人工闸 + XML 沙箱仍是超配项；本轮把 A7（人审）从"有队列"推进到"队列被真实行使过"。

### 8.3 明确保留、不得删减的已知遗留项（禁止为了文档好看而删）

1. **项目从未上线**：北极星指标基线 = **「0（未上线）」**（`docs/01-产品方案(14步)/step7-指标体系.md:92`）；无真实门店试点 / 付费客户 / UAT。
2. **A/B 真实口径样本不足**：真实注入口径仅 **n=12**（-7.42pp），统计上**不足以判定**；代理口径 +4.71pp / p=0.286 **不显著**。两口径不可混读。
3. **有效命中率 5.03% 距目标 ≥80% 差距很大**，且分母只有 179（701 单中 345 单连 `supplier_name` 都没有）。
4. **首轮注入未改造**（H4）→ 记忆仍只在"非快速门禁打回的重试轮"真正进 prompt。
5. **未做的能力**（2026-09-24 复核后更新）：混合召回（C2）/rerank（C3）**已交付（P7·Y2）**；已生效记忆**查看面已交付**（编辑仍无）；**仍缺**：容量上限、记忆编辑、字段级 diff 审核视图、M9/L1/L2/M10。
6. **两条新残债 R7/R8**（§4.5）—— **2026-09-24 复核：均已修（P8a-R7 / P8a-R8）**，结论保留以便对照。
7. **`demo/.rag_chroma` 目前 `consistent=true` 是"当前快照"**，不是"结构上不可能再脱钩"：R7/R8 已修（`rag_paths.py` 单一路径 + 幂等复用补写），但向量库外部损坏 / 手动清库仍可造成脱钩，巡检仍应定期跑。
8. **测试数字与目录级回归**仍受既有噪声影响（`tests/test_gt_review_api.py` 是 HEAD 符号链接 → 与 `demo/tests/` 同名文件被重复采集，产生 25 个 `fixture 'evalset_env' not found` error，属既有噪声非新回归）。

---

## 附录 A：复现命令

### A.1 台账数字复现（2026-09-23 新增，最常用）

```bash
cd /Users/ethan/Documents/GitHub/receipt-agent-interview

# 1) 四项指标 + 有效命中率口径（脚本只读、mode=ro、不 import app.db，故无需 DB_PATH/RAG_DIR）
$HOME/.pyenv/versions/3.9.6/bin/python demo/scripts/memory_metrics.py --json
#   -> memory_hit.rate_pct = 1.71（旧口径，已标注不可用于判定）
#   -> memory_hit_effective.eligible_denominator = 179 / injected = 9 / effective_rate_pct = 5.03
#   -> edit_rate_ab.delta_pp = 4.71（代理口径）
#   -> sku_match.rate_pct = 23.73

# 2) DB↔向量一致性巡检（只读：DB 拷到 tmp、Chroma sqlite 以只读打开）
cd demo && RAG_DIR=$PWD/.rag_chroma $HOME/.pyenv/versions/3.9.6/bin/python -m app.services.memory_consistency --json
#   -> consistent: true / vectors: 15 / distinct_memory_id: 10 / orphan_mids: [] / missing_vector_mids: []

# 3) live 状态速览（只读）
sqlite3 -readonly demo/receipt_demo.db \
  "select (select count(*) from receipts), (select count(*) from receipt_items), (select count(*) from suppliers);"
sqlite3 -readonly demo/receipt_demo.db "select status, count(*) from vendor_memory group by status;"
sqlite3 -readonly demo/receipt_demo.db "select status, count(*) from pending_memory group by status;"
md5 -q demo/receipt_demo.db
```

> **红线**：任何 `import app.*` / pytest 都必须先 `export DB_PATH=/tmp/... RAG_DIR=/tmp/...` —— `db._make_engine()` 在 import 期执行迁移块，会对 `DB_PATH` 指向的库做 ALTER（本项目历史上已两次让 live 库发生加法迁移）。上表 1)/2)/3) 均**不 import `app.db`**，故安全。

### A.2 原快照的复现命令（2026-09-22，保留以复算历史证据）

```bash
# live DB（只读）
sqlite3 -readonly demo/receipt_demo.db \
  "select count(*), tenant_id from vendor_memory group by tenant_id;"
sqlite3 -readonly demo/receipt_demo.db \
  "select memory_id, vendor, source_kind, hit_count, decay_score, status, created_at from vendor_memory;"

# live 向量库（先复制再读，避免 chromadb 打开持久目录产生写入）
rm -rf /tmp/vm_chroma_probe && mkdir -p /tmp/vm_chroma_probe \
  && cp -R demo/.rag_chroma /tmp/vm_chroma_probe/rag
sqlite3 -readonly /tmp/vm_chroma_probe/rag/chroma.sqlite3 "select id,name from collections;"
sqlite3 -readonly /tmp/vm_chroma_probe/rag/chroma.sqlite3 "select count(*) from embeddings;"
# 元数据完整度：带 memory_id / 带 tenant_id
sqlite3 -readonly /tmp/vm_chroma_probe/rag/chroma.sqlite3 \
  "select (select count(*) from embeddings) total,
          (select count(*) from embeddings where id not in (select id from embedding_metadata where key='memory_id')) no_mid,
          (select count(*) from embeddings where id not in (select id from embedding_metadata where key='tenant_id')) no_tid;"
# DB 与向量的 memory_id 交集：comm -12 / comm -23 / comm -13 分别得到 0 / 12 / 9

# 调用点与消费方盘点
grep -rn "retrieve_context(" --include=*.py .
grep -rn "ingest_memory(" --include=*.py .
grep -rn "vendor_hint" demo/static/js/ demo/templates/          # 无输出 = 前端不发 hint
grep -rn "RAG_EMBEDDING_PROVIDER" .                              # 无输出 = 承诺的开关不存在
grep -n "RAG_DIR" tests/conftest.py demo/conftest.py             # 无输出 = 未隔离
grep -rn "accuracy_score\|is_reshot\|few_shots" demo ai_registry  # 无输出 = 文档承诺的机制不存在
grep -rn "api/memory" demo/static/                               # evalset.js:389/427 = 人工闸界面在评测页

# 隔离探针（解释器必须是 /Users/ethan/.pyenv/shims/python3，唯一带 langchain_community/chromadb 的）
DB_PATH=$(mktemp -d)/t.db RAG_DIR=$(mktemp -d) /Users/ethan/.pyenv/shims/python3 - <<'PY'
import sys; sys.path.insert(0, "demo")
import app.services.rag as rag, app.db as db
rag.ingest_memory("探针供应商", "- 菜心 10斤 @8 = 80", notes="版式：送貨單",
                  tenant_id="t2", receipt_id=1001)
print("生产调用形态:", repr(rag.retrieve_context("探针供应商")))            # 期望：'' ← H1
print("显式租户:    ", repr(rag.retrieve_context("探针供应商", tenant_id="t2")))
PY
```

**未触碰**：`git checkout` / `git restore` / `git stash` / 目录级 pytest / live 库与 live 向量库的任何写入。

> **A.2 的注释已过期（保留原文以复算历史）**：其中 4 条注释的"期望输出"在 2026-09-23 已不成立 —— `grep -rn "vendor_hint" demo/static/js/` **现在有输出**（H4 入口已补）、`grep -rn "RAG_EMBEDDING_PROVIDER" .` **现在有输出**（M7 已修）、`grep -n "RAG_DIR" tests/conftest.py demo/conftest.py` **现在有输出**（H3 已修）、`comm -12` **不再是 0**（现为 10 个 mid 全交集，见 A.1 巡检）。隔离探针仍是 H1 的有效复算方式（其"期望 ''"现在**已不成立**：修复后生产调用形态能读到内容）。

---

## 附录 B：行业基准出处

**AWS Bedrock AgentCore Memory**（[O]）
- 概念与记忆类型（短期 event / 长期 record）：https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html ・ https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-types.html （本次独立抓取：明确"Extraction / Consolidation 后台异步"，`RetrieveMemoryRecords` 为语义搜索）
- namespace 与 IAM 条件键隔离：https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/specify-long-term-memory-organization.html
- 结构化元数据（indexedKeys / metadataSchema / `STRICTLY_CONSISTENT`）：https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/long-term-memory-metadata.html
- 可观测性：https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-observability.html
- `CreateMemory`（`eventExpiryDuration` 3–365 天、`encryptionKeyArn`）：https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateMemory.html
- 删除记录：https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/long-term-delete-memory-records.html

**Vertex AI Memory Bank**（[O]）；memory topics / extraction+consolidation / similarity search / revisions 与回滚 / profiles（字段级 TTL）/ TTL 默认 365 天
- 生成记忆：https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/generate-memories
- 配置：…/memory-bank/setup ・ 取回：…/memory-bank/fetch-memories ・ revision：…/memory-bank/revisions ・ profiles：…/memory-bank/profiles
- ADK MemoryService：https://adk.dev/sessions/memory/
- 官方 Caution（"系统并非万无一失"，不能只依赖它过滤敏感信息）：…/memory-bank/generate-memories

**OpenAI ChatGPT memory**（[O]）：Sources 展示"用了哪条记忆"、可查看/编辑/删除、关闭 chat history 引用后 30 天内删除
- https://help.openai.com/en/articles/8590148-memory-faq ・ https://openai.com/index/memory-and-new-controls-for-chatgpt/

**Anthropic Claude memory tool**（[O]）：`/memories` 文件式记忆 + 7 个命令 + 官方 `Path traversal protection` 章节
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool ・ https://claude.com/blog/context-management

**mem0**（[P]/[O]）：三段式 extract→consolidate→retrieve；LLM 四选一 ADD/UPDATE/DELETE/NOOP；scope 标识；PII 警告（"不要在记忆里存密钥或未脱敏 PII，因其可被检索"）
- https://arxiv.org/abs/2504.19413 ・ https://docs.mem0.ai/core-concepts/memory-operations ・ https://docs.mem0.ai/core-concepts/memory-types （本次独立抓取）・ https://docs.mem0.ai/open-source/features/graph-memory

**Zep / Graphiti**（[P]/[O]）：双时态四条时间戳、edge invalidation、cosine+BM25+BFS 后 rerank、audit/RBAC/ABAC
- https://arxiv.org/abs/2501.13956 ・ https://help.getzep.com/concepts ・ https://help.getzep.com/retrieval-philosophy ・ https://help.getzep.com/governance
- 注：Zep 文档站 `concepts/facts`、`graphiti/core-concepts/edge-invalidation` 在 2026-09-21 访问 404，相关结论以论文表述为准

**Letta / MemGPT**（[P]/[O]）：主/外部上下文分层、70% 预警 100% flush、MemFS git 版本化
- https://ar5iv.labs.arxiv.org/html/2310.08560 ・ https://docs.letta.com/agent-sdk/memory/index.md

**LangMem / LangGraph store**（[O]）：hot path vs background；"relevance = similarity + importance + strength"；profile/collection；namespace
- https://langchain-ai.github.io/langmem/concepts/conceptual_guide/

**标准与合规**
- OWASP Top 10 for Agentic Applications 2026（资源页）：https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
- OWASP Agent Memory Guard（直击 ASI06，本次独立抓取原文"the attack survives context resets"）：https://owasp.org/www-project-agent-memory-guard/
- ASI06 详解（第三方，标 [3]）：https://vectorize.io/articles/owasp-asi06 ；Agent Security Bench 最高平均攻击成功率 84.30%（[P]）：https://arxiv.org/abs/2410.02644
- GDPR Art.17 删除权 / Art.5 原则：https://gdpr-info.eu/art-17-gdpr/ ・ https://gdpr-info.eu/art-5-gdpr/
- 香港 PDPO 六大原则与 **s.26**（属罪行，最高罚 HK$10,000；本次独立抓取官方原文）：https://www.pcpd.org.hk/english/data_privacy_law/ordinance_at_a_Glance/ordinance.html

**公开评测基准**：LoCoMo https://arxiv.org/abs/2402.17753 ・ LongMemEval（ICLR 2025）https://arxiv.org/abs/2410.10813
（LongMemEval 把"知识更新"与"拒绝作答"设为一等能力，即"记忆过期与新信息覆盖旧信息"必须可评测 —— 本项目当前两项目标都无法评测。）

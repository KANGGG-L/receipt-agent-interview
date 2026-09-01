# 11 · 组件 Spec · 全链路埋点与体验反馈体系

> **模块定位**：面向 AI-PM 与算法工程师的**度量闭环基础设施**。在「三层互不信任」架构下，AI 只预填、人工背书——而预填到底好不好、人工改了多少、用户在哪里失去耐心，必须由一套**服务端权威、append-only、隐私合规**的埋点体系回答。本 Spec 将 `step7-指标体系` 定义的 11 个规范事件在单店落地版中对齐落库，补齐行级复核 diff、放弃解析、体验反馈等缺口事件，并提供聚合消费端点与右下角 👍/👎 即时反馈入口。
> **对标 14 步方案**：`step7-指标体系`（四层金字塔 + 11 事件规范）、`step11-PRD定稿`（数据契约/字段字典）、`step13-安全合规`（PDPO 脱敏红线）、`step14-上线迭代`（数据驱动飞轮）。
> **实现代码**：[`app/services/receipt_utils.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/services/receipt_utils.py)（解析生命周期 + `compute_review_diff`）、[`app/api_receipts.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/api_receipts.py)（`/api/track` + 复核/审批事件）、[`app/chains/supervisor.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/chains/supervisor.py)（门禁/RAG 事件）、[`app/api_admin.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/api_admin.py)（聚合端点）、`static/js/main.js` + `templates/index.html`（前端钩子与反馈悬浮层）。

---

## 1. 业务背景与价值主张

### 1.1 为什么「三层互不信任」必须配度量闭环？

| 痛点 | 没有埋点时 | 有埋点后 |
| :--- | :--- | :--- |
| AI 预填质量黑盒 | 只知道「识别成功/失败」，不知道人工改了多少 | `receipt_review_submitted` 行级三类 diff + FER，量化每张单的人工负担 |
| 门禁价值不可见 | 算术门禁拦了多少、重试几轮才能过，无统计 | `math_guard_checked` / `contract_guard_checked` + 重试分布 |
| 用户耐心流失无感知 | 不知道有人等不及解析转了手工单 | `parse_abandoned_for_manual` 放弃率 |
| 体验好坏无出口 | 老板不满意只能口头说 | 右下角 👍/👎 即时反馈，智能归属到单据或全局 |

**反虚荣指标立场**（对齐 step7）：上传量是虚荣指标，北极星以 `receipt_approved`（幂等入库）为终态；本体系所有漏斗与转化率均以 upload 为分母、approve 为分子终点。

---

## 2. 指标体系对齐（L1–L4 金字塔映射）

| 指标 | 层级 | 口径定义 | 数据来源事件 | 目标 |
| :--- | :---: | :--- | :--- | :--- |
| 端到端解析延迟 P50/P95 | L1 | `ocr_parse_started` → `ocr_parsed`/`ocr_error` 的 elapsed_ms 分位 | user_event | P95 ≤ 12s（L4 定稿） |
| 单张 Token 成本 | L1 | ai_decision_log 聚合（已有） | ai_decision_log | ≤ HK$0.03/张 |
| 门禁拦截率 | L2 | `math_guard_checked.is_arithmetic_valid=false` 占比 | user_event | 100% 拦截幻觉 |
| 一次通过率 Pass@1 | L2 | attempts==1 的 `ocr_parsed` 占比 | user_event | ≥ 80% |
| 字段编辑率 FER | L3 | Σfield_mod_counts / Σ(total_fields) | `receipt_review_submitted` | ≤ 10% |
| 零修改通过率 | L3 | rows_added+modified+deleted==0 且直接 approve 占比 | review_submitted + approved | ≥ 70% |
| 单张复核耗时 | L3 | parsed → `receipt_review_submitted` 时差 | user_event | P50 ≤ 30s |
| 解析放弃率 | L3 | `parse_abandoned_for_manual` / `ocr_parse_started` | user_event | ≤ 5% |
| SKU 更改率 | L3 | sku_changed>0 的复核单占比 | `receipt_review_submitted` | 趋势下降（RAG 飞轮） |
| 反馈满意率 | L3 | 👍 / (👍+👎) | receipt_feedback + `feedback_received` | ≥ 90% |
| 挽回点击占比（归因拆分） | L3 | `retake_clicked` / (`retake_clicked` + `reparse_clicked`) 为输入归因占比，其余为模型归因；另计挽回入口率 = 挽回点击总数 / `ocr_parsed` 总数 | user_event | 输入:模型归因趋势观测，无硬目标 |
| 点踩率 | L3 | `receipt_feedback` 表 down 数 / 有反馈单据数 | receipt_feedback | ≤ 10% |
| 挽回成功率 | L3 | 每条 `retake_clicked`/`reparse_clicked` 事件，在同 `receipt_id` 存在 ts 更晚的 `receipt_review_submitted` 或 `receipt_approved` 记为挽回成功；成功数 / 点击数 | user_event | ≥ 60% |
| 北极星：周入库单数 | L4 | `receipt_approved` 周计数 | user_event | ≥ 50 张/周/店 |

---

## 3. 埋点事件目录（step7 规范对齐 + 扩展）

### 3.0 公共上下文字段字典（复用 `user_event` 表，append-only）

| 字段 | 类型 | 业务含义与约束 |
| :--- | :--- | :--- |
| `id` | INTEGER | 自增主键 |
| `ts` | VARCHAR | ISO8601 服务端权威时钟 |
| `account_id` | VARCHAR | 操作者（后端事件为 "system"） |
| `session_id` | VARCHAR | 会话标识（前端 localStorage 生成） |
| `event_type` | VARCHAR | 事件名（见下表，snake_case 规范命名） |
| `receipt_id` | INTEGER | 关联单据（可空，全局事件为 NULL） |
| `properties` | JSON | 专属 payload（**禁含 PII**，见 §8） |
| `grp` | VARCHAR | 灰测分组 control/treatment（可空） |

### 3.1 step7 规范事件对齐表

| # | 事件名称 | 触发时机 | 专属 Payload | 写入方 | 落地状态 |
| :---: | :--- | :--- | :--- | :--- | :--- |
| 1 | `receipt_uploaded` | 上传成功创建 Job | `batch_id, image_path, async` | 后端 | 已有（实现别名 `upload`，漏斗双计兼容） |
| 2 | `ocr_parse_started` | Job 状态 uploaded→parsing | `job_id` | 后端（Job 状态机单点） | **本次实现** |
| 3 | `ocr_parsed` | VLM 提取+门禁通过并落库 | `job_id, status:"done", elapsed_ms, attempts, gate_rejects, use_grey` | 后端 | **本次实现** |
| 4 | `ocr_error` | 解析崩溃/超时/契约彻底失败 | `job_id, status:"error"\|"timeout", elapsed_ms, reason, attempts` | 后端 | **本次实现** |
| 5 | `math_guard_checked` | 算术门禁每轮执行完毕 | `is_valid, reject_reason, attempt` | 后端 supervisor | **本次实现**（轻量摘要） |
| 6 | `field_edited` | 用户增/删行、改 SKU 绑定等离散编辑 | `receipt_id, field_name(row_add/row_remove/sku_binding), new_val` | 前端 /api/track | **本次实现**（离散操作；字段级修改由 #7 payload 的 field_mod_counts 权威统计） |
| 7 | `receipt_review_submitted` | save_edited 成功 | `rows_added, rows_modified, rows_deleted, sku_changed, field_mod_counts{name,quantity,unit,unit_price,amount}, fer_rate, total_ai, total_final` | 后端（`compute_review_diff`） | **本次实现**（别名 `save_edited` 兼容） |
| 8 | `receipt_approved` | owner approve 幂等入库 | `e2e_ms`（created_at→approve 时差） | 后端 | **本次实现**（别名 `approve` 兼容） |
| 9 | `receipt_flagged` | owner 标记异常 | `flag_reason` | 后端 | **本次实现** |
| 10 | `price_anomaly_flagged` | 解析落库时单品涨幅 >10% 标红 | `receipt_id, anomaly_count, max_surge_pct` | 后端 | **本次实现** |
| 11 | `reconciliation_completed` | 月结对账完成 | — | 后端 | 既有财务流，**本 Spec 不重复实现** |

### 3.2 扩展事件表（step7 之外、本 Spec 新增 EV-12+）

| # | 事件名称 | 触发时机 | 专属 Payload | 写入方 |
| :---: | :--- | :--- | :--- | :--- |
| 12 | `parse_abandoned_for_manual` | 用户点「等不及？转手工补录」 | `receipt_id, waited_ms, pending_photos` | 前端 |
| 13 | `manual_entry_started` | 进入新建手工单态 | `source: "abort"\|"new"` | 前端 |
| 14 | `contract_guard_checked` | 契约门禁每轮执行完毕 | `is_valid, reject_reason, attempt` | 后端 supervisor |
| 15 | `rag_hit` | VendorMemory 检索注入非空上下文 | `context_len`（**不含供应商名**，见 §8） | 后端 supervisor |
| 16 | `feedback_received` | 👍/ 且无加载单据（全局体验） | `like: 1\|-1, tab, role` | 前端 |
| 17 | `reupload_after_fail` | 解析失败态点击「重新上传」：移除坏图、回到标准上传入口流程 | `receipt_id`（失败单据） | 前端 |
| 18 | `retake_clicked` | 复核工作台点击「重拍」（输入归因：怀疑图拍坏了） | `prior_feedback: 1\|-1\|null`（点击前本会话对该单的最后表态） | 前端 |
| 19 | `reparse_clicked` | 复核工作台点击「重新解析」（模型归因：图没问题怀疑解析错） | `prior_feedback: 1\|-1\|null` | 前端 |
| 20 | `image_replaced` | `replace-image` 换图成功、新图重跑识别前 | `old_image`（旧图文件名，可审计回溯）, `trigger: "retake"` | 后端 |

### 3.3 事件流全景

```
receipt_uploaded ──► ocr_parse_started ──► ocr_parsed / ocr_error
                          │   ▲                │
                          │   └─ contract/math_guard_checked ×N（重试阶梯 ≤3）
                          │                    │
        parse_abandoned_for_manual             ▼
              │（未等解析转手工）      field_edited（离散编辑）
              ▼                        │
        manual_entry_started           ▼
                            receipt_review_submitted（行级三类 diff + FER）
                                       │
                          receipt_approved（北极星）/ receipt_flagged
```

---

## 4. 口径规则与状态机约束

1. **解析生命周期单点写入**：`ocr_parse_started/ocr_parsed/ocr_error` 只在 `receipt_utils` 的 Job 状态机写入（同步/异步两模式唯一权威），耗时由 JOBS `started_ts` 计算；API 层不得重复写（旧同步写点已删除）。
2. **行级 diff 权威口径**（`compute_review_diff` 纯函数）：
   - 对齐：按位置 zip 至 min 长度逐行比 `(name, quantity, unit, unit_price, amount, sku_id)`；final 多出计 `rows_added`，ai 多出计 `rows_deleted`，任一字段不同计 `rows_modified`；
   - `sku_changed`：仅当 AI 原行 `sku_id` 非空且 (sku_id 或 name) 被用户改变——AI 未匹配行不计；
   - **手工单边界**：`ai_prefill_json` 为空 → 全部行计 `rows_added`，其余为 0，不报错；
   - 数值比较统一 float 归一，避免 "1" vs 1.0 误判；**品名比较两侧先过 `canonical_sku_name` 归一**（`ai_prefill_json` 存 VLM 原始品名、明细行存归一品名，不归一会把品名永远计为修改、FER 虚高）。
3. **放弃 vs 超时互斥**：`parse_abandoned_for_manual`（主动，前端 abort 置 cancelled）与 `ocr_error(status=timeout)`（被动，sweep 清扫）不会双计同一单。
4. **三表职责分离**：`audit_logs_json` = 合规审计（谁改了什么，字段级，不可省略）；`ai_decision_log` = AI 引擎视角（token/cost/每轮决策）；`user_event` = 用户行为漏斗（本体系）。门禁事件在 user_event 只记轻量摘要，不重复存 token/cost。
5. **别名兼容**：历史事件 `upload/save_edited/approve/parse_done` 与新规范名在漏斗聚合时双计兼容（`get_funnel` 步骤接受别名集合）。

---

## 5. 前端交互设计：复核操作行内联 👍/ 反馈按钮

```
─────────────────────────────────────────────────────────────────────────────┐
│ [+ 新增明细行]                           [放弃上传] [确认上传单据] [👍] [] │
─────────────────────────────────────────────────────────────────────────────┘
  👍 hover：满意当前解析结果   👎 hover：不满意当前解析结果
  36px 圆角按钮，hover 放大 1.1，👍 绿底 / 👎 红底（语义色变量）
```

**显隐与归属规则**（👍/ 只评价识别结果）：
- **显示条件**：「收据识别」Tab 激活 **且** 已有识别结果——`ai_prefill.items` 非空、非手工单态、非解析中/待确认态（`syncFeedbackVisibility` 于 Tab 切换、结果渲染、失败/手工单入口统一刷新；初始默认隐藏）；
- **归属**：显示即存在当前已识别单据 → 复用 `POST /api/receipt/{id}/feedback`（写入 `receipt_feedback` 表，喂 VendorMemory 飞轮，连续点踩触发 FR-9 提炼）；
- **兜底**：无加载单据时记 `feedback_received` 全局体验事件（防御性分支，显隐规则下正常不可达）；
- 两种路径均 toast 即时确认（「感谢点赞！」/「已收到反馈，我们会改进」），失败静默不阻塞业务。

**前端埋点通道**：统一 `track(eventType, receiptId, properties)` helper，`fetch('/api/track', {keepalive:true})` 静默失败；不用 sendBeacon（绕过鉴权 fetch 包装会丢 X-Role 头）。

---

## 6. 后端 API 接口契约一览

| 方法 | 路径 | 入参 | 返回内容 | 鉴权 |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/track` | `{event_type, receipt_id?, properties?}` | `{"status":"ok"}`（始终 200，内部失败静默） | staff+ |
| `GET` | `/api/analytics/recognition-summary` | `period_days`（默认 7） | 见 §7 聚合结构 | **admin** |
| `GET` | `/api/admin/funnel` | `period_days, groups` | 扩展步骤：upload→ocr_parse_started→ocr_parsed→edit→approve | admin |
| `POST` | `/api/receipt/{id}/feedback` | `{like: 1\|-1, comment?}` | 现有结构（含 distilled 提炼标记） | staff+（复用） |
| `GET` | `/api/analytics/recovery-summary` | `tenant_id?`（`all` 或指定租户） | 挽回与埋点观测聚合：全量事件分布、挽回点击占比、点踩率、挽回成功率、最近事件流（§7.1） | **admin** |

---

## 7. 聚合消费设计（recognition-summary）

Python 侧循环聚合（对齐 `/api/admin/metrics` 既有模式；数据量万级以上再启用 `ai_metric_snapshot` 预聚合）：

```json
{
  "status": "success", "period_days": 7,
  "data": {
    "parse_total": 120, "parse_success": 105, "parse_fail": 15, "parse_fail_rate": 0.125,
    "elapsed_p50_ms": 9000, "elapsed_p95_ms": 12000,
    "gate_reject_count": 8, "gate_reject_reasons": {"math": 5, "contract": 3},
    "retry_distribution": {"1": 90, "2": 20, "3": 10},
    "rows_added": 23, "rows_modified": 67, "rows_deleted": 12,
    "sku_changed": 18, "sku_changed_rate": 0.17, "fer_rate": 0.062,
    "abandon_count": 5, "abandon_rate": 0.042,
    "e2e_avg_ms": 125000, "rag_hit_count": 45,
    "feedback": {"up": 30, "down": 3, "global_up": 4, "global_down": 1},
    "grey_split": {"control": {"parse_success": 80, "fer_rate": 0.07},
                   "treatment": {"parse_success": 25, "fer_rate": 0.05}}
  }
}
```

---

### 7.1 挽回与埋点观测（recovery-summary + 治理与埋点观测台）

`GET /api/analytics/recovery-summary`（**admin** 专属，`api_phase2.py` 实现，支持 `?tenant_id=` 参数；分母 < 30 附 `low_confidence` 标记）：

```json
{
  "status": "success",
  "data": {
    "tenant_id": "all",
    "available_tenants": ["default", "tenant_a"],
    "event_distribution": [
      {"event_type": "upload", "count": 120, "share": 0.31, "last_ts": "2026-08-30T09:12:00"}
    ],
    "recovery": {
      "retake_clicked": 8, "reparse_clicked": 12,
      "input_attribution_share": 0.4,
      "total": 20, "entry_rate": 0.19
    },
    "feedback": {"down": 5, "feedbacked_receipts": 30, "down_rate": 0.1667},
    "recovery_success": {"success": 14, "total": 20, "rate": 0.7},
    "recent_events": [
      {"ts": "...", "event_type": "retake_clicked", "receipt_id": 42, "account_id": "staff@demo.hk", "properties": {"prior_feedback": -1}}
    ],
    "low_confidence": true
  }
}
```

**口径细则**：

1. **多租户过滤**：当入参 `tenant_id` 指定且非 `all` 时，仅聚合计算该租户下的 `user_event` 与 `receipt_feedback`；当未指定或为 `all` 时，聚合计算全量租户；返回 `available_tenants` 供前端选择器渲染；
2. **全量事件分布**：`user_event` 中所有 `event_type` 的计数、占比（分母=全部事件数）、最近触发时间——一张表覆盖当前全部埋点事件（含挽回三事件），按计数降序；
3. **挽回点击**：`retake_clicked`（输入归因）与 `reparse_clicked`（模型归因）计数及各自占比；`entry_rate` = 挽回点击总数 / `ocr_parsed` 总数（无解析事件时为 `null`）；
4. **点踩率**：查 `receipt_feedback` 表——down 数（`like = -1`）/ 有反馈的去重单据数（口径与 `/api/analytics/recognition-summary` 的 feedback 块同源）；
5. **挽回成功率**：每条挽回点击事件，在同 `receipt_id` 的 `user_event` 中找 ts 更晚的 `receipt_review_submitted` 或 `receipt_approved` 即记成功——复用既有结果侧事件，不新增结果事件；
6. **最近事件流**：最近 50 条 `user_event`（ts、event_type、receipt_id、account_id、properties），供人工逐条核对。

**治理与埋点观测台（前端）**：侧边栏新增 `治理与埋点观测` 页签（`tab-analytics`，**admin 可见，staff/owner 隐藏**），顶部提供多租户（`#analyticsTenantSelect`）选择器，四区块：

- **全量埋点事件分布**：消费 `recovery-summary.event_distribution`，HTML 表格 + CSS 进度条渲染占比；
- **挽回与点踩指标**：消费 `recovery-summary` 的 `recovery / feedback / recovery_success / recent_events`，`low_confidence` 时标灰提示；
- **灰测现状**：复用 `GET /api/admin/grey-test`（admin 专属）；
- **A/B 实验数据**：复用 `GET /api/admin/experiments` + `GET /api/admin/experiments/{id}/pvalue`（与黄金样本看板同源端点，不重复造）。

可视化不引图表库，一律 HTML 表格 + CSS 进度条（对齐批量聚合进度条样式）。

---

## 8. 隐私与合规（PDPO 红线）

- `properties` **禁含 PII**：不写电话、身份证、银行账户、原始供应商全称；
- `rag_hit` 仅记 `context_len`，供应商名不落 user_event（RAG 内容在 `receipts.rag_context_json` 受单据级权限保护）；
- 聚合端点 admin 独占，输出仅为计数/分位/比率，天然脱敏；与 §10 脱敏中间件口径一致；
- 反馈文本经现有 feedback 端点 HTML 转义 + 长度截断后入库（复用既有校验）。

---

## 9. 设计-only 埋点清单（本期不实现）

| 事件 | 价值 | 不实现原因 / 触发条件 |
| :--- | :--- | :--- |
| `dwell_time` | Tab 停留时长 | 需 visibility API 全量监听，噪声大；待 DAU 稳定后启用 |
| `crop_rotate_tool` | 裁剪/旋转与识别成功率相关性 | 工具使用频次低，样本不足 |
| `side_by_side_dwell` | 对照视窗停留 | 依赖 hover 联动埋点改造，成本高 |
| `price_anomaly_adopted` | 涨价预警采纳率 | 待预警卡片交互改版后补 |
| `fifo_void_rate` | 餐品消耗冲销率 | 餐品模块刚上线，先观察 |

---

## 10. 验收标准

1. **单测**：`compute_review_diff` 6 用例（identical/modified/added+deleted/sku_changed/手工单/双空）全绿；
2. **端点**：`/api/track` 写入可查；`recognition-summary` staff→403、admin→200 且聚合数字与预置事件一致；funnel 含 `ocr_parse_started` 步骤；
3. **端到端**：上传→解析→改行/加行/删行→保存→approve 后，summary 的 rows_* / sku_changed / e2e 与操作一致；👍 有单→`receipt_feedback` 新增行，无单→`feedback_received` 事件；
4. **自查**：埋点服务端权威（✓ Job 状态机单点）、append-only（✓ user_event 只增不改）、PII 零暴露（✓ §8）、失败静默不阻塞主链路（✓ 全部 try/except）。

---

## 11. 产品决策记录 (Decision Log)

### D-2026-08-28-1：移除手动「作废」操作入口，作废态只读；埋点克制不新增事件

- **背景**：复核表与归档表曾并存「作废」与「删除」两个行操作。二者操作级效果重叠（都是「这行不算」），用户需理解「留痕 vs 不留痕」的记账差异才能正确选择，构成认知负担；真实场景中手动作废频次极低（票面划线由 AI 提取 `is_void` 自动呈现），而 AI 误识行的剔除才是高频操作。
- **决策**：用户剔除行的唯一操作为「删除」；「作废」态仅来自票面划线行的 AI 提取，只读展示（置灰 + 删除线 + 「作废」徽标），不可人工切换。复核表与归档表同步移除作废开关（`toggleRowVoid`/`toggleMainVoid`/`toggleArcVoid` 已删）。
- **口径**：作废行不计入整单总额、不参与算术门禁、**不入库不进成本**（同步修复 `apply_receipt_to_inventory` 入库口径，与门禁/总额对齐）。
- **埋点克制**：不为该交互变更新增埋点事件——行剔除沿用既有 `field_edited/row_remove`；「为体验克制埋点」原则：交互简化类变更不伴随新事件，避免事件目录膨胀稀释核心漏斗信号。
- **取舍**：放弃「用户手动作废某行并留痕」的边缘场景——误识行用删除覆盖、票面划线留痕由 AI 提取保障；若后续出现强留痕诉求，以审计日志（`audit_logs_json`）回溯，而非在操作层复活双概念。

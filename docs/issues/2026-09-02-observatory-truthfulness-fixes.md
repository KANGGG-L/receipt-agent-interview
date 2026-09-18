# 修复工单：治理与埋点观测台真实性治理（灰测样本伪造 / p 值计算 / PDPO 红线 / 租户口径 / session_id）

- 日期：2026-09-02
- 来源：观测台专项独立调查（7 项缺陷：1×P0、2×P1、2×P2、2×P3）
- 状态：已修复并通过全量回归（pytest 229 passed / 1 skipped）

## 背景

「治理与埋点观测台」（admin 专属）的聚合口径存在多处**数据伪造**与**规范违背**：
展示的灰测分组、AI 采纳率、p 值都不是真实计算结果，且埋点违反产品 Spec §8 隐私红线。
本次修复原则：观测数据必须可追溯到真实列/真实事件；无数据时如实标注「无法比对」，
不再用兜底值伪装成有数据。

## 修复清单

### 缺陷 1（P0）：灰测样本数据半伪造
- **缺陷**：`GET /api/admin/grey-test/samples` 三处造假——(a) 用单据 `id % 2 == 1` 伪造灰测分组；
  (b) `ai_prefill` 缺失时拿用户最终录入数据构造「AI 初始解析镜像」再和自己比对，刷出 100% 完全采纳；
  (c) 兜底值伪造（日期 `2026-08-20`、`math_gate_passed: True` 硬编码、`created_at` 假时间）。
- **根因**：前端观测台需要「每个样本都有评估结论」，后端为避免空值直接伪造数据补位。
- **修复**（`demo/app/api_admin.py` get_grey_test_samples）：
  - `use_grey` 只读真实列 `r.use_grey`（api_admin.py:1045），删除 id 奇偶逻辑；
  - 删除镜像伪造块：无真实 `ai_prefill.supplier_name` 时 `feedback_type="no_prefill"`、
    `feedback_label="无 AI 预填 · 无法比对"`、`match_rate=null`、`field_comparisons=[]`；
  - 统计口径收敛：`avg_match_rate` / `positive_feedback_count` / `modified_feedback_count`
    仅由有真实预填的样本贡献，新增 `prefill_available_count` 字段说明可比对样本数（api_admin.py:1167-1186）；
  - 兜底值清理：`date` 缺失→空串、`created_at` 缺失→空串、`math_gate_passed` 置 `null`（api_admin.py:1148-1151）。
- **前端适配**（`demo/static/js/main.js`）：`avg_match_rate` 与弹窗 match_rate 为 null 时显示「—」
  （原回退 100%）；弹窗 `field_comparisons` 为空时显示「无 AI 预填数据」占位行；弹窗 JSON 的
  `field_match_rate` 为 null 时输出 null（原会输出 "null%"）。
- **验证**：pytest 新增 `test_grey_samples_truthfulness`；起服后全量 1367 条样本逐一与
  `receipts.use_grey` 真实列核对，0 mismatch；无 prefill 样本全部 `no_prefill` 且 `match_rate=null`。

### 缺陷 2（P1）：AB 实验 p 值计算错误
- **缺陷**：`db.get_experiment_metrics` 内嵌 `_z_test` 的 `_phi` 用 Abramowitz 近似系数配了
  `e^(-x²/2)` 指数并做 `0.5*y` 折算，Φ 算错，p 值可 >1（实测 z=1.491 → p=1.1064，真值 0.136）。
- **根因**：把 erf 的近似公式（A&S 7.1.26）误当 Φ 用，指数项与折算系数双重错误。
- **修复**（`demo/app/db.py:3669-3672`）：`_phi(x) = 0.5 * (1 + math.erf(x / sqrt(2)))`（标准库精确实现）；
  双侧 p 改按 `|z|` 计算（treatment 低于 control、z<0 时同样成立）。
- **验证**：z=2.0 → p=0.04550；z=1.491 → p=0.13596；pytest 新增
  `test_experiment_metrics_pvalue_range_and_sensitivity`（z≈2 → p<0.05；z≈1 → p>0.3；全部 p∈[0,1]）。

### 缺陷 3（P1）：PDPO 红线——供应商原文写入埋点 properties
- **缺陷**：`POST /api/upload` 把用户输入的 `vendor_hint` 原文写进 `user_event.properties.engine_hint`，
  违反 11-组件Spec §8（properties 禁含供应商全称等 PII）。
- **修复**（`demo/app/api_receipts.py:311-317`）：改为 `{"has_vendor_hint": bool(vendor_hint), ...}`，
  只落布尔位不落原文；Spec §3.1 事件表第 1 行（receipt_uploaded）payload 同步补注
  `has_vendor_hint` 并明示不落供应商提示原文。全仓库 grep 确认其余埋点写入点
  （review diff、approve e2e_ms、guard 摘要、rag_hit context_len 等）均无供应商名/电话明文。

### 缺陷 4（P2）：recognition-summary 无视租户参数
- **缺陷**：`GET /api/analytics/recognition-summary` 查询 `user_event` 完全没有租户过滤，
  传任意 `tenant_id` 返回相同全量数据。
- **修复**（`demo/app/api_admin.py:1334-1341`）：用既有 `resolve_tenant_filter` 得到 effective_tenant，
  非 None 时给事件查询加 `tenant_id ==` 过滤；`list_receipt_feedbacks` 同样按租户过滤；
  响应 data 新增 `tenant_id` 字段（"all" 表示全租户聚合）。
- **验证**：pytest 新增 `test_recognition_summary_tenant_filter`（tenant_a/tenant_b 各一条事件，
  按租户查询各只计 1 条；nonexistent → 0 条）；curl 实测 nonexistent → parse_total=0。

### 缺陷 5（P2）：session_id 从未实现
- **缺陷**：Spec §3.0 要求 `user_event.session_id` 会话标识，但前端 `track()` 只发
  event_type/receipt_id/properties，后端 `TrackBody` 也不收 session_id，列永远为空。
- **修复**：前端 `main.js` 新增 `_demoSessionId()`（main.js:476-495）：首次生成
  `s_<时间戳base36>_<随机6位>` 并持久化 localStorage `demo_session_id`，之后每次上报携带；
  后端 `TrackBody` 增加 `session_id: Optional[str]`（api_receipts.py:1306），`track_event` 传入
  `_track_event → db.log_user_event`（api_receipts.py:1316）；`request.state.session_id` 既有路径保留为回退。
- **验证**：pytest 新增 `test_track_endpoint_persists_session_id`；curl 实测 POST /api/track 带
  session_id 后 `user_event` 最新行 session_id 落值（旧行为空串对比）。

### 缺陷 6（P3）：灰测样本表 XSS 转义缺口
- **缺陷**：`loadAdminGreySamples` 表格行用模板字符串裸插值 `masked_vendor` / `doc_form` /
  `masked_total` / `receipt_id` / `feedback_label`，违反 main.js「一切进 innerHTML 必须过转义」硬规则。
- **修复**（main.js:12717-12727）：全部插值改走 `w2Escape()`；badge 的 style 色值仍来自服务端常量，保留。

### 缺陷 7（P3）：金额/单价掩码函数缺陷
- **缺陷**：`mask_amount` 及明细行掩码用 `f"{v:.1f}"[:2] + ".**"`，对 100.0 产生 "10.**"
  （量级扭曲）、对 5.0 产生 "5..**"（非法格式）。
- **修复**（`demo/app/api_admin.py:995-1007`）：重写 `mask_amount`（`HK$ ` + 首位数字 + `**.**`，
  0 保留 `HK$ 0.00`）并新增同规则 `mask_number` 用于明细行 unit_price/amount
  （如 1234.56 → "HK$ 1**.**"，5.0 → "HK$ 5**.**"），保证不改数量级、不产生非法格式。

## 测试断言变更说明

无既有断言被修改或删除——既有测试没有覆盖灰测样本伪造行为与旧 _phi 实现的断言
（grep 验证 tests/ 下无 grey-test/samples、get_experiment_metrics、engine_hint 相关用例），
本次为纯新增回归用例 4 个：

1. `test_grey_samples_truthfulness`（test_demo.py）：use_grey 读真实列、no_prefill 如实标注、
   统计口径只计可比对样本、掩码不改数量级；
2. `test_experiment_metrics_pvalue_range_and_sensitivity`（test_experiment_guardian.py）：p 值
   方向正确且恒在 [0,1]；
3. `test_recognition_summary_tenant_filter`（test_demo.py）：租户过滤 + nonexistent → 0；
4. `test_track_endpoint_persists_session_id`（test_demo.py）：session_id 落库。

## 验证记录

- pytest 全量：**229 passed, 1 skipped**（基线 225 passed / 1 skipped + 新增 4 用例），0 fail。
- `node --check demo/static/js/main.js`：通过。
- curl 实测（临时起服 15010，验证后已 kill）：
  - `GET /api/admin/grey-test/samples`：1367 条样本 use_grey 与 `receipts.use_grey` 真实列
    **0 mismatch**（旧 id%2 逻辑会伪造出 ~684 条灰测）；`grey_count=0`、
    `prefill_available_count=0`、`avg_match_rate=null`（库内无 ai_prefill，全部如实 no_prefill）；
    prefill 有值路径（thumbs_down/match_rate/统计）由 pytest 用例覆盖。
  - `POST /api/track` 带 session_id → user_event 最新行 session_id 落值，旧行为空串。
  - `GET /api/analytics/recognition-summary?tenant_id=nonexistent` → `data.tenant_id=nonexistent`、
    `parse_total=0`；不带参数 → `tenant_id=all`、`parse_total=11`（真实过滤）。
  - p 值：pytest 用例佐证（z=2.0 → p≈0.0455）。

## 遗留与建议

- `math_gate_passed` 字段后端置 null（无逐单真实门禁结果来源）；如需真实值，后续应把
  supervisor 算术门禁结果持久化到单据级再回填，本工单不扩范围。
- 库内 1367 条存量单据均无 `ai_prefill_json`，观测台「AI 采纳率」在真实预填回流前将如实
  显示「无 AI 预填 · 无法比对」（均值显示「—」）——这是预期行为，不是回归。
- 灰测样本比较基于脱敏字符串（首位数字掩码），比对粒度较粗属既有设计，未在本工单调整。

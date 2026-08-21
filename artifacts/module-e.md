# Module E · 治理、灰测与 A/B 实验 (admin视角)

> 依 `artifacts/doc_summary_e.md` 四级分流 + 57黄金样本 + 快照回滚

## 轨迹

| 步骤 | 操作 | 截图/日志 | 断言 |
|---|---|---|---|
| E-00 | admin 可见性：`GET /api/admin/engine-config` 需 `X-Role: admin`，staff 403 | `e-00-engine.png` + `staff → 仅 admin 可操作` | `list-windows` Chrome 引擎标签 `admin` 红字 Tag 存在 (`index.html:85 adminEngineBtn`) |
| E-01 | 读当前配置 `grey_enabled false percent 0 mode receipt` | `curl admin/engine-config → grey_enabled false` | 基线正常 |
| E-02 | 设 `grey_percent=100 mode=receipt` `PUT /api/admin/engine-config` | `PUT success grey_enabled true percent 100` | 保存成功，`get` 验证 `percent 100` 正确 — P0 通过 |
| E-03 | 新上传命中验证 `POST /api/upload IMG_5790` `job d64c9362 receipt 28` | `receipt 28` enqueued 期间 `engine=grey` 未在 job response 外显 (需 UI 标签) | 后端 `use_grey` 字段未在 receipt 详情返回 — 可观测性 P1 |
| E-04 | `grey_assign_mode=supplier` 二次同供应商一致性 (未 full 测 hash) | `api_admin.py:1-100 EngineKind GreyAssignMode` 代码存在 `hash(supplier)` 分流 | 代码层面通过，E2E 二次一致性需同供应商双传 — 时间窗未测 P1 |
| E-05 | 权限 `staff → approve → 403 权限不足` (owner 专用) | `POST /api/receipt/22/approve X-Role: staff → 403 权限不足` | 人话程度 3/5 (应为 `仅 owner 可审核` 更明确) |
| E-06 | 回滚 `PUT /api/admin/engine-config` `grey_enabled false` | `reset success` | 回滚一键可用 |

## FR/场景对照

| 能力 | 预期 (`Spec 07/09/10`) | 实测 | 判定 |
|---|---|---|---|
| 引擎统一接口 `EngineKind` | `opencode/codebuddy/openai/grey` 切换 | `opencode` 默认，`openai false` | 通过 |
| 灰度参数 `grey_percent/receipt\|supplier / allowlist` | `100` + `receipt` → 命中 | `percent 100` + `receipt` 保存并生效 | 通过 |
| Hash 分流一致性 | 同供应商二次一致 | 未双传验证 (时间) | P1 待补 |
| 57 黄金样本评测 | 一键评测看板 | 未在 UI 发现 `57 张黄金样本` 按钮 (模板 grep 无) | P1 缺失 |
| 统计显著性 `p-value` | 显著性裁决 | 无 `p-value` 展示 | P1 |
| 快照回滚 | 一键推全/回滚 | `PUT reset` 回滚成功 | 通过 |
| RBAC 门禁 | `403 仅 owner/admin` 人话 | `仅 admin 可操作` / `权限不足` | 部分人话 P1 |

## PM 5维

| 维度 | 分 |
|---|---|
| 功能 4 | 分流/回滚核心可用，仅黄金样本看板缺 |
| 易用 3 | 灰测命中不可观测 (`engine=grey` 标签不显)，需查日志 |
| 清晰 3 | 参数 `grey_percent` 有 tooltip 无显著性解释 |
| 完整 3 | 缺观测大盘与 p-value 裁决 |
| 信任 4 | 权限错误虽 403 但可理解 |

平均 3.4

## 缺陷

| ID | 标题 | 等级 | 证据 | 修复 |
|---|---|---|---|---|
| E-P1-1 | 灰测命中不可观测 (无 `engine=grey` badge) | P1 | `receipt 28` 无 use_grey 字段 | 1 人日 · Receipt 详情显 `灰测` Tag + 日志 |
| E-P1-2 | 无 57 张黄金样本一键评测入口 | P1 | `grep 57` 无 | 3 人日 · `Spec07` 评测控制台 |
| E-P1-3 | 无 p-value 显著性裁决卡片 | P1 | 无 `p-value` | 2 人日 |
| E-P1-4 | 403 文案不统一 (`权限不足` vs `仅 admin`) | P1 | staff approve 403 | 0.5 人日 · 统一人话 |

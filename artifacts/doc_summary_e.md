Phase0 Done: E

# SubAgent-E · 治理、灰测与 A/B 实验 — Phase0 文档通读摘要

> 精读三份治理 Spec 与三处实现源码，产出映射表与一句话总结。

## 业务一句话总结

PGM 香港 ERP 的 AI 治理底座：通过 `EngineKind(opencode/codebuddy/openai)` 热插拔、`grey_enabled/ grey_percent/ grey_assign_mode(receipt|supplier) + grey_supplier_ids allowlist` 四级分流、`57 张黄金样本一键导入与覆盖率` 基准、`p-value/Z` 显著性观测大盘、`PUT /api/admin/engine-config/promote → rollback_snapshot → rollback` 秒级推全回滚，以及 `require_admin / require_role(owner)` 人话 403，保障新引擎「灰而不险、问题一键退」且脱敏可观测。

## 映射表（Spec → 代码 → 验收）

| 规格 | Spec 关键概念 | 实现代码 | 前端/接口载体 | 验收要点 |
|---|---|---|---|---|
| 07-AI治理与评测控制台 | 统一 `BaseChatModel invoke` 三引擎适配 `Opencode/CodeBuddy/OpenAI` | `demo/app/llm.py:_get_opencode_bin/_get_codebuddy_bin` 、 `demo/app/models.py:170 EngineKind` | `templates/index.html#adminEngineBtn/tab-engine` 识别/审核/解析三组独立选型 | 切换 `recognition_engine/codebuddy/openai` 保存生效，`openai` 独立 `base_url/api_key/model` |
| 07-灰度分流 | `grey_enabled + grey_percent + grey_assign_mode receipt/supplier + supplier白名单` | `demo/app/models.py:223 should_use_grey` 、 `demo/app/db.py:AppSettingRow engine_config` 、 `demo/app/api_admin.py:37 EngineConfigBody grey_*` | `GET/PUT /api/admin/engine-config` 、 `GET /api/admin/grey-test` | `grey_percent=100 + receipt` 命中灰测，`supplier` 同名 hash 一致 |
| 09-AB全链路分流体系 | 对照组/实验组物理隔离、流量四级路由 `全局→白名单→receipt/supplier`、推全回滚 `<50ms` | `demo/app/models.py:152 EngineConfig grey_*` 、 `ai_registry/canary/router.py` | `PUT /api/admin/engine-config/promote` 、 `PUT /api/admin/engine-config/rollback` 、 `GET /api/admin/engine-config/diff` | 推全前存 `rollback_snapshot`，回滚原子覆盖，diff 脱敏对比 |
| 09-统计显著性 | `p-value<0.05 95%置信`、`N>=100`、Welch t / Two-proportion Z | `demo/app/db.py:1231 _zscore / 1269 _two_proportion_ztest` 、 `demo/app/api_admin.py:848 pvalue` | `GET /api/admin/experiments/{id}/pvalue` 、 `tab-golden` 看板 `p-value` 卡片 | `p<0.05` 显著提示，`low_confidence` 样本不足警告 |
| 10-Admin脱敏观测大盘 | PDPO 私隐私红线、供应商匿名化 `蔬菜供应商_A89`、金额指数化、PII 抹除 | `demo/app/api_admin.py:592 get_grey_test_samples mask_vendor/mask_amount` 、 `demo/app/api_receipts.py _mask_sensitive` | `tab-engine#adminGreySamplesBody` + `tab-golden` | `masked_vendor/masked_total` 脱敏，`effect_evaluation` 采纳率徽色 |
| 07+10-黄金样本57 | 57张真实单 `22手写+15印刷+10热敏+6更正+4月结`、GroundTruth、批量评测 | `demo/app/api_admin.py:780 golden-samples` 、 `scripts/import_golden.py` | `GET /api/admin/golden-samples` 、 `POST /api/admin/golden-samples/import` | 目标覆盖率 `target 57` 、`currency_breakdown`、`items` 最近57行 |
| 07/09/10-RBAC | `Admin独占 403`、`仅 admin可操作/仅 owner可审核` 人话、前端隐藏 | `demo/app/auth.py:88 require_role /104 require_admin` 、 `demo/static/js/main.js:4080 toastHttpError` | `#adminEngineBtn admin红字Tag` 、 `#demoRoleSelect`、toast 403 人话 | `staff→GET /api/admin/engine-config 403 仅 admin` 、`staff→POST /api/receipt/{id}/approve 403 仅 owner可审核` |
| 安全与幂等 | 57 Append-Only幂等归一、三层互不信任、人话失败出口 | `demo/app/api_receipts.py contract门禁` 、 `demo/app/db.py audit_logs` | `detail modal approve/flag` 失败显式出口 | `人工复核修改率`、`算术门禁100%拦截` 看板 |

## 关联文档

- `docs/05-AI产品体系与模块Spec/03-治理运维与AB实验Spec/07-组件Spec-AI治理、多模型灰度发布与评测控制台.md`
- `docs/05-AI产品体系与模块Spec/03-治理运维与AB实验Spec/09-组件Spec-AB测试与全链路灰测分流体系.md`
- `docs/05-AI产品体系与模块Spec/03-治理运维与AB实验Spec/10-组件Spec-Admin端AB测试与脱敏观测大盘方案.md`
- `demo/app/api_admin.py:1-100 EngineConfigBody extra="forbid" + EngineKind/GreyAssignMode`
- `demo/app/models.py:170 class EngineKind/GreyAssignMode should_use_grey hash分配`
- `demo/app/db.py AppSettingRow engine_config + rollback_snapshot + ai_decision_log + _two_proportion_ztest`


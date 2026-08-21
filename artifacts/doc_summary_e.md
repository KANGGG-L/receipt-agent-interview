# SubAgent-E · 治理、灰测与 A/B 实验 — 文档通读摘要

Phase0 Done: E 摘要已产出

## 一句话业务总结
四级分流（receipt|supplier + grey_percent + allowlist）+ 57 张黄金样本一键评测 + 快照回滚，让新模型灰而不险、问题一键退。

## FR/场景映射表

| 能力 | UI/配置 | 后端/分流 | 验收 |
|---|---|---|---|
| 引擎统一接口 | 侧边栏 `button:has-text("[设置]")` → `div.modal:has-text("引擎配置")` | `EngineKind` `opencode/codebuddy/openai/grey` | staff 不可见，admin 可见 |
| 灰度参数 | `input#grey-percent` `grey_assign_mode` 下拉 `receipt/supplier` `供应商 allowlist` | `demo/app/api_admin.py:1-100` `demo/app/models.py:170` | `grey_percent=100` + `receipt` → 新上传命中 `engine=grey` |
| Hash 分流 | 同供应商二次上传一致命中 | `supplier` 模式 hash(supplier) 决定 | 二次一致性 |
| 统计显著性 | 57 张黄金样本评测看板 | `p-value` 显著性检验 | 脱敏观测大盘可观测命中 |
| 快照回滚 | `回滚` 按钮 | 参数快照物理隔离 | 一键恢复 |
| RBAC 门禁 | staff 点 `Approve` | `auth.py` 鉴权 | `toast.error 403 仅 owner` 人话提示 |

## 关联
`07-组件Spec-AI治理、多模型灰度发布与评测控制台.md` + `09-组件Spec-AB测试与全链路灰测分流体系.md` + `10-组件Spec-Admin端AB测试与脱敏观测大盘方案.md`

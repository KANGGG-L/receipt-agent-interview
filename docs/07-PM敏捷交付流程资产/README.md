# 07 · PM 敏捷交付流程资产（Agile Delivery Portfolio）

> **用途**：本文件夹将「PGM 香港餐饮 AI 进货收据与库存中台」项目按 **Project Management / Agile Delivery** 视角重新组织，覆盖 PM 岗位申请所需的九大交付要素。所有内容均锚定仓库内真实文档、测试报告与 git commit，可作为面试 / 简历的可验证证据链。

---

## 项目一句话

面向香港餐饮店老板的「订货收据 → 实时库存 → 运营成本」中台：VLM 识别收据（允许出错）→ 契约 + 算术双门禁校验 → Side-by-Side 人工复核审批后才写入库存。项目周期 **2026.05 – 2026.09**（PGM 实习主导子模块 + 持续迭代），最终阶段综合验收 **4.92/5.0**，测试基线 **730 passed / 58 skipped**。

---

## 九大要素导航表

| # | Agile 要素 | 文档 | 核心证据来源 |
| :- | :--- | :--- | :--- |
| 1 | Workflow / Process Flow | [`01-工作流程与流程图.md`](01-工作流程与流程图.md) | 14 步 PRD 流程、阶段 0-4 迭代流程 |
| 2 | User Stories | [`02-用户故事.md`](02-用户故事.md) | 4 类用户画像、17 个业务场景清单 |
| 3 | Acceptance Criteria | [`03-验收标准AC.md`](03-验收标准AC.md) | `docs/05-AI产品体系与模块Spec/05-增量模块PRD与ACSpec/EXPORT_CENTER_PRD_AC_SPEC.md`、`docs/05-AI产品体系与模块Spec/05-增量模块PRD与ACSpec/DISHE_MODULE_REPAIR_AC.md` |
| 4 | Test Cases | [`04-测试用例.md`](04-测试用例.md) | `docs/09-测试与UAT报告/test_report_qa_round2.md`、pytest/Playwright 基线 |
| 5 | UAT | [`05-UAT用户验收测试.md`](05-UAT用户验收测试.md) | QA Round 1（PASS）/ Round 2（FAIL 驳回）真实实例 |
| 6 | 跟开发沟通和跟进 | [`06-开发沟通与跟进.md`](06-开发沟通与跟进.md) | issue 工单、交接文档、commit 沟通案例 |
| 7 | Sprint / Milestone / Task | [`07-Sprint里程碑与任务追踪.md`](07-Sprint里程碑与任务追踪.md) | 阶段 0-4、Wave A-E、T1-T12 任务台账 |
| 8 | 上线 / Rollout / Iteration | [`08-上线Rollout与迭代.md`](08-上线Rollout与迭代.md) | step14 Go/No-Go、灰测分流、回滚 Runbook |
| 9 | Bug / Issue Tracking | [`09-Bug与Issue管理.md`](09-Bug与Issue管理.md) | `docs/issues/` 4 份工单、P0-P3 缺陷台账 |

---

## 如何在 PM 申请中使用

1. **简历**：直接引用各文档中的量化数字（163 张真实单据识别、单张成本 HK$0.0024、终验 4.92/5.0、730 tests passed、QA 驳回-修复-复验闭环等）。
2. **面试 STAR 叙事**：每份文档末尾附「面试叙事要点」，可按 Situation-Task-Action-Result 展开。重点讲三个故事：
   - **QA Round 2 一票否决驳回**（05 文档）：发现币种混杂与弹窗选择器缺陷，拒绝签字放行，给出根因定位与修复指引，复验通过——展示质量把关魄力。
   - **观测台真实性治理 P0**（09 文档）：发现灰测指标被"美化"（伪造分组刷 100% 采纳率），推动 7 项缺陷如实修复——展示数据诚实与治理能力。
   - **审核腿模型选错被指正**（06 文档）：开发选了非视觉模型 DeepSeek-V3.2 做图片审核，被用户当场指正后 48h 内切换 GLM-4.5V 并补回归——展示跨职能沟通与快速纠偏。
3. **Portfolio 讲解顺序**：建议 01（流程）→ 07（节奏）→ 02/03（需求侧）→ 04/05（质量侧）→ 06/09（协作与风险）→ 08（发布），约 15 分钟讲完一条完整交付主线。

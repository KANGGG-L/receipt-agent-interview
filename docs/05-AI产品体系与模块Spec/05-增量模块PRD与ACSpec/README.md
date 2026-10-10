# 05 · 增量模块 PRD 与 AC Spec

> **模块定位**：产品体系主体（00~04 子目录）定稿之后，按迭代节奏新增的模块级 PRD / 验收标准规格书。写法遵循「业务定位 → 用户画像 → AC 量化条款 → 测试矩阵 → 实施路线」结构，是缺陷修复类 AC 的模板来源。

## 文档索引

| 文档 | 一句话说明 |
| :--- | :--- |
| [07-引擎配置高级参数统一展示与组件化重构探索任务.md](07-引擎配置高级参数统一展示与组件化重构探索任务.md) | 引擎配置高级参数统一展示与组件化重构的**探索任务书**（六卡 DOM/前后端契约/测试边界排查指引） |
| [08-Codebase全景架构与统一化重构蓝图.md](08-Codebase全景架构与统一化重构蓝图.md) | 上述任务的**交付物**：Codebase 全景架构与端到端数据流、六卡参数对比矩阵、组件化与事件委托重构方案、Zero Regression 兼容底线与债务分期路线 |
| [DISHE_MODULE_REPAIR_AC.md](DISHE_MODULE_REPAIR_AC.md) | 「餐品与消耗」模块缺陷修复与产品提升需求拆解与验收标准（AC-1.1~AC-8.5，覆盖 Dishes / BOM / 每日消耗 / FIFO 成本扣减） |
| [EXPORT_CENTER_PRD_AC_SPEC.md](EXPORT_CENTER_PRD_AC_SPEC.md) | 统一报表与数据导出中心 PRD 与验收准则（v2.0.0：3 个使用者组 × 8 张报表、UTF-8 BOM 防乱码导出、A4 盘点打印；报表清单以需求分析第 7 节为准） |
| [EXPORT_CENTER_TABLE_REFACTOR_NEEDS_ANALYSIS.md](EXPORT_CENTER_TABLE_REFACTOR_NEEDS_ANALYSIS.md) | 数据中心表格整理与重构需求分析：14 → 8 张报表的第一性原理判定矩阵、缺陷清单与生产场景重设计（现行报表清单事实源） |

> 对应的真机复验报告见 [`docs/09-测试与UAT报告/test_report_qa_round2.md`](../../09-测试与UAT报告/test_report_qa_round2.md)（QA Round 2）。

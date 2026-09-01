# 整理“引擎配置与灰测大盘”测试组件至“埋点观测”设计方案

## 1. 业务背景与目标

当前系统中，`#tab-engine`（引擎配置）中混入了「灰测脱敏单据抽样与效果评估」等测试与监控大盘组件，导致配置项与运行态观测大盘职责混杂；而 `#tab-analytics`（埋点观测台）中的「灰测现状」与「A/B 实验」又仅为简略文字摘要。
本次改造目标：
1. **职责单一化**：将 `#tab-engine` 纯化为「引擎与系统配置」（聚焦引擎模型参数、网关、灰测规则分流及系统阈值），侧边栏名称调整为「引擎与系统配置」；
2. **大盘能力聚合**：将「灰测单据与用户使用状态抽样观测面板」及「脱敏解析详情弹窗」统一收敛至 `#tab-analytics`（埋点观测台）的「灰测现状与样本观测」区块中，实现埋点、挽回、灰测抽样流和 A/B 实验一站式观测。

---

## 2. 详细改造范围

### 2.1 DOM 结构调整（`demo/templates/index.html`）
1. **侧边栏导航文案**：
   - `#adminEngineBtn`：文案从「引擎与灰测」调整为「引擎与系统配置」，`data-title="引擎与系统配置"`。
2. **`#tab-engine` 清理**：
   - 移出「灰测用户使用状态与脱敏单据抽样观测面板 (Admin 专属)」HTML 结构（`#adminGreySampleMetrics`, `#adminGreySamplesBody`, 刷新按钮等）。
3. **`#tab-analytics` 增强**：
   - 在「灰测现状」卡片中嵌入指标卡片组（总样本 `mGreyTotal`、已审批 `mGreyApproved`、店员已编辑 `mGreyEdited`、赞同 `mGreyPositive`、纠偏 `mGreyModified`、平均采纳率 `mGreyAvgMatch`、灰测组 `mGreyCanary`）与脱敏单据抽样数据表格（`#adminGreySamplesBody`）。
   - 保留脱敏切片解析弹窗 `#adminGreySampleModal`。

### 2.2 前端脚本联动（`demo/static/js/main.js`）
1. **`loadAnalyticsBoard(tenantId)`**：
   - 同时调用 `loadRecoverySummaryBlocks(tenantId)`、`loadAdminGreySamples(tenantId)` 与 `loadAnalyticsExperiments(tenantId)`；
   - 切换租户时，单据抽样流与指标同步刷新。
2. **`loadAdminEngineConfig`**：
   - 移除在引擎配置保存/加载时非必要的观测表格渲染逻辑，保持引擎配置轻量化。

### 2.3 规范与文档同步（`docs/`）
- 更新组件 Spec 文档中的结构与职责划分描述。

---

## 3. 验证方案

1. **测试套件回归**：全量运行 218 个 pytest 用例，确保所有 API 与数据逻辑正常；
2. **Playwright 端到端浏览器验证**：
   - 检查侧边栏文案「引擎与系统配置」；
   - 检查 `#tab-engine` 仅包含引擎配置、灰测参数与系统阈值，无单据抽样流；
   - 检查 `#tab-analytics` 包含全量事件分布、挽回指标、灰测指标卡片与脱敏单据流表格，点击「刷新」与租户筛选均能正常工作；
   - 截图保存至 Artifacts 目录。

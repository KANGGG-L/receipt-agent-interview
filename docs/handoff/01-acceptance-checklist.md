# 全量 45 项原子任务可执行验收标准清单 (Executable Acceptance Checklist)

## 1. 验收执行总则与质量红线

1. 独立 Subagent 隔离执行：每一项任务必须且仅能由独立的 Subagent 串行推进，严格遵循「产品明确 Spec -> 开发编码 -> 独立 QA 自动化验证 -> 独立 UAT 真实运行时验收」四阶段，严禁自我宣布完成或跳过门禁。
2. 真实运行时证据：UI 验收必须使用 Orca 内嵌真实浏览器针对独立临时隔离端口（15100-15999）进行渲染与交互，严禁使用旧快照、静态代码假设或 mock 伪造运行时证据。
3. 零 Emoji 规范：所有界面文案、状态标签、测试断言及交互反馈中严禁出现任何 Emoji 表情符号，状态一律使用标准纯文本标签（如 [OK], [FAIL], [WARN]）。
4. 数据脱敏规范：商业敏感数据（价格掩码 $***、供应商名称别名 Vendor_A、身份证正则脱敏）严格落实，严禁明文污染日志或界面。
5. 基础设施保护：宿主机常驻开发服务端口 15010 严禁停止、覆写或发起重型压测，临时服务在每轮测试后必须主动收敛杀死，释放端口与内存。

---

## 2.1 波次 D0：Spec 澄清与合同保留波次 (4 项，SPEC_KEEP，不修改产品代码)

### [UAT-012] staff餐品与导出入口：撤回原计划违约判断，保留派生Spec差异
- 当前状态：`[DONE] PASS (D0 SPEC_KEEP 门禁已闭环)`
- 优先级：`P1` | 任务分类：`SPEC_CONFLICT` | 验证模式：`SPEC_DECISION`
- 前置依赖：`[]`
- 涉及源码文件：`[]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 记录DEFAULT_ORIGINAL或APPROVED_EXTENSION裁决、原canonical条款及owner。
  1. 保留staff仅识别复核+库存查看；保留原盘点报表HTTP staff200合同，默认不添加每日消耗/数据中心UI。
  1. 可选分支未批准不得宣称新功能完成。
- 负向与安全边界 (Negative Boundary & Security)：
  - 未有明确新增合同批准不得按派生Spec修改产品；默认分支只修验收规则及索引。
  - 保留staff仅识别复核+库存查看；保留原盘点报表HTTP staff200合同，默认不添加每日消耗/数据中心UI。
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["B2-2", "I18N-04", "I18N-05", "S1-P1-06"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-014] 记忆恢复、一致性与安全事件没有可达UI
- 当前状态：`[DONE] PARTIAL (D0 SPEC_KEEP 门禁已闭环，保留未运行标注)`
- 优先级：`P2` | 任务分类：`SPEC_CONFLICT` | 验证模式：`SPEC_DECISION`
- 前置依赖：`[]`
- 涉及源码文件：`[]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 记录DEFAULT_ORIGINAL或APPROVED_EXTENSION裁决、原canonical条款及owner。
  1. 仅按原S2-19核验HKID日志脱敏，默认不添加记忆恢复/一致性/安全事件UI。
  1. 可选分支未批准不得宣称新功能完成。
- 负向与安全边界 (Negative Boundary & Security)：
  - 未有明确新增合同批准不得按派生Spec修改产品；默认分支只修验收规则及索引。
  - 仅按原S2-19核验HKID日志脱敏，默认不添加记忆恢复/一致性/安全事件UI。
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S2-11", "S2-19"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-016] 当前UAT Spec硬删边界与原计划owner回收策略不一致
- 当前状态：`[DONE] PARTIAL (D0 SPEC_KEEP 门禁已闭环，保留未运行标注)`
- 优先级：`P2` | 任务分类：`SPEC_CONFLICT` | 验证模式：`SPEC_DECISION`
- 前置依赖：`[]`
- 涉及源码文件：`[]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 记录DEFAULT_ORIGINAL或APPROVED_EXTENSION裁决、原canonical条款及owner。
  1. 保持现有owner回收/恢复及purge行为；不以不存在的admin-only原要求修改授权。
  1. 可选分支未批准不得宣称新功能完成。
- 负向与安全边界 (Negative Boundary & Security)：
  - 未有明确新增合同批准不得按派生Spec修改产品；默认分支只修验收规则及索引。
  - 保持现有owner回收/恢复及purge行为；不以不存在的admin-only原要求修改授权。
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S1-P1-12", "S1-P1-13", "S3-07", "S4-12"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-026] 库存部门编辑/过滤是派生UAT新增范围，产品未提供
- 当前状态：`[DONE] PASS (D0 SPEC_KEEP 门禁已闭环)`
- 优先级：`P2` | 任务分类：`SPEC_CONFLICT` | 验证模式：`SPEC_DECISION`
- 前置依赖：`[]`
- 涉及源码文件：`[]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 记录DEFAULT_ORIGINAL或APPROVED_EXTENSION裁决、原canonical条款及owner。
  1. 保留部门在receipt明细与成本分摊实体；默认不新增SKU部门编辑/库存部门过滤。
  1. 可选分支未批准不得宣称新功能完成。
- 负向与安全边界 (Negative Boundary & Security)：
  - 未有明确新增合同批准不得按派生Spec修改产品；默认分支只修验收规则及索引。
  - 保留部门在receipt明细与成本分摊实体；默认不新增SKU部门编辑/库存部门过滤。
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["库存部门编辑/过滤是派生UAT新增范围，产品未提供", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。


## 2.2 波次 W1：工程基建与高优先级核心业务缺陷修复 (9 项，当前进行至 UAT-021，已闭环 6 项：ENG-01, ENG-02, ENG-03, UAT-004, UAT-011, UAT-015)

### [ENG-01] 根测试硬编码15010健康探测超时
- 当前状态：`[DONE] PASS (W1 工程测试门禁已闭环)`
- 优先级：`P1` | 任务分类：`ENGINEERING_GATE` | 验证模式：`MANUAL_COMMAND`
- 前置依赖：`[]`
- 涉及源码文件：`["tests/test_phase3_filter_adjustments_deep_qa.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 重用qa/logs/tests.xml failure精确用例在隔离health200下通过
  1. 保留非200/超时可正确失败的测试行为
  1. 所有测试不得写15010
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 重用qa/logs/tests.xml failure精确用例在隔离health200下通过；保留非200/超时可正确失败的测试行为；所有测试不得写15010
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["根测试硬编码15010健康探测超时", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境注入参数运行 pytest 定向测试套件 (`tests/...`)。

### [ENG-02] 旧自动预分析勾选框断言
- 当前状态：`[DONE] PARTIAL (W1 工程测试门禁已闭环，模型路径保留未运行)`
- 优先级：`P1` | 任务分类：`ENGINEERING_GATE` | 验证模式：`MANUAL_COMMAND`
- 前置依赖：`[]`
- 涉及源码文件：`["tests/test_playwright_e2e.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 实际浏览器正常图片路径不找不存在checkbox
  1. 断言图片→启动识别真实请求与结果
  1. 异常画质路径仍按原合同
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 实际浏览器正常图片路径不找不存在checkbox；断言图片→启动识别真实请求与结果；异常画质路径仍按原合同
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["旧自动预分析勾选框断言", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境注入参数运行 pytest 定向测试套件 (`tests/...`)。

### [ENG-03] 手工模式预填徽章旧断言
- 当前状态：`[DONE] PARTIAL (W1 工程测试门禁已闭环，模型路径保留未运行)`
- 优先级：`P1` | 任务分类：`ENGINEERING_GATE` | 验证模式：`MANUAL_COMMAND`
- 前置依赖：`[]`
- 涉及源码文件：`["tests/test_workbench_e2e_playwright.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 手工模式aiPrefillNoticeBadge隐藏
  1. 真实人工填写保存成功
  1. 不能把真正AI预填显示回归为隐藏
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 手工模式aiPrefillNoticeBadge隐藏；真实人工填写保存成功；不能把真正AI预填显示回归为隐藏
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["手工模式预填徽章旧断言", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境注入参数运行 pytest 定向测试套件 (`tests/...`)。

### [UAT-004] 供应商可选电话和备注留空时保存422
- 当前状态：`[DONE] PASS (W1 独立开发/QA/UAT 完整闭环)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`AUTO_API`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/supplier_archive.js", "demo/app/api_suppliers.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 仅name=Vendor_A其余UI默认点击保存，HTTP成功且字段空串/明确规范空值
  1. 填两字段后读回
  1. HTTP null/遗漏/空串分别覆盖
  1. 显式空结算依旧按既有422
- 负向与安全边界 (Negative Boundary & Security)：
  - 422证据只指contact_phone/notes；不放宽settlement必填、不可改贷项/审批规则
  - 仅name=Vendor_A其余UI默认点击保存，HTTP成功且字段空串/明确规范空值；填两字段后读回；HTTP null/遗漏/空串分别覆盖；显式空结算依旧按既有422
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S1-P0-05"]
- 验证方式与测试工具：
  - 自动化 API 探针 (`test_entry.py run`) + 真实浏览器交互操作核查。

### [UAT-011] GT确权按钮同时创建真实业务待审单
- 当前状态：`[DONE] PASS (QA 与 UAT 双门禁已闭环)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/eval_workbench.js", "demo/static/js/review_core.js", "demo/app/api_evalset.py"]`
- 验收凭据与报告：
  - 独立 QA 验证报告：`02-uat011-qa/qa-report.md`
  - 独立 UAT 运行时验收报告：`03-uat011-uat/uat-report.md`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 重用gt-single-click脚本与合成U001
  1. 真实仅click一次捕获所有POST及DB前后：GT confirm恰1，save_edited零、receipts/items/inventory/payments零变
  1. 连续双击GT也不重复业务写
  1. 离开eval回正常手工可保存
- 负向与安全边界 (Negative Boundary & Security)：
  - 不能只去重GT请求遗漏业务save_edited；GT与业务DB隔离
  - 重用gt-single-click脚本与合成U001；真实仅click一次捕获所有POST及DB前后：GT confirm恰1，save_edited零、receipts/items/inventory/payments零变；连续双击GT也不重复业务写；离开eval回正常手工可保存
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S2-07", "S2-16", "S2-24", "S2-27", "S3-23"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-015] 外币应付金额被错误标为港币原值
- 当前状态：`[DONE] PASS (W1 独立开发/QA/UAT 完整闭环，DEV/QA/UAT 全量 PASS)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/finance.js", "demo/static/js/finance_ops.js", "demo/app/api_finance.py", "demo/app/db.py"]`
- 验收凭据与报告：
  - 开发交付报告：`04-uat015-dev/dev-report.md`
  - 独立 QA 验证报告：`05-uat015-qa/qa-report.md`
  - 独立 UAT 运行时验收报告：`06-uat015-uat/uat-report.md`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 明确合成SGD100、snapshot5→HKD500，归档原币S$100，应付/支付HK$500
  1. 港币部分付100→未付400，全付→0
  1. 修改实时FX后历史仍snapshot值
  1. 缺snapshot legacy提示未知不硬标HKD
- 负向与安全边界 (Negative Boundary & Security)：
  - 不以改标签掩盖本金/付款跨币错算；保持付款余额边界与租户隔离
  - 明确合成SGD100、snapshot5→HKD500，归档原币S$100，应付/支付HK$500；港币部分付100→未付400，全付→0；修改实时FX后历史仍snapshot值；缺snapshot legacy提示未知不硬标HKD
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S1-P0-04", "S1-P0-07", "S1-P1-01", "S1-P1-08", "S1-P2-10", "S3-22"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-021] 店员库存列表与餐品列表泄露价格及毛利
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/app/api_inventory.py", "demo/app/api_dishes.py", "demo/static/js/inventory_board.js", "demo/static/js/dish_bom.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 真实staff cookie刷新inventory/dishes列表读完整JSON及页面：last_unit_price/price/gross_profit/gross_margin_rate和所有同义敏感字段null或不返回
  1. 详情仍null
  1. owner同fixture价格可见
  1. 伪owner头不能突破
- 负向与安全边界 (Negative Boundary & Security)：
  - 数量与单位staff可看；不以导航隐藏代API权限、不修改已正确详情脱敏
  - 真实staff cookie刷新inventory/dishes列表读完整JSON及页面：last_unit_price/price/gross_profit/gross_margin_rate和所有同义敏感字段null或不返回；详情仍null；owner同fixture价格可见；伪owner头不能突破
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["店员库存列表与餐品列表泄露价格及毛利", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-028] 停用供应商会清空名称导致无法按原名回看
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`AUTO_API`
- 前置依赖：`["UAT-004"]`
- 涉及源码文件：`["demo/app/api_suppliers.py", "demo/static/js/supplier_archive.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. Vendor_A新增→改名Vendor_B→PATCH active0→showInactive查询，name仍Vendor_B且原引用不变
  1. PATCH notes不影响name/phone，省略与显式清空分别测试
- 负向与安全边界 (Negative Boundary & Security)：
  - 空name创建/显式无效更新不得因部分patch变宽松；tenant隔离
  - Vendor_A新增→改名Vendor_B→PATCH active0→showInactive查询，name仍Vendor_B且原引用不变；PATCH notes不影响name/phone，省略与显式清空分别测试
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["停用供应商会清空名称导致无法按原名回看", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 自动化 API 探针 (`test_entry.py run`) + 真实浏览器交互操作核查。

### [UAT-031] SKU合并没有迁移库存批次，后续FIFO错误暂估
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/app/db.py", "demo/app/api_inventory.py", "demo/app/services/inventory.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 合成源SKU有open remaining1批次并入主SKU，receipt/log/batch统一主SKU、source provenance保留
  1. 合并后可用2kg消耗1.25kg不出现estimated_partial
  1. 失败注入事务回滚
  1. 其他tenant批次不变
- 负向与安全边界 (Negative Boundary & Security)：
  - 不能只改stock总量或删除原批次；历史已消耗trace保留；存量修复须dry-run/备份/幂等及单独批准执行
  - 合成源SKU有open remaining1批次并入主SKU，receipt/log/batch统一主SKU、source provenance保留；合并后可用2kg消耗1.25kg不出现estimated_partial；失败注入事务回滚；其他tenant批次不变
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["SKU合并没有迁移库存批次，后续FIFO错误暂估", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。


## 2.3 波次 W2：单据流转、算术一致性与费用管理缺陷修复 (7 项)

### [UAT-001] 已有原图单据在复核工作台误判为手工无原图
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/archive_list.js", "demo/static/js/review_core.js", "demo/app/api_receipts.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 隔离预置有原图edited单与无图manual单，owner从队列继续复核及归档重开刷新各一次
  1. 有图img naturalWidth>0且请求200，无图才显示手工无原图
  1. 过期URL必须可恢复或明确加载失败
- 负向与安全边界 (Negative Boundary & Security)：
  - 不得把URL加载失败伪作无图，不改存储原图/租户签名授权
  - 隔离预置有原图edited单与无图manual单，owner从队列继续复核及归档重开刷新各一次；有图img naturalWidth>0且请求200，无图才显示手工无原图；过期URL必须可恢复或明确加载失败
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S1-P1-05", "S2-03", "S2-04", "S2-10", "S2-11", "S2-25", "S3-02", "S3-04", "S3-11", "S3-25"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-005] 错误上传格式/损坏图被误归类为识别服务问题
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/upload.js", "demo/app/api_receipts.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 重用empty.png、malformed.png、wrong-type.txt、PDF及正常JPEG/PNG/WEBP fixture
  1. 空/损坏/不支持输入有准确错误且不产生可批准单/库存，有效图模型超时仍保图转手工
  1. 以实际9格式契约逐格式断言而非全部拒PDF
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留当前支持格式和转换能力；RECOGNITION_UNREADABLE与ENGINE_TIMEOUT/输入解码错误分开
  - 重用empty.png、malformed.png、wrong-type.txt、PDF及正常JPEG/PNG/WEBP fixture；空/损坏/不支持输入有准确错误且不产生可批准单/库存，有效图模型超时仍保图转手工；以实际9格式契约逐格式断言而非全部拒PDF
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S1-P1-08", "S3-20", "S4-20"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-008] 实收0的保存与入账算术校验不一致
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/review_core.js", "demo/app/api_receipt_lifecycle.py", "demo/app/services/inventory.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 合成quantity1、unit_price10、amount10、actual_qty0与六费用各独立取值
  1. 若明细有效0则差额必须可见且不能假称无问题
  1. 修正总额匹配后保存审批同结论
  1. actual_qty=null按quantity，0按0
- 负向与安全边界 (Negative Boundary & Security)：
  - 不把0通过truthy fallback变1，不修改已正确财务费用正负合同；未匹配不入库存
  - 合成quantity1、unit_price10、amount10、actual_qty0与六费用各独立取值；若明细有效0则差额必须可见且不能假称无问题；修正总额匹配后保存审批同结论；actual_qty=null按quantity，0按0
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S1-P0-01", "S1-P2-10", "S2-01", "S2-02", "S2-14", "S2-15"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-009] 新建手工单继承前单六项附加费用
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/review_core.js", "demo/static/js/upload.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 打开六费用各非0合成单→新手工单→填一行
  1. 六字段及save payload全默认0/空且receipt_id=null
  1. 重新打开旧单六费用仍保持
- 负向与安全边界 (Negative Boundary & Security)：
  - 仅新单清理，旧单已存字段不丢；无单据跨会话泄漏
  - 打开六费用各非0合成单→新手工单→填一行；六字段及save payload全默认0/空且receipt_id=null；重新打开旧单六费用仍保持
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S1-P0-01", "S1-P2-10", "S2-01", "S2-02", "S2-12", "S2-14", "S2-15", "S3-05", "S3-19", "S3-20"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-029] 选择贷项单仍被正数总额校验阻止保存
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`["UAT-008", "UAT-009"]`
- 涉及源码文件：`["demo/static/js/review_core.js", "demo/app/api_receipt_lifecycle.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. credit_note合成负amount/负qty各一合法变体，UI保存真实请求成功→重开→审核全链路合规
  1. 普通purchase负额仍拒
  1. 费用算术匹配
  1. 已消耗冲销409仍保留
- 负向与安全边界 (Negative Boundary & Security)：
  - 不把负qty负unitprice双负错误接受，不新增贷项类型；最终oracle遵原S2-09
  - credit_note合成负amount/负qty各一合法变体，UI保存真实请求成功→重开→审核全链路合规；普通purchase负额仍拒；费用算术匹配；已消耗冲销409仍保留
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["选择贷项单仍被正数总额校验阻止保存", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-030] 重新打开包装品名会把已保存数量1改成包装个数2
- 当前状态：`[DONE] PASS`（2026-10-16 三阶段闭环：33-uat030-dev -> 34-uat030-qa -> 35-uat030-uat）
- 闭环证据：`35-uat030-uat/uat-report.md`、`35-uat030-uat/uat-result.json`（九条命题全 PASS）、12 张截图 MD5 互异且落在隔离存活窗口、Tenant B before/after/final 三份快照 MD5 完全一致
- 遗留开放缺陷（CLOSE-139 前必结）：`33-uat030-dev` 产物两处行号错引——dev-report.md:9「review_core.js 原第 1916-1925 行」实为 HEAD:1792-1801；dev-result.json 的 `test_review_s2_01.py:39-81` 实为断头跨四测试，权威划分为 :36-45 与 :69-81。更正规格见 `35-uat030-uat/citation-fix-spec.md`
- 优先级：`P1` | 任务分类：`QUALITY_RISK` | 验证模式：`MANUAL_UI`
- 前置依赖：`["UAT-009"]`
- 涉及源码文件：`["demo/static/js/review_core.js", "demo/static/js/archive_list.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. name=Synthetic_Oil 5L*2、quantity1、unit箱保存→刷新→重开两次→无编辑再存：每层name/quantity1精确不变
  1. quantity≠1回归原S2-01保护
  1. 缺qty包装提示独立测
- 负向与安全边界 (Negative Boundary & Security)：
  - 不关闭所有包装提示，不把质量偏好冒认原S2-01分支FAIL
  - name=Synthetic_Oil 5L*2、quantity1、unit箱保存→刷新→重开两次→无编辑再存：每层name/quantity1精确不变；quantity≠1回归原S2-01保护；缺qty包装提示独立测
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["重新打开包装品名会把已保存数量1改成包装个数2", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-034] 已保存的行反馈重新打开后原因和点踩状态清空显示
- 当前状态：`[DONE] PASS`（2026-10-16 三阶段闭环：36-uat034-dev -> 37-uat034-qa -> 38-uat034-uat，含一轮定向修正；W2 波次收官）
- 闭环证据：`38-uat034-uat/uat-report.md`、`38-uat034-uat/uat-result.json`（九项命题全 PASS）、10 张截图 MD5 互异、Tenant B 基线零污染
- 已知架构局限（CLOSE-139 前须留档，非本阶段引入）：行级反馈以 `item_index` 为关联键，而后端 `receipt_feedback` 表（`demo/app/db.py:634-644`）无不可变行主键，故**删行、重排、增行后反馈会错套到错误的明细行**。开发曾宣称该键构成 100% 确定性单射，经 QA 与 UAT 两阶段独立反例实测证伪。静态工作流下本卡片验收标准成立。
- 已验证的安全性质：跨租户 GET/POST 均 404、伪造租户头 404（服务端以 JWT 载荷为准）；未认证访问经**双通道无凭据客户端**实测 401。
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`["UAT-030"]`
- 涉及源码文件：`["demo/static/js/archive_list.js", "demo/static/js/review_core.js", "demo/app/api_receipts.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 点赞原因A→点踩原因B→重复→刷新重开，API/DB仅同键1记录rating-1/commentB且textarea/选中态准确
  1. 另一行/tenant不得串
  1. 业务receipt字段前后相等
- 负向与安全边界 (Negative Boundary & Security)：
  - 不得将反馈覆盖业务明细/记忆；新增读取接口如需按现有权限限定
  - 点赞原因A→点踩原因B→重复→刷新重开，API/DB仅同键1记录rating-1/commentB且textarea/选中态准确；另一行/tenant不得串；业务receipt字段前后相等
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["已保存的行反馈重新打开后原因和点踩状态清空显示", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。


## 2.4 波次 W3：台账展示、多语言报表、可访问性与交互优化 (11 项)

### [UAT-002] 归档待审核与今日待审批统计口径不同且标签缺解释
- 当前状态：`[IN_PROGRESS] 开发已交付，门禁未通过，待修正后进入独立 QA`（2026-10-16 移交时状态）
- 已达成：归档待办 5 态与今日待审批 1 态口径显式化（title + aria-label 逐项枚举）；实测三张合成单据下归档待办 2、今日待审批 1，**严格不相等且各自正确**；`admin_observation.js:511` 硬编码「待审核」已对齐 `demo/static/js/terms.js:15` 权威 label「待老板审批」。
- **待修正缺陷（云端第一优先）**：(1) `supplier_archive.js` 的 `card.title` 无条件赋值覆盖了 `demo/templates/index.html:1054` 的 `title="点击筛选待审核单据"`，导致点击筛选提示丢失，须二者同时可见；(2) 同文件 `if (labelEl.textContent.trim() === '待审核')` 属硬编码 DOM 文本 shim，文案再改即静默失效，应改 `index.html:1055` 源头标签并删除 shim；(3) 报告「全局检索无残留漂移」不准确，`index.html:1054` 仍含「待审核」，且 `index.html:182` 用「待我审批」而状态 label 为「待老板审批」，需判定同义或统一。
- 本地修正任务因调度会话中断**未执行**，代码仍处上述缺陷状态；`demo/templates/index.html` 与 `demo/static/js/terms.js` 当前未被修改，工作区无半成品。
- 优先级：`P2` | 任务分类：`QUALITY_RISK` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/supplier_archive.js", "demo/static/js/workbench_queue.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 合成uploaded1、edited1、approved1，分别读归档与今日API和UI
  1. 批准edited后只相应edited待审批减1，已入账不显示待审批
  1. 说明准确对应当前状态集合
- 负向与安全边界 (Negative Boundary & Security)：
  - 不强行让两个不同状态集合数字相等，不改审批状态机
  - 合成uploaded1、edited1、approved1，分别读归档与今日API和UI；批准edited后只相应edited待审批减1，已入账不显示待审批；说明准确对应当前状态集合
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S3-10", "S3-18", "S4-05", "S4-21"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-003] 库存流水品名截断到相同前缀
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/inventory_board.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 两个名字Synthetic_Rice和Synthetic_Oil共前缀，各入库，流水同时能辨认Rice/Oil
  1. 包含下划线和长中文名称原值可获取
- 负向与安全边界 (Negative Boundary & Security)：
  - 名称原值不改，不靠ID替代主业务名
  - 两个名字Synthetic_Rice和Synthetic_Oil共前缀，各入库，流水同时能辨认Rice/Oil；包含下划线和长中文名称原值可获取
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S1-P2-09", "S4-06", "S4-21"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-007] 消耗台账数量方向显示为正数
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/inventory_board.js", "demo/app/api_inventory.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 库存合成10kg，消耗1kg→台账-1且stock9
  1. 入库1→+1且stock10
  1. 冲销方向相反
  1. DB/API/UI三层同一事件ID核对
- 负向与安全边界 (Negative Boundary & Security)：
  - 不能为显示负数二次扣库存，不改合法消耗/冲销约束
  - 库存合成10kg，消耗1kg→台账-1且stock9；入库1→+1且stock10；冲销方向相反；DB/API/UI三层同一事件ID核对
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["B2-2", "S1-P2-09", "S4-06", "S4-21"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-010] 生产会话缺少可见退出登录入口
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`QUALITY_RISK` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/common.js", "demo/templates/index.html", "demo/app/api_auth.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 三真实cookie角色分别登录→可见按钮退出→刷新及受保护API401
  1. storage无凭据
  1. 取消/网络失败不假退出
- 负向与安全边界 (Negative Boundary & Security)：
  - 不要把demo角色切换当生产注销；不扩大角色写权限
  - 三真实cookie角色分别登录→可见按钮退出→刷新及受保护API401；storage无凭据；取消/网络失败不假退出
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S1-P3-01", "S4-08"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-017] 对账创建弹窗缺dialog语义
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/finance_ops.js", "demo/templates/index.html"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 真实打开对账创建AXTree有dialog及名称
  1. Tab循环不离开，Esc关闭并归还触发焦点
  1. 提交/取消真实可用，Hans/Hant及390不遮挡
- 负向与安全边界 (Negative Boundary & Security)：
  - 已正确焦点/Esc保留；不把语义修复扩大为所有弹窗重写
  - 真实打开对账创建AXTree有dialog及名称；Tab循环不离开，Esc关闭并归还触发焦点；提交/取消真实可用，Hans/Hant及390不遮挡
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S3-13", "S3-14", "S3-15", "S3-16", "S3-24", "S4-12", "S4-13"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-023] 繁体报表预览自动转换业务品名，和CSV原值不一致
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/cost_report.js", "demo/static/js/i18n/i18n.js", "demo/app/api_export.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 合成长中文名超长/规格/说明，Hans→Hant→Hans，8种CSV中包含业务名者对比同ID preview textContent与CSV精确相等
  1. 表头仍翻译，业务name DB不变
- 负向与安全边界 (Negative Boundary & Security)：
  - 不全局关闭繁体翻译，业务名称含HTML须仍escape
  - 合成长中文名超长/规格/说明，Hans→Hant→Hans，8种CSV中包含业务名者对比同ID preview textContent与CSV精确相等；表头仍翻译，业务name DB不变
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["繁体报表预览自动转换业务品名，和CSV原值不一致", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-024] 手机库存品名列过窄且操作与名称分离
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`QUALITY_RISK` | 验证模式：`MANUAL_UI`
- 前置依赖：`["UAT-003"]`
- 涉及源码文件：`["demo/static/css/style.css", "demo/static/css/review-mobile.css", "demo/templates/index.html", "demo/static/js/inventory_board.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 390/412手机Rice/Oil/长中文名查看并真实更多点击，对应同row SKU正确
  1. 操作可见时能同时辨识名称，短名不得逐字竖排
  1. document无水平溢出
  1. 1440操作不退化
- 负向与安全边界 (Negative Boundary & Security)：
  - 局部横滚可保留，不虚构整页溢出；不新增精确像素合同
  - 390/412手机Rice/Oil/长中文名查看并真实更多点击，对应同row SKU正确；操作可见时能同时辨识名称，短名不得逐字竖排；document无水平溢出；1440操作不退化
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["手机库存品名列过窄且操作与名称分离", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-025] 繁体洞察请求未传当前语言
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/inventory_board.js", "demo/app/api_admin.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 真实Hant触发GET /api/ai-insights携lang=zh-Hant-HK(或API已接受等价)，Hans相应lang
  1. 请求与review_locale实读
  1. 支持性prompt捕获仅作单测
  1. 真实模型输出需后续依赖成功证据
- 负向与安全边界 (Negative Boundary & Security)：
  - 不能只把UI繁转当模型语言通过；不可误推语言造成超时
  - 真实Hant触发GET /api/ai-insights携lang=zh-Hant-HK(或API已接受等价)，Hans相应lang；请求与review_locale实读；支持性prompt捕获仅作单测；真实模型输出需后续依赖成功证据
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["繁体洞察请求未传当前语言", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-027] 库存价格金额仍使用美元符号且缺千分位
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`["UAT-021"]`
- 涉及源码文件：`["demo/static/js/inventory_board.js", "demo/static/js/dish_bom.js", "demo/static/js/finance.js", "demo/static/js/i18n/i18n.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 明确合成数1234.5(非商业价)显示HK$1,234.50，0显示HK$0.00，null显示—
  1. 库存列表/走势图指标/历史表/AI卡HansHant逐项断言
  1. 外币不强标HKD
- 负向与安全边界 (Negative Boundary & Security)：
  - 不借格式修复改变FX计算，合成金额可入fixture，报告商业值掩码
  - 明确合成数1234.5(非商业价)显示HK$1,234.50，0显示HK$0.00，null显示—；库存列表/走势图指标/历史表/AI卡HansHant逐项断言；外币不强标HKD
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["库存价格金额仍使用美元符号且缺千分位", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-036] 库存和餐品请求失败仍显示0指标
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`["UAT-002"]`
- 涉及源码文件：`["demo/static/js/inventory_board.js", "demo/static/js/dish_bom.js", "demo/static/js/workbench_queue.js", "demo/static/js/supplier_archive.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 重用fault及real-empty脚本：GET500库存/餐品/scan指标—+错误+可点重试
  1. 取消注入成功加载恢复数值
  1. 新空tenant200/data[]→合法0与空态
  1. 延迟请求不可被旧成功/错误覆盖
- 负向与安全边界 (Negative Boundary & Security)：
  - 归档现已—正确不得回退，不把全部0改成—；错误注入英文不作语言bug
  - 重用fault及real-empty脚本：GET500库存/餐品/scan指标—+错误+可点重试；取消注入成功加载恢复数值；新空tenant200/data[]→合法0与空态；延迟请求不可被旧成功/错误覆盖
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["库存和餐品请求失败仍显示0指标", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT020] 批量照片抽屉遮挡原图旋转工具
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`QUALITY_RISK` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/upload.js", "demo/static/css/review-mobile.css", "demo/static/css/style.css"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 两张合成图片1440/390/412/1024选中抽屉开启，elementFromPoint在旋转按钮内且真实click旋转90°、裁剪/切图有效
  1. drawer收起打开都可操作
- 负向与安全边界 (Negative Boundary & Security)：
  - 不以force click或脚本dispatch伪可点击；不丢原图/当前编辑
  - 两张合成图片1440/390/412/1024选中抽屉开启，elementFromPoint在旋转按钮内且真实click旋转90°、裁剪/切图有效；drawer收起打开都可操作
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["S3-01", "S3-08", "S3-12", "S3-24", "S3-26", "S4-18", "S4-20", "S4-21"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。


## 2.5 波次 W4：SaaS 设计体系、触控靶区、规模性能与极端输入防护 (10 项)

### [ENG-04] Demo导航旧标题/空实验fixture与中断
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`ENGINEERING_GATE` | 验证模式：`MANUAL_COMMAND`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/tests/test_admin_tabs_consolidation_e2e.py", "demo/tests/test_analytics_historical_canary_and_ab.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 导航后业务观测链路真实执行
  1. 合成实验可读且详情结构实际验证
  1. 报告skip精确原因不得计PASS
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 导航后业务观测链路真实执行；合成实验可读且详情结构实际验证；报告skip精确原因不得计PASS
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["Demo导航旧标题/空实验fixture与中断", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境注入参数运行 pytest 定向测试套件 (`tests/...`)。

### [SAAS-CONSISTENCY] 页面容器/工具条/日期标签视觉体系差异
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`DESIGN_OBSERVATION` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/css/style.css", "demo/static/css/review-mobile.css", "demo/templates/index.html"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 1440今日/库存/归档/设置截图同类容器对齐规则明确一致
  1. 报表日期label不逐字两行
  1. 390不退化
  1. 列出采用token及视觉证据，未获批准的语义/品牌扩展不标完成
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 1440今日/库存/归档/设置截图同类容器对齐规则明确一致；报表日期label不逐字两行；390不退化；列出采用token及视觉证据，未获批准的语义/品牌扩展不标完成
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["页面容器/工具条/日期标签视觉体系差异", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [SAAS-HEIGHT] 原38px控件合同未全面达标
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`DESIGN_GAP` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/css/style.css", "demo/static/css/review-mobile.css", "demo/templates/index.html"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 桌面/390测目标真实computed rect高度>=38px
  1. keyboard可用，无截断/重叠
  1. 原文字4.5:1抽样同控件仍满足
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 桌面/390测目标真实computed rect高度>=38px；keyboard可用，无截断/重叠；原文字4.5:1抽样同控件仍满足
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["原38px控件合同未全面达标", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [SAAS-MOBILE] 移动KPI/筛选堆叠与首个业务表过晚
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`DESIGN_GAP` | 验证模式：`MANUAL_UI`
- 前置依赖：`["UAT-024", "UAT-036"]`
- 涉及源码文件：`["demo/static/css/review-mobile.css", "demo/templates/index.html"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 390/412实际可见搜索完整、筛选展开/清除有效
  1. 对同fixture原y891/y1408基线截图记录改后业务起点确实前移
  1. 固定底导航/保存不挡末字段
  1. 不得隐藏全部指标换取假优化
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 390/412实际可见搜索完整、筛选展开/清除有效；对同fixture原y891/y1408基线截图记录改后业务起点确实前移；固定底导航/保存不挡末字段；不得隐藏全部指标换取假优化
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["移动KPI/筛选堆叠与首个业务表过晚", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [SAAS-SCALE] 归档670/队列635行无分页规模任务效率
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`DESIGN_GAP` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/archive_list.js", "demo/static/js/workbench_queue.js", "demo/templates/index.html"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 合成670单/635待办全部可搜索/筛选/顺序访问，页面显示总数与当前范围
  1. 末项可达，不丢记录
  1. 修改记录跨页后状态正确
  1. 有数据/无筛选结果/真实空库区分
  1. 记录DOM行数与交互耗时实测，性能阈值经开发测试规格给出而非凭空PASS
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 合成670单/635待办全部可搜索/筛选/顺序访问，页面显示总数与当前范围；末项可达，不丢记录；修改记录跨页后状态正确；有数据/无筛选结果/真实空库区分；记录DOM行数与交互耗时实测，性能阈值经开发测试规格给出而非凭空PASS
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["归档670/队列635行无分页规模任务效率", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-032] 同日餐品二次提交后FIFO弹窗仍显示第一次份数与成本
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`["UAT-031"]`
- 涉及源码文件：`["demo/static/js/dish_consumption.js", "demo/app/api_dishes.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 同日同餐品真实先3再2份不同request_id，响应merged quantity5、表格5、弹窗5与两次FIFO detail集合/累计成本相等
  1. 重放第二request_id数量仍5
  1. 换日数据不串
- 负向与安全边界 (Negative Boundary & Security)：
  - 不改变正确后端累加/幂等，不用只表格5替代弹窗/成本证据
  - 同日同餐品真实先3再2份不同request_id，响应merged quantity5、表格5、弹窗5与两次FIFO detail集合/累计成本相等；重放第二request_id数量仍5；换日数据不串
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["同日餐品二次提交后FIFO弹窗仍显示第一次份数与成本", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-033] legacy缺汇率单据被排除但部门报表未显示API缺汇率说明
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/cost_report.js", "demo/app/services/cost_report.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 重用legacy NFX合成fixture无snapshot/无汇率，API排除1且UI有1单缺汇率说明，合计保持正确
  1. 补汇率后提示和合计恢复
  1. finally清fixture
  1. 新单缺FX审批仍422
- 负向与安全边界 (Negative Boundary & Security)：
  - 不让legacy缺汇率进入合计，不声称新单审批可绕过
  - 重用legacy NFX合成fixture无snapshot/无汇率，API排除1且UI有1单缺汇率说明，合计保持正确；补汇率后提示和合计恢复；finally清fixture；新单缺FX审批仍422
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["legacy缺汇率单据被排除但部门报表未显示API缺汇率说明", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT-035] 每日消耗数量及导出筛选仍有三个无可访问名称控件
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`PRODUCT_FAULT` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/dish_consumption.js", "demo/templates/index.html"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. owner Hans/Hant×1440/390 AXTree非ignored目标spinbutton/两个combobox name非空且描述对应字段
  1. 真实键盘输入筛选/提交正常
  1. 复核已有全命名不回归
- 负向与安全边界 (Negative Boundary & Security)：
  - 不拿placeholder/title计数替AX有效名称；不声称全站WCAG通过
  - owner Hans/Hant×1440/390 AXTree非ignored目标spinbutton/两个combobox name非空且描述对应字段；真实键盘输入筛选/提交正常；复核已有全命名不回归
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["每日消耗数量及导出筛选仍有三个无可访问名称控件", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT018] BOM极大用量允许保存且无采购餐品显示零成本100%毛利
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`QUALITY_RISK` | 验证模式：`MANUAL_UI`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/dish_bom.js", "demo/app/api_dishes.py", "demo/app/dishes_store.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 无采购SKU+极大999999999999 BOM重现
  1. NaN/Infinity/负/0拒绝写
  1. 未采购成本及毛利有未知/估算标记且不得显示确定100%
  1. 已采购正常BOM保持成本。极大有限上限裁决前记录风险未关闭
- 负向与安全边界 (Negative Boundary & Security)：
  - 不称原B2-6规定上限；允许历史暂估合同但明确来源，禁止静默夹断
  - 无采购SKU+极大999999999999 BOM重现；NaN/Infinity/负/0拒绝写；未采购成本及毛利有未知/估算标记且不得显示确定100%；已采购正常BOM保持成本。极大有限上限裁决前记录风险未关闭
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["B2-1", "B2-4", "B2-5", "B2-6", "S3-21", "S3-22"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

### [UAT019] 极大售出数量无确认直接扣成巨额负库存
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`QUALITY_RISK` | 验证模式：`MANUAL_UI`
- 前置依赖：`["UAT018"]`
- 涉及源码文件：`["demo/static/js/dish_consumption.js", "demo/app/api_dishes.py", "demo/app/services/inventory.py"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 合成stock1kg、BOM0.5kg/份，sale999999999999：普通点击不可静默巨量负库存
  1. 现有409→显式force理由路径仅按既有合同允许
  1. 正常1份扣0.5
  1. 非法日期/non-number不写
  1. 重复request_id不二扣
- 负向与安全边界 (Negative Boundary & Security)：
  - 不擅自新增业务cap或放宽已消耗冲销409；超大值残余风险待上限裁决
  - 合成stock1kg、BOM0.5kg/份，sale999999999999：普通点击不可静默巨量负库存；现有409→显式force理由路径仅按既有合同允许；正常1份扣0.5；非法日期/non-number不写；重复request_id不二扣
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["B2-1", "B2-2", "B2-4", "B2-5", "B2-6", "S1-P1-09"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。


## 2.6 波次 G1：全量工程回归门禁 (1 项，ENG-05 全量 pytest 重跑)

### [ENG-05] 完整根与Demo工程门禁重跑
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`ENGINEERING_GATE` | 验证模式：`MANUAL_COMMAND`
- 前置依赖：`["ENG-01", "ENG-02", "ENG-03", "ENG-04"]`
- 涉及源码文件：`[]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 根tests全量完成exit0且失败0
  1. Demo完整完成exit0失败0，不以418子集替总数
  1. 任何skip逐项说明和实际未验证范围
  1. 后台清理完整且15010仍在
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 根tests全量完成exit0且失败0；Demo完整完成exit0失败0，不以418子集替总数；任何skip逐项说明和实际未验证范围；后台清理完整且15010仍在
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["完整根与Demo工程门禁重跑", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境注入参数运行 pytest 定向测试套件 (`tests/...`)。


## 2.7 波次 G2：外部系统与物理依赖门禁 (2 项，模型与真实 hidden 依赖)

### [DEP-HIDDEN] 真实hidden后台轮询分支1项BLOCKED
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`DEPENDENCY_GATE` | 验证模式：`DEPENDENCY`
- 前置依赖：`[]`
- 涉及源码文件：`["demo/static/js/upload.js"]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 真实visibilitychange及document.hidden记录
  1. 隐藏期间请求间隔对照原case预期，恢复可继续/停止正确
  1. 无法hidden则明示未验，不改产品假修
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 真实visibilitychange及document.hidden记录；隐藏期间请求间隔对照原case预期，恢复可继续/停止正确；无法hidden则明示未验，不改产品假修
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["真实hidden后台轮询分支1项BLOCKED", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 真实依赖环境或物理桌面图形切台探针检测。

### [DEP-MODEL] 9项正常模型后续BLOCKED及超时运行时失败
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P1` | 任务分类：`DEPENDENCY_GATE` | 验证模式：`DEPENDENCY`
- 前置依赖：`[]`
- 涉及源码文件：`[]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 逐9 scope_id在原三组合取得真实job done后读取original/assertions对应值
  1. 实际请求、终态、截图、人工字段/保存及DB各留证
  1. 若依赖未恢复仍BLOCKED并记录真实尝试数，不能用mock/函数测试替PASS
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 逐9 scope_id在原三组合取得真实job done后读取original/assertions对应值；实际请求、终态、截图、人工字段/保存及DB各留证；若依赖未恢复仍BLOCKED并记录真实尝试数，不能用mock/函数测试替PASS
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["9项正常模型后续BLOCKED及超时运行时失败", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 真实依赖环境或物理桌面图形切台探针检测。


## 2.8 波次 G3：全矩阵 139 项终审裁决门禁 (1 项，CLOSE-139 逐项裁决)

### [CLOSE-139] 原139 canonical逐ID行为裁决门禁
- 当前状态：`[TODO] NOT_RUN (待派发)`
- 优先级：`P2` | 任务分类：`ORIGINAL_SCOPE_GATE` | 验证模式：`MANUAL_UI`
- 前置依赖：`["ENG-05", "DEP-MODEL", "DEP-HIDDEN"]`
- 涉及源码文件：`[]`
- 核心正向验收标准 (Positive Acceptance Criteria)：
  1. 139唯一ID逐行有原文行号/动作/预期、对应新证据、精确行为裁决和限制
  1. related case FAIL不能自动为原ID FAIL，related PASS不能自动全条款PASS
  1. 未完成及工程/模型阻塞独立呈现，不能宣称22修复等于原目标全部完成
- 负向与安全边界 (Negative Boundary & Security)：
  - 保留15010；不删基线或skip掩盖红门禁；不将支持性测试冒称模型真实E2E。
  - 139唯一ID逐行有原文行号/动作/预期、对应新证据、精确行为裁决和限制；related case FAIL不能自动为原ID FAIL，related PASS不能自动全条款PASS；未完成及工程/模型阻塞独立呈现，不能宣称22修复等于原目标全部完成
  - 跨tenant读取/写入应拒绝或不可见；对B tenant baseline前后无意外改动。
- 回归覆盖范围 (Regression Scope)：
  - 包含用例 ID：["原139 canonical逐ID行为裁决门禁", "相关source_files行为与角色边界"]
- 验证方式与测试工具：
  - 隔离环境拉起独立服务 (Port 151xx) + 内嵌浏览器真实元素点击、输入与网络捕获核验。

# 修复阶段共用说明（所有修复 subagent 必读）

## 背景
项目：香港餐饮进货收据识别与库存管理系统（见 /home/user/KANGGG-L/receipt-agent-interview/README.md、agent_memory.md）。
审查已完成，报告在 /home/user/.indent/01a12a4e-a627-78c0-a7f6-25a1147af3ec/artifacts/review/：
00-summary.md、S1-backend.md、S2-ai-pipeline.md、S3-frontend-ux.md、S4-frontend-eng.md、S5-i18n.md。
报告里每个问题都有 ID。用户要求：**全部修复，禁止任何残留**。你负责的 ID 清单见你的任务说明。

## 工作区（所有修复 agent 共用同一个工作树）
- 工作树：/home/user/work/receipt-all（分支 fix/review-all，基于 HEAD bb7ff17，已合入：S5 的繁体中文 i18n 改动 + 第一批 P0 修复 + api_settings.py 空壳与其路由注册）。
- 严禁修改 /home/user/KANGGG-L/receipt-agent-interview 与其他 worktree。
- 严禁任何 Git 写操作（commit/stash/checkout/reset/rebase 等）。只读的 git diff/status/log 可用。
- 同时有其他 agent 在同一工作树的其他文件里工作。**共享文件（db.py、main.py、api_receipts.py、receipt_utils.py、main.js、style.css、index.html 等）只能用 `edit` 工具做小范围精确替换，每次编辑前重新读取目标片段；严禁用脚本读整份文件再整份回写，严禁重新格式化或整体重排文件。** 不要碰不属于你任务的代码区域。
- Python 环境：source /home/user/.venvs/receipt/bin/activate。在工作树 demo/ 目录运行测试：`python -m pytest <文件> -q -p no:cacheprovider -W ignore`（测试自带临时库隔离）。全量测试很慢（根 tests/ 约 14 分钟，demo/tests 约 15 分钟），不要全量跑，只跑相关文件；全量由编排者最后统一跑。
- 起服务做浏览器/接口验证时，用你自己的端口和你自己的数据库副本（DB_PATH 环境变量指向 /tmp/indent/01a12a4e-a627-78c0-a7f6-25a1147af3ec/<你的代号>/ 下的文件；RAG_DIR 同理），不要用 15010/15021/15022。你的端口：B1=15041，B2=15042，P=15043，F1=15044，F2=15045，F3=15046。结束时关掉自己的进程（不要用 pkill -f 宽泛匹配，会误杀别人；按你自己记录的 PID 杀）。
- 其他 agent 正在改代码，你运行时可能偶尔遇到别人改到一半的文件导致的临时错误：等几十秒重试，不要去“修”别人的区域。

## 项目规范
- 严禁 Emoji（界面、日志、注释、文档）。严禁 PS/附注式废话。注释只写不明显的“为什么”。
- 所有用户可见文案必须是港式餐饮店员能看懂的人话：不出现 JSON/schema/token/canary/RAG/门禁/契约/Bearer/X-Role/status='xxx'/英文状态码等技术词，报错必须说明“发生了什么 + 下一步怎么做”。新增文案用简体中文书写（繁体由已有 i18n 转换层处理，见 docs/i18n.md；业务数据区域需要保持原值的地方使用 data-i18n-skip）。
- 每个修复必须有回归测试（Python pytest 或 node harness 或 Playwright），并且要能证明“修复前会失败”（可以用 `git stash` 以外的方式：把新测试拷到 `git archive HEAD` 导出的基线副本里验证它在基线上失败；这是只读操作）。纯视觉/布局类用 Playwright 量取断言。
- 不引入新的第三方依赖，除非任务明确允许；若新增 Python 依赖，必须写进 demo/requirements.txt。
- 保持现有 API 向后兼容（新增字段/可选参数，不删字段），除非任务要求；前端依赖的字段名不变。
- “禁止残留”的含义：你的清单里每个 ID 必须落到下面三种状态之一，并给出证据：
  1. FIXED：改了什么（文件:行号）+ 回归测试名 + 测试结果；
  2. FIXED-BY-OTHER：该 ID 的实现归属其他 agent（任务说明里已标明），你只需确认接口配合；
  3. CANNOT-VERIFY：只有依赖真实 LLM key 等外部条件才能验证。仍需把代码修好，只在“验证”一栏说明缺什么。
  不允许出现“建议后续处理”“暂不修”“留作后续”。如果你认为某项在技术上不应该按报告建议修，必须提出更优的替代修法并实施，同时说明理由。

## 单位设置功能的接口契约（B2 实现后端并补测试；F1 实现界面；B1/P 在需要换算的地方调用）
用户需求：每个商户（租户）的老板（owner，admin 也可）在侧栏看到“设置”，可添加单位、设置单位换算；换算按租户存储；内置默认值只是种子，老板可覆盖（含「斤」按 0.5kg 还是 0.6048kg）。
后端新增（文件 demo/app/api_settings.py，路由已在 main.py 注册；存储模块 demo/app/units_store.py，用 CREATE TABLE IF NOT EXISTS 建表，租户列 tenant_id；换算服务由 demo/app/services/unit_factors.py 提供，必须支持传入 tenant_id）：
- GET  /api/settings/units  -> {"status":"success","data":{"units":[{"id":int,"name":str,"kind":"weight|volume|count|other","builtin":bool}],"conversions":[{"id":int,"from_unit":str,"to_unit":str,"factor":float,"note":str,"builtin":bool}]}}（owner 及以上；返回内置种子 + 本租户自定义，自定义覆盖同名换算）
- POST /api/settings/units  body {"name","kind"?}  -> 新增单位（名称去空白、归一、租户内唯一；重复返回 409 与人话提示）
- DELETE /api/settings/units/{id}  -> 仅自定义单位可删；被 SKU 基准单位或换算引用时 409 并说明被谁引用
- POST /api/settings/unit_conversions  body {"from_unit","to_unit","factor","note"?}  语义：1 个 from_unit = factor 个 to_unit；factor>0 且有限；from!=to；两个单位若不存在则自动创建；覆盖同一对已有换算；写入反向换算由查询时自动推导，不重复存
- PUT /api/settings/unit_conversions/{id}、DELETE /api/settings/unit_conversions/{id}
- GET  /api/settings/unit_conversions/check?from_unit=&to_unit=  -> {"convertible":bool,"factor":float|null,"path":[...]}（换算图：同租户换算 + 内置种子，BFS 多跳，如 箱->瓶->毫升）
内置种子：公斤=1000克；斤（港斤/司马斤）=0.6048公斤；市斤=0.5公斤；磅=0.45359237公斤；两/司马两等；升=1000毫升 等，沿用现有 unit_factors.py 与 unit_equivalence.yaml 的合理内容，并统一“斤”的定义为 0.6048kg（香港单据口径），内地市斤用显式单位「市斤」。老板可在设置里覆盖。
入库换算规则：approve 时若明细单位与 SKU 基准单位不同：能换算则换算数量并按基准单位计单价；不能换算则**拒绝入账**（HTTP 422，code=UNIT_CONVERSION_MISSING，msg 人话：「「箱」和库存单位「斤」之间还没有换算关系，请老板到「设置」里添加换算，或把这一行的单位改成「斤」」，并在 data 里带 pairs 列表 [{"from_unit","to_unit","item_name"}]），前端据此引导到设置页。
前端：侧栏新增“设置”入口（owner/admin 可见，staff 不可见，后端同样 403）；页面分两块：「单位」列表+添加；「单位换算」列表+添加/修改/删除；输入时给出示例文案；支持与已有 SKU 基准单位下拉联动（SKU 单位下拉应列出本租户单位）。

## 成本报表口径（B2 负责）
用户决定：以单据总额为准。月度/部门成本 = 已入账单据的 total_amount（含服务费、税、折扣、运费、押金、抹零之后的应付总额，币种按 HKD；非 HKD 单据按已有汇率字段/规则换算或在报表中单列，见 S1-P1-16），再按明细行金额比例摊到各部门（明细无部门时回退单据部门，再回退未分配）；作废行不计入；`spend_summary` 导出与 cost_report/cost_report/items 口径必须一致，并有测试证明“报表合计 == 已入账单据总额合计”。

## 交付格式
最终消息必须包含：
1. 变更文件清单（git diff --stat 的相关部分）
2. 逐 ID 状态表：ID | 状态(FIXED/FIXED-BY-OTHER/CANNOT-VERIFY) | 改动位置 | 回归测试 | 结果/证据
3. 新增或修改的测试及其执行结果（命令 + 通过数）；证明“基线上会失败”的证据
4. 你做出的设计取舍与对其他 agent 的接口依赖
同时把同样内容写入 /home/user/.indent/01a12a4e-a627-78c0-a7f6-25a1147af3ec/artifacts/review/fix-<你的代号>.md。

## 项目强制测试与评估要求（来自 agent_memory.md，原样携带；前端/UX 相关改动必须执行，后端 agent 按与职责相关部分执行）
```
【MANDATORY TESTING & EVALUATION REQUIREMENTS】
1. 必须操作浏览器（Playwright）模拟真实店员用户的全流程操作体验。
2. 除了功能契约与门禁核验外，必须深度评估 UI 与前端整洁度、易用性、交互流畅度。
3. 必须以「高压环境下没受过高等教育的香港餐饮店员（阿叔/阿姨）」视角进行 UX 专项评估：
   - 报错与门禁提示是否 100% 人话直白（如“单据上的数字对不上”、“相差 xx 元”），杜绝任何技术黑话或生硬英文；
   - 按钮尺寸是否足够大（高度 >= 38px）、对比度是否达标（符合 WCAG AA）、点击靶区是否舒适；
   - 逃生通道（转手工录入）是否随时可用且保留原图；
   - 算术拦截时是否清晰给出差额指引，而不是冷冰冰报错或甩锅给画质。
```

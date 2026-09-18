# 修复工单：GUI 全组件测试缺陷 + 引擎链路 SiliconFlow 化 + 审查遗留清理

- 日期：2026-09-02
- 来源：GUI 黑盒全组件探索测试（19 脚本/52 截图）+ Commit `1a0f0eb` 代码审查 + 用户决策「opencode 已过期，全面使用 SiliconFlow」
- 状态：已批准（用户指令）

## 用户决策（产品输入）

1. opencode 引擎已过期：从可选引擎与默认值中全面移除，全链路使用 SiliconFlow（openai 兼容通道）。
2. 查清「已设置 SF 为首选，为何仍出现 opencode / 首选无响应」。

## 调查结论（事实基础）

- **opencode 残留根因**：启动装配 `hydrate_engine_config_from_env`（db.py:2364）只强制识别/审核两腿回 SF，注释明确「灰测组不受影响」→ 灰测三腿与解析腿的 `opencode/mimo-v2.5-free` 永久留在 `app_settings.engine_config`，管理台下拉又持续提供 opencode 选项。
- **「首选无响应」根因**：`.env` 的 `OPENAI_MODEL=Qwen/Qwen3-VL-32B-Thinking`（思考型）被启动装配读为识别模型；`call_timeout_seconds` 被产品规则硬钳制 ≤60s（llm.py `_resolve_timeout`「高压禁止长期空转」）；思考型在复杂单据上必然超 60s → 每次降级 CodeBuddy（实测 108s 完成解析）。
- **附带发现**：代码内置默认模型 `Qwen/Qwen2.5-VL-7B-Instruct` 已不在 SiliconFlow 目录（实测 models 列表），配置它将 404。

## 修复范围与验收标准（AC）

### E 引擎（SiliconFlow 化）
- E1 识别默认模型改为 `Qwen/Qwen3-VL-32B-Instruct`（SF 实测在目录中；与原 32B-Thinking 同代同规模、非思考型，适配 60s 预算）；`.env` 的 `OPENAI_MODEL` 同步更新。
- E2 启动装配扩展至灰测/解析腿：灰测识别/审核/解析引擎若为 `opencode` → 归一为 `openai` + SF 通道（base/key 沿用主腿，模型用标准 SF 模型），保留 enabled 开关原值。
- E3 UI 全部引擎下拉移除 opencode 选项；JS 引擎缺省值 `opencode` → `openai`；模型预设表移除 opencode 组。
- E4 后端默认值全面替换（models.py / db.py / llm.py / api_admin.py 灰测默认、样本流引擎标签改读真实配置而非硬编码 opencode/mimo 文案）。
- E5 api_admin 移除 opencode 快测分支（死代码）；llm.py 构建兜底默认改 SF。
- **AC-E**：重启服务后 DB engine_config 中不含任何 opencode 值；引擎页签所有下拉无 opencode 选项；上传解析一次成功且总耗时 <60s（不再触发降级提示）；样本流/弹窗引擎标签显示真实 SF 模型名。

### F GUI 缺陷（浏览器测试发现）
- F1（B1）角色切换重载导致的在途请求失败不再以 console.error 刷屏（切换处置全局标记，加载器 catch 静默跳过）。AC：切换 3 角色后 console error ≤1 条。
- F2（B2）「挽回入口率/点踩率」>100% 口径改名：`挽回点击/成功解析` 与 `点踩/有反馈单据`，保留原口径注释。AC：观测台不出现名为"率"却 >100% 的指标。
- F3 观测台四个加载器补 stale-response 守卫（沿用 dailyConsumptionReqSeq 模式）。AC：快速切换租户后终态一致。
- F4 rotateImg 烘焙守卫提前到 promise 入口，防并发烘焙。AC：快速双击旋转无竞态报错。

### P 后端契约与去重（审查遗留）
- P1 `delete_dish_category` 异常路径改为 HTTP 500 + logging（校验错误保持 400）。
- P2 租户解析逻辑去重（api_admin/api_phase2 共用 helper）。
- P3 api_receipts 重试门禁与 IMAGE_QUALITY_ERROR 响应体去重（4 处→1 处）。
- P4 db.py write_user_event/log_user_event 租户缺省块去重。

### C 清理
- C1 移除 main.js 调试 console.log（triggerAnalysisNow / bakeRotation）。
- C2 提取 `revokeBakedPreviewUrl()` 消除 7 处重复 revoke 块。
- C3 合并 isOwnerRole/isOwnerRoleNow 双事实来源。
- C4 移除无引用的 window 导出（pollAndApply/hasRecognizedResult/bakeCurrentRotation，先验证无引用）。
- C5 preprocess：删除零调用包装 `orthogonal_correct()`；为正交矫正补单测（P1-2 覆盖缺口）。
- C6 README 测试计数 144→实际值；规格文档命名与实现对齐（埋点观测台→治理与埋点观测台）；CSS 硬编码色值回归设计令牌。
- C7 verify_reorganize.py：补真实租户切换步骤，消除「文档承诺>实际覆盖」。

## 明确不做
- 不放宽 60s 超时硬上限（产品规则：高压禁止长期空转）。
- 不删除 llm.py 的 OpencodeChatModel 类与 CodeBuddy 降级链路（存量 DB/环境的防御性兼容，且 CodeBuddy 是独立引擎不在清理范围）。
- 不改动既有金样本数据与用户引擎密钥。

## QA 结论（2026-09-02 执行）

- pytest 全量：**225 passed, 1 skipped**（含新增 8 个正交矫正用例）；过程中抓到并修复 2 个回归
  （api_phase2 `req_tenant` 残留引用、payment 徽章测试未跟随 CSS 令牌化）。
- 引擎归一：重启装配后 `app_settings.engine_config` **opencode 零残留**；灰测/解析腿 base/key 显式填充。
  过程中修复归一器自身的枚举比较 bug（`str(EngineKind.X)` != "x"，须取 .value）。
- E2E（verify_reorganize.py，含新增租户联动步骤）：全部通过。
- GUI 回归：角色切换 x3 console error **8 -> 0**（B1 达成）；观测台 Toast/租户切换/弹窗正常；
  引擎页签无 opencode 选项、SF 参数回填完整。
- **AC-E 达标：真实上传解析 9 秒完成（原 108 秒），无「首选模型无响应」降级提示**。
- 修复期间 GUI 回归曾发现 C2 提取的正则误吞 helper 自身函数体导致无限递归
  （revokeBakedPreviewUrl 自调用 -> RangeError），已修复并以「上传+双击旋转 0 pageerror」验证。

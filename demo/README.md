# 香港餐饮进货收据识别系统 · 单店落地实现

> 香港餐饮进货收据识别与库存管理系统的**单店落地版本**：完整产品前端 + LangChain 编排的后端。
> 聚焦核心闭环：**三层 RBAC、灰测（模型可插拔 + 交叉审核）、VendorMemory RAG**。

## 一、30 秒讲法

香港餐饮老板每天收到大量进货收据（印刷单/手写单/印章单七种形态），手工录入繁琐易错。
这个系统让老板「**拍照 → AI 预填 → 核对不到一分钟 → 入库 → 月底成本自动算**」。

架构核心是 **「三层互不信任」**：

| 层 | 执行者 | 规则 |
| :--- | :--- | :--- |
| 图像 → 结构化 | VLM（LangChain 可插拔模型） | 允许出错，但自报置信度 |
| 契约 + 算术 | Pydantic 契约门禁 + 代码引擎 | AI 的金额必须过校验，不静默改 |
| 人工背书 | Side-by-Side 复核 → approve | 库存只在 approve 时写入 |

AI 只预填、人工背书 —— 这是「可信库存」的底线。

## 二、三个 RBAC 角色（无密码，下拉切换）

侧边栏品牌区有一个**角色下拉**，切换即生效（存 localStorage，随所有 `/api/` 请求带 `X-Role` 头）：

| 角色 | 权限 | 说明 |
| :--- | :--- | :--- |
| `admin` | 调整当前系统使用哪个识别模型、哪个审核模型；配置**灰测范围与模型**（应用新模型 / 单模型识别 / 一识别一审核） | 模型可插拔 + 灰度发布的平台能力 |
| `owner` 老板 | approve / flag / 盘点 / 看成本 / 部门管理 | 职责分离：录入的人 ≠ 批准的人 |
| `staff` 店员 | 上传 / 复核 / 提交 | 管理安全底线 |

切到 staff 后：新建手工单入口仍可见（staff 可建单/保存，入账仍需 owner 审批），approve 操作被后端拒绝（403）；切到 admin 才能调引擎配置/灰测接口。无需密码——下拉切换即时生效。

### 分组测试（灰测）配置（admin）

admin 通过「引擎配置 · 灰测」弹窗（侧边栏  按钮，仅 admin 可见）配置。

**常规引擎配置**（识别/审核各自的引擎与模型，与灰测隔离）：

```json
{
  "recognition_engine": "opencode",
  "recognition_model": "opencode/mimo-v2.5-free",
  "audit_engine": "opencode",
  "audit_model": "opencode/mimo-v2.5-free"
}
```

**分组测试（灰测）**：随机分配概率 0-100% + 独立全套配置：

```json
{
  "grey_enabled": true,
  "grey_percent": 30,
  "grey_assign_mode": "receipt",
  "grey_recognition_engine": "codebuddy",
  "grey_recognition_model": "minimax-m3-pay",
  "grey_audit_engine": "openai",
  "grey_audit_model": "gpt-4o-mini"
}
```

- **随机分配概率 0-100%**：决定有多大比例的单据受影响（走灰测组新配置）；0 = 全常规，100 = 全灰测
- **分配方式**：
  - `receipt`：按单据随机（每单独立 `random < 概率%`）
  - `supplier`：按供应商（同供应商一致命中，确定性可复现）
- **灰测组独立全套配置**：识别引擎/模型 + 审核引擎/模型 + 各自的 OpenAI 参数，
  与常规完全隔离——命中灰测的单据整单走灰测配置，其余走常规
- 灰测 = 新模型按概率小流量试跑，识别率达标后再全量切换

**三种引擎通道**（常规/灰测各自可选）：
- `opencode`：本机 opencode CLI，视觉模型（默认 MiMo-V2.5 Free 可读图）
- `codebuddy`：本机 CodeBuddy CLI（minimax-m3-pay 视觉）
- `openai`：**任意 OpenAI 兼容接口**——admin 填写 `base_url + api_key + 模型名`（如自建网关 / Qwen / 各家中转）

## 三、架构

```
upload ──► [预处理：EXIF 扶正 + 正交 90/180/270 自动纠正（`preprocess_orthogonal_enabled` 默认 true，`cv2.rotate` 换边） + 小角 deskew/enhance（`preprocess_enabled` 默认 OFF）]
        ──► [识别管线]（后台 Job）
            ├─ extract_chain（VLM → 结构化 JSON，LangChain 模型封装，前端旋转已烤入 `bakeCurrentRotation`）
            ├─ contract gate（Pydantic 拒绝 schema 外字段）
            ├─ math gate（算术校验，零 token）
            ├─ audit_chain（交叉审核，admin 可开关）
            └─ rag（VendorMemory 注入 <vendor_context>）
  ──► parsed ──► 人工 Side-by-Side 复核（save_edited，乐观锁）──► approve ──► SKU 库存 + 供应商建档
```

**前端 = 完整版界面**（`templates/index.html` + `static/`，纯静态无服务端渲染），
4 个 Tab（收据识别 / 实时库存 / 供应商归档+对账 / 部门花销报表）全部可用。

**后端 = LangChain 实现**：98 个 API 端点匹配完整版前端契约，核心 AI 链路用
LangChain 模型封装 + 确定性线性编排；平台化部分（多租户隔离 / 幂等迁移 / 评测台与
GT 抽检台 / 记忆治理与预算）均已落地（详见根 [README](../README.md) 3.6 节）。

### 目录

```
demo/
├── conftest.py              # 顶层夹具：注册 --run-eval 开关（语料缺失时评测用例自动 skip）
├── app/
│   ├── main.py              # FastAPI 入口（聚合路由 + 静态文件）
│   ├── db.py                # SQLite + SQLAlchemy（轻量幂等迁移：租户/治理/评测资产）
│   ├── models.py            # Pydantic 领域模型
│   ├── auth.py              # 三层 RBAC + HMAC token
│   ├── llm.py               # LangChain 模型封装（opencode/CodeBuddy/Qwen 备选）
│   ├── chains/
│   │   ├── extract_chain.py # VLM 识别链
│   │   ├── audit_chain.py   # 交叉审核 Agent
│   │   ├── review_chain.py  # AI 复盘（价格异动/供应商分析）
│   │   └── supervisor.py    # 识别管线编排（线性流程 + 重试阶梯）
│   ├── api_auth.py          # /api/auth/*
│   ├── api_receipts.py      # 上传(异步Job)/列表/详情/save_edited/approve/flag/导出
│   ├── api_inventory.py     # SKU CRUD/盘点/消耗/损耗/价格历史
│   ├── api_suppliers.py     # 供应商/部门/成本报表
│   ├── api_finance.py       # 支付/对账
│   ├── api_admin.py         # 引擎配置/灰测/AI 复盘
│   ├── api_evalset.py       # 评测集 manifest/样本/GT 候选确认/低置信回流 promote
│   └── services/
│       ├── contract.py      # 契约门禁
│       ├── math_engine.py   # 算术门禁
│       ├── inventory.py     # SKU 入账 + 成本
│       ├── rag.py           # VendorMemory RAG（Chroma，带治理元数据与预算）
│       └── receipt_utils.py # AI 结果 ↔ 前端契约桥接
├── scripts/
│   ├── import_golden.py     # 黄金样本导入（历史口径）
│   ├── build_evalset.py     # 评测集三分法构建（去标识 + 分层抽样）
│   ├── run_eval.py          # 可复现评测（--require-confirmed 门槛）
│   ├── run_meta_eval.py     # 元评测（评估器可信度自检）
│   └── gen_gt_candidates.py # GT 异构模型候选生成
├── templates/index.html     # 完整版前端（纯静态）
├── static/                  # 完整版 CSS/JS/图片
└── tests/                   # 确定性单测 + workflow/评测/租户隔离/记忆治理测试
```

## 四、运行

```bash
cd demo
cp .env.example .env        # 默认 opencode 免费模型，零配置可跑
pip install -r requirements.txt
./demo.sh run               # 启动 http://127.0.0.1:15010（浏览器打开）
```

关键链路冒烟：`./demo.sh smoke samples/20161001_711_thermal_receipt.jpg`

## 五、与系统整体的关系

本目录承载系统的「收据识别 → 库存 → 成本」核心闭环，采用**单店落地**形态：
- **产品前端**：完整可用，4 个 Tab（收据识别 / 实时库存 / 供应商归档 / 部门花销报表）
- **后端**：LangChain 模型封装 + 确定性编排（识别 → 契约门禁 → 算术门禁 → 交叉审核 → RAG 注入）
- 面向单店的最小闭环（多租户隔离已落地，`tenant_id` 贯穿业务主表）；后续可平滑扩展 ERP 集成

## 六、现场演示脚本

```bash
./demo.sh run                      # 启动完整服务（http://127.0.0.1:15010）
./demo.sh smoke <图>               # 单图冒烟：AI 识别全链路（40s~2min）
./demo.sh test                     # 跑确定性单测
```

浏览器打开 `http://127.0.0.1:15010` → 前端界面直接可用：

| Tab | 功能 | 角色 |
| :--- | :--- | :--- |
| 收据识别 | 上传 → AI 预填 → 人工复核 → approve | staff 上传/复核，owner approve |
| 实时库存 | SKU 列表/编辑/盘点/消耗/价格历史 | owner |
| 供应商归档 | 供应商 CRUD/合并 + 应付 + 对账 | owner |
| 部门花销报表 | 部门 CRUD + 成本报表 | owner |

三层 RBAC：侧边栏**角色下拉**直接切换 admin/owner/staff（无密码），
切换后刷新数据让权限即时生效——切 staff 看隐藏的 owner 按钮 + 后端 403，
切 admin 访问引擎配置/灰测接口。

admin 的引擎/模型配置经 `/api/admin/engine-config` 暴露，
可换识别/审核模型、配置灰测范围。

## 七、测试与数据

### 7.1 一键导入黄金样本（历史导入口径）

把人工标注/复核的 **57 张真实收据图 + expected 标注**（早期导入口径；现行评测语料为
163 张去标识评测集，见 7.5 节）导入平台：

```bash
./demo.sh import 8           # 导入 8 张（图+人工标注 → edited 收据 + SKU + 供应商）
./demo.sh approve-imported   # 全部 approve 入库（55 SKU / 5 供应商 / 成本 5 万+）
```

导入后前端 4 个 Tab 直接看到完整数据（收据历史 / 实时库存 / 供应商归档 / 成本报表）。

### 7.2 测新样本（实时识别）

上传未标注的图 → 免费模型实时识别 → 契约门禁 → 交叉审核 → 决策履历可见。
对比导入的黄金标注与实时识别差异，正是「AI 预填 + 人工背书」要兜的场景。

### 7.3 完整业务流测试

```bash
./demo.sh workflow           # 上传→识别→复核→approve→库存→成本→复盘→对账→支付 全链路
```

### 7.4 测试口径

- **确定性逻辑**：契约门禁 / 算术门禁 / RBAC / 乐观锁 / 幂等入库 / 租户隔离 / 记忆治理 —— pytest，秒级
- **AI 链路**：免费模型（opencode/mimo-v2.5-free）真实跑，40s~4min
- **黄金样本（历史导入口径）**：57 张真实单据 + 人工标注，导入平台做回归；现行评测走 7.5 节评测集三分法

## 八、测试

确定性逻辑（契约门禁/算术门禁/RBAC/入库/乐观锁/租户隔离/记忆治理）有 pytest 覆盖，不依赖 LLM、秒级：

```bash
python -m pytest tests/            # 全量：144 passed + 1 条件 skip（评测用例，需 --run-eval 且语料存在）
./demo.sh test                     # 快速子集（test_demo.py，30 passed）
```

识别/审核/复盘链用真实免费模型验证（耗时，不走单测）；完整业务流用 `./demo.sh workflow`。

### 7.5 评测集与 GT 抽检台（Wave B 落地）

- **构建**：`python scripts/build_evalset.py` 把真实语料（仅存本地 `evalsets/`，不入库）去标识、分层抽样为 train/val/test 三 split；`python -m pytest tests/ --run-eval` 在有语料的机器上追加评测用例
- **GT 候选**：`python scripts/gen_gt_candidates.py` 用异构模型（百炼 Qwen 系）生成候选 GT（`gt_status=draft`，`gt_source_model` 照实记录），人工在抽检台逐张确认（`gt_status=confirmed`）；`run_eval.py --require-confirmed` 保证出分只依赖人工确认过的 GT
- **元评测**：`python scripts/run_meta_eval.py` 在引擎/模型/Prompt 变更上线前自检评估器可信度（报告落 `ai_registry/benchmarks/meta_eval_runs/`）

抽检台经三轮迭代收敛（决策记录见 git log：`697e379` / `b00a568` / `d4a25ce`）：

1. **缩略图性能**：原图 3024x4032 直出导致翻页卡顿，样本接口改返回约 67 倍压缩的缩略图（base64 内嵌，质量自适应降级），翻页失灵与数量空白同步修复
2. **GT schema v2 + 工作台复用**：候选 GT 从自由表单改为对齐 `ReceiptData` 契约，抽检台复用店员真实复核界面（Side-by-Side 组件），确认路径与生产复核一致
3. **动态费用与手写注记**：费用区（服务费/税额/杂费）与手写注记行改按需动态添加行，对齐复核界面同款标准

## 九、已知取舍

- **审核偶发 unparseable → graceful skip**：审核 Agent 失败不阻塞主链路（「审核不阻断」原则）；
  生产期用条件审 + 重试
- **识别偶发幻觉行（免责条款当商品）**：正是「AI 只预填、人工背书」要兜的错——
  这也是为什么契约门禁 + Side-by-Side 复核缺一不可
- **LangChain 版本兼容**：langchain-dashscope 0.1.8 在 pydantic v2 下 root_validator
  失效，llm.py 里手动补 `client`
- **手动作废入口移除**（D-2026-08-28-1）：「作废」与「删除」操作级重叠、用户需理解
  留痕差异，故作废态改为只读（仅来自票面划线 AI 提取），用户剔除行唯一操作为删除；
  作废行不计总额/门禁/入库。决策记录见
  `docs/05-AI产品体系与模块Spec/03-治理运维与AB实验Spec/11-组件Spec-全链路埋点与体验反馈体系.md` §11

## 十、埋点与反馈

全链路埋点对齐 `step7-指标体系` 的 11 个规范事件并补齐缺口（完整设计见
[`docs/05-AI产品体系与模块Spec/03-治理运维与AB实验Spec/11-组件Spec-全链路埋点与体验反馈体系.md`](../docs/05-AI产品体系与模块Spec/03-治理运维与AB实验Spec/11-组件Spec-全链路埋点与体验反馈体系.md)）：

- **解析生命周期**（Job 状态机单点写入）：`ocr_parse_started` / `ocr_parsed`（含耗时、重试轮数、门禁拒绝数）/ `ocr_error`（error/timeout）
- **门禁**：`math_guard_checked` / `contract_guard_checked`（轻量摘要）；**RAG**：`rag_hit`
- **人工复核 diff**：`receipt_review_submitted` —— 行级三类（修改/增加/删除）+ SKU 更改 + 逐字段计数 + FER，
  由 `compute_review_diff` 纯函数对比 `ai_prefill_json` 与提交版（品名归一后比较）
- **终态**：`receipt_approved`（含 upload→approve 端到端耗时）/ `receipt_flagged` / `price_anomaly_flagged`
- **前端行为**（`POST /api/track`，静默不阻塞）：`parse_abandoned_for_manual`（没等解析转手工单）、
  `manual_entry_start`、`field_edited`（增/删行、SKU 绑定）、`reupload_after_fail`（失败态重新上传、回到标准上传流程）
- **聚合消费**：`GET /api/analytics/recognition-summary`（admin）—— 解析成功率、耗时 P50/P95、门禁拦截、
  行 diff 分布、放弃率、FER、👍/ 分布、灰测组对比；漏斗 `/api/admin/funnel` 已扩展规范步骤
- **👍/ 反馈按钮**：内联于复核操作行「确认上传单据」右侧，只评价识别结果——仅「收据识别」Tab
  且已有识别结果时出现（hover：满意/不满意当前解析结果），反馈绑定当前单据走
  `/api/receipt/{id}/feedback`（喂 VendorMemory 飞轮）

事件存 `user_event` 表（append-only），与 `audit_logs_json`（合规审计）、`ai_decision_log`（AI 引擎视角）职责分离。


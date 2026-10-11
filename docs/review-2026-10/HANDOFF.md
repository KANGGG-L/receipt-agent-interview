# 交接文档：2026-10 全面审查与修复

状态时间点：2026-10-11（会话中途，因额度可能不足而提前交接）。
基线：HEAD bb7ff17。远端 origin/main 之后只多了一个提交 235d76e（仅新增 assets/ 下 15 张 HEIC 测试收据），与本分支无冲突。

## 1. 分支一览

| 分支 | 内容 | 状态 |
|---|---|---|
| `feat/i18n-zh-hant` | 简体 / 繁體中文（香港）切换：键值层 + OpenCC 转换层 + 港式用语表 + 切换器 + CSV 导出 lang 参数 | 稳定。新增测试 node 85/85、e2e 11/11；现有测试与基线一致 |
| `fix/review-p0` | 第一批 P0 修复（XSS、折扣落库、数量覆盖、成本报表口径、应付、对账租户、转手工权限、手机抽屉等）+ 10 条回归测试 | 稳定。根 tests/ 1280 passed / 2 failed（基线内已有）；demo/tests 1656 passed / 4 failed / 6 errors（与基线失败集合完全相同） |
| `fix/review-all` | 集成分支：以上两个分支的改动 + 全部剩余审查项的修复（5 个并行 agent 在做） | **进行中（WIP）**。提交时多个 agent 仍在写代码，部分文件可能处于中间态，未经全量测试 |

`fix/review-all` 是 `fix/review-p0` 与 `feat/i18n-zh-hant` 的并集再加后续工作，接手时以它为主线；其余两个分支只是稳定快照。

## 2. 用户已拍板的决策

1. 成本报表以单据总额为准（含服务费、税、折扣；按明细比例摊到部门；作废行不计）。
2. 每个商户（租户）的老板在侧栏看到「设置」：可添加单位、设置单位换算、设置汇率；按租户存储。
3. 内置换算只是种子，老板可覆盖（含「斤」按 0.5kg 还是 0.6048kg）。种子里「斤」= 0.6048kg（港斤），「市斤」= 0.5kg。
4. 入库遇到没有换算关系的单位：拒绝入账（HTTP 422，code `UNIT_CONVERSION_MISSING`），提示老板到设置里添加换算。
5. 审查中的 P0 到 P3 全部要修，不接受“后续处理”。唯一允许的例外是必须有真实 LLM key 才能验证的项，代码仍要修好。
6. 仓库约定（agent_memory.md）：不使用 Emoji；报错必须是港式餐饮店员能看懂的人话；QA 与评审必须用 Playwright 真实浏览器并以“阿叔/阿姨”视角评估。

## 3. 审查结论与文档位置

全部报告在 `docs/review-2026-10/`：
- `00-summary.md`：汇总、合并后的 11 项 P0。
- `S1-backend.md`（后端业务，54 项）、`S2-ai-pipeline.md`（AI 管线，31 项）、`S3-frontend-ux.md`（前端交互，26 项）、`S4-frontend-eng.md`（前端工程，22 项）、`S5-i18n.md`（i18n，14 项）。
- `fix-brief.md`：修复阶段共用说明（规则、单位设置接口契约、成本报表口径、交付格式）。
- `baseline-root-tests.txt`：修复前根测试基线。
每项问题都有 ID（如 S1-P0-03、S3-02、I18N-06），在代码提交信息、测试名和各 fix-*.md 中沿用。

## 4. fix/review-all 的分工与进度

| 代号 | 范围 | 负责的审查 ID | 进度 |
|---|---|---|---|
| B1 | 后端平台：状态机、冲销、回收站、安全、事务、备份、迁移版本、统一错误信封、HttpOnly 会话、API key 加密、gzip/安全头/静态资源 hash、依赖补齐、死端点清理、弃用引擎代码清理、补审 api_evalset/api_memory/迁移 | S1-P0-01、P1-03/04/05/07/11/12/13/14/15、P2-01/02/03/05/06/07/08/09/10/11/12/13、P3-01/02/03；S2-06/22/30；S3-05/25；S4-04(服务端)/07/08(后端)/10/15 | 进行中 |
| B2 | 财务与库存：单位设置接口（units/conversions/check/fx_rates）、成本报表、SKU 候选、对账落库、付款校验与部分付款、权限矩阵、凭证上传加固、超库存提示、币种、供应商合并、BOM 补审 | S1-P0-02/03/04/05/06/07、P1-01/02/06/08/09/10/16、P2-04/09/10/13；S3-21/22 | 进行中；接口契约已写入 fix-B2.md |
| P | AI 管线 | S2-01 到 S2-31（除 B1 的 06/22/30） | 进行中 |
| F1a | 前端：识别复核、手机底部导航、明细卡片化、等待体验、门禁人话文案、冲销按钮、SKU 候选确认、单位缺失对话框 | S3-01/02/03/04/05/07(部分)/08/09/10/11/19/20/24/26；S2-12 前端；S1-P1-03/05/11 前端 | 进行中 |
| F1b | 前端：设置页（单位/换算/汇率）、库存、归档对账、付款、回收站、供应商合并入口、admin/evalset、今日待办 | S3-06/07(其余)/12/18/21/22/23/25；S1-P1-01/02/09/12、P2-04 前端 | 进行中 |

各 agent 的逐项交付表写在 `docs/review-2026-10/fix-<代号>.md`（若尚未出现，说明该 agent 还没交付；以代码与测试的实际状态为准）。

### 尚未开始的第二轮（必须在第一轮收口后做，否则与第一轮前端改动冲突）
- **F2 前端工程**：S4-05（统一 fetchJson：超时、状态码检查、失败显示“—”并可重试，KPI 不再在加载中显示 0）、S4-06（共享收据数据源 + 分页）、S4-07（清除内联事件处理器，CSP 由 Report-Only 转强制）、S4-08（去掉 localStorage 存 token，改用 B1 的 HttpOnly 会话）、S4-09（harness 接入 pytest）、S4-11（main.js 2.5 万行按 Tab 拆成 ES modules，逐步减少全局符号）、S4-12（四个转义函数、三套 API 封装、39 个原生对话框合并）、S4-13/S3-13/S3-14（弹窗 role=dialog、焦点陷阱、label 关联）、S3-15/S3-16（触控靶区 >= 38px、对比度 AA）、S4-14（CSS 设计 token、!important、z-index 层级）、S4-16（字体自托管）、S4-17/18/19/20/21/22（低优先级项）。
- **F3 i18n 遗留**：I18N-06（后端报错“错误码 + 参数”，数据参数不被转换）、I18N-07（金额统一 `I18N.fmtMoney`，HK$ 与千分位）、I18N-08（分类/供应商主数据简繁折叠）、I18N-09（原生对话框替换，与 F2 合并）、I18N-10（AI 复盘按界面语言出文）、I18N-11/12/13/14。
- 完成后：独立 QA（Playwright，三种角色、手机与桌面、简体与繁体，用 assets 里的真实收据走上传到入账）与独立 Reviewer。

## 5. 如何运行与验证

```
uv venv -p 3.11 /home/user/.venvs/receipt
uv pip install -r demo/requirements.txt pytest playwright httpx opencc-python-reimplemented
python -m playwright install chromium
cd demo && cp .env.example .env
python -m uvicorn app.main:app --port 15010
```
- 无 LLM key 时识别会失败，这是预期降级路径（应转手工录入并保留原图）。
- 测试：根 `tests/`（约 14 分钟）与 `demo/tests/`（约 15 分钟）分开跑，不要合并成一条命令。
- 已知基线失败：根 tests 2 条（`test_ledger_is_read_only_no_write_routes` 因 FastAPI 0.143 的 `_IncludedRouter`；`test_rag_embedding_wiring` 需要先启动过一次服务产生 `demo/.rag_chroma`）；demo/tests 4 failed + 6 errors（`test_admin_tabs_consolidation_e2e`、`test_api_format_loopback` 两条、`test_no_duplicate_defs_and_routes`，以及依赖 `lsof` 的 errors）。B1 负责修复 ledger 那条的兼容问题；其余基线失败如与本次改动无关，需在报告里说明原因而不是忽略。
- 真实收据：`git archive origin/main assets | tar -x`，15 张 HEIC（IMG_5878 到 IMG_5894），用 pillow-heif 转 JPEG 后上传。
- LLM 测试配置只通过环境变量传入，不要写进仓库：`OPENAI_REC_BASE_URL`、`OPENAI_REC_API_KEY`、`OPENAI_REC_MODEL`（测试时用的是 OpenAI 兼容的 `deepseek-v4-flash-vision`，单次识别约 33 秒）。**密钥不在仓库里，也不在本文档中，请向项目所有者索取。**

## 6. 接手时的第一步

1. `git log --oneline fix/review-all`，确认拿到的是最新提交；读 `docs/review-2026-10/fix-*.md`，对照 `fix-brief.md` 的“交付格式”，找出未交付的 agent。
2. 分别全量跑根 tests 与 demo/tests，和第 5 节的基线比较；`fix/review-all` 里可能有中间态文件造成的新失败，先把它们收口，再继续第二轮。
3. 对每个审查 ID 核对三态：FIXED（有修复和回归测试）、FIXED-BY-OTHER、CANNOT-VERIFY（仅限需要真实 key 的项，需用真实 key 补验证）。不允许“后续处理”。
4. 做第二轮（F2、F3），再做独立 QA 与 Reviewer。
5. 合并前处理与 origin/main（235d76e，仅 assets）的合并，以及两个分支都改了 main.js 带来的冲突（`fix/review-all` 已经包含两者，直接以它为准）。

## 7. 已知临时痕迹

- 共享的演示库里留有 subagent 的测试数据（S1 的 `s1test` 租户、S4 的 XSS 样本供应商与收据、S3 的失败上传单据、测试 SKU），在会话沙箱里，不在仓库中。
- 分支中没有提交任何 .env、数据库文件、上传图片或密钥（`.gitignore` 已忽略）。

## 8. 进度更新（B2 已交付）

B2 已交付并全部修完自己的 ID，见 `docs/review-2026-10/fix-B2.md`（接口契约在文件开头）。权限矩阵在 `docs/permission-matrix.md`，生成脚本 `demo/scripts/permission_scan.py --write`，任何人增删端点后必须重跑，否则 `test_matrix_file_matches_code` 失败。B2 另补审了 api_dishes.py，发现并修复 B2-1 到 B2-7（硬删除餐品留孤儿数据、每日消耗无幂等、历史收入随改价改写、停用餐品仍可录消耗、日期份数无校验、状态售价无校验、异常文案带 str(e)）。

B2 点名需要其他人配合的事项（未确认已完成，接手时核对）：
- F1b：对账页要加「导入对账单」界面，`import-statement` 目前没有前端入口，否则成为新的无引用端点。
- B1：approve 不得吞掉 UNIT_CONVERSION_MISSING 与 FX_RATE_MISSING，它们靠 main.py 统一处理器转 422（B2 已验证原样抛出）。
- 前端：每日消耗提交要带 `request_id` 才有防双击；不带则保持同日同餐品合并累加。
- 两个测试在当前工作树失败，需要所属方处理：`test_dish_bom_versioning`（B1 的 auth.py 不再采信 X-Email，需改测试断言）和 `test_frontend_inventory_supplier_role_gate`（前端 main.js 并行改动所致）。两者在基线上通过。

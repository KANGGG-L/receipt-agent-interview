# S1 后端业务逻辑审查报告

## 1. 审查范围与方法

**读过的代码**：`demo/app/api_receipts.py`（save_edited / approve / flag / discard / convert_manual / upload / job 部分）、`api_finance.py`（全文）、`api_suppliers.py`（全文）、`api_inventory.py`（SKU、盘点、消耗、损耗）、`api_export.py`（报表清单与角色门禁）、`api_admin.py` 和 `api_phase2.py`（engine-config、trash、parsing-logs、experiments）、`main.py`、`auth.py`、`api_auth.py`、`models.py`（片段）、`db.py`（引擎、租户钩子、apply_stock_log、create_payment、supplier_stats、迁移入口）、`services/inventory.py`、`services/receipt_utils.py`（job、save_parsed_data、_match_sku）、`services/unit_factors.py`、`unit_equivalence.yaml`、`ai_registry/tools/math_engine/v2_1_0.py`，以及 `requirements.txt`、`demo.sh`、`.env.example`。

**端点权限**：用 AST 静态扫描了 152 个 `/api/*` 操作的守卫（匿名 5 / staff 34 / owner 49 / admin 64），扫描脚本在 `/tmp/indent/01a12a4e-a627-78c0-a7f6-25a1147af3ec/S1/roles.py`，结果在同目录 `roles.txt`。另外对全部 GET 端点做了跨租户探测（`leak.py` / `leak.txt`）。common.md 写的是"98 个端点"，这个数已经过时，实际是 152 个。

**对 15010 发的真实请求**：全部在自建租户 `X-Tenant-Id: s1test` 中进行，没有删除或改动样本收据 #1。验证过的流程：
- 手工建单
- flag 后再由 staff 保存
- 带折扣的单据 approve
- 贷项单（负数）
- 混合单位入库
- SKU 模糊匹配
- 10 路并发 approve
- 付款登记与应付
- 对账跨租户
- 成本报表
- 报表导出越权
- 超量消耗
- 无 key 时上传识别

**AUTH_ENABLED=1**：在 15021 端口用 DB 副本另起了一个实例做验证，已关闭。

**测试**：抽样跑了 `tests/test_supplier_tenant_isolation.py`、`tests/test_auth_enforcement.py`、`demo/tests/test_param_wiring_upload_and_recon.py`、`tests/test_gap3_discount_deposit_math.py`、`demo/tests/test_parsed_with_warnings.py`，共 48 个，全部通过。但它们都覆盖不到下面列出的缺陷。

**未覆盖**：
- 菜品 / 每日消耗（`api_dishes.py`）只看了权限与报错文案，没审 BOM 业务逻辑。
- `api_evalset.py` 和 `api_memory.py` 只做了权限扫描。
- `db.py` 7.9k 行里的迁移逻辑只抽查了结构，没有逐条核对。
- 多进程部署下的并发没有实测，相关结论是代码证据。

**遗留测试数据**（都在 s1test 租户，未清理）：
- 收据 #2、#4–#8、#10–#15
- 供应商 2/4、部门 1、付款 1/2
- 一个 `uploads/vouchers/035d97ce.html`（XSS 验证样本）
- 内存中的对账任务 6254e2ac

其中部门"S1租户机密部门"由于 S1-P0-05 的缺陷，在 default 租户的成本报表里也能看到。

## 2. 维度评分

| 维度 | 分数 | 理由 |
|---|---|---|
| 业务正确性 | 2 | approve 幂等做得扎实，但成本报表、应付账款、单位换算、SKU 匹配、折扣入账都有实测的数据错误 |
| 权限与多租户 | 2 | 端点守卫覆盖齐全，AUTH_ENABLED=1 下身份与租户收口正确；但对账、job、成本报表部门存在跨租户读写，导出中心绕开了 owner 才能看的成本数据 |
| 安全 | 3 | 上传加固、签名取图、密钥脱敏都到位；付款凭证可以上传 HTML 并以同源 text/html 回放，登录无限速，`python -m app.main` 绑定 0.0.0.0 且默认不鉴权 |
| API 设计一致性 | 2 | 至少 5 种错误信封；业务错误常返回 200；`msg`/`message`/`detail` 混用；列表无分页；有重复端点和无前端入口的端点 |
| 可靠性与性能 | 2 | 对账任务和 Job 只存在内存，重启即丢；SQLite 未开 WAL 却监控 WAL；parsing 清扫只在启动时跑一次；入账逐行提交，没有真正的事务；N+1 查询普遍 |
| 可维护性 | 2 | db.py 在 import 时直接执行约 59 条 ALTER 迁移，没有版本表，失败只记 warning；超大文件职责混杂；声明了但从未启用的防御代码（strict 租户钩子）；依赖漏列且未锁版本 |
| 测试覆盖 | 3 | 测试数量多，approve 并发与租户隔离都有用例；但折扣入账、成本报表口径、应付扣减、对账租户、单位混算、子串误匹配这些关键路径没有用例 |

## 3. 问题清单

| ID | 严重度 | 类别 | 位置 | 问题与证据 | 对用户/业务的影响 | 修复建议 | 工作量 |
|---|---|---|---|---|---|---|---|
| S1-P0-01 | P0 | 业务正确性 / 金额 | `api_receipts.py:1315-1318, 1896-1916, 2126-2138`；`db.py:162-212`（receipts 表无对应列） | `SaveEditedBody` 接收 `discount_amount/delivery_fee/deposit_amount/rounding_adjustment`，保存时用这些字段重算门禁（不报警），但 `update_receipt` 不落库，表里也没有这些列。approve 重算时只带 `service_fee/tax_amount`。实测：明细 100、折扣 10、总额 90，保存成功且 `math_warnings=[]`；approve 返回 422 `明细合计=100.0 预期总额=100.0，但总额=90.0（差10.0）`（收据 #4） | 所有带折扣、运费、押金、抹零的单据永远无法入账。店员保存时看不到任何提示，老板审批时才被拦，而且报错指向"金额矛盾"，误导人回去改数 | 给 receipts 加这 4 列，save 时落库，approve 时传进 `ReceiptData`；补一条端到端用例（save→approve） | M |
| S1-P0-02 | P0 | 业务正确性 / 库存 | `db.py:2637-2640`；`services/inventory.py:130-137` | `apply_stock_log` 直接执行 `current_stock += qty`，不按 SKU 的 `base_unit` 换算；`last_unit_price = amount/qty` 也不换算。实测：SKU「菜心」base_unit=斤，库存 8，approve 一张含「2 kg」和「1 箱」的单后库存变成 11.0（斤），`last_unit_price` 从 10 变成 20，并被判为 +33.3% 价格异动（收据 #6） | 库存数量、货值、价格异动预警都会算错。香港供应商斤、磅、公斤、箱混用很常见，误差可能成倍放大 | 入库时用 `unit_factors.convert_unit_quantity` 换算到 base_unit；换算不了（箱、包）就写入 `unit_conversion_warning`，阻断 approve 或要求人工确认换算系数；价格比较统一按基准单位 | M |
| S1-P0-03 | P0 | 业务正确性 / SKU 匹配 | `services/receipt_utils.py:910-915`（`s_core in core or core in s_core`）；`services/inventory.py:105-114` | 核心词双向包含匹配：只要名字互相包含就算同一个 SKU，不看单位和品类。实测：先入「牛肉 1斤 @100」，再入「牛肉丸 1包 @50」，结果并进 SKU「牛肉」，库存 2.0 斤、单价 50；先入「鸡蛋仔粉 1包」，再入「鸡蛋 30只」，结果并进「鸡蛋仔粉」，库存 31 包、单价 1（收据 #11–#14） | 不同商品被错记到同一个 SKU，库存、成本、价格历史都被污染，而且没有任何提示。这比 SKU 爆炸更难发现 | 包含匹配只作为候选（写入 `fuzzy_candidates` 交给人工确认），不在 approve 时自动采用；至少加上单位量纲一致和长度比阈值的约束 | M |
| S1-P0-04 | P0 | 业务正确性 / 报表口径 | `api_suppliers.py:384-433` | 月度成本报表按明细行 `amount` 求和：没有排除 `is_void` 行（入库侧 `inventory.py:77` 已排除），也没有计入服务费、税、折扣。实测：s1test 已入账单据总额合计 250，`/api/cost_report?month=2026-10` 的 `month_total=290.0`（作废行 +50，服务费 -10）。`spend_summary` 导出同样是 290 | 老板看到的月度花销和单据总额、应付账款对不上，财务口径不可信 | 统一口径：以收据总额为准，按明细比例分摊到部门；作废行剔除；费用行单列或按比例分摊；补口径一致性测试 | S |
| S1-P0-05 | P0 | 多租户 | `api_suppliers.py:364`（`db.list_departments()` 未传 tenant） | 成本报表的部门列表不按租户过滤。实测：在 s1test 建部门「S1租户机密部门」，用 default 租户的 owner 调 `/api/cost_report`，返回的 departments 里有这个部门 | 其他租户的部门结构泄露；部门 id 冲突时，金额可能被归到别家部门 | 传入 `tenant_id=_tenant_id(request)` | S |
| S1-P0-06 | P0 | 多租户 / 对账 | `api_finance.py:205, 216-232, 272-324` | `RECON_TASKS` 是进程内全局 dict，不带租户字段。实测：s1test 建的对账任务 6254e2ac 在 default 租户的 `GET /api/reconciliation` 列表里可见（含行数与金额汇总）；default 调 `GET /api/reconciliation/6254e2ac` 返回 500（`supplier.name` 对 None 取属性）；default 调 `POST .../resolve {"line_id":2,"action":"write_off"}` 返回 200「已处理」，等于跨租户核销 | 跨租户读写对账结果；服务重启后所有对账任务丢失 | 对账任务落库并带 tenant_id，所有读写按租户查询；不存在或不属于本租户时返回 404 | M |
| S1-P0-07 | P0 | 业务正确性 / 应付 | `db.py:3916-3921`；`api_suppliers.py:51` | `unpaid_credit_total` 统计所有 credit + approved 的单据，没有排除已付款（paid_at/payment_id）的单据；`is_overdue/overdue_amount` 写死为 False/0.0。实测：收据 #2（100）登记付款后状态为 paid，供应商的 `unpaid_credit_total` 仍是 140.0（正确值应为 40） | 应付账款虚高，付了钱也不减，老板会重复付款或不再相信这个数字；逾期提醒不可用 | 扣除已关联付款的单据（或按付款金额冲减）；按 payment_terms_days 计算逾期 | S |
| S1-P1-01 | P1 | 业务正确性 / 付款 | `api_finance.py:173-199`；`db.py:3970-3993` | 付款登记没有任何校验。实测：`supplier_id=999999, amount=-1, method=bitcoin, paid_at=not-a-date, receipt_ids=4`（未入账的 edited 单）返回 200，收据 #4 变成 `paid`、`paid_at=not-a-date`。另外：不校验收据是否属于该供应商；不比较付款金额与单据总额（部分付款也会把单据整单标为已付）；同一张单可以被重复付款，后一次覆盖 payment_id | 付款台账可以写进垃圾数据；未入账的单据被标成已付；部分付款不可追踪 | 校验供应商归属、金额 > 0、method 枚举、日期格式、收据状态为 approved 且属于该供应商且尚未付清；引入付款分配表来支持部分付款 | M |
| S1-P1-02 | P1 | 业务正确性 / 对账 | `api_finance.py:242-268, 333-417` | 对账忽略 period：11 月的任务包含了 10 月的全部单据（实测任务 6254e2ac）。"已匹配"的判定是"单据已付款"，不是"与对账单一致"。import-statement 只按金额贪心匹配，不看日期和单号，还把未入账的 edited 单算进系统总额。供应商按名字字符串匹配，不用 supplier_id，合并过的供应商会漏单。resolve 不校验 action 和 line_id（实测 `line_id=424242, action=whatever` 返回 200「已处理」）。`statement_amount` 与 `restaurant_only_amount` 是同一个求和 | 对账结果不可用，可能误导核销 | 按 period 和 supplier_id 过滤；匹配规则改为单号 → 日期+金额；未知行或未知 action 返回 404/400；汇总拆开计算 | M |
| S1-P1-03 | P1 | 状态机 | `api_receipts.py:1807-1827, 2404-2436` | `save_edited` 只拒绝 approved，所以 flagged 的单据被 staff 保存一次就变回 edited，等于静默解除异常标记；flag 本身也不递增 version。实测：owner flag 收据 #2 → staff 带 version=1 保存成功，状态变为 edited/v2 → owner approve 成功 | "flagged 不入账"可以被店员一次保存绕过；老板标记的问题单会悄悄入账 | flagged 单据的 save 返回 409，或要求 owner 显式解除；flag/unflag 都要 bump version 并写审计 | S |
| S1-P1-04 | P1 | 状态机 / 并发 | `api_receipts.py:2425`（允许 parsing→flagged）；`services/receipt_utils.py:703`（迟到 Job 只保护 edited/approved） | 识别过程中被 flag 的单据，等 Job 迟到回写时会被改回 parsed，flag 丢失（代码证据，无 LLM key 无法实测） | 异常标记在识别期间会无声消失 | 把 `save_parsed_data` 的保护集合加上 flagged（以及 error 之外的所有人工终态） | S |
| S1-P1-05 | P1 | 状态机 / 数据可信度 | `api_receipts.py:1797-1805, 1324` | 状态由客户端字段 `source:"auto"` 决定：staff 可以直接新建 parsed 状态、审计记为「AI自动识别落库」的单据，然后 owner 不经复核就 approve。实测收据 #15：staff 建单后为 parsed，owner approve 返回 200。approve 允许从 parsed 直接入账（`api_receipts.py:2065`） | 人工录入被伪装成 AI 结果，污染 AI 指标与评测回流；"必须人工复核后再入账"的约束不成立 | auto_save 只允许服务端 Job 调用（内部函数，不对外暴露）；需要的话把 approve 限制为从 edited 发起，或者在前端明确告知 parsed 单据未经人工复核 | S |
| S1-P1-06 | P1 | 权限 | `api_export.py:92-160, 216-234`；`api_dishes.py:292`；`api_inventory.py:236` | 导出中心 8 张报表只有 `spend_summary` 设了 `min_role`。实测 staff 可以预览/下载「采购入账明细台账」（含单价和总额）、「菜品成本与毛利表」、「库存货值结存表」、「采购进价比价表」；`/api/dishes/cost_analysis` 和 SKU 详情的价格趋势也对 staff 开放。而 `/api/cost_report`、`/api/price_history` 对 staff 返回 403 | 进价、毛利等商业敏感信息对店员完全可见，owner 端点上的限制被绕开 | 成本、毛利、进价类报表统一设 `min_role: owner`；建立一张端点 × 角色的权限矩阵表并加测试 | S |
| S1-P1-07 | P1 | 多租户 | `api_receipts.py:939-951`；`services/receipt_utils.py:556-568` | `GET /api/job/{job_id}` 不校验租户。实测：s1test 上传得到的 job f3e5c04c，用 default 或任意租户都能读到。完成态的 Job 会附带 `build_detail`（明细、价格、签名图片 URL）。job_id 是 8 位 hex（`db.py:2054`） | 跨租户读取识别结果与原图链接 | Job 里记录 tenant_id，读取时比对，不一致返回 404；job_id 改用完整 uuid | S |
| S1-P1-08 | P1 | 安全 | `api_finance.py:186-194, 135-136` | 付款凭证不校验类型、大小、内容，扩展名直接取自客户端文件名；取回时按扩展名猜 MIME 并以 inline 方式返回。实测：上传 `x.html`（含 script）后，`GET /api/payment/voucher/035d97ce.html?...` 返回 `content-type: text/html`，同源执行。响应没有 CSP、`X-Content-Type-Options` 或 `Content-Disposition: attachment` | 同源存储型 XSS，可以窃取 localStorage 里的 Bearer token 或角色信息；在默认弱密钥下签名可以伪造（见 `/api/health` 的 `signature_secret_weak:true`） | 复用收据上传的 `_save_upload`（magic bytes、大小上限、重编码）；只允许图片和 PDF；响应加 `nosniff` 和 attachment | S |
| S1-P1-09 | P1 | 业务正确性 / 库存 | `api_inventory.py:420-445`（consume）、同文件 waste | 消耗和报损不校验当前库存。实测：库存 21 斤，消耗 1000 斤返回 200，`current_stock=-979.0`，cost 记为 10000.0（超出批次部分按估价计） | 库存可以变成负数，成本被虚构 | 超出库存时返回 409，并给出人话提示（"库存只有 21 斤，不够扣 1000 斤"），或要求 owner 显式确认 | S |
| S1-P1-10 | P1 | 业务正确性 / 单位 | `services/unit_factors.py:29`（斤=0.5）vs `services/unit_equivalence.yaml:13`（斤≡司马斤=0.6048） | 两份单位真相源互相矛盾：成本和库存折算按「斤」=0.5 kg（市斤），而单位等价表把「斤」视为司马斤（0.6048）。香港单据上写的「斤」通常指司马斤。实测：10 斤显示 `standard_kg=5.0`。`catty` 不在折算表中 | 按公斤计的成本和库存低估约 17%；评分口径与计算口径不一致 | 由业务确认「斤」默认按港斤（0.6048）计并统一到一份表；内地市斤用显式单位；补 catty | S |
| S1-P1-11 | P1 | 业务流程 | `api_receipts.py:1816, 2680`（文案指向"冲销功能"）；全仓无 receipt 冲销端点 | approved 是终态，没有冲销或反审批接口，而报错文案让用户"请使用冲销功能"。唯一的变通办法是手工建一张负数单（实测收据 #5 可以入账），但它不关联原单；而且负数 in 流水不冲减 FIFO 批次（`db.py:2654` 只在 qty>0 时记批次），库存和批次会不一致 | 已入账的错单无法规范更正；文案指向一个不存在的功能 | 实现冲销：生成与原单关联的反向流水和批次冲减，原单标记为 reversed；文案同步修改 | M |
| S1-P1-12 | P1 | 业务流程 | `api_receipts.py:2662-2688`；`api_admin.py:2721-2748`；前端无 trash 入口（`main.js:6615` 却提示"可在管理后台恢复"） | staff 可以放弃任何未入账的单据（包括 flagged），但恢复只有 admin 能做，而且只有 API，没有界面；trash 列表和恢复都不按租户过滤 | 店员误删或故意删除的单据，老板看不到也找不回 | 回收站对 owner 开放并做成界面，按租户过滤；flagged 单据的 discard 限定 owner | S |
| S1-P1-13 | P1 | 权限 | `api_phase2.py:617-650` | `POST /api/admin/experiments/{id}/conclude` 只要求 owner，而实验的创建、启动、停止都是 admin；不校验租户；`concluded_by` 直接取自请求体 | 任意门店老板可以给平台级 A/B 实验结题，审计中的操作人可以伪造 | 改为 require_admin；concluded_by 取自身份 | S |
| S1-P1-14 | P1 | 安全 / 部署 | `main.py:462`；`.env.example`（AUTH_ENABLED=0） | `python -m app.main` 以 `host=0.0.0.0, reload=True` 启动，默认又是免密模式（任何请求带 `X-Role: admin` 就是超管）；登录接口无限速（实测连续 20 次错误密码全部返回 401，没有退避） | 在店里 Wi-Fi 或公网上直接运行等于把超管权限开放给所有人 | 默认绑定 127.0.0.1；AUTH_ENABLED=0 时拒绝绑定非回环地址；登录加失败计数和退避 | S |
| S1-P1-15 | P1 | 可靠性 | `services/inventory.py:74-137`；`db.py:2628-2672`；`api_receipts.py:2154-2196` | 入账逐行 `apply_stock_log` 各自 commit，没有单一事务。失败时由应用层补偿：删除新增的流水和批次、回滚库存，但不回滚新建的 SKU、`last_unit_price`、别名登记和 `update_item_sku`。进程在中途崩溃时补偿根本不会执行，单据仍是旧状态，再次 approve 会把前几行重复入库（代码证据） | 部分入账、库存重复计算 | 把 approve 的写入放进同一个 SQLAlchemy 事务（同一个 session 一次 commit） | M |
| S1-P1-16 | P1 | 业务正确性 / 币种 | `api_suppliers.py`、`api_finance.py`、`services/inventory.py` 中 currency 出现 0 次 | 收据支持 HKD/CNY/USD 等 8 种币种，但成本报表、应付、对账、库存货值都直接相加，不做换算（代码证据） | 外币单据会让港币报表失真 | 聚合时按币种分组或换算成本位币；非 HKD 单据在 approve 时要求填写汇率 | M |
| S1-P2-01 | P2 | API 一致性 | 多处 | 至少 5 种错误信封：`{"status":"error","msg"}`；403 是 `{"detail":"..."}`；422 是 Pydantic 英文 `{"detail":[{"type":"int_parsing",...}]}`；401 是 `{"status":"error","detail"}`；SKU 接口用 `"message"`（`api_inventory.py:189,195,213`）。另外业务错误常用 HTTP 200，例如 `SETTLEMENT_REQUIRED`（`api_receipts.py:1829`）、对账「任务不存在」、供应商名称为空、patch 不存在的供应商、`parsing-logs/detail` 未找到 | 前端只能按端点分别解析错误，人话提示容易丢失；监控上看不出失败 | 统一成 `{status, code, msg, detail?}`；全局异常处理器接管 HTTPException 和 RequestValidationError，并转成中文；业务错误使用 4xx | M |
| S1-P2-02 | P2 | 报错文案 | `auth.py:289-290`；`api_receipts.py:150-152, 2068, 262`；Job `error_msg` | 给店员看的文案里有技术词：401 提到「Bearer 令牌、X-Role 角色头」（AUTH_ENABLED=1 下 X-Role 本来就不被接受）；VERSION_REQUIRED 提到「GET /api/receipt/{id}」；INVALID_STATUS 是「status='flagged'」；上传失败带「magic bytes…：Truncated File Read」；无 key 识别失败的 Job 报「安全阻断: 非法外部 API Base URL: URL 不能为空」 | 违反「100% 人话」的要求，店员看不懂也不知道下一步做什么 | code 留给程序，msg 只写人话和下一步（如"这张单刚被别人改过，请刷新后再保存"、"识别服务未开通，请直接手工录入"） | S |
| S1-P2-03 | P2 | 业务正确性 | `api_receipts.py:1829-1831` | `SETTLEMENT_REQUIRED` 的检查放在第一条状态审计写入（`:1827`）之后，被拒绝的保存也会留下一条"状态变更"审计 | 审计日志失真 | 把所有校验移到写审计之前 | S |
| S1-P2-04 | P2 | 供应商合并 | `api_suppliers.py:180-199` | 合并只改写收据，不迁移 payments.supplier_id、vendor_sku_alias、vendor_memory，不写审计，也不在事务里。允许 keep_id==drop_id：实测合并供应商 4 到自身后，该供应商被停用 | 合并后付款与别名丢失关联，应付和匹配都出错；误操作会把供应商"合并没了" | 拒绝 keep==drop；在单一事务内迁移所有引用并写审计 | S |
| S1-P2-05 | P2 | 可靠性 | `services/receipt_utils.py:48, 419`；`main.py:162-189` | Job 存在内存 `JOBS` 中，从不清理（内存只增不减），重启即丢失。parsing 超时清扫只在启动时跑一次，运行中卡住的单据要等下次重启 | 店员看到单据一直"识别中"；长时间运行内存增长 | 定时清扫（可挂在 guardian 线程上）；Job 状态落库或设 TTL 清理 | S |
| S1-P2-06 | P2 | 可靠性 / 性能 | `api_receipts.py:926-936` | `async def upload_receipt` 在 `async_=false` 时用 `time.sleep(1)` 轮询最多 600 次，会阻塞事件循环（代码证据，默认是 async=true） | 一次同步上传最多会卡住整个服务 10 分钟 | 改为 `def` 或 `await asyncio.sleep`，或者直接删除同步模式 | S |
| S1-P2-07 | P2 | 可靠性 | `db.py:157`；`main.py:353-360, 382-392`；`main.py:410` | SQLite 未设置 WAL 和 busy_timeout（实测 `journal_mode=delete`），但健康检查在监控 WAL 文件大小；健康检查也检查备份目录是否可写，可全仓没有任何备份实现。`any(engines.values())` 的结果被 `perf_baseline` 等字符串字段污染，恒为 True。实测：两个引擎都是 false，health 仍返回 `"status":"ok"` | 健康检查对运维给出错误信号；没有备份，数据丢失后无法恢复 | 连接时执行 `PRAGMA journal_mode=WAL; busy_timeout=5000`；engine 判定只看布尔字段；实现定时 `.backup` | S |
| S1-P2-08 | P2 | 多租户 / 防御代码 | `db.py:1432-1470`、`110-119`；`auth.py:418-425` | DB 层的租户强制钩子只在 `tenant_context(strict=True)` 下生效，而全仓只有测试会调用它；中间件用的是 `set_current_tenant_id`（非 strict），而且在演示态没有 X-Tenant-Id 头时根本不设置。所以中间件对 `TenantBoundaryViolationError` 的处理是死代码 | 给人一种"有 DB 层兜底"的错觉，实际上隔离完全依赖每个函数自己记得传 tenant_id（S1-P0-05 就是漏传的例子） | 在请求中间件里启用 strict，或者删掉这套钩子，避免误导 | S |
| S1-P2-09 | P2 | 性能 | `api_receipts.py:1198-1229`；`api_suppliers.py:380-385`；`db.py:3900-3921`；`receipt_utils.py:912` | `/api/receipts` 没有分页，一次返回全部单据（注释里自己写着 live 库有 1600 张）；成本报表每张单据查一次明细（N+1）；供应商列表对每个供应商全量扫描收据；SKU 模糊匹配对每一行全表扫描 SKU | 数据量一大，列表和报表就会变慢甚至超时 | 加 limit/offset 或游标；用批量查询（已有 `get_receipt_items_multi`）；supplier_stats 改为一次 GROUP BY | M |
| S1-P2-10 | P2 | 金额精度 | `db.py:174, 221-222`；`db.py:3918`；`db.py:2638` | 金额和数量都用 Float，`current_stock += qty` 不做舍入，`unpaid_credit_total` 的求和不做舍入 | 长期累积会出现 0.30000000000000004 这类尾差，对账出现"差 0.00" | 金额改用整数分或 Numeric(12,2)，聚合时统一 round(…, 2) | M |
| S1-P2-11 | P2 | 可维护性 | `db.py:2013`（import 时 `_make_engine()`）、59 处 `ALTER TABLE` | import 即执行迁移，没有 schema 版本表，失败只记 warning 然后继续（可能留下半套 schema）；启动时还会自动做 SKU 去重合并，改动业务数据（`main.py:60-76`） | 升级时出问题很难排查；启动自动改业务数据风险高 | 引入 Alembic 或至少一张 schema_version 表；启动时的去重改成只读巡检，加上人工确认入口 | L |
| S1-P2-12 | P2 | 依赖与配置 | `requirements.txt`；`demo.sh:13`；`README.md:138` | 除已知漏包外，`numpy`、`httpx` 是直接 import 的，也没有声明；所有依赖只写 `>=`，不锁版本。README 写「零配置可跑」，但 `.env.example` 里的 key 全为空（实测识别失败） | 新环境装出来的版本不可复现；演示时识别直接失败 | 补齐依赖并生成锁文件；修正 README 措辞，并给出无 key 时的手工录入路径说明 | S |
| S1-P2-13 | P2 | API 设计 | `api_phase2.py:384, 495`；`api_admin.py:2751-2766`；`api_receipts.py:1232` | 存在重复路由（`/api/analytics/events` 与 `/events/logs`、`/api/obs/kpi-trend` 与 `/api/analytics/kpi-trends` 是同一个函数）；`/api/review`、`/api/receipts/export`、`/api/chat/query`、`/api/insight-cards`、`/api/admin/trash*`、`/api/reconciliation/import-statement`、`/api/admin/grey-compare`、`/api/admin/ai-metrics` 在前端没有引用（静态 grep，可能有动态拼接，待验证）。命名上 snake_case（`price_history`、`save_edited`）与 kebab-case（`replace-image`、`adopt-ai`）混用；owner 级端点挂在 `/api/admin/*` 下（funnel、ai-metrics、maintenance/deduplicate） | 接口面越来越大，权限语义仅凭路径无法判断 | 清理重复和死端点；按资源统一命名；`/api/admin` 只放 admin 端点 | M |
| S1-P3-01 | P3 | 安全 | `auth.py:194-198, 153-167` | 密码明文常量、用 `!=` 比较；token 里含明文邮箱，没有吊销和登出机制；演示态下 `X-Email` 头可以随意伪造审计里的操作人 | 只是演示资产，但离生产差距明显 | 密码哈希加 `compare_digest`；token 加 jti 和吊销表 | M |
| S1-P3-02 | P3 | 可维护性 | `models.py:177-220`；`db.py:5438-5971`；`api_admin.py:2610-2674` | 已弃用的 opencode/codebuddy 引擎还留有约 30 处归一和兼容逻辑 | 阅读负担 | 跑一次性迁移后删除兼容代码 | S |
| S1-P3-03 | P3 | 安全 | `main.py:332` | 匿名可访问 `/api/health`，会暴露 auth 模式、签名密钥是否为弱密钥、备份目录路径，而且每次都执行 `PRAGMA integrity_check`（全库扫描） | 给攻击者提供侦察信息；被高频探测时会加重负载 | 匿名只返回 ok/degraded；详细信息仅 admin 可见；integrity_check 改成定时执行 | S |

## 4. 做得好的地方

- **approve 幂等与乐观锁**：同一单据 10 路并发 approve，结果是 1 个 200、9 个 409，库存流水只写了 1 条（收据 #10）。缺 version 返回 400，version 不一致返回 409；save、approve、discard、迟到 Job 回写共用同一把写锁。
- **上传加固**：按 magic bytes 判定格式，有 15MB 上限，像素有上限，用 Pillow 重编码清除 polyglot，落盘文件名由服务端生成（`api_receipts.py:302-337`）。实测伪造的 PNG 和 txt 文件都被拒绝。
- **不暴露 /uploads 静态目录**：原图和凭证都走签名端点，经 basename/realpath 收敛到上传目录内（`main.py:326-329`、`api_finance.py:37-58`）。
- **AUTH_ENABLED=1 下的身份和租户收口**：实测只带 X-Role 返回 401；staff token 加 `X-Role: admin` 仍是 403；X-Tenant-Id 被忽略，租户只取自 token。启动时拒绝默认密钥。早鉴权中间件先返回 401，消除了 422 探测面。
- **API key 脱敏**：响应（`mask_engine_config_dict`）和配置历史快照落库前（`db.py:5614`）都做了脱敏；routing 视图只给出 `has_key`。
- **没有发现 SQL 注入面**：API 层全部使用 ORM 参数化查询；CSV 导出有公式注入转义和 UTF-8 BOM。

## 5. 优先级最高的 5 项修复（按投入产出比）

1. **修正三个财务口径**（S1-P0-04、S1-P0-07、S1-P0-05，合计约 S）：成本报表剔除作废行、计入费用、以单据总额为准；应付账款扣除已付单据；部门列表加上租户条件。三处都是几行代码，直接决定老板看到的数字是否可信。
2. **折扣、运费、押金、抹零落库并参与 approve 重算**（S1-P0-01，M）：目前所有带折扣的单据都无法入账，影响面最大。
3. **收紧状态机和数据来源**（S1-P1-03、P1-04、P1-05，S）：flagged 单据禁止 staff 保存，flag 时 bump version，迟到 Job 不得覆盖 flagged，`source=auto` 不再对外开放。
4. **补齐租户和权限漏洞**（S1-P0-06、P1-06、P1-07、P1-08、P1-13，S–M）：对账任务落库并带租户；job 校验租户；成本、毛利、进价类导出限定 owner；付款凭证复用图片上传校验；实验结题改为 admin。
5. **改造 SKU 匹配与单位入账**（S1-P0-02、P0-03、P1-10，M）：包含匹配只出候选，不自动并入；入库按 base_unit 换算，换算不了就要求人工确认；统一「斤」的口径。这三项是库存与成本数据被长期、静默污染的根源。

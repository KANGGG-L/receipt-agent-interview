# 商业化安全加固统一 Plan（防注入 · 信息隔离 · 图片链路）

> 日期：2026-09-27
> 输入来源：① 本仓全量安全审计（API 端点威胁模型 + 图片链路审计，结论见 §1）；② 业界商业化落地标准联网调研（OWASP LLM Top 10 2025、多租户隔离架构、多模态注入攻防研究、商业 Guardrail 产品能力、Agent 安全基准、国内合规），结论见 §2。
> 目标：把系统从「demo 级有意识防护」推进到「商业化可交付」的防护水位，并给出可验收的工作项。

---

## §1 本地审计现状（结论摘要）

### 已达标的防线（主链路，强制生效）
- **归一化过滤器 + XML 数据沙箱**（`ai_registry/tools/prompt_injection_guard/v1_1_0_normalize.py`）：NFKC 归一 / 零宽剔除 / 同形字折叠 / leet 折叠后匹配，不可信文本统一包进 `security="untrusted_external_data"` 沙箱。主识别/审核/复盘三条 LLM 腿的调用点全部强制接线（extract_chain L293/566/809/834、audit_chain L75、review_chain L71）。
- **Canary 握手**（`services/canary_guard.py`）：fail-closed 检测 System Prompt 被剥除/错配。
- **输出侧结构性防线**：白名单键过滤 → Pydantic `extra="forbid"` → 契约门禁（拒 NaN/Inf/负数/非法日期，失败打回重试）→ 算术门禁。模型输出唯一去向是 ORM 落库，**无任何"输出变成可执行调用"路径**；`query_sql_gen` 全仓无生产消费者。
- **SSRF 防线完整**：base_url 仅 admin 可配，写入+每次调用双侧校验（fail-closed、封私网/元数据/TEST-NET/fake-IP 段），`allow_redirects=False` 封死重定向绕过；api_key 全端点掩码回显。
- **记忆租户硬隔离**：Chroma 按租户物理分桶、scope 查询失败 fail-closed、跨租户 404 防探测。
- **图片链路**：无外部图片 fetch（SSRF 面天然不存在）；VLM 原始输出按不受信处理；取图端点 HMAC 签名 + 租户归属 + 路径收敛。

### 审计确认的缺口（按严重度）
| 级别 | 缺口 | 位置 |
|---|---|---|
| P0 | memory_extract / memory_consolidate 两个 LLM 写侧调用点**无沙箱、无 canary**，不可信文本裸拼进 HumanMessage | `services/memory_extract.py:463-467`、`services/memory_consolidate.py:473-476` |
| P0 | `replace-image` 换图、`/retry` 重跑**跳过 image_injection_guard**（全仓仅 upload/upload_batch 两个调用点） | `api_receipts.py:1738` 起 |
| P0 | 上传口无 magic bytes/Pillow verify/重编码消毒，无字节大小上限，`convert-image` 整文件进内存 | `api_receipts.py:164-172, 309` |
| P1 | Canary 对恶意 provider **无效**（token 明文在 prompt，原样回填即过，代码自认不提供完整性） | `canary_guard.py:5-14` |
| P1 | 记忆写入侧无注入消毒；PII 脱敏开关 `memory_pii_mask_enabled` **缺省关闭** | `rag.py:654-657` |
| P1 | 图内**语义型注入**（清晰印刷指令）无任何检测，仅靠系统提示词软声明 + 下游沙箱 + 人审 | image_injection_guard docstring L36-42 |
| P1 | image_guard **只告警不拦截**，且降级时返回 `flagged=False` 放行 | `api_receipts.py:268-286` |
| P1 | `preprocess.py` 自身无像素上限（cv2 回退路径完全无限制）；`extract_chain.py:223-245` 全局关 Pillow 解压炸弹保护存在并发竞态 | `preprocess.py:50-87` |
| P2 | 演示模式 `AUTH_ENABLED=0` 缺省，`X-Role: admin` 头即可自称管理员；弱签名密钥下取图签名不构成访问控制 | `auth.py:82,187-191`、`receipt_utils.py:987-992` |
| P2 | 发给 provider 的内容无 PII 脱敏；灰测实验把生产小票发给第三方 treatment base_url | supervisor 灰测腿 |
| P2 | 全仓无"恶意指令图片 → upload → 断言结果未被篡改"的端到端对抗测试 | tests/ |

---

## §2 业界商业化标准对标（联网调研结论）

### 2.1 OWASP LLM Top 10 (2025)
- **LLM01 Prompt Injection 居首**（直连 + 间接注入），官方立场：**注入无法被彻底消除**，必须多层缓解——约束模型行为 + 输出校验 + 特权分离 + 敏感动作 human-in-the-loop + 网关输入/输出过滤 + 下游系统把 LLM 输出当不受信输入。本仓"四层叠加 + 人审兜底"的架构声明与此一致，但缓解层存在上述缺口。
- 其他相关项：LLM02 敏感信息泄漏（→ 本仓 PII 脱敏缺省关闭是直接对标差距）、LLM06 供应链（→ 第三方 provider/base_url 治理）、LLM08 网关安全。

### 2.2 多租户信息隔离标准
业界共识是**纵深防御、多层租户上下文一致**：
1. 数据库层 RLS（row-level security）兜底，而非仅应用层 scoping——本仓目前只有应用层 scoped 查询 + ORM，**无 RLS 兜底**；
2. 租户上下文三处一致（token claims / session / DB query）——本仓生产态已做到"租户只来自 token"（auth.py L195-225），演示态 `X-Tenant-Id` 头可伪造是已知降级；
3. **RAG/向量库必须租户 scoped**——本仓已达标（物理分桶 + fail-closed）；
4. 隔离谱系：共享表 + RLS → schema-per-tenant → DB-per-tenant，商业化按客户等级选择。

### 2.3 多模态/图片注入攻防研究（2025-2026 前沿）
- 攻击面：**AgentTypo**（arXiv:2510.04257，贝叶斯优化搜索排版型注入）、六种视觉载体基准（**OCR 文本、覆盖层、EXIF 元数据、二维码、假界面、混合**）、图像隐写/对抗扰动。本仓 image_injection_guard 只覆盖"隐藏文字"单一载体，对覆盖层/假界面/二维码/EXIF 无覆盖。
- 防御方向：**ARGUS**（特征级检测）、DE-FIVE（频域分析）、Task Shield（任务对齐）；架构级共识：**"读图模型"与"执行模型"分离**、OCR 派生文本一律当不受信输入消毒、图像内容不进指令通道。
- 对本仓的含义：当前"检测+告警+人审"是合理底线，但商业化需要 (a) 扩大检测载体覆盖，(b) flagged 必须接入决策而非仅横幅，(c) 补端到端对抗回归。

### 2.4 商业 Guardrail / 网关产品能力基线（Lakera Guard、AWS Bedrock Guardrails、Azure AI Content Safety、NeMo Guardrails 等）
商业化产品标配四件套，可作为本仓网关化的功能清单：
1. 运行时**注入检测**（本仓有：归一化 guard，缺语义层）；
2. **PII 检测与脱敏**（输入/输出双向；本仓仅显示/导出侧，缺 provider 出站侧）；
3. 话题/内容过滤与 denied topics（本仓无此需求，低优）；
4. 全链路**审计与可观测**（本仓有 audit_logs，缺安全事件专项看板）。

### 2.5 Agent / Tool 安全基准
- **AgentDojo**（97 任务 / 629 安全用例）与 **InjecAgent** 是工具型 agent 注入攻防的事实基准；Promptfoo 等工具已内置。本仓对抗测试零散，**缺系统性对抗回归套件**。
- MCP/tool 投毒防御共识：工具注册时生成不可变 `tool_id`、工具描述不可被运行时篡改、**tool call 白名单 + 最小权限 scope**。本仓模型输出不触发工具执行，天然免疫 tool poisoning，但若未来启用 `query_sql_gen`/记忆治理自动化，须按此标准落地。

### 2.6 国内合规要求（PIPL / 生成式AI服务管理暂行办法 / 等保）
- **数据本地化**：训练/推理数据不出境，部署环境过等保（三级）；
- **大模型上线备案 + 算法备案 + 安全评估**（涉舆论属性）三件套；
- **PIPL**：处理个人信息须有合法性基础，敏感个人信息单独同意；提供删除/注销途径；个人信息出境须安全评估申报；
- **数据分级分类**：用户输入 / 生成内容 / 敏感数据分区存储、敏感数据单独加密；
- 2025-09 起《AI合成内容标识办法》：生成内容可标识、可溯源。
- 对本仓：小票含个人信息（姓名/电话/金额），**PII 出境（发给第三方 provider）在商业交付中需要合同与告知背书，技术上默认应走脱敏或境内合规 provider**。

---

## §3 差距分析（本地现状 → 商业化标准）

| 业界标准要求 | 本仓现状 | 差距 |
|---|---|---|
| 注入多层缓解，全部 LLM 调用点覆盖 | 主链路覆盖，2 个写侧调用点裸拼 | **P0 补齐接线** |
| LLM 输出当不受信输入 + 输出校验 | 已达标（契约/算术/白名单） | 维持 |
| PII 输入/输出双向脱敏 | 仅展示/导出侧；出站不脱敏；memory 脱敏缺省关 | **P1 出站脱敏 + 默认开** |
| RLS/多层租户一致 | 仅应用层 scoping | **P1 SQLite 层兜底**（见 W-ISO-1） |
| 图片多载体注入检测 + 接入决策 | 单载体、只告警、换图绕过 | **P0 接线 + P1 扩载体** |
| 上传资源/内容安全 | 无 magic bytes、无大小上限、炸弹保护有竞态 | **P0/P1** |
| 对抗回归基准（AgentDojo 式） | 零散用例 | **P1 建套件** |
| 供应链（第三方 provider）治理 | admin 可配任意 base_url，灰测发生产数据 | **P1 provider 准入 + 数据分级** |
| 合规三件套 + 数据分级 | 无 | **P2 路线启动** |

---

## §4 开发方向 Work Plan

### 阶段一（P0）：堵住已知防护真空 —— 1~2 周

**W-SEC-1 记忆写侧 LLM 调用点接入沙箱 + canary**
- `memory_extract.py:463-467`、`memory_consolidate.py:473-476`：HumanMessage 改为 `wrap_untrusted_input_sandbox(json.dumps(payload), tag=...)`，调用加 canary 注入/校验（与修正腿同款）。
- 扩展 `tests/test_injection_guard_v1_1.py` 的"调用点锁定"机制，把新调用点纳入必须用 v1.1.0 的断言。
- 验收：新增回归测试证明 diff/payload 中的注入文本被沙箱包裹且 canary 缺失即 fail-closed。

**W-SEC-2 图片守卫全路径覆盖**
- `replace-image`（`api_receipts.py:1738`）落盘后调用 `_run_image_guard` 并透传 supervisor；`/retry` 对无 `image_guard_json` 记录的旧图补扫。
- 验收：换图/重试后详情接口可见 image_guard 结果；回归测试钉住三入口（upload/upload_batch/replace-image）均执行扫描。

**W-SEC-3 上传内容与资源安全**
- `_save_upload`：magic bytes 校验（JPEG/PNG/WEBP/HEIC 签名）+ Pillow `Image.open().verify()` + 解码成功即重编码落盘（消毒 polyglot）；单文件字节上限（建议 15MB）+ `/api/convert-image` 分块限量读取。
- `preprocess.py:_imread`：显式设 `Image.MAX_IMAGE_PIXELS` 检查（拒绝而非告警），cv2 回退路径加同等像素/字节限制。
- `extract_chain.py:223-245`：去掉全局 `MAX_IMAGE_PIXELS=None` 竞态（改用进程级固定上限 + 单图 `Image.open` 后自查尺寸，与 image_web 同款）。
- 验收：伪 .jpg/解压炸弹/超大图各有 400 拒绝测试。

### 阶段二（P1）：商业化信息隔离与数据流治理 —— 2~4 周

**W-ISO-1 租户隔离纵深兜底**
- 引入 SQLite 视图/触发器或迁移 Postgres + RLS：所有 receipt/memory/supplier 行级视图强制 tenant 谓词，应用层 scoping 失效时数据库层兜底。
- 演示模式显式水印：`AUTH_ENABLED=0` 时 `/api/health` 已暴露，需在前端全局横幅 + 审计日志标记 `demo_mode`，防止演示配置被误用于生产。
- 生产 checklist（代码强制）：`AUTH_ENABLED=1` + 非默认签名密钥 + 非预置密码否则拒绝启动（对齐现有弱 secret 拒启逻辑）。
- 验收：以"忘记带 tenant_id 的查询"为目标的注入测试在 DB 层被拒。

**W-ISO-2 出站数据分级与 PII 治理**
- Provider 出站脱敏管道：发送给 provider 的文本通道（记忆先验、OCR 文本）按数据分级开关走 pii_masker；图片原图不可脱敏 → 引入 **provider 准入清单**：非白名单域（默认仅已备案的境内 provider）接收原图需 owner 显式确认 + 审计留痕。
- `memory_pii_mask_enabled` 缺省改 True；记忆写入（approve 链路）增加 `detect_and_neutralize_injections` 消毒，命中即拦截待人审而非仅告警。
- 灰测实验创建时强制显示"生产数据将发送至 treatment base_url"的数据流声明并要求二次确认。
- 验收：出站 prompt 快照测试证明 PII 字段已脱敏；非白名单 base_url 上传原图路径被阻断。

**W-SEC-4 image_guard 接入决策 + 载体扩展**
- flagged + 高置信（≥2 信号）时：不静默识别，改为"拦截待确认"状态（复用现有 `_assert_retryable` 状态机），人审放行才进链路；降级（guard 异常）改为 fail-open 但**强制人审标记**并计入安全看板。
- 载体扩展（对齐 §2.3 六载体）：EXIF 清洗（上传即剥离）、二维码检测告警、边缘覆盖层/假界面启发式（对齐 edge_band 现有信号扩展）。
- 验收：合成对抗图（清晰印刷指令/二维码/覆盖层）各有检测或拦截测试。

**W-SEC-5 系统性对抗回归套件（AgentDojo 式）**
- 新建 `tests/adversarial/`：三类矩阵 —— ①文本注入（直连/间接/归一化绕过/KNOWN_UNDETECTABLE 边界固化）；②图片注入（端到端：恶意指令图 → upload → 断言结构化输出未被篡改、无外发行为）；③恶意 provider（stub 恶意 base_url：回填 canary + 篡改金额 → 断言算术门禁/人审队列拦截）。
- CI 门禁：对抗套件失败阻止合并；季度跑一次新载荷扩展（跟踪 AgentTypo/InjecAgent 公开载荷库）。
- 验收：套件 ≥50 用例，含至少 10 条"当前检不出"的诚实边界固化（沿用现有登记风格）。

**W-SEC-6 安全可观测性**
- 安全事件专项日志（注入命中、image_guard flagged、canary 失败、SSRF 拦截、出站脱敏触发）统一入 `security_events` 表 + admin 看板；对齐商业 guardrail 产品的审计基线。
- 验收：每类事件有埋点测试 + 看板聚合口径文档。

### 阶段三（P2）：合规与商业化交付 —— 按交付节奏

**W-CMP-1 合规路线**
- 数据分级分类文档（用户输入/生成内容/PII/财务数据四级）+ 存储加密（SQLite → SQLCipher 或迁移托管 PG）。
- PIPL 义务落地：个人信息处理告知、删除/注销接口（复用现有软删）、供应商合同数据条款模板。
- 大模型备案/算法备案评估（若对境内公众提供服务）；等保定级测算。
- 合成内容标识：AI 提取结果在前端/导出标注"AI 识别，经人工复核"口径（复用现有人审状态字段）。

**W-ARCH-2 防线架构演进（可选，视规模）**
- Guardrail 网关化：把归一化 guard / PII masker / canary 校验收敛为独立中间层（对齐 Lakera/Bedrock Guardrails 的"输入过滤 → 模型 → 输出过滤"拓扑），便于未来插拔语义检测模型（ARGUS/Task Shield 类）。
- 若启用任何"模型输出驱动工具执行"的能力（如 query_sql_gen），强制：tool call 白名单 + 最小权限 DB 账号 + 只读连接 + AgentDojo/InjecAgent 载荷回归通过后才上线。
- "读图/执行分离"评估：识别腿与记忆写入腿使用不同模型配置，缩小注入横向移动面。

---

## §5 里程碑与验收总览

| 里程碑 | 内容 | 出口标准 |
|---|---|---|
| M1（P0 完成） | W-SEC-1/2/3 | 无裸拼调用点；三上传入口全扫；伪图片/炸弹全拒；对抗冒烟通过 |
| M2（P1 完成） | W-ISO-1/2、W-SEC-4/5/6 | DB 层租户兜底；出站 PII 脱敏默认开；image_guard 进决策；对抗套件 ≥50 用例进 CI；安全看板可用 |
| M3（P2 完成） | W-CMP-1、W-ARCH-2 | 数据分级文档 + 加密 + 合规评估启动；guardrail 网关化方案评审 |

## 附：主要参考来源
- [OWASP LLM Top 10: Mapped to Real Gateway Controls (TrueFoundry)](https://www.truefoundry.com) / [OWASP 官方 Prompt Injection 预防指南](https://www.scribd.com) / [Aurascape 安全清单](https://aurascape.ai)
- [Structural Tenant Isolation for Tool-Using LLM Agents (arXiv)](https://arxiv.org) / [RLS vs 应用级多租户 (Propelius)](https://propelius.tech) / [Multi-Tenant SaaS 架构 (Velcod)](https://velcod.com)
- [AgentTypo: Adaptive Typographic Prompt Injection (arXiv:2510.04257)](https://arxiv.org) / [ARGUS: Defending Against Multimodal Indirect Prompt Injection (arXiv)](https://arxiv.org) / [多模态注入六载体实验评估 (alphaxiv)](https://www.alphaxiv.org)
- [Top 9 AI Security Tools (dev.to)](https://dev.to) / [Bedrock Guardrails vs Azure Content Safety (getmaxim.ai)](https://getmaxim.ai) / [AI 网关选型 (cequence.ai)](https://cequence.ai)
- [AgentDojo 基准 (arXiv)](https://arxiv.org) / [PromptFoo LLM Security Database](https://www.promptfoo.dev)
- [生成式人工智能服务管理暂行办法解读](https://www7.zzu.edu.cn) / [大模型备案全流程指南 (CSDN)](https://bbs.csdn.net) / [数据出境安全评估 (sohu)](https://www.sohu.com)

# 08 · 组件 Spec · 安全合规、隐私保护与系统运维保障

> **模块定位**：系统的安全合规防护网与生产级可用性底座。承载香港《个人资料（私隐）条例》(PDPO) 实施细则、多租户硬隔离防线、OCR 敏感信息脱敏、防篡改审计日志，以及降级容灾与 MLOps 监控告警体系。  
> **对标 14 步方案**：`step13-安全合规`、`step14-上线迭代`。  
> **实现代码**：[`app/auth.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/auth.py)、[`app/db.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/db.py)、[`services/contract.py`](file:///Users/ethan/Documents/GitHub/receipt-agent-interview/demo/app/services/contract.py)  

---

## 1. 法律合规与隐私保护 (Compliance & Privacy)

### 1.1 香港《个人资料（私隐）条例》(PDPO) 实施细则
根据香港法定私隐六项原则（DPP 1~6），系统确立如下合规工程规范：
1. **收集限制原则 (DPP 1)**：单据采集仅提取与餐饮进销存直接相关的商业字段（品名、数量、金额、供应商抬头），严禁抓取单据上偶然出现的无关个人敏感信息；
2. **准确性与留存原则 (DPP 2)**：提供完善的 Side-by-Side 人工纠错入口；对作废单据提供合规软删除脱敏机制；
3. **使用目的原则 (DPP 3)**：用户单据数据仅用于本店库存与成本核算，严禁未经授权用于公开大模型训练；
4. **安全保护原则 (DPP 4)**：图片数据与数据库字段落盘全量采用 AES-256 加密存储，传输强制 HTTPS/TLS 1.3。

---

## 2. 敏感信息防穿透与脱敏规范 (PII Masking)

在单据图片与文本被送往第三方云端大模型 API 前，系统内置轻量级脱敏过滤器：

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 敏感信息自动脱敏过滤规则                               │
├──────────────────────┬──────────────────────────────────┬──────────────────────────────┤
│ 敏感信息类型         │ 匹配规则 / 特征                  │ 处理策略                     │
├──────────────────────┼──────────────────────────────────┼──────────────────────────────┤
│ **香港身份证号**     │ 正则 `^[A-Z]{1,2}\d{6}\([\dA]\)$`│ 强制屏蔽，替换为 `[HKID_MASK]`│
├──────────────────────┼──────────────────────────────────┼──────────────────────────────┤
│ **银行账号 / 支票号**│ 8~16 位连续银行卡/支票数字       │ 保留后 4 位，其余替换为 `***` │
├──────────────────────┼──────────────────────────────────┼──────────────────────────────┤
│ **私人手机号**       │ 8 位香港手机号 (前缀 5/6/9)      │ 脱敏为 `9***1234`            │
└──────────────────────┴──────────────────────────────────┴──────────────────────────────┘
```

---

## 3. 多租户硬隔离防线 (Multi-Tenant Isolation)

系统采取 **“网关强制注入 + 数据库行级隔离 + 向量命名空间硬切分”** 的三维隔离架构：

```mermaid
flowchart TD
    Req[客户端请求] --> Gateway[API 鉴权网关]
    Gateway --> CheckJWT[验证 JWT / Session]
    CheckJWT --> ExtractTenant[提取受信任的 tenant_id]
    
    ExtractTenant --> SQLInject[SQL 查询强制自动注入 WHERE tenant_id = :tenant_id]
    ExtractTenant --> VectorInject[VectorDB 查询强制锁定 collection: tenant_{id}_vendor_memory]
    
    SQLInject --> Postgres[(PostgreSQL 数据库)]
    VectorInject --> Chroma[(Chroma 向量数据库)]
```

---

## 4. 不可篡改审计日志体系 (Audit Log Spec)

为满足财务核数与防贪防舞弊要求，系统设计独立的 `receipt_audit_logs` 表，记录单据生命周期内的全部人工与系统动作：

### 4.1 `receipt_audit_logs` 结构
```sql
CREATE TABLE receipt_audit_logs (
    audit_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    receipt_id VARCHAR(64) NOT NULL,
    action VARCHAR(32) NOT NULL,          -- UPLOAD / EDIT / APPROVE / FLAG / REJECT
    operator_id VARCHAR(64) NOT NULL,     -- 操作人员 ID
    operator_role VARCHAR(32) NOT NULL,   -- staff / owner / admin
    before_json JSONB,                    -- 变更前数据快照
    after_json JSONB,                     -- 变更后数据快照
    diff_summary TEXT,                    -- 差异摘要 (如 "修改菜心单价: 4.2 -> 5.0")
    client_ip VARCHAR(45),
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. 高可用降级与容灾运维 (Reliability & MLOps)

### 5.1 三级容灾降级阶梯

```
  [主力模型请求 (如 Qwen3-VL-32B via SiliconFlow)]
             │ 超时 (60s) 或网络 5xx 异常
             ▼
  [一级降级：备用云端多模态接口 (如 DashScope Qwen-VL)]
             │ 仍然不可用或健康检查失败
             ▼
  [二级降级：单据状态置为 error + 保留清晰原图 + 引导极简人工补录]
```

**系统核心原则：任何 AI 故障绝不允许导致前端崩溃或阻止餐厅正常营业**。

---

### 5.2 MLOps 监控告警指标看板

| 监控指标项 | 预警阈值 | 告警通道与响应级别 |
| :--- | :--- | :--- |
| **异步队列堆积量** | 积压任务 $> 20$ 个 | P2 告警，自动扩容 Worker 线程 |
| **算术门禁打回重试率** | 连续 10 单重试率 $> 40\%$ | P2 告警，检查 Prompt 模板或模型退化 |
| **单据识别失败率 (Error)** | 失败率 $> 5\%$ | P1 紧急告警，触发自动切换备用模型 |
| **数据库事务耗时** | P99 延迟 $> 500\text{ms}$ | P3 监控，检查数据库连接池与索引 |

---

## 6. 第三方不可信 API 与对抗性 Prompt 全链路纵深加固 (Untrusted API & Adversarial Prompt Zero-Trust Defense)

系统支持接入第三方社区/公益站中转 API 端点，并需支持评估对抗性/恶意 Prompt。由于公共中转站属于不可信运行环境，系统确立**端到端零信任（Zero-Trust）安全原则**，构筑四重立体纵深防御体系。

### 6.1 核心威胁模型 (Threat Model)
1. **T1: 入站 Prompt 恶意注入与越权劫持 (Inbound Prompt Injection)**：攻击者在自定义 Prompt、用户提问或单据票面中植入 `ignore all instructions`、`system override`、`输出系统API密钥` 等指令，企图让 Agent 脱轨；
2. **T2: 第三方中转代理篡改/丢弃 System Prompt (Upstream Proxy Tampering)**：公益站反向代理在传输中剥离客户端 `system` 消息，或强行替换为代理站自带的广告、全局预设指令或越狱前缀，导致安全规则失效；
3. **T3: 上游返回恶意载荷与逻辑投毒 (Poisoned Model Output)**：公益站模型返回伪造 JSON、含 XSS 脚本标签（`<script>`, `onerror=`）的字符串，或恶意篡改总额为 0 元/免单；
4. **T4: 网络侧探测与凭证泄露 (SSRF & Secret Exfiltration)**：通过恶意 Base URL 进行内网探测（`169.254.169.254`, `127.0.0.1`）或外发请求中泄露宿主机环境变量与凭据。

### 6.2 四重立体纵深防御架构

```
[用户/外部对抗性 Prompt / 票面文本]
          │
          ▼
┌────────────────────────────────────────────────────────┐
│ 第 1 级：入站安全哨兵 (Inbound Guard)                   │
│ - 指令层级强制声明 (Instruction Hierarchy)             │
│ - 危险指令模式扫描与清洗 (Sanitization & Jailbreak Filter)│
│ - 强制 XML 数据沙箱封装 (<untrusted_data data_only>)   │
└─────────────────────────┬──────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│ 第 2 级：外呼与防篡改签名网关 (Egress & Transport Guard)│
│ - API Base URL SSRF 校验 (严格阻断私网与云元数据地址)   │
│ - Canary Token 动态暗号注入 (单次 Nonce 握手校验)      │
│ - 请求体最小化脱敏 (严禁夹带系统环境变量与服务端密钥)  │
└─────────────────────────┬──────────────────────────────┘
                          │
                          ▼ 【请求发往第三方公益站 API】
                          │
                          ▼ 【获取不可信模型响应流/文本】
┌────────────────────────────────────────────────────────┐
│ 第 3 级：输出强契约与物理算术门禁 (Data & Math Gate)   │
│ - Canary Token 闭环回验 (验证上游是否保留系统契约)     │
│ - Pydantic 强制门禁 (extra="forbid"，严防非法键注入)   │
│ - 物理算术独立复算 (数量×单价、总额勾稽绝对不信模型)   │
│ - 核心状态机服务端锁定 (tenant_id/status 由代码强制控制)│
└─────────────────────────┬──────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│ 第 4 级：展现层安全中和 (Presentation Sanitization)     │
│ - DOM 纯文本安全赋值 (textContent / escapeHtml)        │
│ - 调试日志脱敏与敏感信息打码                           │
└────────────────────────────────────────────────────────┘
```

### 6.3 关键技术实现规范

#### 1. 指令层级约束与 XML 数据沙箱
- 在 System Prompt 顶端确立绝对优先级规范：所有外部输入、票面文本及 RAG 上下文一律封装在 `<untrusted_input data_only="true">` 数据沙箱内；
- 剥夺数据沙箱内任何文本的指令执行权，无论包含何种诱导性语句，均视为被动字面量。

#### 2. Canary Token & Protocol Signature 动态握手
- 针对上游中转代理可能篡改/丢弃 System Prompt 的行业通病，客户端在每次请求时生成单次高熵令牌 `__guard_token: "CANARY_<nonce>"` 并通过 User 消息尾部与 System 消息双重锚定；
- 若上游代理剥离或重写了 System 规则，模型响应必然缺失该 Token，后端立刻触发快速失败（Fail-Fast）拦截，拒绝执行并记录审计日志。

#### 3. SSRF 网关与载荷脱敏
- 对配置的所有 API Base URL 实施强校验，严禁解析到 `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` 及 `169.254.169.254` 等私有与元数据地址；
- 出站请求体经严格脱敏剪裁，杜绝任何宿主机环境变量、数据库连接信息泄漏。

#### 4. 物理算术断言与 Schema 封锁
- 采用 Pydantic `extra="forbid"`，拒绝接受 Schema 定义之外的任何注入字段；
- 模型自报的 `total_amount` 仅作草稿参考，后端由 Python 确定性算术引擎（`math_engine`）依据 `items` 行项目严格重算并累加校验，一旦发生偏差即时拦截打回。

#### 5. 前端安全渲染 (XSS 免疫)
- 前端展示 LLM 输出的商品品名、供应商名称、调整说明、审核理由时，统一使用 DOM `textContent` 属性赋值或 `escapeHtml()` 实体化转义，杜绝 DOM-based XSS。

### 6.4 生产代码落盘与自动化测试矩阵 (Production Implementation)

| 防御层级 | 生产落地组件 | 核心安全拦截与中和机制 | 核心测试套件 |
|---|---|---|---|
| **网络防线** | `demo/app/services/security_guard.py`<br>`demo/app/api_admin.py`<br>`demo/app/llm.py` | - `validate_safe_external_url`: 严格阻断私网 (RFC 1918)、回环地址、CGNAT (`100.64.0.0/10`) 及云元数据 (`169.254.169.254`)。<br>- 域名 DNS 预解析校验，阻止 DNS 重绑定绕过。<br>- 外呼显式配置 `allow_redirects=False` 阻断 HTTP 30x 重定向绕过。<br>- `mask_secret_key`: 接口及日志中密钥仅暴露前3后4位。 | `tests/test_ssrf_and_credential_guard.py` (9 passed) |
| **协议防线** | `demo/app/services/canary_guard.py`<br>`demo/app/chains/extract_chain.py` | - `generate_canary_token`: 生成高熵 `CANARY_<hex>` 单次握手令牌。<br>- `inject_canary_instructions`: 在 System Prompt 与 User 消息尾部实施双重协议锚定。<br>- `verify_canary_token`: 恒定时间校验 (`secrets.compare_digest`)，缺失或不匹配立即 Fail-Fast 阻断。<br>- 契约校验前无条件剥离 `__guard_token`，确保 Pydantic `extra="forbid"` 洁净。 | `tests/test_canary_protocol_guard.py` (6 passed) |
| **数据防线** | `ai_registry/tools/prompt_injection_guard/v1_0_0.py`<br>`demo/app/chains/extract_chain.py` | - 扩充凭证嗅探与提权正则 (`OPENAI_API_KEY`, `process.env`, `os.environ` 窃取拦截)。<br>- `wrap_untrusted_input_sandbox`: 强制 HTML 实体转义并封入 `<untrusted_input data_only="true" security="untrusted_external_data">` 沙箱，剥夺指令执行权。 | `tests/test_enhanced_prompt_guard.py` (7 passed) |
| **展现防线** | `demo/static/js/main.js`<br>`demo/app/api_admin.py` | - 前端 `escapeHtml`: 支持 `null`/`undefined` 安全容错，转义单双引号、`&`、`<`、`>`。<br>- 管理端 `GET /api/admin/engine-config`、`GET /api/admin/grey-test`、`PUT /api/admin/engine-config/*` 与审计日志执行递归密钥打码。 | `tests/test_xss_and_audit_sanitization.py` (4 passed) |
| **端到端攻防** | `tests/test_untrusted_api_security_e2e.py` | - 仿真 SSRF 越界、Canary 丢包/篡改熔断、物理算术门禁强制校验、DOM XSS 载荷免疫四大攻防场景。 | `tests/test_untrusted_api_security_e2e.py` (9 passed) |



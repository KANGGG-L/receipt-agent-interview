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
  [主力模型请求 (如 Qwen-VL)]
             │ 超时 (120s) 或网络 5xx 异常
             ▼
  [一级降级：备用云端多模态模型 (如 GPT-4o)]
             │ 仍然失败
             ▼
  [二级降级：本地离线 CLI 进程 (如 MiMo / MiniMax)]
             │ 依然无法解析
             ▼
  [三级兜底：单据状态置为 error + 保留清晰原图 + 引导极简人工补录]
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

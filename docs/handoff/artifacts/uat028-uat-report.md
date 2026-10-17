# UAT-028 UAT 报告：停用供应商会清空名称导致无法按原名回看

- 日期：2026-10-17（Asia/Shanghai）
- 角色：独立 UAT（用户视角验收；未修改任何产品/测试代码，git 产品代码 clean）
- HEAD：7271d40
- Python：/home/agentuser/receipt-agent-interview/.venv-uat009/bin/python
- 验证模式：AUTO_API（隔离活体 API 探针 + 源码原文自核）
- 结论：**PASS**（25/25 探针检查全过；对 HEAD 代码实测，未预写结论）
- 报告：docs/handoff/artifacts/uat028-uat-report.md；产物：docs/handoff/artifacts/uat028-uat/

## 1. 隔离环境

- 端口：127.0.0.1:**15930**（全新 1593x 区段；未触 15010/15100-15200/15944/15950/15951/15961/15962/15971/15972）
- DB：/tmp/uat028-uat/uat-live.db（独立新文件，不复用 DEV（15950/15951）/ QA（15944）fixture；探针前空库，基线 0 供应商）
- AUTH_ENABLED=1；DEMO_TOKEN_SECRET=随机 48 字节 hex（96 位），仅经 env 注入，未落盘
- owner 经 /api/auth/login（boss@demo.hk）取得 HttpOnly Cookie（receipt_session）；写请求带
  X-Requested-With（auth.py:327 CSRF_HEADER、auth.py:348 csrf_ok）。startup 探测带重试，本机一次通过。
- 探针脚本存档：uat028-uat/logs/probe.py；结果：uat028-uat/logs/probe-result.json、uat028-uat/logs/live-probe.log（含 uvicorn 访问日志）

## 2. 源码原文自核（file:line 打印原文核验，HEAD 7271d40）

- demo/app/api_suppliers.py:86 `name: Optional[str] = None`（缺省不再落成 ""）
- demo/app/api_suppliers.py:102-106 `_optional_text`：patch 缺省返回 None 不进 fields；显式 null/空串写成空串
- demo/app/api_suppliers.py:112-117 phone/notes 走 _optional_text；settlement_pref 显式才写；payment_terms_days/active 显式且非 None 才写
- demo/app/api_suppliers.py:124 `if not create and "name" in body.model_fields_set and body.name is not None and str(body.name).strip():`
- demo/app/api_suppliers.py:131-132 创建路径空/空白 name 400（`if not body.name or not str(body.name).strip(): return ... 400`）
- demo/app/api_suppliers.py:154-155 patch 路径显式空/空白/null name 一律 400
- demo/static/js/supplier_archive.js:224 `body: JSON.stringify({ active })` — 停用请求仅携带 active
- demo/static/js/supplier_archive.js:72 停用行空名兜底 `（未命名供应商）`

## 3. 活体探针结果（25/25 PASS）

正向（核心 AC）：
1. 新增 Vendor_A_uat 200 → 改名 Vendor_B_uat 200 → PATCH {active:0} 200
2. GET /api/suppliers?include_inactive=1&q=Vendor_B_uat：name=Vendor_B_uat、active=0、
   contact_phone=12345678 保留，同一 id 读回（原引用稳定）—— 停用未清空名称
3. 默认列表（不含 include_inactive）不包含该停用行
4. PATCH {notes:"uat-note-1"}（省略 name/phone）：name 与 phone 不变、notes 写入（省略语义）
5. PATCH {notes:"", contact_phone:""}：显式清空生效、name 不变（显式清空语义）

负向与安全：
6. PATCH name="" / "   " / null 全部 400（供应商名称不能为空），随后读回 name 仍 Vendor_B_uat —— 部分 patch 未变宽松
7. 创建 name="" / "   " 均 400 —— 创建严格性未放宽
8. 无 Cookie + 空 Authorization 头 GET → 401 UNAUTHENTICATED（身份在 HttpOnly Cookie，空 Authorization 不构成未认证豁免）
9. 带 Cookie 但无 X-Requested-With 的写请求 → 403 CSRF_REJECTED
10. staff（staff@demo.hk）登录后 PATCH owner 供应商 → 403 FORBIDDEN（角色边界）；staff 列表 200
11. owner 基线漂移检查：探针前后其余供应商零变化（仅新增本探针行）；未发现跨租户可见/可写迹象
12. SQL 直读 suppliers：`(1, 'Vendor_B_uat', 0, '', '')` —— 与 API 一致，DB 层名称保留

## 4. 过程记实（透明项）

- 探针一轮通过，无探针缺陷返工、无产品异常。
- 探针结束 ss 复核 1593x 零监听、uvicorn 进程数 0（见 environment/cleanup.json）。
- 未触发的硬约束登记：本轮无前端浏览器交互（AUTO_API 口径，前端 `JSON.stringify({active})` 以源码原文自核替代运行时点击）。

## 5. 结论

核心 AC 全部满足（停用后按原名回看、引用稳定、省略/显式清空语义正确区分），负向边界无放宽，
认证/CSRF/角色边界有效，租户基线零漂移。UAT-028 验收 **PASS**，可关闭。

## 6. 产物清单

- docs/handoff/artifacts/uat028-uat-report.md（本报告）
- docs/handoff/artifacts/uat028-uat/uat-result.json
- docs/handoff/artifacts/uat028-uat/logs/probe.py
- docs/handoff/artifacts/uat028-uat/logs/probe-result.json
- docs/handoff/artifacts/uat028-uat/logs/live-probe.log
- docs/handoff/artifacts/uat028-uat/environment/cleanup.json

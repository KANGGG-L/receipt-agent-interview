# UAT-028 QA 报告：停用供应商会清空名称导致无法按原名回看

- 日期：2026-10-17（Asia/Shanghai）
- 角色：独立 QA（未修改任何产品代码；git status 产品代码 clean）
- HEAD：7271d40
- Python：/home/agentuser/receipt-agent-interview/.venv-uat009/bin/python
- 验证模式：AUTO_API（隔离活体 API 探针 + 源码原文自核）
- 结论：**PASS**（28/28 探针检查全过；对 HEAD 代码实测，未预写结论）

## 1. 隔离环境

- 端口：127.0.0.1:**15944**（全新 1594x 区段；未触 15010/15100-15200/15950/15951/15961/15962/15971/15972）
- DB：/tmp/uat028-qa/qa-live.db（独立文件，不复用 DEV fixture；探针前为空库，基线 0 供应商）
- AUTH_ENABLED=1；DEMO_TOKEN_SECRET=96 位随机 hex（48 字节，仅 env 注入）
- owner 经 /api/auth/login（boss@demo.hk）取得 HttpOnly Cookie（receipt_session）；写请求带
  X-Requested-With（auth.py:327 CSRF_HEADER、auth.py:342-348 csrf_ok）。startup 带重试逻辑，本机 1s 健康通过。
- 探针脚本：scratch 持有；产物：qa-result.json、logs/live-probe.log、logs/probe-result.json。

## 2. 源码原文自核（file:line 打印原文核验）

- demo/app/api_suppliers.py:86 `name: Optional[str] = None`
- demo/app/api_suppliers.py:102-107 `_optional_text`：patch 缺省返回 None 不进 fields；null/空串写成空串
- demo/app/api_suppliers.py:112-115 phone/notes 走 _optional_text；120-122 active 显式且非 None 才写
- demo/app/api_suppliers.py:123-124 `if not create and "name" in body.model_fields_set and body.name is not None and str(body.name).strip():`
- demo/app/api_suppliers.py:153-155 显式空/空白/null name 一律 400
- demo/app/api_suppliers.py:131-132 创建路径空/空白 name 400
- demo/static/js/supplier_archive.js:227 `body: JSON.stringify({ active })` — 停用请求仅携带 active
- demo/static/js/supplier_archive.js:72 停用行空名兜底 `（未命名供应商）`

## 3. 活体探针结果（28/28 PASS）

正向：
1. 新增 Vendor_A_qa 200 → 改名 Vendor_B_qa 200 → PATCH {active:0} 200
2. GET /api/suppliers?include_inactive=1&q=Vendor_B_qa：name=Vendor_B_qa、active=0、contact_phone=12345678 保留（id 引用稳定，同 id 读回）
3. 默认列表不含停用行；按旧名 Vendor_A_qa 搜索不再命中（行已改名，符合预期）
4. PATCH {notes:...}（省略 name/phone）：name 与 phone 不变（省略语义）
5. PATCH {notes:"",contact_phone:""}：显式清空生效、name 不变（显式清空语义）

负向与安全：
6. PATCH name="" / "   " / null 全部 400，随后读回 name 仍 Vendor_B_qa（部分 patch 未变宽松）
7. 创建 name="" / "   " 均 400
8. 无 Cookie + 空 Authorization 头 GET → 401（身份在 HttpOnly Cookie，空 Authorization 不构成未认证豁免）
9. staff（第二租户身份）登录后 PATCH owner 域供应商 → 403；staff 租户视图前后一致；owner 基线其余供应商零漂移
10. SQL 直读 suppliers：`(1, 'Vendor_B_qa', 0, '', '')` — 与 API 一致

## 4. 过程记实（透明项）

- 首轮探针 2 处 FAIL 均为探针自身断言缺陷（SQL 元组/列表类型比较；旧名搜索预期写反——
  AC 只要求 name 保留与 id 引用稳定，旧行已改名后旧名不命中本就是正确行为），修正探针脚本后复跑 28/28 PASS；
  两轮产品行为完全一致，非产品缺陷。
- 探针结束 ss 复核 1594x 零监听、pgrep 无 uvicorn 残留（见 environment/cleanup.json）。

## 5. 结论

核心 AC 全部满足，负向边界无放宽，租户隔离有效。UAT-028 可进入 UAT 阶段。

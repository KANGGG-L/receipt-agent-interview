# UAT-028 DEV 报告：停用供应商会清空名称导致无法按原名回看

- 日期：2026-10-17（Asia/Shanghai）
- HEAD：7271d40（产品代码 git status clean；本阶段未改任何产品/测试代码）
- Python：/home/agentuser/receipt-agent-interview/.venv-uat009/bin/python
- 验证模式：AUTO_API（TDD 红基线复现 -> GREEN -> 隔离活体 API 探针）
- 结论：DEV_DONE（修复已在 HEAD；红绿证据齐备，19/19 活体探针 PASS）

## 1. 缺陷与根因

验收卡 docs/handoff/01-acceptance-checklist.md:228-243（UAT-028，[TODO] NOT_RUN）。
涉及源码：demo/app/api_suppliers.py、demo/static/js/supplier_archive.js。

根因（修复前快照 4e29834 实测）：PATCH /api/suppliers/{id} 用
`fields = {k: v for k, v in body.model_dump().items() if v is not None}` 组装更新字段，
前端停用按钮发 `PATCH {active:0}` 时，SupplierBody.name 缺省值 `""`（str 非可空缺省）
进入 fields，`db.update_supplier` 只跳过 None，不跳过 `""`，于是 name 被写成空串 ——
停用后按原名回看失败。

修复（已在 HEAD，由 commit 364b407 引入）：
- demo/app/api_suppliers.py:85 `name: Optional[str] = None`（缺省不再落成 ""）
- demo/app/api_suppliers.py:102-107 `_optional_text`：patch 缺省不改动（返回 None 不进 fields）；
  显式 null 与空串写成空串（显式清空语义保留，不放宽为忽略）
- demo/app/api_suppliers.py:112-117 部分更新组装：phone/notes 走 _optional_text；
  settlement_pref 显式才写；payment_terms_days/active 显式且非 None 才写
- demo/app/api_suppliers.py:123-125 name 仅在「显式提供且非空」时进入 fields
  （原文自核：`if not create and "name" in body.model_fields_set and body.name is not None and str(body.name).strip():`）
- demo/app/api_suppliers.py:153-155 显式空/空白/null name 一律 400 拒绝
  （原文自核：`if "name" in body.model_fields_set: if body.name is None or not str(body.name).strip(): return ... 400`）
- demo/app/api_suppliers.py:129-133 创建路径空 name（含纯空白）400
- demo/static/js/supplier_archive.js:223-225 `patchSupplierActive` 仅发送 `{active}`，
  不携带 name/phone/notes（原文自核：`body: JSON.stringify({ active })`）
- demo/static/js/supplier_archive.js:72 停用行渲染对空名兜底 `（未命名供应商）`

## 2. TDD 红基线（修复前代码，git archive 4e29834 提取至 /tmp/uat028-red）

命令（在 /tmp/uat028-red/demo）：
`DB_PATH=/tmp/uat028-red/red_base2.db RAG_DIR=/tmp/uat028-red/red_rag2 AUTH_ENABLED=0
 .venv-uat009/bin/python -m pytest tests/test_uat_028_supplier_deactivate_name_retention.py -q`

结果：3 failed, 1 passed（日志 logs/red-baseline-pytest.log）
关键失败（原文摘录，自核于 pytest 输出）：
- tests/test_uat_028_supplier_deactivate_name_retention.py:80
  `AssertionError: Expected Vendor_B_Lifecycle, got: ''` —— 停用后 name 被清空，正是本卡缺陷
- tests/test_uat_028_supplier_deactivate_name_retention.py:122
  `AssertionError: Omitted name must NOT be changed`（'' == 'Vendor_Field_Test'）

## 3. GREEN（HEAD 7271d40）

命令（在 demo/，干净环境）：
`.venv-uat009/bin/python -m pytest tests/test_uat_028_supplier_deactivate_name_retention.py -q`

结果：4 passed（稳定复跑 3 次均绿；日志 logs/green-full-suite.log）
回归：tests/test_supplier_master_delete.py + tests/test_normalize_supplier_names.py = 21 passed
（日志 logs/green-regression.log）

## 4. 隔离活体 API 探针（AUTO_API 卡片口径）

环境：uvicorn 127.0.0.1:15951；DB_PATH=/tmp/uat028-dev/live.db（独立文件）；
AUTH_ENABLED=1；DEMO_TOKEN_SECRET=96 位随机 hex（48 字节，仅 env 注入）；
owner 经 /api/auth/login（boss@demo.hk）取得 HttpOnly Cookie；写请求带
X-Requested-With（CSRF 自定义头，auth.py:327/342-347）。未认证访问 401
（空 Authorization 不是「未认证豁免」：身份在 HttpOnly Cookie，auth.py:330-336）。

结果：19/19 PASS（完整 stdout 存 logs/uat-028-api.log，uvicorn 访问日志同文件尾部）。要点：
1. Vendor_A_live 新增 200 -> 改名 Vendor_B_live 200 -> PATCH {active:0} 200
   -> GET /api/suppliers?include_inactive=1：name=Vendor_B_live、active=0、
   contact_phone=12345678 保留（响应体字段级证据在日志）
2. GET /api/suppliers（默认）：停用供应商不在活跃列表
3. q=Vendor_B&include_inactive=1 可按新名搜到
4. PATCH {notes:...}（省略 name/phone）：name 与 phone 不变（省略语义）
5. PATCH {notes:"", contact_phone:""}：显式清空生效、name 不变（显式清空语义）
6. PATCH {name:""/"   "/null} 全部 400，且随后读回 name 仍为 Vendor_B_live
7. 空/空白 name 创建 400
8. 无 Cookie 请求 401；租户基线前后一致（default 租户供应商 id 集合除本探针新建行外零变化）
9. SQL 读回 suppliers 表：[1, 'Vendor_B_live', 0, '', '']

端口纪律：使用 15950/15951（1595x 区段），未触 15010/15100-15200/15961/15962/15971/15972；
探针结束 ss 复核 1595x 零监听、无 uvicorn 残留进程。startup 探测带重试，一次健康通过。

## 5. 过程记实（透明项）

- 第一次 GREEN 跑挂：shell 环境继承了前一会话（UAT-009 QA）的 DB_PATH=/tmp/uat009-qa/qa-green.db；
  demo/conftest.py 把 env DB_PATH 当「环境库」共享，测试 2 的 next(...) 选择器命中旧行。
  该环境变量 unset 后 3 次复跑全绿 —— 环境问题，非产品缺陷，如实留档。
- 第一次活体探针写请求被 403 CSRF_REJECTED（Cookie 会话写请求必须带 X-Requested-With，
  auth.py:342-347）：补头后 19 步全过；该次 15950 uvicorn 因进程未即时退出而短暂共存，
  已 SIGTERM 回收并复核端口释放。
- 红基线快照缺 ai_registry 顶层包（git archive 只取了 demo/），从工作区拷入后跑通。

## 6. 产物

- docs/handoff/artifacts/uat028-dev/dev-result.json
- docs/handoff/artifacts/uat028-dev/logs/red-baseline-pytest.log
- docs/handoff/artifacts/uat028-dev/logs/green-full-suite.log
- docs/handoff/artifacts/uat028-dev/logs/green-regression.log
- docs/handoff/artifacts/uat028-dev/logs/uat-028-api.log
- docs/handoff/artifacts/uat028-dev/environment/cleanup.json

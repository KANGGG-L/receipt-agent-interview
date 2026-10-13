# API 约定

本文是后端接口的统一约定。新增或修改接口时按它来；`demo/tests/test_b1_platform.py` 里有对应的回归测试。

## 1. 响应结构

成功响应：`{"status": "success", ...}`，其余字段按接口各自定义，只增不删。

错误响应（所有错误都是同一个信封，由 `app/errors.py` 的全局处理器统一输出）：

```json
{"status": "error", "code": "SETTLEMENT_REQUIRED", "msg": "请先选择这张单是「现结」还是「挂账」，再保存。", "detail": "..."}
```

| 字段 | 给谁看 | 说明 |
|---|---|---|
| `code` | 程序 | 大写下划线，稳定不变，前端按它分支 |
| `msg` | 店员 | 人话：发生了什么 + 下一步怎么做。不出现接口路径、状态码、英文术语 |
| `detail` | 旧前端 | 字符串错误时等于 `msg`；请求校验错误时是 `[{loc, field, msg, type}]`，`loc` 末段是字段名 |
| 其它键 | 程序 | 按需附带，例如 `current_version`、`data`、`retry_after` |

规则：

- 业务拒绝一律用 4xx（缺信息 400/422、状态冲突 409、没权限 403、找不到 404），不再用 HTTP 200 + `status:"error"`。
- 服务端内部故障用 500，`msg` 不带异常原文，原文只进日志。
- 请求参数校验失败（422）由全局处理器转成中文，指出具体字段，例如「「单据总额」需要填数字，请检查后重新填写。」
- 401 表示没登录或登录过期，403 表示登录了但没权限，不要混用。
- 乐观锁：缺少 `version` 返回 400 `VERSION_REQUIRED`，版本不一致返回 409 `VERSION_CONFLICT`（带 `current_version`）。

## 2. 路径命名

- 资源名用名词，复数表示集合：`/api/receipts`、`/api/trash/receipts`；单个资源 `/api/receipt/{id}`。
- 新增接口的路径段用 kebab-case（`sku-duplicates`、`unit-conversions`），查询参数和 JSON 字段用 snake_case。
- 历史接口里有 snake_case 路径（`/api/save_edited`、`/api/price_history`、`/api/cost_report`），为了不破坏前端保持原样，不新增同类写法。
- 动作类操作挂在资源下用 POST：`/api/receipt/{id}/approve`、`/reverse`、`/flag`、`/unflag`。
- 不再提供同一个函数的两个路径别名。需要改路径时，旧路径保留一个版本并在代码注释和本文写明。

## 3. 权限与路径的对应

- `/api/admin/*` 只放超管（admin）接口。老板（owner）及以下角色的接口不挂在 `/api/admin` 下。
- 每个接口的最低角色见 `docs/permission-matrix.md`（由 `demo/scripts/permission_scan.py --write` 生成，测试会对比）。
- 租户只取自登录身份（生产态）。所有按租户的数据查询都必须带 `tenant_id` 条件，不能依赖进程上下文兜底。

## 4. 鉴权方式

两种方式都支持，二选一：

1. `Authorization: Bearer <令牌>`（旧前端沿用）。
2. 登录时下发的 HttpOnly Cookie `receipt_session`（`SameSite=Lax`，生产环境加 `Secure`）。
   - 只在 `AUTH_ENABLED=1` 时下发 Cookie。
   - Cookie 会话的写请求（POST/PUT/PATCH/DELETE）必须带自定义请求头 `X-Requested-With`（任意非空值），否则返回 403 `CSRF_REJECTED`。读请求不需要。
   - `POST /api/auth/logout` 吊销当前令牌并清除 Cookie；吊销记录落库，重启后依然有效。
3. 免密演示模式（`AUTH_ENABLED=0`）下用 `X-Role` 头切换角色，操作人固定记成「角色@demo.hk」，不采信客户端自报的邮箱。

登录失败连续 5 次后开始等待，每多失败一次等待翻倍（上限 15 分钟），返回 429 `LOGIN_THROTTLED` 和 `retry_after` 秒数。

## 5. 列表接口

- 列表接口不带分页参数时返回全部，保持向后兼容。
- 可选参数 `limit`（1 到 1000）、`offset`、`status`（可用逗号分隔多个）、`q`（搜索词）。
- 响应里带 `total`（过滤后的总条数，不受 `limit` 影响）；传了 `limit` 时还带 `limit`、`offset`。

## 6. 新增接口清单（本轮）

| 接口 | 角色 | 说明 |
|---|---|---|
| `POST /api/receipt/{id}/reverse` | 老板 | 冲销已入账单据，body `{version, reason}` |
| `POST /api/receipt/{id}/unflag` | 老板 | 解除「有问题」标记 |
| `GET /api/trash/receipts` | 老板 | 本租户回收站 |
| `POST /api/trash/receipts/{id}/restore` | 老板 | 恢复 |
| `DELETE /api/trash/receipts/{id}` | 老板 | 彻底删除 |
| `GET /api/inventory/sku-duplicates` | 老板 | 重复货品巡检（只读） |
| `POST /api/inventory/sku-duplicates/merge` | 老板 | 老板确认后合并 |
| `GET /api/funnel` | 老板 | 使用漏斗（只统计本租户） |
| `POST /api/auth/logout` | 登录用户 | 登出 |
| `POST /api/csp-report` | 匿名 | 浏览器上报内容安全策略违例 |
| `GET /api/admin/csp-violations` | 超管 | 违例统计 |

## 7. 已删除或改名的接口

| 接口 | 处理 |
|---|---|
| `GET /api/analytics/events` | 删除，与 `/api/analytics/events/logs` 是同一个函数，前端只用后者 |
| `GET /api/obs/kpi-trend` | 删除，与 `/api/analytics/kpi-trends` 重复 |
| `GET /api/admin/ai-metrics`、`GET /api/admin/grey-compare` | 删除，前端和测试都没有引用 |
| `GET /api/review`、`POST /api/chat/query`、`GET /api/insight-cards` | 删除，前端没有入口 |
| `GET /api/receipts/export` | 删除，导出统一走导出中心 |
| `GET /api/admin/trash`、`POST /api/admin/trash/{id}/restore` | 删除，改为老板可用的 `/api/trash/receipts*` |
| `GET /api/admin/funnel` | 改名为 `/api/funnel`，旧路径保留一个版本 |
| `POST /api/admin/maintenance/deduplicate` | 改为 `/api/inventory/sku-duplicates/merge`，旧路径保留一个版本 |

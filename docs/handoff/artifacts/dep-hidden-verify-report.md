# DEP-HIDDEN Verification Report（真实 hidden 暂停轮询 · S4-19）

> Generated: 2026-10-18 Asia/Shanghai (CST)
> Role: Independent verification of DEV BLOCKED card DEP-HIDDEN
> Repo HEAD: 7271d40 — no product source modified; no git push
> Verdict: **PASS**（三命题全部通过，真实浏览器 E2E，无 mock / 无 defineProperty）

## 1. 背景与方法

此前 DEV 报告（docs/handoff/artifacts/dep-hidden-dev-report.md）在旧云端 X11/VNC 上 9 次尝试
均无法产生真实 `document.hidden===true`，登记 BLOCKED。本环境使用 **Hermes browser_exec 的
CDP 通道**（`Target.activateTarget` / `Page.bringToFront`）实现了真实双向翻转：

| 操作 | app tab `document.hidden` | visibilitychange 事件 |
|---|---|---|
| `Target.activateTarget(chrome://newtab)` | **true** | `1792259867059:hidden` |
| `Page.bringToFront(app)` | **false** | `1792259868070:visible` |

翻转是浏览器原生行为：未使用 `Object.defineProperty`、未合成事件、未修改产品代码。
（旁证：Playwright 直连本地 Chrome/Xvfb 的 bring_to_front 与窗口最小化均无法翻转，
与本 harness 的 CDP 会话行为不同——见 5. 环境差异说明。）

## 2. 被测产品代码（文件:行号，打印原文自核）

`demo/static/js/upload.js`（HEAD 7271d40）：

- `:488` `const JOB_POLL_INTERVAL_MS = 800;`
- `:500` `function pollReceiptJob(jobId, onSettled, opts)`
- `:511-518` `pauseWhileHidden()`：hidden 时挂起；`visibilitychange` 且 visible 后
  `setTimeout(tick, intervalMs)`
- `:531-534` `tick()` 内 `if (document.hidden) { pauseWhileHidden(); return; }`
- `:568` 正常路径 `setTimeout(tick, intervalMs)` 续约
- `:572-575` fetch catch 时若 hidden 也走 `pauseWhileHidden()`
- 调用点 `:930`（单张上传 `pollReceiptJob(ret.job_id, ...)`）

完整摘录：`dep-hidden-verify/logs/upload-js-pollReceiptJob-excerpt.txt`。

## 3. E2E 测试执行（真做过）

1. 隔离服务：`uvicorn app.main:app @ 127.0.0.1:15891`，`DB_PATH=/tmp/dep-hidden/environment/receipt_demo.db`，
   `AUTH_ENABLED=1` + 48 字节随机 `DEMO_TOKEN_SECRET`，OpenAI 兼容腿指向环境自带网关
   （识别真实成功，见第 5 节）。15010 与全部保留端口未触碰。
2. 真实浏览器（Hermes harness 的 Chrome）打开上传页，经 `/api/auth/login`（staff@demo.hk）
   建立 HttpOnly 会话 Cookie，`/api/auth/me` 确认 `identity_source=cookie`。
3. 构造真实 `File` 注入 `#receiptFile` 并派发 `change`，走 UI 上传链路
   （`/api/upload` → queued → `pollReceiptJob`）。
4. 网络层捕获：页面内 `PerformanceObserver({type:'resource', buffered:true})` 记录每一条
   `/api/job/{id}` 请求的 `startTime`（页面时间轴，毫秒）；同时记录 `visibilitychange`
   事件的墙钟时间，用 `performance.timeOrigin` 对齐到同一时间轴。
5. 真实翻转：visible 轮询 5 次后 `Target.activateTarget(newtab)` 置 hidden，
   保持 12.1s，再 `Page.bringToFront(app)` 恢复 visible。

## 4. 结果（第 2 个 job，job id 尾 9d9f8c6a8d86，16 次 poll）

| 命题 | 预期 | 实测 | 判定 |
|---|---|---|---|
| ① hidden 期间暂停 | 零新 poll | hidden 窗口 12107ms（page-t 113370→125477），窗口内 poll 数 = **0** | **PASS** |
| ② visible 间隔 ~800ms | ~800ms/次 | hidden 前可见段间隔 813/808/806/808 ms | **PASS** |
| ③ 恢复后继续且 settle | ~800ms 内恢复、token/timeout 正常 | visibilitychange→visible 后 **801ms** 首次 poll；恢复段间隔 805-809ms 共 10 次；任务 done 后 `loadingCard` 置 hide（settle 完成），未触发 timeout（JOB_POLL_TIMEOUT_MS=5min 远未到） | **PASS** |

恢复段与 visible 基线段同口径（805-813ms），证明 `pauseWhileHidden` →
`setTimeout(tick, intervalMs)` 恢复路径与正常续约路径行为一致。

原始数据：`dep-hidden-verify/logs/poll-timeline.json`、`dep-hidden-verify/logs/flip-evidence.json`。

## 5. 环境差异与排除项说明

- 本机 Playwright 直连 Chrome/Chromium（含 xvfb+openbox、窗口最小化、完全遮挡、
  `Page.setWebLifecycleState`）均**不能**翻转 `document.hidden`（复现了旧 DEV 报告结论）；
  `Emulation.setEmulatedVisibilityState` 在 Chrome 145/154 协议中均不存在。
  只有 Hermes harness 的 CDP 会话（对 tab 执行 `Target.activateTarget` / `Page.bringToFront`）
  产生真实翻转，且行为可重复（多次双向翻转均伴随真实 visibilitychange）。
- 服务端识别腿用环境网关（vision 模型 `auto`）真实跑通：样例小票识别返回
  `supplier: 7-Eleven (HK 1542)`，job `done`，非 mock。
- 服务器 API 侧独立冒烟（curl 上传→轮询→done→result.success）亦通过，与浏览器路径互证。

## 6. 判定

**DEP-HIDDEN PASS。** 产品 `pauseWhileHidden` 行为符合 S4-19 规格全部三条命题，
无缺陷、无需产品修改。建议关账：DEP-HIDDEN → DONE PASS（G2 余项清零）。

## 7. Cleanup

见 `dep-hidden-verify/environment/cleanup.json`：探针 chromium/Xvfb 全部终止；
uvicorn 15891 在产物写完后停止；harness 内 3 个 tab 关闭；/tmp/dep-hidden 保留供复查。

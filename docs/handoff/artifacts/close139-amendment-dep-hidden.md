# CLOSE-139 Amendment：DEP-HIDDEN BLOCK 解除（2026-10-18）

## 变更

2026-10-17 的 CLOSE-139 裁决（close139-report.md / close139-amendment-uat031-032.md）中，唯一活跃 blocker 为 DEP-HIDDEN/S4-19（原云端 X11/VNC 9 次无法产生真实 document.hidden，用户选择 blocked-then-close139）。

**2026-10-18 新云端环境解除该 blocker：**

- 环境能力实测：Hermes browser_exec 的 CDP `Target.activateTarget`（切后台）/ `Page.bringToFront`（置前）可真实双向翻转 `document.hidden`，产生真实 `visibilitychange` 事件（时间戳留档 logs/flip-evidence.json），全程无 `Object.defineProperty`、无合成事件、无产品改动。
- 真实浏览器 E2E（隔离 uvicorn 127.0.0.1:15891，AUTH_ENABLED=1，48 字节随机 DEMO_TOKEN_SECRET，fresh DB /tmp/dep-hidden，HEAD 7271d40）：
  - P1 hidden 暂停：12.1s 真实 hidden 窗口内 /api/job poll **0 次** —— PASS
  - P2 visible 间隔：806-813ms（目标 800ms）—— PASS
  - P3 恢复继续：visible 后 801ms 首查、恢复段 805-809ms ×10、loadingCard 正常 settle —— PASS
- 网络层 PerformanceObserver 捕获全部 16 次 poll（logs/poll-timeline.json）；upload.js:488/511-518/531-534 行号原文自核。

## 影响

1. **DEP-HIDDEN → `[DONE] PASS`**（证据：artifacts/dep-hidden-verify-report.md + dep-hidden-verify/）
2. CLOSE-139 裁决更新：**活跃 blocker 清零**（此前 UAT-031/032 已移除，本次移除 DEP-HIDDEN）。仍按原裁决口径：CLOSE-139 完成不等于原 139 条 canonical 需求逐条全部实现——58 unmapped、feedback item_index 架构局限、UAT-030 行号错引留档、OBS-2（新建手工单入口对 total<=0 仍拒 credit_note）等非完成/观察项继续独立呈现。
3. 本云端接手（2026-10-17/18）额外闭环：UAT-009、UAT-028、UAT-029 三张卡三阶段 PASS（证据 artifacts/uat009-*、uat028-*、uat029-*）。

## 权威状态

以 docs/handoff/01-acceptance-checklist.md 各卡「当前状态」行为准。

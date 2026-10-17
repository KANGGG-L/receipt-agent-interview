接手任务：收据 AI 识别 SaaS 原型缺陷修复流水线。**先读 `docs/handoff/00-README.md` 与 `06-usage-stop-handoff.md`。**

上一环境因 Cursor usage ≈90% 于 2026-10-17 19:58 Asia/Shanghai 停机；UAT-008 Dev 被中断。

## 你的角色

只担任调度器（Scheduler / Gatekeeper）。代码、测试、验收全部派发给独立 Subagent；你不亲自写产品代码或跑测试。

## 流水线契约（不可违反）

1. **单向门禁**：开发 -> 独立 QA -> 独立 UAT。FAIL 打回开发。
2. **三阶段互不复用**：QA/UAT 全新隔离端口、DB、fixture。
3. **调度器只收摘要**；产物落盘到 `docs/handoff/artifacts/`。
4. **Git**：默认不写；本轮用户已授权在 usage≈90% 时 commit+push。若尚未 push，先清理 artifacts 垃圾再提交。
5. 禁用 Emoji；Vendor_A/B；密钥勿入库（`/home/box/.config/receipt-agent/`）。

## 当前进度（相对 2026-10-16 旧交接已大变）

- W3 / W4 / ENG-05 / DEP-MODEL 已闭环；UAT-031/032 已 PASS；CLOSE-139 已 `ADJUDICATED_WITH_DOCUMENTED_BLOCKERS`。
- 原 PARTIAL（ENG-02/03、UAT-014/016）已清为 PASS。
- **DEP-HIDDEN** 仍 `[BLOCKED] UNVERIFIED`（本机无法真实 hidden）。
- **UAT-008 已 [DONE] PASS；仍 NOT_RUN：UAT-009（DEV 半成品已提交未验证，下一步续做）、UAT-028、UAT-029。**

逐项以 `01-acceptance-checklist.md` 卡片「当前状态」为准。

## 第一件事

1. `git status` / 确认是否已 push。
2. **清理** `docs/handoff/artifacts/**/venv`、`**/node_modules`、`*.db*`（否则约 3GB 不可 push）。
3. 若用户要保留进度：commit 全部产品修复 + handoff + 精简后的报告 md，然后 push `origin/main`。
4. 派 **UAT-008**（实收 0 与入账算术校验）走完 Dev→QA→UAT。
5. 然后 UAT-009 → UAT-028 → UAT-029；必要时在其他桌面复验 DEP-HIDDEN。

## 必须继承的纪律

1. 权威行号必须逐条自核。
2. 空 Authorization ≠ 未认证（身份在 Cookie）。
3. 声称的测试必须真做过；真实模型 AC 禁止用 mock 冒充 PASS。
4. 保留端口约定：勿占用/勿向 15010 业务库写测试数据。

## 最终目标

CLOSE-139 已裁决但**禁止**宣称原 139 项目标全部完成。未完成项与 DEP-HIDDEN 必须独立呈现。

## 首次汇报只需给出

已读交接确认状态、git/清理/push 情况、UAT-008 派发情况、阻塞点、下一步与证据路径。

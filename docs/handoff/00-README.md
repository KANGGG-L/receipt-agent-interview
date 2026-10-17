# 交接说明：本机调度流水线状态（转下一环境接手用）

> 生成时间：2026-10-17 19:58 (Asia/Shanghai / CST)
> 触发：Cursor usage 约 90%，用户要求停止所有任务；交接文档已刷新。**commit/push 由用户另行确认或按既有 90% 指令执行。**
> 权威状态以本文件 + `01-acceptance-checklist.md` 各卡片「当前状态」行为准；早期表格与 2026-10-16 快照已过期。

---

## 1. 任务背景与最终目标

仓库 `receipt-agent-interview`：收据 AI 识别与库存管理 SaaS 原型（面试项目）。目标是按四阶段门禁把 45 项待修计划做实，并以 **CLOSE-139** 对原始 139 条 canonical 需求逐 ID 裁决。

```
产品 -> 开发 -> 独立 QA -> 独立 UAT
```

硬约束：单向门禁；QA/UAT 全新端口/DB/fixture；调度器不写产品代码；禁用 Emoji；Vendor_A/B；密钥在 `/home/box/.config/receipt-agent/`（勿入库）。

## 2. 当前进度（2026-10-17 停机快照）

| 波次 | 状态 |
|---|---|
| D0 / W1 / W2 / W3 / W4 | 计划内卡片基本已闭环（含 UAT-031/032） |
| G1 ENG-05 | `[DONE] PASS`（根+Demo 全量三阶段） |
| G2 DEP-MODEL | `[DONE] PASS`（真实模型 9/9） |
| G2 DEP-HIDDEN | `[BLOCKED] UNVERIFIED`（本机无法翻转 `document.hidden`） |
| G3 CLOSE-139 | `[DONE] ADJUDICATED_WITH_DOCUMENTED_BLOCKERS`（已修订去掉 UAT-032 阻塞） |

**45 张验收卡粗计：** 约 39 `[DONE] PASS`；CLOSE-139 已裁决（带文档化阻塞）；DEP-HIDDEN BLOCKED；**仍 NOT_RUN：UAT-008、UAT-009、UAT-028、UAT-029**。

原 4 项 PARTIAL（ENG-02/03、UAT-014/016）已在真实路径或 SPEC_KEEP 复验后清为 PASS。

## 3. 停机时正在做的事

- 用户指令：usage 到约 90% → **停止所有任务 → commit → push**。
- 停机时 **UAT-008 Dev** 已被 `StopSubagent` 中断（`[TODO] NOT_RUN`，无完整 DEV 报告）。
- 按 `partials-triage.md` 顺序，UAT-005 已本地三阶段 PASS；**下一张本应是 UAT-008**，其后 UAT-009、UAT-028、UAT-029。

## 4. 下一环境第一件事

1. 读本文件与 `05-cloud-handoff-prompt.md`（已同步刷新）。
2. 确认工作区未提交改动：`git status`（大量 `demo/` 产品修复 + `docs/handoff/artifacts/` 证据；**artifacts 约 3.1G，含误落盘 venv/node_modules/*.db，push 前必须清理**）。
3. 若尚未 push：清理产物垃圾 → 写 commit → push `origin/main`（用户已授权 usage-90% 场景）。
4. 然后派 **UAT-008** Dev→QA→UAT（实收 0 算术校验）。

## 5. 仍开放的阻塞 / 缺口

| 项 | 说明 |
|---|---|
| DEP-HIDDEN / S4-19 | 本机 X11/VNC 9 次无法 `document.hidden===true`；用户选择 `blocked-then-close139`；需其他桌面复验 |
| UAT-008/009/028/029 | checklist 仍 NOT_RUN |
| CLOSE-139 | 已裁决但**不等于**原 139 全完成；见 `artifacts/close139-report.md` 与 amendment |
| artifacts 体积 | 删掉 `**/environment/venv`、`**/node_modules`、`*.db*` 后再提交 |
| 密钥 | `/home/box/.config/receipt-agent/{openai_rec.env,model.env}`；DEP-MODEL 已验证可用 |

## 6. 关键证据索引

- 清单：`01-acceptance-checklist.md`
- 台账：`02-coordinator-ledger.md`
- 分流：`artifacts/partials-triage.md`
- 终审：`artifacts/close139-report.md`、`artifacts/close139-amendment-uat031-032.md`
- 各卡报告：`artifacts/<card>-{dev,qa,uat}-report.md`（约 100+）

## 7. 同目录文档

| 文件 | 用途 |
|---|---|
| `00-README.md` | **本文件**（状态快照） |
| `01-acceptance-checklist.md` | 45 卡权威状态 |
| `02-coordinator-ledger.md` | 调度流水 |
| `03-takeover-report.md` | 早期接管报告（历史） |
| `04-open-defect-citation-fix-spec.md` | UAT-030 行号错引更正 |
| `05-cloud-handoff-prompt.md` | **下一 agent 可粘贴的调度 prompt** |
| `06-usage-stop-handoff.md` | 本次 usage 停机短摘要 |

# e2e-v2 内置浏览器全程留证

> 本目录为 2026-08-21 20:00 “重跑：全程使用 Orca 内置浏览器” 的 snapshot 证据，替代此前 `orca computer` (桌面) 因 TCC 辅助功能阻塞的 `screencapture+curl` 混合方案。

**浏览器面**：`orca tab list` → `browserPageId 298ca6d6-4ac5-4709-82ba-df93312a6d05 url http://127.0.0.1:15010 title 香港餐饮 AI...` (Orca 内置，非外部 Chrome)
**证据形式**：`orca snapshot --json` (accessibility tree + refs e1..eXX) + `orca click/select/upload/eval` 操作日志，每步一 JSON，可被 `HANDOVER` 逐条审计。

## 关键轨迹

| 文件 | 对应 HANDOVER 步骤 | 操作 | 断言 |
|---|---|---|---|
| a-00-baseline.json | A-00 | snapshot 基线 admin | 当前角色 admin, 上传/拖拽收据 e12, 新建手工单 e13 |
| a-02-role-staff-select.json | A-01 | `select --element e1 --value staff` | combobox `● staff·店员` selected, 保存 admin 引擎 tab 仍可见(缺陷) |
| a-03d-revealed2.json + b | A-02 | `eval style.setProperty display block !important` | refs 新增 `e12 Choose files` (file input 可见) |
| a-04-uploaded.json | A-03 | `upload --element e12 --files IMG_5809.jpg` | `uploaded:1` CDP 成功 |
| a-05/06/07 | A-03 | `click e25 全选` → `click e33` 单选 | 解析按钮 `e17 解析 2 → 解析 1` |
| a-08-after-parse-click.json | A-04 | `click e17 解析 1` | BatchUploader.photos `IMG_5809 uploading + stamp pending`, 网络 `GET /api/job/d4e7632c 200` 轮询 |
| b-01b-inventory-eval.json | B-01 | `eval click [data-target=tab-inventory]` (click e3 失效, eval 有效) | snapshot 含 `AI 发现 / 库存食材种数 5 / 白菜 / 本地新鲜菜心_1787140411` |
| b-02-inventory-detail.json | B-02 | `eval #inventoryTableBody` | 4 行 `白菜 20斤 / 本地新鲜菜心 50斤 / _1787140411 50斤 / 红虾 21.5斤` |
| c-00-supplier.json | C-01 | `eval click tab-archive` | 供应商档案 3 行 + 归档表 + `编辑` 按钮 e127 |
| c-01-edit.json | C-02 | `click e127 编辑` | modal `编辑供应商` + `取消/保存` |
| e-01-engine.json | E-01 | `select admin + eval click tab-engine` | 引擎配置 opencode CLI + 灰测 28 脱敏样本 |
| f-00-scan.json | F-01 | `eval click tab-scan` + snapshot | `点赞/点踩 in snapshot false`, `priceHistoryChart true`, `stocktakeModal true` via `eval innerHTML` |

**最终 job**：`d4e7632c` 经 15*2s 轮询 `running → done`，生成 `receipt 29 新鴻興 1080 parsed` (与此前 `receipt 20` 同源，证明内置浏览器上传→解析全链路与 API 直传一致)。

**与 v1 差异**：v1 用 `orca computer` (外部 Chrome) + `screencapture` 因 `Orca Computer Use.app` TCC 阻塞 `get-app-state`；v2 全程 `orca snapshot/click/select/upload/eval` (内置) 无 TCC 阻塞，符合 `orca-cli SKILL.md` “Prefer `orca tab` for worktree browser, use `orca computer` only for desktop UI outside Orca”。

# UAT-002 开发修正证据报告

> 生成时间：2026-10-17 (Asia/Shanghai)
> 范围：调度门禁拦下的三处缺陷；独立 QA / UAT 未启动（由调度器另派）
> 约束：无 git commit / push；供应商别名 Vendor_A / Vendor_B；无 Emoji

## 结论总览

| 缺陷点 | 裁决 |
|---|---|
| 1. title 无条件覆盖导致点击提示丢失 | **PASS** |
| 2. `textContent === '待审核'` 脆弱 shim | **PASS**（源头改标签 + 删除 shim + data 属性） |
| 3. 「待审核」残留 / 「待我审批」vs「待老板审批」 | **PASS**（消除残留；判定为角色第一人称同义并留证） |

## 缺陷 1：功能回归 — title 必须同时可见

**Before（缺陷态）** `demo/static/js/supplier_archive.js` 无条件赋值覆盖 HTML title：

```
card.title = '统计口径：归档待处理全量单据（包含已上传、识别中、待核对、待核对（有疑问）、待老板审批共5种待处理状态）';
```

**After（自核行原文）**

- `demo/static/js/supplier_archive.js:320` — `            const clickHint = (card.getAttribute('data-click-hint') || '').trim()`
- `demo/static/js/supplier_archive.js:321` — `                || '点击筛选待处理单据';`
- `demo/static/js/supplier_archive.js:322` — `            card.title = clickHint + '。' + statsPart;`

合并规则：`card.title = clickHint + '。' + statsPart`，其中 `clickHint` 优先取 `data-click-hint`，否则回落「点击筛选待处理单据」。

**HTML 源头同步**（去掉「待审核」用词）：

- `demo/templates/index.html:1054` — `                        <div class="metric-card csp-s-73" role="button" tabindex="0" data-todo-metric="pending" data-click-hint="点击筛选待处理单据" data-csp-click="[{&quot;op&quot;:&quot;call&quot;,&quot;f&quot;:&quot;todoFilter&quot;,&quot;a&quot;:[{&quot;t&quot;:&quot;lit&quot;,&quot;v&quot;:&quot;pending&quot;}]}]" title="点击筛选待处理单据">`

**冒烟结果（Node harness）**：合并后 title 实测为：

`点击筛选待处理单据。统计口径：归档待处理全量单据（包含已上传、识别中、待核对、待核对（有疑问）、待老板审批共5种待处理状态）`

点击提示与 5 态口径同时存在。

## 缺陷 2：脆弱耦合 — 删除字符串 shim，改源头标签

**Before**：`if (labelEl.textContent.trim() === '待审核') { labelEl.textContent = '待处理（待办全量）'; }`

**After**：

1. 源头标签改为稳定文案（自核）：
   - `demo/templates/index.html:1055` — `                            <div class="csp-s-74">待处理（待办全量）</div>`
2. 卡片增加稳定 data 属性：`data-todo-metric="pending"`、`data-click-hint="点击筛选待处理单据"`（见 :1054）。
3. `supplier_archive.js` **已删除** `textContent.trim() === '待审核'` shim；卡片定位改为：
   - `demo/static/js/supplier_archive.js:317` — `        const card = pendingEl.closest('[data-todo-metric="pending"]') || pendingEl.closest('.metric-card');`

与 `review_core.js:1630-1633`「登记一次、禁止多处漂移」一致：展示文案以 HTML / terms 权威为准，JS 不再焊死精确中文字符串。

静态核验：`rg "textContent.trim() === '待审核'" demo/static/js/supplier_archive.js` → 无命中。

## 缺陷 3：术语 — 消除「待审核」残留；判定「待我审批」为同义

### 3a. 权威状态 label（自核）

- `demo/static/js/terms.js:15` — `            edited: { label: '待老板审批', title: '店员已核对并提交，等老板审核入账', filter: '待老板审批' },`

### 3b. 待办条「待审核」残留 — 已消除

- Before：`index.html:1054` title 含「待审核」；`:1055` 标签为「待审核」。
- After：todoBar 区段内无「待审核」；标签为「待处理（待办全量）」；title/data-click-hint 为「点击筛选待处理单据」。

说明：归档待办卡统计的是 **5 态 pending 聚合**，不是单一 `edited` 状态，故不得用「待审核/待老板审批」作为该卡主标签；采用「待处理（待办全量）」与口径枚举一致。

### 3c. 「待我审批」vs「待老板审批」— 判定为同义（不同人称），保留队列按钮

- `demo/templates/index.html:182` — `                                <button type="button" id="queueTabApprove" class="btn btn-sm btn-secondary queue-tab owner-only csp-s-18" data-queue-filter="to_approve" data-csp-click="[{&quot;op&quot;:&quot;call&quot;,&quot;f&quot;:&quot;setWorkbenchQueueFilter&quot;,&quot;a&quot;:[{&quot;t&quot;:&quot;lit&quot;,&quot;v&quot;:&quot;to_approve&quot;}]}]">待我审批 (<span id="qCountApprove">0</span>)</button>`

**判定依据**：

1. 并列按钮 `queueTabReview` 文案为「待我复核」（角色第一人称队列视角），并非 terms.js 某一单一 status label；「待我审批」与之对称，指向 owner 队列 `data-queue-filter="to_approve"`。
2. 状态权威名仍为「待老板审批」（terms.js / 徽章 / 保存成功提示）；`workbench_queue.js` 的 tab title 已用「仅待老板审批单据 … status=edited」对齐权威口径。
3. 若强行把按钮改为「待老板审批」，会破坏与「待我复核」的人称对称，且把「队列视角」与「状态标签」混为一谈。

**一致性约定（本轮采纳）**：

| 场景 | 文案 | 性质 |
|---|---|---|
| 单据 status=`edited` 徽章 / terms | 待老板审批 | 权威 |
| Owner 队列筛选按钮 / 今日看板入口 | 待我审批 | 角色第一人称同义 |
| 归档待办聚合卡（5 态） | 待处理（待办全量） | 聚合口径标签（非单一状态） |

`finance.js` 今日看板「待我审批」同属角色人称同义，本轮未改（不在三处缺陷的必改清单内）。

## 测试夹具同步

`demo/tests/test_uat_002_today_archive_scope_labels.py`：

- harness mock 改为源头标签「待处理（待办全量）」+ `data-click-hint` / `data-todo-metric`（不再依赖 shim 改写「待审核」）。
- `test_03` 增补断言：title 同时含「点击筛选」与 5 态口径；label 等于源头文案。
- `test_04` 增补静态断言：index.html 待办卡无「待审核」、shim 字符串不存在、保留「待我审批」同义。

本环境未安装 pytest/fastapi，已用独立 Node harness + 静态扫描冒烟通过（见上）。完整 pytest 由独立 QA 阶段执行。

## 变更文件清单

| 文件 | 动作 |
|---|---|
| `demo/static/js/supplier_archive.js` | 合并 title；删除 shim；data-todo-metric 定位 |
| `demo/templates/index.html` | 源头标签/title/data 属性 |
| `demo/tests/test_uat_002_today_archive_scope_labels.py` | harness 与断言对齐新契约 |
| `docs/handoff/artifacts/uat002-dev-fix.md` | 本报告 |

未改：`terms.js`（权威已正确）、`workbench_queue.js`（口径 title 已正确）、`admin_observation.js`（上轮已对齐）。

## 残留风险

1. **同义双轨**：UI 仍同时出现「待我审批」与「待老板审批」。已文档化；若产品后续要求完全单轨，需另开卡片统一队列按钮与今日看板，并同步 i18n。
2. **`csp-s-74` 类名耦合**：label 仍用样式类选取于其它路径；本轮待办卡已用 `data-todo-metric` 定位卡片，但未引入独立 `data-metric-label`。可接受，建议 W3 后续清理 csp 类业务语义。
3. **updateTodoBar 重入**：每次调用用 `data-click-hint` 重建 title，不依赖「读旧 title 再追加」，避免重复拼接「统计口径」。若有代码清掉 `data-click-hint`，会回落默认点击文案，口径仍在。
4. **未跑独立 QA/UAT**：计数器口径不相等、租户隔离等 API 用例需在隔离端口由 QA/UAT agent 重跑；本报告仅覆盖三处门禁缺陷的开发修正与本地 Node/静态冒烟。
5. **白名单外历史改动**：上轮 worker 改过 `admin_observation.js`（方向正确已保留）；本轮未再触碰。

## 调度器下一步建议

1. 派独立 QA（新端口 / 新库 / 新 fixture）复跑 UAT-002 验收与 `test_uat_002_today_archive_scope_labels.py`。
2. QA 全绿后再派独立 UAT。
3. 勿将本冒烟结果计为 QA/UAT PASS。

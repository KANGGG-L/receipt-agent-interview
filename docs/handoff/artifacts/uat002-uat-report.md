# UAT-002 Independent UAT Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation of the three gate defects fixed for UAT-002
> Prior gates (not reused): `uat002-dev-fix.md`, `uat002-qa-report.md` (QA port 15150)

## Overall gate (three acceptance behaviors)

| Behavior | Verdict |
|---|---|
| 1. Pending todo metric card hover title shows click hint AND 统计口径 | **PASS** |
| 2. Label shows 待处理（待办全量） via source HTML + `data-todo-metric`; no 待审核 shim | **PASS** |
| 3. Terminology consistent with acceptance card / `terms.js` rationale | **PASS** |

**Three-behavior UAT gate: PASS.**

This closes the three cloud-gate defects for UAT-002 from an acceptance perspective. It does **not** re-claim full-card API counter / approve-path green (see Residual); those were env-blocked in QA and were not re-run here as product-code defects for this gate.

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15160** (QA used 15150; not reused) |
| Aux port | 15161 (static harness host for Chrome only) |
| DB | `docs/handoff/artifacts/uat002-uat/environment/uat_isolated.db` (fresh empty) |
| Fixture | Synthetic Vendor_A / Vendor_B in Node + Chrome harness only |
| Venv | `docs/handoff/artifacts/uat002-uat/environment/venv` (self-built) |
| Server pid | 105470 (stopped at end) |

## Commands actually run

1. Self-built uvicorn on 127.0.0.1:15160 with fresh `DB_PATH` — `GET /` HTTP 200
2. curl served `index.html`, `supplier_archive.js`, `terms.js` — log `logs/served-html-smoke.txt`
3. Independent Node CJS+vm harness on **served** JS — log `logs/node-title-merge.txt` — `NODE_HARNESS_PASS=true`
4. Chrome headless `--dump-dom` against harness HTML loading served JS from :15160 — `TITLE_TAG UAT002_PASS`, result `logs/chrome-result.json`
5. Source residual scan: `待审核` absent from `index.html` + `supplier_archive.js`; shim string absent
6. Full cleanup: stop pids, free ports, `environment/cleanup.json`

Did **not** run: pytest API approve path (`test_01`/`test_02`/`test_05`); interactive GUI hover on logged-in full app shell.

## Defect 1 — hover title: click hint + 统计口径

Self-checked citations (exact line text):

- `demo/static/js/supplier_archive.js:317` — `        const card = pendingEl.closest('[data-todo-metric="pending"]') || pendingEl.closest('.metric-card');`
- `demo/static/js/supplier_archive.js:320` — `            const clickHint = (card.getAttribute('data-click-hint') || '').trim()`
- `demo/static/js/supplier_archive.js:322` — `            card.title = clickHint + '。' + statsPart;`
- `demo/templates/index.html:1054` — pending card has `data-click-hint="点击筛选待处理单据"` and `title="点击筛选待处理单据"` and `data-todo-metric="pending"`

Runtime (Chrome + Node, same measured title):

`点击筛选待处理单据。统计口径：归档待处理全量单据（包含已上传、识别中、待核对、待核对（有疑问）、待老板审批共5种待处理状态）`

Contains both `点击筛选` and `统计口径` / 5-state enum including `待老板审批`. Pending count under synthetic uploaded+edited+approved = **2**.

## Defect 2 — label source + data attr; no shim

Self-checked:

- `demo/templates/index.html:1055` — `                            <div class="csp-s-74">待处理（待办全量）</div>`
- `rg "textContent.trim() === '待审核'" demo/static/js/supplier_archive.js` → **NO_HIT**
- Card locator uses `data-todo-metric="pending"` (`supplier_archive.js:317`)

Runtime: after `updateTodoBar()`, label remains `待处理（待办全量）` (no rewrite); Chrome result `dataTodoMetric: "pending"`, `no待审核_label: true`.

## Defect 3 — terminology vs terms.js / queue first-person

Self-checked:

- `demo/static/js/terms.js:15` — `            edited: { label: '待老板审批', title: '店员已核对并提交，等老板审核入账', filter: '待老板审批' },`
- `demo/templates/index.html:182` — `queueTabApprove` text `待我审批` with `data-queue-filter="to_approve"`
- todoBar / `index.html` / `supplier_archive.js`: **0** hits for `待审核`
- `workbench_queue.js:79` (supporting, printed via rg): tab title uses `统计口径：仅待老板审批单据 ... status=edited`

Acceptance rationale held: aggregate card = 待处理（待办全量）; authority status = 待老板审批; owner queue first-person = 待我审批 (symmetric with 待我复核). Dual-track documented, not treated as drift for this gate.

## Checklist card note

`docs/handoff/01-acceptance-checklist.md` UAT-002「当前状态」still shows pre-fix wording (`[IN_PROGRESS] ... 待修正后进入独立 QA`). Scheduler should update the card after this UAT; UAT agent did not edit the checklist.

Positive criteria about synthetic uploaded/edited/approved inequality and approve decrement were covered in harness counting (2 vs would-be 1 for edited-only) at UI title/label layer; **full HTTP approve state-machine path was not executed** in this UAT (honest empty for that sub-path).

## Residual risks (honest)

1. No logged-in full-app MANUAL_UI session: hover verified via Chrome executing real served `updateTodoBar` on a minimal DOM that mirrors the production card markup, plus HTTP-served source attributes. Interactive click-to-filter on archive pane not exercised.
2. API approve / tenant isolation pytest paths not re-run (prior QA env gap on `langchain_community`). Not required to prove the three label/title defects; do not cite as UAT API PASS.
3. Dual-track copy (`待我审批` vs `待老板审批`) remains by design for this gate.
4. Checklist card status text is stale until scheduler updates it.

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 105470, http.server 105988) |
| Port 15160 free | true |
| Port 15161 free | true |
| pid alive after stop | false |
| Orphans | none |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat002-uat/environment/cleanup.json` |

No git commit / push. No secrets copied into working tree.

## Scheduler signal

- **UAT three-behavior gate: PASS**
- Evidence: `docs/handoff/artifacts/uat002-uat-report.md`
- Supporting dir: `docs/handoff/artifacts/uat002-uat/`
- Next: scheduler may mark UAT-002 card closed for the three gate defects and proceed W3 remaining items; optional follow-up for full API pytest under complete deps is separate from this gate.

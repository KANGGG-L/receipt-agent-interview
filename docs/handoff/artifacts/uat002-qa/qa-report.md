# UAT-002 Independent QA Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify the three gate defects fixed in `uat002-dev-fix.md` only
> UAT: NOT started (per dispatch)

## Overall gate (three defects)

| Defect | Verdict |
|---|---|
| 1. Hover title contains BOTH click hint and 统计口径 | **PASS** |
| 2. No `textContent==='待审核'` shim; stable `data-todo-metric`; labels match | **PASS** |
| 3. Terminology: no residual 待审核 on todoBar; 待我审批 vs 待老板审批 rationale holds | **PASS** |

**Three-defect gate: PASS.** Do not treat this as full UAT-002 card close; API counter / approve-path tests in the same file are env-blocked (see residual).

## Commands run (actually executed)

1. Static evidence scan (rg/sed/python todoBar residual check)
   - Log: `docs/handoff/artifacts/uat002-qa/logs/static-evidence.txt`
2. `pytest tests/test_uat_002_today_archive_scope_labels.py -v --tb=short`
   - CWD: `demo/`
   - Venv: `docs/handoff/artifacts/uat002-qa/environment/venv` (minimal deps; full `requirements-dev.txt` failed on tiktoken/pyo3 vs Python 3.13)
   - Fresh `DB_PATH=.../qa_pytest.db`
   - Log: `docs/handoff/artifacts/uat002-qa/logs/pytest-uat002.txt`
   - Result: **2 passed, 3 failed**
     - PASSED: `test_03_scope_labels_and_hover_explanations_in_ui_components`
     - PASSED: `test_04_terminology_consistency_for_edited_status`
     - FAILED: `test_01`, `test_02`, `test_05` — `ModuleNotFoundError: No module named 'langchain_community'` on `POST /api/receipt/{id}/approve` (`demo/app/api_receipts.py:2232`). Env gap in QA venv, not a label-fix regression.
3. Standalone Node harness (independent of pytest) exercising `updateTodoBar` + counters
   - Log: `docs/handoff/artifacts/uat002-qa/logs/node-title-merge.txt`
   - Exit 0; `NODE_HARNESS_PASS=true`
   - Measured title: `点击筛选待处理单据。统计口径：归档待处理全量单据（包含已上传、识别中、待核对、待核对（有疑问）、待老板审批共5种待处理状态）`
4. Isolated uvicorn smoke (self-built; no local isolate.py)
   - Port **15150**, fresh DB `qa_isolated_smoke.db`, pid 103056
   - `GET /` HTTP 200; served HTML/JS smoke PASS
   - Log: `docs/handoff/artifacts/uat002-qa/logs/served-html-smoke.txt`, `uvicorn-15150.log`
   - `/api/health` returned 500 (likely same missing optional deps); index static path sufficient for these three defects

## Defect 1 evidence — title merge

Self-checked citations (exact line text):

- `demo/static/js/supplier_archive.js:317` — `        const card = pendingEl.closest('[data-todo-metric="pending"]') || pendingEl.closest('.metric-card');`
- `demo/static/js/supplier_archive.js:320` — `            const clickHint = (card.getAttribute('data-click-hint') || '').trim()`
- `demo/static/js/supplier_archive.js:322` — `            card.title = clickHint + '。' + statsPart;`
- `demo/templates/index.html:1054` — pending card has `data-click-hint="点击筛选待处理单据"` and `title="点击筛选待处理单据"` (and `data-todo-metric="pending"`)

Runtime:

- Node harness title contains both `点击筛选` and `统计口径` / `待老板审批` (5-state enum).
- `test_03` PASSED (asserts click hint + 5-state wording + label).
- Served JS on :15150 line 322 identical merge expression.

## Defect 2 evidence — shim removed; stable data attr; labels match

- `rg "textContent.trim() === '待审核'" demo/static/js/supplier_archive.js` → **NO_SHIM_HIT**
- `demo/templates/index.html:1055` — `                            <div class="csp-s-74">待处理（待办全量）</div>`
- Card locator uses `data-todo-metric="pending"` (`supplier_archive.js:317`)
- Node harness: `label === '待处理（待办全量）'` after `updateTodoBar` (no shim rewrite)
- `test_04` PASSED (asserts shim string absent; HTML source label present)

## Defect 3 evidence — terminology

- todoBar region: `待审核` **absent**; `待处理（待办全量）` present; `data-todo-metric="pending"` present (static + served HTML)
- Entire `demo/templates/index.html` and `demo/static/js/supplier_archive.js`: **0** hits for `待审核`
- Authority status label: `demo/static/js/terms.js:15` — `            edited: { label: '待老板审批', title: '店员已核对并提交，等老板审核入账', filter: '待老板审批' },`
- Queue first-person button retained: `demo/templates/index.html:182` — `queueTabApprove` text `待我审批` with `data-queue-filter="to_approve"`; symmetric with `待我复核` on `queueTabReview`
- Node harness `queueTabApproveTitle` includes `待老板审批` / `status=edited` (1-state scope), supporting dual-track rationale
- `test_04` PASSED (asserts 待我审批 retained as first-person synonym, not treated as edited-label drift)

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | 15150 (fresh; not reused from prior stage) |
| DB | `docs/handoff/artifacts/uat002-qa/environment/qa_isolated_smoke.db` (+ separate `qa_pytest.db` for pytest) |
| Fixture | Synthetic data only inside Node/pytest; Vendor_A / Vendor_B aliases |
| Stop | `STOPPED_OWNED_TEST_PROCESS`, pid 103056 dead |
| Port free | true (`ss` LISTEN empty; SO_REUSEADDR bind ok) |
| Browser tabs | 0 opened; `browser_tab_closed=true` |
| 15010 | not present on this host; untouched |
| cleanup.json | `docs/handoff/artifacts/uat002-qa/environment/cleanup.json` |

No orphan uvicorn left. No git commit / push.

## Residual risks (honest)

1. **API path tests red in this QA venv**: `test_01` / `test_02` / `test_05` failed solely on missing `langchain_community` during approve. Not attributed to the three label defects. Full-stack re-run with complete deps (or ABI3 tiktoken workaround) recommended before claiming whole-file green.
2. **No real browser / Orca interaction**: HTML/JS verified via static + Node VM + curl of isolated index/static assets. Full MANUAL_UI hover/click remains for independent UAT.
3. **Dual-track copy**: UI still shows both `待我审批` (queue/first-person) and `待老板审批` (status authority). Documented and accepted for this gate; product may later unify.
4. **`/api/health` 500** on isolated smoke under minimal venv — static delivery still 200; do not cite health as PASS.
5. Checklist card status in `01-acceptance-checklist.md` still reads pre-fix wording; scheduler should update after UAT, not from this QA alone.

## Scheduler signal

- **QA three-defect gate: PASS**
- Evidence path: `docs/handoff/artifacts/uat002-qa-report.md`
- Supporting logs/dir: `docs/handoff/artifacts/uat002-qa/`
- **UAT: not started**
- Next: independent UAT on a **new** port / DB / fixture (do not reuse 15150 env)

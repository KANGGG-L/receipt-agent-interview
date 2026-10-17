# UAT-003 Independent QA Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-003 acceptance only (inventory ledger name truncation)
> UAT: NOT started (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Synthetic_Rice and Synthetic_Oil distinguishable in ledger UI/API | **PASS** |
| 2. Full original name accessible (title/data-full-name), underscore + long Chinese | **PASS** |
| 3. Primary business name not replaced by ID; cross-tenant boundary holds | **PASS** |

**UAT-003 QA gate: PASS.** Do not treat this as card close; independent UAT (real browser MANUAL_UI) not started.

## Commands run (actually executed)

1. Static evidence scan (printed file:line + nameCell/CSS assertions)
   - Log: `docs/handoff/artifacts/uat003-qa/logs/static-evidence.txt`
2. `pytest tests/test_inventory_ledger_api.py -v --tb=short`
   - Venv: `docs/handoff/artifacts/uat003-qa/environment/venv` (fresh for this stage)
   - Fresh `DB_PATH=.../qa_pytest.db` (module also uses its own temp DBs)
   - Log: `docs/handoff/artifacts/uat003-qa/logs/pytest-uat003.txt`
   - Result: **7 passed** (including `test_uat003_api_keeps_distinct_full_names_with_shared_prefix` and `test_uat003_ui_ledger_name_cell_exposes_full_original`; also `test_tenant_isolation`)
3. Isolated uvicorn smoke (self-built; fresh port/DB)
   - Port **15170** (not 15150/15160), DB `qa_isolated_smoke.db`, pid 111488
   - `GET /` 200; served JS/CSS contain UAT-003 markers
   - Log: `docs/handoff/artifacts/uat003-qa/logs/uvicorn-15170.log`
4. Live API seed + verify on :15170
   - Seeded Synthetic_Rice / Synthetic_Oil / long ZH underscore name (Vendor_A / Vendor_B); t2 Synthetic_Rice qty=9
   - `GET /api/inventory/logs` (X-Role: staff) returns full distinct names; t2 isolated
   - Logs: `docs/handoff/artifacts/uat003-qa/logs/live-api-verify.txt`, `api-logs-default.json`, `api-logs-t2.json`
5. Node name-cell harness (mirrors inventory_board.js nameCell + CSS override check)
   - Log: `docs/handoff/artifacts/uat003-qa/logs/node-name-cell.txt`
   - Exit 0; `NODE_HARNESS_PASS=true`

## Criterion 1 evidence — Rice / Oil distinguishable

Self-checked citations (exact line text):

- `demo/static/js/inventory_board.js:691` — `                const rawName = (r.name == null) ? '' : String(r.name);`
- `demo/static/js/inventory_board.js:693` — `                const nameCell = `<td class="inv-ledger-name-cell" title="${nameEsc}" data-full-name="${nameEsc}"><span class="inv-ledger-name-text">${nameEsc || '-'}</span></td>`;`
- `demo/static/css/style.css:2908` — baseline `#tab-inventory table.data-table td:nth-child(3)` still has `white-space: nowrap;`
- `demo/static/css/style.css:2934` — `#tab-inventory #inventoryLedgerTable td.inv-ledger-name-cell {`
- `demo/static/css/style.css:2938` — `    white-space: normal;`
- `demo/app/api_inventory.py:799` — `"id": r.id, "sku_id": r.sku_id, "name": r.name,`

Runtime:

- Pytest UAT-003 API case PASSED: names set contains both `Synthetic_Rice` and `Synthetic_Oil`, strings unequal, suffixes Rice/Oil present.
- Live :15170 default logs: NAMES include both full strings; RICE qty=1 Vendor_A; OIL qty=2 Vendor_A.
- Node harness visible text: `Synthetic_Rice` vs `Synthetic_Oil` (`VISIBLE_DISTINCT true`).
- Served JS on :15170 line 693 identical nameCell expression with full `nameEsc` in visible span (not truncated in markup).

## Criterion 2 evidence — full original accessible

- nameCell stamps `title` and `data-full-name` with escaped original (`inventory_board.js:693`).
- Long ZH fixture `特级有机黑糯米_` + 20x `测` present in live API body and in Node `data-full-name` / visible span; underscore retained (`LONG_ZH_LEN 28`, `ZH_UNDERSCORE true`).
- CSS dual-ID override uses `white-space: normal`, `overflow: visible`, `word-break: break-word` so wrap wins over nth-child nowrap for `#inventoryLedgerTable` only.
- Pytest `test_uat003_ui_ledger_name_cell_exposes_full_original` PASSED (static JS/CSS contract).

## Criterion 3 evidence — business name primary; cross-tenant

- Primary cell text binds `r.name` via `rawName` / `nameEsc`; `SKU_ID_AS_PRIMARY False` on nameCell template; Node `NO_SKU_ID_AS_PRIMARY true`.
- Live API: `str(sku_id) != name` for Rice row.
- Cross-tenant: default response has 3 rows (Rice/Oil/long ZH); no qty=9 Vendor_B Rice leak. t2 response count=1 name=Synthetic_Oil absent (`CROSS_TENANT_T2_OK True`). Pytest `test_tenant_isolation` PASSED. Unauthenticated `GET /api/inventory/logs` returned 401.

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | 15170 (fresh; not 15150/15160) |
| DB | `docs/handoff/artifacts/uat003-qa/environment/qa_isolated_smoke.db` (+ separate `qa_pytest.db`) |
| Fixture | Synthetic_Rice / Synthetic_Oil / long ZH; Vendor_A / Vendor_B only |
| Stop | `STOPPED_OWNED_TEST_PROCESS`, pid 111488 dead |
| Port free | true (`ss` LISTEN empty; SO_REUSEADDR bind ok) |
| Browser tabs | 0 opened; `browser_tab_closed=true` |
| 15010 | not present on this host; untouched |
| cleanup.json | `docs/handoff/artifacts/uat003-qa/environment/cleanup.json` |

No orphan uvicorn left. No git commit / push.

## Residual risks (honest)

1. **No real browser / Orca painted-pixel proof**: distinguishability verified via API full names + DOM-markup harness + CSS override source. Independent MANUAL_UI must still open `#inventoryLedgerTable` on a new 151xx port and confirm Rice/Oil are visually distinct in the painted cell (not only in `data-full-name`), plus hover title for long Chinese.
2. **max-width: 16rem**: wrap keeps suffixes in markup; extremely long names still rely on title/data-full-name. UAT may judge 16rem on 390px.
3. **Sibling SKU table** (`.inv-name-cell`) out of scope; UAT-024 depends on UAT-003 and was not exercised.
4. Checklist card in `01-acceptance-checklist.md` still `[TODO] NOT_RUN`; scheduler should update only after independent UAT, not from this QA alone.

## Scheduler signal

- **QA gate: PASS**
- Evidence path: `docs/handoff/artifacts/uat003-qa-report.md`
- Supporting logs/dir: `docs/handoff/artifacts/uat003-qa/`
- **UAT: not started**
- Next: independent UAT on a **new** port / DB / fixture (do not reuse 15170 env)

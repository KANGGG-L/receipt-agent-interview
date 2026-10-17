# UAT-003 Independent UAT Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation of inventory ledger name truncation fix
> Prior gates (not reused): `uat003-dev-report.md`, `uat003-qa-report.md` (QA port 15170)

## Overall gate (three acceptance behaviors)

| Behavior | Verdict |
|---|---|
| 1. Synthetic_Rice vs Synthetic_Oil distinguishable in inventory ledger | **PASS** |
| 2. Full names via title/data-full-name (underscore + long Chinese) | **PASS** |
| 3. Primary business name not replaced by ID | **PASS** |

**Three-behavior UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15180** (QA used 15170; UAT-002 used 15150/15160 — not reused) |
| Aux port | 15181 (static harness host for Chrome only) |
| DB | `docs/handoff/artifacts/uat003-uat/environment/uat_isolated.db` (fresh seeded) |
| Fixture | Synthetic_Rice / Synthetic_Oil / long ZH (`特级有机黑糯米_` + 20x `测`); Vendor_A / Vendor_B; t2 qty=9 boundary |
| Venv | `docs/handoff/artifacts/uat003-uat/environment/venv` (stage-local copy) |
| Server pid | 113704 (stopped at end) |
| HTTP pid | 114272 (stopped at end) |

## Commands actually run

1. Seed fresh DB via `app.db.create_sku` + `apply_stock_log` (kind=`in`) — `environment/seed.json`
2. Self-built uvicorn on 127.0.0.1:15180 with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=0` — `GET /` HTTP 200
3. Live API: `GET /api/inventory/logs` with `X-Role: staff` (default + `X-Tenant-Id: t2`); unauthenticated 401 — `logs/live-api-verify.txt`, `logs/api-logs-*.json`
4. Static citation print + served JS/CSS from :15180 — `logs/static-evidence.txt`
5. Chrome headless `--dump-dom` against harness HTML loading served `style.css` + `inventory_board.js` from :15180, calling real `loadInventoryLedger()` with live API payload — `TITLE_TAG UAT003_PASS`, `logs/chrome-result.json`
6. Node secondary nameCell/CSS harness on served assets — `logs/node-name-cell.txt` `NODE_HARNESS_PASS=true`
7. Full cleanup: stop pids, free ports, `environment/cleanup.json`

Did **not** run: interactive GUI hover on logged-in full app shell; UAT-024 mobile sibling table; checklist edit; git write.

## Criterion 1 — Rice / Oil distinguishable in ledger

Self-checked citations (exact line text):

- `demo/static/js/inventory_board.js:691` — `                const rawName = (r.name == null) ? '' : String(r.name);`
- `demo/static/js/inventory_board.js:693` — `                const nameCell = `<td class="inv-ledger-name-cell" title="${nameEsc}" data-full-name="${nameEsc}"><span class="inv-ledger-name-text">${nameEsc || '-'}</span></td>`;`
- `demo/static/css/style.css:2908` — baseline `#tab-inventory table.data-table td:nth-child(3)` still `white-space: nowrap;`
- `demo/static/css/style.css:2934` — `#tab-inventory #inventoryLedgerTable td.inv-ledger-name-cell {`
- `demo/static/css/style.css:2938` — `    white-space: normal;`
- `demo/app/api_inventory.py:799` — `"id": r.id, "sku_id": r.sku_id, "name": r.name,`

Runtime (Chrome + live API on :15180):

- Visible `.inv-ledger-name-text`: `Synthetic_Rice` vs `Synthetic_Oil` (`visibleDistinct: true`, `riceHasSuffixInDOM` / `oilHasSuffixInDOM` true)
- Computed style on name cell: `whiteSpace: normal`, `overflow: visible` (override beats nth-child nowrap)
- clientWidth == scrollWidth (142) for both Rice and Oil cells under 720px harness width — suffixes not horizontally clipped out of layout box
- Live API NAMES include both full strings; RICE qty=1 Vendor_A; OIL qty=2 Vendor_A

## Criterion 2 — full original via title / data-full-name

- Chrome: `title` and `data-full-name` equal full originals for Rice, Oil, and long ZH (`titleMatches` / `dataFullMatches` true)
- Long ZH length 28 with underscore retained (`zhFull` / `zhTitle` match fixture)
- Node harness: `FULL_VIA_DATA true`, `ZH_UNDERSCORE true`, `LONG_ZH_LEN 28`
- Served CSS override uses `white-space: normal` / `overflow: visible` / `word-break: break-word`

## Criterion 3 — business name primary; not replaced by ID

- Visible text binds `r.name` (not `sku_id`); Chrome `notReplacedById: true`; Node `NO_SKU_ID_AS_PRIMARY true`
- Live API: `str(sku_id) != name` for Rice row
- Cross-tenant (supporting negative): default 3 rows, no qty=9 leak; t2 count=1 Synthetic_Rice qty=9; unauthenticated logs GET → 401

## Residual risks (honest)

1. Harness used minimal `#tab-inventory #inventoryLedgerTable` DOM + served production CSS/JS + embedded live API payload (same-origin CORS bypass). Not a full logged-in multi-pane inventory session with real `apiFetch` cookie auth.
2. `max-width: 16rem` still wraps extremely long names across lines; full value remains in title/data-full-name. No 390px mobile reflow pass (UAT-024).
3. Sibling inventory SKU `.inv-name-cell` table out of scope.
4. Checklist card in `01-acceptance-checklist.md` not updated by this UAT (scheduler ownership).

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 113704, http.server 114272) |
| Port 15180 free | true |
| Port 15181 free | true |
| pid alive after stop | false |
| Orphans | none |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat003-uat/environment/cleanup.json` |

No git commit / push. No checklist edit. No secrets copied into working tree beyond stage-local env.

## Scheduler signal

- **UAT three-behavior gate: PASS**
- Evidence: `docs/handoff/artifacts/uat003-uat-report.md`
- Supporting dir: `docs/handoff/artifacts/uat003-uat/`
- Next: scheduler may mark UAT-003 card closed and proceed W3 remaining items (UAT-024 depends on UAT-003).

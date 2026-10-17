# UAT-008 Development Report

> Generated: 2026-10-17 21:05 UTC+8 (Asia/Singapore; same offset as the CST used in earlier reports)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [UAT-008] 实收0的保存与入账算术校验不一致 — P1 PRODUCT_FAULT / MANUAL_UI
> Base: `main` @ `b0e6b1e` (clean tree at start). No git commit / push in this stage.
> Context: ledger records a 2026-10-16 three-stage PASS (fix in `inventory.py` + `review_core.js`, test `test_uat_008_actual_qty_zero_arithmetic.py`), but there was no local packet and the 2026-10-17 DEV run was interrupted. This pass re-verified HEAD, found two **residual** save-vs-approve inconsistencies, fixed them, and produced a full local DEV packet.

## Overall

| Item | Verdict |
|---|---|
| HEAD `b0e6b1e` re-verify (original UAT-008 suite) | 7/7 pass — numeric `0` / `null` paths were already correct |
| Residual defect R1 (backend: blank / invalid `actual_qty`) | **FOUND and FIXED** — save 200 with no warning, approve 422 on the same receipt |
| Residual defect R2 (frontend: total-override hint ignores actual_qty) | **FOUND and FIXED** — hint said "no difference" while issue bar / save / approve said "相差 10" |
| All positive ACs + negative boundaries | PASS (pytest + live API + real browser, isolated port 15180) |
| Regression (S1-P0-01, S1-P2-10, S2-01, S2-02, S2-14, S2-15) + broad non-browser suites | PASS (demo 2160 passed / 18 skipped; root 1248 passed) |

Checklist set to `[TODO] DEV_DELIVERED (2026-10-17 DEV done, awaiting independent QA)`.

## Root cause

`actual_qty` was normalized differently at each of the four places that read it:

| Place | HEAD behaviour for `""` / `"  "` / `"abc"` / `"nan"` |
|---|---|
| save gate `_math_gate` (`api_receipts.py`) | `""` -> None (counts face amount 10); `"  "`/`"abc"` -> `float()` raises -> `_recompute_math_warnings` swallows -> `[]` (no warning) |
| storage `db.set_receipt_items` (`db.py`) | `_money.qty(v)` -> `_to_decimal` maps `""`/invalid to **0** -> stored `0.0` (a fake "effective zero") |
| approve gate (`_math_gate` over DB items) | sees `0.0` -> line effective 0 -> TOTAL_MISMATCH -> 422 APPROVAL_BLOCKED |
| inventory (`inventory.py`) | would stock 0 instead of quantity |

So one receipt (qty 1, price 10, amount 10, six fees net +12.5, total 22.5, `actual_qty=""`) was **saved with `confirm_mismatch=False` and no warning, then blocked at approve** — exactly the card's "保存与入账算术校验不一致". Reproduction against HEAD (`/tmp` isolated DB, TestClient):

```
''    save 200 code=None  stored=0.0  approve=422 APPROVAL_BLOCKED
'  '  save 200 code=None  stored=0.0  approve=422 APPROVAL_BLOCKED
'abc' save 200 code=None  stored=0.0  approve=422 APPROVAL_BLOCKED
'nan' save 200 code=None  stored=0.0  approve=422 APPROVAL_BLOCKED
'0'   save 422 MATH_MISMATCH_CONFIRM   (correct)
```

R2 (UI): `review_ux.js` `itemsSumNow()` (S3-19 total-override hint) summed only `.inp-amount`, ignoring 实收. With 实收 = 0 and the clerk typing the printed total 22.5, the hint computed "明细 22.50, 相差 0", hid itself and **cleared the override flag**, while the issue bar showed the 10.00 blocker; typing the correct 12.5 produced a false "相差 10.00" hint. Real-browser red evidence: `uat008-dev/logs/probe-ui-red-review_ux-HEAD.txt` (12/14, A2 and A4 FAIL).

## Fix (files changed, line refs verified on the working tree)

| File | Lines | Change |
|---|---|---|
| `demo/app/money.py` | 36-66 | New `optional_qty(value, strict=True)`: the single 实收 normalizer. None / `""` / whitespace -> None; finite numbers incl. `0`, `0.0`, `"0"`, `" 0 "` -> float rounded to 4 dp (same as stored qty); `"abc"`, NaN, Inf, bool, list -> ValueError (strict) or None (lax). |
| `demo/app/api_receipts.py` | 1880-1893 | `save_edited`: normalize every item's `actual_qty` once **before** the gate recompute and the DB write; invalid -> `422 INVALID_ACTUAL_QTY` ("第 N 行的「实收」不是有效数字。没收到货请填 0，按票面数量收货请留空。"), nothing written. |
| `demo/app/api_receipts.py` | 1367, 1378-1380, 1388 | `_math_gate` (shared by save and approve) uses `optional_qty(strict=False)`. |
| `demo/app/api_receipts.py` | 2421, 2424, 2429 | `acknowledge_warning` recompute uses the same normalizer. |
| `demo/app/db.py` | 3014-3016 | `set_receipt_items`: `optional_qty(strict=False)` instead of `_money.qty` (no more `"" -> 0.0`). |
| `demo/app/services/inventory.py` | 166-170 | `apply_receipt_to_inventory`: same normalizer (null -> quantity, 0 -> 0). |
| `demo/app/services/receipt_utils.py` | 1091, 1094-1095, 1103 | `compute_gate_issues` (detail `gate_issues`) uses the same normalizer. |
| `demo/static/js/review_core.js` | 2074-2114, 2116-2117 | New `computeItemsAutoTotal()` (actual-aware, fee-aware, credit-note aware) extracted from `recalcTotalSum`; exported on `window`; `recalcTotalSum` calls it. |
| `demo/static/js/review_core.js` | 2968-2973 | `calculateReceiptIssues`: trim-aware test so `"  "` = unfilled (JS `Number("  ")` is 0), `" 0 "` = 0, booleans ignored. |
| `demo/static/js/review_ux.js` | 257-260 | `itemsSumNow()` delegates to `root.computeItemsAutoTotal()` (old body kept only as fallback). |
| `demo/tests/test_uat_008_actual_qty_normalization.py` | new, 310 lines | 46 test cases (see below). |

Not changed: `math_engine` v2_1_0 (already correct), fee sign contract (`+delivery +deposit +service +tax -discount -rounding`, identical in backend `v2_1_0.py:143`, `recalcTotalSum`/`computeItemsAutoTotal`, and `calculateReceiptIssues`), `api_receipt_lifecycle.py` (listed on the card but it holds no 实收 arithmetic). Scope note for QA: `money.py`, `db.py`, `api_receipts.py`, `receipt_utils.py`, `review_ux.js` are outside the card's listed files and were needed because the inconsistency was in them.

## AC -> evidence

| AC / boundary | Evidence |
|---|---|
| AC1 synthetic qty 1 / price 10 / amount 10 / actual 0, six fees each distinct (5 / 4 / 3 / 2 / 1 / 0.5) | All tests and probes use this fixture; UI save request carries the six fees verbatim (`evidence/actual-qty-ui-probe.json` step A5) |
| AC2 effective 0 -> difference visible, no false "no problem" | API: `MATH_MISMATCH_CONFIRM` "...应付 12.5 元...相差 10 元" for `0`, `0.0`, `"0"`, `" 0 "`; detail `gate_issues` has TOTAL_MISMATCH; approve 422 "相差 10 元". UI: issue bar blocker "相差 $10.00", override hint "相差 10.00 元", save blocked locally (0 requests), approve blocked locally (0 requests) — `ui/02_*.png`, `ui/03_*.png` |
| AC3 corrected total -> save and approve reach the same conclusion | Matrix test `test_save_gate_and_approve_gate_reach_same_conclusion` (9 combos); UI: total 12.5 -> save 200 (payload `actual_qty: 0`) -> confirm dialog -> approve 200, DB `approved` (`ui/04-06_*.png`) |
| AC4 null -> quantity, 0 -> 0 | DB read-back: null stored None, stock +1; `0`/`"0"`/`" 0 "`/`0.0` stored 0.0, stock delta 0; blank `""`/`"  "` now stored None and stocked 1 (UI scenario B, API probe) |
| No truthy fallback 0 -> 1 | Same as AC4; `optional_qty` unit tests |
| Fee sign contract unchanged | Unchanged code; S1-P0-01 / S2-02 regression pass |
| Unmatched not stocked | inventory_log and inventory_batches count 0 for every blocked receipt (API probes, pytest) |
| Cross-tenant / Tenant B baseline | B detail / save / approve 404; B receipt count unchanged (pytest + both API probes) |

## Tests run (all actually executed in this stage)

Interpreter: `/workspace/.venvs/receipt/bin/python` (Python 3.13.5; venv outside the repo). Logs under `docs/handoff/artifacts/uat008-dev/logs/`.

| Command | Result | Log |
|---|---|---|
| `pytest demo/tests/test_uat_008_actual_qty_normalization.py` with product files reverted to HEAD (`git stash` of demo/app, demo/static) | **31 failed / 15 passed** (red; 11 behavioural failures + 20 failures of the new `optional_qty` helper contract) | `tdd-red.log` |
| same, with fix | **46 passed** | `tdd-green.log` |
| `pytest demo/tests/test_uat_008_actual_qty_zero_arithmetic.py` (original suite) | 7 passed | `uat008-original.log` |
| S1-P0-01: `test_review_s1_p0_01.py` + `test_b1_receipt_state_machine.py` | 20 passed | `reg-S1-P0-01.log` |
| S1-P2-10: `test_b1_platform.py -k "money_helpers or db_layer_rounds"` | 2 passed | `reg-S1-P2-10.log` |
| S2-01: `test_review_s2_01.py` + `test_p_ai_pipeline_fixes.py::test_s2_01_multipack_qty_one_is_split_and_warned` | 10 passed | `reg-S2-01.log` |
| S2-02 / S2-14 / S2-15: `test_p_ai_pipeline_fixes.py -k s2_02 / s2_14 / s2_15` | 1 / 4 / 2 passed | `reg-S2-02.log`, `reg-S2-14.log`, `reg-S2-15.log` |
| Related: UAT-029, UAT-007, UAT-009, `test_b1_platform.py`, `test_p_ai_pipeline_fixes.py`, root gap6 / gap3 / fix_p0_3 | 161 passed (run before the review_ux.js change; covered again by the broad runs below) | `reg-related.log` |
| Broad `pytest demo/tests` excluding browser / 15010-bound files (`--ignore` list in log header) | **2160 passed, 18 skipped** | `broad-demo-nonbrowser.log` |
| Broad `pytest tests` (root), same exclusions | **1248 passed** | `broad-root-nonbrowser.log` |
| Live phase `uat008-shared/run_phase.sh uat008-dev 15180` | pytest 53 passed; legacy API probe 20/20; normalization API probe 22/22; UI probe 14/14 | `pytest-uat008.txt`, `probe_actual_qty_api.txt`, `probe_actual_qty_normalization_api.txt`, `probe_actual_qty_ui.txt` |
| Real-browser red: UI probe with `review_ux.js` reverted to HEAD | 12/14 (A2, A4 FAIL) | `probe-ui-red-review_ux-HEAD.txt` |

Regression ID binding (authoritative table rows, opened and checked): S1-P0-01 -> `docs/review-2026-10/S1-backend.md:58`; S1-P2-10 -> `S1-backend.md:90` (FIXED record `fix-B2.md:211`); S2-01 -> `S2-ai-pipeline.md:52`; S2-02 -> `S2-ai-pipeline.md:53`; S2-14 -> `S2-ai-pipeline.md:65`; S2-15 -> `S2-ai-pipeline.md:66`. Test files carry the matching section markers (`test_b1_receipt_state_machine.py:56`, `test_b1_platform.py:635`, `test_p_ai_pipeline_fixes.py:94/147/417`).

Not run: browser suites bound to the shared 15010 server (deliberately excluded); no real-model AC on this card, so no model call was made and the model config was not needed.

## Environment / isolation

- Isolated port **15180** (QA / UAT should use another 151xx port). Fresh DB `/workspace/.uat008-dev-env/uat008-dev.db`, plus uploads / rag / artifacts dirs there: outside the repo, not under `docs/handoff/artifacts`.
- `AUTH_ENABLED=0`; identity via `X-Role` / `X-Tenant-Id` headers; tenants `uat008n-a/b`, `uat008-a/b`, `uat008ui-a`; supplier `Vendor_A` (legacy probe uses `Vendor_UAT008_A`).
- 15010: not listening before or after (`uat008-dev/environment/pre-state.txt`, `cleanup.txt`); isolated server pid matched the listener and was stopped; 15180 released.
- Scratch files our runs created under gitignored paths (`demo/receipt_demo.db` empty schema with 0 receipts, `demo/backups/*.db`, `demo/uploads/*`, `demo/.rag_chroma`, root `artifacts/memory/`) were deleted. `demo/.env` (auto-generated `APP_SECRET_KEY`, gitignored) was left in place.
- No secrets in the repo; no venv, node_modules or *.db under artifacts.

## Evidence paths

- Report: `docs/handoff/artifacts/uat008-dev-report.md` (this file)
- `docs/handoff/artifacts/uat008-dev/`: `logs/*`, `evidence/{actual-qty-api-probe.json, actual-qty-normalization-api-probe.json, actual-qty-ui-probe.json}`, `ui/01-07_*.png` + `ui-hashes.txt` (7 distinct MD5), `environment/{pre-state.txt, port.txt, uvicorn.pid, cleanup.txt}`
- Shared scripts: `docs/handoff/artifacts/uat008-shared/{run_phase.sh, probe_actual_qty_api.py (legacy, from the interrupted run), probe_actual_qty_normalization_api.py, probe_actual_qty_ui.py}`

## For QA to check specifically

1. Blank vs zero vs invalid over the raw API (`""`, `"  "`, `"0"`, `" 0 "`, `0.0`, `"abc"`, `"nan"`, `true`): save gate, DB value, detail `gate_issues`, approve gate and inventory must agree on each one.
2. `INVALID_ACTUAL_QTY` is a new 422 code on `save_edited`; confirm nothing is written, and that the recognition "auto" sync-save path is not broken (front end always sends a number or null).
3. UI: 实收 0 + typed total 22.5 -> override hint, issue bar and save toast all say 10.00; "改回明细合计" -> 12.50; total 12.5 -> hint hidden, save and approve succeed.
4. `itemsSumNow` now returns a signed total for credit_note / correction_note (it used to clamp to >= 0, unlike `recalcTotalSum`). Re-check the UAT-029 credit-note UI path.
5. Matched approve with 实收 0 writes one `inventory_log` row with qty 0 (stock delta 0). Decide whether that zero row is acceptable. The success toast still says "库存已增加" in that case; that wording is pre-existing and was not changed.
6. Re-verify every cited line number yourself.

# UAT-008 Independent QA Report

> Generated: 2026-10-17 22:05 UTC+8 (Asia/Singapore)
> Role: Independent QA (did not do the development; DEV report not trusted, everything re-run)
> Card: [UAT-008] 实收0的保存与入账算术校验不一致 - P1 PRODUCT_FAULT / MANUAL_UI
> Base: `main` @ `b0e6b1e` + uncommitted DEV changes (see `uat008-dev-report.md`). No product code modified in QA. No commit / push.
> UAT: not started.

## Overall: QA PASS (awaiting independent UAT)

| AC / boundary | Verdict | Evidence |
|---|---|---|
| AC1 synthetic qty 1 / price 10 / amount 10 / actual 0, six fees distinct (5/4/3/2/1/0.5) | PASS | `uat008-qa/qa_api_probe.py`, `qa_ui_probe.py` (UI save payload carries six fees verbatim) |
| AC2 effective 0 -> difference visible, no false "no problem" | PASS | API: 0 / 0.0 / "0" / " 0 " with total 22.5 -> 422 MATH_MISMATCH_CONFIRM "相差 10 元"; detail TOTAL_MISMATCH; approve 422. UI: hint, issue bar and save toast all show 10.00 (`ui/01`, `ui/02`) |
| AC3 corrected total -> save and approve same conclusion | PASS | API: total 12.5 -> save 200, detail clean, approve 200 for every zero variant; UI: 改回明细合计 -> 12.50, total 12.5 -> save 200 -> approve 200 (`ui/03`, `ui/04`) |
| AC4 actual_qty null -> quantity, 0 -> 0 | PASS | DB read-back: null/""/"   " stored NULL, inventory_log qty 1; 0/0.0/"0"/" 0 " stored 0.0, inventory_log qty 0 |
| No truthy fallback 0 -> 1 | PASS | same as AC4 |
| Fee sign contract unchanged | PASS | `ai_registry/tools/math_engine/v2_1_0.py:143` unchanged; API messages show -折扣 -抹零 +押金 +运费 +服务费 +税; S1-P0-01 / S2-02 regressions pass |
| Unmatched not stocked | PASS | every confirmed-mismatch receipt: approve 422, inventory_log 0 rows |
| Cross-tenant / Tenant B baseline | PASS | B detail / approve / save-over 404; B receipt count unchanged |
| New 422 INVALID_ACTUAL_QTY writes nothing; auto-save path intact | PASS | see section 2 |
| Credit / correction note hint signed like recalcTotalSum | PASS | see section 4 |
| Matched approve with 实收 0 (zero inventory row, toast wording) | PASS (acceptable, with non-blocking observation) | see section 5 |
| DEV report line numbers | PASS (all match) | see section 6 |

Defects found: none blocking. One non-blocking observation (toast wording) and one environment note (root test needs `demo/.rag_chroma`).

## Environment

- Isolated port 15280 (not 15010, not 15180); fresh DB `/workspace/.uat008-qa-env/qa.db` with uploads / rag / artifacts dirs there (outside repo). `AUTH_ENABLED=0`, `RAG_EMBEDDING_PROVIDER=hash`, identity via X-Role / X-Tenant-Id; tenants `uat008qa-a/b`, `uat008qaui-a`; supplier `Vendor_A`.
- Interpreter `/workspace/.venvs/receipt/bin/python`; headless Chromium via playwright.
- 15010 not listening before or after (`uat008-qa/environment/pre-state.txt`, `cleanup.txt`); QA uvicorn stopped, 15280 released.
- Gitignored scratch created by the test runs (`demo/receipt_demo.db*`, `demo/backups/`, `demo/uploads/`) removed. No .db / venv / node_modules under artifacts.

## 1. Raw API matrix (live, `qa_api_probe.py` -> `evidence/qa-api-probe.json`, 36/36 PASS)

For each input: save with wrong total (no confirm) / save confirmed-mismatch then detail + approve + inventory / save correct total then detail + approve + inventory.

| actual_qty | effective | wrong-total save | stored | detail gate | approve (mismatch) | correct total | approve (matched) | inventory_log qty |
|---|---|---|---|---|---|---|---|---|
| `""` | unfilled | 422 (12.5, 相差 10) | NULL | TOTAL_MISMATCH | 422, 0 rows | 22.5 -> 200 | 200 | 1 |
| `"   "` | unfilled | 422 | NULL | TOTAL_MISMATCH | 422, 0 rows | 22.5 -> 200 | 200 | 1 |
| `null` | unfilled | 422 | NULL | TOTAL_MISMATCH | 422, 0 rows | 22.5 -> 200 | 200 | 1 |
| `"0"` | 0 | 422 (22.5, 相差 10) | 0.0 | TOTAL_MISMATCH | 422, 0 rows | 12.5 -> 200 | 200 | 0 |
| `" 0 "` | 0 | 422 | 0.0 | TOTAL_MISMATCH | 422, 0 rows | 12.5 -> 200 | 200 | 0 |
| `0.0` | 0 | 422 | 0.0 | TOTAL_MISMATCH | 422, 0 rows | 12.5 -> 200 | 200 | 0 |
| `"abc"`, `"nan"`, `true`, `"inf"`, `[1]` | invalid | 422 INVALID_ACTUAL_QTY (both confirm=false and confirm=true) | nothing written | - | - | - | - | - |

Save gate, stored value, detail gate_issues, approve gate and inventory agree on every input.

## 2. INVALID_ACTUAL_QTY and auto-save

- New receipt: receipts / receipt_items / inventory_log counts identical before and after each invalid request.
- Update of an existing receipt with `actual_qty:"abc"` plus changed name / total: 422, existing items and total/status unchanged.
- Auto sync-save (`source:"auto"`, receipt in `parsed`, created via `db.create_receipt`): actual_qty 0 / null / "" -> 200, status stays `parsed`, stored 0.0 / NULL / NULL. The front end (`review_core.js` save payload builder ~line 2601) only ever sends a finite number or null, so the new 422 cannot be hit from the UI auto-save path.

## 3. UI (real browser, `qa_ui_probe.py` -> `evidence/qa-ui-probe.json`, 14/14 PASS)

- Type 实收 0 -> total auto 12.50.
- Type total 22.5 -> override hint "和明细加起来（12.50 元）相差 10.00 元"; issue bar blocker "相差 $10.00"; click 保存 -> no network request, toast "还有 1 处必须先改的地方 ... 相差 $10.00" (`ui/01`, `ui/02`).
- Click 改回明细合计 -> total 12.50, hint hidden, no total blocker (`ui/03`).
- Type 12.5 -> hint hidden; save -> 200, payload `actual_qty: 0`, six fees verbatim; approve confirm dialog -> 200, DB `approved` (`ui/04`).
- Screenshots: 6 distinct MD5 (`ui/ui-hashes.txt`).

## 4. Credit-note hint (signed)

Seeded credit_note (amount -10, fees as above, total 2.5). In browser: `computeItemsAutoTotal()` = 2.50; total typed -5 -> hint "（2.50 元）相差 7.50"; amount set to -20 -> auto -7.50, hint "（-7.50 元）相差 2.50" (negative shown, no clamp to 0); 改回明细合计 -> total -7.50, identical to recalcTotalSum (`ui/05`, `ui/06`). Credit-note related suites (UAT-029 / 007 / 009 test files) 16 passed. Full UAT-029 UI save path is out of scope (card still NOT_RUN).

## 5. Matched approve with 实收 0

Observed (UI receipt #20): one inventory_log row `qty 0.0, amount 0.0, type in`; toast "单据 #20 已审核入账，库存已增加".
Verdict against the card: acceptable. ACs require "0按0" and "未匹配不入库存"; a matched receipt with 实收 0 stocks 0, stock delta 0, consistent with save and approve. The zero-qty audit row does not change stock. The toast wording is pre-existing (`review_modal.js:288`), not covered by any AC; recorded as non-blocking observation O1 (suggest a later wording card such as "已入账，本单实收为 0，库存未变化").

## 6. DEV report line-number check (re-opened every reference on the working tree)

All match: `money.py:36-66` (def at 36, return at 66); `api_receipts.py:1880-1893` (normalize + INVALID_ACTUAL_QTY), `1367/1378-1380/1388` (`_math_gate`), `2421/2424/2429` (acknowledge_warning); `db.py:3014-3016`; `inventory.py:166-170`; `receipt_utils.py:1091/1094-1095/1103`; `review_core.js:2074-2114` (computeItemsAutoTotal, window export at 2114), `2116-2117` (recalcTotalSum), `2968-2973` (trim-aware calculateReceiptIssues); `review_ux.js:257-260`; new test file 310 lines, 10 test functions, 46 collected cases. `v2_1_0.py:143` exists at `ai_registry/tools/math_engine/v2_1_0.py:143` (report omits the directory). Regression bindings `S1-backend.md:58/90`, `S2-ai-pipeline.md:52/53/65/66`, `fix-B2.md:211`, and test markers `test_b1_receipt_state_machine.py:56`, `test_b1_platform.py:635`, `test_p_ai_pipeline_fixes.py:94/147/417` all point at the claimed rows.

## 7. Tests actually run in QA (logs in `uat008-qa/logs/`)

| Command | Result | Log |
|---|---|---|
| `pytest demo/tests/test_uat_008_actual_qty_normalization.py` | 46 passed | `new-test.log` |
| `pytest demo/tests/test_uat_008_actual_qty_zero_arithmetic.py` | 7 passed | `orig-test.log` |
| S1-P0-01: `test_review_s1_p0_01.py` + `test_b1_receipt_state_machine.py` | 20 passed | `reg-S1-P0-01.log` |
| S1-P2-10: `test_b1_platform.py -k "money_helpers or db_layer_rounds"` | 2 passed | `reg-S1-P2-10.log` |
| S2-01: `test_review_s2_01.py` + `test_p_ai_pipeline_fixes.py::test_s2_01_multipack_qty_one_is_split_and_warned` | 10 passed | `reg-S2-01.log` |
| S2-02 / S2-14 / S2-15: `test_p_ai_pipeline_fixes.py -k "s2_02 or s2_14 or s2_15"` | 7 passed | `reg-S2-02-14-15.log` |
| UAT-007 / 009 / 029 test files | 16 passed | `rel-uat029-007-009.log` |
| `pytest demo/tests` non-browser (same ignore list as DEV, `ignore-list.txt`) | 2160 passed, 18 skipped | `broad-demo.log` |
| `pytest tests` (root) non-browser | 1247 passed, 1 failed | `broad-root.log` |
| re-run `tests/test_rag_embedding_wiring.py` | 14 passed, 1 failed (same) | `rerun-rag-wiring.log` |
| same, with an empty gitignored `demo/.rag_chroma` dir created then removed | 15 passed | `rerun-rag-wiring-with-store-dir.log` |

Root failure `test_real_store_creates_identity_suffixed_collections` is environmental: it asserts the live demo vector store `demo/.rag_chroma` already exists, and the DEV stage cleanup deleted that directory. Unrelated to UAT-008 (RAG wiring, no actual_qty code). Note E1 for the coordinator: do not delete `demo/.rag_chroma` in cleanup, or recreate it before running root tests.

Not run: browser suites bound to the shared 15010 server (excluded deliberately); DEV's own probes (QA wrote independent probes instead).

## Next

UAT-008 independent UAT (fresh 151xx port, not 15010 / 15180 / 15280).

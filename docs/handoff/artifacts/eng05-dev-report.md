# ENG-05 Development / Gate Report

> Generated: 2026-10-17 07:56 Asia/Shanghai (CST)
> Role: Development / first gate runner only (independent QA / UAT NOT run in this stage)
> Card: [ENG-05] 完整根与Demo工程门禁重跑 — P1 ENGINEERING_GATE / G1
> Constraints: no git push; keep port 15010 reserved; do not delete baselines; do not mask reds with skips
> Pattern: match ENG-04 three-phase → stop at DEV handoff (here: FAIL), leave independent QA re-run

## Overall (DEV gate self-check)

| Criterion | Verdict |
|---|---|
| 1. Root `tests/` full suite exit 0 and failures 0 | **FAIL** — exit 1; **3 failed**, 1288 passed, 1 skipped (469.18s) |
| 2. Demo full suite exit 0 failures 0 (not a 418-subset) | **FAIL** — exit 1; **35 failed**, 2139 passed, 20 skipped (1024.17s); collected **2193** items (+1 collection skip) |
| 3. Any skip explained item-by-item with what was NOT verified | **PASS** (documented below; skips not counted as PASS) |
| 4. Background cleanup complete AND 15010 still present/reserved | **PASS** — 15010 was FREE before/after (no baseline listener); temp 15275/15285 stopped; `environment/cleanup.json` |
| Negative: do not delete baselines / skip to mask red / claim supportive as model E2E | **PASS** — failures left red; no product code patched to greening; segno install only |

**ENG-05 DEV gate: FAIL.** Do not flip DONE. Independent QA / UAT not started.

## Environment

- Python 3.13.5 / pytest 9.1.1
- Venv: `docs/handoff/artifacts/eng05-dev/environment/venv` (cloned from known-good uat025-qa venv; `segno` + `beautifulsoup4` installed — first root run had 8 ModuleNotFoundError: segno; fixed env then re-ran)
- `AUTH_ENABLED=0`, `RAG_EMBEDDING_PROVIDER=hash`
- Isolated health target for ENG-01 probe: `http://127.0.0.1:15275` with seeded dummy engine config so `/api/health` returned `{"status":"ok"}` (not 15010)
- Demo Playwright / PW env: `PW_TEST_PORT=15285`, `PW_TEST_BASE_URL=http://127.0.0.1:15285`
- 15010: never listening during this run; left FREE_RESERVED

## Commands (authoritative)

```text
# Root r2 (authoritative after segno + isolated health URL)
PW_TEST_BASE_URL=http://127.0.0.1:15275 PW_TEST_PORT=15275 \
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash \
pytest tests/ -v --tb=line -rs --junitxml=.../root-junit-r2.xml
# → 3 failed, 1288 passed, 1 skipped in 469.18s; exit 1

# Demo full suite (NOT a subset)
PW_TEST_PORT=15285 PW_TEST_BASE_URL=http://127.0.0.1:15285 \
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash \
pytest demo/tests/ -v --tb=line -rs --junitxml=.../demo-junit.xml
# collect-only: 2193 tests collected
# → 35 failed, 2139 passed, 20 skipped in 1024.17s; exit 1
```

Logs:
- `docs/handoff/artifacts/eng05-dev/logs/root-pytest-r2.txt`, `root-junit-r2.xml`, `root-failures-r2.txt`, `root-skips-r2.txt`
- `docs/handoff/artifacts/eng05-dev/logs/demo-pytest.txt`, `demo-junit.xml`, `demo-failures.txt`, `demo-skips.txt`
- First root attempt (pre-segno / no health URL): `root-pytest.txt` — 12 failed (historical; superseded by r2)

## Root failures (3) — concrete

1. `tests.test_migration_live_copy::test_live_copy_migration`
   - `AssertionError: 副本应包含单据数据` (live `demo/receipt_demo.db` has **0** receipts)
   - NOT verified: live-copy migration invariants on a non-empty live baseline
2. `tests.test_qa_obs_center_consistency_audit::test_ac_3_1_zero_em_dash_in_obs_center`
   - 16 render sites of fullwidth em dash `—` remain in observation-center JS (dish cost / price placeholders)
   - NOT verified: AC 3.1 zero-em-dash contract for obs center
3. `tests.test_registry_ssot::test_ac13_math_consumer_assertions_untouched`
   - `tests/test_u5_real_handwritten_long_receipts.py` assert #11 rewritten vs golden baseline (AC-13)
   - NOT verified: math_engine consumer assert freeze AC-13 for that file

## Root skip (1) — explained

1. `tests.test_qa_eval_center_gate_verification::test_qa_live_service_port_15010_e2e`
   - Reason: `Port 15010 service is not running or not reachable`
   - NOT verified: live eval-center E2E against a real 15010 baseline (by design this run kept 15010 free/reserved; this is skip ≠ PASS)

Note: with isolated `PW_TEST_BASE_URL=15275`, `test_qa_live_service_port_15010_healthy`, `test_playwright_e2e`, and `test_workbench_playwright_e2e_audit` **PASSED** (not claimed as model E2E).

## Demo failures (35) — concrete list

Full messages: `logs/demo-failures.txt`.

Dish / cost / consumption (theory cost None / 409):
1. `demo.tests.test_api_dishes::test_dish_crud_and_bom_calculation` — `assert None == 16.2`
2. `demo.tests.test_api_dishes::test_daily_consumption_batch_and_fifo_deduction` — `assert None == 650.0`
3. `demo.tests.test_api_dishes::test_void_daily_consumption_and_rollback` — `assert None == 0.0`
4. `demo.tests.test_api_dishes::test_zero_cost_and_estimated_batches_transparency` — `assert 409 == 200`
5. `demo.tests.test_daily_consumption_idempotent_merge::test_daily_consumption_second_submit_merges` — `assert None == 70.0`
6. `demo.tests.test_dish_bom_versioning::test_bom_versioning_snapshot_lifecycle` — `assert None == 30.0`
7. `demo.tests.test_recipe_cost_caliber::test_theory_cost_caliber_consistent_across_detail_and_exports` — `assert None == 13.6` (ledger prior: DEF-RECIPE-CAL)

Permissions / canary / GT / UI:
8. `demo.tests.test_b2_permission_matrix::test_matrix_file_matches_code` — matrix md out of sync (`permission_scan.py --write`)
9. `demo.tests.test_canary_table_truncation::test_canary_table_no_truncation_and_no_duplicate_requests` — Playwright Timeout 45s
10. `demo.tests.test_evalset_gt_linkage::test_confirm_writes_shared_corpus_on_default_db` — missing `source_receipt_id` writeback
11. `demo.tests.test_f1b_ia_admin::test_admin_pages_no_horizontal_overflow_on_mobile` — `btnSubViewTelemetry`
12. `demo.tests.test_golden_board_gt_filter::test_confirm_sample_promotes_source_receipt_to_golden` — `assert 0 == 1`

Upload / image / param / analytics Playwright:
13. `demo.tests.test_p0_rotation_redesign::test_ac_be_04_upload_endpoint_preserves_raw_image` — `error` vs `queued`
14. `demo.tests.test_param_wiring_upload_and_recon::test_upload_single_and_batch_share_same_grp_resolution` — list length mismatch
15. `demo.tests.test_playwright_analytics_center_full_audit::test_analytics_center_every_component_audit` — telemetry table empty
16. `demo.tests.test_playwright_historical_canary_and_ab::test_playwright_historical_canary_and_ab_observatory` — Timeout 8s
17. `demo.tests.test_playwright_pvalue_select_switch::test_pvalue_experiment_select_switch` — Timeout 15s
18. `demo.tests.test_price_threshold_owner_api::test_threshold_change_moves_anomaly_and_summary_in_lockstep` — summary/inv mismatch

Receipt detail image URL blanking (5):
19–23. `demo.tests.test_receipt_detail_image_url::*` (5 tests) — expected blank URL still served `/api/receipts/...`

Recovery / i18n / UAT-005 fixtures / upload security:
24. `demo.tests.test_recovery_actions::test_replace_image_invalid_content_400` — `INVALID_IMAGE` vs `UNSUPPORTED_IMAGE_FORMAT`
25. `demo.tests.test_review_i18n_08::test_ac6_migration_is_idempotent_and_keeps_assigned_rows` — `assert 6 == 5`
26–32. `demo.tests.test_uat_005_upload_error_classification::*` (7) — `FileNotFoundError` absolute host path `/Users/ethan/harness-tmp/.../21-uat005-dev/fixtures/...` (fixtures not on this box)
33–35. `demo.tests.test_upload_content_security::*` (3) — `INVALID_IMAGE` vs `UNSUPPORTED_IMAGE_FORMAT`

Note: `test_b1_platform::test_requirements_declare_every_direct_import_with_upper_bounds` (ledger DEF-B1-STD / Python 3.9 `sys.stdlib_module_names`) **did not fail** on Python 3.13.5 in this run.

## Demo skips (20) — item-by-item what was NOT verified

1. `demo.tests.test_i18n_zh_hant` (collection) — no `opencc` module → Hant conversion suite NOT run
2. `test_admin_tabs_consolidation_e2e` — still targets 15010; no service + no `E2E_ALLOW_SPAWN_UVICORN` → live admin tabs consolidation E2E NOT run (skip ≠ PASS; ENG-04 path)
3–11. eval baseline denominators/freeze (9) — `demo/evalsets/manifest.csv` missing (business corpus not in repo) → real baseline freeze/denominator checks NOT run
12–13. `test_eval_harness` real corpus (2) — need `--run-eval` + evalsets → real corpus split/scale NOT run
14–15. `test_evalset_gt_linkage` real evalset (2) — evalsets missing → golden-board real corpus linkage NOT run
16–17. `test_gt_consistency_validator` real corpus (2) — need `--run-eval` → real GT consistency NOT run
18. `test_image_injection_guard::test_real_receipts_zero_false_positive` — <30 real uploads → zero-FP regression NOT run
19. `test_residue_rules::test_real_corpus_rule_row_counts` — need `--run-eval` → real residue counts NOT run
20. `test_sku_alias_candidates::test_real_corpus_coverage_and_high_confidence` — need `--run-eval` → real alias coverage NOT run

## Cleanup / 15010

- `docs/handoff/artifacts/eng05-dev/environment/cleanup.json`
- 15010: FREE_RESERVED before and after (no baseline process existed; not killed)
- Temporary listeners 15275 (health) and 15285 (demo PW) stopped; ports free
- Side note: health uvicorn briefly wrote `demo/backups/receipt_20261017_0728*.db` via app backup scheduler while probing; live `receipt_demo.db` not intentionally mutated for greening (still 0 receipts). Baselines not deleted.

## Not done (out of DEV scope for this FAIL)

- Product fixes for the 3 root + 35 demo reds (ENG-05 card `source_files: []` — gate re-run only)
- Independent QA second isolated re-run
- Independent UAT
- Git commit / push
- Checklist DONE

## Checklist / ledger action

- Set `[ENG-05]` → `[TODO] FAIL` with date and this report path (not DONE)
- Ledger: ENG-05 DEV gate FAIL; next remains ENG-05 until green (or fix cards spun from listed reds); after ENG-05 PASS → DEP-MODEL or DEP-HIDDEN per ledger order; **UAT-032 still blocked on UAT-031**

## Next gate

Independent QA should re-run **full** `tests/` and **full** `demo/tests/` on an isolated env (not 418-subset), treat skips as non-PASS with printed reasons, keep 15010 reserved, and only flip DONE after a green independent re-run + UAT if the three-phase pattern is followed. Prefer fixing the concrete reds above before expecting PASS.

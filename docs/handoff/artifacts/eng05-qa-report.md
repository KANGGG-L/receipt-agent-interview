# ENG-05 Independent QA Report (full re-run)

> Generated: 2026-10-17 08:43 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development / fix pass)
> Card: [ENG-05] 完整根与Demo工程门禁重跑 — P1 ENGINEERING_GATE / G1
> Prior: `eng05-dev-report.md` (DEV gate FAIL: root 3 + demo 35), `eng05-fix-dev-report.md` (DEV_DELIVERED; 38/38 local recheck)
> Constraints: no git push; keep port 15010 reserved; do not kill 15010; full suites (not 418-subset); skip != PASS
> Pattern: ENG-04 three-phase — this is QA second isolated full re-run; UAT not started

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Root `tests/` full suite exit 0 and failures 0 | **PASS** — exit 0; **0 failed**, 1291 passed, 1 skipped (444.89s / junit 444.821s) |
| 2. Demo full suite exit 0 failures 0 (not a 418-subset) | **FAIL** — exit 1; **1 failed**, 2173 passed, 20 skipped (1122.70s); collected **2193** items (+1 collection skip) |
| 3. Any skip explained item-by-item with what was NOT verified | **PASS** (documented below; skips not counted as PASS) |
| 4. Background cleanup complete AND 15010 still free/reserved | **PASS** — 15010 FREE_RESERVED before/after; temp 15315/15325 stopped; `environment/cleanup.json` |
| Negative: do not delete baselines / skip to mask red / claim supportive as model E2E | **PASS** — failure left red; no product patch during QA; no 15010 baseline claimed |

**ENG-05 QA gate: FAIL** (demo failures=1). Do not flip DONE. Independent UAT **not** next until a green full re-run.

## Environment

- Python 3.13.5 / pytest 9.1.1
- Venv: `docs/handoff/artifacts/eng05-qa/environment/venv` (fresh clone of eng05-dev venv for this QA isolation)
- `AUTH_ENABLED=0`, `RAG_EMBEDDING_PROVIDER=hash`
- Root health target: `http://127.0.0.1:15315` with seeded dummy engine config (`eng05-qa-health-seed`) so `/api/health` returned `{"status":"ok"}` (not 15010)
- Demo Playwright / PW env: `PW_TEST_PORT=15325`, `PW_TEST_BASE_URL=http://127.0.0.1:15325`
- 15010: never listening during this run; left FREE_RESERVED

## Commands (authoritative)

```text
# Root full suite
PW_TEST_BASE_URL=http://127.0.0.1:15315 PW_TEST_PORT=15315 \
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash \
pytest tests/ -v --tb=line -rs --junitxml=.../root-junit.xml
# → 1291 passed, 1 skipped in 444.89s; exit 0; junit tests=1292 failures=0 errors=0 skipped=1

# Demo full suite (NOT a subset)
PW_TEST_PORT=15325 PW_TEST_BASE_URL=http://127.0.0.1:15325 \
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash \
pytest demo/tests/ -v --tb=line -rs --junitxml=.../demo-junit.xml
# collect: 2193 tests collected / 1 skipped at collection
# → 1 failed, 2173 passed, 20 skipped in 1122.70s; exit 1
# junit: tests=2194 fail=1 err=0 skip=20 time=1122.613
```

Logs:
- `docs/handoff/artifacts/eng05-qa/logs/root-pytest.txt`, `root-junit.xml`, `root-skips.txt`, `root-timing.txt`
- `docs/handoff/artifacts/eng05-qa/logs/demo-pytest.txt`, `demo-junit.xml`, `demo-failures.txt`, `demo-skips.txt`, `demo-timing.txt`
- Health: `logs/health-uvicorn.log`

## Root results

| Metric | Value |
|---|---|
| Exit | 0 |
| Passed | 1291 |
| Failed | 0 |
| Skipped | 1 |
| Duration | 444.89s (0:07:24) |

Prior DEV root reds (3) did **not** reappear (migration live-copy, obs-center em-dash AC 3.1, registry AC-13 math_consumer).

### Root skip (1) — explained

1. `tests.test_qa_eval_center_gate_verification::test_qa_live_service_port_15010_e2e`
   - Reason: `Port 15010 service is not running or not reachable`
   - NOT verified: live eval-center E2E against a real 15010 baseline (by design this run kept 15010 free/reserved; skip ≠ PASS)

Note: with isolated `PW_TEST_BASE_URL=15315`, `test_qa_live_service_port_15010_healthy`, `test_playwright_e2e`, and `test_workbench_playwright_e2e_audit` **PASSED** (not claimed as model E2E).

## Demo results

| Metric | Value |
|---|---|
| Exit | 1 |
| Collected | 2193 (+1 collection skip) |
| Passed | 2173 |
| Failed | 1 |
| Skipped | 20 |
| Duration | 1122.70s (0:18:42) |

Prior DEV demo reds (35) did **not** reappear in this full re-run. The single new red is unrelated to that list.

### Demo failure (1) — concrete

1. `demo.tests.test_f1a_flows::test_s3_07_staff_discard_does_not_promise_undo`
   - `playwright._impl._errors.TimeoutError: Page.goto: Timeout 30000ms exceeded`
   - Call log: navigating to `http://127.0.0.1:54307/`, waiting until `load`
   - Duration ~135.9s (junit)
   - Context: F1a suite uses `_f1a_support.site` / `make_page` with an ephemeral live Chromium target; failure occurred at `pg.open()` **before** discard-dialog assertions (`"没法自己找回"` / no undo toast)
   - Neighbors in same file: `test_s3_07_flag_uses_in_app_dialog_with_reason` and `test_s3_07_discard_uses_in_app_dialog_and_owner_gets_undo` **PASSED**; subsequent `test_s1_p1_11_owner_sees_reverse_button_on_approved_and_posts_payload` **PASSED**
   - NOT verified: staff discard dialog copy (no self-recovery / no undo promise) and absence of undo toast after confirm

Full excerpt: `logs/demo-failures.txt`.

## Demo skips (20) — item-by-item what was NOT verified

1. `demo.tests.test_i18n_zh_hant` (collection) — no `opencc` module → Hant conversion suite NOT run
2. `test_admin_tabs_consolidation_e2e` — still targets 15010; no service + no `E2E_ALLOW_SPAWN_UVICORN` → live admin tabs consolidation E2E NOT run (skip ≠ PASS; ENG-04 path)
3–5. `test_eval_baseline_denominators` (3: d1/d2/d3) — `demo/evalsets/manifest.csv` missing (business corpus not in repo) → real baseline denominator checks NOT run
6–11. `test_eval_baseline_freeze` (6) — same missing `manifest.csv` → real baseline freeze / tamper / sample-set drift / strict exit NOT run
12–13. `test_eval_harness` real corpus (2) — need `--run-eval` + evalsets → real corpus split/scale NOT run
14–15. `test_evalset_gt_linkage` real evalset (2) — evalsets missing → golden-board real corpus linkage NOT run
16–17. `test_gt_consistency_validator` real corpus (2) — need `--run-eval` → real GT consistency NOT run
18. `test_image_injection_guard::test_real_receipts_zero_false_positive` — <30 real uploads → zero-FP regression NOT run
19. `test_residue_rules::test_real_corpus_rule_row_counts` — need `--run-eval` → real residue counts NOT run
20. `test_sku_alias_candidates::test_real_corpus_coverage_and_high_confidence` — need `--run-eval` → real alias coverage NOT run

## Cleanup / 15010

- `docs/handoff/artifacts/eng05-qa/environment/cleanup.json`
- 15010: FREE_RESERVED before and after (no baseline process existed; not killed)
- Temporary listeners 15315 (root health) and 15325 (demo PW session server) stopped; ports free
- Baselines not deleted

## Checklist / ledger action

- Set `[ENG-05]` → `[TODO] FAIL` with date and this report path (not DONE; not QA_PASS)
- Ledger: ENG-05 independent QA FAIL; next remains ENG-05 (fix the listed red or re-run until failures 0); after green QA → independent UAT third full re-run per ENG-04 three-phase; then DEP-MODEL / DEP-HIDDEN; **UAT-032 still blocked on UAT-031**

## Next gate

Do **not** dispatch independent UAT yet. Prefer investigating / fixing `test_s3_07_staff_discard_does_not_promise_undo` (ephemeral F1a `Page.goto` timeout to `127.0.0.1:54307`) or a clean full re-run after env/load mitigation; then re-dispatch independent QA until both suites exit 0 with failures 0. Only then: independent UAT third full re-run (still no DONE until UAT PASS if three-phase is followed).

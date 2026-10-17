# ENG-05 Independent QA Report #2 (full re-run after fix2)

> Generated: 2026-10-17 09:18 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development / fix2 pass)
> Card: [ENG-05] 完整根与Demo工程门禁重跑 — P1 ENGINEERING_GATE / G1
> Prior: `eng05-qa-report.md` (QA#1 FAIL: demo 1 fail), `eng05-fix2-dev-report.md` (DEV_DELIVERED; F1a Page.open harden)
> Constraints: no git push; keep port 15010 reserved; do not kill 15010; full suites (not 418-subset); skip != PASS
> Pattern: ENG-04 three-phase — this is QA second isolated full re-run after fix2; UAT not started in this pass

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Root `tests/` full suite exit 0 and failures 0 | **PASS** — exit 0; **0 failed**, 1291 passed, 1 skipped (449.44s / junit 449.374s) |
| 2. Demo full suite exit 0 failures 0 (not a 418-subset) | **PASS** — exit 0; **0 failed**, 2175 passed, 19 skipped (952.41s); collected **2193** items (+1 collection skip) |
| 3. Any skip explained item-by-item with what was NOT verified | **PASS** (documented below; skips not counted as PASS) |
| 4. Background cleanup complete AND 15010 still free/reserved | **PASS** — 15010 FREE_RESERVED before/after; temp 15415/15425 stopped; `environment/cleanup.json` |
| Negative: do not delete baselines / skip to mask red / claim supportive as model E2E | **PASS** — no product patch during QA; prior red left to harness fix2 only; no 15010 baseline claimed |

**ENG-05 QA#2 gate: PASS** (root failures=0, demo failures=0). Checklist stays `[TODO] DEV_DELIVERED` with **QA_PASS** note — **not DONE**. Next: independent UAT third full re-run (ENG-04 three-phase).

## Environment

- Python 3.13.5 / pytest 9.1.1
- Venv: `docs/handoff/artifacts/eng05-qa2/environment/venv` (symlink to eng05-qa venv; same deps; isolated ports/DB)
- `AUTH_ENABLED=0`, `RAG_EMBEDDING_PROVIDER=hash`
- Root health target: `http://127.0.0.1:15415` with seeded dummy engine config (`eng05-qa2-health-seed`) so `/api/health` returned `{"status":"ok"}` (not 15010)
- Demo Playwright / PW env: `PW_TEST_PORT=15425`, `PW_TEST_BASE_URL=http://127.0.0.1:15425`
- 15010: never listening during this run; left FREE_RESERVED

## Commands (authoritative)

```text
# Root full suite
PW_TEST_BASE_URL=http://127.0.0.1:15415 PW_TEST_PORT=15415 \
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash \
pytest tests/ -v --tb=line -rs --junitxml=.../root-junit.xml
# → 1291 passed, 1 skipped in 449.44s; exit 0; junit tests=1292 failures=0 errors=0 skipped=1

# Demo full suite (NOT a subset)
PW_TEST_PORT=15425 PW_TEST_BASE_URL=http://127.0.0.1:15425 \
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash \
pytest demo/tests/ -v --tb=line -rs --junitxml=.../demo-junit.xml
# collect: 2193 tests collected / 1 skipped at collection
# → 2175 passed, 19 skipped in 952.41s; exit 0
# junit: tests=2194 fail=0 err=0 skip=19 time=952.312
```

Logs:
- `docs/handoff/artifacts/eng05-qa2/logs/root-pytest.txt`, `root-junit.xml`, `root-skips.txt`, `root-timing.txt`
- `docs/handoff/artifacts/eng05-qa2/logs/demo-pytest.txt`, `demo-junit.xml`, `demo-failures.txt`, `demo-skips.txt`, `demo-timing.txt`
- Health: `logs/health-uvicorn.log`

## Root results

| Metric | Value |
|---|---|
| Exit | 0 |
| Passed | 1291 |
| Failed | 0 |
| Skipped | 1 |
| Duration | 449.44s (0:07:29) |

Prior DEV root reds (3) and QA#1 green root did **not** regress.

### Root skip (1) — explained

1. `tests.test_qa_eval_center_gate_verification::test_qa_live_service_port_15010_e2e`
   - Reason: `Port 15010 service is not running or not reachable`
   - NOT verified: live eval-center E2E against a real 15010 baseline (by design this run kept 15010 free/reserved; skip ≠ PASS)

Note: with isolated `PW_TEST_BASE_URL=15415`, `test_qa_live_service_port_15010_healthy`, `test_playwright_e2e`, and `test_workbench_playwright_e2e_audit` **PASSED** (not claimed as model E2E).

## Demo results

| Metric | Value |
|---|---|
| Exit | 0 |
| Collected | 2193 (+1 collection skip) |
| Passed | 2175 |
| Failed | 0 |
| Skipped | 19 |
| Duration | 952.41s (0:15:52) |

### Fix2 target (prior QA#1 red) — verified green

- `demo.tests.test_f1a_flows::test_s3_07_staff_discard_does_not_promise_undo` → **PASSED** (was TimeoutError `Page.goto` 30s / wait_until=load in QA#1)

Prior DEV demo reds (35) and the QA#1 single F1a timeout did **not** reappear.

Delta vs QA#1: passed 2173→2175 (+2), skipped 20→19 (−1), failed 1→0. Explained by (a) staff_discard now PASS, (b) `test_image_injection_guard::test_real_receipts_zero_false_positive` now PASS (was skipped in QA#1 for <30 real uploads; this run had enough fixtures).

### Demo skips (19) — item-by-item what was NOT verified

1. `demo.tests.test_i18n_zh_hant` (collection) — no `opencc` module → Hant conversion suite NOT run
2. `test_admin_tabs_consolidation_e2e` — still targets 15010; no service + no `E2E_ALLOW_SPAWN_UVICORN` → live admin tabs consolidation E2E NOT run (skip ≠ PASS; ENG-04 path)
3–5. `test_eval_baseline_denominators` (3: d1/d2/d3) — `demo/evalsets/manifest.csv` missing (business corpus not in repo) → real baseline denominator checks NOT run
6–11. `test_eval_baseline_freeze` (6) — same missing `manifest.csv` → real baseline freeze / tamper / sample-set drift / strict exit NOT run
12–13. `test_eval_harness` real corpus (2) — need `--run-eval` + evalsets → real corpus split/scale NOT run
14–15. `test_evalset_gt_linkage` real evalset (2) — evalsets missing → golden-board real corpus linkage NOT run
16–17. `test_gt_consistency_validator` real corpus (2) — need `--run-eval` → real GT consistency NOT run
18. `test_residue_rules::test_real_corpus_rule_row_counts` — need `--run-eval` → real residue counts NOT run
19. `test_sku_alias_candidates::test_real_corpus_coverage_and_high_confidence` — need `--run-eval` → real alias coverage NOT run

## Cleanup / 15010

- `docs/handoff/artifacts/eng05-qa2/environment/cleanup.json`
- 15010: FREE_RESERVED before and after (no baseline process existed; not killed)
- Temporary listeners 15415 (root health) and 15425 (demo PW session server) stopped; ports free
- Baselines not deleted
- No git push

## Checklist / ledger action

- Keep `[ENG-05]` → `[TODO] DEV_DELIVERED` with **QA_PASS** note and this report path (**not DONE**)
- Ledger: ENG-05 independent QA#2 PASS; next = independent UAT third full re-run (ENG-04 three-phase); then DEP-MODEL / DEP-HIDDEN; **UAT-032 still blocked on UAT-031**
- Do **not** flip DONE until UAT PASS if three-phase is followed

## Next gate

Dispatch **independent UAT** third full re-run (ENG-04 three-phase pattern: root + demo full suites again under UAT role). Do **not** mark ENG-05 DONE in this QA pass.

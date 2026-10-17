# ENG-05 Fix2 Development Report

> Generated: 2026-10-17 08:52:50 CST (Asia/Shanghai / CST)
> Role: Development only (fix remaining ENG-05 QA red)
> Card: [ENG-05] 完整根与Demo工程门禁重跑 — P1 ENGINEERING_GATE / G1
> Prior: eng05-qa-report.md (root green; demo 1 fail); eng05-fix-dev-report.md (38/38 prior reds)
> Constraints: no git push; keep port 15010 reserved; do not claim ENG-05 DONE

## Overall (this DEV fix2 pass)

| Metric | Value |
|---|---|
| Target red | 1 (`test_s3_07_staff_discard_does_not_promise_undo`) |
| Product code changed | No |
| Harness changed | Yes — `demo/tests/_f1a_support.py` |
| Local recheck | staff_discard 3× PASS; `test_f1a_flows.py` 32/32 PASS; `test_f1a_mobile.py` 16/16 PASS |
| Full ENG-05 suite re-run | NOT done (out of scope; leave to independent QA) |
| ENG-05 card status | `[TODO] DEV_DELIVERED` (ready for independent full re-run; **not DONE**) |

**Ready for independent QA full re-run: YES**

## Diagnosis

Failure from eng05-qa-report:

- `demo.tests.test_f1a_flows::test_s3_07_staff_discard_does_not_promise_undo`
- `playwright._impl._errors.TimeoutError: Page.goto: Timeout 30000ms exceeded`
- navigating to ephemeral `http://127.0.0.1:54307/`, waiting until `load`
- Occurred at `pg.open()` **before** discard-dialog asserts
- Neighbors in same module (flag / owner-discard / reverse) PASSED; subsequent tests on same module-scoped server PASSED
- Root suite already green (1291/0/1); prior 38 reds did not recur
- JUnit time for the red was ~135.9s (anomalous vs neighbors ~2–3s), consistent with navigation stall under full-suite contention rather than a product hang

Cause classification:

| Hypothesis | Verdict |
|---|---|
| Server never started / wrong port | Rejected — module `site` fixture shared; neighbors used same URL successfully |
| Port race on bind | Rejected — `start_pinned_server` already waits on `/api/health`; failure was mid-module |
| Product hang on `/` | Rejected — subsequent navigations in same process passed |
| Too-short / too-strict `goto` under suite load | **Accepted** — `Page.open` used bare `goto` default `wait_until="load"` (30s). Index loads 30+ scripts; under full demo-suite CPU contention `load` can exceed 30s even while health is OK. No health re-probe and no retry before navigate. |

## Fix

File: `demo/tests/_f1a_support.py` (fixture harden only; test body unchanged; not deleted/skipped)

1. `Site.ensure_ready(timeout)` — poll `/api/health` via `is_healthy`; if child exited, raise with server log tail.
2. `Page.open` —
   - call `ensure_ready` before each navigate attempt
   - `goto(..., wait_until="domcontentloaded", timeout=60000)` (avoid waiting on every script for `load`)
   - up to 3 retries on Playwright Timeout/Error with short backoff
   - wait for `.sidebar-nav` with `state="attached"` (not visible — mobile CSS hides the sidebar)
   - then existing tab-click path

No product JS/API change. No test skip. No baseline delete.

## Local recheck

```text
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash
# staff_discard ×3
pytest demo/tests/test_f1a_flows.py::test_s3_07_staff_discard_does_not_promise_undo -v
# → RUN1/2/3 exit 0

# full f1a flows file
pytest demo/tests/test_f1a_flows.py -v
# → 32 passed in 61.37s

# shared-support mobile file (regression for attached vs visible)
pytest demo/tests/test_f1a_mobile.py -v
# → 16 passed in 32.75s

# neighbors
pytest ...::test_s3_04_busy_... ::test_s3_07_discard_... ::test_s3_07_flag_... -v
# → 3 passed
```

Logs: `docs/handoff/artifacts/eng05-fix2-dev/logs/`

## Not done / out of scope

- Full root `tests/` + full `demo/tests/` ENG-05 gate re-run (independent QA)
- Independent UAT
- Git commit / push
- Checklist DONE (must stay non-DONE until green independent full re-run)

## Cleanup / 15010

- Ephemeral `_playwright_serve.py` children stopped after local runs
- **15010: FREE_RESERVED** (never listened during this pass)

## Checklist / ledger action

- `01-acceptance-checklist.md` [ENG-05] → `[TODO] DEV_DELIVERED` (fix2; not DONE)
- Ledger note appended for coordinator

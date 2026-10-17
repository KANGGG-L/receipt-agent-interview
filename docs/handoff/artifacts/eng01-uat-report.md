# ENG-01 Independent UAT Report

> Generated: 2026-10-17 19:24 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform the development or QA)
> Card: [ENG-01] 根测试硬编码15010健康探测超时 — P1 ENGINEERING_GATE
> Prior: `eng01-dev-report.md`, `eng01-qa-report.md`
> Constraints: no git push; keep port 15010 FREE_RESERVED; never hardcode 15010 as probe target; fresh isolate port ≠15451 ≠15461; do not strip checklist DONE PASS

## Overall (UAT re-verify)

| Criterion | Verdict |
|---|---|
| 1. Reuse precise `tests.xml` failure case under isolate health 200 | **PASS** — node `test_qa_live_service_port_15010_healthy`; isolate `http://127.0.0.1:15471` health `{"status":"ok"}` → **1 passed** (exit 0) |
| 2. Non-200 / timeout still correctly fail (not skip) | **PASS** — four negative runs each exit 1 / 1 failed (no skip) |
| 3. No test hardcodes 15010 as probe target (ENG-01 source file) | **PASS** — literal audit of `tests/test_phase3_filter_adjustments_deep_qa.py`: 0 `host:15010` connect URLs; 15010 only as `_LIVE_PORT_FORBIDDEN`, reject messages, historical node name/docs |
| Negative: keep 15010 free; no baseline delete/skip-mask | **PASS** — 15010 FREE_RESERVED before/after; no product source edits; negatives left red |

**ENG-01 UAT re-verify: PASS.** Checklist remains `[DONE] PASS` (ledger); local DEV + QA + UAT packets now complete (three-phase local evidence).

## Environment

- Python 3.13.5 / pytest 9.1.1 (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15471** (≠ DEV 15451, ≠ QA 15461); fresh DB `eng01-uat/environment/health_isolated.db`
- Engine seed: dummy `openai_rec_*` via `set_engine_config(..., source="eng01-uat-health-seed")` so anonymous `/api/health` returns `{"status":"ok"}`
- Auth: `AUTH_ENABLED=0`; RAG/uploads/backups under `eng01-uat/environment/`
- 15010: never listening; left FREE_RESERVED; never used as positive probe target

## Precise case

Node: `tests/test_phase3_filter_adjustments_deep_qa.py::test_qa_live_service_port_15010_healthy`

```text
PW_TEST_BASE_URL=http://127.0.0.1:15471 PW_TEST_PORT=15471 PW_TEST_HEALTH_TIMEOUT=5 \
pytest tests/test_phase3_filter_adjustments_deep_qa.py::test_qa_live_service_port_15010_healthy -v
# → 1 passed (exit 0)
# [PROBE] GET http://127.0.0.1:15471/api/health timeout=5.0
# [PROBE] GET http://127.0.0.1:15471/api/analytics/recovery-summary?... timeout=5.0
```

`qa/logs/tests.xml` still missing on this machine; reuse of the named precise failure node matches card + DEV + QA + ENG-05 junit history.

## Negatives (must fail, not skip)

| Case | Env / stub | Exit | Asserted failure |
|---|---|---|---|
| No config | unset BASE_URL/PORT | 1 | 未配置…拒绝回落硬编码 15010（失败非 skip） |
| Forbid 15010 | `PW_TEST_PORT=15010` + base URL 15010 | 1 | 探测目标解析到 15010。拒绝连接，拒绝写入 |
| Non-200 | stub :15472 → HTTP 503 | 1 | 探测非 200 … HTTP 503 |
| Timeout | hang accept :15473, timeout=1s | 1 | 探测超时 …（失败非 skip） |

Logs: `eng01-uat/logs/pytest-neg-*.txt`

## Literal audit (affected file)

File: `tests/test_phase3_filter_adjustments_deep_qa.py` (ENG-01 `source_files`)

- 9 occurrences of token `15010`
- 0 hardcoded probe target URLs (`127.0.0.1:15010` / `localhost:15010`)
- Roles: `_LIVE_PORT_FORBIDDEN` reject constant; reject/doc comments; historical pytest node name; assert messages clarifying not a product-15010 judgment
- Evidence: `eng01-uat/evidence/literal-audit.json` verdict PASS

No product source edits in this UAT stage (re-verify only).

## Evidence paths

- Report: `docs/handoff/artifacts/eng01-uat-report.md` (this file)
- Folder: `docs/handoff/artifacts/eng01-uat/`
  - `logs/health.json`, `health-admin.json`, `health-uvicorn.log`
  - `logs/pytest-positive.txt` / `.xml`, `pytest-positive-probe.txt`
  - `logs/pytest-neg-no-config.txt`, `pytest-neg-forbid-15010.txt`, `pytest-neg-non200.txt`, `pytest-neg-timeout.txt`
  - `evidence/literal-audit.json`, `evidence/uat-summary.json`, `evidence/health-ok.json`
  - `environment/cleanup.json`, `pre-state.txt`, `health_isolated.db`, `port.txt`

## Checklist annotation

- Did **not** strip `[DONE] PASS`
- Updated ENG-01 status note: local DEV + QA + UAT packets complete (`eng01-{dev,qa,uat}-report.md`)

## Cleanup / 15010

- Isolate uvicorn 15471 stopped; stubs 15472/15473 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git push

## Next gate

Per `artifacts/partials-triage.md`: **UAT-004** (ledger-only, no local packet — re-verify + evidence).

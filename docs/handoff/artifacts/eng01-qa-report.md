# ENG-01 Independent QA Report

> Generated: 2026-10-17 19:22 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Card: [ENG-01] 根测试硬编码15010健康探测超时 — P1 ENGINEERING_GATE
> Prior: `eng01-dev-report.md`
> Constraints: no git; keep port 15010 FREE_RESERVED; never hardcode 15010 as probe target; fresh isolate port ≠15451; do not strip checklist DONE PASS
> UAT: NOT started (per dispatch: No UAT)

## Overall (QA re-verify)

| Criterion | Verdict |
|---|---|
| 1. Reuse precise `tests.xml` failure case under isolate health 200 | **PASS** — node `test_qa_live_service_port_15010_healthy`; isolate `http://127.0.0.1:15461` health `{"status":"ok"}` → **1 passed** (exit 0) |
| 2. Non-200 / timeout still correctly fail (not skip) | **PASS** — four negative runs each exit 1 / 1 failed (no skip) |
| 3. No test hardcodes 15010 as probe target (ENG-01 source file) | **PASS** — literal audit of `tests/test_phase3_filter_adjustments_deep_qa.py`: 0 `host:15010` connect URLs; 15010 only as `_LIVE_PORT_FORBIDDEN`, reject messages, historical node name/docs |
| Negative: keep 15010 free; no baseline delete/skip-mask | **PASS** — 15010 FREE_RESERVED before/after; no product source edits; negatives left red |

**ENG-01 QA re-verify: PASS.** Checklist remains `[DONE] PASS` (ledger); annotated local QA packet present. Independent UAT packet still pending.

## Environment

- Python 3.13.5 / pytest 9.1.1 (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15461** (≠ DEV 15451); fresh DB `eng01-qa/environment/health_isolated.db`
- Engine seed: dummy `openai_rec_*` via `set_engine_config(..., source="eng01-qa-health-seed")` so anonymous `/api/health` returns `{"status":"ok"}`
- Auth: `AUTH_ENABLED=0`; RAG/uploads/backups under `eng01-qa/environment/`
- 15010: never listening; left FREE_RESERVED; never used as positive probe target

## Precise case

Node: `tests/test_phase3_filter_adjustments_deep_qa.py::test_qa_live_service_port_15010_healthy`

```text
PW_TEST_BASE_URL=http://127.0.0.1:15461 PW_TEST_PORT=15461 PW_TEST_HEALTH_TIMEOUT=5 \
pytest tests/test_phase3_filter_adjustments_deep_qa.py::test_qa_live_service_port_15010_healthy -v
# → 1 passed (exit 0)
# [PROBE] GET http://127.0.0.1:15461/api/health timeout=5.0
# [PROBE] GET http://127.0.0.1:15461/api/analytics/recovery-summary?... timeout=5.0
```

`qa/logs/tests.xml` still missing on this machine; reuse of the named precise failure node matches card + DEV + ENG-05 junit history.

## Negatives (must fail, not skip)

| Case | Env / stub | Exit | Asserted failure |
|---|---|---|---|
| No config | unset BASE_URL/PORT | 1 | 未配置…拒绝回落硬编码 15010（失败非 skip） |
| Forbid 15010 | `PW_TEST_PORT=15010` + base URL 15010 | 1 | 探测目标解析到 15010。拒绝连接，拒绝写入 |
| Non-200 | stub :15462 → HTTP 503 | 1 | 探测非 200 … HTTP 503 |
| Timeout | hang accept :15463, timeout=1s | 1 | 探测超时 …（失败非 skip） |

Logs: `eng01-qa/logs/pytest-neg-*.txt`

## Literal audit (affected file)

File: `tests/test_phase3_filter_adjustments_deep_qa.py` (ENG-01 `source_files`)

- 9 occurrences of token `15010`
- 0 hardcoded probe target URLs (`127.0.0.1:15010` / `localhost:15010`)
- Roles: `_LIVE_PORT_FORBIDDEN` reject constant; reject/doc comments; historical pytest node name; assert messages clarifying not a product-15010 judgment
- Evidence: `eng01-qa/evidence/literal-audit.json` verdict PASS

### Broader observation (not counted against ENG-01 AC)

- `tests/test_openai_gateway_healthy_ssrf.py` / `tests/test_ssrf_and_credential_guard.py`: `localhost:15010` appears only as SSRF deny-list parametrize URLs (must never call out) — not live health probes.
- `tests/test_qa_eval_center_gate_verification.py::test_qa_live_service_port_15010_e2e` still hardcodes `http://127.0.0.1:15010/...` and skips if unreachable. Outside ENG-01 `source_files`; recorded for parent tracking only; does not flip this QA verdict.

No product source edits in this QA stage (re-verify only).

## Evidence paths

- Report: `docs/handoff/artifacts/eng01-qa-report.md` (this file)
- Folder: `docs/handoff/artifacts/eng01-qa/`
  - `logs/health.json`, `health-admin.json`, `health-uvicorn.log`
  - `logs/pytest-positive.txt` / `.xml`, `pytest-positive-probe.txt`
  - `logs/pytest-neg-no-config.txt`, `pytest-neg-forbid-15010.txt`, `pytest-neg-non200.txt`, `pytest-neg-timeout.txt`
  - `evidence/literal-audit.json`, `evidence/qa-summary.json`, `evidence/health-ok.json`
  - `environment/cleanup.json`, `pre-state.txt`, `health_isolated.db`, `port.txt`

## Checklist annotation

- Did **not** strip `[DONE] PASS`
- Updated ENG-01 status note: local QA packet `eng01-qa-report.md` present; UAT local packet still pending

## Cleanup / 15010

- Isolate uvicorn 15461 stopped; stubs 15462/15463 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git

## Not done (out of QA scope)

- Independent UAT packet (`eng01-uat-report.md`)
- Do not treat DEV+QA alone as three-phase local close beyond ledger DONE + packet annotations

## Next gate

Independent UAT: new isolate port/DB (≠15451, ≠15461); re-run the same positive node + the four negatives; confirm literal audit still PASS; then annotate UAT packet on checklist without stripping DONE PASS.

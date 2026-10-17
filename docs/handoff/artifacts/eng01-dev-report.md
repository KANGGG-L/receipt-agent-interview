# ENG-01 Development Re-verify Report

> Generated: 2026-10-17 19:19 Asia/Shanghai (CST)
> Role: Development re-verify only (independent QA / UAT NOT run in this stage)
> Card: [ENG-01] 根测试硬编码15010健康探测超时 — P1 ENGINEERING_GATE
> Constraints: no git push; keep port 15010 free/reserved; never use 15010 as probe target; do not flip checklist DONE away
> Note: Ledger already PASS; this packet supplies local DEV evidence only. QA/UAT packets still required for full three-phase local closure.

## Overall (DEV re-verify)

| Criterion | Verdict |
|---|---|
| 1. Reuse precise `tests.xml` failure case under isolate health 200 | **PASS** — `qa/logs/tests.xml` missing on this machine; reused named node `test_qa_live_service_port_15010_healthy` (card + ENG-05 junit history). Isolate `http://127.0.0.1:15451` health `{"status":"ok"}` → **1 passed** |
| 2. Non-200 / timeout still correctly fail (not skip) | **PASS** — four negative runs each exit 1 / 1 failed (no skip) |
| 3. No test hardcodes 15010 as probe target | **PASS** — literal audit: no `host:15010` connect URL; 15010 only as `_LIVE_PORT_FORBIDDEN`, reject messages, historical node name/docs |
| Negative: keep 15010 free; no baseline delete/skip-mask | **PASS** — 15010 FREE_RESERVED before/after; no product source edits; negatives left red |

**ENG-01 DEV re-verify: PASS.** Checklist remains `[DONE] PASS` (ledger). Local note: DEV packet present; QA/UAT local packets still pending.

## Environment

- Python 3.13.5 / pytest 9.1.1 (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15451** (fresh DB `eng01-dev/environment/health_isolated.db`)
- Engine seed: dummy `openai_rec_*` via `set_engine_config(..., source="eng01-dev-health-seed")` so anonymous `/api/health` returns `{"status":"ok"}`
- Auth: `AUTH_ENABLED=0`; RAG hash
- 15010: never listening; left FREE_RESERVED

## Precise case

Node: `tests/test_phase3_filter_adjustments_deep_qa.py::test_qa_live_service_port_15010_healthy`

```text
PW_TEST_BASE_URL=http://127.0.0.1:15451 PW_TEST_PORT=15451 PW_TEST_HEALTH_TIMEOUT=5 \
pytest tests/test_phase3_filter_adjustments_deep_qa.py::test_qa_live_service_port_15010_healthy -v
# → 1 passed (exit 0)
# [PROBE] GET http://127.0.0.1:15451/api/health timeout=5.0
# [PROBE] GET http://127.0.0.1:15451/api/analytics/recovery-summary?... timeout=5.0
```

`qa/logs/tests.xml` was not present under the repo; the card/checklist name the same precise failure node, and ENG-05 root junit previously recorded this node under isolated `PW_TEST_BASE_URL`.

## Negatives (must fail, not skip)

| Case | Env / stub | Exit | Asserted failure |
|---|---|---|---|
| No config | unset BASE_URL/PORT | 1 | 未配置…拒绝回落硬编码 15010（失败非 skip） |
| Forbid 15010 | `PW_TEST_PORT=15010` + base URL 15010 | 1 | 探测目标解析到 15010。拒绝连接，拒绝写入 |
| Non-200 | stub :15452 → HTTP 503 | 1 | 探测非 200 … HTTP 503 |
| Timeout | hang accept :15453, timeout=1s | 1 | 探测超时 …（失败非 skip） |

Logs: `eng01-dev/logs/pytest-neg-*.txt`

## Literal audit (affected file)

File: `tests/test_phase3_filter_adjustments_deep_qa.py`

- 9 occurrences of token `15010`
- 0 hardcoded probe target URLs (`127.0.0.1:15010` / `localhost:15010`)
- Roles: `_LIVE_PORT_FORBIDDEN` reject constant; reject/doc comments; historical pytest node name; assert messages clarifying not a product-15010 judgment
- Evidence: `eng01-dev/evidence/literal-audit.json` verdict PASS

No source edits in this DEV stage (HEAD already injects origin/timeout; re-verify only).

## Evidence paths

- Report: `docs/handoff/artifacts/eng01-dev-report.md` (this file)
- Folder: `docs/handoff/artifacts/eng01-dev/`
  - `logs/health.json`, `health-admin-after.json`, `health-uvicorn.log`
  - `logs/pytest-positive.txt` / `.xml`, `pytest-positive-probe.txt`
  - `logs/pytest-neg-no-config.txt`, `pytest-neg-forbid-15010.txt`, `pytest-neg-non200.txt`, `pytest-neg-timeout.txt`
  - `evidence/literal-audit.json`, `evidence/dev-summary.json`, `evidence/health-ok.json`
  - `environment/cleanup.json`, `pre-state.txt`, `health_isolated.db`

## Cleanup / 15010

- Isolate uvicorn 15451 stopped; stubs 15452/15453 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git push

## Not done (out of DEV scope)

- Independent QA packet (`eng01-qa-report.md`)
- Independent UAT packet (`eng01-uat-report.md`)
- Do not treat this DEV packet alone as three-phase local close beyond ledger DONE

## Next gate

Independent QA: new isolate port/DB; re-run the same positive node + the four negatives; confirm literal audit still PASS; leave DONE until UAT packet also exists (or follow scheduler for three-phase local annotation).

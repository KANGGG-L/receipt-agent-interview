# ENG-02 UAT Report

Generated: 2026-10-17 18:40:04 CST. Card: [ENG-02] 旧自动预分析勾选框断言 (P1 ENGINEERING_GATE). Phase: UAT.
Isolate: port **15441**, fresh DB `eng02-uat/environment/eng02.db`, AUTH_ENABLED=0, RAG hash. 15010 untouched (FREE_RESERVED). Creds sourced from openai_rec.env + model.env; key names only in `logs/env-keys-present.txt`, no values.

## Verdict: UAT PASS

| AC | Method | Result |
|---|---|---|
| 1 正常图片路径不找不存在 checkbox | live page, `#chkAutoAnalyze` count | PASS (0) |
| 2 图片 -> 启动识别真实请求与结果 | **real model, no route mocks**: real POST /api/upload (200, no force=true), job dcb99152d9c043228936ec03d4892576, real /api/job polls, prefill | PASS: 7-ELEVEN / 2016-10-01 / 13.00, 1 item row, 19.5s |
| 3 异常画质路径仍按原合同 | real server, blurred image | PASS: preConfirmCard + quality warn shown, 0 uploads before click; explicit start -> 400 IMAGE_QUALITY_ERROR without force; errorCard + btnForceRetry visible |
| Supporting regression | tests/test_playwright_e2e.py (route-mocked by design; NOT counted toward model AC) | 3 passed |
| Negative | 15010 free; no skip; no mock as model E2E | PASS |

## Evidence
- `eng02-uat/evidence/real-probe.json` (checks, verdict, full /api network log), `logs/real-probe.txt`
- `eng02-uat/screens/00-landing.png, 01-real-recognize-prefill.png, 02-blurry-preconfirm.png, 03-blurry-after-start.png`
- `logs/pytest.txt`, `logs/pytest.xml`, `logs/uvicorn.log`, `environment/cleanup.json`
- Scripts: `eng02-shared/run_phase.sh`, `eng02-shared/probe_real.py`

## Notes
- Phases use the same harness on separate isolates/DBs; distinct real job ids per phase. Screens 01-03 are MD5-identical across phases because the same input yields the same deterministic real-model render; QA/UAT independence is by fresh process/port/DB/job, not by different harness.
- Cleanup: uvicorn 15441 stopped; rag_chroma/uploads removed; 15010 FREE_RESERVED; no git push.

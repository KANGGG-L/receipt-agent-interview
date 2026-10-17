# UAT-014 QA Report

Generated: 2026-10-17 19:08 CST. Card: [UAT-014] 记忆恢复、一致性与安全事件没有可达UI (P2 SPEC_CONFLICT / SPEC_DECISION). Phase: QA (independent isolate; no product code changed).
Isolate: port **15449** (≠ DEV 15439), fresh DB `uat014-qa/environment/uat014.db` (+ backup `db/uat014.db`), AUTH_ENABLED=0, RAG hash. 15010 untouched (FREE_RESERVED). Creds sourced from openai_rec.env + model.env for env presence only (masking path did not need a real model call); key names only in `logs/env-keys-present.txt`, no values. No full HKID / phone / API key printed in evidence.

## SPEC_DECISION: DEFAULT_ORIGINAL (SPEC_KEEP) — reconfirmed

| Field | Value |
|---|---|
| Decision | **DEFAULT_ORIGINAL** |
| Card contract | `docs/handoff/01-acceptance-checklist.md` §[UAT-014] — D0 SPEC_KEEP; source_files=`[]`; "仅按原S2-19核验HKID日志脱敏，默认不添加记忆恢复/一致性/安全事件UI" |
| Original canonical | **S2-19** `docs/review-2026-10/S2-ai-pipeline.md:70` — `llm_logger` must call registry `PIIMaskerTool` so HKID / private phone are masked in `llm_call_log`. Related **S2-11** same file `:62` — API/behavior, not a new UI. |
| Derived Spec rejected | Card title (memory-recovery / consistency / security-event reachable UI) is **not** in original S2-19/S2-11 acceptance. Optional branch without approval must not claim those UIs done. |
| Owner | D0 SPEC_KEEP wave / handoff checklist owner (`docs/handoff/01-acceptance-checklist.md` §2.1 UAT-014). |
| Product change this phase | None. |

## Verdict: QA PASS

| AC | Method | Result |
|---|---|---|
| 1 SPEC_DECISION recorded | DEFAULT_ORIGINAL + citations above (matches DEV) | PASS |
| 2 Live S2-19 HKID log masking | Isolate 15449; production `record_llm_call` → `llm_call_log`; sqlite read-back | PASS: `plaintext_leak_count=0`; stored text has `[HKID_MASKED]`, `****67`, `110101********2345`, `sk-***` only |
| 3 No new memory-recovery / consistency / security-event UI | Scan templates + static/js for forbidden UI affordances | PASS: `forbidden_ui_hits=[]` |
| Tenant B unchanged | curl `X-Role: owner` + `X-Tenant-Id: uat014qab` GET `/api/dishes` + `/api/receipts`; DB counts for B | PASS: dishes HTTP 200 `data:[]`; receipts `[]`; `llm_call_log` for B = 0 (probe row only under `uat014-dev`) |
| Regression S2-19 x3 + S2-11 x2 | pytest on isolate DB | PASS: 5 passed |
| Negative | source_files empty honored; 15010 free; no git push; no plaintext secrets in evidence | PASS |

## Evidence
- `uat014-qa/evidence/hkid-live-probe.json` (verdict PASS; redacted samples only)
- `uat014-qa/evidence/db-llm-call-log.json` (on-disk `llm_call_log` row #1 masked)
- `uat014-qa/evidence/no-new-ui.json` (zero forbidden UI hits; pre-existing `loadVendorMemoryGovernance` noted)
- `uat014-qa/evidence/tenant-b.json` (Tenant B API + DB isolation)
- `uat014-qa/db/uat014.db` (sqlite backup of isolate DB)
- `logs/hkid-live-probe.txt`, `logs/pytest-s2.txt`, `logs/pytest-s2.xml`, `logs/no-new-ui.txt`, `logs/uvicorn.log`, `logs/uvicorn-tenantb-retry.log`, `logs/health.json`, `logs/env-keys-present.txt`, `logs/tenant_b_*.json`
- `environment/cleanup.json`
- Scripts: `uat014-shared/run_qa_phase.sh`, `probe_hkid_live.py`, `check_no_new_ui.py`

## Live masking (redacted samples only)
- Prompt stored: `UAT014_LIVE_PROBE customer [HKID_MASKED] phone ****67 alt [HKID_MASKED] id 110101********2345 key sk-***`
- Raw output stored: `UAT014_LIVE_PROBE vendor note [HKID_MASKED] contact ****67`
- Path: `record_llm_call` → `sanitize_text` / `sanitize_prompt_messages` → `PIIMaskerTool` → `db.insert_llm_call_log`
- Real model: **not required** (synthetic PII injected into the production logging path). Distinct isolate from DEV (port 15449, DB under `uat014-qa/`).

## No new UI claim
- Forbidden patterns (memory-recovery / 记忆恢复 / security-events panel / memory-consistency UI / revive frontend wiring / security-events loaders): **0 hits**.
- Pre-existing admin vendor-memory governance (`loadVendorMemoryGovernance`, `GET /api/admin/vendor-memory`) remains as prior work; **not** claimed as UAT-014 deliverable.
- Optional derived UIs were **not** added and are **not** marked done.
- Dirty UI files vs HEAD listed in `no-new-ui.json` belong to other in-progress cards; this card made **zero** product file edits.

## Tenant B / isolation
| Check | Result |
|---|---|
| Tenant B `uat014qab` GET `/api/dishes` | HTTP 200 `data: []` |
| Tenant B GET `/api/receipts?limit=5` | HTTP 200 empty |
| DB `llm_call_log` where tenant_id=B | **0** |
| Probe row tenant | `uat014-dev` only (1 row) |
| Note | First curl attempt without `X-Role` got 401 from EarlyAuthMiddleware (expected in AUTH_ENABLED=0); retry with `X-Role: owner` + `X-Tenant-Id` succeeded. |

## Notes
- Independent QA isolate; product code unchanged this phase.
- Cleanup: uvicorn 15449 stopped; rag_chroma/uploads removed; 15010 FREE_RESERVED; no git push.
- Checklist set to `[TODO] QA_PASSED` only (not DONE). Next gate: independent UAT.

# UAT-014 DEV Report

Generated: 2026-10-17 19:06 CST. Card: [UAT-014] 记忆恢复、一致性与安全事件没有可达UI (P2 SPEC_CONFLICT / SPEC_DECISION). Phase: DEV (SPEC_KEEP re-verify + live S2-19 evidence; no product code changed).
Isolate: port **15439**, fresh DB `uat014-dev/environment/uat014.db` (+ backup `db/uat014.db`), AUTH_ENABLED=0, RAG hash. 15010 untouched (FREE_RESERVED). Creds sourced from openai_rec.env + model.env for env presence only (masking path did not need a real model call); key names only in `logs/env-keys-present.txt`, no values.

## SPEC_DECISION: DEFAULT_ORIGINAL (SPEC_KEEP)

| Field | Value |
|---|---|
| Decision | **DEFAULT_ORIGINAL** |
| Card contract | `docs/handoff/01-acceptance-checklist.md` §[UAT-014] — D0 SPEC_KEEP; source_files=`[]`; "仅按原S2-19核验HKID日志脱敏，默认不添加记忆恢复/一致性/安全事件UI" |
| Original canonical | **S2-19** `docs/review-2026-10/S2-ai-pipeline.md:70` — `llm_logger` must call registry `PIIMaskerTool` so HKID / private phone are masked in `llm_call_log` (not a separate dual-implementation). Related regression **S2-11** same file `:62` (warnings do not write VendorMemory) — API/behavior, not a new UI. |
| Derived Spec rejected | Card title complaint that "记忆恢复 / 一致性 / 安全事件" lack reachable UI — **not** in original S2-19/S2-11 acceptance. Optional branch without approval must not claim those UIs done. |
| Owner | D0 SPEC_KEEP wave / handoff checklist owner (`docs/handoff/01-acceptance-checklist.md` §2.1 UAT-014). |
| Product change | None. Masking already live via `demo/app/services/llm_logger.py` → `ai_registry.tools.pii_masker`. |

## Verdict: DEV PASS

| AC | Method | Result |
|---|---|---|
| 1 SPEC_DECISION recorded | DEFAULT_ORIGINAL + citations above | PASS |
| 2 Live S2-19 HKID log masking | Isolate 15439; production `record_llm_call` wrote HKID/phone/mainland-id/key-bearing prompt+raw into `llm_call_log`; sqlite read-back | PASS: `plaintext_leak_count=0`; stored text has `[HKID_MASKED]`, `****67`, `110101********2345`, `sk-***` only |
| 3 No new memory-recovery / consistency / security-event UI | Scan templates + static/js for forbidden UI affordances; document pre-existing vendor-memory governance without claiming it | PASS: `forbidden_ui_hits=[]` |
| Regression S2-19 x3 + S2-11 x2 | pytest on isolate DB | PASS: 5 passed |
| Negative | source_files empty honored (no product edits); 15010 free; no git push; no plaintext secrets in evidence | PASS |

## Evidence
- `uat014-dev/evidence/hkid-live-probe.json` (verdict PASS; redacted samples only)
- `uat014-dev/evidence/db-llm-call-log.json` (on-disk `llm_call_log` row #1 masked)
- `uat014-dev/evidence/no-new-ui.json` (zero forbidden UI hits; pre-existing `loadVendorMemoryGovernance` noted)
- `uat014-dev/db/uat014.db` (sqlite backup of isolate DB)
- `logs/hkid-live-probe.txt`, `logs/pytest-s2.txt`, `logs/pytest-s2.xml`, `logs/no-new-ui.txt`, `logs/uvicorn.log`, `logs/health.json`, `logs/env-keys-present.txt`
- `environment/cleanup.json`
- Scripts: `uat014-shared/run_phase.sh`, `probe_hkid_live.py`, `check_no_new_ui.py`

## Live masking (redacted samples only)
- Prompt stored: `UAT014_LIVE_PROBE customer [HKID_MASKED] phone ****67 alt [HKID_MASKED] id 110101********2345 key sk-***`
- Raw output stored: `UAT014_LIVE_PROBE vendor note [HKID_MASKED] contact ****67`
- Path: `record_llm_call` → `sanitize_text` / `sanitize_prompt_messages` → `PIIMaskerTool` → `db.insert_llm_call_log`
- Real model: **not required** (synthetic PII injected into the production logging path). Keys were present but unused for this AC.

## No new UI claim
- Forbidden patterns (memory-recovery / 记忆恢复 / security-events panel / memory-consistency UI / revive frontend wiring / security-events loaders): **0 hits**.
- Pre-existing admin vendor-memory governance (`loadVendorMemoryGovernance`, `GET /api/admin/vendor-memory`) remains as prior work; **not** claimed as UAT-014 deliverable.
- Optional derived UIs (memory revive surface, consistency panel, security-events panel) were **not** added and are **not** marked done.

## Notes
- Residual PARTIAL was "D0 SPEC_KEEP 门禁已闭环，保留未运行标注" — live S2-19 masking was the missing runtime evidence.
- Dirty UI files vs HEAD listed in `no-new-ui.json` belong to other in-progress cards; this card made **zero** product file edits (`llm_logger.py` / `pii_masker` clean vs HEAD).
- Cleanup: uvicorn 15439 stopped; rag_chroma/uploads removed; 15010 FREE_RESERVED; no git push. DB evidence re-snapshotted via sqlite `backup()` after WAL settle.
- Checklist set to `[TODO] DEV_DELIVERED` only (not DONE). Next gate: independent QA.

# PARTIAL / Ledger-only Triage (post CLOSE-139)

Generated: 2026-10-17 CST. Sources: close139-report.md, close139-amendment-uat031-032.md, 01-acceptance-checklist.md, 02-coordinator-ledger.md.
Host facts: real model creds work (DEP-MODEL 9/9 real PASS); 15010 reserved; Playwright chromium available; no cloud packets in this clone.

## PARTIAL (real-model residual) - first

| Order | Card | Current status | Missing evidence | Closable on host | Note |
|---|---|---|---|---|---|
| 1 | ENG-02 | [DONE] PARTIAL (model path not run) | AC2 "image -> recognize real request and result" was route-mocked only | YES | P1; real model path now runnable |
| 2 | ENG-03 | [DONE] PASS (2026-10-17 Dev/QA/UAT real model) | closed | YES | closed this wave; see eng03-uat-report.md |
| 3 | UAT-014 | [DONE] PASS (2026-10-17 Dev/QA/UAT live S2-19) | closed | YES | closed this wave; see uat014-uat-report.md |
| 4 | UAT-016 | [DONE] PASS (2026-10-17 Dev/QA/UAT SPEC_KEEP live) | closed | YES | closed this wave; see uat016-uat-report.md |

## Ledger-only, no local packet (re-verify + evidence)

| Order | Card | Current status | Missing evidence | Closable on host |
|---|---|---|---|---|
| 5 | ENG-01 | [DONE] PASS (ledger) + local DEV/QA/UAT packets | closed this wave; see eng01-uat-report.md | YES |
| 6 | UAT-004 | [DONE] PASS (ledger) + local DEV/QA/UAT packets | closed this wave; see uat004-uat-report.md | YES |
| 7 | UAT-011 | [DONE] PASS (ledger) + local DEV/QA/UAT packets | closed this wave; see uat011-uat-report.md | YES |
| 8 | UAT-015 | [DONE] PASS (ledger) + local DEV/QA/UAT packets | closed this wave; see uat015-uat-report.md | YES |
| 9 | UAT-001 | [DONE] PASS (2026-10-17 Dev/QA/UAT local packets) | closed this wave; see uat001-uat-report.md | YES |
| 10 | UAT-005 | [DONE] PASS (2026-10-17 Dev/QA/UAT local packets) | closed this wave; see uat005-uat-report.md | YES |
| 11 | UAT-008 | [DONE] PASS (ledger) | no local packet | YES |
| 12 | UAT-009 | [DONE] PASS (ledger) | no local packet | YES |
| 13 | UAT-028 | [DONE] PASS (ledger) | no local packet | YES |
| 14 | UAT-029 | [DONE] PASS (ledger) | no local packet | YES |
| 15 | UAT-030 | [DONE] PASS (ledger; citation spec 04 kept) | no local packet | YES |
| 16 | UAT-034 | [DONE] PASS (ledger) | no local packet | YES |

Not in scope / still blocked: DEP-HIDDEN (BLOCKED_UNVERIFIED, Page Visibility cannot flip on this host).

Order rationale: PARTIALs whose residual is the real model path first (P1 ENG first), then SPEC PARTIALs, then W1 then W2 ledger-only cards in canonical order.

# UAT-009 QA environment & cleanup record

- Host time at probe end: 2026-10-18 07:0x (UTC+8) / 2026-10-17 23:0x UTC
- HEAD under test: 7271d40 (git status clean for product code; only handoff docs/venv untracked)
- Ports used: 15971 (AUTH_ENABLED=1, production-auth mode) and 15972 (AUTH_ENABLED=0, dev header mode for cross-tenant probe). QA 区段 1597x，未触 15010 / 15100-15200。
- Databases (all outside the repo, /tmp/uat009-qa/):
  - live.db       — AUTH=1 instance, logged-in UI walk + API probe
  - xtenant.db    — AUTH=0 instance, cross-tenant / Tenant B baseline probe
  - qa-green.db   — TDD GREEN rerun (HEAD code, 13 passed)
  - qa-red.db     — TDD RED rerun (upload.js + workbench_queue.js reverted to b0e6b1e, 5 failed / 8 passed)
  - qa-reg.db     — regression reruns (S2-01 + UAT-030, 14 passed)
- 15010 business DB untouched; 15010 not listening before or after.
- DEMO_TOKEN_SECRET: 96-char random hex (48 bytes), set via env only, never written to the repo.
- Cleanup: both QA uvicorn instances stopped after probes; ports 15971/15972 released; 15010 not listening (verified after kill).
- Playwright Chromium: system /usr/bin/google-chrome via explicit executable_path (bundled browser download unavailable).
- No product code modified by QA. Test scripts live in scripts/ of this directory; raw logs in logs/; JSON evidence in evidence/; screenshots in ui/.

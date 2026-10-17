# ENG-04 Development Report

> Generated: 2026-10-17 05:50 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [ENG-04] Demo导航旧标题/空实验fixture与中断 — P1 ENGINEERING_GATE
> Constraints: no git commit / push; keep port 15010; do not delete baselines

## Overall (DEV self-check)

| Criterion | Verdict |
|---|---|
| 1. 导航后业务观测链路真实执行（标题对齐现行「观测中心」后可进入 Phase 2；埋点区结束「加载中」） | **PASS** (code + unit guards; live E2E needs 15010 or `E2E_ALLOW_SPAWN_UVICORN=1`) |
| 2. 合成实验可读且详情结构实际验证 | **PASS** (`test_experiment_admin_detail_structure` + samples) |
| 3. skip 报告精确原因且不得计 PASS | **PASS** (pytest SKIPPED; `__main__` exit 5) |
| Negative: 保留 15010；不删基线；不无条件 spawn uvicorn | **PASS** (opt-in guards still green) |

**ENG-04 DEV gate: PASS.** Do not treat as card close; independent QA/UAT not started.

## Defects fixed

### 1. Demo 导航旧标题（`test_admin_tabs_consolidation_e2e.py`）

- **Before:** asserted sidebar `#analyticsBoardBtn` contains stale copy `AI 效果观测与评测中心`.
- **After:** asserts current product copy `观测中心` (matches `index.html` `data-i18n="nav.analytics"`); rejects stale title string.
- After nav click, explicitly asserts `#analyticsEventDistBody` / `#analyticsRecoveryBody` are past `加载中` (business observation path actually runs, not interrupted by title assert).
- `__main__` unavailable path: prints exact skip reason and **`sys.exit(5)`** (not 0), so CLI skip cannot be mistaken for PASS.

### 2. 空实验 fixture / 中断（`test_analytics_historical_canary_and_ab.py`）

- **Before:** `test_experiment_admin_detail_structure` did `pytest.skip("No experiments available")` on empty DB; samples test fell back to `id=1`.
- **After:** `_ensure_synthetic_experiment()` creates/reuses `ENG04_Synthetic_Experiment` via `db.create_experiment`; detail + samples tests always hit a real id.
- Detail assertions now check types (`experiment` dict id/name/status; `guardrail_events`/`snapshots` as lists; metrics/detail non-None) — structure actually verified, not key-presence-only.

## Files changed

- `demo/tests/test_admin_tabs_consolidation_e2e.py`
- `demo/tests/test_analytics_historical_canary_and_ab.py`
- `docs/handoff/01-acceptance-checklist.md` ([ENG-04] → `DEV_DELIVERED`; [UAT-027] → `DONE PASS` in Part A)
- `docs/handoff/02-coordinator-ledger.md` (W3 closeout + ENG-04 DEV)
- `docs/handoff/artifacts/w3-closeout-summary.md` (Part A)
- `docs/handoff/artifacts/eng04-dev-report.md` (this file)

No product/runtime source under `demo/app` / `demo/static` / `demo/templates` changed in ENG-04 DEV (test-only).

## Commands run (DEV)

```text
pytest demo/tests/test_analytics_historical_canary_and_ab.py \
  demo/tests/test_admin_tabs_consolidation_e2e.py::test_default_path_cannot_spawn_uvicorn \
  ::test_opt_in_spawn_guard_self_calibrates \
  ::test_unavailability_reason_is_readable_and_offers_opt_in \
  ::test_default_path_does_not_spawn_any_process \
  ::test_opt_in_spawn_is_reaped_on_failure -v
# → 10 passed

pytest demo/tests/test_admin_tabs_consolidation_e2e.py::test_admin_tabs_consolidation_e2e -v
# (no 15010, no E2E_ALLOW_SPAWN_UVICORN) → 1 skipped (exact reason; not PASS)

python demo/tests/test_admin_tabs_consolidation_e2e.py
# → [SKIP] <exact reason>; exit=5
```

Logs: `docs/handoff/artifacts/eng04-dev/logs/pytest-unit.txt`, `docs/handoff/artifacts/eng04-dev/logs/pytest-e2e-skip.txt`.

## Not done (out of DEV scope)

- Independent QA / Independent UAT
- Live Playwright E2E against a real 15010 (or opt-in spawn) — left for QA isolation
- Git commit / push

## Next gate

Independent QA should re-run both files on an isolated port/DB (or controlled 15010), confirm title + observation path + synthetic experiment structure, and treat any skip as non-PASS with the printed reason.

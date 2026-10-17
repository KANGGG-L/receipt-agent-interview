# ENG-04 Independent QA Report

> Generated: 2026-10-17 05:51 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify ENG-04 acceptance only (Demo nav title / observation path / synthetic experiment fixture / skip ≠ PASS)
> Card: [ENG-04] Demo导航旧标题/空实验fixture与中断 — P1 ENGINEERING_GATE
> Prior: `eng04-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 导航后业务观测链路真实执行（`#analyticsBoardBtn` → `#tab-analytics`；埋点区结束「加载中」） | **PASS** (live Playwright E2E on reserved 15010) |
| 2. 标题非陈旧（现行「观测中心」；拒绝「AI 效果观测与评测中心」） | **PASS** (live E2E + `index.html` `data-i18n="nav.analytics"`) |
| 3. 合成实验可读且详情结构实际验证 | **PASS** (`test_experiment_admin_detail_structure` + DB row `ENG04_Synthetic_Experiment`) |
| 4. skip 报告精确原因且不得计 PASS | **PASS** (pytest SKIPPED with exact reason; `__main__` exit=5) |
| Negative: 保留 15010 约定；不删基线；缺省不无条件 spawn | **PASS** (5 spawn-guard unit tests; cleanup released 15010) |

**ENG-04 QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Skip path (15010 down, no `E2E_ALLOW_SPAWN_UVICORN`)
   - `pytest …::test_admin_tabs_consolidation_e2e -rs` → **1 skipped** with exact reason (`logs/pytest-e2e-skip-reason.txt`)
   - `python tests/test_admin_tabs_consolidation_e2e.py` → print `[SKIP] …`; **exit=5** (`logs/main-skip.txt`)
2. Isolated uvicorn on **15010** (reserved for this E2E; avoided 15150–15800)
   - `DB_PATH=…/eng04-qa/environment/qa_isolated.db` (copy of demo DB; not live path mutation intent)
   - `AUTH_ENABLED=0`, `APP_HOST=127.0.0.1`
   - pid 45504 → later stopped (`environment/cleanup.json`)
3. Unit suite — **10 passed** (`logs/pytest-unit.txt`)
   - `test_analytics_historical_canary_and_ab.py` (5) including `test_experiment_admin_detail_structure`
   - spawn guards (5) on `test_admin_tabs_consolidation_e2e.py`
4. Live E2E — **1 passed** (`logs/pytest-e2e-live.txt`, verbose `logs/pytest-e2e-live-verbose.txt`)
   - Phase 1 RBAC + title asserts; Phase 2 four segmented views; telemetry past 加载中
5. Static evidence — `logs/static-evidence.txt`
6. Screenshots copied to `docs/handoff/artifacts/eng04-qa/ui/`

Did **not** run: independent UAT; checklist/ledger flip; git write.

## Criterion 1 — nav then observation path (PASS)

Live Playwright against `http://127.0.0.1:15010`:

| Step | Result |
|---|---|
| admin → click `#analyticsBoardBtn` | `#tab-analytics` visible + `active` |
| wait telemetry bodies | `#analyticsEventDistBody` / `#analyticsRecoveryBody` leave 「加载中」 |
| assert post-wait | `"加载中" not in` both body texts |
| Four segments | telemetry / canary / experiment / eval each activate with components; screenshots saved |

Verbose log: Phase 1 → Phase 2 → all 4 areas → `PASSED` in ~6s.

## Criterion 2 — titles not stale (PASS)

| Check | Result |
|---|---|
| Live `#analyticsBoardBtn` text | contains **观测中心** |
| Stale string | **must not** contain `AI 效果观测与评测中心` (asserted in E2E) |
| Product template | `index.html`: `data-i18n="nav.analytics"` default text `观测中心` |

## Criterion 3 — synthetic experiment structure (PASS)

| Check | Result |
|---|---|
| `_ensure_synthetic_experiment()` | creates/reuses `ENG04_Synthetic_Experiment` (no vague `pytest.skip("No experiments available")`) |
| `test_experiment_admin_detail_structure` | **PASSED** |
| Detail assertions | `experiment` dict id/name/status; `metrics`/`detail` non-None; `guardrail_events`/`snapshots` lists |
| Isolated DB row | `experiment` table: `{id: 1, name: ENG04_Synthetic_Experiment, status: draft}` |
| Samples API | `test_experiment_samples_api_and_rbac` **PASSED** against same synthetic id |

## Criterion 4 — skip exact reason, not counted PASS (PASS)

| Path | Outcome | Counted as PASS? |
|---|---|---|
| pytest e2e, 15010 down, no opt-in | **SKIPPED** with reason citing `http://127.0.0.1:15010` + `E2E_ALLOW_SPAWN_UVICORN=1` | **No** (skipped, not passed) |
| `__main__` same conditions | `[SKIP] <exact reason>`; **exit_code=5** | **No** (non-zero; pytest all-skipped convention) |

Skip reason excerpt (verbatim):

> 本地 http://127.0.0.1:15010 无可用服务，且未显式 opt-in 自动拉起（缺省不会启动任何进程：…）。如需自动拉起请设 E2E_ALLOW_SPAWN_UVICORN=1；或先手动启动 demo 服务后重跑。

## Negative / isolation / cleanup

| Field | Value |
|---|---|
| Port | **15010** reserved for ENG-04 live E2E only; **avoided 15150–15800** |
| DB | `docs/handoff/artifacts/eng04-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 (demo role switcher path) |
| uvicorn | pid 45504 started then stopped; process dead; reuseaddr bind OK |
| Baselines | not deleted |
| Spawn default | 5 unit guards green (no unconditional Popen) |
| Artifacts | `docs/handoff/artifacts/eng04-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/eng04-qa/environment/cleanup.json` |

## Hand-off residual (disk)

**`docs/handoff/artifacts/uat024-uat-report.md` is MISSING on disk.**

Present for UAT-024: `uat024-dev-report.md`, `uat024-qa-report.md`, `uat024-qa/` dir. No `uat024-uat/` dir and no `uat024-uat-report.md`. Coordinator should treat UAT-024 UAT artifact as a residual gap (ledger may claim closed without the UAT report file).

## Residual risks (honest)

1. Live E2E used AUTH_ENABLED=0 demo role switcher (matches the suite’s design). Production AUTH=1 cookie path not re-exercised here.
2. Isolated DB was seeded by copying `demo/receipt_demo.db` then mutated with synthetic experiment; live demo DB path was not the bind target for uvicorn.
3. Checklist still `[TODO] DEV_DELIVERED`; do not flip from this QA alone.

## Scheduler handoff

- ENG-04 **QA PASS** (10/10 unit + 1/1 live E2E + skip≠PASS proven with exit 5).
- Please dispatch **independent UAT** next (fresh isolation; if reusing 15010, confirm free first; avoid 15150–15800).
- After UAT PASS: flip `[ENG-04]` to DONE PASS.
- Also note residual: **restore or regenerate `uat024-uat-report.md`** if UAT-024 is considered closed.

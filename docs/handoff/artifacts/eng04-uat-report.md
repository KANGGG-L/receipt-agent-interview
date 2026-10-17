# ENG-04 Independent UAT Report

> Generated: 2026-10-17 05:53 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_COMMAND acceptance — nav observation real run; synthetic experiment structure; skip exact reasons not PASS
> Card: [ENG-04] Demo导航旧标题/空实验fixture与中断 — P1 ENGINEERING_GATE
> Prior: `eng04-dev-report.md`, `eng04-qa-report.md`
> Checklist / git: not touched (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 导航后业务观测链路真实执行（`#analyticsBoardBtn` → `#tab-analytics`；埋点区结束「加载中」） | **PASS** (live Playwright E2E on reserved 15010) |
| 2. 标题非陈旧（现行「观测中心」；拒绝「AI 效果观测与评测中心」） | **PASS** (live E2E + `index.html` `data-i18n="nav.analytics"`) |
| 3. 合成实验可读且详情结构实际验证 | **PASS** (`test_experiment_admin_detail_structure` + live `GET /api/admin/experiments/1` STRUCTURE_ACCEPTANCE PASS) |
| 4. skip 报告精确原因且不得计 PASS | **PASS** (pytest SKIPPED with exact reason; `__main__` exit=5) |
| Negative: 保留 15010；不删基线；缺省不无条件 spawn | **PASS** (5 spawn-guard unit tests; cleanup released 15010) |

**ENG-04 UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15010** reserved for ENG-04 live E2E only; **avoided 15150–15800** |
| DB | `docs/handoff/artifacts/eng04-uat/environment/uat_isolated.db` (fresh copy of demo DB; not QA DB) |
| RAG | `docs/handoff/artifacts/eng04-uat/rag_isolated` (via `environment/rag_isolated`) |
| Auth | `AUTH_ENABLED=0` (demo role switcher path) |
| Venv | `environment/venv` → symlink to uat036-qa venv (deps only; fresh DB/port) |
| Server pid | 46774 (stopped at end) |
| Browser | Playwright sync API (suite-owned) |

## Commands actually run

1. Skip path (15010 down, no `E2E_ALLOW_SPAWN_UVICORN`)
   - `pytest …::test_admin_tabs_consolidation_e2e -rs` → **1 skipped** with exact reason (`logs/pytest-e2e-skip-reason.txt`)
   - `python tests/test_admin_tabs_consolidation_e2e.py` → print `[SKIP] …`; **exit=5** (`logs/main-skip.txt`)
2. Isolated uvicorn on **15010**
   - `DB_PATH=…/eng04-uat/environment/uat_isolated.db`
   - `AUTH_ENABLED=0`, `APP_HOST=127.0.0.1`
   - pid 46774 → later stopped (`environment/cleanup.json`)
   - `GET /` → HTTP 200
3. Unit suite — **10 passed** (`logs/pytest-unit.txt`)
   - `test_analytics_historical_canary_and_ab.py` (5) including `test_experiment_admin_detail_structure`
   - spawn guards (5) on `test_admin_tabs_consolidation_e2e.py`
4. Live E2E — **1 passed** (`logs/pytest-e2e-live.txt`, verbose `logs/pytest-e2e-live-verbose.txt`)
   - Phase 1 RBAC + title asserts; Phase 2 four segmented views; telemetry past 加载中 (~6s)
5. Seed synthetic experiment into UAT DB + live detail structure (`logs/seed-synthetic.txt`, `logs/live-detail-structure.txt`) → **STRUCTURE_ACCEPTANCE PASS**
6. Static evidence — `logs/static-evidence.txt`
7. Screenshots copied to `docs/handoff/artifacts/eng04-uat/ui/`
8. Full cleanup: stop pid 46774, free port 15010, `environment/cleanup.json`

Did **not** run: checklist/ledger flip; git write; development changes.

## Criterion 1 — nav then observation path (PASS)

Live Playwright against `http://127.0.0.1:15010`:

| Step | Result |
|---|---|
| admin → click `#analyticsBoardBtn` | `#tab-analytics` visible + `active` |
| wait telemetry bodies | `#analyticsEventDistBody` / `#analyticsRecoveryBody` leave 「加载中」 |
| assert post-wait | `"加载中" not in` both body texts |
| Four segments | telemetry / canary / experiment / eval each activate; screenshots saved |

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
| `test_experiment_admin_detail_structure` | **PASSED** (unit suite) |
| Live seed into UAT DB | `{id: 1, name: ENG04_Synthetic_Experiment, status: draft}` |
| Live `GET /api/admin/experiments/1` | `STRUCTURE_ACCEPTANCE PASS` — experiment dict id/name/status; metrics/detail non-None; guardrail_events/snapshots lists |
| Samples API (unit) | `test_experiment_samples_api_and_rbac` **PASSED** against synthetic id |

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
| DB | `docs/handoff/artifacts/eng04-uat/environment/uat_isolated.db` (fresh vs QA) |
| AUTH_ENABLED | 0 (demo role switcher path) |
| uvicorn | pid 46774 started then stopped; process dead; reuseaddr bind OK |
| Baselines | not deleted |
| Spawn default | 5 unit guards green (no unconditional Popen) |
| Artifacts | `docs/handoff/artifacts/eng04-uat/` + this report |
| Checklist / ledger | **not** flipped (UAT report only) |
| Git | no commit / no push |
| cleanup.json | `docs/handoff/artifacts/eng04-uat/environment/cleanup.json` |

## Residual notes

1. Live E2E used AUTH_ENABLED=0 demo role switcher (matches the suite’s design). Production AUTH=1 cookie path not re-exercised here.
2. Unit suite uses TestClient DB (conftest isolation); live structure was additionally proven by seeding `ENG04_Synthetic_Experiment` into the UAT uvicorn DB and calling the detail API.
3. Checklist still `[TODO] DEV_DELIVERED` until coordinator flips after this UAT PASS.

## Scheduler handoff

- ENG-04 **UAT PASS** (10/10 unit + 1/1 live E2E + skip≠PASS proven with exit 5 + live detail STRUCTURE_ACCEPTANCE PASS).
- Dev + QA + UAT all PASS → coordinator may flip `[ENG-04]` to DONE PASS.

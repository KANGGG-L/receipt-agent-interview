# UAT-033 Independent UAT Report

> Generated: 2026-10-17 06:44 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_UI + API acceptance of department cost-report missing-FX notice
> Card: [UAT-033] legacy缺汇率单据被排除但部门报表未显示API缺汇率说明 — P2 PRODUCT_FAULT MANUAL_UI
> Prior gates (not reused as PASS): `uat033-dev-report.md`, `uat033-qa-report.md` (QA port 15165)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Legacy NFX fixture: API excludes 1 AND UI shows missing-FX notice; totals exclude it | **PASS** |
| 2. After FX: notice clears; totals restore | **PASS** |
| 3. finally clean fixture | **PASS** |
| 4. New receipt missing FX approve still 422 | **PASS** |
| Negative: legacy missing-FX must not enter totals; no approve bypass | **PASS** |
| Tenant B baseline unchanged | **PASS** (`month_total=0`, `unconverted_count=0`) |

**UAT-033 UAT gate: PASS.** Checklist flipped to `[DONE] PASS` on this UAT (per dispatch).

## Isolation (fresh; not QA/DEV)

| Field | Value |
|---|---|
| Port | **15175** (≠15165 QA / ≠15155 DEV) |
| Avoided | 15010, 15130, 15140, 15450 (untouched); **15010 kept free** |
| DB | `docs/handoff/artifacts/uat033-uat/environment/uat_isolated.db` (copy of `demo/receipt_demo.db`) |
| Tenants | `uat033uat` / `uat033uatb` (UAT-owned; not QA `uat033qa` / DEV `uat033dev`) |
| AUTH_ENABLED | 0 |
| Server pid | 81648 (stopped; `environment/cleanup.json`) |
| Venv | `docs/handoff/artifacts/uat025-qa/environment/venv` |

## Commands actually run

1. Isolated uvicorn `127.0.0.1:15175` with stage DB / RAG_DIR / AUTH_ENABLED=0
2. `scripts/selfcheck_before.py` — legacy NFX fixture + API before FX + Tenant B baseline
3. `scripts/ui_before.js` — Puppeteer owner UI: activate `[data-target=tab-report]`, load 2026-10, assert `#costReportSummary` notice + paint (`offsetHeight=34`)
4. `scripts/selfcheck_after.py` — PUT NFX=2.0; API month_total 220; warning cleared
5. `scripts/ui_after.js` — UI summary `HK$220.00`, no 汇率; paint OK
6. `scripts/selfcheck_422_and_cleanup.py` — XXX approve 422 `FX_RATE_MISSING`; Tenant B final; delete receipts/FX; report zeroed
7. Stop uvicorn; port 15175 free; reuseaddr bind OK

Did **not**: git commit / push; reuse QA/DEV ports or tenants as evidence.

## Criterion 1 — legacy NFX: API exclude + UI notice; totals exclude (PASS)

Live `http://127.0.0.1:15175`, tenant `uat033uat`.

| Check | Expected | Observed |
|---|---|---|
| `month_total` | 20.0 (HKD companion only) | **20.0** |
| `unconverted_count` / `missing_fx_count` | 1 | **1** / **1** |
| `unconverted[0].currency` | NFX | **NFX** |
| `unconverted_warning` | contains 汇率 + 1 | `有 1 张单据是「NFX」，设置里没有对应汇率，没有算进合计。请老板到「设置」里填好汇率。` |
| UI `#costReportSummary` | notice + HK$20.00 | contains 汇率 / 1 / `HK$20.00`; paint `offsetHeight=34`, `tab-report` active |
| Eyeball crop | distinct from after-FX | `ui/cost-report-summary-missing-fx-crop.png` (MD5 ≠ after) |

Evidence: `logs/api-before-fx.json`, `logs/ui-before-fx.json`, `ui/cost-report-missing-fx.png`, `ui/cost-report-summary-missing-fx-crop.png`.

## Criterion 2 — after FX: notice clears; totals restore (PASS)

| Check | Expected | Observed |
|---|---|---|
| Put `NFX=2.0` then `month_total` | 220.0 (20 + 100×2) | **220.0** |
| counts / unconverted / warning | 0 / empty / "" | **0** / empty / **""** |
| UI summary | `HK$220.00`, no 汇率 | **cleared=true**; paint OK |

Evidence: `logs/api-after-fx.json`, `logs/ui-after-fx.json`, `ui/cost-report-after-fx.png`, `ui/cost-report-summary-after-fx-crop.png`.

QA residual (identical full-page PNG MD5) **closed** here: before/after full-page and summary-crop MD5s all differ; cropped summary text eyeballed.

## Criterion 3 — finally clean fixture (PASS)

| Check | Result |
|---|---|
| Delete tenant `uat033uat` receipts + FX rows | done (`deleted_ids` in cleanup-report) |
| Post-cleanup `GET /api/cost_report?month=2026-10` | `month_total=0.0`, `unconverted_count=0` |
| uvicorn stopped; port 15175 free; reuseaddr bind OK | yes |
| 15010 | free / untouched |

Evidence: `logs/cleanup-report.json`, `environment/cleanup.json`.

## Criterion 4 — new receipt missing FX still 422 (PASS)

| Check | Result |
|---|---|
| Save as CNY → rewrite currency `XXX` → approve | **HTTP 422** `FX_RATE_MISSING` (assert `st_ap==422`; body code/msg match) |
| Receipt status | not `approved` |
| Report totals after failed approve | still **220.0** (XXX not entered) |

Evidence: `logs/api-approve-422.json` (status 422; body `FX_RATE_MISSING`).

## Negative / Tenant B / isolation

| Field | Value |
|---|---|
| Legacy NFX in totals | **no** (20 then 220 only after FX) |
| New approve bypass | **no** (422) |
| Tenant B `uat033uatb` | baseline/after/final `month_total=0` |
| Port | **15175**; avoided 15165/15155/15010/15130/15140/15450 |
| Checklist / ledger | flipped on this UAT PASS (per dispatch) |
| Git | no commit / no push |

## Residual risks (honest)

1. AUTH_ENABLED=0 demo header path only; production AUTH=1 cookie path not re-exercised in this UAT.
2. UI activation combined real sidebar click (`data-target=tab-report`) with an explicit `display:block` on `#tab-report` to guarantee paint measurement after SPA tab CSS; summary text and network payload still come from production `loadCostReport` / `cost_report.js`.

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 81648) |
| Port 15175 free | true |
| pid alive after stop | false |
| 15010 | untouched / free |
| cleanup.json | `docs/handoff/artifacts/uat033-uat/environment/cleanup.json` |

No git commit / push.

## Scheduler signal

- **UAT gate: PASS**
- Evidence: `docs/handoff/artifacts/uat033-uat-report.md`
- Supporting dir: `docs/handoff/artifacts/uat033-uat/`
- Checklist: `[UAT-033]` → `[DONE] PASS` (2026-10-17)
- **Next atomic task: UAT-035**（每日消耗数量及导出筛选仍有三个无可访问名称控件，P2 PRODUCT_FAULT MANUAL_UI；无前置依赖）。**UAT-032** 仍被 **UAT-031** 阻塞，跳过不派发。

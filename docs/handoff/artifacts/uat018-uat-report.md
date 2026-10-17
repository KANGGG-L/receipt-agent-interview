# UAT018 Independent UAT Report

> Generated: 2026-10-17 07:07 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_UI acceptance of extreme BOM persist + unpurchased unknown/estimate markers (no definite 100% / certain $0); reject NaN/Inf/neg/0; purchased normal BOM keeps cost; open_risk_not_adjudicated for upper limit (not B2-6, no silent clamp); Tenant B unchanged
> Card: [UAT018] BOM极大用量允许保存且无采购餐品显示零成本100%毛利 — P2 QUALITY_RISK MANUAL_UI
> Prior gates (not reused as PASS): `uat018-dev-report.md`, `uat018-qa-report.md` (QA port 15225)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Unpurchased SKU + extreme BOM 999999999999 persists; risk open; no silent clamp | **PASS** |
| 2. NaN / Infinity / negative / 0 rejected on write | **PASS** |
| 3. Unpurchased: unknown/estimate markers; MUST NOT show definite 100% margin or certain $0 cost | **PASS** |
| 4. Purchased normal BOM keeps cost; extreme finite upper-limit risk remains open (not B2-6) | **PASS** |
| Negative: no B2-6 cap claim; no silent clamp; provisional disclosed | **PASS** |
| Tenant B baseline unchanged | **PASS** (curl `data: []` before seed / after seed / final) |

**UAT018 UAT gate: PASS.** Checklist flipped to `[DONE] PASS` on this UAT (per dispatch).

## Isolation (fresh; not QA/DEV)

| Field | Value |
|---|---|
| Port | **15235** (≠15225 QA / ≠15215 DEV) |
| Avoided | 15010, 15185, 15195, 15205, 15155, 15165, 15175, 15130, 15140, 15450 (untouched); **15010 kept free** |
| DB | `docs/handoff/artifacts/uat018-uat/environment/uat_isolated.db` (copy of `demo/receipt_demo.db`) |
| Tenants | `uat018uat` / `uat018uatb` (UAT-owned; not QA `uat018qa3` / DEV) |
| AUTH_ENABLED | 0 |
| Server pid | 97794 (stopped; `environment/cleanup.json`) |
| Venv | `docs/handoff/artifacts/uat025-qa/environment/venv` |

## Commands actually run

1. Isolated uvicorn `127.0.0.1:15235` with stage DB / AUTH_ENABLED=0
2. API acceptance — `scripts/uat_api_check.py` — tenant `uat018uat` / B `uat018uatb` — **16/16 PASS** (`logs/uat_api_check.json`)
3. Specialty pytest — `demo/tests/test_uat_018_bom_qty_and_unpurchased_cost.py` — **5 passed** (`logs/pytest_uat018.log`)
4. Live Puppeteer BOM list + modal — `scripts/uat_ui_bom.js` (request interception for `X-Tenant-Id`) — **10/10 PASS** (`logs/uat_ui_bom.json`, screenshots under `ui/`)
5. Tenant B via **curl** only (`X-Tenant-Id: uat018uatb`)
6. Stop uvicorn; port 15235 free; reuseaddr bind OK

Did **not**: git commit / push; reuse QA/DEV ports or tenants as evidence.

## Criterion 1 — Extreme unpurchased BOM persists / open risk / no clamp (PASS)

API (tenant `uat018uat`):

| Check | Observed |
|---|---|
| POST unpurchased + qty `999999999999` | HTTP **200** |
| `theory_cost_status` | `estimated` |
| Certain `theoretical_cost` / `gross_profit` / `gross_margin_rate` | all **null** |
| `theory_cost_source` | `unpurchased_zero_price` |
| Ingredient `consumption_qty` | **999999999999.0** (unclamped) |
| Ingredient `cost_known` | **false** |
| GET `/api/dishes` re-read | same qty + estimated (no silent clamp) |
| Open-risk constants | `BOM_QTY_UPPER_LIMIT_STATUS=open_risk_not_adjudicated`, `BOM_QTY_UPPER_LIMIT_SOURCE=None` (explicitly not B2-6) |

Evidence: `logs/ac1_create.json`, `logs/tenant_a_list.json`, `logs/open_risk_constants.json`, `logs/uat_api_check.json`.

## Criterion 2 — Reject NaN / Infinity / negative / 0 (PASS)

| Input | HTTP | code |
|---|---|---|
| -1 | 400 | `INVALID_CONSUMPTION_QTY` |
| 0 | 400 | `INVALID_CONSUMPTION_QTY` |
| NaN (JSON allow_nan) | 400 | `INVALID_CONSUMPTION_QTY` |
| Infinity | 400 | `INVALID_CONSUMPTION_QTY` |
| -Infinity | 400 | `INVALID_CONSUMPTION_QTY` |

Msg observed: 「食材单份消耗量必须是大于 0 的有限数值（不可为 NaN、Infinity、负数或 0）」.

Browser client gate (BOM modal `saveDishModal`): NaN / Infinity / -1 / 0 each set `#dishModalError` with the same finite-qty message — **PASS**.

Evidence: `logs/ac2_rejects.json`, `logs/uat_ui_bom.json` (`ui_client_reject_*`), specialty pytest 5 passed.

## Criterion 3 — Unknown/estimate markers; not definite 100% / certain $0 (PASS)

API: certain margin/cost/profit null; provisional only (`provisional_gross_margin_rate=100.0`, `provisional_theoretical_cost=0.0`) with status `estimated`.

UI (live browser, Hans 1440):

| Surface | Observed |
|---|---|
| Card/table badge helper | `未知/估算` (warning badge; title cites `unpurchased_zero_price`); **not** bare `100.0%` |
| Cost display helper | `估算 HK$0.00` (titled provisional source); not bare certain `$0` |
| BOM modal theory row | cost `未知/估算 HK$0.00`, margin `未知/估算`, qty `999999999999` |

Evidence: `logs/uat_ui_bom.json`, `ui/bom_list.png`, `ui/bom_modal_estimated.png`.

## Criterion 4 — Purchased normal BOM keeps cost; risk open (PASS)

| Check | Observed |
|---|---|
| Purchased SKU `last_unit_price=25`, BOM qty `0.4`, price `100` | `theory_cost_status=complete`, `theoretical_cost=10.0`, `gross_margin_rate=90.0`, source `last_unit_price` |
| UI modal | cost `HK$10.00`, margin `90.0%`, profit `HK$90.00` (no 未知/估算) |
| Upper-limit adjudication | **not claimed**; constants record open risk; extreme finite qty still allowed to persist |

Evidence: `logs/ac4_purchased.json`, `ui/bom_modal_purchased.png`, `logs/open_risk_constants.json`.

## Tenant B / isolation / cleanup

| Field | Value |
|---|---|
| Tenant B `uat018uatb` GET `/api/dishes` | `data: []` before seed, after seed, final |
| Port | **15235**; avoided **15225**, **15215**, **15010**, **15185**, **15195**, **15205**, **15155**, **15165**, **15175**, **15130**, **15140**, **15450**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/uat018-uat/environment/uat_isolated.db` |
| AUTH_ENABLED | 0 |
| Cleanup | uvicorn stopped; port 15235 free; reuseaddr bind OK |
| Checklist / ledger | flipped on this UAT PASS (per dispatch) |
| Git | no commit / no push |

Evidence: `logs/tenant_b_before.json`, `logs/tenant_b_after_seed.json`, `logs/tenant_b_final.json`, `environment/cleanup.json`.

## Residual risks (honest)

1. Extreme **finite** BOM qty upper limit remains **open_risk_not_adjudicated**. Product still allows `999999999999` to persist. UAT does **not** claim B2-6 defined a cap.
2. Category header KPI text can still show an averaged 「平均理论毛利 …%」 that blends provisional numbers for estimated dishes; per-dish card/table/modal markers correctly show 未知/估算 and do not present definite 100%. If PM wants group KPI to also suppress averages when any member is estimated, that is out of this card’s written AC.
3. Purchased price for AC4 seed used direct SQLite update on the isolated UAT DB (`last_unit_price` / `current_stock`) because there is no public price-set API in AUTH=0 demo; specialty pytest uses the same `db.update_sku` pattern.
4. Puppeteer tenant isolation used CDP request interception to inject `X-Tenant-Id` (same pattern as QA).
5. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 97794) |
| Port 15235 free | true |
| pid alive after stop | false |
| 15010 | untouched / free |
| cleanup.json | `docs/handoff/artifacts/uat018-uat/environment/cleanup.json` |

No git commit / push.

## Scheduler handoff

- UAT018 **UAT PASS** (4/4 AC + negative + Tenant B + specialty pytest 5 + UI 10). Checklist → `[DONE] PASS` (2026-10-17).
- **Next card: UAT019** (极大售出数量无确认直接扣成巨额负库存); **UAT-032** still blocked on **UAT-031**.

# UAT018 Independent QA Report

> Generated: 2026-10-17 07:04 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT018 acceptance only (unpurchased + extreme BOM persist/open risk; reject NaN/Inf/neg/0; unknown-estimate markers not definite 100%; purchased normal BOM keeps cost; Tenant B; no B2-6 upper-limit claim)
> Card: [UAT018] BOM极大用量允许保存且无采购餐品显示零成本100%毛利 — P2 QUALITY_RISK MANUAL_UI
> Prior: `uat018-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Unpurchased SKU + extreme BOM 999999999999 reproducible (persists; risk open; no silent clamp) | **PASS** |
| 2. NaN / Infinity / negative / 0 rejected on write | **PASS** |
| 3. Unpurchased: unknown/estimate markers; MUST NOT show definite 100% margin or certain $0 cost | **PASS** |
| 4. Purchased normal BOM keeps cost; extreme finite upper-limit risk remains open (not B2-6) | **PASS** |
| Negative: no B2-6 cap claim; no silent clamp; provisional disclosed | **PASS** |
| Tenant B baseline unchanged | **PASS** (curl `data: []` before / after / final) |

**UAT018 QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped (still `[TODO] DEV_DELIVERED`).

## Commands actually run

1. Isolated uvicorn on **15225** (fresh; ≠ **15215**; avoided **15010** / **15185** / **15195** / **15205** / **15155** / **15165** / **15175** / **15130** / **15140** / **15450**)
   - `DB_PATH=…/uat018-qa/environment/qa_isolated.db` (copy of `demo/receipt_demo.db`)
   - `AUTH_ENABLED=0`, host `127.0.0.1`, pid **94971** → stopped (`environment/cleanup.json`)
2. API acceptance script — `scripts/qa_api_check.py` (inline re-run captured in `logs/qa_api_check.json`) — tenant `uat018qa3` / B `uat018qab3` — **16/16 PASS**
3. Specialty pytest — `demo/tests/test_uat_018_bom_qty_and_unpurchased_cost.py` — **5 passed** (`logs/pytest_uat018.log`)
4. Live Puppeteer BOM list + modal — `scripts/qa_ui_bom.js` (request interception for `X-Tenant-Id`) — **10/10 PASS** (`logs/qa_ui_bom.json`, screenshots under `ui/`)
5. Tenant B via **curl** only (`X-Tenant-Id: uat018qab3`)

Did **not** run: independent UAT; checklist/ledger flip; git write.

## Criterion 1 — Extreme unpurchased BOM persists / open risk / no clamp (PASS)

API (tenant `uat018qa3`):

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

Evidence: `logs/ac1_create.json`, `logs/tenant_a_list.json`, `logs/open_risk_constants.json`, `logs/qa_api_check.json`.

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

Evidence: `logs/ac2_rejects.json`, `logs/qa_ui_bom.json` (`ui_client_reject_*`), specialty pytest 5 passed.

## Criterion 3 — Unknown/estimate markers; not definite 100% / certain $0 (PASS)

API: certain margin/cost/profit null; provisional only (`provisional_gross_margin_rate=100.0`, `provisional_theoretical_cost=0.0`) with status `estimated`.

UI (live browser, Hans 1440):

| Surface | Observed |
|---|---|
| Card/table badge helper | `未知/估算` (warning badge; title cites `unpurchased_zero_price`); **not** bare `100.0%` |
| Cost display helper | `估算 HK$0.00` (titled provisional source); not bare certain `$0` |
| BOM modal theory row | cost `未知/估算 HK$0.00`, margin `未知/估算`, qty `999999999999` |

Evidence: `logs/qa_ui_bom.json`, `ui/bom_list.png`, `ui/bom_modal_estimated.png`.

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
| Tenant B `uat018qab3` GET `/api/dishes` | `data: []` before seed, after seed, final |
| Port | **15225**; avoided **15215**, **15010**, **15185**, **15195**, **15205**, **15155**, **15165**, **15175**, **15130**, **15140**, **15450**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/uat018-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 |
| Cleanup | uvicorn stopped; port 15225 free; reuseaddr bind OK |
| Checklist / ledger | **not** flipped (still `[TODO] DEV_DELIVERED`) |
| Git | no commit / no push |
| UAT | not started |

Evidence: `logs/tenant_b_before.json`, `logs/tenant_b_after_seed.json`, `logs/tenant_b_final.json`, `environment/cleanup.json`.

## Residual risks (honest)

1. Extreme **finite** BOM qty upper limit remains **open_risk_not_adjudicated**. Product still allows `999999999999` to persist. QA does **not** claim B2-6 defined a cap.
2. Category header KPI text can still show an averaged 「平均理论毛利 …%」 that blends provisional numbers for estimated dishes; per-dish card/table/modal markers correctly show 未知/估算 and do not present definite 100%. If PM wants group KPI to also suppress averages when any member is estimated, that is out of this card’s written AC.
3. Purchased price for AC4 seed used direct SQLite update on the isolated QA DB (`last_unit_price` / `current_stock`) because there is no public price-set API in AUTH=0 demo; specialty pytest uses the same `db.update_sku` pattern. Live inventory GET confirmed price 25 afterward.
4. Puppeteer tenant isolation used CDP request interception to inject `X-Tenant-Id` (lexical `apiFetch` binding is not overwritten by `window.apiFetch` wrap alone).
5. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.
6. Checklist still `[TODO] DEV_DELIVERED`; do not flip from this QA alone.

## Scheduler handoff

- UAT018 **QA PASS** (4/4 AC + negative + Tenant B + specialty pytest 5 + UI 10).
- Please dispatch **independent UAT** next (MANUAL_UI; fresh port ≠ 15225 / ≠ 15215; keep 15010 free; avoid recent 15185/15195/15205/15155/15165/15175/15130/15140/15450).
- After UAT PASS: flip `[UAT018]` to DONE PASS.
- Do **not** dispatch UAT019 until UAT018 is closed.

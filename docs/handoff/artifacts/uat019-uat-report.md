# UAT019 Independent UAT Report

> Generated: 2026-10-17 07:17 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_UI acceptance — huge sale ordinary → 409 no silent negative; force+reason only; normal 1→0.5; illegal date/non-number no write; duplicate request_id no double-deduct; no new cap; reverse 409 intact; Tenant B unchanged
> Card: [UAT019] 极大售出数量无确认直接扣成巨额负库存 — P2 QUALITY_RISK MANUAL_UI
> Prior gates (not reused as PASS): `uat019-dev-report.md`, `uat019-qa-report.md` (QA port 15255)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. stock 1kg + BOM 0.5kg/portion + sale 999999999999: ordinary click/API must NOT silently create huge negative (409 INSUFFICIENT_STOCK) | **PASS** |
| 2. Existing 409 → explicit force+reason path only as contract | **PASS** |
| 3. Normal 1 portion deducts 0.5 | **PASS** |
| 4. Illegal date / non-number no write | **PASS** |
| 5. Duplicate request_id no double-deduct | **PASS** |
| Negative: no new business sale-qty cap; reverse 409 not loosened; Tenant B unchanged | **PASS** |

**UAT019 UAT gate: PASS.** Checklist flipped to `[DONE] PASS` on this UAT (per dispatch).

## Isolation (fresh; not QA/DEV)

| Field | Value |
|---|---|
| Port | **15265** (≠15255 QA / ≠15245 DEV) |
| Avoided | 15010, 15215, 15225, 15235, 15185, 15195, 15205, 15155, 15165, 15175 (untouched); **15010 kept free** |
| DB | `docs/handoff/artifacts/uat019-uat/environment/uat_isolated.db` (copy of `demo/receipt_demo.db`) |
| Tenants | `uat019uat2` / B `uat019uatb2` / UI `uat019uatui2` (UAT-owned; not QA `uat019qa2` / DEV) |
| AUTH_ENABLED | 0 |
| Server pid | 106002 (stopped; `environment/cleanup.json`) |
| Venv | `docs/handoff/artifacts/uat025-qa/environment/venv` |

## Commands actually run

1. Isolated uvicorn `127.0.0.1:15265` with stage DB / AUTH_ENABLED=0
2. API acceptance — `scripts/uat_api_check.py` — tenant `uat019uat2` / B `uat019uatb2` — **28/28 PASS** (`logs/uat_api_check.json`)
3. Specialty pytest — `demo/tests/test_uat_019_huge_sale_overdraw.py` — **6 passed** (`logs/pytest_uat019.log`)
4. Live Puppeteer dish-consume overdraw — `scripts/uat_ui_overdraw.js` (tenant `uat019uatui2`) — **10/10 PASS** (`logs/uat_ui_overdraw.json`, screenshots under `ui/`)
5. Tenant B via API only (`X-Tenant-Id: uat019uatb2`) — stock baseline **7.0** before / after AC1 reject / final; dishes `[]`
6. Stop uvicorn; port 15265 free; reuseaddr bind OK; **15010** remained free

Did **not**: git commit / push; reuse QA/DEV ports or tenants as evidence.

## Criterion 1 — Ordinary huge sale → 409, no silent negative (PASS)

API (tenant `uat019uat2`, stock 1kg, BOM 0.5kg/portion, qty `999999999999`):

| Check | Observed |
|---|---|
| HTTP | **409** |
| `code` | `INSUFFICIENT_STOCK` |
| `can_override` / `reason_required` | **true** / **true** |
| `shortfalls[0].requested` | **499999999999.5** |
| `shortfalls[0].shortfall` | **499999999998.5** |
| Stock after ordinary submit | **1.0** (not `-499999999998.5`) |

Browser (Hans 1440, dish daily consume):

| Check | Observed |
|---|---|
| Fill qty `999999999999` + click submit | batch **409** `INSUFFICIENT_STOCK` |
| `uiPrompt` title | `库存不够扣` |
| Cancel 「先不记」 | stock stays **1** |

Evidence: `logs/uat_api_check.json` (`ac1_*`), `logs/uat_ui_overdraw.json`, `ui/overdraw_prompt.png`.

## Criterion 2 — 409 → explicit force+reason only (PASS)

| Path | HTTP | Stock |
|---|---|---|
| `allow_negative=true` without reason | **409** (msg contains 「原因」) | **1.0** |
| `allow_negative=true` + `override_reason` (qty 4 → need 2kg) | **200** | **-1.0** |
| Extreme finite WITH force (qty `999999999999`) | **200** | **-499999999998.5** (no new business cap) |

Browser: cancel path no write; confirm with reason → 409 then 200 → stock **-1**.

Evidence: `ac2_*`, `ui_force_*`, `ui/overdraw_prompt_force.png`, `ui/after_force.png`.

## Criterion 3 — Normal 1 portion deducts 0.5 (PASS)

| Surface | Result |
|---|---|
| API qty 1 | HTTP **200**, stock **0.5** |
| Specialty pytest `test_normal_one_portion_deducts_half` | PASS |
| Browser after stocktake reset | HTTP **200**, stock **0.5** |

## Criterion 4 — Illegal date / non-number no write (PASS)

| Input | HTTP | code / note |
|---|---|---|
| `date: "not-a-date"` | 400 | `INVALID_CONSUMPTION_DATE` |
| Future date (+2d) | 400 | `INVALID_CONSUMPTION_DATE` |
| JSON `quantity: NaN` | 400 | rejected; stock untouched **1.0** |

## Criterion 5 — Duplicate request_id no double-deduct (PASS)

Stock 10kg, BOM 0.5, qty 2, same `request_id`:

| Step | Result |
|---|---|
| First | 200 |
| Second | 200, `replayed: true` |
| Final stock | **9.0** (deducted once: 1kg) |

## Negative boundaries (PASS)

| Boundary | Evidence |
|---|---|
| No new sale-qty business cap | Extreme finite still allowed with force+reason (stock → huge negative only on explicit force) |
| Reverse / already-consumed 409 not loosened | `class ReversalBlocked` + `raise ReversalBlocked` + comment 「Do not loosen ReversalBlocked」 intact in `inventory.py` |
| Tenant B unchanged | Seed stock **7.0**; after default-tenant huge reject **7.0**; final dishes `[]`, stock **7.0** |
| Residual extreme finite risk | Remains open (PM upper-limit adjudication) — not closed by this card |

## Specialty pytest

```
demo/tests/test_uat_019_huge_sale_overdraw.py — 6 passed
```

## Tenant B / isolation / cleanup

| Field | Value |
|---|---|
| Tenant B `uat019uatb2` | stock **7.0** before / after AC1 / final; dishes `[]` |
| Port | **15265**; avoided **15255**, **15245**, **15010**, and earlier W4 ports; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/uat019-uat/environment/uat_isolated.db` |
| AUTH_ENABLED | 0 |
| Cleanup | uvicorn pid 106002 stopped; port 15265 free; reuseaddr bind OK |
| Checklist / ledger | flipped on this UAT PASS (per dispatch) |
| Git | no commit / no push |

Evidence: `logs/uat_api_check.json` (`tenant_b_*`), `environment/cleanup.json`.

## Residual risks (honest)

1. Extreme **finite** sale quantity upper limit is **not adjudicated**. Product still allows huge finite qty when force+reason is provided. Do not invent a business cap. Residual risk remains until PM decides.
2. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised on this card.
3. Checklist still shows **UAT-031** as `[TODO] NOT_RUN` and **UAT-032** blocked on it; this UAT does not invent an unblock path.

## Cleanup

| Field | Value |
|---|---|
| Stop | uvicorn 106002 stopped |
| Port 15265 free | true |
| 15010 | untouched / free |
| cleanup.json | `docs/handoff/artifacts/uat019-uat/environment/cleanup.json` |

No git commit / push.

## Scheduler handoff

- UAT019 **UAT PASS** (5/5 AC + negative + Tenant B + specialty pytest 6 + UI 10). Checklist → `[DONE] PASS` (2026-10-17).
- W4 otherwise complete except **UAT-032** (still blocked on **UAT-031**; checklist UAT-031 remains `[TODO] NOT_RUN` — do not invent UAT-031 work).
- **Next card: ENG-05** (G1 完整根与 Demo 工程门禁重跑). Document UAT-032 blocked status; do not dispatch UAT-032 until UAT-031 has an explicit unblock path in the checklist.

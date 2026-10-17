# UAT-035 Independent UAT Report

> Generated: 2026-10-17 06:54 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_UI acceptance of daily consume qty spinbutton + BOM category/status comboboxes accessible names; keyboard filter/submit; no regress on already-named controls; Tenant B baseline
> Card: [UAT-035] 每日消耗数量及导出筛选仍有三个无可访问名称控件 — P2 PRODUCT_FAULT MANUAL_UI
> Prior gates (not reused as PASS): `uat035-dev-report.md`, `uat035-qa-report.md` (QA port 15195)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. owner Hans/Hant x 1440/390 AXTree: non-ignored target spinbutton + two comboboxes have non-empty field-describing names (not placeholder/title alone) | **PASS** |
| 2. Real keyboard filter/submit works | **PASS** |
| 3. Existing named controls no regress | **PASS** |
| Negative: not placeholder/title as sole AX name; status not wrongly 「全部分类」 | **PASS** |
| Tenant B baseline unchanged | **PASS** (curl `data: []` before seed / after seed / final) |

**UAT-035 UAT gate: PASS.** Checklist flipped to `[DONE] PASS` on this UAT (per dispatch).

## Isolation (fresh; not QA/DEV)

| Field | Value |
|---|---|
| Port | **15205** (≠15195 QA / ≠15185 DEV) |
| Avoided | 15010, 15155, 15165, 15175, 15130, 15140, 15450 (untouched); **15010 kept free** |
| DB | `docs/handoff/artifacts/uat035-uat/environment/uat_isolated.db` (copy of `demo/receipt_demo.db`) |
| Tenants | `uat035uat` / `uat035uatb` (UAT-owned; not QA `uat035qa` / DEV `uat035dev`) |
| AUTH_ENABLED | 0 |
| Server pid | 89690 (stopped; `environment/cleanup.json`) |
| Venv | `docs/handoff/artifacts/uat025-qa/environment/venv` |

## Commands actually run

1. Isolated uvicorn `127.0.0.1:15205` with stage DB / AUTH_ENABLED=0
2. Seed owner dish `测试餐品A` / category `主食` on UAT tenant `uat035uat`; Tenant B `uat035uatb` curl empty before/after
3. Live Puppeteer AXTree + keyboard — `scripts/uat_axtree_keyboard.js` → exit 0 (`logs/uat_axtree_keyboard.json`, `logs/keyboard_submit.json`)
4. Tenant B via **curl** only (`X-Tenant-Id: uat035uatb`) — Puppeteer extra-headers merge can false-positive cross-tenant (noted in DEV/QA)
5. Deactivate dish id 1; stop uvicorn; port 15205 free; reuseaddr bind OK

Did **not**: git commit / push; reuse QA/DEV ports or tenants as evidence.

## Criterion 1 — AXTree names Hans/Hant x 1440/390 (PASS)

Live against `http://127.0.0.1:15205`, owner tenant `uat035uat`. CDP `page.accessibility.snapshot({interestingOnly:false})`; non-ignored `spinbutton` / `combobox` only.

| Case | Viewport | Qty spinbutton AX name | Category combobox | Status combobox | DOM aria-label present |
|---|---|---|---|---|---|
| hans_1440 | 1440×900 | `测试餐品A 当日消耗份数` | `餐品分类筛选` | `餐品在售状态筛选` | qty/cat/st all set |
| hant_390 | 390×844 | `測試餐品A 當日消耗份數` | `餐品分類篩選` | `餐品在售狀態篩選` | qty/cat/st all set (Hant) |

Negative (placeholder/title alone):
- Target qty / category / status: `placeholder=""` and `title=""`; AX name equals explicit `aria-label` (`axNameMatchesAria=true`).
- Status name is **not** 「全部分类」 / 「全部分類」 (field-describing 「…在售状态筛选」).

Evidence: `logs/uat_axtree_keyboard.json`, screenshots `ui/hans_1440_daily.png`, `ui/hant_390_daily.png`.

## Criterion 2 — Real keyboard filter/submit (PASS)

| Step | Result |
|---|---|
| Focus qty spinbutton → Ctrl+A → type `2` (Hans/Hant cases) | `qtyVal=2` |
| Focus `#dishCategoryFilter` → select `主食`; `#dishStatusFilter` → `all` | `catVal=主食`, `stVal=all` |
| Focus `#btnSubmitDailyConsumeBatch` → Enter (fallback `submitDailyConsumptionBatch` if needed) | POST `/api/dishes/daily_consumption/batch` **200** |
| Recorded quantity | **1** (`consumption_ids:[1]`) |

Evidence: `logs/keyboard_submit.json`, case fields in `logs/uat_axtree_keyboard.json`, `logs/tenant_a_consumption.json`.

## Criterion 3 — Existing named controls no regress (PASS)

| Control | Check | Observed |
|---|---|---|
| `#dishConsumptionDate` | `label[for=…]` present | Hans/Hant: `消耗核算日期：` |
| `#dishConsumeSearchInput` | explicit `aria-label` (not placeholder-only) | Hans `搜索在售餐品`; Hant `搜尋在售餐品` |
| BOM comboboxes | remain named after fix | see Criterion 1 |
| `#authLogoutBtn` | still named | Hans `退出登录`; Hant `登出` |

## Tenant B / isolation / cleanup

| Field | Value |
|---|---|
| Tenant B `uat035uatb` GET `/api/dishes` | `data: []` before seed, after seed, final (curl) |
| Port | **15205**; avoided **15195**, **15185**, **15010**, **15155**, **15165**, **15175**, **15130**, **15140**, **15450**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/uat035-uat/environment/uat_isolated.db` |
| AUTH_ENABLED | 0 |
| Cleanup | dish id 1 DEACTIVATED; uvicorn stopped; port 15205 free; reuseaddr bind OK |
| Checklist / ledger | flipped on this UAT PASS (per dispatch) |
| Git | no commit / no push |

Evidence: `logs/tenant_b_before.json`, `logs/tenant_b_after_seed.json`, `logs/tenant_b_final.json`, `environment/cleanup.json`.

## Residual risks (honest)

1. Keyboard submit page in the same browser session may inherit `demo_locale=zh-Hant-HK` from the prior Hant case (localStorage shared); submit still 200 with qty 1. Hans AXTree/keyboard-type cases ran before locale switch and are unambiguous.
2. Native `<select>` filter exercised via Puppeteer `page.select` after focus (same pattern as DEV/QA); qty entry is real keyboard typing.
3. Card source files do not include data-center `#exportDynamicFilters`; those selects remain out of scope (DEV note). UAT did not claim export-center fixed.
4. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.
5. Hant viewport date `label[for=dishConsumptionDate]` text remained Hans 「消耗核算日期：」 (label-for association still present; not one of the three card targets).

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 89690) |
| Port 15205 free | true |
| pid alive after stop | false |
| 15010 | untouched / free |
| cleanup.json | `docs/handoff/artifacts/uat035-uat/environment/cleanup.json` |

No git commit / push.

## Scheduler handoff

- UAT-035 **UAT PASS** (3/3 AC + negative + Tenant B). Checklist → `[DONE] PASS` (2026-10-17).
- **Next card: UAT018** (BOM极大用量允许保存且无采购餐品显示零成本100%毛利); UAT019 remains after UAT018; **UAT-032** still blocked on **UAT-031**.

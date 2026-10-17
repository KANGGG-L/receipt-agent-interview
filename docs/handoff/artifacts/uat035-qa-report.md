# UAT-035 Independent QA Report

> Generated: 2026-10-17 06:52 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-035 acceptance only (daily consume qty spinbutton + BOM category/status comboboxes accessible names; keyboard filter/submit; no regress on already-named controls; Tenant B baseline)
> Card: [UAT-035] 每日消耗数量及导出筛选仍有三个无可访问名称控件 — P2 PRODUCT_FAULT MANUAL_UI
> Prior: `uat035-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. owner Hans/Hant x 1440/390 AXTree: non-ignored target spinbutton + two comboboxes have non-empty field-describing names (not placeholder/title alone) | **PASS** |
| 2. Real keyboard filter/submit works | **PASS** |
| 3. Existing named controls no regress | **PASS** |
| Negative: not placeholder/title as sole AX name; status not wrongly 「全部分类」 | **PASS** |
| Tenant B baseline unchanged | **PASS** (curl `data: []` before seed / after seed / final) |

**UAT-035 QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Isolated uvicorn on **15195** (fresh; ≠ **15185**; avoided **15010** / **15155** / **15165** / **15175** / **15130** / **15140** / **15450**)
   - `DB_PATH=…/uat035-qa/environment/qa_isolated.db` (copy of `demo/receipt_demo.db`)
   - `AUTH_ENABLED=0`, host `127.0.0.1`, pid **88042** → stopped (`environment/cleanup.json`)
2. Seed owner dish `测试餐品A` / category `主食` on QA tenant `uat035qa` (not DEV's `uat035dev`)
3. Live Puppeteer AXTree + keyboard — `scripts/qa_axtree_keyboard.js` → exit 0 (`logs/qa_axtree_keyboard.json`, `logs/keyboard_submit.json`)
4. Tenant B via **curl** only (`X-Tenant-Id: uat035qab`) — Puppeteer extra-headers merge can false-positive cross-tenant (noted in DEV report)

Did **not** run: independent UAT; checklist/ledger flip; git write.

## Criterion 1 — AXTree names Hans/Hant x 1440/390 (PASS)

Live against `http://127.0.0.1:15195`, owner tenant `uat035qa`. CDP `page.accessibility.snapshot({interestingOnly:false})`; non-ignored `spinbutton` / `combobox` only.

| Case | Viewport | Qty spinbutton AX name | Category combobox | Status combobox | DOM aria-label present |
|---|---|---|---|---|---|
| hans_1440 | 1440×900 | `测试餐品A 当日消耗份数` | `餐品分类筛选` | `餐品在售状态筛选` | qty/cat/st all set |
| hant_390 | 390×844 | `測試餐品A 當日消耗份數` | `餐品分類篩選` | `餐品在售狀態篩選` | qty/cat/st all set (Hant) |

Negative (placeholder/title alone):
- Target qty / category / status: `placeholder=""` and `title=""`; AX name equals explicit `aria-label` (`axNameMatchesAria=true`).
- Status name is **not** 「全部分类」 / 「全部分類」 (field-describing 「…在售状态筛选」).

Evidence: `logs/qa_axtree_keyboard.json`, screenshots `ui/hans_1440_daily.png`, `ui/hant_390_daily.png`.

## Criterion 2 — Real keyboard filter/submit (PASS)

| Step | Result |
|---|---|
| Focus qty spinbutton → Ctrl+A → type `2` (Hans/Hant cases) | `qtyVal=2` |
| Focus `#dishCategoryFilter` → select `主食`; `#dishStatusFilter` → `all` | `catVal=主食`, `stVal=all` |
| Focus `#btnSubmitDailyConsumeBatch` → Enter (fallback `submitDailyConsumptionBatch` if needed) | POST `/api/dishes/daily_consumption/batch` **200** |
| Recorded quantity | **1** (`consumption_ids:[1]`) |

Evidence: `logs/keyboard_submit.json`, case fields in `logs/qa_axtree_keyboard.json`, `logs/tenant_a_consumption.json`.

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
| Tenant B `uat035qab` GET `/api/dishes` | `data: []` before seed, after seed, final (curl) |
| Port | **15195**; avoided **15185**, **15010**, **15155**, **15165**, **15175**, **15130**, **15140**, **15450**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/uat035-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 |
| Cleanup | dish id 1 DEACTIVATED; uvicorn stopped; port 15195 free; reuseaddr bind OK |
| Checklist / ledger | **not** flipped (still `[TODO] DEV_DELIVERED`) |
| Git | no commit / no push |
| UAT | not started |

Evidence: `logs/tenant_b_before.json`, `logs/tenant_b_after_seed.json`, `logs/tenant_b_final.json`, `environment/cleanup.json`.

## Residual risks (honest)

1. Keyboard submit page in the same browser session may inherit `demo_locale=zh-Hant-HK` from the prior Hant case (localStorage shared); submit still 200 with qty 1. Hans AXTree/keyboard-type cases ran before locale switch and are unambiguous.
2. Native `<select>` filter exercised via Puppeteer `page.select` after focus (same pattern as DEV); qty entry is real keyboard typing.
3. Card source files do not include data-center `#exportDynamicFilters`; those selects remain out of scope (DEV note). QA did not claim export-center fixed.
4. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.
5. Checklist still `[TODO] DEV_DELIVERED`; do not flip from this QA alone.

## Scheduler handoff

- UAT-035 **QA PASS** (3/3 AC + negative + Tenant B).
- Please dispatch **independent UAT** next (MANUAL_UI; fresh port ≠ 15195 / ≠ 15185; avoid 15010 / 15155 / 15165 / 15175 / 15130 / 15140 / 15450).
- After UAT PASS: flip `[UAT-035]` to DONE PASS.

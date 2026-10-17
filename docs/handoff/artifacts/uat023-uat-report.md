# UAT-023 Independent UAT Report

> Generated: 2026-10-17 04:41 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation of Hant export preview business names vs CSV
> Card: [UAT-023] 繁体报表预览自动转换业务品名，和CSV原值不一致 — P2 PRODUCT_FAULT
> Prior gates (not reused): `uat023-dev-report.md`, `uat023-qa-report.md` (QA port 15260)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Hans→Hant→Hans：业务名 preview textContent == CSV 原值（含合成长名） | **FAIL** |
| 2. 表头仍翻译；UI chrome / I18N.convert 仍活跃 | **PASS** |
| 3. DB 业务 name 不变 | **PASS** |
| 4. 业务名含 HTML 仍 escape（无 live `<script>`） | **PASS** |
| Negative: 不全局关闭 zh-Hant-HK | **PASS** |

**UAT-023 UAT gate: FAIL.**

Root cause of FAIL (painted DOM, not API JSON): `receipts_itemized_ledger` column **归属部门** is business-identity text but is **not** covered by `isExportBusinessIdentityHeader` (`h === '部门' || h.includes('部门名称')` misses `归属部门`). Under zh-Hant-HK the painted cell OpenCC-converts to `後廚冷鏈中心（超長部門名稱）` while Hans CSV keeps `后厨冷链中心（超长部门名称）`.

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15270** (QA used 15260; forbidden reuse of 15150–15260) |
| DB | `docs/handoff/artifacts/uat023-uat/environment/uat_isolated.db` (fresh) |
| RAG | `docs/handoff/artifacts/uat023-uat/environment/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo X-Role owner; MANUAL_UI does not require production cookie) |
| Fixture | synthetic long Hans SKU / supplier / dept + approved receipt + stock logs (dish create N/A on `db`) |
| Venv | `docs/handoff/artifacts/uat023-uat/environment/venv` → uat003-qa venv link |
| Server pid | 151017 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; real DOM locale switch + export preview paint |

## Commands actually run

1. Self-built uvicorn on `127.0.0.1:15270` with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=0` — `GET /` HTTP 200; `/api/auth/me` shows `auth_enabled=false`
2. Seed long Hans names — `scripts/seed_uat023.py` → `logs/seed.json`
3. Browser MANUAL_UI `scripts/browser_uat023.js` (puppeteer-core + system Chrome): locale Hans→Hant-HK→Hans; export tab; 5 reports with biz rows; painted `#exportPreviewTbody` identity textContent vs Hans CSV; header/chrome convert probes — `logs/browser-uat.txt`, `logs/browser-uat-result.json`, screenshots under `ui/`
4. Static citation print — `logs/static-evidence.txt`
5. Post-run SQLite read — `logs/db-after.json` (SKU/supplier names still exact Hans)
6. Full cleanup: stop pid, free port, `environment/cleanup.json`

Did **not** run: pytest / QA harnesses (those are QA-stage); checklist edit; git write.

Browser check tally: **92 PASS / 3 FAIL** (all 3 FAIL on `归属部门`).

## Criterion 1 — preview biz names == CSV under Hant (FAIL)

Self-checked citations:

- `demo/static/js/export_center.js:893-898` — `isExportBusinessIdentityHeader` covers 品名/名称/说明/备注/配方/建议/供应商/往来/`部门` exact / `部门名称` substring — **not** `归属部门`
- `demo/static/js/export_center.js:927` — `data-i18n-skip` only when `preserveBiz`
- Card AC: 「8种CSV中包含业务名者对比同ID preview textContent与CSV精确相等」

Runtime (browser, port 15270):

- **PASS** painted identity equality for: `食材品名`, `商品品名`, `供应商名称`, `往来供应商/经手`, `备注` / `操作备注` across stocktake / valuation / stock_logs / ledger (15 Hant equality PASS lines)
- **PASS** LONG_NAME exact Hans preserved under Hant on stocktake + ledger (`有机走地鸡胸肉…` present; `有機走地雞胸肉` absent)
- **FAIL** `receipts_itemized_ledger` / `归属部门` row0:
  - painted Hant textContent = `後廚冷鏈中心（超長部門名稱）`
  - Hans CSV cell = `后厨冷链中心（超长部门名称）`
  - `data-i18n-skip` absent on that `<td>`
- Screenshots: `ui/preview-hant-receipts_itemized_ledger.png`, `ui/preview-hans-receipts_itemized_ledger.png`

Honest note: QA live verify compared **API preview JSON** (never OpenCC) to CSV, so it could not see this paint-time divergence. Independent UAT uses painted DOM as the card requires.

## Criterion 2 — headers / chrome still translate (PASS)

Runtime:

- document `lang` / `data-locale` = `zh-Hant-HK` after `?lang=zh-Hant-HK`
- `I18N.convert('导出 CSV')` → `匯出 CSV`; `已入账` → `已入賬`; `供应商名称` → `供應商名稱`
- Per-report: Hant painted thead headers differ from Hans CSV headers; Hant CSV headers localized vs Hans

## Criterion 3 — DB name unchanged (PASS)

- `logs/db-after.json`: SKU id=1 name still exact `有机走地鸡胸肉（去皮去骨特长规格说明超长品名测试ABC）`; supplier still exact long Hans
- No write path exercised on preview/download beyond reads

## Criterion 4 + negative — HTML escape; Hant not disabled (PASS)

- XSS SKU `鸡翅<script>alert(1)</script>` appears in stocktake preview; tbody has no live `<script>` child / raw tag injection
- I18N.convert remains active (not globally disabled)

## Residual risks / next fix hint

1. Minimal fix candidate (dev, not applied here): extend `isExportBusinessIdentityHeader` to treat `归属部门` (and any `*部门` business label that is not pure UI chrome) as identity — e.g. `h.includes('部门')` carefully, or explicit `归属部门` / `销售对象` list aligned with QA identity index.
2. Re-run independent QA + UAT after fix; do not flip checklist on this FAIL.
3. `dish_cost_margin` still had no dish seed path (`db.create_dish` missing) — same coverage gap QA noted; not the cause of this FAIL.
4. Browser automation used headless Chromium via puppeteer-core (not a human GUI tab). Flows exercised real served HTML/JS + OpenCC MutationObserver on painted preview tables.

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 151017) |
| Port 15270 free | yes (`environment/cleanup.json`) |
| Browser contexts | closed; no leftover chrome for 15270 |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat023-uat/environment/cleanup.json` |

No git commit / push. No checklist edit.

## Scheduler signal

- **UAT gate: FAIL**
- Evidence: `docs/handoff/artifacts/uat023-uat-report.md` + `docs/handoff/artifacts/uat023-uat/`
- Please **return to development** to cover `归属部门` (and re-check similar dept/business labels) with `data-i18n-skip`, then re-dispatch independent QA → UAT. Do **not** flip `[UAT-023]` to DONE.

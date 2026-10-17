# UAT-023 Independent QA2 Report (after fix2)

> Generated: 2026-10-17 04:44 Asia/Shanghai (CST)
> Role: Independent re-QA (did NOT perform the development)
> Scope: Re-verify UAT-023 after fix2 — especially painted DOM `归属部门` (prior UAT FAIL)
> Card: [UAT-023] 繁体报表预览自动转换业务品名，和CSV原值不一致 — P2 PRODUCT_FAULT
> Prior: `uat023-dev-fix2-report.md`, `uat023-uat-report.md` (**FAIL** on painted `归属部门`), `uat023-qa-report.md` (PASS on API JSON only — missed paint)
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Hans→Hant→Hans：业务名 painted textContent == CSV 原值（含 `归属部门`） | **PASS** |
| 1b. `receipts_itemized_ledger` / `归属部门` has `data-i18n-skip` under Hant | **PASS** |
| 1c. Prior identity cases still PASS (品名/供应商/备注等) | **PASS** |
| 2. 表头仍翻译；UI chrome / I18N.convert 仍活跃 | **PASS** |
| 3. DB 业务 name 不变 | **PASS** |
| 4. 业务名含 HTML 仍 escape | **PASS** |
| Negative: 不全局关闭 zh-Hant-HK | **PASS** |
| Specialty pytest (5) | **PASS** |
| Frontend harness (22) | **PASS** |
| Live painted browser MANUAL_UI (95 checks) | **PASS** (95/0) |

**UAT-023 QA2 gate: PASS.** Do not treat this as card close; independent UAT not started. Checklist not flipped.

## Prior FAIL addressed

From `uat023-uat-report.md` (port 15270):

- Painted Hant `归属部门` became `後廚冷鏈中心（超長部門名稱）` vs Hans CSV `后厨冷链中心（超长部门名称）`
- `data-i18n-skip` absent (old predicate `h === '部门' \|\| h.includes('部门名称')` missed `归属部门`)

QA2 live paint (port **15280**) after fix2 (`h.includes('部门')`):

| Check | Result |
|---|---|
| `receipts_itemized_ledger/Hant: identity[归属部门] row0 preview==CSV` | PASS — preview=`后厨冷链中心（超长部门名称）` == CSV Hans |
| `receipts_itemized_ledger/Hant: identity[归属部门] row0 has data-i18n-skip` | PASS |
| Header still converts | PASS — painted thead `归属部门` → `歸屬部門` |
| Wrong Hant form absent in body | PASS — sample keeps Hans dept; status badge may convert (`已入账`→`已入賬`) |

Citations:

- Served JS: `export_center.js:899` — `\|\| h.includes('部门') \|\| h === '销售对象'`
- `export_center.js:928` — `data-i18n-skip` when `preserveBiz`
- Extract: `docs/handoff/artifacts/uat023-qa2/logs/guishu-bumen-extract.json`
- Full browser: `logs/browser-qa2-result.json` (alias of `browser-uat-result.json`)

## Commands actually run

1. Static evidence — `logs/static-evidence.txt` (predicate + skipAttr print)
2. `pytest demo/tests/test_uat_023_export_preview_biz_names.py -v` — **5 passed** (`logs/pytest-uat023.txt`)
   - incl. `test_export_identity_header_covers_guishu_bumen`
3. `node --check` export_center / cost_report / i18n — OK
4. `node demo/tests/frontend_uat023_export_preview_harness.js` — **22 PASS** (`logs/frontend-harness.txt`)
   - incl. `identity header 归属部门`, `build marks only 归属部门 cell with data-i18n-skip`, `Hant: 归属部门 cell == CSV Hans`
5. Isolated uvicorn live painted verify (self-built)
   - Port **15280** (avoided 15150–15270; prior UAT used 15270)
   - DB `environment/qa2_isolated.db`, RAG `rag_isolated`, `AUTH_ENABLED=0`
   - Seed: `scripts/seed_uat023.py` → `logs/seed.json`
   - Browser: `scripts/browser_qa2.js` (puppeteer-core + Chrome; Hans→Hant-HK→Hans painted `#exportPreviewTbody` vs Hans CSV)
   - Served JS wiring: `logs/live-served-verify.txt`
   - Result: **pass=true, passCount=95, failCount=0** (`logs/browser-qa2.txt`)

Did **not** run: independent UAT stage; checklist edit; git write.

## Criterion 1 — painted biz names == CSV under Hant (PASS)

Prior identity cases still PASS under Hant paint (examples):

- `供应商名称` row0: `鲜生农产供应商（特长名称说明）` == Hans CSV + `data-i18n-skip`
- `商品品名` / stocktake `食材品名`: LONG_NAME exact Hans preserved; `有機走地雞胸肉` absent
- `归属部门` (the prior FAIL): exact Hans + skipAttr (see table above)

Reports exercised in browser: `inventory_stocktake`, `receipts_itemized_ledger`, `inventory_valuation`, `spend_summary`, `inventory_stock_logs`.

## Criterion 2 — headers / chrome still translate (PASS)

- document `lang` / `data-locale` = `zh-Hant-HK`
- `I18N.convert('导出 CSV')` → `匯出 CSV`; `已入账` → `已入賬`; `供应商名称` → `供應商名稱`
- Ledger Hant painted headers differ from Hans CSV (e.g. `归属部门`→`歸屬部門`)

## Criterion 3 — DB unchanged (PASS)

`logs/db-after.json`:

- SKU id=1 name still exact `有机走地鸡胸肉（去皮去骨特长规格说明超长品名测试ABC）`
- Department still exact `后厨冷链中心（超长部门名称）`
- Supplier still exact long Hans

## Criterion 4 + negative (PASS)

- XSS SKU appears escaped in stocktake preview DOM (no live `<script>` child)
- I18N.convert remains active (not globally disabled)

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | **15280** (fresh; avoided 15150–15270) |
| DB | `docs/handoff/artifacts/uat023-qa2/environment/qa2_isolated.db` (+ pytest DB) |
| AUTH_ENABLED | 0 |
| uvicorn | pid 156464 started then stopped; port free; reuseaddr bind OK |
| Browser | headless Chrome via puppeteer-core; contexts closed |
| Artifacts | `docs/handoff/artifacts/uat023-qa2/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/uat023-qa2/environment/cleanup.json` |

## Coverage note (honest)

- `dish_cost_margin` still had no dish seed path (`db.create_dish` missing) — same gap as prior QA/UAT; not related to the fix2 FAIL.
- QA1 compared API preview JSON only and missed paint-time OpenCC; QA2 deliberately used painted DOM (same class of probe as independent UAT) for the regression that previously failed.

## Scheduler handoff

- UAT-023 **QA2 PASS** (fix2 regression closed on painted `归属部门`).
- Please dispatch **independent UAT** next (fresh port ≠ 15280; do not reuse 15150–15280).
- Only after UAT PASS flip `[UAT-023]` checklist to `[DONE] PASS`.

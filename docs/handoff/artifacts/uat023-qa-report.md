# UAT-023 Independent QA Report

> Generated: 2026-10-17 04:37 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-023 acceptance only (繁体报表预览业务品名与 CSV 原值一致)
> Card: [UAT-023] 繁体报表预览自动转换业务品名，和CSV原值不一致 — P2 PRODUCT_FAULT
> UAT: NOT started (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Hans→Hant→Hans：业务品名 preview textContent == CSV 原值（identity cells `data-i18n-skip`） | **PASS** |
| 2. 表头仍翻译；UI chrome / 状态格仍 OpenCC | **PASS** |
| 3. DB 业务 name 不变（preview/download 只读） | **PASS** |
| 4. 业务名含 HTML 仍 escape | **PASS** |
| Negative: 不全局关闭 zh-Hant-HK | **PASS** |
| Specialty pytest (4) | **PASS** |
| Frontend harness (8) + QA roundtrip (19) | **PASS** |
| Live 8-report preview vs CSV (sanitize-aware) | **PASS** (83/83) |

**UAT-023 QA gate: PASS.** Do not treat this as card close; independent UAT (real browser painted DOM under locale switch + 8 CSV MANUAL_UI paint) not started.

## Commands run (actually executed)

1. Static evidence scan (export_center / cost_report / i18n / api_export)
   - Log: `docs/handoff/artifacts/uat023-qa/logs/static-evidence.txt`
2. `pytest demo/tests/test_uat_023_export_preview_biz_names.py -v --tb=short`
   - Venv: `docs/handoff/artifacts/uat003-qa/environment/venv` (linked)
   - Fresh `DB_PATH=.../uat023-qa/environment/qa_pytest.db`
   - Log: `docs/handoff/artifacts/uat023-qa/logs/pytest-uat023.txt`
   - Result: **4 passed**
3. Frontend syntax + specialty harness
   - `node --check` export_center.js / cost_report.js / i18n.js — OK
   - `node demo/tests/frontend_uat023_export_preview_harness.js` — **8 PASS**
   - Log: `docs/handoff/artifacts/uat023-qa/logs/node-check.txt`, `frontend-harness.txt`
4. QA Hans→Hant→Hans roundtrip harness (loads real I18N + extracted `buildExportRowsHtml`)
   - Script: `docs/handoff/artifacts/uat023-qa/scripts/frontend_uat023_hans_hant_roundtrip.js`
   - Log: `docs/handoff/artifacts/uat023-qa/logs/frontend-roundtrip.txt`
   - Result: **PASS 19 FAIL 0** (name preserved == CSV; header/chrome/status convert; HTML escape; Hant not disabled)
5. Isolated uvicorn live verify (self-built; fresh port/DB)
   - Port **15260** (avoided 15150-15250), DB `qa_isolated_smoke.db`, AUTH_ENABLED=0
   - Seeded synthetic long Hans SKU/supplier/dept + receipt + stock logs
   - All 8 report types: preview headers/rows vs CSV Hans; identity cols vs Hant CSV exact; Hant headers == `localize()`
   - Script: `docs/handoff/artifacts/uat023-qa/scripts/live_preview_csv_verify.py`
   - Log: `docs/handoff/artifacts/uat023-qa/logs/live-preview-csv.txt` / `.json`
   - Result: **83 passed / 0 failed**
   - Served JS wiring: `docs/handoff/artifacts/uat023-qa/logs/live-served-verify.txt`

## Criterion 1 evidence — preview biz names == CSV under Hant

Self-checked citations:

- `demo/static/js/export_center.js:893` — `function isExportBusinessIdentityHeader(h)`
- `demo/static/js/export_center.js:902` — `function buildExportRowsHtml(rows, headers)`
- `demo/static/js/export_center.js:927` — `const skipAttr = preserveBiz ? ' data-i18n-skip' : '';`
- `demo/app/api_export.py:1442-1448` — `preview_report` UAT-023 契约：行内业务名不做简繁；前端须 skip，不得全局关 Hant
- `demo/static/js/i18n/i18n.js:292-297` — skip 机制；注释明确勿全局关掉转换

Runtime:

- Specialty harness: long Hans name preserved under Hant; status/chrome convert
- QA roundtrip: Hans→Hant→Hans name stays `有机走地鸡胸肉（去皮去骨特长规格说明超长品名测试ABC）` and equals CSV cell
- Live :15260 — for every report with identity columns and rows, each biz cell `preview == Hant CSV` (stocktake / valuation / price_anomaly / consumption / spend_summary / stock_logs / ledger)
- `dish_cost_margin` had 0 rows in this fixture (no dish seed API in `db`); headers/localize still verified; empty-ok recorded (not claimed as painted dish-name coverage)

Real painted browser locale toggle + click-through preview table is reserved for independent UAT.

## Criterion 2 evidence — headers / chrome still translate

Citations:

- thead path in `renderExportPreviewUI` has `<th` and **no** `data-i18n-skip` (pytest asserts)
- `download_report` localizes headers only: `headers = [localize(h, req.lang) for h in ...]` (`api_export.py:1489`)

Runtime:

- Harness: `食材品名` / `供应商名称` headers convert; `导出 CSV` → `匯出 CSV`; `已入账` → `已入賬`
- Live: for all 8 reports, Hant CSV headers == `localize(Hans header, zh-Hant-HK)`

## Criterion 3 evidence — DB name unchanged

Runtime:

- Seeded SKU name read before exports == after all preview/download calls
- Supplier name still exact long Hans string in SQLite
- No write path in preview/download (read-only contract)

## Criterion 4 + negative — HTML escape; Hant not globally disabled

Citations / runtime:

- `buildExportRowsHtml` uses `esc(cellText)` before HTML; roundtrip asserts `&lt;script&gt;` present and raw `<script>` absent in XSS name cell
- cost_report: `w2Escape(dep.name)` / `suppName` / `raw_name` inside skip spans (`cost_report.js:142,272,285`)
- `DISABLE_HANT` / `convertDisabled` absent in i18n.js (pytest + served JS)
- Probe after skip path: `导出 CSV` still converts under zh-Hant-HK

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | 15260 (fresh; not 15150-15250) |
| DB | `docs/handoff/artifacts/uat023-qa/environment/qa_isolated_smoke.db` (+ pytest DB) |
| AUTH_ENABLED | 0 (export preview/download smoke via X-Role) |
| uvicorn | started then stopped; port free after cleanup |
| Artifacts | `docs/handoff/artifacts/uat023-qa/` (logs, scripts, environment) |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |

## Coverage note (honest)

- Live fixture covered business-name rows for 7/8 report IDs; `dish_cost_margin` empty (dish create helper not on `db` module). Static + harness still cover identity-header skip for `餐品名称` / `诊断分析建议`. Independent UAT should seed a dish sale path if painted dish-name preview is required.

## Scheduler handoff

- UAT-023 **QA PASS**.
- Please dispatch **independent UAT** next (real browser: locale Hans→Hant→HK→Hans; open export preview for reports with long biz names; assert `#exportPreviewTbody` identity cell textContent === CSV cell; headers/chrome converted; DB unchanged).
- Only after UAT PASS flip `[UAT-023]` checklist to `[DONE] PASS`.

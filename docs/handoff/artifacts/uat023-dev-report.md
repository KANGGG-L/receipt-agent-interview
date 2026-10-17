# UAT-023 Development Report

> Generated: 2026-10-17 04:35 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT-023] 繁体报表预览自动转换业务品名，和CSV原值不一致 — P2 PRODUCT_FAULT
> Vendor aliases in fixtures: n/a (i18n / export preview)

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Root cause identified | PASS |
| Preview business names not OpenCC-converted | PASS (data-i18n-skip on identity cells) |
| Headers / UI chrome still translate | PASS |
| HTML escape on names retained | PASS (`esc` / `w2Escape`) |
| Hant translation not globally disabled | PASS |
| Specialty pytest (4) | PASS |
| Frontend harness (8) | PASS |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.

## Root cause (printed citations)

1. CSV download correctly keeps row business data as source (Hans) and only localizes headers/filename:
   - `demo/app/api_export.py` `download_report` — `headers = [localize(h, req.lang) ...]`；rows via `_sanitize_csv_cell` only.
   - Comment on `ExportReq.lang`: row data is not OpenCC-converted.
2. Preview returns the same unconverted Hans rows/headers, but the zh-Hant-HK DOM translator rewrites every CJK text node, including product / supplier cells in `#exportPreviewTbody`.
3. `buildExportRowsHtml` (`demo/static/js/export_center.js`) escaped cells but did **not** mark business-identity `<td>` with `data-i18n-skip`, so preview `textContent` diverged from CSV cell values under Hant.
4. Cost report tables had the same gap for department name / supplier / `raw_name`.

## Fix

### Export preview — `demo/static/js/export_center.js`

- Added `isExportBusinessIdentityHeader(h)` covering 品名 / 名称 / 说明 / 备注 / 配方 / 建议 / 供应商 / 往来 / 部门.
- `buildExportRowsHtml` sets `data-i18n-skip` on matching `<td>` (virtual scroll + print share this builder).
- `<th>` path unchanged — headers still OpenCC/localize.
- Cell values still pass through `esc()` before HTML (XSS / injection boundary).

### Cost report — `demo/static/js/cost_report.js`

- Department name: `<span data-i18n-skip>` around `dep.name` (「已停用」 chrome stays outside skip).
- Supplier group header: skip only the name span.
- Drilldown `raw_name` cell: `data-i18n-skip` on the `<td>`.

### i18n policy — `demo/static/js/i18n/i18n.js` (+ `docs/i18n.md`)

- Documented that business-identity display must use `data-i18n-skip`; UI chrome / headers keep conversion; do **not** globally disable Hant.
- `isSkipElement` comment reinforces UAT-023 boundary (mechanism unchanged).

### API contract — `demo/app/api_export.py`

- `preview_report` docstring: headers/rows are Hans source; rows never OpenCC; front-end must skip identity columns; DB names read-only.

## Tests

```text
node --check demo/static/js/export_center.js
node --check demo/static/js/cost_report.js
node --check demo/static/js/i18n/i18n.js
# exit 0

node demo/tests/frontend_uat023_export_preview_harness.js
# 8 PASS (name preserved, header/chrome converted, HTML escape, wiring)

.../venv/bin/pytest demo/tests/test_uat_023_export_preview_biz_names.py -v
# 4 passed
```

MANUAL_UI (合成长中文名；Hans→Hant→Hans；8 种含业务名 CSV 与同 ID preview textContent 精确相等；DB 名不变) is **reserved for independent QA/UAT**.

## Acceptance mapping (dev evidence only)

| Criterion | Dev evidence |
|---|---|
| Preview business names == CSV originals under Hant | Identity cells `data-i18n-skip`; harness preserves long Hans name; API rows unconverted |
| Headers still translate | `<th>` not skipped; harness asserts `I18N.convert` on 食材品名 / 供应商名称 |
| DB business name unchanged | preview/download read-only; no write path touched |
| Escape HTML in names | `esc` / harness `&lt;script&gt;` |
| Do not globally disable Hant | i18n skip is per-subtree; converter + MutationObserver intact |

## Negative boundary

- Did **not** disable zh-Hant-HK conversion globally.
- Did **not** OpenCC-convert CSV row data on the server.
- Status / chrome cells (e.g. 已入账, 导出 CSV) still convert.

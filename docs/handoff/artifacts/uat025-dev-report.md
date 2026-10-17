# UAT-025 Development Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [UAT-025] 繁体洞察请求未传当前语言 — P2 PRODUCT_FAULT
> Vendors in fixtures / prose: none (lang wiring only)

## Summary

`loadAiLeanInsights()` called `GET /api/ai-insights` with no `lang` query.
The API already accepted `lang` and wrote `data.review_locale =
normalize_locale(lang)`, so Traditional UI never reached the backend as
`zh-Hant-HK` (or accepted equivalents). UI OpenCC conversion alone must not
count as model/locale pass.

Fix (dev only): append `?lang=` from `I18N.getLocale()` on the insights URL;
document the UAT-025 contract on `ai_insights`. No QA/UAT in this stage.

## Before

- `demo/static/js/inventory_board.js` `apiFetch('/api/ai-insights')` — no locale.
- Backend recorded empty `review_locale` when `lang` omitted.
- Existing supportive API test (`test_review_i18n_10.py`) already proved
  `lang=zh-HK` -> `review_locale=zh-Hant-HK` when the query is present.

## After

### JS (`demo/static/js/inventory_board.js` `loadAiLeanInsights`)

- Read `(window.I18N && I18N.getLocale) ? I18N.getLocale() : ''`
- Build `/api/ai-insights?lang=` + `encodeURIComponent(lang)` when locale known
- Fallback to bare path only if locale helper unavailable (should not happen
  after i18n boot)

### API (`demo/app/api_admin.py` `GET /api/ai-insights`)

- Behavior unchanged: `normalize_locale(lang)` -> `data.review_locale`
- Still does **not** call the review model here
- Docstring adds UAT-025 client contract (`zh-Hant-HK` / `zh-Hans` / accepted
  equivalents)

## Acceptance criteria (dev self-check mapping)

| Criterion | Dev evidence |
|---|---|
| Hant GET carries `lang=zh-Hant-HK` (or accepted equiv) | JS builds `?lang=` from `getLocale()`; API maps `zh-HK`/`zh-Hant*` -> `zh-Hant-HK` (existing normalize + I18N-10) |
| Hans corresponding `lang` | same path with `zh-Hans` |
| Request + `review_locale` readable | API returns `data.review_locale` |
| Supportive prompt capture unit-only | `build_review_prompt` locale line covered by `test_review_i18n_10.py`; not claimed as live model E2E |
| Real model output deferred | Explicit non-claim |

Negative: UI繁转 alone is not treated as language pass; no speculative model call added that could timeout.

## Tests run (dev self-check; not independent QA)

| Command | Result |
|---|---|
| `node --check demo/static/js/inventory_board.js` | exit 0 |
| `node demo/tests/frontend_uat025_ai_insights_lang_harness.js` | 11 PASS / 0 FAIL |
| `python3` inline runner static asserts in `test_uat_025_ai_insights_lang.py` | 2/2 PASS |
| API TestClient cases in same file | skipped here (no fastapi in box env); covered by existing `test_review_i18n_10.py` + file asserts when deps present |
| `python3 -m py_compile demo/app/api_admin.py` | exit 0 |

MANUAL_UI (live Hant/Hans network capture of GET + response `review_locale`)
left for independent QA/UAT.

## Files touched

- `demo/static/js/inventory_board.js`
- `demo/app/api_admin.py`
- `demo/tests/test_uat_025_ai_insights_lang.py` (new)
- `demo/tests/frontend_uat025_ai_insights_lang_harness.js` (new)
- `docs/handoff/artifacts/uat025-dev-report.md` (this file)
- `docs/handoff/01-acceptance-checklist.md` ([UAT-024] DONE PASS; [UAT-025] DEV_DELIVERED)
- `docs/handoff/02-coordinator-ledger.md` (UAT-024 closed; UAT-025 DEV; next = UAT-025 until QA)

## Explicit non-claims

- Independent QA not run
- Independent UAT not run
- Card must not be marked DONE PASS until QA + UAT gates pass
- No git commit / push
- Real model insight text language not proven in this stage
- Cross-tenant / role gates unchanged (owner-only endpoint behavior same)

## Next gate

Independent QA on fresh isolate port: switch UI to Hant and Hans; capture real
`GET /api/ai-insights?lang=...`; assert response `review_locale`; confirm UI
OpenCC alone is not used as the pass criterion.

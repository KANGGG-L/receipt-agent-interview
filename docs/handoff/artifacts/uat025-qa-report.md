# UAT-025 Independent QA Report

> Generated: 2026-10-17 04:58 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-025 acceptance only (Hant/Hans insight requests carry current lang + review_locale)
> Card: [UAT-025] 繁体洞察请求未传当前语言 — P2 PRODUCT_FAULT
> Prior: `uat025-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 真实 Hant 触发 GET `/api/ai-insights` 携 `lang=zh-Hant-HK`（或 API 已接受等价） | **PASS** |
| 1b. Hans 相应 `lang=zh-Hans` | **PASS** |
| 2. 请求与 `review_locale` 实读 | **PASS** |
| 3. 支持性 prompt 捕获仅作单测（不冒充 live model E2E） | **PASS** (deferred / non-claim) |
| 4. 真实模型输出需后续依赖成功证据 | **PASS** (explicit non-claim; endpoint does not call model) |
| Negative: 不能只把 UI 繁转当模型语言通过 | **PASS** (gate = request `lang` + response `review_locale`) |
| Specialty pytest (3) | **PASS** |
| Frontend harness (11) | **PASS** |
| Live browser MANUAL_UI | **PASS** (18/0) |

**UAT-025 QA gate: PASS.** Do not treat this as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Static evidence — `docs/handoff/artifacts/uat025-qa/logs/static-evidence.txt`
2. `node --check demo/static/js/inventory_board.js` — OK (`logs/node-check.txt`)
3. `node demo/tests/frontend_uat025_ai_insights_lang_harness.js` — **11 PASS / 0 FAIL** (`logs/frontend-harness.txt`)
4. `pytest demo/tests/test_uat_025_ai_insights_lang.py -v` — **3 passed** (`logs/pytest-uat025.txt`)
   - includes TestClient Hant / `zh-HK` / Hans / empty `review_locale` mapping
5. Isolated uvicorn live MANUAL_UI (self-built)
   - Port **15350** (fresh; avoided 15150–15300)
   - DB `environment/qa_isolated.db`, RAG `rag_isolated`, `AUTH_ENABLED=0`
   - Browser: `scripts/browser_qa025.js` (puppeteer-core + Chrome; Hant + Hans locales)
   - Result: **pass=true, passCount=18, failCount=0** (`logs/browser-qa-result.json`, `logs/browser-qa.txt`)
6. Live served verify — `logs/live-served-verify.txt` (JS markers + curl `review_locale` + staff 403)

Did **not** run: independent UAT stage; checklist edit; git write; live review-model call (endpoint does not invoke `run_review`).

Model creds (`/home/box/.config/receipt-agent/openai_rec.env`) were **not** sourced — not required for this card’s API path.

## Criterion 1 / 1b — Hant & Hans GET carry lang (PASS)

Live owner inventory path after `/?lang=...`:

| Locale URL | Captured request | `lang` query | `data.review_locale` |
|---|---|---|---|
| `zh-Hant-HK` | `GET /api/ai-insights?lang=zh-Hant-HK` | `zh-Hant-HK` | `zh-Hant-HK` |
| `zh-Hans` | `GET /api/ai-insights?lang=zh-Hans` | `zh-Hans` | `zh-Hans` |

Also re-fired via `I18N.setLocale` + `loadAiLeanInsights()` — same `lang` / `review_locale` (PASS).

Document / I18N state:

- Hant: `data-locale=zh-Hant-HK`, `I18N.getLocale()=zh-Hant-HK`
- Hans: `data-locale=zh-Hans`, `I18N.getLocale()=zh-Hans`

Screenshots: `ui/hant-inventory.png`, `ui/hans-inventory.png`.

## Criterion 2 — request + review_locale readable (PASS)

- Network capture of request URL + JSON body `data.review_locale` in browser QA.
- Curl / API aliases (same isolate port):

| Input `lang` | `review_locale` |
|---|---|
| `zh-Hant-HK` | `zh-Hant-HK` |
| `zh-HK` | `zh-Hant-HK` |
| `zh-Hant` | `zh-Hant-HK` |
| `zh-Hans` | `zh-Hans` |
| (omit) | `""` |

Pytest TestClient cases in `test_uat_025_ai_insights_lang.py` agree (3/3).

## Criterion 3 / 4 — supportive prompt / real model (PASS as non-claims)

- Endpoint docstring + code: records `normalize_locale(lang)` only; **does not** call `run_review`.
- Harness asserts no `run_review(` in `ai_insights` block.
- Real model insight text language **not** proven here (matches card: deferred to later evidence).

## Negative — UI OpenCC alone is not a language pass (PASS)

- Under Hant, `I18N.convert('价格异常')` → `價格異常` (OpenCC active).
- Gate still required `lang` query + `review_locale` match; convert presence alone was **not** accepted as pass.
- Staff role: `GET /api/ai-insights?lang=zh-Hant-HK` with `X-Role: staff` → **403** (`logs/live-served-verify.txt`).

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | **15350** (fresh; avoided 15150–15300) |
| DB | `docs/handoff/artifacts/uat025-qa/environment/qa_isolated.db` (+ pytest DB) |
| AUTH_ENABLED | 0 |
| uvicorn | pid 8756 started then stopped; port free; reuseaddr bind OK |
| Browser | headless Chrome via puppeteer-core; contexts closed |
| Artifacts | `docs/handoff/artifacts/uat025-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/uat025-qa/environment/cleanup.json` |

## Scheduler handoff

- UAT-025 **QA PASS** (18/18 live checks + specialty gates).
- Please dispatch **independent UAT** next (fresh port ≠ 15350; do not reuse 15150–15350).

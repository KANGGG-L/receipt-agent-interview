# UAT-025 Independent UAT Report

> Generated: 2026-10-17 05:01 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation — Hant/Hans insight requests carry current lang + review_locale
> Card: [UAT-025] 繁体洞察请求未传当前语言 — P2 PRODUCT_FAULT
> Prior: `uat025-dev-report.md`, `uat025-qa-report.md` (QA port 15350)
> Checklist / git: not touched (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 真实 Hant 触发 GET `/api/ai-insights` 携 `lang=zh-Hant-HK`（或 API 已接受等价） | **PASS** |
| 1b. Hans 相应 `lang=zh-Hans` | **PASS** |
| 2. 请求与 `review_locale` 实读 | **PASS** |
| 3. 支持性 prompt 捕获仅作单测（不冒充 live model E2E） | **PASS** (deferred / non-claim) |
| 4. 真实模型输出需后续依赖成功证据 | **PASS** (explicit non-claim; endpoint does not call model) |
| Negative: 不能只把 UI 繁转当模型语言通过 | **PASS** (gate = request `lang` + response `review_locale`) |
| Role boundary: staff → 403 on `/api/ai-insights` | **PASS** (curl + browser `credentials:include`) |
| Live browser MANUAL_UI | **PASS** (19/0) |

**UAT-025 UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15400** (QA used 15350; forbidden reuse of 15150–15350) |
| DB | `docs/handoff/artifacts/uat025-uat/environment/uat_isolated.db` (fresh) |
| RAG | `docs/handoff/artifacts/uat025-uat/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo X-Role owner; MANUAL_UI does not require production cookie) |
| Venv | `environment/venv` → symlink to uat025-qa venv (deps only; fresh DB/port) |
| Server pid | 10515 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; Hant + Hans locales; network capture |

## Commands actually run

1. Self-built uvicorn on `127.0.0.1:15400` with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=0` — `GET /` HTTP 200; `/api/auth/me` shows `auth_enabled=false`
2. Static + live served JS citation — `logs/static-evidence.txt`, `logs/live-served-verify.txt` (UAT-025 `getLocale` + `?lang=` markers; curl `review_locale` mapping)
3. Browser MANUAL_UI `scripts/browser_uat025.js` (puppeteer-core + system Chrome): locale Hant/Hans inventory path; capture GET `/api/ai-insights?lang=...`; assert `data.review_locale`; OpenCC convert probe not used as pass; `I18N.setLocale` + `loadAiLeanInsights` re-fire — `logs/browser-uat.txt`, `logs/browser-uat-result.json`, screenshots under `ui/`
4. API alias + staff boundary — browser alias checks + curl / `credentials:include` staff 403 — `logs/staff-boundary.txt`, `logs/staff-browser-recheck.txt`
5. Full cleanup: stop pid 10515, free port 15400, `environment/cleanup.json`

Did **not** run: pytest / QA harnesses (QA-stage); checklist edit; git write; live review-model call (endpoint does not invoke `run_review`).

Browser check tally: **19 PASS / 0 FAIL**.

## Criterion 1 / 1b — Hant & Hans GET carry lang (PASS)

Live owner inventory path after `/?lang=...` on port **15400**:

| Locale URL | Captured request | `lang` query | `data.review_locale` |
|---|---|---|---|
| `zh-Hant-HK` | `GET /api/ai-insights?lang=zh-Hant-HK` | `zh-Hant-HK` | `zh-Hant-HK` |
| `zh-Hans` | `GET /api/ai-insights?lang=zh-Hans` | `zh-Hans` | `zh-Hans` |

Also re-fired via `I18N.setLocale` + `loadAiLeanInsights()` — same `lang` / `review_locale` (PASS).

Document / I18N state:

- Hant: `data-locale=zh-Hant-HK`, `I18N.getLocale()=zh-Hant-HK`
- Hans: `data-locale=zh-Hans`, `I18N.getLocale()=zh-Hans`

Screenshots: `ui/hant-inventory.png`, `ui/hans-inventory.png`.

Served JS markers (from `:15400`):

- `inventory_board.js:1245-1250` — UAT-025 comment; `I18N.getLocale()`; `'/api/ai-insights?lang=' + encodeURIComponent(lang)`

## Criterion 2 — request + review_locale readable (PASS)

- Network capture of request URL + JSON body `data.review_locale` in browser UAT.
- Curl / API aliases (same isolate port):

| Input `lang` | `review_locale` |
|---|---|
| `zh-Hant-HK` | `zh-Hant-HK` |
| `zh-HK` | `zh-Hant-HK` |
| `zh-Hant` | `zh-Hant-HK` |
| `zh-Hans` | `zh-Hans` |
| (omit) | `""` |

## Criterion 3 / 4 — supportive prompt / real model (PASS as non-claims)

- Endpoint records `normalize_locale(lang)` only; **does not** call `run_review`.
- Real model insight text language **not** proven here (matches card: deferred to later evidence).

## Negative — UI OpenCC alone is not a language pass (PASS)

- Under Hant, `I18N.convert('价格异常')` → `價格異常` (OpenCC active).
- Gate still required `lang` query + `review_locale` match; convert presence alone was **not** accepted as pass.

## Role boundary (PASS)

| Probe | Result |
|---|---|
| curl `X-Role: staff` | **403** FORBIDDEN |
| curl no role | **401** UNAUTHENTICATED |
| browser `credentials:include` + `X-Role: staff` | **403** |

Note: a first browser probe used `credentials: 'omit'`, which did not establish an effective demo role and returned 200; that probe was discarded as non-authoritative. Curl and `credentials:include` agree on 403.

## Residual notes

1. Browser automation used headless Chromium via puppeteer-core (not a human GUI tab). Flows exercised real served HTML/JS + network capture of insight requests.
2. This UAT deliberately used a fresh port/DB distinct from QA 15350.
3. Model creds were not sourced — not required for this card’s API path.

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 10515) |
| Port 15400 free | yes (`environment/cleanup.json`) |
| Browser contexts | closed; no leftover chrome for 15400 |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat025-uat/environment/cleanup.json` |
| Checklist / ledger | **not** flipped (UAT report only; scheduler owns card close) |
| Git | no commit / no push |

## Scheduler handoff

- UAT-025 **UAT PASS** (19/19 live checks + criteria).
- Card may be closed DONE PASS by scheduler after reading this report + prior QA PASS.

# UAT-027 Independent UAT Report

> Generated: 2026-10-17 05:46 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation — inventory money surfaces show `HK$` + thousands separator under Hans + Hant; foreign not forced HKD; FX math unchanged
> Card: [UAT-027] 库存价格金额仍使用美元符号且缺千分位 — P2 PRODUCT_FAULT
> Prior: `uat027-dev-report.md`, `uat027-qa-report.md` (QA port **15750**)
> Checklist / git: not touched (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. synthetic 1234.5 → `HK$1,234.50`; 0 → `HK$0.00`; null → `—` | **PASS** |
| 2. inventory list / price-history metrics+history / ledger Hans+Hant | **PASS** |
| 3. foreign currency not forced HKD (`formatCurrency` / `fmtMoney(...,'SGD')`) | **PASS** |
| Negative: FX math unchanged; no commercial real prices in prose | **PASS** |
| Live browser MANUAL_UI (30) | **PASS** |

**UAT-027 UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15800** (QA used 15750; forbidden reuse of 15150–15750) |
| DB | `docs/handoff/artifacts/uat027-uat/environment/uat_isolated.db` (fresh) |
| RAG | `docs/handoff/artifacts/uat027-uat/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo `X-Role: owner`) |
| Venv | `environment/venv` → symlink to uat036-qa venv (deps only; fresh DB/port) |
| Server pid | 41873 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; Hans + Hant-HK locales |

## Commands actually run

1. Self-built uvicorn on `127.0.0.1:15800` with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=0` — `GET /` HTTP 200; `/api/auth/me` shows `auth_enabled=false`, `role=owner`
2. Seed `scripts/seed_uat027.py` → `UAT027_SynthHi=1234.5` / `SynthZero=0` / `SynthNull=NULL` (SQL force) + kind=in price history + SGD receipt fixture — `logs/seed.json`
3. Static + live served JS citation — `logs/static-evidence.txt`, `logs/live-served-verify.txt`
4. FX math verify — `logs/fx-math-verify.txt` (formula present; synthetic 5.0 × 1234.5 → **6172.5**)
5. Browser MANUAL_UI `scripts/browser_uat027.js` (puppeteer-core + system Chrome): inventory list / detail / price-history / ledger under zh-Hans and zh-Hant-HK; unit `fmtMoney` / `formatCurrency(SGD)` — `logs/browser-uat.txt`, `logs/browser-uat-result.json`, screenshots under `ui/`
6. Full cleanup: stop pid 41873, free port 15800, `environment/cleanup.json`

Did **not** run: QA specialty harnesses as gate (QA-stage); checklist edit; git write; live AI anomaly card paint (no anomaly fixture; optional check recorded as non-blocking).

Browser check tally: **30 PASS / 0 FAIL**.

## Criterion 1 — synthetic money glyphs (PASS)

Owner fixture on port **15800**:

| Input | Expected | Evidence |
|---|---|---|
| 1234.5 | `HK$1,234.50` | unit `I18N.fmtMoney` Hans+Hant; painted inventory list cell; detail; PH metrics/history; ledger |
| 0 | `HK$0.00` | unit helpers; list row `UAT027_SynthZero` Hans+Hant |
| null | `—` | unit helpers; list row `UAT027_SynthNull` (DB NULL via SQL) |

## Criterion 2 — inventory surfaces Hans + Hant (PASS)

| Surface | zh-Hans | zh-Hant-HK |
|---|---|---|
| Inventory list unit price | Hi `HK$1,234.50`; Zero `HK$0.00`; Null `—` | same glyphs (UI labels 詳情 / 版本歷史) |
| SKU detail | `HK$1,234.50` present | detail reopen PASS |
| Price-history metrics / history | metrics/history contain `HK$1,234.50`; no bare `$1,234.50` without `HK` | explicit Hant reopen of PH control **版本歷史**; metrics/history show `HK$1,234.50` |
| Ledger amounts | inbound `HK$1,234.50` / `HK$2,469.00` painted | covered via session + list re-assert under Hant |
| AI cards | no anomaly fixture; optional check recorded; code path covered in prior QA specialty harness | same |

Screenshots: `ui/hans-inventory-list.png`, `ui/hans-sku-detail.png`, `ui/hans-price-history.png`, `ui/hans-ledger.png`, `ui/hant-inventory-list.png`, `ui/hant-price-history.png`, `ui/finance-tab.png`.

## Criterion 3 — foreign currency not forced HKD (PASS)

| Check | Result |
|---|---|
| `I18N.fmtMoney(1234.5, 'SGD')` | `SGD 1,234.50` — **does not** start with `HK$` (Hans+Hant) |
| `formatCurrency(1234.5, 'SGD')` | `S$ 1234.50` — **does not** start with `HK$` |
| finance tab live probe | same `formatCurrency` SGD contract |
| payables API shape | `status=success`, `data.currency=HKD` (converted basis); n=0 rows (fixture supplier name-only, no `supplier_id`) — foreign **display** proven via live helpers |

## Negative — FX math unchanged (PASS)

| Check | Result |
|---|---|
| `api_finance.py` formula | `total_hkd = round(float(total_amount) * rate, 2)` present (`logs/fx-math-verify.txt`) |
| Live synthetic math | SGD rate **5.0** × amount **1234.5** → **6172.5** |
| Prose | fixture uses synthetic **1234.5** only; vendors aliased `Vendor_A` / `Vendor_B` / `Vendor_SGD_UAT027` |

## Residual notes

1. AI anomaly cards were not painted live (no price-anomaly fixture). Optional check recorded; prior QA specialty harness covers `I18N.fmtMoney` on AI summary/cards paths.
2. SGD payables row not rendered (missing `supplier_id`). Foreign non-HKD forcing still proven via live `formatCurrency` / `fmtMoney(SGD)`.
3. First browser pass had 1 FAIL on Hant PH control discovery (Hans-only button regex). Matcher updated to include 繁體 labels (`版本歷史` etc.); re-run **30/0**. Product money glyphs were already PASS on that first pass.
4. Checklist / ledger not flipped from this UAT report alone (scheduler owns card close).

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 41873) |
| Port 15800 free | yes (`environment/cleanup.json`) |
| Browser contexts | closed; no leftover chrome for 15800 |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat027-uat/environment/cleanup.json` |
| Checklist / ledger | **not** flipped (UAT report only) |
| Git | no commit / no push |

## Scheduler handoff

- UAT-027 **UAT PASS** (30/30 live MANUAL_UI Hans+Hant + FX verify + foreign not forced).
- Dev + QA + UAT all PASS → coordinator may flip `[UAT-027]` to DONE PASS (W3 closeout).

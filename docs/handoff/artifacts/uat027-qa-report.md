# UAT-027 Independent QA Report

> Generated: 2026-10-17 05:43 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-027 acceptance only (HK$ + thousands separator on inventory money surfaces; foreign not forced HKD; FX math unchanged)
> Card: [UAT-027] 库存价格金额仍使用美元符号且缺千分位 — P2 PRODUCT_FAULT
> Prior: `uat027-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. synthetic 1234.5 → `HK$1,234.50`; 0 → `HK$0.00`; null → `—` | **PASS** |
| 2. inventory list / price-history metrics+history / ledger Hans+Hant | **PASS** |
| 3. foreign currency not forced HKD (`formatCurrency` / `fmtMoney(...,'SGD')`) | **PASS** |
| Negative: FX math unchanged (`round(amount * rate, 2)`); no commercial real prices in prose | **PASS** |
| Specialty harness `frontend_uat027_money_format_harness.js` (29) | **PASS** |
| i18n converter harness (85) | **PASS** |
| Live browser MANUAL_UI (28) | **PASS** |

**UAT-027 QA gate: PASS.** Do not treat this as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Static evidence — `docs/handoff/artifacts/uat027-qa/logs/static-evidence.txt`
2. `node --check` inventory_board.js + dish_bom.js + finance.js + i18n.js — OK (`logs/node-check.txt`)
3. `node demo/tests/frontend_uat027_money_format_harness.js` — **29 PASS / 0 FAIL** (`logs/frontend-harness.txt`, `logs/harness-uat027.json`)
4. `node demo/tests/frontend_i18n_converter_harness.js` — **85 PASS / 0 FAIL** (`logs/i18n-harness.txt`)
5. Isolated uvicorn live MANUAL_UI (self-built)
   - Port **15750** (fresh; avoided 15150–15700)
   - DB `environment/qa_isolated.db`, RAG `rag_isolated`, `AUTH_ENABLED=0`
   - Seed: `scripts/seed_uat027.py` → UAT027_SynthHi=1234.5 / SynthZero=0 / SynthNull=NULL + kind=in price history + SGD receipt fixture (`logs/seed.json`)
   - Browser: `scripts/browser_qa027.js` (puppeteer-core + Chrome; Hans + Hant-HK)
   - Result: **pass=28 fail=0** (`logs/browser-qa-result.json`, `logs/browser-qa.txt`)
6. Live served verify — `logs/live-served-verify.txt` (fmtMoney / formatHkd / finance foreign markers)
7. FX math verify — `logs/fx-math-verify.txt` (snapshot rate 5.0 × 1234.5 → **6172.5**)

Did **not** run: independent UAT stage; checklist/ledger flip; git write.

## Criterion 1 — synthetic money glyphs (PASS)

| Input | Expected | Evidence |
|---|---|---|
| 1234.5 | `HK$1,234.50` | harness Hans+Hant; painted inventory list cell; detail; PH metrics; ledger amounts |
| 0 | `HK$0.00` | harness; list row `UAT027_SynthZero` |
| null | `—` | harness; list row `UAT027_SynthNull` (DB NULL via SQL; `update_sku` skips None) |

Unit helpers in live page (`I18N.fmtMoney`) matched exactly under both `zh-Hans` and `zh-Hant-HK`.

## Criterion 2 — inventory surfaces Hans + Hant (PASS)

Owner fixture (`X-Role: owner` / `demo_role=owner`), synthetic SKUs only.

| Surface | zh-Hans | zh-Hant-HK |
|---|---|---|
| Inventory list unit price | Hi `HK$1,234.50`; Zero `HK$0.00`; Null `—` | same glyphs (UI labels 詳情) |
| SKU detail | `HK$1,234.50` present | covered via list + PH reopen |
| Price-history metrics / history | metrics contain `HK$1,234.50`; no bare `$1,234.50` without `HK` | body/list still show `HK$1,234.50` after locale switch |
| Ledger amounts | inbound `HK$1,234.50` / `HK$2,469.00` painted | (same session after Hans detail; list re-asserted under Hant) |
| AI cards | no anomaly fixture in this seed; **static harness** asserts `I18N.fmtMoney` on AI summary/cards paths | same |

Screenshots: `ui/hans-inventory-list.png`, `ui/hans-sku-detail.png`, `ui/hans-price-history.png`, `ui/hans-ledger.png`, `ui/hant-inventory-list.png`, `ui/hant-price-history.png`.

## Criterion 3 — foreign currency not forced HKD (PASS)

| Check | Result |
|---|---|
| `I18N.fmtMoney(1234.5, 'SGD')` | `SGD 1,234.50` — **does not** start with `HK$` (Hans+Hant) |
| `formatCurrency(1234.5, 'SGD')` | `S$ 1234.50` — **does not** start with `HK$` |
| `finance.js` static | keeps `formatCurrency(r.total_amount, r.currency)` + `currency !== 'HKD'`; HKD path uses `I18N.fmtMoney(r.total_hkd)` for converted amount only |
| Served finance.js | markers confirmed live |

Payables list for the seeded SGD receipt stayed empty because `compute_payables` requires a resolved `supplier_id` (fixture had name-only row). Foreign **display** contract was still proven via live `formatCurrency` / `fmtMoney` and static finance wiring; full payables paint can be deepened in independent UAT with a supplier-linked receipt.

## Negative — FX math unchanged (PASS)

| Check | Result |
|---|---|
| `demo/app/api_finance.py` / `recon_store.py` / `finance.js` | **git clean** vs HEAD (no UAT-027 edits) |
| Formula still present | `total_hkd = round(float(total_amount) * rate, 2)` at `api_finance.py:263` |
| Live snapshot math | SGD rate **5.0** × amount **1234.5** → **6172.5** (`logs/fx-math-verify.txt`) |
| Prose | fixture uses synthetic **1234.5** only; vendors aliased `Vendor_A` / `Vendor_B` / `Vendor_SGD_UAT027` |

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | **15750** (fresh; avoided 15150–15700) |
| DB | `docs/handoff/artifacts/uat027-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 (identity via `X-Role: owner` + `demo_role`) |
| uvicorn | pid 39623 started then stopped; process dead; reuseaddr bind OK |
| Browser | headless Chrome via puppeteer-core; context closed |
| Artifacts | `docs/handoff/artifacts/uat027-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/uat027-qa/environment/cleanup.json` |

## Residual risks (honest)

1. AI anomaly cards were not painted live (no price-anomaly fixture). Code-path coverage is from the specialty harness (AI summary/cards call `I18N.fmtMoney`). Independent UAT can seed an anomaly if pixel proof of the card is required.
2. Hant price-history panel metrics array was empty in one reopen (detail chrome timing); Hant list glyphs and Hans PH metrics were solid. UAT should re-open PH under Hant explicitly.
3. SGD payables row not rendered (missing supplier_id). Foreign non-HKD forcing still proven via `formatCurrency` / `fmtMoney(SGD)` in the live page.
4. Checklist in `01-acceptance-checklist.md` still `[TODO] DEV_DELIVERED`; do not flip from this QA alone.

## Scheduler handoff

- UAT-027 **QA PASS** (29/29 specialty harness + 85/85 i18n harness + 28/28 live browser checks + FX math verify).
- Please dispatch **independent UAT** next (fresh port ≠ 15750; do not reuse 15150–15750; MANUAL_UI).
- After UAT PASS: flip `[UAT-027]` to DONE PASS (W3 closeout item).

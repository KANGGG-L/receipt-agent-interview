# UAT-021 Independent UAT Report

> Generated: 2026-10-17 05:37 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation — staff vs owner price/margin masking (MANUAL_UI + cookie API)
> Card: [UAT-021] 店员库存列表与餐品列表泄露价格及毛利 — P1 PRODUCT_FAULT
> Prior: `uat021-dev-report.md`, `uat021-qa-report.md` (QA port **15650**)
> Checklist / git: not touched (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Real staff cookie → inventory/dishes list JSON + painted UI: `last_unit_price` / `price` / `gross_profit` / `gross_margin_rate` (+ synonyms) null/omitted / emdash | **PASS** |
| 2. Staff detail still null (SKU detail + dish detail + dish version snapshot) | **PASS** |
| 3. Owner same fixture sees prices (API + painted UI) | **PASS** |
| 4. Fake owner header (`X-Role: owner` + real staff cookie) cannot bypass | **PASS** |
| Negative: qty/unit staff-visible; inventory nav not hidden as sole auth; X-Role alone → 401 | **PASS** |
| Live cookie API | **PASS** (42/0) |
| Browser MANUAL_UI | **PASS** (27/0) |

**UAT-021 UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15700** (QA used 15650; forbidden reuse of **15150–15650**) |
| DB | `docs/handoff/artifacts/uat021-uat/environment/uat_isolated.db` (fresh) |
| RAG | `docs/handoff/artifacts/uat021-uat/rag_isolated` |
| Auth | `AUTH_ENABLED=1`, `DEMO_TOKEN_SECRET=uat021-uat-secret-not-default-32charsX` |
| Fixture | owner-seeded SKU `UAT021料-招牌菜` + dish `UAT021招牌菜` price **48.0**; staff@demo.hk / boss@demo.hk |
| Venv | `environment/venv` → uat025-qa venv (deps only; fresh DB/port) |
| Server pid | 34468 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; **fresh BrowserContext per role**; real login form (`#authLoginEmail` / `#authLoginSubmit`) |

## Commands actually run

1. Self-built uvicorn on `127.0.0.1:15700` with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=1` — `GET /` health OK; `/api/auth/me` shows `auth_enabled=true`
2. Cookie API seed+verify `scripts/seed_and_api.py` — **pass=42 fail=0** (`logs/live-api-verify.txt`, `logs/live-api-result.json`, fixture `environment/fixture.json`)
3. Browser MANUAL_UI `scripts/browser_uat021.js` — staff + owner contexts; inventory/dishes painted cells; cookie APIs; fake header — **pass=27 fail=0** (`logs/browser-uat.txt`, `logs/browser-uat-result.json`, screenshots under `ui/`)
4. Static + live served citation — `logs/static-evidence.txt`
5. Full cleanup: stop pid, free port, `environment/cleanup.json`

Did **not** run: specialty pytest (QA-stage); checklist/ledger flip; git write.

## Criterion 1 — staff list JSON + painted UI (PASS)

Real login form → HttpOnly `receipt_session` cookie (`identity_source=cookie`, role=staff).

| Surface | Staff result |
|---|---|
| `GET /api/inventory` fixture SKU | `last_unit_price=null`; `vs_avg_pct`/`ack_action` null; `base_unit=斤`, `current_stock` present |
| `GET /api/dishes` fixture dish | `price`/`theoretical_cost`/`gross_profit`/`gross_margin_rate` all **null** |
| Inventory table painted cell `data-label="最新进货单价"` | **`—`**; no `$` price glyph in row |
| Dishes table painted selling-price cell | **`—`**; no `48` / `HK$48` money glyph |

Screenshots: `ui/staff-inventory.png`, `ui/staff-dishes.png`.

## Criterion 2 — detail + version still null (PASS)

| Endpoint (staff cookie) | Assert |
|---|---|
| `GET /api/inventory/skus/{id}` | `prices_visible=false`; costs null |
| `GET /api/dishes/{id}` | price/cost/margin null; ingredient `consumption_qty=0.5` / `unit=斤` retained; ing cost fields null |
| `GET /api/dishes/{id}/versions/1` | `snapshot.dish.price` null; name retained |

Browser page-context fetches mirrored the same nulls.

## Criterion 3 — owner same fixture sees prices (PASS)

Owner cookie `boss@demo.hk` on **same** SKU/dish ids:

| Surface | Owner result |
|---|---|
| Dish list/detail/version | `price=48.0`; `gross_profit`/`gross_margin_rate` present (gp=48, gm=100) |
| Inventory list | `last_unit_price=0.0` (**visible** to owner; **null** to staff on same id) |
| SKU detail | `prices_visible=true` |
| Inventory painted cell | **`$0.00`** (owner glyph; staff had `—`) |
| Dishes painted cell | **`HK$48.00`** |

Screenshots: `ui/owner-inventory.png`, `ui/owner-dishes.png`.

## Criterion 4 — fake owner header cannot bypass (PASS)

Staff cookie + `X-Role: owner` (+ API script also sent `X-User` / `X-Tenant-Id`):

- inventory `last_unit_price` still null
- dishes list/detail price still null
- version snapshot price still null
- SKU detail `prices_visible=false`

Also: cleared cookies + `X-Role: owner` alone → `GET /api/inventory` **401**.

## Negative — qty/unit; API is source of truth (PASS)

- Staff inventory: unit `斤` + stock visible; inventory **sidebar nav remains visible** (price-leak surface not “solved” by hiding the tab).
- Staff dishes chrome nav is `display:none` (existing product UX in `common.js`); UAT still forced the dishes section open and confirmed API+DOM redaction — **nav-hide is not accepted as auth**.
- Ingredient qty/unit retained under staff.
- Did **not** treat chrome hide as a substitute for API redaction.

## Residual notes

1. Inventory seed via PATCH set `last_unit_price=0.0` (owner-visible zero, not a rich costed inbound). Redaction still proven: owner non-null vs staff null on the same SKU id. Dish price **48.0** is the strong AC3 signal (API + painted `HK$48.00`).
2. Staff dishes row cellTexts included a trailing `$0.00` glyph that is **not** the seeded selling price 48 (selling-price cell is `—`; `has48Money=false`). Treated as non-AC residual; AC sensitive fields remain null/emdash.
3. Browser automation used headless Chromium via puppeteer-core with real login form typing and sidebar/DOM reads (not force-faked role switches).

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 34468) — see `environment/cleanup.json` |
| Port 15700 free | yes |
| Browser contexts | closed |
| Checklist / ledger | **not** flipped (UAT only; scheduler owns close) |
| Git | no commit / no push |

## Scheduler handoff

- UAT-021 **UAT PASS** (42/42 live cookie API + 27/27 MANUAL_UI).
- Card may be marked DONE PASS by coordinator (dev + QA + UAT all PASS) and **UAT-027 unblocked**.
- Artifacts: `docs/handoff/artifacts/uat021-uat/` + this report.

# UAT-021 Independent QA Report

> Generated: 2026-10-17 05:33 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-021 acceptance only (staff price/margin redaction on inventory + dishes + version snapshot)
> Card: [UAT-021] 店员库存列表与餐品列表泄露价格及毛利 — P1 PRODUCT_FAULT
> Prior: `uat021-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Real staff cookie → inventory/dishes list JSON: `last_unit_price` / `price` / `gross_profit` / `gross_margin_rate` (+ synonyms) null/omitted | **PASS** |
| 2. Staff detail still null (SKU detail + dish detail + dish version snapshot) | **PASS** |
| 3. Owner same fixture sees prices | **PASS** |
| 4. Fake owner header (`X-Role: owner` + real staff cookie) cannot bypass | **PASS** |
| Negative: qty/unit staff-visible; no nav-hide-as-auth; X-Role alone → 401 when AUTH_ENABLED=1 | **PASS** |
| Specialty pytest (10) | **PASS** |
| Live AUTH cookie API (69) | **PASS** |
| Browser cookie smoke (10) | **PASS** |

**UAT-021 QA gate: PASS.** Do not treat this as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Static evidence — `docs/handoff/artifacts/uat021-qa/logs/static-evidence.txt`
2. `node --check` inventory_board.js + dish_bom.js; `py_compile` api_dishes.py + api_inventory.py — OK (`logs/node-check.txt`)
3. `pytest demo/tests/test_uat_021_price_margin_masking.py -v` — **10 passed** (`logs/pytest-uat021.txt`)
   - covers list/detail, owner visibility, fake header, tenant isolation, **version snapshot** staff/owner/fake-header
4. Isolated uvicorn live (self-built; fresh port/DB; **AUTH_ENABLED=1**)
   - Port **15650** (fresh; avoided 15150–15600)
   - DB `environment/qa_isolated.db`, RAG `rag_isolated`
   - `DEMO_TOKEN_SECRET=uat021-qa-secret-not-default-32charsXX`
   - Script: `scripts/live_api_verify.py`
   - Result: **pass=69 fail=0** (`logs/live-api-result.json`, `logs/live-api-verify.txt`)
5. Live served JS markers + X-Role alone 401 — `logs/live-served-verify.txt`
6. Browser cookie smoke (puppeteer-core + Chrome) — **pass=10 fail=0** (`logs/browser-qa-result.json`, `logs/browser-qa.txt`; screenshots `ui/staff-inventory.png`, `ui/owner-inventory.png`)

Did **not** run: independent UAT stage; checklist/ledger flip; git write.

## Criterion 1 — staff list JSON redaction (PASS)

Real login `staff@demo.hk` → HttpOnly `receipt_session` cookie (`identity_source=cookie`).

| Endpoint | Sensitive fields | Staff result |
|---|---|---|
| `GET /api/inventory` | `last_unit_price`, `vs_avg_pct`, `ack_action`; `price_anomaly` False/null; meta threshold null | **null / masked** |
| `GET /api/dishes` | `price`, `theoretical_cost`, `gross_profit`, `gross_margin_rate`; ing `sku_last_unit_price` / `ingredient_cost` | **all null** |

Fixture dish name `QA021招牌菜` price seeded **48.0** as owner; staff list returns `price=null`.

## Criterion 2 — detail + version still null (PASS)

| Endpoint | Staff asserts |
|---|---|
| `GET /api/inventory/skus/{id}` | `prices_visible=false`; `last_unit_price`/`avg_cost`/`vs_avg_pct`/`latest_price` null; batches `unit_cost` null |
| `GET /api/dishes/{id}` | price/cost/margin null; ing cost null |
| `GET /api/dishes/{id}/versions/1` | `snapshot.dish.price` null; cost/margin synonyms null; name + consumption_qty/unit retained |

Specialty pytest AC for version snapshot also **3/3 PASS** (staff mask / owner visible / fake header).

## Criterion 3 — owner same fixture sees prices (PASS)

Owner cookie `boss@demo.hk`:

- Dish list/detail/version: `price=48.0`; margin fields present (gp/gm non-null)
- Inventory list: `last_unit_price` non-null for owner (`0.0` after PATCH seed — still **visible to owner**, **null to staff**)
- SKU detail: `prices_visible=true`

## Criterion 4 — fake owner header cannot bypass (PASS)

Staff cookie + attack headers `X-Role: owner` (+ `X-User` / `X-Tenant-Id`):

- inventory list `last_unit_price` still null
- dishes list/detail price/gp/cost still null
- SKU detail `prices_visible=false`
- version snapshot `price` still null

Also: after clearing cookies, `X-Role: owner` alone → **`GET /api/inventory` 401** (not production auth).

## Negative — qty/unit visible; API is source of truth (PASS)

- Staff inventory list/detail: `base_unit=斤`, `current_stock` present (0.0 in live seed), name visible
- Staff dish ingredients / version ingredients: `consumption_qty=0.5`, `unit=斤` retained
- UI defense-in-depth present: `inventory_board.js` requires `isOwnerRoleNow()` for price cell; `dish_bom.js` gates price/cost/margin (served markers confirmed)
- Did **not** accept nav/tab hiding as a substitute for API auth

Tenant isolation (AC6) exercised in specialty pytest `test_tenant_isolation_and_b_baseline_unpolluted` (PASS). Live AUTH=1 path is token-tenant-bound (default); cross-tenant covered by pytest.

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | **15650** (fresh; avoided 15150–15600) |
| DB | `docs/handoff/artifacts/uat021-qa/environment/qa_isolated.db` (+ `qa_pytest.db`) |
| Auth | `AUTH_ENABLED=1`, non-default `DEMO_TOKEN_SECRET` |
| Fixture accounts | boss@demo.hk (owner) / staff@demo.hk (staff) |
| uvicorn | pid 32798 started then stopped; process dead; reuseaddr bind OK |
| Browser | headless Chrome via puppeteer-core; context closed |
| Artifacts | `docs/handoff/artifacts/uat021-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/uat021-qa/environment/cleanup.json` |

## Residual risks (honest)

1. Live inventory seed used PATCH `last_unit_price` (owner saw `0.0`, not a rich costed inbound). Redaction still proven: owner non-null vs staff null on the same SKU id. Costed inbound path is covered by specialty pytest (`unit_price=15.0` / stock=40).
2. Full MANUAL_UI pixel walk (inventory table cell glyphs / dish BOM cards under real painted login for three roles) can be deepened in independent UAT on a **new** port — QA already did cookie browser smoke + screenshots.
3. Checklist in `01-acceptance-checklist.md` still `[TODO] DEV_DELIVERED`; do not flip from this QA alone.

## Scheduler handoff

- UAT-021 **QA PASS** (10/10 pytest + 69/69 live cookie API + 10/10 browser smoke).
- Please dispatch **independent UAT** next (fresh port ≠ 15650; do not reuse 15150–15650).
- After UAT PASS: flip `[UAT-021]` to DONE PASS and **unblock UAT-027**.

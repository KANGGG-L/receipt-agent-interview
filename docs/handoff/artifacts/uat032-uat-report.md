# UAT-032 Independent UAT Report

> Generated: 2026-10-17 15:26 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_UI acceptance — same-day 3 then 2 → merged qty 5 in response/table/dialog + FIFO detail sets / cumulative cost; replay second request_id still qty 5; different day no cross-leak; Tenant B unchanged; reject table=5 alone without dialog/cost
> Card: [UAT-032] 同日餐品二次提交后FIFO弹窗仍显示第一次份数与成本 — P2 PRODUCT_FAULT MANUAL_UI
> Prior gates (not reused as PASS): `uat032-dev-report.md` (port 15132), `uat032-qa-report.md` (port 15142)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Same day same dish: 3 then 2 different request_ids → merged qty 5 in response, table, dialog; FIFO detail sets / cumulative cost match both submits | **PASS** |
| 2. Replay second request_id still qty 5 | **PASS** |
| 3. Different day no cross-leak | **PASS** |
| Negative: do not accept table=5 alone without dialog/cost evidence; Tenant B unchanged | **PASS** |

**UAT-032 UAT gate: PASS.** Checklist flipped to `[DONE] PASS` on this UAT (per dispatch). W4 UAT-032 closed. CLOSE-139 may need amendment (card was listed blocked/unverified at adjudication time).

## Isolation (fresh; not QA/DEV)

| Field | Value |
|---|---|
| Port | **15150** (≠15142 QA / ≠15132 DEV) |
| Avoided | **15010 kept free**; 15132 / 15142 never bound this turn |
| DB | `docs/handoff/artifacts/uat032-uat/environment/uat_isolated.db` (copy of `demo/receipt_demo.db`) |
| Tenants | `uat032uat` / `uat032uatb` (UAT-owned; not QA `uat032qa` / DEV) |
| AUTH_ENABLED | 0 |
| Server pid | 263815 (stopped; `environment/cleanup.json`) |
| Venv | `docs/handoff/artifacts/eng05-qa/environment/venv` |

## Commands actually run

1. Isolated uvicorn `127.0.0.1:15150` with stage DB / AUTH_ENABLED=0
2. Specialty + merge regression pytest — **5 passed** (`logs/pytest-specialty.log`)
   - `demo/tests/test_uat_032_same_day_fifo_modal_merge.py` (2)
   - `demo/tests/test_daily_consumption_idempotent_merge.py` (3)
3. API + static acceptance — `scripts/uat_api_check.py` — tenants `uat032uat` / B `uat032uatb` — **31/31 PASS** (`logs/uat_api_check.json`)
4. Playwright UI FIFO modal — `scripts/uat_ui_fifo_modal.py` — **PASS** with dialog qty + cost + two FIFO detail lines (`logs/uat_ui_fifo.json`, `ui/modal-after-second.png`)
5. Stop uvicorn; process gone; **15010** free; 15150 LISTEN cleared (TIME-WAIT only)

Did **not**: git commit / push; reuse QA/DEV ports or tenants as evidence; accept table=5 without dialog/cost.

## Criterion 1 — Same-day 3 then 2 → qty 5 response/table/dialog + FIFO cost (PASS)

HTTP (tenant `uat032uat`, day `2026-04-10`, dish BOM 0.5kg @ HK$10/kg):

| Check | Observed |
|---|---|
| First submit qty 3 (`request_id=uat032-uat-1`) | HTTP 200; `merged=false`; `quantity=3.0`; snapshot details **1**; cost **15.0** |
| Second submit qty 2 (`request_id=uat032-uat-2`) | HTTP 200; `merged=true`; `quantity=5.0`; same `consumption_ids` |
| Response `data.consumptions[0]` snapshot (dialog source) | `quantity=5.0`; `total_cost=25.0`; **2** FIFO details; detail cost sum **25.0** |
| GET `/api/dishes/daily_consumption?date=2026-04-10` table | 1 row; qty **5.0**; cost **25.0**; details **2**; matches snapshot |

Playwright UI (headless Chromium, date today Asia/Shanghai `2026-10-17`):

| Surface | Observed |
|---|---|
| `#traceDishQty` (FIFO modal) | `2026-10-17 / 售出 5 份` |
| `#traceTotalCost` | `HK$25.00` |
| `#dishDailyHistoryTableBody` | `5 份` + `HK$25.00` |
| `#costTraceDetailsContainer` | two FIFO deduct lines: **1.5 kg** @10 → HK$15.00 + **1 kg** @10 → HK$10.00 (sum 2.5 kg / HK$25.00) |

Negative gate: UI verdict requires `qty_has_5` **and** `table_has_5` **and** `cost_has_25` **and** `details_has_two_batches` (`kg` count >= 2) **and** `details_lines_ok` — table alone would FAIL.

Static markers: `await loadDailyConsumption` + `fromSnap`; always-fetch `showCostTraceModal`; cache-bust `?v=20261017_uat032`; API `_serialize_consumption_row` / `consumption_snapshots`.

Pytest: `test_uat032_same_day_3_then_2_snapshot_and_replay`, `test_uat032_third_merge_still_accumulates` PASSED.

Evidence: `logs/uat_api_check.json`, `logs/uat_ui_fifo.json`, `ui/modal-after-second.png`.

## Criterion 2 — Replay second request_id still qty 5 (PASS)

| Check | Observed |
|---|---|
| Replay POST same body as second submit (`request_id=uat032-uat-2`) | HTTP 200; `replayed=true` |
| `data.quantity` | **5.0** (not +2 again) |
| Replay snapshot details | still **2** |

Evidence: `logs/uat_api_check.json` (`replay_ok`, `replay_flag`, `replay_qty_still_5`, `replay_snap_details_2`).

## Criterion 3 — Different day no cross-leak + Tenant B (PASS)

| Check | Observed |
|---|---|
| Submit qty 1 on day B `2026-04-11` | `quantity=1.0`; `merged=false` |
| Day A GET after day B write | still 1 row qty **5.0** with **2** details |
| Day B GET | 1 row qty **1.0** |
| Tenant B stock after A activity | unchanged **3.0** |

Evidence: `logs/uat_api_check.json` (`other_day_qty_1`, `day_a_unpolluted`, `day_b_isolated`, `tenant_b_unchanged`).

## Cleanup

- Uvicorn pid 263815 on **15150** stopped; LISTEN cleared; **15010** / **15132** / **15142** free (`environment/cleanup.json`).
- Isolate DB retained under `uat032-uat/environment/` for handoff (not production).
- No git commit / no push.

## Checklist / ledger

- `01-acceptance-checklist.md`: UAT-032 → `[DONE] PASS` (2026-10-17 Asia/Shanghai).
- `02-coordinator-ledger.md`: W4 UAT-032 closed; note CLOSE-139 may need amendment (UAT-032 was documented as blocked/unverified at CLOSE-139 adjudication).

## Limits / honesty

- Dialog evidence covered by Playwright on isolate (auto-open after second submit); not a human operator session on a physical display.
- Cost evidence required: modal `HK$25.00` + two FIFO detail cost lines; table qty 5 alone was explicitly insufficient for PASS.
- This UAT does **not** reopen or flip CLOSE-139; amendment is a scheduler/user decision.

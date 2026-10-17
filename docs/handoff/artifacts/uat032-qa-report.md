# UAT-032 Independent QA Report

> Generated: 2026-10-17 15:24 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-032 acceptance only (same-day 3 then 2 → merged qty 5 in response/table/dialog + FIFO detail sets / cumulative cost; replay second request_id still qty 5; different day no cross-leak; reject table=5 alone without dialog/cost)
> Card: [UAT-032] 同日餐品二次提交后FIFO弹窗仍显示第一次份数与成本 — P2 PRODUCT_FAULT MANUAL_UI
> Prior: `uat032-dev-report.md`
> UAT: NOT started (per dispatch: No UAT / no DONE / no git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Same day same dish: 3 then 2 different request_ids → merged qty 5 in response, table, dialog; FIFO detail sets / cumulative cost match both submits | **PASS** |
| 2. Replay second request_id still qty 5 | **PASS** |
| 3. Different day no cross-leak | **PASS** |
| Negative: do not accept table=5 alone without dialog/cost evidence | **PASS** |

**UAT-032 QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped (still `[TODO] DEV_DELIVERED`).

## Commands actually run

1. Isolated uvicorn on **15142** (fresh; ≠ **15132**; **15010** never bound)
   - `DB_PATH=…/uat032-qa/environment/qa_isolated.db` (copy of `demo/receipt_demo.db`)
   - `AUTH_ENABLED=0`, host `127.0.0.1`, pid **262291** → stopped (`environment/cleanup.json`)
2. Specialty + merge regression pytest — **5 passed** (`logs/pytest-specialty.log`)
   - `demo/tests/test_uat_032_same_day_fifo_modal_merge.py` (2)
   - `demo/tests/test_daily_consumption_idempotent_merge.py` (3)
3. API + static acceptance — `scripts/qa_api_check.py` — tenants `uat032qa` / B `uat032qab` — **31/31 PASS** (`logs/qa_api_check.json`)
4. Playwright UI FIFO modal — `scripts/qa_ui_fifo_modal.py` — **PASS** with dialog qty + cost + two FIFO detail lines (`logs/qa_ui_fifo.json`, `ui/modal-after-second.png`)

Did **not** run: independent UAT; checklist/ledger flip to DONE; git write.

## Criterion 1 — Same-day 3 then 2 → qty 5 response/table/dialog + FIFO cost (PASS)

HTTP (tenant `uat032qa`, day `2026-04-10`, dish BOM 0.5kg @ HK$10/kg):

| Check | Observed |
|---|---|
| First submit qty 3 (`request_id=uat032-qa-1`) | HTTP 200; `merged=false`; `quantity=3.0`; snapshot details **1**; cost **15.0** |
| Second submit qty 2 (`request_id=uat032-qa-2`) | HTTP 200; `merged=true`; `quantity=5.0`; same `consumption_ids` |
| Response `data.consumptions[0]` snapshot (dialog source) | `quantity=5.0`; `total_cost=25.0`; **2** FIFO details; detail cost sum **25.0** |
| GET `/api/dishes/daily_consumption?date=2026-04-10` table | 1 row; qty **5.0**; cost **25.0**; details **2**; matches snapshot |

Playwright UI (headless Chromium, date today Asia/Shanghai `2026-10-17`):

| Surface | Observed |
|---|---|
| `#traceDishQty` (FIFO modal) | `2026-10-17 / 售出 5 份` |
| `#traceTotalCost` | `HK$25.00` |
| `#dishDailyHistoryTableBody` | `5 份` + `HK$25.00` |
| `#costTraceDetailsContainer` | two FIFO deduct lines: **1.5 kg** @10 → HK$15.00 + **1 kg** @10 → HK$10.00 (sum 2.5 kg / HK$25.00) |

Negative gate: UI verdict requires `qty_has_5` **and** `table_has_5` **and** `cost_has_25` **and** `details_has_two_batches` — table alone would FAIL.

Static markers: `await loadDailyConsumption` + `fromSnap`; always-fetch `showCostTraceModal`; cache-bust `?v=20261017_uat032`; API `_serialize_consumption_row` / `consumption_snapshots`.

Pytest: `test_uat032_same_day_3_then_2_snapshot_and_replay`, `test_uat032_third_merge_still_accumulates` PASSED.

Evidence: `logs/qa_api_check.json` (`second_qty_5`, `dialog_snap_*`, `table_*`), `logs/qa_ui_fifo.json`, `ui/modal-after-second.png`.

## Criterion 2 — Replay second request_id still qty 5 (PASS)

| Check | Observed |
|---|---|
| Replay POST same body as second submit (`request_id=uat032-qa-2`) | HTTP 200; `replayed=true` |
| `data.quantity` | **5.0** (not +2 again) |
| Replay snapshot details | still **2** |

Pytest specialty covers replay path; merge regression suite also green.

Evidence: `logs/qa_api_check.json` (`replay_ok`, `replay_flag`, `replay_qty_still_5`, `replay_snap_details_2`).

## Criterion 3 — Different day no cross-leak (PASS)

| Check | Observed |
|---|---|
| Submit qty 1 on day B `2026-04-11` | `quantity=1.0`; `merged=false` |
| Day A GET after day B write | still 1 row qty **5.0** with **2** details |
| Day B GET | 1 row qty **1.0** |
| Tenant B stock after A activity | unchanged **3.0** |

Evidence: `logs/qa_api_check.json` (`other_day_qty_1`, `day_a_unpolluted`, `day_b_isolated`, `tenant_b_unchanged`).

## Cleanup

- Uvicorn pid 262291 on **15142** stopped; **15010** and **15132** never bound / remain free (`environment/cleanup.json`).
- Isolate DB retained under `uat032-qa/environment/` for handoff (not production).
- No git; no DONE; no UAT; no checklist flip.

## Limits / honesty

- This report is **independent QA only**. MANUAL_UI UAT still required before DONE.
- Dialog evidence covered by Playwright on isolate (auto-open after second submit); not a human operator session.
- Cost evidence required: modal `HK$25.00` + two FIFO detail cost lines; table qty 5 alone was explicitly insufficient for PASS.

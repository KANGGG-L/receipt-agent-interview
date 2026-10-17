# UAT-008 Independent UAT Report

> Generated: 2026-10-17 21:50 UTC+8 (Asia/Singapore)
> Role: Independent UAT / acceptance reviewer. Did not do DEV or QA; did not reuse DEV/QA scripts, ports, DBs or fixtures. No product code modified. No commit / push.
> Card: [UAT-008] 实收0的保存与入账算术校验不一致 - P1 PRODUCT_FAULT / MANUAL_UI (`01-acceptance-checklist.md:303`)
> Base: `main` @ `b0e6b1e` + uncommitted DEV changes (`uat008-dev-report.md`), QA PASS (`uat008-qa-report.md`).

## Final verdict: UAT PASS

| AC / boundary | Verdict | Evidence (`uat008-uat/`) |
|---|---|---|
| AC1 synthetic qty 1 / price 10 / amount 10 / 实收 0, six fees each distinct (运费 5 / 押金 4 / 服务费 3 / 税 2 / 折扣 1 / 抹零 0.5) | PASS | Created through the real UI (新建手工单, typed every field, added six fee rows via 附加费用 drawer). Form data and save payload carry the values verbatim; auto total 12.50. `ui/01`, `logs/uat-ui-walk.txt` |
| AC2 effective 0 -> difference visible, no false "no problem" (save / detail / approval all 相差 10) | PASS | Save: hint "和明细加起来（12.50 元）相差 10.00 元", issue blocker "相差 $10.00", 保存 click blocked locally with toast naming 相差 $10.00 (`ui/02`, `ui/03`); API save without confirm -> 422 MATH_MISMATCH_CONFIRM "...应付 12.5 元...相差 10 元". Detail: API `data.gate_issues` TOTAL_MISMATCH diff 10.0 + `math_warnings` 相差 10; reopened in UI from the queue -> blocker 相差 $10.00 (`ui/06`); queue row 问题数 1 (`ui/08`). Approval: UI 审核入账 blocked locally, 0 requests (`ui/07`); approve-confirm modal (server detail data) shows blocker warning with confirm disabled (`ui/08b`); API approve -> 422 APPROVAL_BLOCKED "...相差 10 元"; 0 inventory rows |
| AC3 corrected total -> save and approve reach the same conclusion | PASS | Path A (same UI-created receipt): total 12.5 -> save 200 -> detail clean -> reopen from queue clean -> UI approve 200 (`ui/04`, `ui/05`). Path B (mismatched receipt): reopen, correct to 12.50 -> save 200 -> UI approve 200 (`ui/09`) |
| AC4 actual_qty=null -> quantity, 0 -> 0 | PASS | 实收 blank: auto total 22.50, payload `actual_qty: null`, stored NULL, approve 200, inventory_log qty 1, batch initial_qty 1 (`ui/10`). 实收 0: stored 0.0, inventory_log qty 0, no inventory batch. Tenant A `current_stock` = 1.0 after three approvals (0, 0, 1) (`evidence/db-readback.json`, `evidence/inventory-tenant-a.json`) |
| No truthy fallback 0 -> 1 | PASS | as AC4 (payload 0 stays 0 end-to-end; stock not incremented by the 实收 0 receipts) |
| Fee sign contract unchanged | PASS | Server messages show 减折扣 / 减抹零 / 加押金 / 加运费 / 加服务费 / 加税; UI auto total and backend 应付 agree (12.5 / 22.5 / credit note 2.5 / 12.5) |
| Unmatched not stocked | PASS | Mismatched receipt: UI + API approve blocked, inventory_log 0 rows until corrected |
| Cross-tenant / Tenant B baseline | PASS | Tenant B browser queue lists only its own receipt (`ui/13`); B detail / approve / save-over on A receipts -> 404; `/api/receipts` as B lists no A receipt; B receipt row + items byte-identical before/after; B inventory empty (`evidence/inventory-tenant-b.json`) |
| Credit-note signed hint | PASS | UI 折让单: amount -10 + fees -> auto 2.50; amount -20 -> typed -5 -> hint "（-7.50 元）相差 2.50 元" (negative, not clamped to 0); 改回明细合计 -> -7.50 (`ui/11`); with 实收 0 -> 12.50 (`ui/12`). Backend agrees on the total formula: credit_note amount -10 total 2.5 (null) -> 200, total 5 -> 422 相差 2.5; 实收 0 total 12.5 -> 200, total 2.5 -> 422 相差 10 |
| New test file + original UAT-008 tests | PASS | `logs/new-test.log` 46 passed; `logs/orig-test.log` 7 passed |
| Regression bound IDs (S1-P0-01, S1-P2-10, S2-01, S2-02, S2-14, S2-15 test files) | PASS | `logs/regression-bound.log` 164 passed (see command below) |

Defects: none blocking. Observations below (O1 judged, N1-N3 new, E1 analysed).

## Environment

- Isolated port **15380** (not 15010 / 15180 / 15280). Fresh DB `/workspace/.uat008-uat-env/uat.db` with uploads / rag / artifacts dirs there (outside the repo; removed after the run). `AUTH_ENABLED=0`, `RAG_EMBEDDING_PROVIDER=hash`, identity via `X-Role` / `X-Tenant-Id` browser context headers; tenants `uat008uat-a` (main), `uat008uat-b` (baseline), `uat008uat-cn` (credit-note API check); supplier `Vendor_A` / `Vendor_B`.
- Interpreter `/workspace/.venvs/receipt/bin/python`; headless Chromium via playwright, viewport 1440x1000.
- 15010 not listening before or after (`environment/pre-state.txt`, `environment/server-stop.txt`); UAT uvicorn stopped; 15380 released.
- UAT walk script written fresh for this stage: `uat_ui_walk.py` (output `logs/uat-ui-walk.txt`, `evidence/uat-ui-walk.json`, 35/35). The final run is the 7th iteration of the script on a fresh DB each time; earlier iterations only fixed my own selectors / expectations (fees drawer must be opened first, toast container selector, sidebar tab after reload, `data.gate_issues` path, and a credit-note fixture I had made row-inconsistent). No product behaviour changed between runs.
- 14 screenshots, all distinct MD5 (`ui/ui-hashes.txt`).

## O1 judgement: approval toast says 库存已增加 when 实收 0

Observed (receipts #2 and #3): approve 200, one `inventory_log` row `qty 0.0, amount 0.0, kind in`, no `inventory_batches` row, SKU stock unchanged; toast "单据 #2 已审核入账，库存已增加".

Judgement against the ACs: **not an AC failure.** The ACs are about arithmetic consistency ("差额必须可见且不能假称无问题", "修正总额匹配后保存审批同结论", "0按0", "未匹配不入库存"). For a matched receipt with 实收 0, save, detail, approval and stock all agree (stock delta 0). The "不能假称无问题" clause refers to the mismatch difference, and the mismatch is never hidden. The zero-qty `inventory_log` row is a harmless audit trail and creates no batch.

The toast is still factually wrong for this case: it tells the owner stock increased when it did not. It is pre-existing static text (`review_modal.js:288`) and outside this card's scope. Recommend a small P3 follow-up wording card, e.g. "单据 #N 已审核入账（本单实收为 0，库存未变化）" when every effective qty is 0. Non-blocking.

## E1: `tests/test_rag_embedding_wiring.py` needs `demo/.rag_chroma`

Ran myself:
- Without the dir (state after DEV/QA cleanup): 14 passed, 1 failed, `test_real_store_creates_identity_suffixed_collections`. `_assert_store_not_written` asserts that the live store fingerprint `before` is not None ("已存在的 live demo 向量库 <demo>/.rag_chroma 必须存在") (`logs/e1-rag-wiring-without-dir.log`).
- With an empty `demo/.rag_chroma` created temporarily: 15 passed (`logs/e1-rag-wiring-with-temp-dir.log`). The dir was removed right after. `<repo>/.rag_chroma` does not exist before or after.

Is the folder expected to be tracked or created by setup? **No to both:**
- `.gitignore:25` ignores `.rag_chroma/`, and `git log --all -- demo/.rag_chroma` is empty, so it was never tracked.
- Nothing in setup creates it: `demo/demo.sh` and the READMEs do not mention it. `demo/.env.example:98` only sets `RAG_DIR=./.rag_chroma`. It is a runtime artifact that the RAG service (`rag_paths.py:43`, default `<demo>/.rag_chroma`) creates lazily on the first vector write.
- The test's own docstring assumes a machine where memory is live and the store already holds vectors. So on any fresh clone or CI it fails until the app has written a vector once.

Recommendation:
1. Keep it untracked; never commit a vector store.
2. Coordinator / stage cleanup must not delete `demo/.rag_chroma` if it existed before the run. If it is absent, run root tests after `mkdir -p demo/.rag_chroma` and remove it again.
3. Open a separate test-harness card (not product code, not UAT-008). Either give the test an explicit skip with a reason when the live store is absent and no live-memory marker is set, or add a documented bootstrap step that creates the empty store. The docstring's "do not revert to assert-absent" constraint stays satisfied either way.

## New observations (non-blocking, outside UAT-008)

- **N1 (pre-existing, low):** `currentRole` is never defined in the front end (`typeof currentRole === 'undefined'` in the page; only read in `workbench_queue.js:222/379` and `review_modal.js:99/178`). So the owner-only queue shortcut 审核入账 never renders: rows show 继续复核 only. The client-side staff guard also never fires; the server still enforces roles. The mismatch gate holds on every reachable path, and the modal behind the shortcut (`openApproveConfirmModalFromQueue`, invoked directly) also blocks with confirm disabled. Suggest a separate card.
- **N2 (UAT-029 scope):** on a 折让单 with qty 1 / price 10 / amount -20, the UI shows only the grand-total blocker. The backend additionally flags the row ("单据写 -20 元，按数量单价应是 -10 元"). This is row-level arithmetic parity on credit notes; hand it to UAT-029, which owns the credit-note save path.
- **N3 (docs):** the QA report and ledger entry are stamped "22:05 UTC+8", but this UAT ran and finished at about 21:50 UTC+8 the same day. The QA timestamp looks mislabelled. Cosmetic.

## Tests actually run in UAT (logs in `uat008-uat/logs/`)

| Command | Result | Log |
|---|---|---|
| `pytest demo/tests/test_uat_008_actual_qty_normalization.py -q -p no:cacheprovider -W ignore` | 46 passed | `new-test.log` |
| `pytest demo/tests/test_uat_008_actual_qty_zero_arithmetic.py -q -p no:cacheprovider -W ignore` | 7 passed | `orig-test.log` |
| `pytest demo/tests/test_review_s1_p0_01.py demo/tests/test_b1_receipt_state_machine.py demo/tests/test_b1_platform.py demo/tests/test_review_s2_01.py demo/tests/test_p_ai_pipeline_fixes.py -k "not browser"` | 164 passed | `regression-bound.log` |
| `pytest tests/test_rag_embedding_wiring.py` (no `demo/.rag_chroma`) | 14 passed, 1 failed (E1) | `e1-rag-wiring-without-dir.log` |
| same, with temporary empty `demo/.rag_chroma` (removed after) | 15 passed | `e1-rag-wiring-with-temp-dir.log` |
| Live UI + API walk `uat_ui_walk.py` on :15380 | 35/35 | `uat-ui-walk.txt`, `evidence/uat-ui-walk.json` |

Not run: broad `demo/tests` / root `tests` full suites (DEV and QA both ran them; not repeated here), and browser suites bound to the shared 15010 server.

## Cleanup

- UAT uvicorn stopped; 15380 released; 15010 untouched.
- Removed gitignored scratch created by this stage: `demo/backups/` (7 startup backups written by the UAT server), `demo/receipt_demo.db` (created by the pytest runs), temporary `demo/.rag_chroma`, and the whole `/workspace/.uat008-uat-env/`.
- Left in place (pre-existing, not created by UAT): gitignored `demo/.env` and root `artifacts/memory/` (mtime 21:09 UTC+8, before this stage).
- No venv, node_modules or .db under `uat008-uat/`.

## Evidence paths

- Report: `docs/handoff/artifacts/uat008-uat-report.md`
- `docs/handoff/artifacts/uat008-uat/`: `uat_ui_walk.py`, `logs/*`, `evidence/{uat-ui-walk.json, db-readback.json, inventory-tenant-a.json, inventory-tenant-b.json}`, `ui/01-13*.png` + `ui-hashes.txt`, `environment/{pre-state.txt, pre-test-git-status.txt, server-stop.txt}`

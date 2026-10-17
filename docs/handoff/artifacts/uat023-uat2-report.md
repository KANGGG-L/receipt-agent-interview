# UAT-023 Independent UAT2 Report (re-UAT after fix2)

> Generated: 2026-10-17 04:46 Asia/Shanghai (CST)
> Role: Independent re-UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation of Hant export preview business names vs CSV after fix2
> Card: [UAT-023] 繁体报表预览自动转换业务品名，和CSV原值不一致 — P2 PRODUCT_FAULT
> Prior: `uat023-dev-fix2-report.md`, `uat023-qa2-report.md` (QA2 port 15280), prior FAIL `uat023-uat-report.md` (port 15270)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Hans→Hant→Hans：业务名 painted textContent == CSV 原值（含 `归属部门`） | **PASS** |
| 1b. `receipts_itemized_ledger` / `归属部门` has `data-i18n-skip` under Hant | **PASS** |
| 1c. Prior FAIL regression closed (wrong Hant form absent) | **PASS** |
| 2. 表头仍翻译；UI chrome / I18N.convert 仍活跃 | **PASS** |
| 3. DB 业务 name 不变 | **PASS** |
| 4. 业务名含 HTML 仍 escape（无 live `<script>`） | **PASS** |
| Negative: 不全局关闭 zh-Hant-HK | **PASS** |
| Live painted browser MANUAL_UI (95 checks) | **PASS** (95/0) |

**UAT-023 UAT2 gate: PASS.**

Prior FAIL root cause addressed: painted `#exportPreviewTbody` under zh-Hant-HK now keeps Hans `归属部门` cell equal to Hans CSV, with `data-i18n-skip`. Wrong OpenCC form `後廚冷鏈中心（超長部門名稱）` was **not** seen in any preview equality detail.

## Isolation (fresh; not QA2 / prior UAT)

| Field | Value |
|---|---|
| Port | **15290** (QA2 used 15280; prior UAT used 15270; forbidden reuse of 15150–15280) |
| DB | `docs/handoff/artifacts/uat023-uat2/environment/uat2_isolated.db` (fresh) |
| RAG | `docs/handoff/artifacts/uat023-uat2/environment/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo X-Role owner; MANUAL_UI does not require production cookie) |
| Fixture | synthetic long Hans SKU / supplier / dept + approved receipt + stock logs (`db.create_dish` N/A) |
| Venv | `environment/venv` → symlink to uat023-qa2 venv |
| Server pid | 158374 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; real DOM locale switch + export preview paint |

## Commands actually run

1. Self-built uvicorn on `127.0.0.1:15290` with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=0` — `GET /` HTTP 200; `/api/auth/me` shows `auth_enabled=false`
2. Seed long Hans names — `scripts/seed_uat023.py` → `logs/seed.json`
3. Static + live served JS citation — `logs/static-evidence.txt`, `logs/live-served-verify.txt` (fix2 `h.includes('部门')` + `data-i18n-skip`)
4. Browser MANUAL_UI `scripts/browser_uat2.js` (puppeteer-core + system Chrome): locale Hans→Hant-HK→Hans; export tab; 5 reports with biz rows; painted `#exportPreviewTbody` identity textContent vs Hans CSV; header/chrome convert probes — `logs/browser-uat2.txt`, `logs/browser-uat2-result.json`, screenshots under `ui/`
5. Explicit `归属部门` extract — `logs/guishu-bumen-extract.json`
6. Post-run SQLite read — `logs/db-after.json` (SKU / dept / supplier still exact Hans)
7. Full cleanup: stop pid, free port, `environment/cleanup.json`

Did **not** run: pytest / QA harnesses (QA-stage); checklist edit; git write.

Browser check tally: **95 PASS / 0 FAIL**.

## Criterion 1 — preview biz names == CSV under Hant (PASS) incl. prior FAIL column

Self-checked citations (served from :15290):

- `export_center.js:899` — `|| h.includes('部门') || h === '销售对象'` (fix2; covers `归属部门`)
- `export_center.js:928` — `data-i18n-skip` when `preserveBiz`

Runtime (browser, port 15290) — mandatory painted check:

| Check | Result |
|---|---|
| `receipts_itemized_ledger/Hant: identity[归属部门] row0 preview==CSV` | **PASS** — preview=`后厨冷链中心（超长部门名称）` == Hans CSV |
| `receipts_itemized_ledger/Hant: identity[归属部门] row0 has data-i18n-skip` | **PASS** |
| Wrong Hant form `後廚冷鏈中心（超長部門名稱）` in preview details | **absent** (`wrong_hant_seen_in_preview: []`) |
| Hans phase same column | PASS |

Other identity columns under Hant paint also PASS (品名 / 供应商名称 / 备注 etc. across stocktake / valuation / stock_logs / ledger). LONG_NAME exact Hans preserved; `有機走地雞胸肉` absent.

Screenshots: `ui/preview-hant-receipts_itemized_ledger.png`, `ui/preview-hans-receipts_itemized_ledger.png`.

Extract: `logs/guishu-bumen-extract.json`.

## Criterion 2 — headers / chrome still translate (PASS)

Runtime:

- document `lang` / `data-locale` = `zh-Hant-HK`
- `I18N.convert('导出 CSV')` → `匯出 CSV`; `已入账` → `已入賬`; `供应商名称` → `供應商名稱`
- Per-report: Hant painted thead headers differ from Hans CSV headers; Hant CSV headers localized vs Hans

## Criterion 3 — DB name unchanged (PASS)

`logs/db-after.json`:

- SKU id=1 name still exact `有机走地鸡胸肉（去皮去骨特长规格说明超长品名测试ABC）`
- Department still exact `后厨冷链中心（超长部门名称）`
- Supplier still exact long Hans

## Criterion 4 + negative — HTML escape; Hant not disabled (PASS)

- XSS SKU `鸡翅<script>alert(1)</script>` appears in stocktake preview; tbody has no live `<script>` child
- I18N.convert remains active (not globally disabled)

## Residual notes

1. `dish_cost_margin` still had no dish seed path (`db.create_dish` missing) — same coverage gap prior QA/UAT noted; unrelated to the fix2 regression.
2. Browser automation used headless Chromium via puppeteer-core (not a human GUI tab). Flows exercised real served HTML/JS + OpenCC MutationObserver on painted preview tables.
3. This UAT2 deliberately used painted DOM (same probe class as the prior FAIL UAT), not API preview JSON alone.

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 158374) |
| Port 15290 free | yes (`environment/cleanup.json`) |
| Browser contexts | closed; no leftover chrome for 15290 |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat023-uat2/environment/cleanup.json` |

No git commit / push. No checklist edit.

## Scheduler signal

- **UAT2 gate: PASS**
- Evidence: `docs/handoff/artifacts/uat023-uat2-report.md` + `docs/handoff/artifacts/uat023-uat2/`
- Prior FAIL closed on painted `归属部门`. Scheduler may flip `[UAT-023]` checklist to `[DONE] PASS` (this stage did not touch checklist).

# UAT-023 Development Report (fix2)

> Generated: 2026-10-17 04:42 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT-023] 繁体报表预览自动转换业务品名，和CSV原值不一致 — P2 PRODUCT_FAULT
> Prior: `uat023-dev-report.md` (fix1), `uat023-qa-report.md` (PASS on API JSON), `uat023-uat-report.md` (**FAIL** on painted DOM)
> Checklist: **NOT flipped** — remains for scheduler after re-QA / re-UAT

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| UAT FAIL root cause addressed | PASS |
| `归属部门` treated as identity (skip OpenCC) | PASS |
| Similar dept headers (`部门` / `部门名称`) covered | PASS (`h.includes('部门')`) |
| Headers / UI chrome still translate | PASS (negative control + header convert asserts) |
| Regression: Hans→Hant `归属部门` textContent == CSV Hans | PASS (frontend harness) |
| Specialty pytest (5) | PASS |
| Frontend harness (22) | PASS |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |
| Checklist DONE | NOT_APPLIED |

**Dev delivery (fix2): ready for independent QA re-dispatch.** Do not treat this report as card PASS.

## UAT FAIL root cause (printed citations)

From `docs/handoff/artifacts/uat023-uat-report.md`:

- Painted DOM under zh-Hant-HK: `归属部门` cell became `後廚冷鏈中心（超長部門名稱）` while Hans CSV kept `后厨冷链中心（超长部门名称）`.
- `data-i18n-skip` absent on that `<td>`.
- Old predicate (fix1):

```text
demo/static/js/export_center.js (pre-fix2)
|| h === '部门' || h.includes('部门名称');
```

- `归属部门` is neither exact `部门` nor a substring match of `部门名称`, so `preserveBiz` stayed false.

QA could not see this: it compared **API preview JSON** (never OpenCC) to CSV. Independent UAT used painted `#exportPreviewTbody`.

## Fix (fix2)

### `demo/static/js/export_center.js` — `isExportBusinessIdentityHeader`

Printed after patch:

```text
893:function isExportBusinessIdentityHeader(h) {
895:    // 部门身份列：部门 / 部门名称 / 归属部门 等（UAT-023 fix2：旧谓词漏掉「归属部门」）
899:        || h.includes('部门') || h === '销售对象';
```

- Replaced `h === '部门' || h.includes('部门名称')` with `h.includes('部门')` so `归属部门` / `部门名称` / `部门` all skip OpenCC.
- Added `h === '销售对象'` to align with QA/UAT identity index (`live_preview_csv_verify.py` / `browser_uat023.js`).
- `buildExportRowsHtml` unchanged: still emits `data-i18n-skip` when `preserveBiz` is true (`export_center.js:911` / `:927`).
- `<th>` path unchanged — table headers still convert.

## Tests

```text
node --check demo/static/js/export_center.js
# exit 0

node demo/tests/frontend_uat023_export_preview_harness.js
# 22 PASS including:
#   PASS identity header 归属部门
#   PASS build marks only 归属部门 cell with data-i18n-skip
#   PASS Hant: 归属部门 cell == CSV Hans
#   PASS Hant: 归属部门 header still converts
#   PASS Hant without skip: dept name converts (control)

.../uat023-qa/environment/venv/bin/python -m pytest \
  demo/tests/test_uat_023_export_preview_biz_names.py -v
# 5 passed (incl. test_export_identity_header_covers_guishu_bumen)
```

Harness citations (runtime Hans→Hant equality with CSV seed string):

```text
harness:134:eq('identity header 归属部门', box.__isId('归属部门'), true);
harness:141:const LONG_DEPT = '后厨冷链中心（超长部门名称）';
harness:146:eq('build marks only 归属部门 cell with data-i18n-skip', skipCount, 1);
harness:156:eq('Hant: 归属部门 cell == CSV Hans', deptTd.childNodes[0].data, LONG_DEPT);
harness:157:eq('Hant: 归属部门 header still converts', ...);
harness:163:eq('Hant without skip: dept name converts (control)', ...);
```

MANUAL_UI (8 CSV × painted DOM Hans→Hant→Hans, incl. `receipts_itemized_ledger` / `归属部门`) is **reserved for independent QA/UAT**.

## Acceptance mapping (dev evidence only)

| Criterion | Dev evidence (fix2) |
|---|---|
| Preview biz names == CSV under Hant (incl. `归属部门`) | Predicate covers `归属部门`; harness paint equality with LONG_DEPT |
| Headers still translate | Harness: Hant header converts; without-skip control converts cell |
| DB business name unchanged | No write-path change in fix2 |
| Escape HTML in names | Prior harness `esc` asserts retained |
| Do not globally disable Hant | Unchanged; pytest `DISABLE_HANT` / `convertDisabled` absent |

## Explicit non-actions

- Did **not** edit `docs/handoff/01-acceptance-checklist.md` (UAT-023 stays non-DONE).
- Did **not** run independent QA or independent UAT.
- Did **not** git commit / push.

## Scheduler signal

- **Dev fix2 ready** for re-dispatch: independent QA → independent UAT.
- Evidence: `docs/handoff/artifacts/uat023-dev-fix2-report.md`
- Prior FAIL evidence retained: `docs/handoff/artifacts/uat023-uat-report.md`

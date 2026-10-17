# -*- coding: utf-8 -*-
"""UAT-023 QA live verify: seed long Hans names; preview vs CSV; DB unchanged; headers localize."""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib import request as urlrequest

PORT = int(os.environ.get("APP_PORT", "15260"))
BASE = f"http://127.0.0.1:{PORT}"
DB_PATH = os.environ["DB_PATH"]
OWNER = {"X-Role": "owner", "X-Email": "boss@demo.hk", "X-Tenant-Id": "default"}

LONG_NAME = "有机走地鸡胸肉（去皮去骨特长规格说明超长品名测试ABC）"
LONG_SUPPLIER = "鲜生农产供应商（特长名称说明）"
LONG_DISH = "有机走地鸡胸肉煲（超长餐品名称规格说明）"
LONG_DEPT = "后厨冷链中心（超长部门名称）"
XSS_NAME = "鸡翅<script>alert(1)</script>"

results = []
failed = 0


def ok(name, cond, detail=""):
    global failed
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(("PASS" if cond else "FAIL"), name, (detail if not cond else ""))
    if not cond:
        failed += 1


def http_json(method, path, body=None, headers=None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urlrequest.Request(BASE + path, data=data, headers=hdrs, method=method)
    with urlrequest.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        try:
            return resp.status, json.loads(raw.decode("utf-8")), raw
        except Exception:
            return resp.status, None, raw


def http_bytes(method, path, body=None, headers=None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urlrequest.Request(BASE + path, data=data, headers=hdrs, method=method)
    with urlrequest.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read()


def seed_via_db():
    sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
    from app import db

    db.DB_PATH = DB_PATH
    db._make_engine()

    def ensure_sku(name, **kw):
        existing = db.find_sku_by_name(name, tenant_id="default")
        if existing:
            return existing.id
        sku_id, err = db.create_sku(name, tenant_id="default", **kw)
        assert not err, err
        return sku_id

    sku_id = ensure_sku(LONG_NAME, category="肉类", base_unit="kg", min_stock_alert=1.0)
    db.update_sku(sku_id, current_stock=12.5, last_unit_price=88.0)

    sku_xss = ensure_sku(XSS_NAME, category="肉类", base_unit="kg")
    db.update_sku(sku_xss, current_stock=3.0, last_unit_price=40.0)

    dept_id = db.create_department(LONG_DEPT)
    db.create_supplier(LONG_SUPPLIER)

    rid = db.create_receipt_record(
        supplier_name=LONG_SUPPLIER,
        status="approved",
        receipt_date="2026-09-10",
        total_amount=880.0,
        currency="HKD",
        paid_at="2026-09-11T08:00:00",
        expected_pay_date="",
        department_id=dept_id,
        created_at="2026-09-10T08:00:00",
        updated_at="2026-09-10T08:00:00",
    )
    db.set_receipt_items(
        rid,
        [{"name": LONG_NAME, "quantity": 10, "unit": "kg", "unit_price": 88.0, "amount": 880.0}],
    )

    # stock log so inventory_stock_logs / consumption have rows
    db.apply_stock_log(
        sku_id, LONG_NAME, 2.0, "kg", 176.0, LONG_SUPPLIER,
        "2026-09-12", rid, "consume", tenant_id="default",
    )
    db.apply_stock_log(
        sku_id, LONG_NAME, 10.0, "kg", 880.0, LONG_SUPPLIER,
        "2026-09-10", rid, "in", tenant_id="default",
    )

    dish_id = None
    try:
        dish_id = db.create_dish(LONG_DISH, category="热菜", price=128.0, status="active")
    except TypeError:
        try:
            dish_id = db.create_dish(name=LONG_DISH, category="热菜", sell_price=128.0)
        except Exception as e:
            print("WARN create_dish:", e)
    except Exception as e:
        print("WARN create_dish:", e)

    return {
        "sku_id": sku_id,
        "sku_name": LONG_NAME,
        "xss_sku_id": sku_xss,
        "dept_id": dept_id,
        "receipt_id": rid,
        "dish_id": dish_id,
    }


def read_sku_name(sku_id):
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute("SELECT name FROM skus WHERE id=?", (sku_id,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def preview(report_id, params=None, lang=None):
    body = {"report_id": report_id, "params": params or {}}
    if lang:
        body["lang"] = lang
    status, js, _ = http_json("POST", "/api/export/preview", body, OWNER)
    assert status == 200, (status, js)
    return js["data"]


def download_csv(report_id, params=None, lang=None):
    body = {"report_id": report_id, "params": params or {}}
    if lang:
        body["lang"] = lang
    status, raw = http_bytes("POST", "/api/export/download", body, OWNER)
    assert status == 200, status
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    headers = rows[0] if rows else []
    data_rows = rows[1:] if len(rows) > 1 else []
    return headers, data_rows, text


def identity_col_indexes(headers_hans):
    idxs = []
    for i, h in enumerate(headers_hans):
        if (
            "品名" in h
            or "名称" in h
            or "说明" in h
            or "备注" in h
            or "配方" in h
            or "建议" in h
            or "供应商" in h
            or "往来" in h
            or h == "部门"
            or "部门名称" in h
            or h == "核算对象"
            or h == "归属部门"
            or "诊断" in h
        ):
            idxs.append(i)
    return idxs


def main():
    print("=== SEED ===")
    baseline = seed_via_db()
    name_before = read_sku_name(baseline["sku_id"])
    ok("DB seed long name present", name_before == LONG_NAME, repr(name_before))

    period = {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    report_params = {
        "inventory_stocktake": {},
        "dish_ingredient_consumption": dict(period),
        "inventory_valuation": {},
        "inventory_price_anomaly": {"direction": "all"},
        "dish_cost_margin": dict(period),
        "spend_summary": dict(period),
        "inventory_stock_logs": dict(period),
        "receipts_itemized_ledger": dict(period),
    }

    print("=== PREVIEW vs CSV (8 reports) ===")
    sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
    from app.i18n_text import localize

    reports_with_biz_hit = 0
    for rid, params in report_params.items():
        pdata = preview(rid, params)
        p_headers = pdata.get("headers") or []
        p_rows = pdata.get("preview_rows") or []
        c_headers, c_rows, _ = download_csv(rid, params, lang="zh-Hans")
        c_headers_hant, c_rows_hant, _ = download_csv(rid, params, lang="zh-Hant-HK")

        ok(
            f"{rid}: preview headers == CSV Hans headers",
            p_headers == c_headers,
            f"preview={p_headers!r} csv={c_headers!r}",
        )
        # JSON preview may keep numbers as float; CSV is all strings.
        # CSV may also prefix formula-injection sanitize (\' before =+-@).
        def norm_cell(c):
            s = "" if c is None else str(c)
            if s.startswith("'") and len(s) > 1 and s[1] in "=+-@\t\r":
                return s[1:]
            return s
        p_rows_s = [[norm_cell(c) for c in r] for r in p_rows]
        c_rows_s = [[norm_cell(c) for c in r] for r in c_rows]
        ok(
            f"{rid}: preview rows == CSV Hans rows (str, sanitize-aware)",
            p_rows_s == c_rows_s,
            f"n_preview={len(p_rows)} n_csv={len(c_rows)}",
        )

        idxs = identity_col_indexes(p_headers)
        if idxs and p_rows:
            reports_with_biz_hit += 1
            for ci in idxs:
                for ri, prow in enumerate(p_rows):
                    if ri >= len(c_rows_hant):
                        ok(f"{rid}: row {ri} present in Hant CSV", False)
                        continue
                    pv = prow[ci] if ci < len(prow) else None
                    cv = c_rows_hant[ri][ci] if ci < len(c_rows_hant[ri]) else None
                    equal = str(pv) == str(cv)
                    if not equal and isinstance(cv, str) and cv.startswith("'") and cv[1:] == str(pv):
                        equal = True
                    ok(
                        f"{rid}: biz col[{ci}]={p_headers[ci]!r} row{ri} preview==HantCSV",
                        equal,
                        f"preview={pv!r} hant_csv={cv!r}",
                    )

            flat = "\n".join(" | ".join(str(c) for c in r) for r in p_rows)
            if rid in (
                "inventory_stocktake",
                "inventory_valuation",
                "receipts_itemized_ledger",
            ):
                hit = LONG_NAME in flat or LONG_SUPPLIER in flat or LONG_DEPT in flat or XSS_NAME in flat
                ok(f"{rid}: synthetic long biz name appears in preview", hit, flat[:240])
        else:
            ok(f"{rid}: empty-or-no-identity-ok", True, f"idxs={idxs} nrows={len(p_rows)}")

        headers_match_localize = all(
            localize(h, "zh-Hant-HK") == ch for h, ch in zip(p_headers, c_headers_hant)
        ) and len(p_headers) == len(c_headers_hant)
        ok(
            f"{rid}: Hant CSV headers == localize(Hans)",
            headers_match_localize,
            f"hans={p_headers!r} hant={c_headers_hant!r}",
        )

    ok(
        "at least one report had biz rows for identity compare",
        reports_with_biz_hit >= 1,
        f"hit={reports_with_biz_hit}",
    )

    print("=== DB UNCHANGED AFTER EXPORTS ===")
    name_after = read_sku_name(baseline["sku_id"])
    ok(
        "DB sku name unchanged after preview/download",
        name_after == LONG_NAME == name_before,
        repr(name_after),
    )
    con = sqlite3.connect(DB_PATH)
    try:
        sn = con.execute("SELECT name FROM suppliers WHERE name=?", (LONG_SUPPLIER,)).fetchone()
        ok("DB supplier name unchanged", bool(sn) and sn[0] == LONG_SUPPLIER, repr(sn))
    finally:
        con.close()

    print("=== SERVED JS wiring ===")
    _, _, js_raw = http_json("GET", "/static/js/export_center.js")
    js = js_raw.decode("utf-8")
    ok("served export_center has isExportBusinessIdentityHeader", "function isExportBusinessIdentityHeader" in js)
    ok("served export_center emits data-i18n-skip", "preserveBiz ? ' data-i18n-skip'" in js)
    _, _, i18n_raw = http_json("GET", "/static/js/i18n/i18n.js")
    i18n = i18n_raw.decode("utf-8")
    ok("served i18n has data-i18n-skip skip", "data-i18n-skip" in i18n)
    ok("served i18n not DISABLE_HANT", "DISABLE_HANT" not in i18n)
    ok("served i18n has zh-Hant-HK", "zh-Hant-HK" in i18n)
    _, _, cost_raw = http_json("GET", "/static/js/cost_report.js")
    cost = cost_raw.decode("utf-8")
    ok(
        "served cost_report has data-i18n-skip on dep/supplier/raw_name",
        'data-i18n-skip>${w2Escape(dep.name)}' in cost
        and 'data-i18n-skip>${w2Escape(suppName)}' in cost
        and "data-i18n-skip>${w2Escape(r.raw_name" in cost,
    )

    out = {
        "failed": failed,
        "passed": sum(1 for r in results if r["ok"]),
        "total": len(results),
        "baseline": {k: baseline[k] for k in ("sku_id", "sku_name", "receipt_id")},
        "results": results,
    }
    Path(os.environ.get("OUT_JSON", "/tmp/uat023-live.json")).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"failed": failed, "passed": out["passed"], "total": out["total"]}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

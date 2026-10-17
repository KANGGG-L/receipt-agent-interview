# -*- coding: utf-8 -*-
"""UAT-011 gt-single-click probe: GT confirm must not create business pending receipts.

AC:
- synthetic U001; one click → all POSTs + DB before/after
- GT confirm exactly 1; save_edited zero
- receipts/items/inventory/payments unchanged
- double-click GT no duplicate business writes
- leave eval → normal manual save still works
- Tenant B isolation
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from urllib import error, request

from playwright.sync_api import sync_playwright

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
DB_PATH = os.environ["DB_PATH"]
EVALSET_DIR = Path(os.environ["EVALSET_DIR"])
UI = ART / "ui"
EV = ART / "evidence"
UI.mkdir(parents=True, exist_ok=True)
EV.mkdir(parents=True, exist_ok=True)

TENANT_A = "uat011-a"
TENANT_B = "uat011-b"

result = {
    "card": "UAT-011",
    "script": "gt_single_click.py",
    "verdict": "FAIL",
    "steps": [],
    "posts_single": [],
    "posts_double": [],
    "posts_manual": [],
    "db_before": {},
    "db_after_gt": {},
    "db_after_manual": {},
}


def step(name, ok, detail):
    result["steps"].append({"name": name, "pass": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(tag, name, json.dumps(detail, ensure_ascii=False, default=str)[:800])


def hdr(tenant: str, role: str = "admin") -> dict:
    return {
        "Content-Type": "application/json",
        "X-Role": role,
        "X-Tenant-Id": tenant,
        "X-Requested-With": "fetch",
    }


def call(method: str, path: str, body=None, *, tenant: str, role: str = "admin"):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = request.Request(BASE + path, data=data, headers=hdr(tenant, role), method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return {"ok": True, "status": resp.status, "body": parsed}
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return {"ok": False, "status": e.code, "body": parsed}
    except Exception as e:
        return {"ok": False, "status": 0, "body": str(e)}


def _has_col(cur, table, col):
    try:
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
        return col in cols
    except sqlite3.Error:
        return False


def db_counts(tenant: str | None = None):
    """AC tables: receipts, receipt_items (items), inventory_batches+inventory_log, payments."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    def _count(sql, args=()):
        cur.execute(sql, args)
        return int(cur.fetchone()["c"])

    if tenant:
        tables = {
            "receipts": _count("SELECT COUNT(*) AS c FROM receipts WHERE tenant_id=?", (tenant,)),
            "receipt_items": _count(
                "SELECT COUNT(*) AS c FROM receipt_items i JOIN receipts r ON i.receipt_id=r.id WHERE r.tenant_id=?",
                (tenant,),
            ),
            "inventory_batches": (
                _count("SELECT COUNT(*) AS c FROM inventory_batches WHERE tenant_id=?", (tenant,))
                if _has_col(cur, "inventory_batches", "tenant_id")
                else _count("SELECT COUNT(*) AS c FROM inventory_batches")
            ),
            "inventory_log": (
                _count("SELECT COUNT(*) AS c FROM inventory_log WHERE tenant_id=?", (tenant,))
                if _has_col(cur, "inventory_log", "tenant_id")
                else _count("SELECT COUNT(*) AS c FROM inventory_log")
            ),
            "payments": (
                _count("SELECT COUNT(*) AS c FROM payments WHERE tenant_id=?", (tenant,))
                if _has_col(cur, "payments", "tenant_id")
                else _count(
                    "SELECT COUNT(*) AS c FROM payments p JOIN receipts r ON p.receipt_id=r.id WHERE r.tenant_id=?",
                    (tenant,),
                )
            ),
        }
    else:
        tables = {
            "receipts": _count("SELECT COUNT(*) AS c FROM receipts"),
            "receipt_items": _count("SELECT COUNT(*) AS c FROM receipt_items"),
            "inventory_batches": _count("SELECT COUNT(*) AS c FROM inventory_batches"),
            "inventory_log": _count("SELECT COUNT(*) AS c FROM inventory_log"),
            "payments": _count("SELECT COUNT(*) AS c FROM payments"),
        }
    con.close()
    return {"tenant": tenant, "tables": tables, "totals": tables}


def classify_posts(posts):
    confirm = [p for p in posts if "/api/evalset/sample/" in p.get("url", "") and "/confirm" in p.get("url", "")]
    save_edited = [p for p in posts if "/api/save_edited" in p.get("url", "")]
    other = [p for p in posts if p not in confirm and p not in save_edited]
    return {"confirm": confirm, "save_edited": save_edited, "other": other, "all": posts}


def seed_marker_receipt(tenant: str, supplier: str):
    """Create a minimal pending receipt via save_edited so counts are non-zero."""
    body = {
        "supplier_name": supplier,
        "date": "2026-10-01",
        "total_amount": 10.0,
        "settlement_type": "cash",
        "doc_form": "manual_entry",
        "currency": "HKD",
        "items": [{"name": "marker", "quantity": 1, "unit": "个", "unit_price": 10, "amount": 10}],
    }
    return call("POST", "/api/save_edited", body, tenant=tenant, role="staff")


def gt_still_draft(sid: str) -> bool:
    p = EVALSET_DIR / "expected" / f"{sid}.json"
    if not p.exists():
        return False
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("gt_status") == "draft"


def main():
    # Seed markers
    sa = seed_marker_receipt(TENANT_A, "Marker_A")
    sb = seed_marker_receipt(TENANT_B, "Marker_B")
    step(
        "seed_markers",
        sa.get("status") == 200 and sb.get("status") == 200,
        {"a": sa.get("status"), "b": sb.get("status"), "a_body": sa.get("body"), "b_body": sb.get("body")},
    )

    before = db_counts(TENANT_A)
    before_b = db_counts(TENANT_B)
    before_all = db_counts(None)
    result["db_before"] = {"A": before, "B": before_b, "ALL": before_all}

    posts_bucket = {"list": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"X-Role": "admin", "X-Tenant-Id": TENANT_A},
        )
        page = context.new_page()
        page.add_init_script("() => { try { localStorage.setItem('demo_role','admin'); } catch(e){} }")

        def on_request(req):
            if req.method == "POST":
                posts_bucket["list"].append(
                    {
                        "url": req.url,
                        "method": req.method,
                        "post_data": (req.post_data or "")[:2000],
                        "ts": time.time(),
                    }
                )

        page.on("request", on_request)

        # ---- AC1/2: single click ----
        posts_bucket["list"] = []
        page.goto(BASE + "/evalset/workbench/U001", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)
        # wait for form prefill
        page.wait_for_function(
            """() => {
                const s = document.getElementById('inpSupplier');
                return s && s.value && s.value.length > 0;
            }""",
            timeout=20000,
        )
        UI.joinpath("01-workbench-u001.png").write_bytes(page.screenshot(full_page=False))
        btn = page.locator("#btnSaveReview")
        btn.wait_for(state="visible", timeout=10000)
        btn_text = btn.inner_text()
        step("gt_button_label", "确认本张 GT" in btn_text or "GT" in btn_text, {"text": btn_text})

        # Prevent navigation so we can inspect after click
        page.evaluate(
            """() => {
                window.__uat011_nav = [];
                const _assign = location.assign.bind(location);
                try {
                    Object.defineProperty(window, 'location', {
                        configurable: true,
                        value: new Proxy(location, {
                            set(t, p, v) { window.__uat011_nav.push(['set', p, String(v)]); return true; },
                            get(t, p) {
                                if (p === 'href') return t.href;
                                if (p === 'assign' || p === 'replace') return (u) => { window.__uat011_nav.push([p, String(u)]); };
                                const v = t[p];
                                return typeof v === 'function' ? v.bind(t) : v;
                            }
                        })
                    });
                } catch (e) {
                    // fallback: stub history
                }
            }"""
        )

        posts_bucket["list"] = []
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/confirm" in r.url,
            timeout=20000,
        ) as resp_info:
            btn.click(click_count=1)
        resp = resp_info.value
        page.wait_for_timeout(800)
        UI.joinpath("02-after-single-click.png").write_bytes(page.screenshot(full_page=False))

        single_posts = list(posts_bucket["list"])
        result["posts_single"] = single_posts
        cls = classify_posts(single_posts)
        confirm_ok = len(cls["confirm"]) == 1 and resp.status == 200
        save_zero = len(cls["save_edited"]) == 0
        step(
            "ac_single_click_posts",
            confirm_ok and save_zero,
            {
                "confirm_count": len(cls["confirm"]),
                "save_edited_count": len(cls["save_edited"]),
                "confirm_status": resp.status,
                "other_post_urls": [p["url"] for p in cls["other"]],
            },
        )

        after_gt = db_counts(TENANT_A)
        after_gt_b = db_counts(TENANT_B)
        after_gt_all = db_counts(None)
        result["db_after_gt"] = {"A": after_gt, "B": after_gt_b, "ALL": after_gt_all}
        unchanged = (
            before_all["totals"] == after_gt_all["totals"]
            and before["tables"] == after_gt["tables"]
            and before_b["tables"] == after_gt_b["tables"]
        )
        step(
            "ac_db_unchanged_after_gt",
            unchanged,
            {
                "before_totals": before_all["totals"],
                "after_totals": after_gt_all["totals"],
                "A_before": before["tables"],
                "A_after": after_gt["tables"],
                "B_before": before_b["tables"],
                "B_after": after_gt_b["tables"],
            },
        )

        # GT file confirmed
        gt_path = EVALSET_DIR / "expected" / "U001.json"
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        step(
            "ac_gt_file_confirmed",
            gt.get("gt_status") == "confirmed",
            {"gt_status": gt.get("gt_status"), "reviewed_by": gt.get("gt_reviewed_by")},
        )

        # ---- AC: double-click no duplicate business writes ----
        # Reload U002 (still draft). Delay confirm; double-click; expect 1 confirm, 0 save_edited.
        posts_bucket["list"] = []

        def delay_confirm(route):
            if route.request.method == "POST" and "/confirm" in route.request.url:
                time.sleep(0.8)
                route.continue_()
            else:
                route.continue_()

        page.route("**/api/evalset/sample/*/confirm", delay_confirm)
        page.goto(BASE + "/evalset/workbench/U002", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1000)
        page.wait_for_function(
            """() => {
                const s = document.getElementById('inpSupplier');
                return s && s.value && s.value.length > 0;
            }""",
            timeout=20000,
        )
        posts_bucket["list"] = []
        btn2 = page.locator("#btnSaveReview")
        # double-click while first request is delayed
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/confirm" in r.url,
            timeout=20000,
        ):
            btn2.dblclick()
        page.wait_for_timeout(1200)
        UI.joinpath("03-after-double-click-u002.png").write_bytes(page.screenshot(full_page=False))
        page.unroute("**/api/evalset/sample/*/confirm")

        double_posts = list(posts_bucket["list"])
        result["posts_double"] = double_posts
        cls2 = classify_posts(double_posts)
        after_dbl_all = db_counts(None)
        dbl_ok = (
            len(cls2["confirm"]) == 1
            and len(cls2["save_edited"]) == 0
            and after_dbl_all["totals"] == after_gt_all["totals"]
        )
        step(
            "ac_double_click_no_dup_business",
            dbl_ok,
            {
                "confirm_count": len(cls2["confirm"]),
                "save_edited_count": len(cls2["save_edited"]),
                "totals_before_dbl": after_gt_all["totals"],
                "totals_after_dbl": after_dbl_all["totals"],
            },
        )

        # ---- AC: leave eval, normal manual save works ----
        posts_bucket["list"] = []
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.evaluate(
            """() => {
                try { localStorage.setItem('demo_role','staff'); } catch(e){}
                if (typeof setRole === 'function') setRole('staff');
            }"""
        )
        page.wait_for_timeout(600)
        # open manual entry
        page.evaluate(
            """() => {
                if (typeof showTab === 'function') showTab('tab-workbench');
                const btn = document.getElementById('btnNewManualEntry');
                if (btn) btn.click();
                else if (typeof startManualEntry === 'function') startManualEntry();
            }"""
        )
        page.wait_for_timeout(800)
        # ensure form visible
        page.locator("#inpSupplier").wait_for(state="visible", timeout=15000)
        page.locator("#inpSupplier").fill("Vendor_Manual_After_Eval")
        page.locator("#inpDate").fill("2026-10-16")
        # fill first item row if present
        page.evaluate(
            """() => {
                const rows = document.querySelectorAll('#itemTableBody tr');
                if (rows.length === 0 && typeof appendTableRow === 'function') {
                    appendTableRow({name:'manual-item', quantity:2, unit:'个', unit_price:15, amount:30});
                }
                const name = document.querySelector('#itemTableBody .inp-name, #itemTableBody input[name="name"]');
                const qty = document.querySelector('#itemTableBody .inp-qty, #itemTableBody input[name="quantity"]');
                const price = document.querySelector('#itemTableBody .inp-price, #itemTableBody input[name="unit_price"]');
                if (name) { name.value = 'manual-item'; name.dispatchEvent(new Event('input',{bubbles:true})); }
                if (qty) { qty.value = '2'; qty.dispatchEvent(new Event('input',{bubbles:true})); }
                if (price) { price.value = '15'; price.dispatchEvent(new Event('input',{bubbles:true})); }
            }"""
        )
        page.locator("#inpTotal").fill("30.00")
        page.locator("#inpTotal").dispatch_event("input")
        if page.locator("#inpSettlementType").count():
            page.locator("#inpSettlementType").select_option("cash")
        if page.locator("#inpDocForm").count():
            page.locator("#inpDocForm").select_option("manual_entry")
        page.wait_for_timeout(400)
        UI.joinpath("04-manual-form.png").write_bytes(page.screenshot(full_page=False))

        # Prove eval context is gone
        in_eval = page.evaluate(
            """() => {
                const meta = document.querySelector('meta[name="eval-workbench"]');
                const flag = window.__EVAL_WORKBENCH__;
                return !!(flag && flag.sampleId) || !!(meta && String(meta.content||'').trim());
            }"""
        )
        step("leave_eval_context_cleared", not in_eval, {"in_eval": in_eval})

        posts_bucket["list"] = []
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/api/save_edited" in r.url,
            timeout=20000,
        ) as save_info:
            page.locator("#btnSaveReview").click()
        save_resp = save_info.value
        page.wait_for_timeout(600)
        UI.joinpath("05-after-manual-save.png").write_bytes(page.screenshot(full_page=False))
        manual_posts = list(posts_bucket["list"])
        result["posts_manual"] = manual_posts
        cls3 = classify_posts(manual_posts)
        save_body = {}
        try:
            save_body = save_resp.json()
        except Exception:
            save_body = {"raw": save_resp.text()[:500]}
        manual_ok = (
            len(cls3["save_edited"]) >= 1
            and save_resp.status == 200
            and (save_body.get("status") == "success" or save_body.get("receipt_id") or save_body.get("id"))
            and len(cls3["confirm"]) == 0
        )
        step(
            "ac_leave_eval_manual_save",
            manual_ok,
            {
                "save_edited_count": len(cls3["save_edited"]),
                "confirm_count": len(cls3["confirm"]),
                "http": save_resp.status,
                "body_status": save_body.get("status"),
                "receipt_id": save_body.get("receipt_id") or save_body.get("id"),
            },
        )

        after_manual = db_counts(TENANT_A)
        result["db_after_manual"] = after_manual
        # receipts should have increased by the manual save (business path works)
        receipts_grew = (
            isinstance(after_manual["tables"].get("receipts"), int)
            and isinstance(after_gt["tables"].get("receipts"), int)
            and after_manual["tables"]["receipts"] >= after_gt["tables"]["receipts"] + 1
        )
        step(
            "ac_manual_save_wrote_business_db",
            receipts_grew,
            {"A_after_gt": after_gt["tables"], "A_after_manual": after_manual["tables"]},
        )

        # Tenant B isolation: B counts still at seed level for receipts relative to after_gt
        # (manual save was on tenant A headers)
        b_now = db_counts(TENANT_B)
        b_ok = b_now["tables"] == after_gt_b["tables"]
        # B cannot see A's marker by id if we try hack — list shouldn't include Marker_A
        blist = call("GET", "/api/receipts", tenant=TENANT_B, role="owner")
        leak = False
        names = []
        if isinstance(blist.get("body"), dict):
            data = blist["body"].get("data") or blist["body"].get("receipts") or []
            if isinstance(data, dict):
                data = data.get("items") or data.get("receipts") or data.get("rows") or []
            if isinstance(data, list):
                for row in data:
                    if isinstance(row, dict):
                        names.append(row.get("supplier_name") or row.get("supplier") or "")
                leak = any("Marker_A" in str(n) or "Vendor_Manual" in str(n) for n in names)
        step(
            "neg_tenant_b_isolation",
            b_ok and not leak,
            {"B_tables_stable": b_ok, "leak": leak, "B_names_sample": names[:10], "B_http": blist.get("status")},
        )

        browser.close()

    fails = [s for s in result["steps"] if not s["pass"]]
    result["verdict"] = "PASS" if not fails else "FAIL"
    result["fail_count"] = len(fails)
    EV.joinpath("gt-single-click.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    summary = {
        "verdict": result["verdict"],
        "fail_count": result["fail_count"],
        "steps": [{"name": s["name"], "pass": s["pass"]} for s in result["steps"]],
    }
    EV.joinpath("dev-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("VERDICT", result["verdict"], "fails=", len(fails))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

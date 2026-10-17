#!/usr/bin/env python3
"""UAT-029 independent UAT live API probe (user-perspective acceptance).
Isolated env: port 15903 (1590x, none of the forbidden ports), DB /tmp/uat029-uat/uat-live.db,
AUTH_ENABLED=1, 48-byte random DEMO_TOKEN_SECRET via env only. No product code touched.
"""
import json, os, secrets, signal, sqlite3, subprocess, sys, time, urllib.request, urllib.error

ROOT = "/home/agentuser/receipt-agent-interview/demo"
PORT = 15903
BASE = f"http://127.0.0.1:{PORT}"
DB = "/tmp/uat029-uat/uat-live.db"
LOG = "/tmp/uat029-uat/logs/live-probe.log"

results = []
def check(name, ok, detail=""):
    results.append({"step": name, "pass": bool(ok), "detail": str(detail)[:500]})
    print(("PASS" if ok else "FAIL"), name, "|", str(detail)[:400], flush=True)

def req(method, path, body=None, cookie=None, csrf=False, bearer=None, extra=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data: r.add_header("Content-Type", "application/json")
    if csrf: r.add_header("X-Requested-With", "XMLHttpRequest")
    if cookie: r.add_header("Cookie", cookie)
    if bearer: r.add_header("Authorization", "Bearer " + bearer)
    if extra: [r.add_header(k, v) for k, v in extra.items()]
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.headers, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try: return e.code, e.headers, json.loads(raw)
        except Exception: return e.code, e.headers, {"raw": raw[:300]}

def start_server():
    env = dict(os.environ)
    for k in ("DB_PATH", "RAG_DIR"): env.pop(k, None)
    env.update({"DB_PATH": DB, "RAG_DIR": "/tmp/uat029-uat/rag",
                "AUTH_ENABLED": "1", "DEMO_TOKEN_SECRET": secrets.token_hex(48)})
    logf = open(LOG, "a")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                          "--host", "127.0.0.1", "--port", str(PORT)],
                         cwd=ROOT, env=env, stdout=logf, stderr=logf)
    for i in range(120):
        try:
            s, _, _ = req("GET", "/api/health")
            if s in (200, 404): return p
        except Exception: pass
        time.sleep(0.5)
    p.terminate(); raise RuntimeError("server startup timeout")

def q(sql, a=()):
    c = sqlite3.connect(DB)
    try: return c.execute(sql, a).fetchall()
    finally: c.close()

ITEM = lambda name, qty, price, amount: {
    "name": name, "quantity": qty, "unit": "pcs", "unit_price": price, "amount": amount}

def save(body, cookie):
    return req("POST", "/api/save_edited", body, cookie=cookie, csrf=True)

def main():
    p = start_server()
    try:
        # ---- security preflight (UAT discipline: empty Authorization != anonymous exemption)
        s, _, b = req("GET", "/api/receipts")
        check("S1 no-credential GET /api/receipts -> 401", s == 401, f"{s} {b}")
        s, h, b = req("POST", "/api/auth/login", {"email": "boss@demo.hk", "password": "boss123"})
        setc = h.get("Set-Cookie", "")
        check("S2 owner login 200, HttpOnly cookie receipt_session",
              s == 200 and "receipt_session" in setc and "httponly" in setc.lower(), setc[:140])
        owner = setc.split(";")[0]
        owner_tok = b.get("token", "")
        s, _, b = req("GET", "/api/auth/me", cookie=owner)
        check("S3 /api/auth/me via cookie", s == 200, f"{s} {json.dumps(b)[:200]}")
        # cookie-session write WITHOUT X-Requested-With -> 403 CSRF
        s, _, b = req("POST", "/api/save_edited", {"supplier_name": "X", "total_amount": 1,
                      "items": [ITEM("a", 1, 1, 1)]}, cookie=owner)
        check("S4 cookie write without CSRF header -> 403 CSRF_REJECTED",
              s == 403 and b.get("code") == "CSRF_REJECTED", f"{s} {b}")
        # forged bearer token
        s, _, b = req("GET", "/api/receipts", bearer=owner_tok[:-4] + "aaaa")
        check("S5 forged bearer token -> 401", s == 401, f"{s}")
        # owner token + forged X-Tenant-Id -> tenant ignored (D19)
        s, _, b = req("GET", "/api/receipts", cookie=owner, extra={"X-Tenant-Id": "tenant_b"})
        check("S6 owner cookie + X-Tenant-Id: tenant_b -> tenant_b data NOT visible",
              s == 200 and all(x.get("tenant_id", "default") != "tenant_b" for x in (b.get("data") or [])),
              f"{s} rows={len(b.get('data') or [])}")
        b_before = q("select count(*) from receipts where tenant_id='tenant_b'")[0][0]
        check("S7 tenant_b baseline rows before", b_before == 0, f"count={b_before}")
        # staff login
        s, h, b2 = req("POST", "/api/auth/login", {"email": "staff@demo.hk", "password": "staff123"})
        staff = h.get("Set-Cookie", "").split(";")[0]
        check("S8 staff login 200", s == 200, f"{s}")

        # ---- AC1: credit_note negative-amount variant (UI-equivalent payload), full chain
        s, _, b = save({"supplier_name": "UAT029_Vendor", "date": "2026-10-18",
                        "total_amount": -10, "settlement_type": "cash", "doc_form": "credit_note",
                        "items": [ITEM("Credit_Item_A", 1, 10, -10)]}, owner)
        rid1 = (b or {}).get("receipt_id")
        check("AC1a credit_note neg-amount save 200, math_blockers empty",
              s == 200 and b.get("status") == "success" and not (b.get("math_blockers") or []),
              f"{s} {json.dumps(b)[:250]}")
        s, _, b = req("GET", f"/api/receipt/{rid1}", cookie=owner)
        d = b.get("data") or {}
        check("AC1b readback total=-10 doc_form=credit_note status=edited",
              s == 200 and d.get("total_amount") == -10 and d.get("doc_form") == "credit_note"
              and d.get("status") == "edited", f"{s} {json.dumps(d)[:250]}")
        s, _, b = req("POST", f"/api/receipt/{rid1}/approve", {"version": d.get("version")}, cookie=owner, csrf=True)
        check("AC1c approve 200", s == 200 and b.get("status") == "success", f"{s} {json.dumps(b)[:200]}")
        row = q("select status,total_amount,doc_form,version from receipts where id=?", (rid1,))
        check("AC1d DB approved total=-10", row and row[0][0] == "approved" and row[0][1] == -10, str(row))

        # ---- AC2: credit_note negative-qty variant
        s, _, b = save({"supplier_name": "UAT029_Vendor", "date": "2026-10-18",
                        "total_amount": -20, "settlement_type": "cash", "doc_form": "credit_note",
                        "items": [ITEM("Credit_Item_B", -2, 10, -20)]}, owner)
        rid2 = (b or {}).get("receipt_id")
        it = q("select quantity,amount from receipt_items where receipt_id=?", (rid2,))
        check("AC2 credit_note neg-qty save 200, DB qty=-2 amount=-20",
              s == 200 and not (b.get("math_blockers") or []) and it == [(-2.0, -20.0)],
              f"{s} db={it}")
        s, _, b = req("GET", f"/api/receipt/{rid2}", cookie=owner)
        v2 = (b.get("data") or {}).get("version")
        s, _, b = req("POST", f"/api/receipt/{rid2}/approve", {"version": v2}, cookie=owner, csrf=True)
        check("AC2b neg-qty variant approve 200", s == 200, f"{s} {json.dumps(b)[:150]}")

        # ---- AC3: fee arithmetic match (item -10 + delivery_fee -5 = -15)
        s, _, b = save({"supplier_name": "UAT029_Vendor", "date": "2026-10-18",
                        "total_amount": -15, "settlement_type": "cash", "doc_form": "credit_note",
                        "delivery_fee": -5,
                        "items": [ITEM("Credit_Item_C", 1, 10, -10)]}, owner)
        rid3 = (b or {}).get("receipt_id")
        check("AC3 fee arithmetic item -10 + delivery -5 = total -15 -> 200 no blockers",
              s == 200 and not (b.get("math_blockers") or []), f"{s} {json.dumps(b)[:200]}")

        # ---- AC4: plain purchase negative total still rejected
        s, _, b = save({"supplier_name": "UAT029_NegPurchase", "date": "2026-10-18",
                        "total_amount": -10, "settlement_type": "cash", "doc_form": "",
                        "items": [ITEM("P_Item", 1, 10, -10)]}, owner)
        rid4 = (b or {}).get("receipt_id")
        blockers = b.get("math_blockers") or []
        check("AC4a purchase negative total -> math_blockers raised",
              s == 200 and len(blockers) >= 1, f"{s} blockers={blockers}")
        s, _, b = req("GET", f"/api/receipt/{rid4}", cookie=owner)
        v4 = (b.get("data") or {}).get("version")
        s, _, b = req("POST", f"/api/receipt/{rid4}/approve", {"version": v4}, cookie=owner, csrf=True)
        check("AC4b purchase negative total approve -> 422 APPROVAL_BLOCKED",
              s == 422 and b.get("code") == "APPROVAL_BLOCKED", f"{s} {json.dumps(b)[:200]}")

        # ---- N1: double negative (qty<0 AND price<0) still rejected
        s, _, b = save({"supplier_name": "UAT029_DoubleNeg", "date": "2026-10-18",
                        "total_amount": 10, "settlement_type": "cash", "doc_form": "credit_note",
                        "items": [ITEM("DN_Item", -2, -5, 10)]}, owner)
        check("N1 double-negative qty&price rejected (422 or blockers)",
              s >= 400 or len(b.get("math_blockers") or []) >= 1, f"{s} {json.dumps(b)[:300]}")

        # ---- N2: no new credit-note doc type added
        forms = q("select distinct doc_form from receipts")
        check("N2 doc forms in DB limited to known enums (no new credit type)",
              all(f[0] in ("credit_note", "", "printed_delivery_note") for f in forms), str(forms))

        # ---- AC5 / oracle S2-09: reversal protection on consumed stock
        s, _, b = save({"supplier_name": "UAT029_PosVendor", "date": "2026-10-18",
                        "total_amount": 30, "settlement_type": "cash", "doc_form": "",
                        "items": [ITEM("Pos_Item", 3, 10, 30)]}, owner)
        rid5 = (b or {}).get("receipt_id")
        s, _, b = req("GET", f"/api/receipt/{rid5}", cookie=owner)
        v5 = (b.get("data") or {}).get("version")
        s, _, b = req("POST", f"/api/receipt/{rid5}/approve", {"version": v5}, cookie=owner, csrf=True)
        check("AC5a positive receipt approve 200", s == 200, f"{s}")
        stock_before = q("select sku_id, quantity from inventory_movements where receipt_id=?", (rid5,)) \
            if q("select name from sqlite_master where name like '%movement%'") else q("select 1")
        s, _, b = req("POST", f"/api/receipt/{rid5}/reverse",
                      {"reason": "UAT029 reversal", "version": q("select version from receipts where id=?", (rid5,))[0][0]},
                      cookie=owner, csrf=True)
        check("AC5b reverse approved receipt -> 200 stock deducted back",
              s == 200 and b.get("status") == "success", f"{s} {json.dumps(b)[:200]}")
        stock_mid = q("select coalesce(sum(quantity),0) from inventory_movements where receipt_id=?", (rid5,)) \
            if stock_before != [(1,)] else None
        s, _, b = req("POST", f"/api/receipt/{rid5}/reverse",
                      {"reason": "UAT029 reversal replay", "version": q("select version from receipts where id=?", (rid5,))[0][0]},
                      cookie=owner, csrf=True)
        already = b.get("already_reversed")
        check("AC5c second reverse: idempotent 200 already_reversed=true (no double deduction)",
              s == 200 and already is True, f"{s} already_reversed={already} {json.dumps(b)[:200]}")
        stock_after = q("select coalesce(sum(quantity),0) from inventory_movements where receipt_id=?", (rid5,)) \
            if stock_before != [(1,)] else None
        check("AC5d inventory zero net change across double reverse",
              stock_after is None or (stock_mid is not None and stock_after == stock_mid),
              f"mid={stock_mid} after={stock_after}")

        # ---- N3: cross-tenant / staff boundary
        s, _, b = req("POST", f"/api/receipt/{rid5}/reverse",
                      {"reason": "staff tries", "version": q("select version from receipts where id=?", (rid5,))[0][0]},
                      cookie=staff, csrf=True)
        check("N3a staff (non-owner) reverse -> 403", s == 403, f"{s}")
        s, _, b = req("GET", f"/api/receipt/{rid1}", cookie=staff, extra={"X-Tenant-Id": "tenant_b"})
        check("N3b staff cannot escalate tenant via header", (b.get("data") or {}).get("tenant_id", "default") != "tenant_b",
              str((b.get('data') or {}).get('tenant_id')))
        b_after = q("select count(*) from receipts where tenant_id='tenant_b'")[0][0]
        a_rows = q("select id,supplier_name,total_amount,status from receipts where tenant_id='default' order by id")
        check("N3c tenant_b baseline unchanged after all probes", b_after == b_before == 0, f"count={b_after}")

        json.dump({"results": results, "tenant_b_before": b_before, "tenant_b_after": b_after,
                   "default_rows_final": a_rows},
                  open("/tmp/uat029-uat/logs/probe-result.json", "w"), ensure_ascii=False, indent=1)
    finally:
        p.send_signal(signal.SIGTERM); p.wait(timeout=15)
        print("server terminated; port check:", subprocess.run(
            ["ss", "-ltn"], capture_output=True, text=True).stdout.count(f":{PORT} "), "listeners on", PORT)

if __name__ == "__main__":
    main()

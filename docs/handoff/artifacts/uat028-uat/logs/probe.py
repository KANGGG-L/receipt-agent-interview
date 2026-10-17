#!/usr/bin/env python3
"""UAT-028 independent UAT live API probe (user-perspective acceptance).
Isolated env: port 15930, DB /tmp/uat028-uat/uat-live.db, AUTH_ENABLED=1.
"""
import json, os, secrets, signal, socket, subprocess, sys, time, urllib.request, urllib.error, sqlite3

ROOT = "/home/agentuser/receipt-agent-interview/demo"
PORT = 15930
BASE = f"http://127.0.0.1:{PORT}"
DB = "/tmp/uat028-uat/uat-live.db"
LOG = "/tmp/uat028-uat/logs/live-probe.log"

results = []
def check(name, ok, detail=""):
    results.append({"step": name, "pass": bool(ok), "detail": str(detail)[:400]})
    print(("PASS" if ok else "FAIL"), name, "|", detail)

def req(method, path, body=None, cookie=None, csrf=False, extra=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data: r.add_header("Content-Type", "application/json")
    if csrf: r.add_header("X-Requested-With", "XMLHttpRequest")
    if cookie: r.add_header("Cookie", cookie)
    if extra: [r.add_header(k, v) for k, v in extra.items()]
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.headers, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try: body_ = json.loads(e.read().decode() or "{}")
        except Exception: body_ = {}
        return e.code, e.headers, body_

def start_server():
    env = dict(os.environ)
    for k in ("DB_PATH", "RAG_DIR"): env.pop(k, None)
    env.update({
        "DB_PATH": DB, "RAG_DIR": "/tmp/uat028-uat/rag",
        "AUTH_ENABLED": "1",
        "DEMO_TOKEN_SECRET": secrets.token_hex(48),
    })
    logf = open(LOG, "a")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                          "--host", "127.0.0.1", "--port", str(PORT)],
                         cwd=ROOT, env=env, stdout=logf, stderr=logf)
    for i in range(60):
        try:
            s, _, _ = req("GET", "/api/health")
            if s in (200, 404): return p
        except Exception: pass
        time.sleep(0.5)
    raise RuntimeError("server startup timeout")

def main():
    p = start_server()
    try:
        # owner login -> HttpOnly cookie
        s, h, b = req("POST", "/api/auth/login", {"email": "boss@demo.hk", "password": "boss123"})
        setc = h.get("Set-Cookie", "")
        check("owner login 200", s == 200, f"{s} {b}")
        cookie = setc.split(";")[0] if setc else ""
        check("cookie is HttpOnly session", "receipt_session" in setc and "httponly" in setc.lower(), setc[:120])

        # baseline: supplier count before
        s, _, b = req("GET", "/api/suppliers?include_inactive=1", cookie=cookie)
        base_ids = sorted(x["id"] for x in b.get("data", []))
        check("baseline list ok", s == 200, f"count={len(base_ids)}")

        # AC1: create Vendor_A_uat -> rename Vendor_B_uat -> PATCH active=0 -> name retained
        s, _, b = req("POST", "/api/suppliers", {"name": "Vendor_A_uat", "contact_phone": "12345678"}, cookie=cookie, csrf=True)
        check("create Vendor_A_uat", s == 200 and b.get("status") == "success", f"{s} {b}")
        sid = b.get("id")
        s, _, b = req("PATCH", f"/api/suppliers/{sid}", {"name": "Vendor_B_uat"}, cookie=cookie, csrf=True)
        check("rename to Vendor_B_uat", s == 200 and b.get("status") == "success", f"{s} {b}")
        s, _, b = req("PATCH", f"/api/suppliers/{sid}", {"active": 0}, cookie=cookie, csrf=True)
        check("deactivate via PATCH active=0", s == 200 and b.get("status") == "success", f"{s} {b}")
        s, _, b = req("GET", f"/api/suppliers?include_inactive=1&q=Vendor_B_uat", cookie=cookie)
        rows = [x for x in b.get("data", []) if x["id"] == sid]
        check("showInactive: name still Vendor_B_uat, active=0, phone kept",
              s == 200 and len(rows) == 1 and rows[0]["name"] == "Vendor_B_uat"
              and rows[0]["active"] == 0 and rows[0].get("contact_phone") == "12345678",
              f"{s} {rows}")
        s, _, b = req("GET", f"/api/suppliers?q=Vendor_B_uat", cookie=cookie)
        check("default list excludes deactivated", s == 200 and not any(x["id"] == sid for x in b.get("data", [])), f"{s}")

        # AC2: PATCH notes omitting name/phone -> unchanged (omit semantics)
        s, _, b = req("PATCH", f"/api/suppliers/{sid}", {"notes": "uat-note-1"}, cookie=cookie, csrf=True)
        check("patch notes (omit name/phone) 200", s == 200, f"{s} {b}")
        s, _, b = req("GET", f"/api/suppliers?include_inactive=1&q=Vendor_B_uat", cookie=cookie)
        r0 = [x for x in b["data"] if x["id"] == sid][0]
        check("omit semantics: name/phone unchanged, notes set",
              r0["name"] == "Vendor_B_uat" and r0.get("contact_phone") == "12345678" and r0.get("notes") == "uat-note-1", f"{r0}")

        # AC2b: explicit clear of notes/phone -> cleared, name unchanged
        s, _, b = req("PATCH", f"/api/suppliers/{sid}", {"notes": "", "contact_phone": ""}, cookie=cookie, csrf=True)
        s, _, b = req("GET", f"/api/suppliers?include_inactive=1&q=Vendor_B_uat", cookie=cookie)
        r0 = [x for x in b["data"] if x["id"] == sid][0]
        check("explicit clear: notes/phone cleared, name kept",
              r0["name"] == "Vendor_B_uat" and r0.get("notes", "") == "" and r0.get("contact_phone", "") == "", f"{r0}")

        # Negative: explicit invalid name updates rejected, name unchanged
        for bad in [{"name": ""}, {"name": "   "}, {"name": None}]:
            s, _, b = req("PATCH", f"/api/suppliers/{sid}", bad, cookie=cookie, csrf=True)
            check(f"PATCH name={bad['name']!r} -> 400", s == 400, f"{s} {b}")
        s, _, b = req("GET", f"/api/suppliers?include_inactive=1&q=Vendor_B_uat", cookie=cookie)
        r0 = [x for x in b["data"] if x["id"] == sid][0]
        check("after rejected updates name intact", r0["name"] == "Vendor_B_uat", f"{r0['name']}")

        # Negative: empty name creation rejected
        for bad in ["", "   "]:
            s, _, b = req("POST", "/api/suppliers", {"name": bad}, cookie=cookie, csrf=True)
            check(f"create name={bad!r} -> 400", s == 400, f"{s} {b}")

        # Negative: unauthenticated (empty Authorization, no cookie) -> 401
        s, _, b = req("GET", "/api/suppliers", extra={"Authorization": ""})
        check("no cookie + empty Authorization -> 401", s == 401, f"{s} {b}")

        # CSRF: cookie but no X-Requested-With on write -> 403
        s, _, b = req("PATCH", f"/api/suppliers/{sid}", {"notes": "no-csrf"}, cookie=cookie)
        check("write without CSRF header -> 403", s == 403, f"{s} {b}")

        # Tenant isolation: staff account (tenant=default) attempting PATCH owner's supplier
        # staff is same tenant 'default' per auth.py; verify staff role denial (403) and cross-tenant visibility
        s, h, b = req("POST", "/api/auth/login", {"email": "staff@demo.hk", "password": "staff123"})
        scookie = h.get("Set-Cookie", "").split(";")[0]
        check("staff login", s == 200, f"{s}")
        s, _, b = req("PATCH", f"/api/suppliers/{sid}", {"active": 1}, cookie=scookie, csrf=True)
        check("staff PATCH -> 403 (role boundary)", s == 403, f"{s} {b}")
        s, _, b = req("GET", "/api/suppliers?include_inactive=1", cookie=scookie)
        check("staff list ok", s == 200, f"{s}")
        # owner baseline unchanged except our probe row
        s, _, b = req("GET", "/api/suppliers?include_inactive=1", cookie=cookie)
        after_ids = sorted(x["id"] for x in b.get("data", []))
        check("owner baseline drift check (only probe row added)",
              set(after_ids) - set(base_ids) == {sid}, f"added={[i for i in after_ids if i not in base_ids]}")

        # SQL direct read
        con = sqlite3.connect(DB)
        row = con.execute("SELECT id, name, active, contact_phone, notes FROM suppliers WHERE id=?", (sid,)).fetchone()
        check("SQL: name retained in DB", row is not None and row[1] == "Vendor_B_uat" and row[2] == 0, f"{row}")
        con.close()
    finally:
        p.send_signal(signal.SIGTERM)
        try: p.wait(timeout=10)
        except Exception: p.kill()
        # port release check
        time.sleep(1)
        listening = []
        try:
            out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout
            listening = [l for l in out.splitlines() if ":1593" in l and "LISTEN" in l]
        except Exception: pass
        check("port 1593x released", not listening, f"{listening}")

    npass = sum(1 for r in results if r["pass"])
    print(f"TOTAL {npass}/{len(results)} PASS")
    with open("/tmp/uat028-uat/logs/probe-result.json", "w") as f:
        json.dump({"results": results, "summary": f"{npass}/{len(results)} PASS", "port": PORT, "db": DB}, f, indent=2)
    return 0 if npass == len(results) else 1

if __name__ == "__main__":
    sys.exit(main())

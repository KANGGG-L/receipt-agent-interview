#!/usr/bin/env python3
"""UAT-029 independent UAT real-browser walk (port 15903, AUTH_ENABLED=1, isolated DB /tmp/uat029-uat/uat-ui.db).
User-perspective: real login, open review, save, reopen, approve; negative UI check; network capture.
"""
import json, os, secrets, signal, sqlite3, subprocess, sys, time
from pathlib import Path
from urllib import request, error
from playwright.sync_api import sync_playwright

ROOT = "/home/agentuser/receipt-agent-interview/demo"
PORT = 15903
BASE = f"http://127.0.0.1:{PORT}"
DB = "/tmp/uat029-uat/uat-ui.db"
ART = Path("/home/agentuser/receipt-agent-interview/docs/handoff/artifacts/uat029-uat")
UI = ART / "logs"
LOG = "/tmp/uat029-uat/logs/ui-server.log"

R = {"steps": [], "net": []}
def step(n, ok, d=None):
    R["steps"].append({"name": n, "pass": bool(ok), "detail": str(d)[:600]})
    print(("PASS" if ok else "FAIL"), n, "|", str(d)[:400], flush=True)

def q(sql, a=()):
    c = sqlite3.connect(DB)
    try: return c.execute(sql, a).fetchall()
    finally: c.close()

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
            with request.urlopen(BASE + "/api/health", timeout=5) as r:
                if r.status == 200: return p
        except error.HTTPError:
            return p
        except Exception: pass
        time.sleep(0.5)
    p.terminate(); raise RuntimeError("startup timeout")

TOASTS = "() => [...document.querySelectorAll('.toast-container > *')].map(e=>e.innerText.trim()).filter(Boolean)"
def clear_toasts(pg): pg.evaluate("() => document.querySelectorAll('.toast-container > *').forEach(e=>e.remove())")
def toasts(pg): return " | ".join(pg.evaluate(TOASTS))
def nreq(): return len(R["net"])
def snap(pg, n):
    p = UI / n; pg.screenshot(path=str(p)); R.setdefault("hashes", {})[n] = snap_md5(p)
def snap_md5(p):
    import hashlib; return hashlib.md5(p.read_bytes()).hexdigest()

def main():
    p = start_server()
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, executable_path="/usr/bin/google-chrome")
            ctx = br.new_context(viewport={"width": 1440, "height": 900})
            pg = ctx.new_page()
            pg.on("request", lambda r: R["net"].append({"req": r.url, "m": r.method,
                   "body": r.post_data, "csrf": r.headers.get("x-requested-with")})
                  if ("save_edited" in r.url or "/approve" in r.url or "/reverse" in r.url) else None)
            pg.on("response", lambda r: R["net"].append({"resp": r.url, "status": r.status})
                  if ("save_edited" in r.url or "/approve" in r.url or "/reverse" in r.url) else None)

            # real login
            pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000); pg.wait_for_timeout(2000)
            pg.wait_for_selector("#authLoginEmail", timeout=20000)
            pg.fill("#authLoginEmail", "boss@demo.hk"); pg.fill("#authLoginPassword", "boss123")
            pg.click("#authLoginSubmit"); pg.wait_for_timeout(2500)
            s_me = pg.evaluate("async () => (await (await fetch('/api/auth/me')).json())")
            step("U1 real login boss@demo.hk, /api/auth/me identity_source=cookie",
                 s_me.get("identity_source") == "cookie" and not s_me.get("demo_identity"), str(s_me)[:200])
            snap(pg, "ui-01-after-login.png")

            # create credit_note receipt via UI: new manual entry, doc_form=credit_note, negative amount
            clear_toasts(pg)
            pg.locator("#btnNewManualEntry").click()
            pg.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000)
            pg.wait_for_timeout(600)
            vis = pg.evaluate("""() => {
                const el = document.getElementById('btnNewManualEntry');
                const inp = document.getElementById('btnSaveReview');
                const r = inp.getBoundingClientRect();
                return {mvis: !!el && el.offsetParent !== null, save_w: r.width, save_disabled: inp.disabled,
                        tab: !!document.querySelector('#tab-scan.active')};
            }""")
            step("U0-diag save button visible/enabled after manual entry", vis["save_w"] > 0 and not vis["save_disabled"], str(vis))
            pg.fill("#inpSupplier", "UAT029_UI_Credit")
            pg.fill("#inpDate", "2026-10-18")
            pg.locator("#inpSettlementType").select_option("cash")
            pg.locator("#inpDocForm").select_option("credit_note")
            tr = pg.locator("#itemTableBody tr").first
            tr.locator(".inp-name").fill("Credit_UI_Item")
            tr.locator(".inp-qty").fill("1")
            tr.locator(".inp-unit").fill("pcs")
            tr.locator(".inp-price").fill("10")
            tr.locator(".inp-amount").fill("-10")
            tr.locator(".inp-amount").dispatch_event("input")
            pg.locator("#inpTotal").click(); pg.locator("#inpTotal").fill("-10"); pg.wait_for_timeout(600)
            st = pg.evaluate("() => {const b=calculateReceiptIssues(collectReviewFormData()).filter(i=>i.type==='blocker');"
                             "return {blockers:b.map(i=>i.id), total: document.getElementById('inpTotal').value}}")
            step("U2 UI form credit_note total=-10: no err_total blocker (defect core)",
                 "err_total" not in st["blockers"], str(st))
            snap(pg, "ui-02-credit-form.png")

            # save via real UI click
            n0 = nreq()
            pg.locator("#btnSaveReview").click()
            ri = None
            try:
                ri = pg.wait_for_response(lambda r: "save_edited" in r.url and r.request.method == "POST", timeout=20000)
            except Exception as e:
                step("U3-diag", False, f"no save_edited response: {e}; toasts={toasts(pg)} net={R['net'][n0:]}")
            pg.wait_for_timeout(1500)
            if ri is not None:
                body = ri.value.json(); rid = body.get("receipt_id")
                payloads = [x for x in R["net"] if "req" in x and "save_edited" in x["req"]]
                pl = json.loads(payloads[-1]["body"]) if payloads else {}
                step("U3 UI save click -> real POST save_edited 200, payload total=-10",
                     ri.value.status == 200 and body.get("status") == "success" and float(pl.get("total_amount", 0)) == -10,
                     f"status={ri.value.status} rid={rid} toast={toasts(pg)} csrf={payloads[-1].get('csrf') if payloads else None}")
            snap(pg, "ui-03-saved.png")

            # reopen from queue, then approve via UI
            pg.goto(BASE + "/", wait_until="domcontentloaded"); pg.wait_for_timeout(2000)
            pg.locator("button.sidebar-btn[data-target='tab-scan']").click(); pg.wait_for_timeout(1000)
            pg.wait_for_selector(f"#workbenchQueueBody tr[data-receipt-id='{rid}']", timeout=20000)
            pg.locator(f"#workbenchQueueBody tr[data-receipt-id='{rid}'] button:has-text('继续复核')").click()
            pg.wait_for_timeout(1500)
            reopen_total = pg.locator("#inpTotal").input_value()
            step("U4 reopen from queue: total shows -10.00", abs(float(reopen_total) + 10) < 1e-9, f"total={reopen_total}")
            snap(pg, "ui-04-reopened.png")
            clear_toasts(pg)
            pg.locator("#btnApproveReceipt").click()
            pg.wait_for_selector("#modalApproveConfirmDialog:not(.hide)", timeout=10000)
            ai = pg.expect_response(lambda r: r.url.endswith(f"/api/receipt/{rid}/approve"), timeout=20000)
            with ai:
                pg.locator("#btnExecuteApproveConfirm").click()
            pg.wait_for_timeout(1200)
            dbrow = q("select status,total_amount,version from receipts where id=?", (rid,))
            step("U5 UI approve -> 200; DB approved total=-10 (full chain UI save->reopen->approve)",
                 ai.value.status == 200 and dbrow and dbrow[0][0] == "approved" and dbrow[0][1] == -10,
                 f"status={ai.value.status} db={dbrow} toast={toasts(pg)}")
            snap(pg, "ui-05-approved.png")

            # negative UI: plain printed manual entry with negative total blocked client-side
            pg.goto(BASE + "/", wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
            pg.locator("button.sidebar-btn[data-target='tab-scan']").click(); pg.wait_for_timeout(800)
            clear_toasts(pg); n0 = nreq()
            pg.locator("#btnNewManualEntry").click(); pg.wait_for_timeout(800)
            pg.fill("#inpSupplier", "UAT029_UI_NegPurchase")
            pg.fill("#inpDate", "2026-10-18")
            pg.locator("#inpSettlementType").select_option("cash")
            tr = pg.locator("#itemTableBody tr").first
            tr.locator(".inp-name").fill("P_Item")
            tr.locator(".inp-qty").fill("1"); tr.locator(".inp-price").fill("10")
            tr.locator(".inp-amount").fill("-10"); tr.locator(".inp-amount").dispatch_event("input")
            pg.locator("#inpTotal").click(); pg.locator("#inpTotal").fill("-10"); pg.wait_for_timeout(600)
            st = pg.evaluate("() => calculateReceiptIssues(collectReviewFormData()).filter(i=>i.type==='blocker').map(i=>i.id)")
            pg.locator("#btnSaveReview").click(); pg.wait_for_timeout(1500)
            tt = toasts(pg)
            step("U6 printed negative total blocked in UI (no save request, blocker present)",
                 "err_total" in st and nreq() == n0, f"blockers={st} toast={tt} net_delta={nreq()-n0}")
            snap(pg, "ui-06-neg-purchase-blocked.png")

            # double negative UI check: credit_note qty -2 price -5
            pg.goto(BASE + "/", wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
            pg.locator("button.sidebar-btn[data-target='tab-scan']").click(); pg.wait_for_timeout(800)
            clear_toasts(pg); n0 = nreq()
            pg.locator("#btnNewManualEntry").click(); pg.wait_for_timeout(800)
            pg.fill("#inpSupplier", "UAT029_UI_DoubleNeg")
            pg.fill("#inpDate", "2026-10-18")
            pg.locator("#inpSettlementType").select_option("cash")
            pg.locator("#inpDocForm").select_option("credit_note")
            tr = pg.locator("#itemTableBody tr").first
            tr.locator(".inp-name").fill("DN_Item")
            tr.locator(".inp-qty").fill("-2"); tr.locator(".inp-price").fill("-5")
            tr.locator(".inp-amount").fill("10"); tr.locator(".inp-amount").dispatch_event("input")
            pg.locator("#inpTotal").click(); pg.locator("#inpTotal").fill("10"); pg.wait_for_timeout(600)
            st = pg.evaluate("() => calculateReceiptIssues(collectReviewFormData()).filter(i=>i.type==='blocker').map(i=>i.id)")
            pg.locator("#btnSaveReview").click(); pg.wait_for_timeout(1500)
            tt = toasts(pg)
            step("U7 credit_note double-negative (qty<0 & price<0) blocked in UI",
                 any("err_item" in b or "err_qty" in b for b in st) or ("不能同时" in tt) or nreq() == n0,
                 f"blockers={st} toast={tt} net_delta={nreq()-n0}")
            snap(pg, "ui-07-double-neg-blocked.png")

            # doc form options: no new credit type
            opts = pg.evaluate("() => [...document.querySelectorAll('#inpDocForm option')].map(o=>o.value)")
            step("U8 doc_form options limited to known enums (no new credit type)",
                 set(opts) <= {"", "printed_delivery_note", "ncr_handwritten", "thermal", "weigh_slip",
                               "correction_note", "credit_note", "monthly_statement", "manual_entry"}, str(opts))

            R["net_log"] = R["net"]
            json.dump(R, open("/tmp/uat029-uat/logs/ui-result.json", "w"), ensure_ascii=False, indent=1)
            br.close()
    finally:
        p.send_signal(signal.SIGTERM); p.wait(timeout=15)
        out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True).stdout
        print("port listeners after:", out.count(f":{PORT} "))

if __name__ == "__main__":
    main()

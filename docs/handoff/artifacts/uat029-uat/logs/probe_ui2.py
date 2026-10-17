#!/usr/bin/env python3
"""UAT-029 independent UAT real-browser walk v2 (port 15903, AUTH_ENABLED=1, isolated DB /tmp/uat029-uat/uat-ui2.db).
Receipt-centric path (same as QA walk): seed a credit_note receipt via API (real signed-in session),
open it in the review UI from the queue, save via real button click, reopen, approve via UI.
Negative UI: plain printed entry with negative total must be blocked client-side (no network write).
"""
import json, os, secrets, signal, sqlite3, subprocess, sys, time
from pathlib import Path
from urllib import request, error
from playwright.sync_api import sync_playwright

ROOT = "/home/agentuser/receipt-agent-interview/demo"
PORT = 15903
BASE = f"http://127.0.0.1:{PORT}"
DB = "/tmp/uat029-uat/uat-ui2.db"
ART = Path("/home/agentuser/receipt-agent-interview/docs/handoff/artifacts/uat029-uat")
UI = ART / "logs"
LOG = "/tmp/uat029-uat/logs/ui-server2.log"

R = {"steps": [], "net": []}
def step(n, ok, d=None):
    R["steps"].append({"name": n, "pass": bool(ok), "detail": str(d)[:700]})
    print(("PASS" if ok else "FAIL"), n, "|", str(d)[:450], flush=True)

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

def snap(pg, n):
    import hashlib
    p = UI / n; pg.screenshot(path=str(p)); R.setdefault("hashes", {})[n] = hashlib.md5(p.read_bytes()).hexdigest()

TOASTS = "() => [...document.querySelectorAll('.toast-container > *')].map(e=>e.innerText.trim()).filter(Boolean)"
def toasts(pg): return " | ".join(pg.evaluate(TOASTS))
def clear_toasts(pg): pg.evaluate("() => document.querySelectorAll('.toast-container > *').forEach(e=>e.remove())")
def nreq(): return len(R["net"])

def main():
    p = start_server()
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, executable_path="/usr/bin/google-chrome")
            ctx = br.new_context(viewport={"width": 1440, "height": 900})
            pg = ctx.new_page()
            pg.on("request", lambda r: R["net"].append({"req": r.url, "m": r.method,
                   "body": r.post_data, "csrf": r.headers.get("x-requested-with")})
                  if ("save_edited" in r.url or "/approve" in r.url) else None)
            pg.on("response", lambda r: R["net"].append({"resp": r.url, "status": r.status})
                  if ("save_edited" in r.url or "/approve" in r.url) else None)

            # real login
            pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000); pg.wait_for_timeout(2000)
            pg.wait_for_selector("#authLoginEmail", timeout=20000)
            pg.fill("#authLoginEmail", "boss@demo.hk"); pg.fill("#authLoginPassword", "boss123")
            pg.click("#authLoginSubmit"); pg.wait_for_timeout(2500)
            s_me = pg.evaluate("async () => (await (await fetch('/api/auth/me')).json())")
            step("U1 real login, identity_source=cookie", s_me.get("identity_source") == "cookie", str(s_me)[:160])
            snap(pg, "ui-01-after-login.png")

            # seed TWO credit_note receipts via in-page fetch (signed-in cookie session, CSRF header like the app)
            seeds = pg.evaluate("""async () => {
                const mk = (total, qty) => ({
                    receipt_id: null, source: 'manual', supplier_name: 'UAT029_UI_Credit',
                    date: '2026-10-18', total_amount: total, doc_form: 'credit_note',
                    settlement_type: 'cash', currency: 'HKD',
                    items: [{ name: 'Credit_UI_Item', quantity: qty, unit: 'pcs', unit_price: 10, amount: total }]
                });
                const out = [];
                for (const b of [mk(-10, 1), mk(-20, -2)]) {
                    const r = await fetch('/api/save_edited', { method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                        body: JSON.stringify(b) });
                    out.push(r.status);
                }
                return out;
            }""")
            step("U1b seeded two credit_note receipts via signed-in session (200/200)", seeds == [200, 200], str(seeds))
            rid1, rid2 = 1, 2

            # open receipt #1 from the queue (识别复核 tab)
            pg.goto(BASE + "/", wait_until="domcontentloaded"); pg.wait_for_timeout(2000)
            pg.locator("button.sidebar-btn[data-target='tab-scan']").click(); pg.wait_for_timeout(1000)
            pg.wait_for_selector(f"#workbenchQueueBody tr[data-receipt-id='{rid1}']", timeout=20000)
            pg.locator(f"#workbenchQueueBody tr[data-receipt-id='{rid1}'] button:has-text('继续复核')").click()
            pg.wait_for_timeout(1500)
            form = pg.evaluate("""() => ({ total: document.getElementById('inpTotal').value,
                docForm: document.getElementById('inpDocForm').value,
                blockers: calculateReceiptIssues(collectReviewFormData()).filter(i=>i.type==='blocker').map(i=>i.id) })""")
            step("U2 reopen credit_note #1 in review UI: total -10.00, docForm credit_note, no err_total (defect core)",
                 abs(float(form["total"]) + 10) < 1e-9 and form["docForm"] == "credit_note"
                 and "err_total" not in form["blockers"], str(form))
            snap(pg, "ui-02-credit-reopened.png")

            # save via real button click (reopened-receipt path)
            clear_toasts(pg)
            pg.locator("#btnSaveReview").click()
            try:
                with pg.expect_response(lambda r: "save_edited" in r.url and r.request.method == "POST", timeout=20000) as ri_ctx:
                    pass
                ri = ri_ctx
                body = ri.value.json()
                payloads = [x for x in R["net"] if "req" in x and "save_edited" in x["req"]]
                pl = json.loads(payloads[-1]["body"]) if payloads else {}
                step("U3 UI save click -> real POST /api/save_edited 200 (total_amount -10, X-Requested-With present)",
                     ri.value.status == 200 and body.get("status") == "success"
                     and float(pl.get("total_amount", 0)) == -10 and bool(payloads[-1].get("csrf")),
                     f"status={ri.value.status} toast={toasts(pg)} csrf={payloads[-1].get('csrf')}")
            except Exception as e:
                step("U3 UI save click -> no save_edited request", False, f"{e} toast={toasts(pg)}")
            snap(pg, "ui-03-saved.png")

            # reopen again, then approve via UI confirm dialog
            pg.goto(BASE + "/", wait_until="domcontentloaded"); pg.wait_for_timeout(2000)
            pg.locator("button.sidebar-btn[data-target='tab-scan']").click(); pg.wait_for_timeout(1000)
            pg.wait_for_selector(f"#workbenchQueueBody tr[data-receipt-id='{rid1}']", timeout=20000)
            pg.locator(f"#workbenchQueueBody tr[data-receipt-id='{rid1}'] button:has-text('继续复核')").click()
            pg.wait_for_timeout(1500)
            clear_toasts(pg)
            pg.locator("#btnApproveReceipt").click()
            pg.wait_for_selector("#modalApproveConfirmDialog:not(.hide)", timeout=10000)
            try:
                with pg.expect_response(lambda r: r.url.endswith(f"/api/receipt/{rid1}/approve"), timeout=20000) as ai_ctx:
                    pg.locator("#btnExecuteApproveConfirm").click()
                ai = ai_ctx
                dbrow = q("select status,total_amount,version from receipts where id=?", (rid1,))
                step("U4 UI approve -> POST approve 200; DB approved total=-10 (full UI chain)",
                     ai.value.status == 200 and dbrow and dbrow[0][0] == "approved" and dbrow[0][1] == -10,
                     f"status={ai.value.status} db={dbrow} toast={toasts(pg)}")
            except Exception as e:
                step("U4 UI approve -> no approve request", False, f"{e} toast={toasts(pg)}")
            snap(pg, "ui-04-approved.png")

            # negative UI: printed doc with negative total blocked client-side, no network write
            pg.goto(BASE + "/", wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
            pg.locator("button.sidebar-btn[data-target='tab-scan']").click(); pg.wait_for_timeout(800)
            clear_toasts(pg); n0 = nreq()
            pg.locator("#btnNewManualEntry").click()
            pg.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000); pg.wait_for_timeout(600)
            pg.fill("#inpSupplier", "UAT029_UI_NegPurchase")
            pg.fill("#inpDate", "2026-10-18")
            pg.locator("#inpSettlementType").select_option("cash")
            tr = pg.locator("#itemTableBody tr").first
            tr.locator(".inp-name").fill("P_Item")
            tr.locator(".inp-qty").fill("1"); tr.locator(".inp-price").fill("10")
            tr.locator(".inp-amount").fill("-10"); tr.locator(".inp-amount").dispatch_event("input")
            pg.locator("#inpTotal").click(); pg.locator("#inpTotal").fill("-10"); pg.wait_for_timeout(600)
            blockers = pg.evaluate("() => calculateReceiptIssues(collectReviewFormData()).filter(i=>i.type==='blocker').map(i=>i.id)")
            pg.locator("#btnSaveReview").click(); pg.wait_for_timeout(1500)
            tt = toasts(pg)
            step("U5 printed negative total blocked in UI (err_total + no network write)",
                 "err_total" in blockers and nreq() == n0, f"blockers={blockers} toast={tt} net_delta={nreq()-n0}")
            snap(pg, "ui-05-neg-purchase-blocked.png")

            # negative UI: credit_note double-negative blocked at issue gate
            clear_toasts(pg); n0 = nreq()
            pg.goto(BASE + "/", wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
            pg.locator("button.sidebar-btn[data-target='tab-scan']").click(); pg.wait_for_timeout(800)
            clear_toasts(pg)
            pg.locator("#btnNewManualEntry").click()
            pg.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000); pg.wait_for_timeout(600)
            pg.fill("#inpSupplier", "UAT029_UI_DoubleNeg")
            pg.fill("#inpDate", "2026-10-18")
            pg.locator("#inpSettlementType").select_option("cash")
            pg.locator("#inpDocForm").select_option("credit_note")
            tr = pg.locator("#itemTableBody tr").first
            tr.locator(".inp-name").fill("DN_Item")
            tr.locator(".inp-qty").fill("-2"); tr.locator(".inp-price").fill("-5")
            tr.locator(".inp-amount").fill("10"); tr.locator(".inp-amount").dispatch_event("input")
            pg.locator("#inpTotal").click(); pg.locator("#inpTotal").fill("10"); pg.wait_for_timeout(600)
            blockers2 = pg.evaluate("() => calculateReceiptIssues(collectReviewFormData()).filter(i=>i.type==='blocker').map(i=>i.id)")
            step("U6 credit_note double-negative (qty<0 & price<0) blocked at issue gate (err_item_num_0)",
                 "err_item_num_0" in blockers2, f"blockers={blockers2}")
            snap(pg, "ui-06-double-neg-blocked.png")

            # doc form options: no new credit type
            opts = pg.evaluate("() => [...document.querySelectorAll('#inpDocForm option')].map(o=>o.value)")
            step("U7 doc_form options limited to known enums (no new credit type)",
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

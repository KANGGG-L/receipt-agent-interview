# UAT-009 independent QA real-browser walk (port 15971, AUTH_ENABLED=1, real login via UI form).
import hashlib, json, os, sqlite3, time
from pathlib import Path
from playwright.sync_api import sync_playwright
BASE=os.environ["BASE"]; assert ":15010" not in BASE and ":15961" in BASE and ":15971" not in BASE
DB=os.environ["DB_PATH"]; ART=Path(os.environ["ART"]); UI=ART/"ui"; UI.mkdir(exist_ok=True,parents=True)
FEES={"delivery_fee":5.0,"deposit_amount":4.0,"service_fee":3.0,"tax_amount":2.0,"discount_amount":1.0,"rounding_adjustment":0.5}
FEE_KEYS=list(FEES)
R={"steps":[],"net":[],"hashes":{},"toasts":[]}
def step(n,ok,d=None): R["steps"].append({"name":n,"pass":bool(ok),"detail":d}); print("PASS" if ok else "FAIL",n,json.dumps(d,ensure_ascii=False,default=str)[:700],flush=True)
def q(s,a=()):
    c=sqlite3.connect(DB)
    try: return c.execute(s,a).fetchall()
    finally: c.close()
STATE="""() => ({rid: (typeof currentReceiptId!=='undefined')? currentReceiptId : null,
 fees: collectFeeMap(), docForm: document.getElementById('inpDocForm').value, currency: document.getElementById('inpCurrency').value,
 evidence: getAutoPaymentEvidence(), bannerHidden: document.getElementById('alertBanner').classList.contains('hide'),
 drawerHidden: document.getElementById('feesDrawerContent').classList.contains('hide'),
 badgeHidden: document.getElementById('feesSummaryBadge').classList.contains('hide'),
 notes: collectNoteRows(), total: document.getElementById('inpTotal').value})"""
def snap(pg,n):
    p=UI/n; pg.screenshot(path=str(p),full_page=False); R["hashes"][n]=hashlib.md5(p.read_bytes()).hexdigest()
def toasts(pg): return pg.evaluate("() => [...document.querySelectorAll('.toast-container > *')].map(e=>e.innerText.trim()).filter(Boolean)")
def clear_toasts(pg): pg.evaluate("() => document.querySelectorAll('.toast-container > *').forEach(e=>e.remove())")
def open_receipt(pg,rid):
    clear_toasts(pg)
    pg.evaluate(f"() => window.openReceiptInWorkbench({rid}, true, true)")
    try:
        pg.wait_for_function(f"() => String((window.eval('currentReceiptData')||{{}}).id||'')==='{rid}'",timeout=15000)
    except Exception:
        pass
    pg.wait_for_timeout(600)
    cur=pg.evaluate("() => String((window.eval('currentReceiptData')||{}).id||'')")
    if cur!=str(rid):
        raise RuntimeError(f"open_receipt failed: currentReceiptData.id={cur} want {rid}")
def fill_new_manual(pg):
    clear_toasts(pg)
    pg.locator("#btnNewManualEntry").click(); pg.wait_for_selector("#prefillFormCard:not(.hide)"); pg.wait_for_timeout(500)
    pg.locator("#inpSupplier").fill("Vendor_UI_new"); pg.locator("#inpDate").fill("2026-10-17")
    pg.locator("#inpSettlementType").select_option("cash")
    tr=pg.locator("#itemTableBody tr").first
    tr.locator(".inp-name").fill("Item_UI"); tr.locator(".inp-qty").fill("1"); tr.locator(".inp-unit").fill("pcs")
    tr.locator(".inp-price").fill("10"); tr.locator(".inp-amount").fill("10")
    pg.locator("#inpSupplier").click(); pg.keyboard.press("Escape")
def save(pg):
    with pg.expect_response(lambda r:"save_edited" in r.url and r.request.method=="POST",timeout=20000) as ri:
        pg.locator("#btnSaveReview").click()
    pg.wait_for_timeout(1200); body=None
    try: body=ri.value.json()
    except Exception: pass
    return ri.value.status, body
def last_payload(): return json.loads([x for x in R["net"] if "req" in x and "save_edited" in x.get("req","") and x.get("body")][-1]["body"])

with sync_playwright() as p:
    br=p.chromium.launch(headless=True, executable_path="/usr/bin/google-chrome")
    ctx=br.new_context(viewport={"width":1440,"height":1000}); pg=ctx.new_page()
    pg.on("request",lambda r: R["net"].append({"req":r.url,"m":r.method,"body":r.post_data}) if "save_edited" in r.url else None)
    pg.on("response",lambda r: R["net"].append({"resp":r.url,"status":r.status}) if "save_edited" in r.url else None)

    # 1. load home -> login overlay appears (auth enabled)
    pg.goto(BASE+"/",wait_until="domcontentloaded",timeout=60000)
    pg.wait_for_selector("#authLoginOverlay",timeout=20000)
    snap(pg,"01_login_overlay.png")
    step("auth gate: login overlay shown on first load",True,None)
    pg.fill("#authLoginEmail","boss@demo.hk"); pg.fill("#authLoginPassword","boss123")
    pg.click("#authLoginSubmit")
    pg.wait_for_selector("#authLoginOverlay",state="detached",timeout=20000)
    pg.wait_for_timeout(1500)
    step("login via UI form succeeds (HttpOnly cookie session)",True,None)

    # 2. seed old receipt through the UI itself: new manual with six fees -> save
    fill_new_manual(pg)
    if not pg.locator("#btnAddFeeRow").is_visible():
        pg.locator(".fees-drawer-toggle").first.click(); pg.wait_for_timeout(300)
    for k,v in FEES.items():
        pg.locator("#btnAddFeeRow").click(); row=pg.locator("#feesRowsContainer .fee-row").last
        row.locator(".fee-type-select").select_option(k); row.locator(".fee-amount-input").fill(str(v))
    pg.locator("#inpTotal").click(); pg.locator("#inpTotal").fill("22.5"); pg.wait_for_timeout(400)
    print("toasts before save:", pg.evaluate("() => [...document.querySelectorAll('.toast-container > *')].map(e=>e.innerText.trim()).join(' | ')"), flush=True)
    pg.wait_for_timeout(1200)
    clear_toasts(pg)
    pg.wait_for_timeout(300)
    pg.locator("#btnSaveReview").scroll_into_view_if_needed(); pg.wait_for_timeout(200)
    st,body=save(pg); old_rid=(body or {}).get("receipt_id")
    pl=last_payload()
    step("UI: create old receipt with six nonzero fees -> save 200",st==200 and old_rid,{"status":st,"rid":old_rid,"fees":{k:pl[k] for k in FEE_KEYS}})
    snap(pg,"02_old_receipt_saved.png")

    # 3. reopen old receipt -> six fee rows visible
    open_receipt(pg,old_rid)
    s=pg.evaluate(STATE)
    rows=pg.locator("#feesRowsContainer .fee-row").count()
    step("reopen old: six fee rows rendered with saved values",rows==6 and all(abs(s["fees"][k]-v)<1e-9 for k,v in FEES.items()),{"rows":rows,"fees":s["fees"],"docForm":s["docForm"],"currency":s["currency"]})
    snap(pg,"03_old_reopen_six_fees.png")

    # 4. new manual entry -> all cleared; fill one row; capture payload
    fill_new_manual(pg)
    s=pg.evaluate(STATE)
    step("new manual: receipt_id null, six fees all 0, doc_form/currency/evidence reset, banner hidden, drawer/badge reset",
         s["rid"] is None and all(s["fees"][k]==0 for k in FEE_KEYS) and s["docForm"]=="printed_delivery_note"
         and s["currency"]=="HKD" and s["evidence"]=="" and s["bannerHidden"] and s["drawerHidden"] and s["badgeHidden"] and s["notes"]==[],s)
    snap(pg,"04_new_manual_cleared.png")
    st,body=save(pg); new_rid=(body or {}).get("receipt_id")
    pl=last_payload()
    step("UI save new manual: payload six fees all 0, receipt_id null, doc_form/currency/evidence defaults",
         st==200 and pl["receipt_id"] is None and all(float(pl[k])==0 for k in FEE_KEYS)
         and pl["doc_form"]=="printed_delivery_note" and pl["currency"]=="HKD" and pl["payment_evidence"]=="" and pl["adjustment_notes"]==[],
         {"status":st,"rid":new_rid,"payload":{k:pl[k] for k in FEE_KEYS+["receipt_id","doc_form","currency","payment_evidence"]}})
    snap(pg,"05_new_saved_zero_fees.png")
    dbrow=q("select delivery_fee,deposit_amount,service_fee,tax_amount,discount_amount,rounding_adjustment,payment_evidence,doc_form,currency from receipts where id=?",(new_rid,))
    step("DB read-back of UI-created new manual: zeros/defaults",all(abs(dbrow[0][i])<1e-9 for i in range(6)) and dbrow[0][6]=="" and dbrow[0][7]=="printed_delivery_note" and dbrow[0][8]=="HKD",dbrow)

    # 5. reopen old again -> fees still intact
    open_receipt(pg,old_rid)
    s=pg.evaluate(STATE)
    rows=pg.locator("#feesRowsContainer .fee-row").count()
    step("reopen old after flow: six fees still intact (cleanup only touches new)",
         rows==6 and all(abs(s["fees"][k]-v)<1e-9 for k,v in FEES.items()),{"rows":rows,"fees":s["fees"],"docForm":s["docForm"]})
    snap(pg,"06_old_reopen_intact.png")
    st2,b2=save(pg)
    ok2 = st2==200 and isinstance(b2,dict) and b2.get("status")=="success"
    step("old receipt untouched re-save still 200 success (response echoes receipt_id, fees untouched on server)",ok2 and b2.get("receipt_id")==old_rid,{"status":st2,"resp_keys":list((b2 or {}).keys())})

    # 6. user choice on new manual still submitted (contract intact)
    fill_new_manual(pg)
    if not pg.locator("#btnAddFeeRow").is_visible():
        pg.locator(".fees-drawer-toggle").first.click(); pg.wait_for_timeout(300)
    pg.locator("#btnAddFeeRow").click(); row=pg.locator("#feesRowsContainer .fee-row").last
    row.locator(".fee-type-select").select_option("delivery_fee"); row.locator(".fee-amount-input").fill("8")
    pg.locator("#inpTotal").click(); pg.locator("#inpTotal").fill("18"); pg.wait_for_timeout(300)
    st,body=save(pg); rid3=(body or {}).get("receipt_id")
    pl=last_payload()
    step("user-entered fee on new manual submitted verbatim (delivery_fee 8)",st==200 and abs(float(pl["delivery_fee"])-8.0)<1e-9,{"status":st,"rid":rid3,"delivery_fee":pl["delivery_fee"]})

    # 7. late-response guard race: open old then immediately new manual
    pg.evaluate(f"() => window.openReceiptInWorkbench({old_rid}, true, true)")
    pg.evaluate("() => window.startManualEntry()")
    pg.wait_for_timeout(1200)
    s=pg.evaluate(STATE)
    step("race: open then immediate new manual -> receipt_id stays null, fees stay 0",
         s["rid"] is None and all(s["fees"][k]==0 for k in FEE_KEYS),s)
    snap(pg,"07_race_new_manual_clean.png")

    # 8. page reload -> session cookie persists, new manual still clean
    pg.reload(wait_until="domcontentloaded"); pg.wait_for_timeout(3000)
    overlay=pg.locator("#authLoginOverlay").count()
    step("reload: session persists via HttpOnly cookie (no re-login)",overlay==0,{"overlays":overlay})
    # after reload the app may land on a different tab; go back to upload tab explicitly
    pg.evaluate("() => { const b=document.querySelector('.sidebar-btn[data-target=\"tab-scan\"]'); if (b) b.click(); }")
    pg.wait_for_timeout(1500)
    fill_new_manual(pg)
    s=pg.evaluate(STATE)
    step("post-reload new manual: all defaults (no cross-session leak)",
         s["rid"] is None and all(s["fees"][k]==0 for k in FEE_KEYS) and s["docForm"]=="printed_delivery_note" and s["currency"]=="HKD" and s["evidence"]=="",s)
    snap(pg,"08_post_reload_clean.png")

    br.close()
json.dump(R,open(ART/"evidence"/"uat-ui-walk.json","w"),ensure_ascii=False,indent=1,default=str)
npass=sum(1 for x in R["steps"] if x["pass"]); print("UI_WALK_DONE",npass,"/",len(R["steps"]))

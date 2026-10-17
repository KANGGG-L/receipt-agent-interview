# UAT-008 independent UAT real-browser walk (port 15380). Written fresh for UAT.
import hashlib, json, os, sqlite3, time
from pathlib import Path
from urllib import request, error
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:15380"; DB="/workspace/.uat008-uat-env/uat.db"
ART=Path("/workspace/receipt-agent-interview/docs/handoff/artifacts/uat008-uat"); UI=ART/"ui"
TA,TB="uat008uat-a","uat008uat-b"
FEES=[("delivery_fee",5),("deposit_amount",4),("service_fee",3),("tax_amount",2),("discount_amount",1),("rounding_adjustment",0.5)]
R={"steps":[],"net":[],"hashes":{},"toasts":[]}
def step(n,ok,d=None): R["steps"].append({"name":n,"pass":bool(ok),"detail":d}); print("PASS" if ok else "FAIL",n,json.dumps(d,ensure_ascii=False,default=str)[:700],flush=True)
def q(s,a=()):
    c=sqlite3.connect(DB)
    try: return c.execute(s,a).fetchall()
    finally: c.close()
def api(m,p,b=None,t=TA,role="owner"):
    r=request.Request(BASE+p,data=json.dumps(b).encode() if b is not None else None,method=m,headers={"X-Role":role,"X-Tenant-Id":t,"Content-Type":"application/json"})
    try:
        with request.urlopen(r,timeout=60) as x: return x.status,json.loads(x.read() or b"null")
    except error.HTTPError as e:
        raw=e.read().decode()
        try: return e.code,json.loads(raw)
        except Exception: return e.code,raw
STATE="""() => {const h=document.getElementById('totalOverrideHint');
 return {total: document.getElementById('inpTotal').value,
 hint: h? h.textContent.trim():'', hintVisible: !!h && !h.classList.contains('hide'),
 blockers: calculateReceiptIssues(collectReviewFormData()).filter(i=>i.type==='blocker').map(i=>i.id+': '+(i.guide||i.message||'')),
 auto: computeItemsAutoTotal(), receiptId: (typeof currentReceiptId!=='undefined')? currentReceiptId : null}}"""
TOASTS="() => [...document.querySelectorAll('.toast-container > *')].map(e=>e.innerText.trim()).filter(Boolean)"
def snap(pg,n):
    p=UI/n; pg.screenshot(path=str(p),full_page=False); R["hashes"][n]=hashlib.md5(p.read_bytes()).hexdigest()
def toasts(pg): t=pg.evaluate(TOASTS); R["toasts"].append(t); return " | ".join(t)
def clear_toasts(pg): pg.evaluate("() => document.querySelectorAll('.toast-container > *').forEach(e=>e.remove())")
def nreq(): return len([x for x in R["net"] if "req" in x])
def fill_manual(pg, name, actual, doc_form=None, amount=None):
    pg.locator("#btnNewManualEntry").click(); pg.wait_for_selector("#prefillFormCard:not(.hide)"); pg.wait_for_timeout(500)
    pg.locator("#inpSupplier").fill("Vendor_A"); pg.locator("#inpDate").fill("2026-10-17")
    pg.locator("#inpSettlementType").select_option("cash")
    if doc_form: pg.locator("#inpDocForm").select_option(doc_form)
    tr=pg.locator("#itemTableBody tr").first
    tr.locator(".inp-name").fill(name); tr.locator(".inp-qty").fill("1"); tr.locator(".inp-unit").fill("pcs")
    tr.locator(".inp-price").fill("10")
    if amount is not None: tr.locator(".inp-amount").fill(str(amount)); tr.locator(".inp-amount").dispatch_event("input")
    if actual is not None: tr.locator(".inp-actual-qty").fill(actual)
    pg.locator("#inpSupplier").click(); pg.keyboard.press("Escape")
    if not pg.locator("#btnAddFeeRow").is_visible(): pg.locator(".fees-drawer-toggle").first.click(); pg.wait_for_timeout(300)
    for key,val in FEES:
        pg.locator("#btnAddFeeRow").click(); row=pg.locator("#feesRowsContainer .fee-row").last
        row.locator(".fee-type-select").select_option(key); row.locator(".fee-amount-input").fill(str(val))
    pg.wait_for_timeout(400)
def type_total(pg,v):
    t=pg.locator("#inpTotal"); t.click(); t.fill(v); pg.wait_for_timeout(500)
def save(pg):
    with pg.expect_response(lambda r:"save_edited" in r.url and r.request.method=="POST",timeout=20000) as ri: pg.locator("#btnSaveReview").click()
    pg.wait_for_timeout(1500); b=None
    try: b=ri.value.json()
    except Exception: pass
    return ri.value.status,b
def last_payload(): return json.loads([x for x in R["net"] if "req" in x and "save_edited" in x["req"]][-1]["body"])
def home(pg):
    pg.goto(BASE+"/",wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
    pg.locator("button.sidebar-btn[data-target='tab-scan']").click(); pg.wait_for_timeout(800)
def open_from_queue(pg,rid):
    home(pg); pg.wait_for_selector(f"#workbenchQueueBody tr[data-receipt-id='{rid}']",timeout=20000)
    pg.locator(f"#workbenchQueueBody tr[data-receipt-id='{rid}'] button:has-text('继续复核')").click()
    pg.wait_for_function(f"() => String((typeof currentReceiptData!=='undefined' && currentReceiptData && currentReceiptData.id) || (typeof currentReceiptId!=='undefined' ? currentReceiptId : ''))==='{rid}' && document.querySelector('#itemTableBody tr .inp-actual-qty')",timeout=20000)
    pg.wait_for_timeout(1000)
def approve_ui(pg,rid,expect_request=True):
    clear_toasts(pg); n0=nreq()
    pg.locator("#btnApproveReceipt").click()
    if not expect_request:
        pg.wait_for_timeout(1200); return None, nreq()-n0, toasts(pg)
    pg.wait_for_selector("#modalApproveConfirmDialog:not(.hide)",timeout=10000)
    with pg.expect_response(lambda r:r.url.endswith(f"/api/receipt/{rid}/approve"),timeout=20000) as ai: pg.locator("#btnExecuteApproveConfirm").click()
    pg.wait_for_timeout(800); return ai.value.status, nreq()-n0, toasts(pg)

# Tenant B baseline receipt (created via API, as user B)
fee={k:v for k,v in FEES}
sb,bb=api("POST","/api/save_edited",{"supplier_name":"Vendor_B","date":"2026-10-17","total_amount":22.5,"settlement_type":"cash","source":"manual","items":[{"name":"Item_UAT_B","quantity":1,"unit":"pcs","unit_price":10,"amount":10,"actual_qty":None}],**fee},t=TB)
RB=bb.get("receipt_id"); B_BASE=q("select id,status,total_amount,version from receipts where tenant_id=?",(TB,))+q("select receipt_id,name,quantity,actual_qty,amount from receipt_items where receipt_id=?",(RB,))
step("tenant B baseline receipt created",sb==200,{"rid":RB,"baseline":B_BASE})

with sync_playwright() as p:
    br=p.chromium.launch(headless=True)
    ctx=br.new_context(viewport={"width":1440,"height":1000},extra_http_headers={"X-Role":"owner","X-Tenant-Id":TA}); pg=ctx.new_page()
    pg.on("request",lambda r: R["net"].append({"req":r.url,"m":r.method,"body":r.post_data}) if ("save_edited" in r.url or "/approve" in r.url) else None)
    pg.on("response",lambda r: R["net"].append({"resp":r.url,"status":r.status}) if ("save_edited" in r.url or "/approve" in r.url) else None)
    pg.goto(BASE+"/",wait_until="domcontentloaded",timeout=60000); pg.wait_for_timeout(2000)

    # ---- AC1/AC2 at save: UI manual entry, qty1 price10 amount10 actual0 + six fees
    fill_manual(pg,"Item_UAT_zero","0")
    s=pg.evaluate(STATE); pl=pg.evaluate("() => collectReviewFormData()")
    it=pl["items"][0]
    step("AC1 form: qty 1 / price 10 / amount 10 / actual 0, six fees 5/4/3/2/1/0.5; auto total 12.50",
         s["total"]=="12.50" and it["quantity"]==1 and it["unit_price"]==10 and float(it["amount"])==10 and it["actual_qty"]==0
         and all(float(pl[k])==v for k,v in FEES),{"state":s,"item":it,"fees":{k:pl[k] for k,_ in FEES}})
    snap(pg,"01_manual_entry_actual0_auto_12_50.png")
    type_total(pg,"22.5"); s=pg.evaluate(STATE)
    step("AC2 save-stage UI: typed printed total 22.5 -> hint shows 明细 12.50 相差 10.00; issue bar blocker 相差 10.00",
         s["hintVisible"] and "12.50" in s["hint"] and "10.00" in s["hint"] and any("10.00" in b for b in s["blockers"]),s)
    snap(pg,"02_total_22_5_mismatch_hint_10.png")
    clear_toasts(pg); n0=nreq(); pg.locator("#btnSaveReview").click(); pg.wait_for_timeout(1500); tt=toasts(pg)
    step("AC2 save click with 22.5 -> blocked locally (no request), toast names 相差 10.00",nreq()==n0 and "10.00" in tt,{"toast":tt})
    snap(pg,"03_save_blocked_toast_10.png")

    # ---- AC3 at save: correct to 12.5 -> save 200 -> approve 200 (same receipt, UI only)
    type_total(pg,"12.5"); s=pg.evaluate(STATE)
    step("AC3 typed 12.5 -> hint hidden, no blocker",not s["hintVisible"] and not s["blockers"],s)
    st,b=save(pg); body=last_payload(); RZ=(b or {}).get("receipt_id")
    step("AC3 UI save 12.5 -> 200; payload actual_qty 0, total 12.5, six fees verbatim",
         st==200 and body["items"][0]["actual_qty"]==0 and float(body["total_amount"])==12.5 and all(float(body[k])==v for k,v in FEES),
         {"status":st,"rid":RZ,"payload_item":body["items"][0],"total":body["total_amount"]})
    sd,bd=api("GET",f"/api/receipt/{RZ}")
    step("AC3 detail after save: no TOTAL_MISMATCH gate issue, stored actual_qty 0.0",sd==200 and not ((bd.get("data") or {}).get("gate_issues")) and "TOTAL_MISMATCH" not in json.dumps(bd,ensure_ascii=False) and q("select actual_qty from receipt_items where receipt_id=?",(RZ,))==[(0.0,)],
         {"status":sd,"gate_issues":(bd.get("data") or {}).get("gate_issues"),"math_warnings":(bd.get("data") or {}).get("math_warnings"),"stored":q("select actual_qty from receipt_items where receipt_id=?",(RZ,))})
    open_from_queue(pg,RZ); s=pg.evaluate(STATE)
    step("AC3 reopen from queue (detail UI): total 12.50, actual 0, no blocker",abs(float(s["total"])-12.5)<1e-9 and not s["blockers"] and pg.locator("#itemTableBody tr .inp-actual-qty").first.input_value() in ("0","0.0","0.00"),
         {"state":s,"actual":pg.locator("#itemTableBody tr .inp-actual-qty").first.input_value()})
    snap(pg,"04_reopened_12_5_clean.png")
    a_st,a_n,a_t=approve_ui(pg,RZ)
    lg=q("select qty,amount,kind from inventory_log where receipt_id=?",(RZ,))
    step("AC3 UI approve -> 200, status approved; AC4 0->0: inventory_log qty 0",a_st==200 and q("select status from receipts where id=?",(RZ,))==[("approved",)] and sum(r[0] for r in lg)==0,
         {"status":a_st,"inventory_log":lg,"toast":a_t})
    R["O1_observation"]={"receipt":RZ,"inventory_log":lg,"toast":a_t}
    snap(pg,"05_approved_zero_toast.png")

    # ---- AC2 at detail + approval: mismatched receipt saved with explicit confirm (API, as user), opened in UI
    s1,b1=api("POST","/api/save_edited",{"supplier_name":"Vendor_A","date":"2026-10-17","total_amount":22.5,"settlement_type":"cash","source":"manual","confirm_mismatch":False,
        "items":[{"name":"Item_UAT_mis","quantity":1,"unit":"pcs","unit_price":10,"amount":10,"actual_qty":0}],**fee})
    step("AC2 API save 22.5 without confirm -> 422 MATH_MISMATCH_CONFIRM 相差 10",s1==422 and (b1 or {}).get("code")=="MATH_MISMATCH_CONFIRM" and "相差 10" in str(b1),{"status":s1,"body":b1})
    s2,b2=api("POST","/api/save_edited",{"supplier_name":"Vendor_A","date":"2026-10-17","total_amount":22.5,"settlement_type":"cash","source":"manual","confirm_mismatch":True,
        "items":[{"name":"Item_UAT_mis","quantity":1,"unit":"pcs","unit_price":10,"amount":10,"actual_qty":0}],**fee})
    RM=(b2 or {}).get("receipt_id")
    sd,bd=api("GET",f"/api/receipt/{RM}")
    gi=((bd or {}).get("data") or {}).get("gate_issues") or []
    step("AC2 detail API: confirmed-mismatch receipt data.gate_issues TOTAL_MISMATCH 相差 10, math_warnings 相差 10",s2==200 and any(g.get("code")=="TOTAL_MISMATCH" and "相差 10" in (g.get("hint") or "") for g in gi) and any("相差 10" in w for w in ((bd.get("data") or {}).get("math_warnings") or [])),
         {"save":s2,"rid":RM,"gate_issues":gi,"status":q("select status from receipts where id=?",(RM,))})
    open_from_queue(pg,RM); s=pg.evaluate(STATE)
    step("AC2 detail UI: reopened mismatch receipt shows blocker 相差 10.00",any("10.00" in x for x in s["blockers"]),s)
    issue_bar=pg.evaluate("() => (document.getElementById('receiptIssueSummary')||{}).innerText||''")
    R["mismatch_issue_bar"]=issue_bar
    snap(pg,"06_detail_mismatch_10.png")
    a_st,a_n,a_t=approve_ui(pg,RM,expect_request=False)
    step("AC2 approval UI: 审核入账 blocked locally, no request, blocker message",a_n==0 and "必须先改" in a_t,{"requests":a_n,"toast":a_t})
    snap(pg,"07_approve_blocked.png")
    sa,ba=api("POST",f"/api/receipt/{RM}/approve",{"version":q("select version from receipts where id=?",(RM,))[0][0]})
    step("AC2 approval API: 422 with 相差 10, no inventory row",sa==422 and "10" in json.dumps(ba,ensure_ascii=False) and q("select count(*) from inventory_log where receipt_id=?",(RM,))==[(0,)],{"status":sa,"body":ba})
    # queue row for the mismatched receipt: problem count, and the approve-modal path used by the queue shortcut
    home(pg); pg.locator("#queueTabApprove").click(); pg.wait_for_timeout(1000)
    row=pg.locator(f"#workbenchQueueBody tr[data-receipt-id='{RM}']")
    rowtxt=row.inner_text() if row.count() else ""
    shortcut=row.locator("button:has-text('审核入账')").count() if row.count() else 0
    step("AC2 queue 待我审批 row for mismatch receipt shows 问题数 1 (not 0)",row.count()==1 and "\t1\t" in rowtxt.replace("\n","\t"),{"row":rowtxt,"queue_shortcut_buttons":shortcut})
    snap(pg,"08_queue_mismatch_issue_count.png")
    # queue shortcut button is not rendered in this build (row shows 继续复核 only); invoke its handler directly to check the modal gate on server data
    n0=nreq(); pg.evaluate(f"() => openApproveConfirmModalFromQueue({RM})"); pg.wait_for_selector("#modalApproveConfirmDialog:not(.hide)",timeout=10000); pg.wait_for_timeout(500)
    m=pg.evaluate("""() => ({issue:(document.getElementById('approveConfirmIssueCount')||{}).textContent, warnVisible: !document.getElementById('approveConfirmBlockerWarn').classList.contains('hide'), warn: document.getElementById('approveConfirmBlockerWarn').innerText, submitDisabled: document.getElementById('btnExecuteApproveConfirm').disabled})""")
    snap(pg,"08b_queue_approve_modal_blocked.png")
    step("AC2 approve-confirm modal from queue handler (server detail data, actual 0): blocker warn visible, confirm disabled, no request",
         m["warnVisible"] and m["submitDisabled"] and nreq()==n0 and q("select count(*) from inventory_log where receipt_id=?",(RM,))==[(0,)],m)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    # correct mismatched receipt in UI: 改回明细合计 -> save -> approve
    open_from_queue(pg,RM)
    hb=pg.locator("#totalOverrideHint button")
    if hb.count() and hb.is_visible(): hb.click()
    else: type_total(pg,"12.5")
    pg.wait_for_timeout(400); s=pg.evaluate(STATE)
    step("AC3 detail correction: total now 12.50, no blocker",abs(float(s["total"])-12.5)<1e-9 and not s["blockers"],s)
    st,b=save(pg)
    a_st,a_n,a_t=approve_ui(pg,RM) if st==200 else (None,0,"")
    lg=q("select qty from inventory_log where receipt_id=?",(RM,))
    step("AC3 corrected receipt: save 200 and approve 200 (same conclusion), inventory qty 0",st==200 and a_st==200 and sum(r[0] for r in lg)==0,{"save":st,"approve":a_st,"log":lg,"toast":a_t})
    snap(pg,"09_corrected_approved.png")

    # ---- AC4 null: actual blank -> quantity 1, total 22.5 matches
    home(pg)
    fill_manual(pg,"Item_UAT_null",None); s=pg.evaluate(STATE)
    step("AC4 actual blank -> auto total 22.50 (null按quantity)",s["total"]=="22.50" and not s["blockers"],s)
    st,b=save(pg); RN=(b or {}).get("receipt_id"); body=last_payload()
    a_st,a_n,a_t=approve_ui(pg,RN) if st==200 else (None,0,"")
    lg=q("select qty from inventory_log where receipt_id=?",(RN,))
    step("AC4 null: payload actual_qty null, stored NULL, approve 200, inventory qty 1",st==200 and body["items"][0]["actual_qty"] is None and q("select actual_qty from receipt_items where receipt_id=?",(RN,))==[(None,)] and a_st==200 and sum(r[0] for r in lg)==1,
         {"save":st,"payload_aq":body["items"][0]["actual_qty"],"approve":a_st,"log":lg,"toast":a_t})
    snap(pg,"10_null_approved_qty1.png")

    # ---- credit-note signed hint
    home(pg)
    fill_manual(pg,"Item_UAT_credit",None,doc_form="credit_note",amount=-10); s=pg.evaluate(STATE)
    step("credit note: amount -10 + fees -> auto total 2.50 (signed formula)",abs(s["auto"]-2.5)<1e-9,s)
    pg.locator("#itemTableBody tr .inp-amount").first.fill("-20"); pg.locator("#itemTableBody tr .inp-amount").first.dispatch_event("input"); pg.wait_for_timeout(300)
    type_total(pg,"-5"); s=pg.evaluate(STATE)
    step("credit note: amount -20 (auto -7.50), typed -5 -> hint shows signed -7.50 and 相差 2.50 (no clamp to 0)",s["hintVisible"] and "-7.50" in s["hint"] and "2.50" in s["hint"] and abs(s["auto"]+7.5)<1e-9,s)
    snap(pg,"11_credit_note_signed_hint.png")
    pg.locator("#totalOverrideHint button").click(); pg.wait_for_timeout(400); s=pg.evaluate(STATE)
    step("credit note: 改回明细合计 -> -7.50",s["total"]=="-7.50",s)
    pg.locator("#itemTableBody tr .inp-actual-qty").first.fill("0"); pg.wait_for_timeout(300); s=pg.evaluate(STATE)
    step("credit note + 实收 0: line effective 0 -> auto total = fees only 12.50",abs(s["auto"]-12.5)<1e-9 and s["total"]=="12.50",s)
    snap(pg,"12_credit_note_actual0.png")

    # ---- tenant isolation in the browser: tenant B cannot see A's receipts
    ctxb=br.new_context(viewport={"width":1440,"height":1000},extra_http_headers={"X-Role":"owner","X-Tenant-Id":TB}); pb=ctxb.new_page()
    home(pb); pb.wait_for_timeout(1500)
    ids=pb.evaluate("() => [...document.querySelectorAll('#workbenchQueueBody tr[data-receipt-id]')].map(t=>Number(t.dataset.receiptId))")
    step("tenant B browser queue: none of tenant A receipts visible",not ({RZ,RM,RN} & set(ids)),{"b_queue_ids":ids,"a_ids":[RZ,RM,RN]})
    snap(pb,"13_tenant_b_queue.png")
    br.close()

# ---- credit note: backend gate agrees with the signed UI hint
cn=lambda tot,aq,amt:{"supplier_name":"Vendor_A","date":"2026-10-17","total_amount":tot,"settlement_type":"cash","source":"manual","doc_form":"credit_note","confirm_mismatch":False,
   "items":[{"name":"Item_UAT_cn","quantity":1,"unit":"pcs","unit_price":10,"amount":amt,"actual_qty":aq}],**fee}
for tot,aq,amt,exp in [(2.5,None,-10,200),(5,None,-10,422),(12.5,0,-10,200),(2.5,0,-10,422)]:
    s_,b_=api("POST","/api/save_edited",cn(tot,aq,amt),t="uat008uat-cn")
    step(f"credit note API (amount {amt}, actual {aq}, total {tot}) -> {exp} (agrees with UI auto total)",s_==exp,{"status":s_,"code":(b_ or {}).get("code") if isinstance(b_,dict) else None,"msg":(b_ or {}).get("msg") if isinstance(b_,dict) else b_})
# ---- tenant isolation API
for lab,(m,pth,b) in {"detail":("GET",f"/api/receipt/{RM}",None),"approve":("POST",f"/api/receipt/{RZ}/approve",{"version":1}),
    "save_over":("POST","/api/save_edited",{"receipt_id":RM,"version":1,"supplier_name":"Vendor_B","date":"2026-10-17","total_amount":12.5,"settlement_type":"cash","source":"manual","items":[{"name":"x","quantity":1,"unit":"pcs","unit_price":10,"amount":10,"actual_qty":0}],**fee})}.items():
    s,bb=api(m,pth,b,t=TB); step(f"tenant B {lab} on A receipt -> 404",s==404,{"status":s})
lst=api("GET","/api/receipts",t=TB)[1]
step("tenant B /api/receipts lists no A receipt",not ({RZ,RM,RN} & {r.get("id") for r in (lst.get("receipts") or lst.get("items") or lst if isinstance(lst,list) else lst.get("receipts",[]))}) if lst else True,{"sample":str(lst)[:300]})
B_AFTER=q("select id,status,total_amount,version from receipts where tenant_id=?",(TB,))+q("select receipt_id,name,quantity,actual_qty,amount from receipt_items where receipt_id=?",(RB,))
step("tenant B baseline unchanged after whole run",B_AFTER==B_BASE,{"before":B_BASE,"after":B_AFTER})
h=list(R["hashes"].values()); step("screenshots distinct",len(h)==len(set(h)),R["hashes"])
n=sum(x["pass"] for x in R["steps"]); R.update(passed=n,total=len(R["steps"]))
(ART/"evidence"/"uat-ui-walk.json").write_text(json.dumps(R,ensure_ascii=False,indent=1,default=str))
(UI/"ui-hashes.txt").write_text("\n".join(f"{v}  {k}" for k,v in R["hashes"].items())+"\n")
print(f"SUMMARY {n}/{len(R['steps'])}")

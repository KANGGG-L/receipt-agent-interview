# UAT-008 independent QA real-browser probe (headless chromium, port 15280). Env: BASE, DB_PATH, ART
import hashlib, json, os, sqlite3, sys
from pathlib import Path
from urllib import request, error
from playwright.sync_api import sync_playwright
BASE=os.environ["BASE"]; assert ":15010" not in BASE
DB=os.environ["DB_PATH"]; ART=Path(os.environ["ART"]); UI=ART/"ui"; UI.mkdir(exist_ok=True,parents=True)
T="uat008qaui-a"; FEES={"delivery_fee":5.0,"deposit_amount":4.0,"service_fee":3.0,"tax_amount":2.0,"discount_amount":1.0,"rounding_adjustment":0.5}
R={"steps":[],"net":[],"hashes":{}}
def step(n,ok,d=None): R["steps"].append({"name":n,"pass":bool(ok),"detail":d}); print("PASS" if ok else "FAIL",n,json.dumps(d,ensure_ascii=False,default=str)[:600])
def q(s,a=()):
    c=sqlite3.connect(DB)
    try: return c.execute(s,a).fetchall()
    finally: c.close()
def seed(name,extra=None,items=None,total=22.5):
    b={"supplier_name":"Vendor_A","date":"2026-10-17","total_amount":total,"settlement_type":"cash","source":"manual","confirm_mismatch":True,
       "items":items or [{"name":name,"quantity":1,"unit":"pcs","unit_price":10,"amount":10,"actual_qty":None}],**FEES}
    if extra: b.update(extra)
    r=request.Request(BASE+"/api/save_edited",data=json.dumps(b).encode(),method="POST",headers={"X-Role":"staff","X-Tenant-Id":T,"Content-Type":"application/json"})
    try: return json.loads(request.urlopen(r).read())["receipt_id"]
    except error.HTTPError as e: print("seed fail",e.code,e.read().decode()[:300]); return None
ST="""() => ({total: document.getElementById('inpTotal').value,
 hint: (document.getElementById('totalOverrideHint')||{}).textContent||'',
 hintVisible: !!document.getElementById('totalOverrideHint') && !document.getElementById('totalOverrideHint').classList.contains('hide'),
 issueBar: (document.getElementById('receiptIssueSummary')||document.querySelector('[id*=IssueSummary]')||{}).innerText||'',
 issues: calculateReceiptIssues(collectReviewFormData()).map(i=>({id:i.id,type:i.type,guide:i.guide})),
 auto: computeItemsAutoTotal()})"""
def snap(pg,n):
    p=UI/n; pg.screenshot(path=str(p)); R["hashes"][n]=hashlib.md5(p.read_bytes()).hexdigest()
def opn(pg,rid):
    pg.evaluate(f"() => window.openReceiptInWorkbench({rid}, true, true)")
    pg.wait_for_function(f"() => String((window.currentReceiptData||{{}}).id||currentReceiptId)==='{rid}' && document.querySelector('#itemTableBody tr .inp-actual-qty')",timeout=15000)
    pg.wait_for_timeout(700)
rz=seed("Item_QA_zero"); rc=seed("Item_QA_credit",{"doc_form":"credit_note"},[{"name":"Item_QA_credit","quantity":1,"unit":"pcs","unit_price":10,"amount":-10,"actual_qty":None}],-10+12.5)
step("seed",rz and rc,{"zero":rz,"credit":rc})
with sync_playwright() as p:
    br=p.chromium.launch(headless=True); ctx=br.new_context(viewport={"width":1440,"height":1000},extra_http_headers={"X-Role":"owner","X-Tenant-Id":T}); pg=ctx.new_page()
    pg.on("request",lambda r: R["net"].append({"req":r.url,"m":r.method,"body":r.post_data}) if ("save_edited" in r.url or "/approve" in r.url) else None)
    pg.on("response",lambda r: R["net"].append({"resp":r.url,"status":r.status}) if ("save_edited" in r.url or "/approve" in r.url) else None)
    pg.goto(BASE+"/",wait_until="domcontentloaded",timeout=60000); pg.wait_for_timeout(1500)
    opn(pg,rz)
    a=pg.locator("#itemTableBody tr .inp-actual-qty").first; a.click(); a.fill("0"); pg.wait_for_timeout(400)
    s=pg.evaluate(ST); step("type 实收 0 -> auto total 12.50",s["total"]=="12.50",s)
    t=pg.locator("#inpTotal"); t.click(); t.fill("22.5"); pg.wait_for_timeout(500)
    s=pg.evaluate(ST); g=[i for i in s["issues"] if i["id"]=="err_grand_total_math"]
    step("typed 22.5: override hint visible, mentions 12.50 and 10.00",s["hintVisible"] and "10.00" in s["hint"] and "12.50" in s["hint"],s["hint"])
    step("typed 22.5: issue blocker shows 10.00",bool(g) and g[0]["type"]=="blocker" and "10.00" in g[0]["guide"],g)
    snap(pg,"01_typed_22_5_hint_issue_10.png")
    n0=len([x for x in R["net"] if "req" in x]); pg.locator("#btnSaveReview").click(); pg.wait_for_timeout(1200)
    toast=pg.evaluate("() => [...document.querySelectorAll('.toast, [class*=toast]')].map(e=>e.innerText).join(' | ')")
    step("save click: no request, toast shows 10.00",len([x for x in R["net"] if "req" in x])==n0 and "10.00" in toast,{"toast":toast[:300]})
    snap(pg,"02_save_blocked_toast_10.png")
    pg.locator("#totalOverrideHint button").click(); pg.wait_for_timeout(400)
    s=pg.evaluate(ST); step("click 改回明细合计 -> total 12.50, hint hidden, no total blocker",s["total"]=="12.50" and not s["hintVisible"] and not any(i["id"]=="err_grand_total_math" for i in s["issues"]),s)
    snap(pg,"03_revert_items_sum_12_50.png")
    t.click(); t.fill("12.5"); pg.wait_for_timeout(400); s=pg.evaluate(ST)
    step("typed 12.5 -> hint hidden",not s["hintVisible"],s["hint"])
    with pg.expect_response(lambda r:"save_edited" in r.url,timeout=15000) as ri: pg.locator("#btnSaveReview").click()
    body=json.loads([x for x in R["net"] if "req" in x][-1]["body"])
    step("save 12.5 -> 200, payload actual_qty 0, six fees",ri.value.status==200 and body["items"][0]["actual_qty"]==0 and all(body[k]==v for k,v in FEES.items()),{"status":ri.value.status,"aq":body["items"][0]["actual_qty"],"total":body["total_amount"]})
    pg.wait_for_timeout(1500)
    pg.locator("#btnApproveReceipt").click(); pg.wait_for_selector("#modalApproveConfirmDialog:not(.hide)",timeout=10000)
    with pg.expect_response(lambda r:r.url.endswith(f"/api/receipt/{rz}/approve"),timeout=15000) as ai: pg.locator("#btnExecuteApproveConfirm").click()
    pg.wait_for_timeout(600)
    toast=pg.evaluate("() => [...document.querySelectorAll('.toast, [class*=toast]')].map(e=>e.innerText).join(' | ')")
    snap(pg,"04_approved_toast.png")
    lg=q("select * from inventory_log where receipt_id=?",(rz,))
    step("approve 200, status approved",ai.value.status==200 and q("select status from receipts where id=?",(rz,))==[("approved",)],{"status":ai.value.status})
    R["approve_zero_observation"]={"inventory_log_rows":lg,"toast":toast[:300]}
    print("OBS approve zero:",R["approve_zero_observation"])
    # credit note
    opn(pg,rc); pg.wait_for_timeout(300); s0=pg.evaluate(ST)
    step("credit note opened, docform credit_note, computeItemsAutoTotal signed (2.50)",abs(s0["auto"]-2.5)<1e-9,s0)
    t.click(); t.fill("-5"); pg.wait_for_timeout(400); s=pg.evaluate(ST)
    step("credit note typed -5: hint visible, shows 2.50 and diff 7.50",s["hintVisible"] and "2.50" in s["hint"] and "7.50" in s["hint"],s["hint"])
    # make items negative enough so auto total is negative: amount -20
    amt=pg.locator("#itemTableBody tr .inp-amount").first; amt.click(); amt.fill("-20"); amt.dispatch_event("input"); pg.wait_for_timeout(400); s=pg.evaluate(ST)
    step("credit note amount -20 (auto -7.50): hint shows negative -7.50, diff 2.50",s["hintVisible"] and "-7.50" in s["hint"] and "2.50" in s["hint"] and abs(s["auto"]+7.5)<1e-9,s)
    snap(pg,"05_credit_note_negative_hint.png")
    pg.locator("#totalOverrideHint button").click(); pg.wait_for_timeout(400); s=pg.evaluate(ST)
    step("credit note 改回明细合计 -> total -7.50 (same as recalcTotalSum)",s["total"]=="-7.50",s["total"])
    snap(pg,"06_credit_note_revert.png")
    br.close()
h=list(R["hashes"].values()); step("screenshots distinct",len(h)==len(set(h)),R["hashes"])
n=sum(x["pass"] for x in R["steps"]); R.update(passed=n,total=len(R["steps"]))
(ART/"evidence"/"qa-ui-probe.json").write_text(json.dumps(R,ensure_ascii=False,indent=1))
(UI/"ui-hashes.txt").write_text("\n".join(f"{v}  {k}" for k,v in R["hashes"].items())+"\n")
print(f"SUMMARY {n}/{len(R['steps'])}")

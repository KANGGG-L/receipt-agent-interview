# UAT-008 independent QA live API probe (port 15280). Env: BASE, DB_PATH, OUT
import json, os, sqlite3, sys
from urllib import request, error
BASE=os.environ["BASE"]; assert ":15010" not in BASE
DB=os.environ["DB_PATH"]; OUT=os.environ["OUT"]
TA,TB="uat008qa-a","uat008qa-b"
FEES={"delivery_fee":5.0,"deposit_amount":4.0,"service_fee":3.0,"tax_amount":2.0,"discount_amount":1.0,"rounding_adjustment":0.5}
res=[]
def call(m,p,b=None,t=TA,role="staff"):
    r=request.Request(BASE+p,data=json.dumps(b).encode() if b is not None else None,method=m,
        headers={"X-Role":role,"X-Tenant-Id":t,"Content-Type":"application/json"})
    try:
        with request.urlopen(r,timeout=60) as x: return x.status,json.loads(x.read() or b"null")
    except error.HTTPError as e:
        raw=e.read().decode(); 
        try: return e.code,json.loads(raw)
        except Exception: return e.code,raw
def q(sql,a=()):
    c=sqlite3.connect(DB)
    try: return c.execute(sql,a).fetchall()
    finally: c.close()
def chk(n,ok,d): res.append({"name":n,"pass":bool(ok),"detail":d}); print("PASS" if ok else "FAIL",n,json.dumps(d,ensure_ascii=False,default=str)[:400])
def pl(aq,total,confirm=False,name="Item_Q",**kw):
    d={"supplier_name":"Vendor_A","date":"2026-10-17","total_amount":total,"settlement_type":"cash","confirm_mismatch":confirm,"source":"manual",
       "items":[{"name":name,"quantity":1,"unit":"pcs","unit_price":10.0,"amount":10.0,"actual_qty":aq}],**FEES}; d.update(kw); return d
def counts(): return {"receipts":q("select count(*) from receipts where tenant_id=?",(TA,))[0][0],
    "items":q("select count(*) from receipt_items")[0][0],"inv_log":q("select count(*) from inventory_log")[0][0]}
b0=q("select count(*) from receipts where tenant_id=?",(TB,))[0][0]
# expected effective qty per input: None => unfilled (face 10 -> total 22.5 matches), 0 => 12.5 matches
cases=[("empty","",None),("spaces","   ",None),("str0","0",0.0),("str_sp0"," 0 ",0.0),("float0",0.0,0.0),("null",None,None)]
for lab,aq,eff in cases:
    good=22.5 if eff is None else 12.5; bad=12.5 if eff is None else 22.5
    s,b=call("POST","/api/save_edited",pl(aq,bad,name="Item_bad_"+lab))
    chk(f"{lab}: wrong total {bad} -> save 422 MATH_MISMATCH_CONFIRM 相差 10",s==422 and isinstance(b,dict) and b.get("code")=="MATH_MISMATCH_CONFIRM" and "10" in str(b.get("msg")),{"s":s,"b":b})
    s,b=call("POST","/api/save_edited",pl(aq,bad,True,name="Item_badc_"+lab)); rid=b.get("receipt_id") if isinstance(b,dict) else None
    st=q("select actual_qty from receipt_items where receipt_id=?",(rid,)); sd,bd=call("GET",f"/api/receipt/{rid}")
    sa,ba=call("POST",f"/api/receipt/{rid}/approve",{"version":1},role="owner"); lg=q("select count(*) from inventory_log where receipt_id=?",(rid,))[0][0]
    chk(f"{lab}: confirmed-mismatch saved, stored={eff}, detail TOTAL_MISMATCH, approve 422, no stock",
        s==200 and st==[(eff,)] and "TOTAL_MISMATCH" in json.dumps(bd) and sa==422 and lg==0,{"save":s,"stored":st,"approve":sa,"msg":ba.get("msg") if isinstance(ba,dict) else ba,"log":lg})
    s,b=call("POST","/api/save_edited",pl(aq,good,name="Item_ok_"+lab)); rid=b.get("receipt_id") if isinstance(b,dict) else None
    st=q("select actual_qty from receipt_items where receipt_id=?",(rid,)); sd,bd=call("GET",f"/api/receipt/{rid}")
    sa,ba=call("POST",f"/api/receipt/{rid}/approve",{"version":1},role="owner"); lg=q("select count(*),coalesce(sum(qty),0) from inventory_log where receipt_id=?",(rid,))
    expq=1.0 if eff is None else 0.0
    chk(f"{lab}: total {good} -> save 200 no warning, stored={eff}, detail no TOTAL_MISMATCH, approve 200, inventory qty {expq}",
        s==200 and st==[(eff,)] and "TOTAL_MISMATCH" not in json.dumps(bd) and sa==200 and lg[0][1]==expq,{"save":s,"stored":st,"approve":sa,"approve_body":ba,"log(count,sum)":lg})
for lab,aq in [("abc","abc"),("nan","nan"),("true",True),("inf","inf"),("list",[1])]:
    c0=counts()
    for conf in (False,True):
        s,b=call("POST","/api/save_edited",pl(aq,22.5,conf)); c1=counts()
        chk(f"invalid {lab} confirm={conf}: 422 INVALID_ACTUAL_QTY, nothing written",s==422 and isinstance(b,dict) and b.get("code")=="INVALID_ACTUAL_QTY" and c0==c1,{"s":s,"b":b,"before":c0,"after":c1})
# invalid on existing receipt update: nothing changed
s,b=call("POST","/api/save_edited",pl(None,22.5,name="Item_upd")); rid=b["receipt_id"]
before=q("select name,actual_qty from receipt_items where receipt_id=?",(rid,)); rb=q("select total_amount,status from receipts where id=?",(rid,))
up=pl("abc",99.0,True,name="Item_changed",receipt_id=rid,version=1); s,b=call("POST","/api/save_edited",up)
chk("invalid on update: 422, existing row/items unchanged",s==422 and b.get("code")=="INVALID_ACTUAL_QTY" and before==q("select name,actual_qty from receipt_items where receipt_id=?",(rid,)) and rb==q("select total_amount,status from receipts where id=?",(rid,)),{"s":s,"before":before,"rb":rb})
# auto save path on a parsed receipt
sys.path.insert(0,"/workspace/receipt-agent-interview/demo")
from app import db as appdb
for lab,aq in [("auto_zero",0),("auto_null",None),("auto_blank","")]:
    r=appdb.create_receipt(supplier_name="Vendor_A",status="parsed",tenant_id=TA); rid=r if isinstance(r,int) else (r.get("id") if isinstance(r,dict) else getattr(r,"id",r))
    ver=q("select version from receipts where id=?",(rid,))[0][0]
    p=pl(aq,22.5,receipt_id=rid,version=ver); p["source"]="auto"; p["settlement_type"]=None
    s,b=call("POST","/api/save_edited",p); st=q("select status from receipts where id=?",(rid,)); ai=q("select actual_qty from receipt_items where receipt_id=?",(rid,))
    chk(f"{lab}: auto sync-save still 200, status stays parsed",s==200 and st==[("parsed",)],{"s":s,"b":b if s!=200 else "ok","status":st,"items":ai})
s,b=call("POST","/api/save_edited",pl(0,12.5,name="Item_iso")); rid=b["receipt_id"]
chk("tenant B detail 404",call("GET",f"/api/receipt/{rid}",t=TB)[0]==404,{})
chk("tenant B approve 404",call("POST",f"/api/receipt/{rid}/approve",{"version":1},t=TB,role="owner")[0]==404,{})
chk("tenant B save-over 404",call("POST","/api/save_edited",pl(0,12.5,receipt_id=rid,version=1),t=TB)[0]==404,{})
chk("tenant B baseline unchanged",q("select count(*) from receipts where tenant_id=?",(TB,))[0][0]==b0,{})
n=sum(r["pass"] for r in res); json.dump({"passed":n,"total":len(res),"checks":res},open(OUT,"w"),ensure_ascii=False,indent=1)
print(f"SUMMARY {n}/{len(res)}")

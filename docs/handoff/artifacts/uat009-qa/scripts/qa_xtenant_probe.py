# UAT-009 independent QA cross-tenant probe: dev header-mode (AUTH_ENABLED=0) instance on 15972
# Verifies tenant isolation (read/write across tenants rejected) + Tenant B baseline zero-pollution,
# the same contract the card demands; identity via X-Role/X-Tenant-Id (demo mode), matching dev tests.
import json, os, sqlite3, sys
import urllib.request, urllib.error
BASE=os.environ["BASE"]; assert ":15010" not in BASE and ":15971" not in BASE
DB=os.environ["DB_PATH"]; OUT=os.environ["OUT"]
TA,TB="uat009qa-a","uat009qa-b"
FEES={"delivery_fee":5.0,"deposit_amount":4.0,"service_fee":3.0,"tax_amount":2.0,"discount_amount":1.0,"rounding_adjustment":0.5}
FEE_KEYS=list(FEES)
res=[]
def call(m,p,b=None,t=TA,role="staff"):
    h={"X-Role":role,"X-Tenant-Id":t,"Content-Type":"application/json"}
    r=urllib.request.Request(BASE+p,data=json.dumps(b).encode() if b is not None else None,method=m,headers=h)
    try:
        with urllib.request.urlopen(r,timeout=60) as x: return x.status,json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        raw=e.read().decode()
        try: return e.code,json.loads(raw)
        except Exception: return e.code,raw
def q(sql,a=()):
    c=sqlite3.connect(DB)
    try: return c.execute(sql,a).fetchall()
    finally: c.close()
def chk(n,ok,d): res.append({"name":n,"pass":bool(ok),"detail":d}); print(("PASS" if ok else "FAIL"),n,json.dumps(d,ensure_ascii=False,default=str)[:400])

b0=q("select count(*) from receipts where tenant_id=?",(TB,))[0][0]
# A: old receipt six fees nonzero
s,b=call("POST","/api/save_edited",{"supplier_name":"Vendor_QA_old","date":"2026-10-15","total_amount":115.5,"settlement_type":"cash","source":"manual",
    "doc_form":"ncr_handwritten","currency":"CNY","payment_evidence":"PAID stamp QA","adjustment_notes":["short 1 box"],
    "items":[{"name":"Item_old","quantity":1,"unit":"pcs","unit_price":100,"amount":100}],**FEES})
old_rid=(b or {}).get("receipt_id")
chk("A seed old receipt",s==200 and old_rid,{"s":s,"rid":old_rid})
# B baseline receipt
s,bb=call("POST","/api/save_edited",{"supplier_name":"Vendor_B_base","date":"2026-10-16","total_amount":10,"settlement_type":"cash","source":"manual",
    "items":[{"name":"Item_B","quantity":1,"unit":"pcs","unit_price":10,"amount":10}],**{k:0 for k in FEE_KEYS}},t=TB)
rb=bb.get("receipt_id")
B_BASE=q("select id,status,total_amount,version from receipts where tenant_id=?",(TB,))+q("select receipt_id,name,quantity,amount from receipt_items where receipt_id=?",(rb,))
chk("B baseline receipt created",s==200,{"rid":rb})

# cross-tenant reads/writes -> 404
s1,_=call("GET",f"/api/receipt/{old_rid}",t=TB,role="owner")
s2,_=call("GET",f"/api/receipt/{rb}",t=TA,role="owner")
s3,b3=call("POST","/api/save_edited",{"receipt_id":old_rid,"supplier_name":"hacker","date":"2026-10-15","total_amount":999,"settlement_type":"cash","source":"manual",
    "items":[{"name":"hack","quantity":1,"unit":"pcs","unit_price":999,"amount":999}]},t=TB)
chk("B reads A receipt -> 404",s1==404,{"s":s1})
chk("A reads B receipt -> 404",s2==404,{"s":s2})
chk("B writes over A receipt -> 404",s3==404,{"s":s3,"code":(b3 or {}).get("code") if isinstance(b3,dict) else b3})

# unauthenticated (no role header) -> 401 (EarlyAuthMiddleware)
try:
    r=urllib.request.Request(BASE+"/api/receipts")
    with urllib.request.urlopen(r,timeout=30) as x: su=x.status
except urllib.error.HTTPError as e: su=e.code
chk("no credentials at all -> 401",su==401,{"s":su})

# A flow: old -> fees preserved after new-manual default save (server side)
s,b=call("POST","/api/save_edited",{"receipt_id":None,"source":"manual","supplier_name":"Vendor_QA_new","date":"2026-10-17","total_amount":10,
    "items":[{"name":"Item_new","quantity":1,"unit":"pcs","unit_price":10,"amount":10}],"settlement_type":"cash",
    **{k:0 for k in FEE_KEYS},"adjustment_notes":[],"payment_evidence":""})
new_rid=b.get("receipt_id")
s,d=call("GET",f"/api/receipt/{old_rid}")
dd=d["data"]
chk("A old receipt fees intact after new-manual save",all(float(dd[k])==v for k,v in FEES.items()),{k:dd[k] for k in FEE_KEYS})
s,d=call("GET",f"/api/receipt/{new_rid}")
nd=d["data"]
chk("A new receipt all defaults",all(float(nd[k])==0.0 for k in FEE_KEYS) and nd["currency"]=="HKD" and (nd.get("payment_evidence") or "")=="",None)

# Tenant B baseline unchanged (counts + row content)
b_after_counts=q("select count(*) from receipts where tenant_id=?",(TB,))[0][0]
B_AFTER=q("select id,status,total_amount,version from receipts where tenant_id=?",(TB,))+q("select receipt_id,name,quantity,amount from receipt_items where receipt_id=?",(rb,))
chk("Tenant B baseline zero-pollution (count + rows identical)",b_after_counts==b0+1 and B_AFTER==B_BASE,{"before":B_BASE,"after":B_AFTER})
json.dump(res,open(OUT,"w"),ensure_ascii=False,indent=1)
print("XT_PROBE_DONE",sum(1 for r in res if r["pass"]),"/",len(res))

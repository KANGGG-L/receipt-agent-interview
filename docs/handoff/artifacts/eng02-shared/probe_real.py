#!/usr/bin/env python3
"""ENG-02 real-model probe. NO route mocks. AC1 no #chkAutoAnalyze; AC2 real POST /api/upload -> real job -> prefill; AC3 blurry stays on preConfirmCard, no upload until explicit start."""
import json, os, sys, time
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
BASE=os.environ["BASE"]; ART=Path(os.environ["ART"]); SCR=ART/"screens"; SCR.mkdir(parents=True,exist_ok=True)
SHARP=Path(os.environ["SHARP"]); BLUR=Path(os.environ["BLUR"])
def shot(pg,path):
    for i in range(3):
        try:
            pg.screenshot(path=path,full_page=False,timeout=15000); return True
        except Exception as e:
            pg.wait_for_timeout(1500)
    res.setdefault("screenshot_failures",[]).append(path); return False
res={"base":BASE,"mocks_used":False,"checks":{}}
net=[]
with sync_playwright() as p:
    b=p.chromium.launch(headless=True)
    ctx=b.new_context(viewport={"width":1440,"height":900},extra_http_headers={"X-Role":"staff","X-Tenant-Id":"default"})
    pg=ctx.new_page()
    pg.on("request",lambda r: net.append({"t":round(time.time(),2),"m":r.method,"url":r.url}) if "/api/" in r.url else None)
    pg.goto(BASE+"/",wait_until="domcontentloaded",timeout=60000); pg.wait_for_timeout(800)
    if pg.locator("#demoRoleSelect").count():
        try: pg.select_option("#demoRoleSelect","staff")
        except Exception: pass
    n=pg.locator("#chkAutoAnalyze").count()
    res["checks"]["AC1_chkAutoAnalyze_count"]=n
    shot(pg,str(SCR/"00-landing.png"))
    with pg.expect_response(lambda r: r.request.method=="POST" and "/api/upload" in r.url,timeout=60000) as ri:
        pg.set_input_files("#receiptFile",str(SHARP))
    r=ri.value; q=urlparse(r.url).query
    try: body=r.json()
    except Exception: body={"_raw":r.text()[:300]}
    res["checks"]["AC2_upload"]={"status":r.status,"query":q,"force_in_query":"force=true" in q,"job_id":body.get("job_id") or body.get("id"),"keys":sorted(body.keys())[:20]}
    t0=time.time()
    try:
        pg.wait_for_function("()=>{const s=document.getElementById('inpSupplier');return s&&s.value&&s.value.trim().length>0}",timeout=240000)
        ok=True
    except Exception as e:
        ok=False; res["checks"]["AC2_wait_error"]=str(e)[:200]
    pg.wait_for_timeout(800); shot(pg,str(SCR/"01-real-recognize-prefill.png"))
    rows=pg.locator("#itemTableBody tr").count()
    res["checks"]["AC2_result"]={"prefill":ok,"secs":round(time.time()-t0,1),"supplier":pg.input_value("#inpSupplier"),"date":pg.input_value("#inpDate"),"total":pg.input_value("#inpTotal"),"item_rows":rows,"preConfirm_hidden":not pg.locator("#preConfirmCard").is_visible(),"job_polls":sum(1 for x in net if "/api/job/" in x["url"])}
    # AC3 abnormal quality
    pg.goto(BASE+"/",wait_until="domcontentloaded",timeout=60000); pg.wait_for_timeout(800)
    before=sum(1 for x in net if x["m"]=="POST" and "/api/upload" in x["url"])
    pg.set_input_files("#receiptFile",str(BLUR)); pg.wait_for_timeout(2500)
    after=sum(1 for x in net if x["m"]=="POST" and "/api/upload" in x["url"])
    pc=pg.locator("#preConfirmCard").is_visible(); qw=pg.locator("#preConfirmQualityWarn").is_visible()
    shot(pg,str(SCR/"02-blurry-preconfirm.png"))
    clicked=None
    if pc:
        with pg.expect_response(lambda r: r.request.method=="POST" and "/api/upload" in r.url,timeout=60000) as ri2:
            pg.locator("#preConfirmCard button.btn-success").click()
        r2=ri2.value; clicked={"status":r2.status,"force_in_query":"force=true" in urlparse(r2.url).query,"body":r2.text()[:300]}
        pg.wait_for_timeout(3000); shot(pg,str(SCR/"03-blurry-after-start.png"))
        clicked["errorCard_visible"]=pg.locator("#errorCard").is_visible()
        clicked["btnForceRetry_visible"]=pg.locator("#btnForceRetry").count()>0 and pg.locator("#btnForceRetry").first.is_visible()
    res["checks"]["AC3_quality"]={"preConfirm_visible":pc,"quality_warn_visible":qw,"uploads_before_click":after-before,"after_start":clicked}
    b.close()
res["network"]=net
c=res["checks"]; a2=c["AC2_upload"]; r2=c["AC2_result"]; a3=c["AC3_quality"]
v={"AC1":c["AC1_chkAutoAnalyze_count"]==0,
   "AC2":a2["status"]==200 and not a2["force_in_query"] and r2["prefill"] and r2["item_rows"]>=1 and r2["job_polls"]>=1 and ("7" in r2["supplier"] or "ELEVEN" in r2["supplier"].upper()),
   "AC3":a3["preConfirm_visible"] and a3["uploads_before_click"]==0 and bool(a3["after_start"]) and not a3["after_start"]["force_in_query"] and (a3["after_start"]["status"]!=200 or a3["after_start"]["errorCard_visible"])}
res["verdict"]=v; res["overall"]="PASS" if all(v.values()) else "FAIL"
(ART/"evidence").mkdir(exist_ok=True); (ART/"evidence"/"real-probe.json").write_text(json.dumps(res,ensure_ascii=False,indent=2))
print(json.dumps({"verdict":v,"overall":res["overall"],"AC2":r2,"AC3":a3},ensure_ascii=False))
sys.exit(0 if res["overall"]=="PASS" else 1)

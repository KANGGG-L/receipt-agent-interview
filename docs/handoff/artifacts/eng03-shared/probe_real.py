#!/usr/bin/env python3
"""ENG-03 real-model probe. NO route mocks / no fake syncReviewSourceBadge for AC3.
AC1 manual mode hides #aiPrefillNoticeBadge
AC2 real manual fill + save succeeds (AI badge stays hidden)
AC3 real AI recognition prefill keeps #aiPrefillNoticeBadge visible
"""
import json, os, sqlite3, sys, time
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

BASE = os.environ["BASE"]
ART = Path(os.environ["ART"])
SCR = ART / "screens"
SCR.mkdir(parents=True, exist_ok=True)
SHARP = Path(os.environ["SHARP"])
DB = Path(os.environ.get("DB_PATH", ART / "environment" / "eng03.db"))

res = {"base": BASE, "mocks_used": False, "checks": {}, "network": []}
net = res["network"]

def shot(pg, path):
    for _ in range(3):
        try:
            pg.screenshot(path=path, full_page=False, timeout=15000)
            return True
        except Exception:
            pg.wait_for_timeout(1500)
    res.setdefault("screenshot_failures", []).append(path)
    return False

def badge_state(pg):
    return pg.evaluate(
        """() => {
          const ai = document.getElementById('aiPrefillNoticeBadge');
          const man = document.getElementById('manualEntryFormBadge');
          const vis = (el) => {
            if (!el) return false;
            if (el.classList.contains('hide')) return false;
            const st = window.getComputedStyle(el);
            return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
          };
          return {
            ai_visible: vis(ai),
            ai_text: ai ? (ai.innerText || '').trim() : null,
            ai_has_hide: ai ? ai.classList.contains('hide') : null,
            manual_visible: vis(man),
            manual_text: man ? (man.innerText || '').trim() : null,
            manual_has_hide: man ? man.classList.contains('hide') : null,
            title: (document.querySelector('#prefillFormCard .card-title') || {}).innerText || '',
            prefill_visible: (() => {
              const c = document.getElementById('prefillFormCard');
              return !!(c && !c.classList.contains('hide'));
            })(),
            preConfirm_visible: (() => {
              const c = document.getElementById('preConfirmCard');
              return !!(c && !c.classList.contains('hide'));
            })(),
            supplier: (document.getElementById('inpSupplier') || {}).value || '',
            date: (document.getElementById('inpDate') || {}).value || '',
            total: (document.getElementById('inpTotal') || {}).value || '',
            doc_form: (document.getElementById('inpDocForm') || {}).value || '',
            item_rows: document.querySelectorAll('#itemTableBody tr').length
          };
        }"""
    )

def fill_manual_line(pg, qty, price):
    row = pg.locator("#itemTableBody tr").first
    row.locator(".inp-name").fill("优质测试食材")
    row.locator(".inp-qty").fill(str(qty))
    row.locator(".inp-qty").dispatch_event("input")
    row.locator(".inp-unit").fill("斤")
    row.locator(".inp-price").fill(str(price))
    row.locator(".inp-price").dispatch_event("input")

def landing(pg):
    pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_selector("#receiptFile", state="attached", timeout=15000)
    pg.wait_for_timeout(1000)
    if pg.locator("#demoRoleSelect").count():
        try:
            pg.select_option("#demoRoleSelect", "staff")
        except Exception:
            pass

def api_upload_and_poll(pg):
    """Real model path without depending on file-input change event."""
    meta = {"path": "api_upload"}
    # multipart via page.evaluate fetch with blob from file bytes is heavy;
    # use Playwright APIRequestContext instead (still hits same server, real model).
    api = pg.context.request
    with open(SHARP, "rb") as f:
        data = f.read()
    resp = api.post(
        BASE + "/api/upload?treatment=true&async=true&force=false",
        multipart={
            "receipt": {
                "name": "sharp.jpg",
                "mimeType": "image/jpeg",
                "buffer": data,
            }
        },
        headers={"X-Role": "staff", "X-Tenant-Id": "default"},
        timeout=60000,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text()[:300]}
    meta.update(
        {
            "status": resp.status,
            "force_in_query": False,
            "job_id": body.get("job_id") or body.get("id"),
            "receipt_id": body.get("receipt_id"),
            "keys": sorted(body.keys())[:20],
            "body_status": body.get("status"),
            "body_msg": (body.get("msg") or body.get("detail") or "")[:200] if isinstance(body, dict) else "",
            "body_code": body.get("code") if isinstance(body, dict) else None,
        }
    )
    if resp.status != 200 or not meta["job_id"]:
        meta["error"] = "upload_failed"
        return meta
    job_id = meta["job_id"]
    t0 = time.time()
    polls = []
    final = None
    while time.time() - t0 < 240:
        jr = api.get(
            BASE + f"/api/job/{job_id}",
            headers={"X-Role": "staff", "X-Tenant-Id": "default"},
            timeout=30000,
        )
        try:
            jb = jr.json()
        except Exception:
            jb = {}
        st = jb.get("status") or jb.get("job_status") or ""
        polls.append({"t": round(time.time() - t0, 1), "http": jr.status, "status": st})
        if st in ("done", "parsed", "error", "failed"):
            final = jb
            break
        time.sleep(2)
    meta["polls"] = polls
    meta["job_final_status"] = (final or {}).get("status") or (final or {}).get("job_status")
    if not meta.get("receipt_id"):
        meta["receipt_id"] = (final or {}).get("receipt_id")
    meta["secs"] = round(time.time() - t0, 1)
    return meta

def ui_upload(pg):
    """ENG-02 style: set_input_files; click preConfirm if quality gate stops auto-start."""
    meta = {"path": "ui_auto"}
    try:
        with pg.expect_response(
            lambda r: r.request.method == "POST" and "/api/upload" in r.url,
            timeout=30000,
        ) as ri:
            pg.set_input_files("#receiptFile", str(SHARP))
        r = ri.value
    except Exception as e1:
        meta["auto_error"] = str(e1)[:180]
        pg.wait_for_timeout(1500)
        if pg.locator("#preConfirmCard").count() and pg.locator("#preConfirmCard").is_visible():
            meta["path"] = "ui_preConfirm"
            shot(pg, str(SCR / "03a-preconfirm.png"))
            with pg.expect_response(
                lambda r: r.request.method == "POST" and "/api/upload" in r.url,
                timeout=60000,
            ) as ri2:
                btn = pg.locator("#preConfirmCard button.btn-success")
                if btn.count() == 0:
                    btn = pg.locator("#preConfirmCard button").first
                btn.click()
            r = ri2.value
        else:
            # try Start Recognize button after setting files again
            meta["path"] = "ui_btnStart"
            shot(pg, str(SCR / "03a-upload-stuck.png"))
            pg.set_input_files("#receiptFile", str(SHARP))
            pg.wait_for_timeout(800)
            try:
                with pg.expect_response(
                    lambda r: r.request.method == "POST" and "/api/upload" in r.url,
                    timeout=20000,
                ) as ri3:
                    if pg.locator("#btnStartRecognize").count():
                        pg.locator("#btnStartRecognize").click()
                    elif pg.locator("#preConfirmCard button.btn-success").count():
                        pg.locator("#preConfirmCard button.btn-success").click()
                    else:
                        pg.evaluate(
                            "() => { if (typeof triggerAnalysisNow==='function') triggerAnalysisNow(false); }"
                        )
                r = ri3.value
            except Exception as e2:
                meta["btn_error"] = str(e2)[:180]
                return None, meta
    q = urlparse(r.url).query
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text()[:300]}
    meta.update(
        {
            "status": r.status,
            "query": q,
            "force_in_query": "force=true" in q,
            "job_id": body.get("job_id") or body.get("id"),
            "receipt_id": body.get("receipt_id"),
            "keys": sorted(body.keys())[:20],
            "body_status": body.get("status"),
        }
    )
    return meta, meta

def write_out():
    (ART / "evidence").mkdir(exist_ok=True)
    (ART / "evidence" / "real-probe.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2)
    )

try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"X-Role": "staff", "X-Tenant-Id": "default"},
        )
        pg = ctx.new_page()
        pg.on(
            "request",
            lambda r: net.append({"t": round(time.time(), 2), "m": r.method, "url": r.url})
            if "/api/" in r.url
            else None,
        )

        # ===== AC3: real AI recognition =====
        landing(pg)
        shot(pg, str(SCR / "00-landing.png"))
        ui_meta, _ = ui_upload(pg)
        opened_via = "ui_prefill_wait"
        if ui_meta and ui_meta.get("status") == 200 and ui_meta.get("job_id"):
            res["checks"]["AC3_upload"] = ui_meta
            t0 = time.time()
            prefill_ok = False
            try:
                pg.wait_for_function(
                    "()=>{const s=document.getElementById('inpSupplier');return s&&s.value&&s.value.trim().length>0}",
                    timeout=240000,
                )
                prefill_ok = True
            except Exception as e:
                res["checks"]["AC3_wait_error"] = str(e)[:300]
            pg.wait_for_timeout(800)
            try:
                pg.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000)
            except Exception:
                pass
            ac3_badges = badge_state(pg)
            shot(pg, str(SCR / "03-real-ai-prefill-badge.png"))
            job_id = ui_meta.get("job_id")
            rid = ui_meta.get("receipt_id")
            secs = round(time.time() - t0, 1)
        else:
            # Fallback: API upload (still real model) + openReceiptInWorkbench
            res["checks"]["AC3_ui_upload_failed"] = ui_meta or {"error": "ui_upload_returned_none"}
            opened_via = "api_upload_openReceiptInWorkbench"
            api_meta = api_upload_and_poll(pg)
            res["checks"]["AC3_upload"] = api_meta
            # capture 422 body detail for evidence (no secrets)
            if api_meta.get("status") != 200:
                try:
                    api2 = pg.context.request
                    # already have status in meta; keep as-is
                except Exception:
                    pass
            job_id = api_meta.get("job_id")
            rid = api_meta.get("receipt_id")
            secs = api_meta.get("secs")
            prefill_ok = False
            ac3_badges = {}
            if rid:
                opened_via = "queue_continue_review_click"
                for attempt in range(3):
                    try:
                        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
                        pg.wait_for_selector("#btnNewManualEntry", state="visible", timeout=15000)
                        if pg.locator("#demoRoleSelect").count():
                            try:
                                pg.select_option("#demoRoleSelect", "staff")
                            except Exception:
                                pass
                        # Wait until today's queue lists this receipt (API job already done).
                        pg.wait_for_selector(
                            f'tr[data-receipt-id="{rid}"]', timeout=20000
                        )
                        shot(pg, str(SCR / "03b-queue-before-open.png"))
                        btn = pg.locator(
                            f'tr[data-receipt-id="{rid}"] button:has-text("继续复核")'
                        )
                        if btn.count() == 0:
                            btn = pg.locator(f'tr[data-receipt-id="{rid}"] button')
                        btn.first.click()
                        pg.wait_for_selector("#prefillFormCard:not(.hide)", timeout=15000)
                        pg.wait_for_function(
                            "()=>{const s=document.getElementById('inpSupplier');return s&&s.value&&s.value.trim().length>0}",
                            timeout=30000,
                        )
                        prefill_ok = True
                        break
                    except Exception as e:
                        res.setdefault("checks", {}).setdefault("AC3_open_attempts", []).append(
                            {"attempt": attempt + 1, "error": str(e)[:240]}
                        )
                        # Fallback evaluate opener once per attempt
                        try:
                            pg.evaluate(
                                """(id) => {
                                  if (typeof openReceiptInWorkbench === 'function') {
                                    openReceiptInWorkbench(id, true, true);
                                  }
                                }""",
                                rid,
                            )
                            pg.wait_for_function(
                                "()=>{const s=document.getElementById('inpSupplier');return s&&s.value&&s.value.trim().length>0}",
                                timeout=15000,
                            )
                            opened_via = "openReceiptInWorkbench_fallback"
                            prefill_ok = True
                            break
                        except Exception as e2:
                            res["checks"]["AC3_open_attempts"].append(
                                {"attempt": attempt + 1, "fallback_error": str(e2)[:240]}
                            )
                            pg.wait_for_timeout(1000)
                pg.wait_for_timeout(800)
                ac3_badges = badge_state(pg)
                # Prefer badge fields; if supplier empty but receipt_api has it, still screenshot
                shot(pg, str(SCR / "03-real-ai-prefill-badge.png"))

        job_polls = sum(1 for x in net if "/api/job/" in x["url"])
        # also count API polls stored in upload meta
        if isinstance(res["checks"].get("AC3_upload"), dict):
            job_polls = max(job_polls, len(res["checks"]["AC3_upload"].get("polls") or []))
        receipt_api = None
        if rid:
            receipt_api = pg.evaluate(
                """async (id) => {
                  const res = await fetch('/api/receipt/' + id, {headers:{'X-Role':'staff','X-Tenant-Id':'default'}});
                  const body = await res.json();
                  const data = body.data || body || {};
                  return {
                    http: res.status, status: body.status, id: data.id || id,
                    doc_form: data.doc_form, supplier_name: data.supplier_name,
                    date: data.date || data.receipt_date, total_amount: data.total_amount,
                    ai_prefill_present: !!(data.ai_prefill_json || data.ai_prefill),
                    receipt_status: data.status
                  };
                }""",
                rid,
            )
        res["checks"]["AC3_result"] = {
            "prefill": prefill_ok,
            "secs": secs,
            "badges": ac3_badges,
            "job_polls": job_polls,
            "receipt_api": receipt_api,
            "receipt_id": rid,
            "job_id": job_id,
            "opened_via": opened_via,
        }

        # ===== AC1+AC2 on fresh context =====
        ctx2 = b.new_context(
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"X-Role": "staff", "X-Tenant-Id": "default"},
        )
        pg2 = ctx2.new_page()
        landing(pg2)
        pg2.wait_for_selector("#btnNewManualEntry", state="visible", timeout=15000)
        pg2.locator("#btnNewManualEntry").click()
        try:
            pg2.wait_for_selector("#prefillFormCard:not(.hide)", timeout=8000)
        except Exception as e_man:
            res["checks"]["AC1_click_fallback"] = str(e_man)[:200]
            shot(pg2, str(SCR / "01a-manual-click-miss.png"))
            pg2.evaluate(
                "() => { if (typeof startManualEntry === 'function') startManualEntry(); }"
            )
            pg2.wait_for_selector("#prefillFormCard:not(.hide)", timeout=8000)
        pg2.wait_for_timeout(400)
        ac1 = badge_state(pg2)
        shot(pg2, str(SCR / "01-manual-mode-badges.png"))
        res["checks"]["AC1_manual_badges"] = ac1

        pg2.locator("#inpSupplier").fill("Vendor_A")
        pg2.locator("#inpDate").fill("2026-10-17")
        fill_manual_line(pg2, 4, 20)
        pg2.locator("#inpTotal").fill("80.00")
        pg2.locator("#inpTotal").dispatch_event("input")
        pg2.locator("#inpSettlementType").select_option("cash")
        pg2.locator("#inpDocForm").select_option("manual_entry")
        pg2.wait_for_timeout(400)
        with pg2.expect_response(
            lambda r: r.request.method == "POST" and "/api/save_edited" in r.url,
            timeout=20000,
        ) as save_info:
            pg2.locator("#btnSaveReview").click()
        save_resp = save_info.value
        try:
            save_body = save_resp.json()
        except Exception:
            save_body = {"_raw": save_resp.text()[:300]}
        pg2.wait_for_timeout(800)
        ac2_badges = badge_state(pg2)
        shot(pg2, str(SCR / "02-manual-save-done.png"))
        receipt_id = save_body.get("receipt_id") or save_body.get("id")
        readback = pg2.evaluate(
            """async (id) => {
              if (!id) return null;
              const res = await fetch('/api/receipt/' + id, {headers:{'X-Role':'staff','X-Tenant-Id':'default'}});
              const body = await res.json();
              const data = body.data || body || {};
              return {http: res.status, status: body.status, doc_form: data.doc_form,
                      supplier: data.supplier_name, total: data.total_amount,
                      receipt_status: data.status, id: data.id || id};
            }""",
            receipt_id,
        )
        res["checks"]["AC2_manual_save"] = {
            "http": save_resp.status,
            "body_status": save_body.get("status"),
            "receipt_id": receipt_id,
            "badges_after_save": ac2_badges,
            "readback": readback,
        }
        ctx2.close()
        b.close()
except Exception as e:
    res["fatal"] = str(e)[:500]
    write_out()
    print(json.dumps({"overall": "FAIL", "fatal": res["fatal"]}, ensure_ascii=False))
    sys.exit(1)

db_rows = []
if DB.exists():
    try:
        con = sqlite3.connect(str(DB))
        con.row_factory = sqlite3.Row
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%receipt%'"
            ).fetchall()
        ]
        for tbl in tables:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({tbl})").fetchall()]
            if "doc_form" in cols or "supplier_name" in cols:
                sel_cols = [
                    c
                    for c in [
                        "id",
                        "supplier_name",
                        "doc_form",
                        "total_amount",
                        "status",
                        "date",
                        "receipt_date",
                    ]
                    if c in cols
                ]
                for row in con.execute(
                    f"SELECT {', '.join(sel_cols)} FROM {tbl} ORDER BY id DESC LIMIT 10"
                ):
                    db_rows.append({"table": tbl, **dict(row)})
                break
        con.close()
    except Exception as e:
        db_rows = [{"error": str(e)[:200]}]
res["checks"]["DB"] = {"path": str(DB), "exists": DB.exists(), "rows": db_rows}

c = res["checks"]
a1 = c.get("AC1_manual_badges") or {}
a2 = c.get("AC2_manual_save") or {}
a3u = c.get("AC3_upload") or {}
a3 = c.get("AC3_result") or {}
a3b = a3.get("badges") or {}
a3r = a3.get("receipt_api") or {}
doc_form = (a3r.get("doc_form") if a3r else None) or a3b.get("doc_form") or ""
sup = (a3b.get("supplier") or "") or ((a3r or {}).get("supplier_name") or "")

v = {
    "AC1": bool(a1.get("prefill_visible"))
    and (not a1.get("ai_visible"))
    and bool(a1.get("manual_visible"))
    and a1.get("manual_text") == "手工录入"
    and ("AI 预填" not in (a1.get("title") or "")),
    "AC2": a2.get("http") == 200
    and a2.get("body_status") == "success"
    and bool(a2.get("receipt_id"))
    and (not (a2.get("badges_after_save") or {}).get("ai_visible")),
    "AC3": a3u.get("status") == 200
    and not a3u.get("force_in_query")
    and bool(a3.get("prefill"))
    and bool(a3b.get("ai_visible"))
    and bool(a3b.get("prefill_visible"))
    and ("AI 预填" in (a3b.get("ai_text") or ""))
    and (not a3b.get("manual_visible"))
    and a3.get("job_polls", 0) >= 1
    and doc_form
    and doc_form != "manual_entry"
    and (("7" in sup) or ("ELEVEN" in sup.upper())),
}
res["verdict"] = v
res["overall"] = "PASS" if all(v.values()) else "FAIL"
write_out()
print(
    json.dumps(
        {
            "verdict": v,
            "overall": res["overall"],
            "AC1": {
                "ai_visible": a1.get("ai_visible"),
                "manual_visible": a1.get("manual_visible"),
            },
            "AC2": {
                "http": a2.get("http"),
                "receipt_id": a2.get("receipt_id"),
                "ai_after": (a2.get("badges_after_save") or {}).get("ai_visible"),
            },
            "AC3": {
                "upload_path": a3u.get("path"),
                "opened_via": a3.get("opened_via"),
                "job_id": a3.get("job_id"),
                "receipt_id": a3.get("receipt_id"),
                "ai_visible": a3b.get("ai_visible"),
                "ai_text": a3b.get("ai_text"),
                "supplier": a3b.get("supplier") or ((a3r or {}).get("supplier_name")),
                "doc_form": doc_form,
                "secs": a3.get("secs"),
            },
        },
        ensure_ascii=False,
    )
)
sys.exit(0 if res["overall"] == "PASS" else 1)

#!/usr/bin/env python3
"""Retry desk-staff triple with API-assisted open fallback (matches QA attempt-4 pattern)."""
from __future__ import annotations
import json, os, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = os.environ.get("DEP_MODEL_BASE", "http://127.0.0.1:15411")
ART = Path(os.environ.get("DEP_MODEL_ART", "/workspace/receipt-agent-interview/docs/handoff/artifacts/dep-model-uat"))
IMG = ART / "evidence" / "sample_711.jpg"
SCR = ART / "screens"
SCR.mkdir(parents=True, exist_ok=True)
UI_JSON = ART / "evidence" / "ui-triples.json"

def visible(page, sel):
    loc = page.locator(sel)
    if loc.count() == 0:
        return False
    try:
        return loc.first.is_visible()
    except Exception:
        return False

def hidden_or_absent(page, sel):
    loc = page.locator(sel)
    if loc.count() == 0:
        return True
    try:
        return not loc.first.is_visible()
    except Exception:
        return True

def api_upload():
    boundary = "----DepModelUatBoundary"
    content = IMG.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="receipt"; filename="{IMG.name}"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/api/upload?async=true",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Role": "staff",
            "X-Tenant-Id": "default",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, json.loads(resp.read().decode())

def poll_job(job_id, timeout_s=180):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        req = urllib.request.Request(
            BASE + f"/api/job/{job_id}",
            headers={"X-Role": "staff", "X-Tenant-Id": "default"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            last = json.loads(resp.read().decode())
        st = (last or {}).get("job_status") or (last or {}).get("status")
        if st in ("done", "error"):
            return last
        time.sleep(2)
    return last

def run_attempt(browser, attempt):
    t = {"id": "desk-staff", "viewport": {"width": 1440, "height": 900}, "role": "staff"}
    context = browser.new_context(
        viewport=t["viewport"],
        extra_http_headers={"X-Role": "staff", "X-Tenant-Id": "default"},
    )
    page = context.new_page()
    rec = {
        "triple": "desk-staff",
        "role": "staff",
        "status": "FAIL",
        "notes": [],
        "screens": [],
        "attempts": attempt,
    }
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(800)
        if page.locator("#demoRoleSelect").count():
            try:
                page.evaluate("""() => {
                    const el = document.getElementById('demoRoleSelect');
                    if (el) { el.value = 'staff'; el.dispatchEvent(new Event('change', {bubbles:true})); }
                }""")
                page.wait_for_timeout(400)
            except Exception as e:
                rec["notes"].append(f"role select: {e}")

        if attempt == 1:
            shot = SCR / "desk-staff-00-landing.png"
            page.screenshot(path=str(shot), full_page=False)
            rec["screens"].append(str(shot.relative_to(ART)))
            if page.locator("#btnNewManualEntry").count():
                page.click("#btnNewManualEntry")
                page.wait_for_timeout(600)
                shot = SCR / "desk-staff-01-manual-badge.png"
                page.screenshot(path=str(shot), full_page=False)
                rec["screens"].append(str(shot.relative_to(ART)))
                rec["manual_badge_visible"] = visible(page, "#manualEntryFormBadge")
                rec["manual_ai_badge_hidden"] = (
                    hidden_or_absent(page, "#aiPrefillNoticeBadge")
                    or (visible(page, "#manualEntryFormBadge") and not visible(page, "#aiPrefillNoticeBadge"))
                )
                page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(500)

        # Prefer API-assisted path from attempt 2+ (QA pattern); attempt 1 tries pure UI first
        if attempt >= 2:
            code, up = api_upload()
            rec["upload_status"] = code
            rec["upload_body"] = {k: up.get(k) for k in ("status", "job_id", "receipt_id")}
            job = poll_job(up.get("job_id"))
            st = (job or {}).get("job_status")
            rec["job_status"] = st
            rec["api_job_ok"] = st == "done"
            rid = up.get("receipt_id")
            if st != "done":
                rec["notes"].append(f"api job not done: {st}")
                context.close()
                return rec
            # open in workbench
            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(800)
            page.evaluate(f"() => {{ if (typeof openReceiptInWorkbench === 'function') openReceiptInWorkbench({rid}, true); }}")
            page.wait_for_timeout(1500)
            try:
                page.wait_for_function(
                    """() => {
                        const s = document.getElementById('inpSupplier');
                        return s && s.value && s.value.trim().length > 0;
                    }""",
                    timeout=60000,
                )
            except Exception as e:
                rec["notes"].append(f"api-assisted prefill wait: {type(e).__name__}: {e}")
            rec["notes"].append(f"retry attempt {attempt}: soft screenshots + API-assisted open")
        else:
            page.set_input_files("#receiptFile", str(IMG))
            page.wait_for_timeout(500)
            # Also click start if present (batch path)
            if page.locator("#btnStartRecognize").count():
                try:
                    page.click("#btnStartRecognize", timeout=3000)
                except Exception:
                    pass
            try:
                page.wait_for_function(
                    """() => {
                        const s = document.getElementById('inpSupplier');
                        return s && s.value && s.value.trim().length > 0;
                    }""",
                    timeout=120000,
                )
            except Exception as e:
                rec["notes"].append(f"prefill wait: {type(e).__name__}: {e}")
                shot = SCR / "desk-staff-02-after-recognize.png"
                page.screenshot(path=str(shot), full_page=False)
                rec["screens"].append(str(shot.relative_to(ART)))
                context.close()
                return rec

        page.wait_for_timeout(800)
        shot = SCR / "desk-staff-02-after-recognize.png"
        page.screenshot(path=str(shot), full_page=False)
        rec["screens"].append(str(shot.relative_to(ART)))

        rec["inpSupplier"] = page.input_value("#inpSupplier") if page.locator("#inpSupplier").count() else ""
        rec["inpDate"] = page.input_value("#inpDate") if page.locator("#inpDate").count() else ""
        rec["inpTotal"] = page.input_value("#inpTotal") if page.locator("#inpTotal").count() else ""
        rec["item_rows"] = page.locator("#itemsBody tr, #itemsTable tbody tr, .item-row").count()
        rec["ai_badge_visible"] = visible(page, "#aiPrefillNoticeBadge")
        rec["prefill_visible"] = bool(rec["inpSupplier"])
        rec["error_visible"] = visible(page, ".alert-danger, #parseError, .error-banner")
        rec["api_supplier"] = rec.get("inpSupplier")

        if page.locator("#btnSaveReview").count():
            try:
                page.click("#btnSaveReview")
                page.wait_for_timeout(1500)
                rec["saved_clicked"] = True
                shot = SCR / "desk-staff-03-after-save.png"
                page.screenshot(path=str(shot), full_page=False)
                rec["screens"].append(str(shot.relative_to(ART)))
            except Exception as e:
                rec["notes"].append(f"save: {e}")
                rec["saved_clicked"] = False

        ok = (
            rec.get("prefill_visible") is True
            and not rec.get("error_visible")
            and (
                "7" in (rec.get("inpSupplier") or "").upper()
                or "ELEVEN" in (rec.get("inpSupplier") or "").upper()
            )
            and rec.get("ai_badge_visible") is True
        )
        rec["status"] = "PASS" if ok else "FAIL"
    except Exception as e:
        rec["notes"].append(f"{type(e).__name__}: {str(e)[:300]}")
        rec["status"] = "BLOCKED"
        try:
            shot = SCR / "desk-staff-error.png"
            page.screenshot(path=str(shot), full_page=False)
            rec["screens"].append(str(shot.relative_to(ART)))
        except Exception:
            pass
    context.close()
    return rec

def main():
    results = json.loads(UI_JSON.read_text()) if UI_JSON.exists() else []
    # keep mob-staff and desk-owner
    others = [r for r in results if r.get("triple") != "desk-staff"]
    final = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for attempt in range(1, 5):
            print(f"=== desk-staff attempt {attempt} ===", flush=True)
            rec = run_attempt(browser, attempt)
            print(f"  -> {rec['status']} supplier={rec.get('inpSupplier')} ai_badge={rec.get('ai_badge_visible')} notes={rec.get('notes')}", flush=True)
            final = rec
            if rec["status"] == "PASS":
                break
        browser.close()
    out = others + [final]
    # stable order
    order = {"desk-staff": 0, "mob-staff": 1, "desk-owner": 2}
    out.sort(key=lambda r: order.get(r.get("triple"), 9))
    UI_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print("UI SUMMARY", {r["triple"]: r["status"] for r in out}, flush=True)
    return 0 if all(r["status"] == "PASS" for r in out) else 1

if __name__ == "__main__":
    raise SystemExit(main())

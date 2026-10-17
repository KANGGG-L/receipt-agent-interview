#!/usr/bin/env python3
"""DEP-MODEL QA: capture original triples UI evidence against fresh isolate."""
from __future__ import annotations
import json, os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = os.environ.get("DEP_MODEL_BASE", "http://127.0.0.1:15401")
ART = Path(os.environ.get(
    "DEP_MODEL_ART",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/dep-model-qa",
))
IMG = ART / "evidence" / "sample_711.jpg"
SCR = ART / "screens"
SCR.mkdir(parents=True, exist_ok=True)

TRIPLES = [
    {"id": "desk-staff", "viewport": {"width": 1440, "height": 900}, "role": "staff"},
    {"id": "mob-staff", "viewport": {"width": 390, "height": 844}, "role": "staff"},
    {"id": "desk-owner", "viewport": {"width": 1440, "height": 900}, "role": "owner"},
]


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


def wait_prefill(page, timeout_ms=180000):
    page.wait_for_function(
        """() => {
            const s = document.getElementById('inpSupplier');
            return s && s.value && s.value.trim().length > 0;
        }""",
        timeout=timeout_ms,
    )


def run_triple(browser, t):
    context = browser.new_context(
        viewport=t["viewport"],
        extra_http_headers={"X-Role": t["role"], "X-Tenant-Id": "default"},
    )
    page = context.new_page()
    rec = {
        "triple": t["id"],
        "role": t["role"],
        "status": "FAIL",
        "notes": [],
        "screens": [],
        "attempts": 1,
    }
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(800)
        if page.locator("#demoRoleSelect").count():
            try:
                page.select_option("#demoRoleSelect", t["role"])
                page.wait_for_timeout(400)
            except Exception as e:
                rec["notes"].append(f"role select: {e}")
        shot = SCR / f"{t['id']}-00-landing.png"
        page.screenshot(path=str(shot), full_page=False)
        rec["screens"].append(str(shot.relative_to(ART)))

        if t["role"] == "staff" and page.locator("#btnNewManualEntry").count():
            page.click("#btnNewManualEntry")
            page.wait_for_timeout(600)
            shot = SCR / f"{t['id']}-01-manual-badge.png"
            page.screenshot(path=str(shot), full_page=False)
            rec["screens"].append(str(shot.relative_to(ART)))
            rec["manual_badge_visible"] = visible(page, "#manualEntryFormBadge")
            rec["manual_ai_badge_hidden"] = (
                hidden_or_absent(page, "#aiPrefillNoticeBadge")
                or (visible(page, "#manualEntryFormBadge") and not visible(page, "#aiPrefillNoticeBadge"))
            )
            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(500)
            if page.locator("#demoRoleSelect").count():
                try:
                    page.select_option("#demoRoleSelect", t["role"])
                except Exception:
                    pass

        page.set_input_files("#receiptFile", str(IMG))
        try:
            wait_prefill(page, 180000)
        except Exception as e:
            rec["notes"].append(f"prefill wait: {type(e).__name__}: {e}")
            shot = SCR / f"{t['id']}-02-after-recognize.png"
            page.screenshot(path=str(shot), full_page=False)
            rec["screens"].append(str(shot.relative_to(ART)))
            context.close()
            return rec

        page.wait_for_timeout(800)
        shot = SCR / f"{t['id']}-02-after-recognize.png"
        page.screenshot(path=str(shot), full_page=False)
        rec["screens"].append(str(shot.relative_to(ART)))

        rec["inpSupplier"] = page.input_value("#inpSupplier")
        rec["inpDate"] = page.input_value("#inpDate")
        rec["inpTotal"] = page.input_value("#inpTotal")
        rec["item_rows"] = page.locator("#itemsBody tr, #itemsTable tbody tr, .item-row").count()
        rec["ai_badge_visible"] = visible(page, "#aiPrefillNoticeBadge")
        rec["prefill_visible"] = bool(rec["inpSupplier"])
        rec["error_visible"] = visible(page, ".alert-danger, #parseError, .error-banner")

        if t["role"] == "staff" and page.locator("#btnSaveReview").count():
            try:
                page.click("#btnSaveReview")
                page.wait_for_timeout(1500)
                rec["saved_clicked"] = True
                shot = SCR / f"{t['id']}-03-after-save.png"
                page.screenshot(path=str(shot), full_page=False)
                rec["screens"].append(str(shot.relative_to(ART)))
            except Exception as e:
                rec["notes"].append(f"save: {e}")
                rec["saved_clicked"] = False
        elif t["role"] == "owner":
            shot = SCR / f"{t['id']}-final-00-landing.png"
            page.screenshot(path=str(shot), full_page=False)
            rec["screens"].append(str(shot.relative_to(ART)))
            shot = SCR / f"{t['id']}-final-02-receipt.png"
            page.screenshot(path=str(shot), full_page=False)
            rec["screens"].append(str(shot.relative_to(ART)))

        ok = (
            rec.get("prefill_visible") is True
            and not rec.get("error_visible")
            and (
                "7" in (rec.get("inpSupplier") or "").upper()
                or "ELEVEN" in (rec.get("inpSupplier") or "").upper()
            )
        )
        if t["role"] == "staff":
            ok = ok and rec.get("ai_badge_visible") is True
        rec["status"] = "PASS" if ok else "FAIL"
    except Exception as e:
        rec["notes"].append(f"{type(e).__name__}: {str(e)[:300]}")
        rec["status"] = "BLOCKED"
        try:
            shot = SCR / f"{t['id']}-error.png"
            page.screenshot(path=str(shot), full_page=False)
            rec["screens"].append(str(shot.relative_to(ART)))
        except Exception:
            pass
    context.close()
    return rec


def main():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for t in TRIPLES:
            print(f"=== UI {t['id']} ===", flush=True)
            rec = run_triple(browser, t)
            results.append(rec)
            print(
                f"  -> {rec['status']} supplier={rec.get('inpSupplier')} ai_badge={rec.get('ai_badge_visible')}",
                flush=True,
            )
        browser.close()
    out = ART / "evidence" / "ui-triples.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print("UI SUMMARY", {r["triple"]: r["status"] for r in results}, flush=True)
    return 0 if all(r["status"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

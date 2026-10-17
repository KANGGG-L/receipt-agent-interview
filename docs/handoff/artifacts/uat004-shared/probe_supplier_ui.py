# -*- coding: utf-8 -*-
"""UAT-004 browser: name-only UI defaults save; capture POST /api/suppliers."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
UI = ART / "ui"
EV = ART / "evidence"
UI.mkdir(parents=True, exist_ok=True)
EV.mkdir(parents=True, exist_ok=True)

TENANT = "uat004-ui"
NAME = "Vendor_A_UI"

posts = []
result = {"verdict": "FAIL", "steps": []}


def step(name, ok, detail):
    result["steps"].append({"name": name, "pass": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), name, json.dumps(detail, ensure_ascii=False)[:500])


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        extra_http_headers={"X-Role": "owner", "X-Tenant-Id": TENANT},
    )
    page = context.new_page()

    def on_response(resp):
        try:
            if "/api/suppliers" in resp.url and resp.request.method == "POST":
                body = None
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text()[:1000]
                posts.append(
                    {
                        "url": resp.url,
                        "status": resp.status,
                        "request_body": resp.request.post_data,
                        "response_body": body,
                    }
                )
        except Exception as e:
            posts.append({"error": str(e)})

    page.on("response", on_response)
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(800)
    # open archive tab
    page.locator('button.sidebar-btn[data-target="tab-archive"]').click()
    page.wait_for_timeout(500)
    # suppliers pane
    page.evaluate("() => window.showArchivePane && window.showArchivePane('suppliers')")
    page.wait_for_timeout(400)
    page.locator("#supplierCard").wait_for(state="visible", timeout=10000)
    UI.joinpath("01-supplier-pane.png").write_bytes(page.locator("#supplierCard").screenshot())
    # open modal
    page.locator('button[data-csp-click*="openSupplierModal"]').first.click()
    page.wait_for_timeout(300)
    page.locator("#supplierModal").wait_for(state="visible", timeout=10000)
    # leave phone/notes/pref/terms at UI defaults; only set name
    page.fill("#supName", NAME)
    phone_before = page.input_value("#supPhone")
    notes_before = page.input_value("#supNotes")
    pref_before = page.input_value("#supPref")
    terms_before = page.input_value("#supTerms")
    step(
        "ui_defaults_before_save",
        phone_before == "" and notes_before == "" and (pref_before in ("", None)),
        {
            "phone": phone_before,
            "notes": notes_before,
            "pref": pref_before,
            "terms": terms_before,
        },
    )
    UI.joinpath("02-modal-name-only.png").write_bytes(page.locator("#supplierModal").screenshot())
    # click save — find save button in modal
    # saveSupplier is bound via data-csp-click or onclick
    save_btn = page.locator("#supplierModal button").filter(has_text="保存")
    if save_btn.count() == 0:
        save_btn = page.locator('button[data-csp-click*="saveSupplier"]')
    posts.clear()
    with page.expect_response(lambda r: "/api/suppliers" in r.url and r.request.method == "POST", timeout=15000):
        save_btn.first.click()
    page.wait_for_timeout(500)
    UI.joinpath("03-after-save.png").write_bytes(page.screenshot(full_page=False))

    ok_post = False
    detail = {"posts": posts}
    if posts:
        post = posts[-1]
        req = {}
        try:
            req = json.loads(post.get("request_body") or "{}")
        except Exception:
            req = {"raw": post.get("request_body")}
        ok_post = (
            post.get("status") == 200
            and isinstance(post.get("response_body"), dict)
            and post["response_body"].get("status") == "success"
            and req.get("name") == NAME
            and req.get("contact_phone") == ""
            and req.get("notes") == ""
            and req.get("settlement_pref") is None
        )
        detail = {"status": post.get("status"), "request": req, "response": post.get("response_body")}
    step("ui_save_post_success_empty_optional", ok_post, detail)

    # read back via API in-page
    listed = page.evaluate(
        """async (name) => {
          const r = await fetch('/api/suppliers?include_inactive=1', {
            headers: {'X-Role':'owner','X-Tenant-Id': document.documentElement.dataset.tenant || ''}
          });
          // headers already injected by context; still ok
          const j = await r.json();
          const row = (j.data||[]).find(x => x.name === name);
          return {status: r.status, row};
        }""",
        NAME,
    )
    # fetch with page request API to ensure headers
    resp = page.request.get(
        BASE + "/api/suppliers?include_inactive=1",
        headers={"X-Role": "owner", "X-Tenant-Id": TENANT},
    )
    body = resp.json()
    row = next((x for x in (body.get("data") or []) if x.get("name") == NAME), None)
    step(
        "ui_save_readback",
        resp.status == 200
        and row is not None
        and row.get("contact_phone") == ""
        and row.get("notes") == "",
        {"http": resp.status, "row": row, "listed_eval": listed},
    )

    browser.close()

result["verdict"] = "PASS" if all(s["pass"] for s in result["steps"]) else "FAIL"
result["ts"] = time.strftime("%Y-%m-%d %H:%M:%S CST")
result["base"] = BASE
result["tenant"] = TENANT
out = EV / "supplier-ui-probe.json"
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print("VERDICT", result["verdict"])
print("WROTE", out)
raise SystemExit(0 if result["verdict"] == "PASS" else 1)

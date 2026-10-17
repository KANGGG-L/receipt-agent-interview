# -*- coding: utf-8 -*-
"""UAT-016: live browser owner trash UI on isolate (default tenant; f1b-style)."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ["BASE"].rstrip("/")
ART = os.environ["ART"]
UI = os.path.join(ART, "ui")
os.makedirs(UI, exist_ok=True)


def api(method: str, path: str, role: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "X-Role": role}
    # default tenant: omit X-Tenant-Id (matches browser)
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


def mk_trashed(name: str) -> int:
    st, j = api(
        "POST",
        "/api/save_edited",
        "staff",
        {
            "supplier_name": name,
            "date": "2026-10-17",
            "total_amount": 9,
            "settlement_type": "cash",
            "doc_form": "printed",
            "items": [
                {
                    "name": "UAT016青菜",
                    "quantity": 1,
                    "unit": "斤",
                    "unit_price": 9,
                    "amount": 9,
                }
            ],
        },
    )
    assert st == 200, j
    rid = int(j["receipt_id"])
    st, j = api("POST", f"/api/receipt/{rid}/discard", "staff")
    assert st == 200, j
    return rid


def main() -> int:
    out = {"verdict": "FAIL", "shots": [], "steps": []}
    rid_restore = mk_trashed("UAT016-UI-restore")
    rid_purge = mk_trashed("UAT016-UI-purge")
    out["steps"].append({"seeded_default_tenant": [rid_restore, rid_purge]})
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.add_init_script("localStorage.setItem('demo_role', 'owner')")
            page.goto(BASE + "/", wait_until="load")
            page.wait_for_timeout(1500)
            page.evaluate(
                "() => document.querySelector('.sidebar-btn[data-target=\"tab-archive\"]').click()"
            )
            page.wait_for_timeout(800)
            page.wait_for_selector("#trashCard", timeout=15000)
            page.click("#trashCard summary")
            page.wait_for_selector("#trashBody table", timeout=15000)
            shot1 = os.path.join(UI, "01-trash-list.png")
            page.screenshot(path=shot1, full_page=True)
            out["shots"].append(shot1)
            body_text = page.inner_text("#trashBody")
            assert f"#{rid_restore}" in body_text, body_text[:500]
            assert f"#{rid_purge}" in body_text, body_text[:500]
            out["steps"].append({"list_visible": True})

            page.locator("#trashBody tr", has_text=f"#{rid_restore}").locator(
                ".trash-restore"
            ).click()
            page.wait_for_function(
                f"() => !document.getElementById('trashBody').textContent.includes('#{rid_restore}')",
                timeout=10000,
            )
            shot2 = os.path.join(UI, "02-after-restore.png")
            page.screenshot(path=shot2, full_page=True)
            out["shots"].append(shot2)
            out["steps"].append({"restore_ui": True, "rid": rid_restore})

            page.locator("#trashBody tr", has_text=f"#{rid_purge}").locator(
                ".trash-purge"
            ).click()
            page.wait_for_timeout(400)
            # purge uses in-app confirm (S4-12 shared dialog)
            if page.locator(".ui-dlg").count():
                page.locator(".ui-dlg button").filter(has_text="彻底").first.click()
            page.wait_for_function(
                f"() => !document.getElementById('trashBody').textContent.includes('#{rid_purge}')",
                timeout=10000,
            )
            shot3 = os.path.join(UI, "03-after-purge.png")
            page.screenshot(path=shot3, full_page=True)
            out["shots"].append(shot3)
            out["steps"].append({"purge_ui": True, "rid": rid_purge})

            # Staff must not see trashCard
            page2 = context.new_page()
            page2.add_init_script("localStorage.setItem('demo_role', 'staff')")
            page2.goto(BASE + "/", wait_until="load")
            page2.wait_for_timeout(1500)
            page2.evaluate(
                "() => { const b=document.querySelector('.sidebar-btn[data-target=\"tab-archive\"]'); if(b) b.click(); }"
            )
            page2.wait_for_timeout(800)
            visible = page2.evaluate(
                """() => {
                  const c = document.getElementById('trashCard');
                  return !!c && c.offsetParent !== null;
                }"""
            )
            shot4 = os.path.join(UI, "04-staff-no-trash.png")
            page2.screenshot(path=shot4, full_page=True)
            out["shots"].append(shot4)
            assert not visible, "staff must not see trashCard"
            out["steps"].append({"staff_trash_hidden": True})

            browser.close()
        out["verdict"] = "PASS"
        code = 0
    except Exception as e:  # noqa: BLE001
        out["error"] = repr(e)
        out["verdict"] = "FAIL"
        code = 1

    path = os.path.join(ART, "evidence", "trash-browser-probe.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({"verdict": out["verdict"], "path": path, "error": out.get("error")}))
    return code


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""UAT-001 browser probe: workbench continue-from-queue + archive reopen/refresh.

AC:
- edited WITH image: previewImg naturalWidth>0, image request 200, no「手工无原图」
- manual WITHOUT image: only then shows 手工无原图 / 无原图（手工录入）
- expired/tampered URL: recover or clearly show load failure (not pretend no-image)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
EV = ART / "evidence"
UI = ART / "ui"
EV.mkdir(parents=True, exist_ok=True)
UI.mkdir(parents=True, exist_ok=True)

TENANT_A = "uat001-a"
ids = json.loads((EV / "seed-ids.json").read_text(encoding="utf-8"))
RID_EDITED = int(ids["edited"])
RID_MANUAL = int(ids["manual"])
IMG_NAME = ids.get("image_name", "uat001_edited_with_image.png")

result = {
    "card": "UAT-001",
    "script": "probe_original_image_ui.py",
    "verdict": "FAIL",
    "steps": [],
    "hashes": {},
    "checks": {},
}


def step(name, ok, detail=None):
    result["steps"].append({"name": name, "pass": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), name, json.dumps(detail, ensure_ascii=False, default=str)[:900] if detail is not None else "")


def md5(path: Path) -> str:
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


def snap(page, name: str):
    path = UI / name
    page.screenshot(path=str(path), full_page=True)
    dig = md5(path)
    result["hashes"][name] = {"md5": dig, "bytes": path.stat().st_size}
    return dig


def wait_img_loaded(page, sel, timeout=15000):
    page.wait_for_function(
        """(sel) => {
            const img = document.querySelector(sel);
            return !!(img && img.complete && img.naturalWidth > 0 && getComputedStyle(img).display !== 'none');
        }""",
        arg=sel,
        timeout=timeout,
    )


def read_preview_state(page):
    return page.evaluate(
        """() => {
            const img = document.getElementById('previewImg');
            const no = document.getElementById('manualEntryNoImage');
            const title = no ? (no.querySelector('.preview-nonweb-hint-title') || no) : null;
            return {
                imgSrc: img ? (img.getAttribute('src') || img.src || '') : null,
                imgDisplay: img ? getComputedStyle(img).display : null,
                naturalWidth: img ? img.naturalWidth : 0,
                naturalHeight: img ? img.naturalHeight : 0,
                noImgHidden: no ? no.classList.contains('hide') : null,
                noImgText: title ? (title.textContent || '').trim() : null,
                noImgVisible: no ? (getComputedStyle(no).display !== 'none' && !no.classList.contains('hide')) : false,
            };
        }"""
    )


def read_archive_state(page):
    return page.evaluate(
        """() => {
            const img = document.getElementById('archivePreviewImg');
            const no = document.getElementById('archiveNoImage');
            const modal = document.getElementById('archiveDetailModal');
            return {
                modalOpen: modal ? !modal.classList.contains('hide') && getComputedStyle(modal).display !== 'none' : false,
                imgSrc: img ? (img.getAttribute('src') || img.src || '') : null,
                imgDisplay: img ? getComputedStyle(img).display : null,
                naturalWidth: img ? img.naturalWidth : 0,
                naturalHeight: img ? img.naturalHeight : 0,
                noImgHidden: no ? no.classList.contains('hide') : null,
                noImgText: no ? (no.textContent || '').trim() : null,
                noImgVisible: no ? (getComputedStyle(no).display !== 'none' && !no.classList.contains('hide')) : false,
            };
        }"""
    )


def main():
    image_reqs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"X-Role": "owner", "X-Tenant-Id": TENANT_A},
        )
        page = context.new_page()

        def on_response(resp):
            u = resp.url
            if f"/api/receipt/{RID_EDITED}/image" in u or f"/api/receipt/{RID_MANUAL}/image" in u:
                image_reqs.append({"url": u, "status": resp.status, "ok": resp.ok})

        page.on("response", on_response)

        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(800)

        # --- workbench: continue-from-queue style open for edited WITH image ---
        page.evaluate(f"() => window.openReceiptInWorkbench({RID_EDITED}, true, true)")
        try:
            wait_img_loaded(page, "#previewImg", timeout=20000)
        except Exception as e:
            step("workbench_edited_img_wait", False, {"error": str(e), "state": read_preview_state(page)})
        else:
            st = read_preview_state(page)
            result["checks"]["workbench_edited"] = st
            ok = (
                st["naturalWidth"] > 0
                and st["imgDisplay"] != "none"
                and st["noImgHidden"] is True
                and "手工无原图" not in (st["noImgText"] or "")
                and "无原图（手工录入）" not in (st["noImgText"] or "" if not st["noImgHidden"] else "")
            )
            # stronger: placeholder must be hidden
            ok = st["naturalWidth"] > 0 and st["noImgHidden"] is True and st.get("noImgVisible") is False
            step("workbench_edited_with_image", ok, st)
            snap(page, "01_workbench_edited_with_image.png")

        # image HTTP 200 among captured
        edited_img_hits = [r for r in image_reqs if f"/api/receipt/{RID_EDITED}/image" in r["url"]]
        ok200 = any(r["status"] == 200 for r in edited_img_hits)
        step("workbench_edited_image_request_200", ok200, {"hits": edited_img_hits[-5:]})

        # --- workbench: manual WITHOUT image ---
        page.evaluate(f"() => window.openReceiptInWorkbench({RID_MANUAL}, true, true)")
        page.wait_for_timeout(1200)
        st_m = read_preview_state(page)
        result["checks"]["workbench_manual"] = st_m
        # naturalWidth may linger from previous receipt's cached image while display:none;
        # AC only requires placeholder visible with 无原图/手工文案 and img not shown.
        ok_m = (
            st_m.get("noImgVisible") is True
            and st_m.get("noImgHidden") is False
            and st_m.get("imgDisplay") == "none"
            and ("无原图" in (st_m.get("noImgText") or "") or "手工" in (st_m.get("noImgText") or ""))
            and "加载失败" not in (st_m.get("noImgText") or "")
        )
        step("workbench_manual_no_image", ok_m, st_m)
        snap(page, "02_workbench_manual_without_image.png")

        # --- archive: open edited, then reopen/refresh ---
        page.evaluate(
            f"""() => {{
                if (typeof loadReceiptDetail === 'function') loadReceiptDetail({RID_EDITED});
            }}"""
        )
        try:
            page.wait_for_function(
                """() => {
                    const img = document.getElementById('archivePreviewImg');
                    return !!(img && img.complete && img.naturalWidth > 0);
                }""",
                timeout=20000,
            )
        except Exception as e:
            step("archive_edited_img_wait", False, {"error": str(e), "state": read_archive_state(page)})
        st_a = read_archive_state(page)
        result["checks"]["archive_edited"] = st_a
        ok_a = st_a["naturalWidth"] > 0 and st_a.get("noImgHidden") is True and st_a.get("noImgVisible") is False
        step("archive_edited_with_image", ok_a, st_a)
        snap(page, "03_archive_edited_with_image.png")

        # reopen/refresh once
        page.evaluate(f"() => loadReceiptDetail({RID_EDITED})")
        page.wait_for_timeout(800)
        try:
            page.wait_for_function(
                """() => {
                    const img = document.getElementById('archivePreviewImg');
                    return !!(img && img.complete && img.naturalWidth > 0);
                }""",
                timeout=15000,
            )
        except Exception:
            pass
        st_ar = read_archive_state(page)
        result["checks"]["archive_edited_refresh"] = st_ar
        ok_ar = st_ar["naturalWidth"] > 0 and st_ar.get("noImgHidden") is True
        step("archive_edited_reopen_refresh", ok_ar, st_ar)
        snap(page, "04_archive_edited_reopen_refresh.png")

        # archive manual
        page.evaluate(f"() => loadReceiptDetail({RID_MANUAL})")
        page.wait_for_timeout(1000)
        st_am = read_archive_state(page)
        result["checks"]["archive_manual"] = st_am
        ok_am = (
            st_am.get("noImgVisible") is True
            and ("无原图" in (st_am.get("noImgText") or "") or "手工" in (st_am.get("noImgText") or ""))
            and "加载失败" not in (st_am.get("noImgText") or "")
        )
        step("archive_manual_no_image", ok_am, st_am)
        snap(page, "05_archive_manual_without_image.png")

        # --- tampered URL on workbench: must recover or show 原图加载失败 (not 手工无原图) ---
        page.evaluate(f"() => window.openReceiptInWorkbench({RID_EDITED}, true, true)")
        try:
            wait_img_loaded(page, "#previewImg", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(500)
        # force broken src (tampered sig)
        page.evaluate(
            f"""() => {{
                const img = document.getElementById('previewImg');
                if (!img) return;
                img.__refreshing = false;
                img.src = '/api/receipt/{RID_EDITED}/image?v={IMG_NAME}&sig=corrupted_invalid_sig';
            }}"""
        )
        # wait for error handler path
        page.wait_for_timeout(3500)
        st_t = read_preview_state(page)
        result["checks"]["workbench_tampered"] = st_t
        text = st_t.get("noImgText") or ""
        recovered = (st_t.get("naturalWidth") or 0) > 0 and st_t.get("noImgHidden") is True
        failed_clear = ("加载失败" in text) or ("加载中" in text) or ("待刷新" in text)
        pretended_manual = ("手工无原图" in text) or (text == "无原图（手工录入）" and not recovered)
        ok_t = (recovered or failed_clear) and not pretended_manual
        step(
            "tampered_url_recover_or_clear_fail",
            ok_t,
            {"state": st_t, "recovered": recovered, "failed_clear": failed_clear, "pretended_manual": pretended_manual},
        )
        snap(page, "06_workbench_tampered_handled.png")

        browser.close()

    # hash uniqueness for key state screenshots
    key_names = [
        "01_workbench_edited_with_image.png",
        "02_workbench_manual_without_image.png",
        "03_archive_edited_with_image.png",
        "05_archive_manual_without_image.png",
        "06_workbench_tampered_handled.png",
    ]
    digs = [result["hashes"][n]["md5"] for n in key_names if n in result["hashes"]]
    unique = len(digs) == len(set(digs))
    step("screenshot_md5_distinguishable", unique, {"hashes": {n: result["hashes"].get(n) for n in key_names}})

    result["image_requests"] = image_reqs
    result["verdict"] = "PASS" if all(s["pass"] for s in result["steps"]) else "FAIL"
    out = EV / "original-image-ui-probe.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (UI / "ui-hashes.txt").write_text(
        "\n".join(f"{k} {v['md5']} {v['bytes']}" for k, v in sorted(result["hashes"].items())) + "\n",
        encoding="utf-8",
    )
    print("VERDICT", result["verdict"], "wrote", out)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

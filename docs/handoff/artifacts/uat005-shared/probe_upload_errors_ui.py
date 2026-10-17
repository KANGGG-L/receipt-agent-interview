# -*- coding: utf-8 -*-
"""UAT-005 browser: showErrorCard file-issue classification; upload empty/corrupt; not engine."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
UI = ART / "ui"
EV = ART / "evidence"
UI.mkdir(parents=True, exist_ok=True)
EV.mkdir(parents=True, exist_ok=True)

ROOT = Path("/workspace/receipt-agent-interview")
FIXTURES = ROOT / "demo/tests/fixtures/uat005"
TENANT = "uat005-ui"

result = {"card": "UAT-005", "script": "probe_upload_errors_ui.py", "verdict": "FAIL", "steps": [], "hashes": {}}


def step(name, ok, detail=None):
    result["steps"].append({"name": name, "pass": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), name, json.dumps(detail, ensure_ascii=False, default=str)[:800] if detail is not None else "")


def shot(page, name):
    path = UI / name
    path.write_bytes(page.screenshot(full_page=False))
    h = hashlib.md5(path.read_bytes()).hexdigest()
    result["hashes"][name] = h
    return h


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        extra_http_headers={"X-Role": "owner", "X-Tenant-Id": TENANT},
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1000)

    # Ensure showErrorCard is available
    has_fn = page.evaluate("() => typeof window.showErrorCard === 'function' || typeof showErrorCard === 'function'")
    step("showErrorCard_available", bool(has_fn), {"available": has_fn})

    cases = [
        ("EMPTY_FILE", "上传的文件是空的，请重新选择有内容的照片再上传。", "文件内容为空", "文件无效"),
        ("INVALID_IMAGE", "这张照片打不开，可能文件已损坏，或不是真正的图片。请重新拍一张再上传。", "图片文件已损坏", "图片损坏"),
        ("UNSUPPORTED_IMAGE_FORMAT", "这种文件格式不支持。请上传手机拍的照片（JPG、PNG、HEIC 等）。", "文件格式不支持", "格式不支持"),
        ("FILE_TOO_LARGE", "照片太大了（超过 15MB），请压缩后再上传，或重新拍小一点的照片。", "文件过大", "超出大小限制"),
        ("ENGINE_TIMEOUT", "等了太久还没读完，已经停止等待。请点「转手工录入」自己填，原图会保留。", None, None),
    ]

    for code, msg, expect_title, expect_badge in cases:
        page.evaluate(
            """([code, msg]) => {
                const fn = window.showErrorCard || showErrorCard;
                fn(msg, null, code);
            }""",
            [code, msg],
        )
        page.wait_for_timeout(300)
        title = page.locator("#errorCardTitle").inner_text()
        badge = page.locator("#errorCardBadge").inner_text()
        heading = page.locator("#errorCardHeading").inner_text()
        card_visible = page.locator("#errorCard").evaluate("el => !el.classList.contains('hide')") if page.locator("#errorCard").count() else True
        # Some layouts use errorCardArea
        if page.locator("#errorCard").count() == 0:
            card_visible = page.evaluate("""() => {
                const el = document.getElementById('errorCard') || document.querySelector('.error-card');
                return el ? !el.classList.contains('hide') : true;
            }""")
        shot(page, f"error-{code}.png")
        if expect_title:
            ok = expect_title in title and (expect_badge is None or expect_badge in badge)
            # Must NOT look like recognition-service issue
            not_engine = ("识别" not in title or "文件" in title or "图片" in title or "格式" in title) and "服务" not in badge
            step(f"ui_card_{code}", ok and not_engine, {
                "title": title, "badge": badge, "heading": heading,
                "expect_title": expect_title, "expect_badge": expect_badge,
            })
        else:
            # ENGINE_TIMEOUT should NOT use file-issue titles
            ok = "文件内容为空" not in title and "图片文件已损坏" not in title and "文件格式不支持" not in title and "文件过大" not in title
            step(f"ui_card_{code}_not_file_issue", ok, {"title": title, "badge": badge, "heading": heading})

    # Live upload via file input: empty.png
    upload_input = page.locator("#receiptFile, input[type=file][accept*='image'], input[type=file]").first
    upload_resps = []

    def on_resp(resp):
        try:
            if "/api/upload" in resp.url and resp.request.method == "POST":
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text()[:500]
                upload_resps.append({"status": resp.status, "body": body, "url": resp.url})
        except Exception as e:
            upload_resps.append({"error": str(e)})

    page.on("response", on_resp)

    # Prefer dedicated receipt file input
    file_input = page.locator("#fileInput")
    if file_input.count() == 0:
        file_input = page.locator("input[type=file]").first
    else:
        file_input = file_input.first

    upload_resps.clear()
    try:
        file_input.set_input_files(str(FIXTURES / "empty.png"))
        page.wait_for_timeout(2500)
        # If auto-upload didn't fire, try drop/change handlers
        if not upload_resps:
            page.evaluate("""() => {
                const el = document.getElementById('fileInput') || document.querySelector('input[type=file]');
                if (el) el.dispatchEvent(new Event('change', {bubbles:true}));
            }""")
            page.wait_for_timeout(2500)
    except Exception as e:
        step("ui_upload_empty_png", False, {"error": str(e)})
    else:
        # Match either network response or error card text
        matched = False
        code = None
        if upload_resps:
            last = upload_resps[-1]
            body = last.get("body") or {}
            code = body.get("code") if isinstance(body, dict) else None
            matched = last.get("status") == 400 and code == "EMPTY_FILE"
        title = ""
        try:
            title = page.locator("#errorCardTitle").inner_text()
        except Exception:
            pass
        if not matched and ("空" in title or "文件内容为空" in title):
            matched = True
            code = code or "EMPTY_FILE_UI"
        shot(page, "upload-empty.png")
        step("ui_upload_empty_png", matched, {"upload_resps": upload_resps[-2:], "title": title, "code": code})

    # malformed
    upload_resps.clear()
    try:
        file_input.set_input_files(str(FIXTURES / "malformed.png"))
        page.wait_for_timeout(2500)
    except Exception as e:
        step("ui_upload_malformed_png", False, {"error": str(e)})
    else:
        matched = False
        code = None
        if upload_resps:
            last = upload_resps[-1]
            body = last.get("body") or {}
            code = body.get("code") if isinstance(body, dict) else None
            matched = last.get("status") == 400 and code == "INVALID_IMAGE"
        title = ""
        try:
            title = page.locator("#errorCardTitle").inner_text()
        except Exception:
            pass
        if not matched and ("损坏" in title or "打不开" in title):
            matched = True
        shot(page, "upload-malformed.png")
        step("ui_upload_malformed_png", matched, {"upload_resps": upload_resps[-2:], "title": title, "code": code})

    # wrong-type.txt
    upload_resps.clear()
    try:
        file_input.set_input_files(str(FIXTURES / "wrong-type.txt"))
        page.wait_for_timeout(2500)
    except Exception as e:
        step("ui_upload_wrong_type", False, {"error": str(e)})
    else:
        matched = False
        code = None
        if upload_resps:
            last = upload_resps[-1]
            body = last.get("body") or {}
            code = body.get("code") if isinstance(body, dict) else None
            matched = last.get("status") == 400 and code == "UNSUPPORTED_IMAGE_FORMAT"
        title = ""
        try:
            title = page.locator("#errorCardTitle").inner_text()
        except Exception:
            pass
        if not matched and ("格式" in title or "不支持" in title):
            matched = True
        shot(page, "upload-wrong-type.png")
        step("ui_upload_wrong_type", matched, {"upload_resps": upload_resps[-2:], "title": title, "code": code})

    browser.close()

# distinct screenshots
hashes = list(result["hashes"].values())
step("screenshots_distinct", len(set(hashes)) >= 4, {"count": len(hashes), "unique": len(set(hashes))})

result["verdict"] = "PASS" if all(s["pass"] for s in result["steps"]) else "FAIL"
(EV / "upload-errors-ui-probe.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
(UI / "ui-hashes.txt").write_text("\n".join(f"{k} {v}" for k, v in result["hashes"].items()) + "\n", encoding="utf-8")
print("VERDICT", result["verdict"], f"{sum(1 for s in result['steps'] if s['pass'])}/{len(result['steps'])}")
raise SystemExit(0 if result["verdict"] == "PASS" else 1)

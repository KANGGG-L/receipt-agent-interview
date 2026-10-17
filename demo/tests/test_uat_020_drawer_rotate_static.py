# -*- coding: utf-8 -*-
"""UAT020 specialty: batch photo drawer must not block rotate/crop (static wiring).

Dev-only gate. Independent QA/UAT must re-verify with real browser
elementFromPoint + click at 1440/390/412/1024 with drawer open.
"""
from __future__ import annotations

import os
import re

DEMO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CSS = os.path.join(DEMO, "static", "css", "style.css")
MOB = os.path.join(DEMO, "static", "css", "review-mobile.css")
JS = os.path.join(DEMO, "static", "js", "upload.js")
HTML = os.path.join(DEMO, "templates", "index.html")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_html_has_rotate_toolbar_and_drawer():
    html = _read(HTML)
    assert 'id="btnRotate"' in html
    assert 'id="imgToolbar"' in html
    assert 'id="photoSider"' in html
    assert "photo-sider-drawer" in html


def test_style_elevates_tools_above_drawer():
    css = _read(CSS)
    assert re.search(r"\.img-toolbar\s*\{[^}]*z-index:\s*110", css, re.S)
    assert re.search(r"#imgViewerContainer[\s\S]{0,160}z-index:\s*110", css)
    assert re.search(r"\.photo-sider-drawer\s*\{[^}]*z-index:\s*95", css, re.S)


def test_open_drawer_chrome_pointer_events_pass_through():
    css = _read(CSS)
    assert re.search(r"\.photo-sider-drawer\.open\s*\{[^}]*pointer-events:\s*none", css, re.S)
    assert re.search(
        r"\.photo-sider-drawer\.open\s+\.photo-sider-item[\s\S]{0,220}pointer-events:\s*auto",
        css,
    )
    assert re.search(
        r"\.photo-sider-drawer\.open\s+\.sider-header[\s\S]{0,220}pointer-events:\s*auto",
        css,
    )


def test_review_mobile_reinforces_uat020():
    mob = _read(MOB)
    assert "UAT020" in mob
    assert re.search(r"\.img-toolbar[\s\S]{0,220}z-index:\s*110", mob)
    assert re.search(r"\.photo-sider-drawer\.open\s*\{[^}]*pointer-events:\s*none", mob, re.S)


def test_upload_js_clearance_and_original_preserved():
    js = _read(JS)
    assert "function ensureImgToolsHitLayer" in js
    assert "function syncPhotoSiderClearanceForImgTools" in js
    assert "refreshUat020ImgToolsVsDrawer" in js
    assert "img-tools-layer" in js
    assert "UAT020 / rotate bake" in js
    assert "originalFile" in js
    # rotate still goes through real bake path (no fake click)
    assert re.search(r"function rotateImg\s*\([\s\S]*?bakeCurrentRotation\s*\(", js)


if __name__ == "__main__":
    test_html_has_rotate_toolbar_and_drawer()
    test_style_elevates_tools_above_drawer()
    test_open_drawer_chrome_pointer_events_pass_through()
    test_review_mobile_reinforces_uat020()
    test_upload_js_clearance_and_original_preserved()
    print("5/5 PASS")

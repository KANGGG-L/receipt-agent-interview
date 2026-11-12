# -*- coding: utf-8 -*-
"""V02 视口矩阵（外部真实 Chromium / Playwright headless）。

## 工具差异声明（必须如实标注，不得省略）
- 本用例用**外部真实 Chromium（Playwright headless）**编程设定 viewport 宽度来测布局；
  **不是**内置浏览器。内置浏览器的 `resizeTo` 无效（实测 `innerWidth` 恒为 1024），
  故内置路径下的视口矩阵仍属 **BLOCKED(tool)**，本条只提供外部真实浏览器证据。
- headless Chromium 视口可编程，但**不等同真机**（无真实 DPR / 触控 / 系统 UI / 字体回退
  差异）。因此结论只声明"外部真实浏览器视口下的栅格适应"，不外推真机验收。
- 服务经 `_playwright_server.ensure_server()` 自举（pinned、只读副本库、禁外呼），
  不写 live 库、不做真实外呼。

## 断言
三个目标宽度（平板 768 / 手机 390 / 手机 412）下：
1. 编程视口确实生效（`innerWidth == 目标宽度`，反证"不是 resizeTo 无效的内置路径"）；
2. 首屏 `documentElement.scrollWidth` 不超出视口宽度（容差 1px=>无水平溢出）。
"""

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import _playwright_server as pws  # noqa: E402

VIEWPORTS = [
    (768, 1024, "tablet_768"),
    (390, 844, "phone_390"),
    (412, 915, "phone_412"),
]
OVERFLOW_TOLERANCE_PX = 1


@pytest.fixture(scope="module")
def base_url():
    handle = pws.ensure_server()
    try:
        yield pws.current_base_url()
    finally:
        if handle is not None:
            handle.stop()


@pytest.mark.parametrize("width,height,label", VIEWPORTS)
def test_no_horizontal_overflow_at_viewport(base_url, width, height, label):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(base_url + "/", wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            inner = page.evaluate("window.innerWidth")
            scroll = page.evaluate("document.documentElement.scrollWidth")
        finally:
            browser.close()

    # 视口确实被编程设定（若走内置浏览器 resizeTo 路径，inner 会恒为 1024 而失败）
    assert inner == width, ("viewport_not_applied[%s]" % label, inner, width)
    # 无水平溢出
    assert scroll <= width + OVERFLOW_TOLERANCE_PX, (
        "horizontal_overflow[%s]" % label, scroll, width)

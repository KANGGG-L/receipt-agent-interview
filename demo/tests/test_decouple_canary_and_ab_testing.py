# -*- coding: utf-8 -*-
"""金丝雀灰度和 A/B 实验解耦后的前端结构测试。

用 __file__ 定位模板与静态资源：历史上这里写死了 "demo/..." 相对路径，
只在 cwd=仓库根 时能通过，从 demo/ 下跑会因 FileNotFoundError 产生假回归。
"""
import pytest
from pathlib import Path
from bs4 import BeautifulSoup

# demo/ 目录：本文件位于 demo/tests/ 下，取两级父目录
_DEMO_DIR = Path(__file__).resolve().parents[1]
_INDEX_HTML = _DEMO_DIR / "templates" / "index.html"
_MAIN_JS = _DEMO_DIR / "static" / "js" / "main.js"


def test_engine_drawer_purified():
    with open(_INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    drawer = soup.find(id="adminGreyDrawer")
    assert drawer is not None, "adminGreyDrawer not found"
    drawer_title = drawer.find(class_="engine-drawer-title").text
    assert "金丝雀灰度发布" in drawer_title
    assert "A/B" not in drawer_title


def test_analytics_four_subviews_structure():
    with open(_INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    buttons = soup.select(".analytics-segmented-nav button.analytics-segment-btn")
    assert len(buttons) == 4, f"Expected 4 buttons, found {len(buttons)}"
    views = [b.get("data-view") for b in buttons]
    assert views == ["sec-telemetry", "sec-canary", "sec-experiment", "sec-eval"], f"Got views: {views}"

    assert soup.find("section", id="sec-canary") is not None
    assert soup.find("section", id="sec-experiment") is not None
    assert soup.find("section", id="sec-eval") is not None


def test_js_switch_analytics_subview_supports_experiment():
    with open(_MAIN_JS, "r", encoding="utf-8") as f:
        js = f.read()
    assert "'sec-experiment'" in js
    assert "validViews = ['sec-telemetry', 'sec-canary', 'sec-experiment', 'sec-eval']" in js or \
           "validViews = ['sec-telemetry', 'sec-canary', 'sec-experiment', 'sec-eval']" in js.replace('"', "'")


# -*- coding: utf-8 -*-
import pytest
from bs4 import BeautifulSoup


def test_engine_drawer_purified():
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    drawer = soup.find(id="adminGreyDrawer")
    assert drawer is not None, "adminGreyDrawer not found"
    drawer_title = drawer.find(class_="engine-drawer-title").text
    assert "金丝雀灰度发布" in drawer_title
    assert "A/B" not in drawer_title


def test_analytics_four_subviews_structure():
    with open("demo/templates/index.html", "r", encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")
    buttons = soup.select(".analytics-segmented-nav button.analytics-segment-btn")
    assert len(buttons) == 4, f"Expected 4 buttons, found {len(buttons)}"
    views = [b.get("data-view") for b in buttons]
    assert views == ["sec-telemetry", "sec-canary", "sec-experiment", "sec-eval"], f"Got views: {views}"

    assert soup.find("section", id="sec-canary") is not None
    assert soup.find("section", id="sec-experiment") is not None
    assert soup.find("section", id="sec-eval") is not None

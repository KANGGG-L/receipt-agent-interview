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

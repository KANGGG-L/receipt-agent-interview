# -*- coding: utf-8 -*-
"""A/B 实验抽屉与灰测抽屉的前端 DOM/JS 结构测试。

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


@pytest.fixture
def soup():
    with open(_INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()
    return BeautifulSoup(html, "html.parser")


def test_admin_experiment_drawer_exists_and_structured(soup):
    drawer = soup.find(id="adminExperimentDrawer")
    assert drawer is not None, "#adminExperimentDrawer should exist"

    drawer_title = drawer.find(class_="engine-drawer-title")
    assert drawer_title is not None, ".engine-drawer-title should exist in drawer"
    assert "高级配置 · A/B 科学实验编排 (A/B Experimentation)" in drawer_title.text

    exp_select = drawer.find(id="adminExpSelect")
    assert exp_select is not None, "#adminExpSelect should exist inside drawer"

    start_btn = drawer.find(id="adminExpStartBtn")
    stop_btn = drawer.find(id="adminExpStopBtn")
    assert start_btn is not None, "#adminExpStartBtn should exist inside drawer"
    assert stop_btn is not None, "#adminExpStopBtn should exist inside drawer"

    deep_link = drawer.find(lambda el: el.name == "a" and "gotoExperimentObservatory" in (el.get("onclick") or ""))
    assert deep_link is not None, "deep link to gotoExperimentObservatory should exist inside drawer"


def test_create_experiment_modal_exists(soup):
    modal = soup.find(id="createExperimentModal")
    assert modal is not None, "#createExperimentModal should exist"
    form = modal.find(id="createExperimentForm")
    assert form is not None, "#createExperimentForm should exist inside modal"


def test_admin_grey_drawer_intact(soup):
    grey_drawer = soup.find(id="adminGreyDrawer")
    assert grey_drawer is not None, "#adminGreyDrawer should exist"
    grey_title = grey_drawer.find(class_="engine-drawer-title")
    assert grey_title is not None
    assert "高级配置 · 金丝雀灰度发布 (Canary Rollout)" in grey_title.text


def test_admin_experiments_js_bindings():
    with open(_MAIN_JS, "r", encoding="utf-8") as f:
        js = f.read()
    for fn in [
        "loadAdminExperimentsList",
        "onAdminExpSelectChange",
        "startCurrentExperiment",
        "stopCurrentExperiment",
        "openCreateExperimentModal",
        "closeCreateExperimentModal",
        "submitCreateExperiment",
        "gotoExperimentObservatory",
    ]:
        assert fn in js, f"Expected {fn} to be defined in main.js"

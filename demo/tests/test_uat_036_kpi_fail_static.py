# -*- coding: utf-8 -*-
"""UAT-036 static evidence: KPI fail shows em-dash + retry + request generation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_inventory_fail_markers():
    js = _read("demo", "static", "js", "inventory_board.js")
    assert "inventoryLoadSeq" in js
    assert "markInventoryMetricsFailed" in js
    assert "data-uat036" in js and "inventory-retry" in js
    assert "clearInventoryMetricsFailed" in js


def test_dish_and_workbench_fail_markers():
    dish = _read("demo", "static", "js", "dish_bom.js")
    dish_c = _read("demo", "static", "js", "dish_consumption.js")
    wb = _read("demo", "static", "js", "workbench_queue.js")
    assert "dishesLoadSeq" in dish
    assert "餐品列表加载失败" in dish
    assert "markDishKpiFailed" in dish_c
    assert "workbenchQueueLoadSeq" in wb
    assert "workbenchQueueKpiFailed" in wb


def test_supplier_archive_does_not_clobber_archive_dash():
    sa = _read("demo", "static", "js", "supplier_archive.js")
    assert "archiveKpiRetry" in sa
    assert "todoPendingNum" in sa
    assert "—" in sa or "\\u2014" in sa or "'—'" in sa

# -*- coding: utf-8 -*-
"""UAT-024 DEV：手机库存品名列与操作同卡可读（静态接线门禁）。

MANUAL_UI（390/412 真机点击更多、document 溢出、1440 回归）留给独立 QA/UAT。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INV_JS = ROOT / "demo/static/js/inventory_board.js"
STYLE = ROOT / "demo/static/css/style.css"
MOBILE = ROOT / "demo/static/css/review-mobile.css"
INDEX = ROOT / "demo/templates/index.html"


def test_inventory_row_stamps_data_labels_and_sku_id():
    src = INV_JS.read_text(encoding="utf-8")
    assert 'tr.dataset.skuId' in src
    assert 'data-label="品名"' in src
    assert 'data-label="操作"' in src
    assert 'data-label="分类"' in src
    assert 'data-label="当前库存"' in src
    assert 'class="inv-actions" data-sku-id="' in src or "data-sku-id=\"${Number(sku.id)}\"" in src
    assert 'data-full-name=' in src
    assert 'inv-actions-cell' in src
    # More menu still binds sku id from same row path
    assert "function toggleInventoryMoreMenu" in src
    assert "onclick='toggleInventoryMoreMenu(this, ${Number(sku.id)})'" in src


def test_name_text_uses_break_word_not_break_all():
    import re
    style = STYLE.read_text(encoding="utf-8")
    assert ".inv-name-text" in style
    # Strip comments first so narrative text cannot fake a rule match.
    stripped = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
    found = 0
    for m in re.finditer(r"(?:^|[\s,}])\.inv-name-text\s*\{[^}]*\}", stripped):
        body = m.group(0)
        if "word-break" not in body:
            continue
        found += 1
        assert "word-break: break-all" not in body
        assert "word-break: break-word" in body
    assert found >= 2, f"expected >=2 inv-name-text word-break rules, got {found}"


def test_mobile_css_card_layout_and_no_min_width_force():
    mob = MOBILE.read_text(encoding="utf-8")
    assert "UAT-024" in mob
    assert "#inventoryTable" in mob
    assert "min-width: 0 !important" in mob
    assert 'data-label="品名"' in mob or "td.inv-name-cell" in mob
    assert 'data-label="操作"' in mob or "td.inv-actions-cell" in mob
    assert "word-break: break-word" in mob
    assert "word-break: break-all" not in mob
    # Card grid only inside max-width 640 media — desktop 1440 unaffected
    assert "@media (max-width: 640px)" in mob
    uat_block = mob[mob.index("UAT-024") :]
    assert "grid-template-columns" in uat_block
    assert "display: grid" in uat_block


def test_index_marks_inventory_sku_table_class():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="inventoryTable"' in html
    assert "inventory-sku-table" in html
    # Desktop min-width contract remains in style.css (1440 path)
    style = STYLE.read_text(encoding="utf-8")
    assert "min-width: 640px" in style
    assert "UAT-024" in style


def test_desktop_min_width_not_removed_globally():
    """1440 must not regress: wide-viewport min-width:640 stays in style.css."""
    style = STYLE.read_text(encoding="utf-8")
    # The override to 0 is only in mobile CSS
    assert "min-width: 0 !important" not in style
    mob = MOBILE.read_text(encoding="utf-8")
    assert "min-width: 0 !important" in mob

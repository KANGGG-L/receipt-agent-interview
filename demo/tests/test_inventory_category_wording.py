# -*- coding: utf-8 -*-
"""实时库存表「空分类」显示口径回归。

缺陷（前端 QA 走查发现）：
- 同一 SKU 分类为空时，实时库存表渲染英文占位 `N/A`（main.js 行内模板），
  而编辑弹窗（`sku.category || '其他'`）与数据导出中心（后端归一为「其他」）显示「其他」。
  → 同一指标跨页口径矛盾，且英文占位混入中文 UI。

覆盖（真实生产函数，非抄逻辑；node vm 沙箱载入真实 main.js）：
- `frontend_inventory_category_harness.js`：让 /api/inventory 返回 category='' 的 SKU，
  断言表格渲染「其他」而非 `N/A`。

反证（非恒真）：把 main.js 的 `sku.category || '其他'` 改回 `'N/A'`（/tmp 突变体，不动仓库文件）
后，本用例必红 —— 见 harness 的 C2/C3 两条断言。
"""

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS_DIR = os.path.join(_DEMO_DIR, "tests")
_MAIN_JS = os.path.join(_DEMO_DIR, "static", "js", "main.js")
# F4 S4-11：库存表行渲染与编辑弹窗随库存域迁至 inventory_board.js（index.html 加载链
# 中紧跟 main.js）。静态接线与反证突变体据此对准该文件。
_INV_BOARD_JS = os.path.join(_DEMO_DIR, "static", "js", "inventory_board.js")
_HARNESS = os.path.join(_TESTS_DIR, "frontend_inventory_category_harness.js")


def _run_harness(harness_path, main_js=None, inv_board_js=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node，跳过前端真实产出校验")
    argv = [node, harness_path]
    # argv[2] = main.js 槽位（缺省用真 main.js）；argv[3] = inventory_board.js 突变体覆盖。
    argv.append(main_js if main_js else _MAIN_JS)
    if inv_board_js:
        argv.append(inv_board_js)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, "脚手架无 JSON 输出：stdout=%s stderr=%s" % (proc.stdout, proc.stderr)
    result = json.loads(lines[-1])
    return result, proc.returncode


def test_inventory_empty_category_shows_other_not_na():
    """空分类 SKU 在实时库存表显示「其他」，不得出现英文占位 N/A。"""
    result, code = _run_harness(_HARNESS)
    assert code == 0, "脚手架断言失败: %s" % (result.get("failures"),)
    assert result["fail"] == 0, result["failures"]
    assert result["pass"] >= 4, "脚手架用例数异常偏少: %s" % result["pass"]


def test_static_wiring_no_english_na_placeholder_in_inventory_row():
    """静态接线：库存表分类胶囊单一口径为「其他」，前端库存域不得再有 `sku.category || 'N/A'`。"""
    with open(_MAIN_JS, "r", encoding="utf-8") as f:
        main_js = f.read()
    with open(_INV_BOARD_JS, "r", encoding="utf-8") as f:
        inv_js = f.read()
    union = main_js + "\n" + inv_js
    assert inv_js.count("sku.category || '其他'") >= 2, \
        "库存表与编辑弹窗必须共用「其他」口径（两处调用点均在库存域文件）"
    assert "sku.category || 'N/A'" not in union, \
        "不得残留英文占位 N/A（与编辑弹窗/导出中心口径矛盾）"


def test_counterexample_mutant_is_red(tmp_path):
    """非恒真反证：把口径改回 'N/A' 的突变体必须让脚手架失败。

    反证在 /tmp 突变体上进行（突变目标＝库存域文件 inventory_board.js，
    经 harness argv[3] 覆盖注入），仓库文件全程只读（符合本仓反证纪律）。
    """
    with open(_INV_BOARD_JS, "r", encoding="utf-8") as f:
        src = f.read()
    mutant = src.replace("w2Escape(sku.category || '其他')",
                         "w2Escape(sku.category || 'N/A')")
    assert mutant != src, "突变体未生效（目标字面不存在？）"
    mut_path = tmp_path / "inventory_board_mutant_invcat.js"
    mut_path.write_text(mutant, encoding="utf-8")

    result, code = _run_harness(_HARNESS, inv_board_js=str(mut_path))
    assert code != 0, "突变体存在缺陷时脚手架却报绿（判据恒真）"
    assert result["fail"] >= 1, "突变体存在缺陷时应有断言失败：%s" % (result,)

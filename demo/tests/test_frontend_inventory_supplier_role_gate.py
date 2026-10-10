# -*- coding: utf-8 -*-
"""前端「低权限角色不暴露 owner-only 入口」的回归（权限泄漏与后端 fail-closed 对齐）。

缺陷背景（Orca 浏览器走查）：前端对 staff 角色暴露了 owner-only 写操作入口，
而后端对同一接口 fail-closed 返回 403 —— 能力与后端不一致（权限泄漏）：
- F5-L1 实时库存与价格页：行内「价格走势 / 盘点 / 编辑 / 更多」、工具条「合并食材 / 一键清理重复食材」
- F5-L2 归档对账页「供应商档案」：行内「查看单据 / 编辑 / 更多」
- F1    RAG data_only 调试入口：仅 admin 可见（owner/staff 均不可见）

覆盖（真实生产函数，非抄逻辑；node vm 沙箱载入真实 main.js + 读真实 index.html）：
- `frontend_role_gate_harness.js`：staff 下 F5 行内入口不输出、owner 下输出；
  applyRoleVisibility 对 .admin-only / .owner-only 的显示切换（F1）；静态 class 接线。

反证（非恒真）：在 /tmp 突变体（仓库文件全程只读，符合本仓反证纪律）上分别关掉
F5 行门禁、class 收敛、静态 class 声明，脚手架必红 —— 见 test_counterexample_*。
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
_INDEX_HTML = os.path.join(_DEMO_DIR, "templates", "index.html")
_HARNESS = os.path.join(_TESTS_DIR, "frontend_role_gate_harness.js")


def _run_harness(harness_path, main_js=None, index_html=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node，跳过前端真实产出校验")
    argv = [node, harness_path]
    if main_js or index_html:
        argv.append(main_js or _MAIN_JS)
    if index_html:
        argv.append(index_html)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, "脚手架无 JSON 输出：stdout=%s stderr=%s" % (proc.stdout, proc.stderr)
    result = json.loads(lines[-1])
    return result, proc.returncode


def test_inventory_and_supplier_role_gate_staff_hidden_owner_visible():
    """F5-L1/F5-L2：staff 下 owner-only 行内入口不输出，owner 下输出；F1 仅 admin 可见。"""
    result, code = _run_harness(_HARNESS)
    assert code == 0, "脚手架断言失败: %s" % (result.get("failures"),)
    assert result["fail"] == 0, result["failures"]
    assert result["pass"] >= 20, "脚手架用例数异常偏少: %s" % result["pass"]


def test_inventory_toolbar_and_rag_debug_static_wiring():
    """静态接线：owner 级入口收在 owner-only 容器内，未误伤店员可用的「+ 新增食材」；F1 用 admin-only。

    Phase 2 起「合并食材 / 一键清理重复食材」收进「管理库存 ▼」下拉，owner-only
    声明在其父容器 dropdown-wrap owner-only 上（staff 下整组隐藏，门禁语义保留）。
    判据：按钮必须位于最近的 owner-only 容器内部（容器开标签到按钮之间无 </div> 闭合）。
    """
    with open(_INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()

    def _inside_owner_only_wrap(needle):
        i = html.index(needle)
        w = html.rindex("dropdown-wrap owner-only", 0, i)
        return "</div>" not in html[w:i]

    assert _inside_owner_only_wrap("openSkuMergeModalFromInv"), \
        "「合并食材」必须位于 owner-only 容器（管理库存下拉）内"
    assert _inside_owner_only_wrap("deduplicateSkus()"), \
        "「一键清理重复食材」必须位于 owner-only 容器（管理库存下拉）内"
    assert "owner-only" in html[max(0, html.index("openSupplierModal()") - 200):html.index("openSupplierModal()")], \
        "「+ 新增供应商」必须声明 owner-only"
    assert "owner-only" not in html[max(0, html.index("openAddSkuModal()") - 220):html.index("openAddSkuModal()")], \
        "「+ 新增食材」后端为 require_role('staff')，店员可用，不得被误门禁"
    assert "admin-only" in html[max(0, html.index("arcDataOnlyToggle") - 400):html.index("arcDataOnlyToggle")], \
        "RAG data_only 调试开关容器必须声明 admin-only"
    rag_card = html[html.index("arcRagContextCard"):html.index("arcRagContextCard") + 200]
    assert "admin-only" in rag_card, "RAG 上下文调试卡必须声明 admin-only"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_counterexample_inventory_row_gate_mutant_is_red(tmp_path):
    """反证：去掉库存行 owner 门禁（恒显示）后脚手架必红。"""
    src = _read(_MAIN_JS)
    mutant = src.replace("const skuActionsHtml = isOwnerRoleNow() ?",
                         "const skuActionsHtml = true ?")
    assert mutant != src, "突变体未生效（目标字面不存在？）"
    mut = tmp_path / "main_mutant_inv_gate.js"
    mut.write_text(mutant, encoding="utf-8")
    result, code = _run_harness(_HARNESS, str(mut))
    assert code != 0, "库存行门禁被关掉后脚手架却报绿（判据恒真）"
    assert result["fail"] >= 1, result


def test_counterexample_supplier_row_gate_mutant_is_red(tmp_path):
    """反证：去掉供应商行 owner 门禁（恒显示）后脚手架必红。"""
    src = _read(_MAIN_JS)
    mutant = src.replace("const supplierActionsHtml = isOwnerRoleNow() ?",
                         "const supplierActionsHtml = true ?")
    assert mutant != src, "突变体未生效（目标字面不存在？）"
    mut = tmp_path / "main_mutant_sup_gate.js"
    mut.write_text(mutant, encoding="utf-8")
    result, code = _run_harness(_HARNESS, str(mut))
    assert code != 0, "供应商行门禁被关掉后脚手架却报绿（判据恒真）"
    assert result["fail"] >= 1, result


def test_counterexample_class_convergence_mutant_is_red(tmp_path):
    """反证：让 .owner-only 永不停用（class 收敛失效，对应 F1/F5 工具栏）后脚手架必红。"""
    src = _read(_MAIN_JS)
    mutant = src.replace("el.style.display = (role === 'staff') ? 'none' : el.dataset.baseDisplay;",
                         "el.style.display = el.dataset.baseDisplay;")
    assert mutant != src, "突变体未生效（目标字面不存在？）"
    mut = tmp_path / "main_mutant_class.js"
    mut.write_text(mutant, encoding="utf-8")
    result, code = _run_harness(_HARNESS, str(mut))
    assert code != 0, "class 收敛被关掉后脚手架却报绿（判据恒真）"
    assert result["fail"] >= 1, result


def test_counterexample_index_html_class_mutant_is_red(tmp_path):
    """反证：把「管理库存 ▼」下拉的 owner-only 去掉后（合并食材/清理重复食材随之失去门禁），
    静态接线断言必红。"""
    html = _read(_INDEX_HTML)
    mutant = html.replace('dropdown-wrap owner-only', 'dropdown-wrap')
    assert mutant != html, "突变体未生效（目标字面不存在？）"
    mut_html = tmp_path / "index_mutant.html"
    mut_html.write_text(mutant, encoding="utf-8")
    result, code = _run_harness(_HARNESS, _MAIN_JS, str(mut_html))
    assert code != 0, "owner-only 容器声明被去掉后脚手架却报绿（判据恒真）"
    assert result["fail"] >= 1, result

# -*- coding: utf-8 -*-
"""W-10 待确认记忆「字段级 diff」前端真实产出校验（node vm 载入真实 evalset.js）。

缺陷：人工闸审核界面（评测台）只摊整段候选内容，审核者看不到 fact_kind / fact_key /
fact_value 的逐字段差异，只能整段接受/拒绝。

覆盖：
- `frontend_pending_memory_diff_harness.js`：真实 evalset.js 在 node vm 沙箱里渲染待审
  表格，断言结构化候选逐字段显示 kind/key/既有值/候选值；无既有值显示「新增」；遗留
  纯文本候选退回中性说明且审批按钮不受影响；候选键值被转义。
- 静态接线：evalset.html 表头含「字段级 diff」列、占位行 colspan 与列数一致、diff 样式
  类有定义。

反证（非恒真，突变体在 /tmp，仓库文件全程只读）：
- 去掉行内的字段级 diff 单元格 → 脚手架必红；
- 让 pendingFactDiffHtml 对一切候选都退回纯文本说明 → 脚手架必红。
"""

import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS_DIR = os.path.join(_DEMO_DIR, "tests")
_EVALSET_JS = os.path.join(_DEMO_DIR, "static", "js", "evalset.js")
_EVALSET_HTML = os.path.join(_DEMO_DIR, "templates", "evalset.html")
_HARNESS = os.path.join(_TESTS_DIR, "frontend_pending_memory_diff_harness.js")


def _run_harness(js_path=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node，跳过前端真实产出校验")
    argv = [node, _HARNESS]
    if js_path:
        argv.append(js_path)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, "脚手架无 JSON 输出：stdout=%s stderr=%s" % (proc.stdout, proc.stderr)
    return json.loads(lines[-1]), proc.returncode


def test_pending_memory_field_diff_renders():
    """真实 evalset.js 渲染字段级 diff：kind/key/既有值/候选值逐字段可见，旧流程不破。"""
    result, code = _run_harness()
    assert code == 0, "脚手架断言失败: %s" % (result.get("failures"),)
    assert result["fail"] == 0, result["failures"]
    assert result["pass"] >= 20, "脚手架用例数异常偏少: %s" % result["pass"]


def _page_style_sources():
    """evalset.html 声明的全部样式来源：内联 <style> 块 + 外链 stylesheet 文件内容。

    F3·B1 后 <style> 块外移到 external css，diff 样式类的「有消费方」判据按
    页面声明的样式来源联合判定，不再钉「样式必须写在 HTML 里」的旧机制。
    """
    with open(_EVALSET_HTML, "r", encoding="utf-8") as f:
        html = f.read()
    chunks = re.findall(r"<style[^>]*>([\s\S]*?)</style>", html)
    for href in re.findall(r'<link[^>]+rel="stylesheet"[^>]*>', html):
        m = re.search(r'href="([^"]+)"', href)
        if not m:
            continue
        css_path = os.path.join(
            _DEMO_DIR, "static", m.group(1).replace("/static/", "", 1)
        )
        with open(css_path, "r", encoding="utf-8") as f:
            chunks.append(f.read())
    return html, "\n".join(chunks)


def test_static_wiring_header_colspan_and_styles():
    """接线：待审表头新增「字段级 diff」列、占位行 colspan=7、diff 样式类有定义。"""
    with open(_EVALSET_HTML, "r", encoding="utf-8") as f:
        html = f.read()
    assert "字段级 diff" in html, "待审表头缺少字段级 diff 列"
    assert 'id="pendingMemoryBody"' in html, "待审 tbody 锚点缺失"
    # 占位行 colspan 必须随列数（6→7）调整，否则表格会错列
    idx = html.index('id="pendingMemoryBody"')
    assert 'colspan="7"' in html[idx:idx + 200], "占位行 colspan 未随列数调整"
    _, page_css = _page_style_sources()
    assert page_css.strip(), "页面未声明任何样式来源（扫描面失效，判据会假绿）"
    for cls in (".fact-diff", ".fd-key", ".fd-old"):
        assert cls in page_css, "diff 样式类在页面样式来源中缺定义: %s" % cls


def test_counterexample_diff_cell_removed_is_red(tmp_path):
    """非恒真反证：抽掉行内字段级 diff 单元格 → 脚手架必红。"""
    with open(_EVALSET_JS, "r", encoding="utf-8") as f:
        src = f.read()
    target = "'<td class=\"fact-diff-td\">' + pendingFactDiffHtml(m) + '</td>' +"
    assert target in src, "突变目标字面不存在，反证失效"
    mutant = src.replace(target, "")
    assert mutant != src
    mut_path = tmp_path / "evalset_mutant_nodiffcell.js"
    mut_path.write_text(mutant, encoding="utf-8")

    result, code = _run_harness(str(mut_path))
    assert code != 0, "抽掉 diff 单元格后脚手架却报绿（判据恒真）"
    assert result["fail"] >= 1, "应有断言失败: %s" % (result,)


def test_counterexample_diff_always_legacy_is_red(tmp_path):
    """非恒真反证：让 pendingFactDiffHtml 对一切候选退回纯文本说明 → 脚手架必红。"""
    with open(_EVALSET_JS, "r", encoding="utf-8") as f:
        src = f.read()
    target = "if (!has(m.fact_kind) && !has(m.fact_key) && !has(m.fact_value)) {"
    assert target in src, "突变目标字面不存在，反证失效"
    mutant = src.replace(target, "if (true) {")
    assert mutant != src
    mut_path = tmp_path / "evalset_mutant_alwayslegacy.js"
    mut_path.write_text(mutant, encoding="utf-8")

    result, code = _run_harness(str(mut_path))
    assert code != 0, "一切候选都退回纯文本视角后脚手架却报绿（判据恒真）"
    assert result["fail"] >= 1, "应有断言失败: %s" % (result,)

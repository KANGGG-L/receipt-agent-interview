# -*- coding: utf-8 -*-
"""阻塞级缺陷（空明细复核表单无法保存）+ 登录面板首屏判定的回归。

背景（独立 UAT 验收）：
1. 任何以空明细渲染的复核表单（items=[]，表格里是**无任何 input** 的占位行
   `tr.warning-row`）——用户点「+ 新增明细行」补录一行后点「确认上传单据」
   → 不发任何请求、界面无任何提示，控制台抛
   `TypeError: Cannot read properties of null (reading 'value')`，用户彻底卡死。
   命中场景含「转手工补录」（convert_manual → items=[]）这条主推链路。
2. 登录面板的显示原先依赖首屏**数据端点**返回 401 的时序（`/api/auth/me` 在白名单内
   匿名恒 200，`decideAuthProbe` 对 2xx 直接放行）。

覆盖（真实生产函数，非抄逻辑；node vm 沙箱载入真实 main.js）：
- `frontend_empty_items_save_harness.js`：占位行移除 + 采集侧兜底 + 失败不静默 + 真的发请求
- `frontend_auth_panel_harness.js`：鉴权开启且无身份 → 直接显示面板；开发态免登录直通；
  401 → onUnauthorized 兜底路径不变
- 静态接线断言：单一判据来源、保存失败路径必须带 [WARN] 与人话提示

端到端（真实浏览器 + 真实服务）见 `test_playwright_empty_items_save.py`。
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

_EMPTY_ITEMS_HARNESS = os.path.join(_TESTS_DIR, "frontend_empty_items_save_harness.js")
_AUTH_PANEL_HARNESS = os.path.join(_TESTS_DIR, "frontend_auth_panel_harness.js")


def _run_harness(harness_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node，跳过前端真实产出校验")
    proc = subprocess.run([node, harness_path, _MAIN_JS], capture_output=True, text=True, timeout=60)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, "脚手架无 JSON 输出：stdout=%s stderr=%s" % (proc.stdout, proc.stderr)
    result = json.loads(lines[-1])
    assert proc.returncode == 0, "脚手架断言失败: %s" % (result.get("failures"),)
    assert result["fail"] == 0, result["failures"]
    return result


def test_empty_items_review_form_can_save_after_adding_row():
    """空明细表单加行后可保存；占位行既不残留也不进提交；保存失败不静默。"""
    result = _run_harness(_EMPTY_ITEMS_HARNESS)
    assert result["pass"] >= 18, "脚手架用例数异常偏少: %s" % result["pass"]


def test_login_panel_decision_uses_auth_me_payload():
    """首屏登录面板判定直接吃 /api/auth/me 的 auth_enabled + account；开发态免登录直通。"""
    result = _run_harness(_AUTH_PANEL_HARNESS)
    assert result["pass"] >= 12, "脚手架用例数异常偏少: %s" % result["pass"]


def _main_js():
    with open(_MAIN_JS, "r", encoding="utf-8") as f:
        return f.read()


def test_static_wiring_placeholder_single_source_of_truth():
    js = _main_js()
    # 占位行判据单一来源（与 renderEditForm 写入的 class 同名）
    assert js.count("ITEM_TABLE_PLACEHOLDER_SELECTOR = 'tr.warning-row'") == 1, \
        "占位行判据必须收口为单一常量"
    # 加行（appendTableRow）里：先清占位行，再挂真行
    start = js.index("function appendTableRow(")
    end = js.index("function addEmptyRow(")
    body = js[start:end]
    assert "clearItemTablePlaceholder(tbody);" in body, "加行路径未清除空明细占位行"
    assert body.index("clearItemTablePlaceholder(tbody);") < body.index("tbody.appendChild(tr);"), \
        "必须先移除占位行再挂真行"
    # 采集侧兜底：无 .inp-name 的行整行跳过（不抛错、不进提交）
    cstart = js.index("function collectReviewFormData(")
    cend = js.index("function buildSavePayloadFromData(")
    cbody = js[cstart:cend]
    assert "const nameInput = tr.querySelector('.inp-name');" in cbody, \
        "采集侧必须显式识别无输入控件的行"
    assert "if (!nameInput) return;" in cbody, "采集侧必须跳过占位行而不是抛错"
    assert "tr.querySelector('.inp-name').value.trim()" not in cbody, \
        "不得再对 .inp-name 无条件取值"


def test_static_wiring_save_failure_not_silent():
    """保存入口的采集失败路径必须兜住异常、给人话提示、并以 [WARN] 留痕。"""
    js = _main_js()
    start = js.index("function submitSaveEdited(")
    end = js.index("function refreshReviewFormFromServer(")
    body = js[start:end]
    assert "try {\n        data = collectReviewFormData();\n    } catch (err) {" in body, \
        "submitSaveEdited 必须兜住采集异常（否则用户点保存毫无反应）"
    assert "console.warn('[WARN]" in body, "保存失败留痕必须用 [WARN] 措辞"
    catch_block = body[body.index("catch (err) {"):]
    catch_block = catch_block[:catch_block.index("}") + 1]
    assert "showToast(" in catch_block, "保存失败必须给人话提示（不得静默）"


def test_static_wiring_login_panel_probe():
    js = _main_js()
    start = js.index("function decideAuthProbe(")
    end = js.index("// 登录面板（JS 动态创建")
    body = js[start:end]
    assert "auth_enabled" in body and "account" in body, \
        "首屏判定必须直接使用 /api/auth/me 的 auth_enabled 与 account"
    # 401 兜底路径（onUnauthorized → showLoginPanel）保持不变
    assert "onUnauthorized: () => showLoginPanel()" in js, "401 → 登录面板兜底路径不得移除"

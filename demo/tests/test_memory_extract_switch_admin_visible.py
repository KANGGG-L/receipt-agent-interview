# -*- coding: utf-8 -*-
"""LLM 抽取开关（settings 键 `memory_extract_enabled`）在管理台「可被发现 + 一键开关」的判别式用例（P23/AQ1）。

why 需要它：
开关缺省 **False** 是刻意的 —— 打开后会在「复核保存 / 入账」两条写请求路径上真发一次
外部模型调用（增成本、增耗时），属运营决策，不得为了数字好看默默改缺省。但「缺省关闭」
只有配上「管理台能发现它、能一键开」才算可运营：在此之前
`grep -rn memory_extract_enabled demo/static demo/templates` **零命中** —— 后端
`api_receipts._memory_extract_enabled()` 是唯一消费方（只读 settings、无环境变量兜底），
于是整条 LLM 抽取链（`save_edited` / `approve` 两处触发）在生产上**永不执行**；
这正是 `memory_extract_runs` 一直 0 行、`pending_memory` 只有 1 条（且是种子工具造的）的原因。

判据照抄先例 `demo/tests/test_vendor_precheck_switch_admin_visible.py`：
  1. **控件存在**：index.html 里有 `setMemoryExtractEnabled` 复选框（且 id 唯一）；
  2. **真被调用**：main.js 的 load / collect / reset 三处**都**处理了该键
     （缺任一处就会出现「显示了但不生效」或「保存了又被重置」）；
  3. **后端消费方存在**：键已登记进 `SETTINGS_DEFAULTS`（否则 PUT 会被"未知配置键"
     挡下）且 `api_receipts` 里存在**非测试**的读键调用点；
  4. **自校准**：换一个不存在的 id / 键跑同一套断言，命中数必须为 0
     （证明上面的断言真能区分「有」与「无」，而不是恒真）。

第 2 条**刻意用函数体级断言**（按花括号配对切出每个函数的完整实现再查 id），
而不是「整个文件里能搜到这个字符串」—— 后者会把「id 只出现在注释里」「id 出现在
别的函数里」都判成通过，正是本项目高发的 ②「定义了没接线」能溜过测试的原因。
"""

import os
import re
import sys
import tempfile

# 红线：import app.* 会在 import 期执行 db._make_engine() 的迁移块、直接对 DB_PATH
# 指向的库做 ALTER。必须在任何 app.* import 之前把 DB_PATH / RAG_DIR 指到 /tmp，
# 否则会改动 live 库 / live 向量库（本项目已因此发生过三次 live 加法迁移）。
_TMP = tempfile.mkdtemp(prefix="memory_extract_switch_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "aq1_switch.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "demo"))

HTML = os.path.join(_REPO, "demo", "templates", "index.html")
MAIN_JS = os.path.join(_REPO, "demo", "static", "js", "main.js")
API_RECEIPTS = os.path.join(_REPO, "demo", "app", "api_receipts.py")

FIELD_ID = "setMemoryExtractEnabled"
KEY = "memory_extract_enabled"

# 自校准用的「不存在的 id / 键」：同一套断言打在它们身上必须一条都不命中。
ABSENT_FIELD_ID = "setMemoryExtractNotExist"
ABSENT_KEY = "memory_extract_not_exist"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_js_function_body(js, name):
    """按花括号配对切出 JS `function <name>(...) { ... }` 的完整函数体（含外层花括号）。

    why 不满足于「整个文件里搜得到」：那种弱断言会把注释里的 id、别的函数里的 id
    都算命中。本函数把作用域收敛到**函数体**，才能回答「这一处到底接线了没有」。
    """
    idx = js.find("function " + name + "(")
    assert idx != -1, "main.js 里找不到函数 %s（接线点被改名/删除？）" % name
    brace = js.find("{", idx)
    assert brace != -1, "函数 %s 没有函数体" % name
    depth = 0
    for i in range(brace, len(js)):
        ch = js[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return js[brace:i + 1]
    raise AssertionError("函数 %s 的花括号不配对，无法切出函数体" % name)


def _extract_py_function_body(src, name):
    """按缩进切出 Python `def <name>(...)` 的函数体（到下一个顶格行为止）。"""
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("def %s(" % name):
            start = i
            break
    assert start is not None, "api_receipts.py 里找不到函数 %s（接线点被改名/删除？）" % name
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() and not ln.startswith((" ", "\t")):
            break
        out.append(ln)
    return "\n".join(out)


def _count_wiring(js, field_id, key):
    """数该 id/键在 load / collect / reset 三处的接线命中数（取值 0..3）。

    load   ：读回显值（`getElementById(id)` 必须出现在 loadAdminSettings 函数体内）；
    collect：下发保存值（既要读控件，也要写进 payload）；
    reset  ：「恢复缺省值」把该键带回缺省（否则按钮文案与实际不符）。
    """
    hits = 0
    load = _extract_js_function_body(js, "loadAdminSettings")
    if "getElementById('%s')" % field_id in load:
        hits += 1
    collect = _extract_js_function_body(js, "_collectAdminSettingsPayload")
    if ("getElementById('%s')" % field_id) in collect and \
            re.search(r"payload\.%s\s*=" % re.escape(key), collect):
        hits += 1
    reset = _extract_js_function_body(js, "resetAdminSettingsToDefaults")
    if re.search(r"payload\.%s\s*=\s*!!_adminSettingsDefaults\.%s" % (re.escape(key), re.escape(key)), reset):
        hits += 1
    return hits


def test_checkbox_exists_in_admin_form():
    """控件必须存在且 id 唯一（含自校准：不存在的 id 命中数必须为 0）。"""
    html = _read(HTML)
    assert html.count('id="%s"' % FIELD_ID) == 1, \
        "管理台表单里必须有且只有一个 LLM 抽取开关控件"
    # 自校准：断言不是恒真 —— 换一个不存在的 id，命中数必须是 0。
    assert html.count('id="%s"' % ABSENT_FIELD_ID) == 0


def test_main_js_wires_load_collect_reset():
    """load / collect / reset 三处接线一个都不能少（含自校准）。"""
    js = _read(MAIN_JS)
    assert _count_wiring(js, FIELD_ID, KEY) == 3, (
        "LLM 抽取开关的 load / collect / reset 接线不齐（命中 %d/3）："
        "缺 load = 显示了但不反映当前值；缺 collect = 点了保存也不生效；"
        "缺 reset = 保存后被「恢复缺省值」留下" % _count_wiring(js, FIELD_ID, KEY))
    # 自校准：同一套断言打在不存在的 id/键上，命中数必须为 0。
    assert _count_wiring(js, ABSENT_FIELD_ID, ABSENT_KEY) == 0, \
        "自校准失败：不存在的 id/键竟然也被判成已接线 —— 上面的断言是恒真的"


def test_key_registered_and_default_still_off():
    """键必须登记（否则 PUT 被「未知配置键」挡下）且缺省仍为 False（不得默默打开）。"""
    from app.services import settings_service

    assert KEY in settings_service.SETTINGS_DEFAULTS, "未登记的键会被 PUT 以「未知配置键」挡下"
    assert settings_service.SETTINGS_DEFAULTS[KEY] is False, \
        "抽取必须缺省关闭（打开即真发模型调用，属运营决策，不得为了数字默认开启）"


def test_backend_consumer_exists_outside_tests():
    """后端消费方是真的：api_receipts 里有非测试的读键调用点（不是只定义、无人调用）。"""
    src = _read(API_RECEIPTS)

    assert 'MEMORY_EXTRACT_SETTINGS_KEY = "memory_extract_enabled"' in src, \
        "开关键常量不见了（下游读的键与 settings 登记的键会脱钩）"

    # 定义体内确实读 settings（而不是读了个常量就返回）。
    assert "settings_service.get_bool(MEMORY_EXTRACT_SETTINGS_KEY" in src, \
        "_memory_extract_enabled 未真正读 settings"

    # 非测试调用点：定义行本身会命中一次，故 >= 2 才说明「另有调用」。
    # 只有定义、没有调用 = 界面开关改完不生效（本仓高发的 ②「定义了没接线」）。
    assert src.count("_memory_extract_enabled()") >= 2, \
        "_memory_extract_enabled() 只有定义没有调用点 -> 管理台一键开关不会真的生效"

    # 两个触发入口都经同一个抽取函数（唯一实现），调用点必须真的落在抽取函数体内。
    body = _extract_py_function_body(src, "_extract_and_enqueue_memory_facts")
    assert "_memory_extract_enabled()" in body, \
        "开关判定不在抽取函数体内 -> 两处触发都不受该开关约束"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

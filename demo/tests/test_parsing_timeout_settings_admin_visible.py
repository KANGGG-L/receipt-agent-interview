# -*- coding: utf-8 -*-
"""`parsing_timeout_seconds` 在管理台「可见 + 可调」的判别式用例（P28-AU2 / P26 审核 mustFix 3）。

why 需要它：
这个 settings 键**早就存在**（`demo/app/services/settings_service.py` 的 `SETTINGS_DEFAULTS`，
缺省 300），消费方也齐（`receipt_utils.parsing_timeout_seconds` 被启动自检
`app/main.py::_sweep_stale_parsing_startup` 与只读巡检 `GET /api/admin/parsing-stale`
共用）。但它此前**不在管理台**（`main.js` 的 `ADMIN_SETTINGS_FIELDS` 里没有它）
→ 运维无法调这个阈值。这正是本仓高频缺陷「登记 != 可见」：后端有消费方、界面上看不见，
等于不可运营（同类先例：`memory_extract_enabled` 后端有消费方、管理台零命中 →
整条抽取链在生产上从未执行）。

判据照抄先例 `demo/tests/test_vendor_precheck_switch_admin_visible.py` /
`test_memory_extract_switch_admin_visible.py`，但本键走**数值型**通道
（`ADMIN_SETTINGS_FIELDS` 表），故断言分四层：
  1. **控件存在**：index.html 里有 `setParsingTimeoutSeconds` 输入框（id 唯一）；
  2. **表项正确**：从 main.js 源码**解析**出 `ADMIN_SETTINGS_FIELDS` 数组，其中必须有且
     只有一条 (id, key, type) == (setParsingTimeoutSeconds, parsing_timeout_seconds, int)
     —— 类型写错（如 float）会让"秒数"被存成小数；
  3. **表真被消费**：load / collect / reset 三个函数的**函数体内**都引用了这张表，
     且各自那一步真的做了事（load 回显 `el.value = String(data[key])`；collect 读控件并写
     `payload[key] =`，且 int 分支真的走 `Math.round`；reset 写 `payload[key] =
     _adminSettingsDefaults[key]`）。按花括号配对切出函数体再断言，而不是"整个文件里搜得到"
     —— 后者会把注释里的符号、别的函数里的引用都判成通过，正是本仓 ②「定义了没接线」
     能溜过测试的原因。三处缺一：缺 load = 显示空；缺 collect = 保存不生效；
     缺 reset = 被「恢复缺省值」漏掉。
  4. **缺省不得改**：后端缺省仍为 300（本任务只接管理台，不动缺省值）。
  5. **真跑一遍**（端到端）：源码断言只能证明「表里有这一行、三个函数里引用了这张表」，
     证不了「这条路径真的把值带过去了」。故再用 node vm 载入**真实 main.js**加真实控件 id
     建的最小 DOM，真的调用 load / collect / reset 三个生产函数做往返校验
     （`frontend_admin_settings_parsing_timeout_harness.js`）—— 这是「把 collect 里对该 id
     的读取去掉，用例必须失败」这条反向验证真正咬得住的地方（源码断言对「在循环里加一个
     continue 跳过该键」这类局部改法是钝的）。
另加防恒真的**自校准**：换一个不存在的 id / 键跑同一套断言，命中数必须为 0
（证明上面的断言真能区分「有」与「无」，而不是无论查什么都返回命中）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest
from f4_frontend_sources import frontend_union  # F4 S4-11: main.js+域文件联合全文

# 红线：import app.* 会在 import 期执行 db._make_engine() 的迁移块、直接对 DB_PATH
# 指向的库做 ALTER。必须在任何 app.* import 之前把 DB_PATH / RAG_DIR 指到 /tmp，
# 否则会改动 live 库 / live 向量库（本项目已因此发生过三次 live 加法迁移）。
_TMP = tempfile.mkdtemp(prefix="parsing_timeout_admin_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "au2_settings.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "demo"))

HTML = os.path.join(_REPO, "demo", "templates", "index.html")
MAIN_JS = os.path.join(_REPO, "demo", "static", "js", "main.js")

FIELD_ID = "setParsingTimeoutSeconds"
KEY = "parsing_timeout_seconds"
TYPE = "int"
DEFAULT = 300

# 自校准用的「不存在的 id / 键」：同一套断言打在它们身上必须一条都不命中。
ABSENT_FIELD_ID = "setParsingTimeoutNotExist"
ABSENT_KEY = "parsing_timeout_not_exist"

# 表项要真的被这三个函数消费；缺任一处都会让管理台控件失效（见模块 docstring 第 3 条）。
_WIRING_FUNCS = ("loadAdminSettings", "_collectAdminSettingsPayload", "resetAdminSettingsToDefaults")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_js_function_body(js, name):
    """按花括号配对切出 JS `function <name>(...) { ... }` 的完整函数体（含外层花括号）。

    why 不满足于「整个文件里搜得到」：那种弱断言会把注释里的符号、别的函数里的引用都算
    命中。本函数把作用域收敛到**函数体**，才能回答「这一处到底接线了没有」。
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


def _parse_admin_settings_fields(js):
    """从源码解析出 `ADMIN_SETTINGS_FIELDS` 数组的 (id, key, type) 三元组列表。

    why 必须解析数组而不是「整个文件里搜 id 字符串」：后者无法回答「这一行到底登记了没有、
    类型对不对」，注释里或别的数组里出现同名字符串都会被误判命中。本函数按方括号配对切出
    数组字面量，再只认表项形态 `['id', 'key', 'type']`。
    """
    idx = js.find("const ADMIN_SETTINGS_FIELDS = [")
    assert idx != -1, "main.js 里找不到 ADMIN_SETTINGS_FIELDS 表（被改名/删除？）"
    open_br = js.find("[", idx)
    depth = 0
    end = None
    for i in range(open_br, len(js)):
        ch = js[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "ADMIN_SETTINGS_FIELDS 的方括号不配对，无法切出表"
    block = js[open_br:end + 1]
    return re.findall(r"\[\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*\]", block)


def _collect_bypasses_table(js, field_id, key):
    """collect 体内是否出现「该键 / 该 id」的字面量（出现即说明它绕过了表驱动读取）。

    why 需要这条不变量：本表是**表驱动**的（load / collect / reset 由同一张表驱动，见
    main.js 的注释「三处由同一张表驱动，不再各写一份」）。在这套设计下，「去掉 collect 里
    对该 id 的读取」只有两种写法：
      ① 删表项        —— 上面的三元组断言会红；
      ② 在循环里特判跳过（如 `if (key === 'parsing_timeout_seconds') continue;`）
         —— 只改循环体、不碰表，纯结构断言（表里有这一行 + 函数里引用了这张表）**看不出来**。
    所以这里补一条：collect 体内不得出现该键或该 id 的字面量 —— 表驱动路径只会用循环变量
    `key` / `id`，一旦写死某个键名，就是特判（绕过）。这条与运行时往返用例互补：
    这条是静态的「没有绕过」，那条是动态的「真的读到了」。
    """
    collect = _extract_js_function_body(js, "_collectAdminSettingsPayload")
    literals = ("'%s'" % key, '"%s"' % key, "'%s'" % field_id, '"%s"' % field_id)
    return any(lit in collect for lit in literals)


def _wiring_steps(js, field_id, key, type_):
    """返回三处接线各自的真假 [load, collect, reset]（每处都要求「表内有这一项 + 那步真做事」）。

    表内没有该 (id, key, type) 三元组 → 三处全 False（这正是自校准要验证的：不存在的
    项无论查什么都命中 0）。
    """
    if (field_id, key, type_) not in _parse_admin_settings_fields(js):
        return [False, False, False]
    load = _extract_js_function_body(js, "loadAdminSettings")
    collect = _extract_js_function_body(js, "_collectAdminSettingsPayload")
    reset = _extract_js_function_body(js, "resetAdminSettingsToDefaults")
    return [
        # load：按表遍历 + 真把值回显到控件
        ("ADMIN_SETTINGS_FIELDS" in load
         and "getElementById(id)" in load
         and "el.value = String(data[key])" in load),
        # collect：按表遍历 + 真读控件写进 payload（含 int 取整分支），且不得特判绕过该键
        ("ADMIN_SETTINGS_FIELDS" in collect
         and "getElementById(id)" in collect
         and "payload[key] =" in collect
         and "(type === 'int') ? Math.round(num) : num" in collect
         and not _collect_bypasses_table(js, field_id, key)),
        # reset：按表遍历 + 真把该键带回缺省
        ("ADMIN_SETTINGS_FIELDS" in reset
         and "payload[key] = _adminSettingsDefaults[key]" in reset),
    ]


def _wiring_hits(js, field_id, key, type_):
    return sum(1 for x in _wiring_steps(js, field_id, key, type_) if x)


def test_input_exists_in_admin_form():
    """控件必须存在且 id 唯一（含自校准：不存在的 id 命中数必须为 0）。"""
    html = _read(HTML)
    assert html.count('id="%s"' % FIELD_ID) == 1, \
        "管理台表单里必须有且只有一个解析超时阈值控件"
    # 自校准：断言不是恒真 —— 换一个不存在的 id，命中数必须是 0。
    assert html.count('id="%s"' % ABSENT_FIELD_ID) == 0


def test_table_entry_type_is_int():
    """表项三元组必须精确匹配，且类型为 int（float 会把秒数存成小数）。"""
    entries = _parse_admin_settings_fields(frontend_union())  # F4 S4-11: main.js+域文件联合全文
    matches = [e for e in entries if e[0] == FIELD_ID]
    assert len(matches) == 1, \
        "ADMIN_SETTINGS_FIELDS 里 setParsingTimeoutSeconds 必须恰好登记一次（实际 %d 次）" % len(matches)
    assert matches[0] == (FIELD_ID, KEY, TYPE), \
        "表项三元组不对：期望 (%s, %s, %s)，实际 %r（类型写错会让秒数被存成小数/字符串）" % (
            FIELD_ID, KEY, TYPE, matches[0])
    # 自校准：不存在的 id 在表里查不到任何表项。
    assert [e for e in entries if e[0] == ABSENT_FIELD_ID] == []


def test_main_js_wires_load_collect_reset():
    """load / collect / reset 三处接线一个都不能少（含自校准）。"""
    js = frontend_union()  # F4 S4-11: main.js+域文件联合全文
    steps = _wiring_steps(js, FIELD_ID, KEY, TYPE)
    assert all(steps), (
        "解析超时阈值控件的 load / collect / reset 接线不齐：%s（缺 load = 显示了但不反映"
        "当前值；缺 collect = 改了也不生效；缺 reset = 保存后被「恢复缺省值」留下）"
        % dict(zip(_WIRING_FUNCS, steps)))
    # 自校准：同一套断言打在不存在的 id/键上，命中数必须为 0。
    assert _wiring_hits(js, ABSENT_FIELD_ID, ABSENT_KEY, TYPE) == 0, \
        "自校准失败：不存在的 id/键竟然也被判成已接线 —— 上面的断言是恒真的"
    # 自校准（新增的「不得特判绕过」不变量自身也要有判别力）：合成一段最小「特判跳过」代码，
    # 断言它被识别 —— 否则这条不变量可能恒假（永远判作「没有绕过」）。
    fake_bypass = (
        "function _collectAdminSettingsPayload() {\n"
        "  for (const [id, key, type] of ADMIN_SETTINGS_FIELDS) {\n"
        "    if (key === '%s') continue;\n"
        "  }\n"
        "}" % KEY)
    assert _collect_bypasses_table(fake_bypass, FIELD_ID, KEY) is True, \
        "自校准失败：合成的「特判跳过」样例没被识别 —— 这条不变量是恒假的"


def test_key_registered_and_default_unchanged():
    """键必须登记（否则 PUT 被「未知配置键」挡下）且缺省仍为 300（本任务不得改缺省）。"""
    from app.services import settings_service

    assert KEY in settings_service.SETTINGS_DEFAULTS, "未登记的键会被 PUT 以「未知配置键」挡下"
    assert settings_service.SETTINGS_DEFAULTS[KEY] == DEFAULT, \
        "缺省必须仍是 300（本任务只把控件接到管理台，不动缺省值）"


def test_backend_consumer_exists_outside_tests():
    """后端消费方是真的：阈值读取点与清扫调用点都在生产路径上（不是只登记、无人用）。"""
    src = _read(os.path.join(_REPO, "demo", "app", "services", "receipt_utils.py"))
    assert 'PARSING_TIMEOUT_SETTINGS_KEY = "%s"' % KEY in src, \
        "消费方读的键与 settings 登记的键脱钩了"
    # 阈值读取点必须真的读 settings（而不是读个常量就返回）。
    assert "settings_service.get_int(PARSING_TIMEOUT_SETTINGS_KEY" in src, \
        "parsing_timeout_seconds 未真正读 settings -> 管理台改阈值不生效"


def test_other_registered_int_float_keys_inventory():
    """盘点：登记为 int/float 但前端（main.js + index.html）完全不出现的键（本波不修，仅登记）。

    why 单列成用例：本仓「登记 != 可见」是高频缺陷（`memory_extract_enabled` 就是受害者），
    把盘点固化成可复跑的读数，比写在某份报告里更不容易过期。本用例**只盘点不断言为空**
    —— 要求「所有键都必须接管理台」会误伤刻意不可调的键（如 guard_* 由实验守护写入）。
    """
    from app.services import settings_service

    js = frontend_union()  # F4 S4-11: main.js+域文件联合全文
    html = _read(HTML)
    unwired = []
    for k, v in sorted(settings_service.SETTINGS_DEFAULTS.items()):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue   # 布尔项走各自显式通道（preprocess_enabled / vendor_precheck_enabled / ...）
        if k not in js and k not in html:
            unwired.append(k)
    # 本任务接的这个键不属于该名单。
    assert KEY not in unwired, "本键已接管理台，不应出现在未接线名单里"
    print("\n[盘点] 登记为 int/float 但前端零出现的 settings 键（%d 个）：%s"
          % (len(unwired), unwired))


def test_runtime_roundtrip_via_real_main_js():
    """端到端往返（真实 main.js）：load 回显 / collect 下发（int 取整）/ reset 回缺省。

    why 与上面几条源码断言并存：源码断言对「在循环里加一个 continue 跳过该键」这类局部
    改法是钝的；这里真的调用生产函数走一遍，才让「去掉 collect 里对该 id 的读取」这条
    反向验证必然变红。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node，跳过真实 main.js 前端往返校验")
    harness = os.path.join(_REPO, "demo", "tests",
                           "frontend_admin_settings_parsing_timeout_harness.js")
    proc = subprocess.run([node, harness, MAIN_JS], capture_output=True, text=True, timeout=60)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, "前端脚手架没有产出 JSON 汇总：\n%s\n%s" % (proc.stdout, proc.stderr)
    result = json.loads(lines[-1])
    assert proc.returncode == 0 and result["fail"] == 0, \
        "前端往返失败: %s" % result["failures"]
    assert result["pass"] >= 12, "脚手架用例数异常偏少（判据被削弱？）: %s" % result["pass"]
    # console 错误数必须是 0：被测 main.js 里被静默吞掉的异常（本仓 ③ 类高发缺陷）
    # 只会在这个读数上现形。缺字段同样判失败 —— 否则脚手架悄悄去掉计数就会被放过。
    assert result.get("consoleErrors") == 0, \
        "前端脚手架报了 console 错误（有异常被静默吞掉？）: %s" % result.get("consoleErrors")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))

# -*- coding: utf-8 -*-
"""T4（2026-10-20 P0 波，F-C 收口）：`memory_pii_mask_enabled` 的「表述缺省 == 代码缺省」守卫。

背景（真实缺陷，不是形式要求）：本键的代码缺省在 W-ISO-2（2026-10-03，commit 057f3c6）
由 False 改为 True，同波漏改了 rag.py 的注释与 settings_service 的收口清单，于是
2026-10-20 复核时树上是「注释说缺省 False / 代码是缺省 True」的漂移状态。漂移的代价不是
难看而已：下一位读者会把 False 当成设计意图，进而把「默认脱敏」当回归来关掉。

本文件全部**只读**：不 import 任何 `app.*`（不触发 `db._make_engine()` 的 import 期迁移），
因此绝不碰 live 库与 live 向量目录；需要突变体时在 pytest 的 tmp_path 里造副本，
仓库文件不改一个字节。

【覆盖面：四档判据，逐档给判别力】
  1) 代码缺省唯一事实源 —— 按文件路径加载 settings_service（该模块顶层只有
     `import logging`，无 DB 副作用），读 `SETTINGS_DEFAULTS[本键]`。
  2) 第二/第三份实现 —— AST 钉住 rag 消费点用的键名，以及它写死的布尔兜底
     （`get_bool(key, True)` 的显式 default 会短路表值），逐项等于 1) 的实测值。
  3) 表述侧 —— 四个互补口径（a/b/c/d，其中 d 管名字不管文字）：
     a. **全文件口径**：两个生产文件里每一条*可判定的*现行缺省表述都必须等于代码缺省；
        可判定表述为零也翻红（防「把说错的注释删掉」当修复）。逐点断言，消息带 file:line。
     b. **锚点口径**（本波新增，补上一轮被打穿的洞）：按 AST 结构定位 7 个「现行缺省必须
        正面表态」的位置（rag 常量上方注释区、三个消费函数 docstring、settings 表项上方
        注释区、settings 模块 docstring 的收口条目、tests 域 PII 用例文件的模块 docstring
        段落）。每个锚点必须*至少产出一条*可判定且
        等于代码缺省的表述 —— 于是「换成词表认不出的措辞」和「删空」都等价于锚点零表述，
        直接翻红。判别力由 `test_anchor_statement_rewording_or_deletion_flips_the_anchor_red`
        在 tmp 突变体上自证，并同步断言「全文件口径在该突变体上仍然一致」，把
        「锚点口径严格强于全文件口径」写成机器事实而不是文案。
     c. **tests 域口径**（本波新增，对应上一轮 mustFix 2）：扫 `tests/` 与 `demo/tests/`
        （含两个 conftest.py）里凡按*标识符*（键名 / 常量名 / 消费函数名）谈到本键的文件，
        用与 a. 完全相同的实现与词表做逐点断言，并要求两域合计的可判定表述不为零。
        文件级用标识符预筛、单元级用话题词表：前者把「与本键无关的脱敏测试」（API Key 掩码、
        供应商名别名化、LLM I/O 日志脱敏等）整片挡在门外，避免把兄弟开关的缺省表述误算到
        本键头上；后者在同一文件内把话题收窄到本键。
     d. **名字口径**（本波新增，对应上一轮 mustFix 1 的第二半）：同一批 tests 域文件里，
        凡 body 写了 `get_bool(本键) is True` 的用例，函数名不得再用否认式词形
        （is_disabled / default_off / does_not_alter …）。上一轮那个「名字与 body 互相
        打架」的缺陷形态由此有了机器防线，改回旧名即翻红（判别力见
        `test_misnamed_default_case_flips_the_name_guard_red`）。

【限定句 —— 本守卫*不*保证什么（上一轮红线 6：声明不得领先于覆盖面）】
  本守卫是文本匹配器，覆盖面由「结构锚点 + 话题标识符 + 缺省词表」三件事共同决定，
  因此它只保证 docstring【覆盖面】一节列出的那几类翻红，不保证「任一侧漂移即翻红」。
  实测不在覆盖面内的形态（rag.py 属主 = 本轮 F-B1，本波不改 rag.py，只登记成待办）：
    - rag.py 写入路径的两处 W-9 注释（`ingest_memory` 与 `approve_pending_to_memory`
      自由文本分支各一处）仍写
      `开关关闭（缺省）时逐字节原样返回`。它既不在 7 个锚点内，也不含词表里的任何
      「关闭」词形，故本守卫**放过它** —— 这是真实残留漂移，不是误报。
    - 反面对照（必须*不被*误伤，否则守卫会因噪声被顺手关掉）：rag.py 现行缺省注释块末行
      `本键时才逐字节原样返回（那是关掉态，不是现行缺省态）` 与
      `_mask_memory_text` docstring 首行「开关关闭时逐字节原样返回」都是**合法**句子
      （描述关掉态/关闭分支，不是声明现行缺省）。这也是「缺省/默认」这道门留作
      *负向过滤器*的原因：把门彻底拆掉会立刻把这两句误判成本键的 False 表述。
    - 名字口径只钉「body 断言 True 而用例名否认」这一方向（实测的唯一缺陷形态）；
      反方向（body 断 False、名字却说开启）目前没有证据支持，本守卫不声称覆盖。

【判别力（突变体放在 tmp_path，仓库文件全程只读）】
  - `T4_SETTINGS_SERVICE_PY` 指向「把表内缺省改成 False」的 settings_service 突变体
        → 用例 1 与全文件口径必须翻红（注释说 True、代码是 False）；
  - `T4_RAG_PY` 指向「把注释改回缺省 False」的 rag 突变体
        → 全文件口径必须翻红；
  - `T4_RAG_PY` 指向「删掉变更史注释块」的 rag 突变体
        → 变更史存在性用例必须翻红；
  - 锚点口径 / tests 域口径的反证不走环境变量：由用例自己在 tmp_path 造副本（见
        `test_anchor_statement_rewording_or_deletion_flips_the_anchor_red`、
        `test_tests_domain_flips_red_on_a_wrong_statement`）。
不覆盖环境变量时跑的是仓库真树，应全绿。
"""

import ast
import importlib.util
import os
import re
from collections import namedtuple

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_HERE, ".."))
_REPO_ROOT = os.path.dirname(_DEMO_DIR)

KEY = "memory_pii_mask_enabled"
CONST_NAME = "MEMORY_PII_MASK_KEY"
CONSUMER_FN = "_pii_mask_enabled"
MASKER_TOOL_FN = "_pii_masker_tool"
MASK_TEXT_FN = "_mask_memory_text"
SETTINGS_DICT_NAME = "SETTINGS_DEFAULTS"
# 「缺省值变更史」= 历史注释块的唯一哨兵；只有该块的首行允许同时出现两种缺省取值。
HISTORY_SENTINEL = "缺省值变更史"
# docstring 归属标签：模块级 docstring 没有 __name__，用哨兵串占位。
MODULE_LABEL = "<module>"

_DEFAULT_SETTINGS_SERVICE_PY = os.path.join(_DEMO_DIR, "app", "services", "settings_service.py")
_DEFAULT_RAG_PY = os.path.join(_DEMO_DIR, "app", "services", "rag.py")
_DEFAULT_PII_MASK_TEST_PY = os.path.join(_DEMO_DIR, "tests", "test_memory_pii_mask.py")


def _resolve(env_var, default_path):
    """被测文件路径：环境变量优先（供 tmp 突变体用），否则取仓库内真实文件。"""
    raw = os.environ.get(env_var, "").strip()
    return os.path.abspath(raw) if raw else default_path


SETTINGS_SERVICE_PY = _resolve("T4_SETTINGS_SERVICE_PY", _DEFAULT_SETTINGS_SERVICE_PY)
RAG_PY = _resolve("T4_RAG_PY", _DEFAULT_RAG_PY)
# tests 域的锚点目标刻意**不**开环境变量：tests 域扫描根本来就写死在仓库两域，
# 反证用的突变体由用例自己把绝对路径显式传给 `_check_anchor`（见 ANCHORS 的判别力用例），
# 免得多出一个「只影响锚点、不影响域扫描」的半吊子开关。
PII_MASK_TEST_PY = _DEFAULT_PII_MASK_TEST_PY

# 单元级话题标记：整行/整块里出现任一才认为「这段在讲本键」，避免把兄弟开关
# （memory_extract / memory_first_round / vendor_precheck 全都缺省 False）的表述
# 误算到本键头上。
TOPIC_TOKENS = (KEY, CONST_NAME, "脱敏")
# 文件级预筛（只用于 tests 域）：整篇文本里连这些**标识符**都没出现，就与本键无关，
# 连解析都不做。刻意不含「脱敏」——本仓叫「脱敏」的东西一大把，但都不讲本键。
FILE_SCOPE_TOKENS = (KEY, CONST_NAME, CONSUMER_FN, MASKER_TOOL_FN, MASK_TEXT_FN, "pii_masker")

# 现行缺省表述的词表。刻意要求「缺省/默认 + 取值」紧邻的成文形态（外加布尔字面量兜底），
# 因为本仓的注释一贯这样写；一句里同时出现两种取值会被判为「无法判定」而逐点翻红。
_TRUE_TOKENS = ("缺省 True", "缺省True", "缺省为 True", "默认 True", "缺省开启",
                "默认开启", "缺省即开", "开启（缺省）", "缺省为开")
_FALSE_TOKENS = ("缺省 False", "缺省False", "缺省为 False", "默认 False", "缺省关闭",
                 "默认关闭", "缺省为关", "不默认打开", "未开启的部署（缺省）",
                 "缺省态就是「不改动")

# 句读切分：注释里一句只准表一次态，跨句不合并判断。
_SENTENCE_SPLIT = re.compile(r"[。；;！!\n]")

# tests 域扫描根：目录与**单文件**混用（单文件根必须显式分支处理，
# 因为 `os.walk(某文件路径)` 不报错、也**静默不产出任何东西** —— 这是本仓守卫的既发事故）。
_TESTS_DIR = os.path.join(_REPO_ROOT, "tests")
_DEMO_TESTS_DIR = os.path.join(_DEMO_DIR, "tests")
TESTS_DOMAIN_ROOTS = (
    _TESTS_DIR,
    _DEMO_TESTS_DIR,
    os.path.join(_REPO_ROOT, "tests", "conftest.py"),
    os.path.join(_DEMO_DIR, "conftest.py"),
)
_SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".git"}
# 豁免面：只允许本守卫文件自己。它的 docstring 必然同时引用两种取值（讲突变体、讲历史），
# 按构造不可能满足「只说现行缺省」，见 `test_tests_domain_exemption_is_load_bearing`。
_SELF_EXEMPT = (os.path.abspath(__file__),)

# 逐点断言用的表述记录：path/lineno 供消息定位，value 为声称的布尔（矛盾时 None）。
Claim = namedtuple("Claim", "path lineno text value contradiction")


# ------------------------------------------------------------------
# 1) 代码缺省：按路径加载 settings_service，读表
# ------------------------------------------------------------------
def _settings_defaults(path):
    """按**文件路径**单独加载 settings_service 并返回 SETTINGS_DEFAULTS 的快照。

    不走 `import app.services.settings_service`：那会把整个 app 包（含 app.db 的 import 期
    迁移）拖进来。本模块顶层只有 `import logging`，按路径加载零副作用。
    """
    spec = importlib.util.spec_from_file_location("_t4_settings_service_snapshot", path)
    assert spec is not None and spec.loader is not None, "无法按路径加载模块：%s" % path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    defaults = getattr(module, SETTINGS_DICT_NAME, None)
    assert isinstance(defaults, dict), "%s 里没有 %s 字典" % (path, SETTINGS_DICT_NAME)
    return defaults


def _code_default(settings_path=None):
    """本键的代码缺省（唯一事实源），取 bool 以便与表述逐点比对。"""
    defaults = _settings_defaults(settings_path or SETTINGS_SERVICE_PY)
    assert KEY in defaults, "本键没登记进 %s，消费点会拿到 None" % SETTINGS_DICT_NAME
    return bool(defaults[KEY])


# ------------------------------------------------------------------
# 2) rag 消费点：AST 读键名与写死的兜底缺省
# ------------------------------------------------------------------
def _find_module_assign(tree, name):
    """模块级 `NAME = ...` 的赋值节点（定位常量定义行，锚点据此上溯注释区）。"""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return node
    return None


def _find_dict_entry(tree, dict_name, key):
    """`DICT = {...}` 里字面量键 `key` 的节点（用于上溯表项注释区）。"""
    assign = _find_module_assign(tree, dict_name)
    if assign is None or not isinstance(assign.value, ast.Dict):
        return None
    for k in assign.value.keys:
        if isinstance(k, ast.Constant) and k.value == key:
            return k
    return None


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _rag_key_literal(tree):
    """模块级 `MEMORY_PII_MASK_KEY = "..."` 的字面量（消费点实际用的键名）。"""
    node = _find_module_assign(tree, CONST_NAME)
    if node is not None and isinstance(node.value, ast.Constant) \
            and isinstance(node.value.value, str):
        return node.value.value
    return None


def _docstring_node(node):
    """取该定义体首个语句若为字符串字面量则返回其 Expr 节点，否则 None。"""
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
            and isinstance(first.value.value, str):
        return first
    return None


def _rag_hardcoded_bools(tree):
    """`_pii_mask_enabled` 里所有写死的布尔：get_bool 的显式兜底 + 异常分支的 return。

    这两处都是「同一口径的第二、第三份实现」（get_bool 一旦收到显式 default 就不再查
    SETTINGS_DEFAULTS），故用断言钉住它们与表内值逐项相等，而不是指望注释同步。
    """
    func = _find_function(tree, CONSUMER_FN)
    assert func is not None, "rag.py 里找不到消费函数 %s（改名了就先改本守卫）" % CONSUMER_FN
    found = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            fname = getattr(node.func, "attr", "")
            if fname == "get_bool" and len(node.args) >= 2:
                key_arg, default_arg = node.args[0], node.args[1]
                key_is_const = (isinstance(key_arg, ast.Name) and key_arg.id == CONST_NAME)
                if not key_is_const and not (isinstance(key_arg, ast.Constant)
                                             and key_arg.value == KEY):
                    raise AssertionError(
                        "%s 的 get_bool 第一个参数不是 %s，键名口径已分叉" % (CONSUMER_FN, CONST_NAME))
                if isinstance(default_arg, ast.Constant) and isinstance(default_arg.value, bool):
                    found.append(("get_bool 显式兜底", default_arg.lineno, default_arg.value))
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, bool):
                found.append(("return 字面量", node.value.lineno, node.value.value))
    assert found, "%s 里没读到任何布尔兜底，守卫的抓取口径需要更新" % CONSUMER_FN
    return found


# ------------------------------------------------------------------
# 3) 注释与 docstring 里的缺省表述（带行号，供逐点定位）
# ------------------------------------------------------------------
def _read_lines(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().splitlines()


def _parse_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _comment_units(lines, start_index=1):
    """连续整行 `#` 注释聚成一个单元（本键的缺省表述都以这种注释块出现）。

    返回 [(单元首行行号, [去掉 `#` 的文本行])]。`start_index` 让「从某行往上截取的局部区」
    也能报出真实行号（锚点定位靠它）。
    """
    units = []
    current = []
    cur_start = None
    for offset, line in enumerate(lines):
        lineno = start_index + offset
        if line.lstrip().startswith("#"):
            if not current:
                cur_start = lineno
            current.append(line.strip().lstrip("#").strip())
            continue
        if current:
            units.append((cur_start, current))
            current = []
    if current:
        units.append((cur_start, current))
    return units


def _docstring_blocks(tree):
    """模块 / 函数 / 类的 docstring：[(归属名, 首行行号, [原文行])].

    行号推算：字符串字面量 Expr 的 `lineno` 就是起始引号所在行，docstring 第 k 行
    （k 从 1 起）落在 `lineno + k - 1`；「三引号后直接换行」与「三引号后紧跟正文」
    两种写法都成立。
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            owner, holder = MODULE_LABEL, node
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owner, holder = node.name, node
        else:
            continue
        expr = _docstring_node(holder)
        if expr is None:
            continue
        text_lines = expr.value.value.splitlines()
        if not text_lines:
            continue
        out.append((owner, expr.lineno, text_lines))
    return out


def _segment_units(start_index, raw_lines):
    """按空行分段、段内按 `- ` / `* ` 项目符号切单元（与旧 _docstring_units 同口径）。"""
    units = []
    seg = []
    seg_start = None
    for offset, line in enumerate(raw_lines):
        lineno = start_index + offset
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            if seg:
                units.append((seg_start, seg))
            seg = [stripped.lstrip("-* ").strip()]
            seg_start = lineno
        elif not stripped:
            if seg:
                units.append((seg_start, seg))
            seg = []
            seg_start = None
        else:
            if not seg:
                seg_start = lineno
            seg.append(stripped)
    if seg:
        units.append((seg_start, seg))
    return [u for u in units if any(x.strip() for x in u[1])]


def _docstring_units(tree):
    """所有 docstring 的单元，带归属名：[(归属名, 单元首行行号, [文本行])]."""
    return [(owner, u_start, u_lines)
            for owner, start, text_lines in _docstring_blocks(tree)
            for u_start, u_lines in _segment_units(start, text_lines)]


def _file_units(path):
    """一个文件里的全部「表述单元」：整行注释块 + docstring 段/条目。"""
    lines = _read_lines(path)
    tree = _parse_file(path)
    units = _comment_units(lines, 1)
    units += [(start, text) for _owner, start, text in _docstring_units(tree)]
    return units


def _classify(fragment):
    """该句若对本键的现行缺省表态，返回 (布尔或 None, 是否自相矛盾)；否则返回 None。

    「缺省/默认」这道门**刻意保留**为负向过滤器：本仓的历史指涉句（例如「变更史（以及
    「False 为何不再是设计意图」）唯一登记在…」）不带取值声明，拆掉门就会把它误判成
    False 表述（见模块 docstring【限定句】）。反 evasion 不靠拆门，靠锚点正面判据：
    措辞换成词表认不出的写法 = 锚点零表述 = 翻红。
    """
    if ("缺省" not in fragment) and ("默认" not in fragment):
        return None
    says_true = any(tok in fragment for tok in _TRUE_TOKENS) or ("True" in fragment)
    says_false = (any(tok in fragment for tok in _FALSE_TOKENS)
                  or ("False" in fragment) or ("未开启" in fragment))
    if says_true and says_false:
        return (None, True)
    if says_true:
        return (True, False)
    if says_false:
        return (False, False)
    return None


def _unit_is_history(unit_lines):
    first_line = next((ln for ln in unit_lines if ln.strip()), "")
    return HISTORY_SENTINEL in first_line


def _claims_of_units(path, units):
    """把单元集转成逐点 Claim（哨兵起头的历史单元单列，不参与现行缺省比对）。"""
    claims = []
    for start, unit_lines in units:
        if _unit_is_history(unit_lines):
            continue
        for offset, text in enumerate(unit_lines):
            for fragment in _SENTENCE_SPLIT.split(text):
                fragment = fragment.strip()
                if not fragment:
                    continue
                verdict = _classify(fragment)
                if verdict is None:
                    continue
                value, contradiction = verdict
                claims.append(Claim(path, start + offset, fragment, value, contradiction))
    return claims


def _collect_default_claims(path, topic_tokens=TOPIC_TOKENS):
    """返回 (逐点表述列表, 变更史单元列表)。

    「现行表述」= 含主题标记、且首行不含 `缺省值变更史` 哨兵的注释块 / docstring 单元；
    哨兵块是唯一允许登记 False 的地方，因此它被单列出来由变更史用例专门校验。
    """
    claims = []
    history_units = []
    for start, unit_lines in _file_units(path):
        unit_text = "\n".join(unit_lines)
        if not any(tok in unit_text for tok in topic_tokens):
            continue
        if _unit_is_history(unit_lines):
            history_units.append(unit_text)
            continue
        claims.extend(_claims_of_units(path, [(start, unit_lines)]))
    return claims, history_units


def _assert_points_agree(points, actual, where):
    """逐点断言（取代上一轮的全文件集合相等）：消息一律带 `file:line`，能定位到行。"""
    assert points, (
        "%s 里找不到任何关于本键现行缺省的可判定表述（%s）—— 要么表述被删空，要么"
        "话题词表/词形与本仓写法不符；两者都必须显式处理，不能默认放行。" % (where, KEY))
    for p in points:
        loc = "%s:%d" % (p.path, p.lineno)
        assert not p.contradiction, (
            "%s 同一句同时出现开/关两种缺省表述，无法判定：%r" % (loc, p.text))
        assert p.value == actual, (
            "%s 的现行缺省表述 %r 声称 %r，与 %s[%r] 实测缺省 %r 不一致"
            "（T4 要修的正是这一类漂移）。" % (loc, p.text, p.value, SETTINGS_DICT_NAME, KEY, actual))


# ------------------------------------------------------------------
# 4) 锚点表：现行缺省必须**正面表态**的位置（按 AST 结构定位，不写死行号）
# ------------------------------------------------------------------
# (锚点 id, 目标文件档, 定位方式, 定位参数)
ANCHORS = (
    ("rag_key_block", "rag", "comments_above_const", CONST_NAME),
    ("rag_consumer_docstring", "rag", "function_docstring", CONSUMER_FN),
    ("rag_masker_tool_docstring", "rag", "function_docstring", MASKER_TOOL_FN),
    ("rag_mask_text_docstring", "rag", "function_docstring", MASK_TEXT_FN),
    ("settings_table_entry_block", "settings", "comments_above_dict_entry",
     (SETTINGS_DICT_NAME, KEY)),
    ("settings_module_bullet", "settings", "module_docstring_matching", KEY),
    # tests 域的正面表态锚点：PII 脱敏用例文件的模块 docstring「修复」段必须自己把现行
    # 缺省说清楚（上一轮 mustFix 1 说的就是这一段）。域级「合计非空」只能兜住「全都删光」，
    # 删掉这一个文件里的表述却留着别处照旧说 True 的形态，只有锚点判据抓得到。
    ("pii_mask_test_fix_paragraph", "pii_mask_test", "module_docstring_matching", KEY),
)
ANCHOR_IDS = [a[0] for a in ANCHORS]
_ANCHOR_INDEX = dict((a[0], a[1:]) for a in ANCHORS)
_ANCHOR_WHERE = {
    "rag_key_block": "rag 的 %s 上方注释区" % CONST_NAME,
    "rag_consumer_docstring": "rag 的 %s() docstring" % CONSUMER_FN,
    "rag_masker_tool_docstring": "rag 的 %s() docstring" % MASKER_TOOL_FN,
    "rag_mask_text_docstring": "rag 的 %s() docstring" % MASK_TEXT_FN,
    "settings_table_entry_block": "settings 的 %s[%r] 表项上方注释区" % (SETTINGS_DICT_NAME, KEY),
    "settings_module_bullet": "settings 模块 docstring 里点名本键的收口条目",
    "pii_mask_test_fix_paragraph": "demo/tests/test_memory_pii_mask.py 模块 docstring 里点名本键的段落",
}


def _anchor_default_path(target):
    if target == "rag":
        return RAG_PY
    if target == "settings":
        return SETTINGS_SERVICE_PY
    if target == "pii_mask_test":
        return PII_MASK_TEST_PY
    raise AssertionError("未知锚点目标档：%r" % target)


def _comments_above(lines, target_lineno):
    """从 target_lineno 往上收「注释行 + 空行」，遇到其它内容即停，再按空行断块。

    跨空行继续上溯是**必需**的：rag 的常量上方既有「现行缺省」块，又有用空行隔开的
    变更史块；只收最近一块的话，锚点就只剩历史块、判据立刻失真。
    """
    idx = target_lineno - 2
    while idx >= 0 and (lines[idx].lstrip().startswith("#") or not lines[idx].strip()):
        idx -= 1
    region_start = idx + 1
    region = lines[region_start:target_lineno - 1]
    return _comment_units(region, region_start + 1)


def _anchor_units(anchor_id, path=None):
    """按结构定位锚点，返回该锚点的表述单元集 [(首行行号, [文本行])]。"""
    target, kind, selector = _ANCHOR_INDEX[anchor_id]
    path = path or _anchor_default_path(target)
    lines = _read_lines(path)
    tree = _parse_file(path)
    if kind == "comments_above_const":
        node = _find_module_assign(tree, selector)
        assert node is not None, "%s 里找不到模块级常量 %s，锚点定位失效" % (path, selector)
        return _comments_above(lines, node.lineno)
    if kind == "comments_above_dict_entry":
        dict_name, key = selector
        node = _find_dict_entry(tree, dict_name, key)
        assert node is not None, "%s 的 %s 里找不到键 %r，锚点定位失效" % (path, dict_name, key)
        return _comments_above(lines, node.lineno)
    if kind == "function_docstring":
        blocks = [(start, text) for owner, start, text in _docstring_blocks(tree)
                  if owner == selector]
        assert blocks, "%s 里找不到函数 %s 的 docstring，锚点定位失效" % (path, selector)
        out = []
        for start, text_lines in blocks:
            out.extend(_segment_units(start, text_lines))
        return out
    if kind == "module_docstring_matching":
        blocks = [(start, text) for owner, start, text in _docstring_blocks(tree)
                  if owner == MODULE_LABEL]
        assert blocks, "%s 没有模块 docstring，锚点定位失效" % path
        units = []
        for start, text_lines in blocks:
            units.extend(_segment_units(start, text_lines))
        matched = [u for u in units if selector in "\n".join(u[1])]
        assert matched, "%s 的模块 docstring 里没有点名 %r 的收口条目，锚点定位失效" % (path, selector)
        return matched
    raise AssertionError("未知锚点定位方式：%r（锚点表与本实现已分叉）" % kind)


def _check_anchor(anchor_id, path, actual):
    """锚点判据本体：该锚点必须至少产出一条可判定表述，且逐点等于代码缺省。"""
    claims = _claims_of_units(path, _anchor_units(anchor_id, path))
    _assert_points_agree(claims, actual,
                         "锚点 %s（%s）" % (anchor_id, _ANCHOR_WHERE[anchor_id]))
    return claims


# ------------------------------------------------------------------
# 5) tests 域扫描面
# ------------------------------------------------------------------
# 用例名与自身断言方向的一致性词表（对应上一轮 mustFix 1 的第二半）：body 里写着
# `get_bool(本键) is True`，名字却叫 `test_default_is_disabled_...` 就是「名字说反话」。
# 只钉「body 断 True 而名字否认」这一方向（实测的唯一缺陷形态）；反方向目前无缺陷证据，
# 本守卫**不**声称覆盖它。
_NAME_DENY_TOKENS_WHEN_BODY_ASSERTS_TRUE = ("is_disabled", "default_is_off", "default_off",
                                            "does_not_alter", "not_masked", "unchanged_write")


def _default_polarity_assertions(fn_node):
    """该测试函数体内 `get_bool(本键) is True/False` 的断言方向列表（返回布尔值列表）。"""
    out = []
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 \
                or not isinstance(node.ops[0], ast.Is):
            continue
        call = node.left
        if not (isinstance(call, ast.Call)
                and getattr(call.func, "attr", "") == "get_bool"):
            continue
        key_arg = call.args[0] if call.args else None
        names_the_key = ((isinstance(key_arg, ast.Constant) and key_arg.value == KEY)
                         or (isinstance(key_arg, ast.Attribute) and key_arg.attr == CONST_NAME)
                         or (isinstance(key_arg, ast.Name) and key_arg.id == CONST_NAME))
        if not names_the_key:
            continue
        comparator = node.comparators[0]
        if isinstance(comparator, ast.Constant) and isinstance(comparator.value, bool):
            out.append((node.lineno, comparator.value))
    return out


def _name_body_conflicts(path):
    """返回 [(行号, 函数名, 断言方向, 命中的反话词)]：名字与 body 断言方向打架的用例。"""
    conflicts = []
    for node in ast.walk(_parse_file(path)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        for lineno, polarity in _default_polarity_assertions(node):
            if polarity is not True:
                continue
            for token in _NAME_DENY_TOKENS_WHEN_BODY_ASSERTS_TRUE:
                if token in node.name:
                    conflicts.append((node.lineno, node.name, polarity, token))
                    break
    return conflicts


def _walk_tests_domain_py_files():
    """roots 下的全部 *.py（豁免文件已剔除），返回 (根标签, 路径)。

    根可能是目录也可能是单文件；单文件必须显式处理，否则 `os.walk` 静默产出 0 个文件、
    扫描面假绿（本仓守卫的既发事故形态）。
    """
    out = []
    for root in TESTS_DOMAIN_ROOTS:
        label = os.path.relpath(root, _REPO_ROOT)
        if os.path.isfile(root):
            if root.endswith(".py") and os.path.abspath(root) not in _SELF_EXEMPT:
                out.append((label, root))
            continue
        assert os.path.isdir(root), "tests 域扫描根不存在或不是目录：%s" % root
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                path = os.path.abspath(os.path.join(dirpath, fn))
                if path in _SELF_EXEMPT:
                    continue
                out.append((label, path))
    return out


def _iter_tests_domain_files():
    """标识符预筛后的 tests 域文件：整篇文本没提到本键的键名/常量名/消费函数名就跳过。"""
    files = []
    for _label, path in _walk_tests_domain_py_files():
        try:
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        if any(tok in src for tok in FILE_SCOPE_TOKENS):
            files.append(path)
    return sorted(files)


def _tests_domain_points(paths=None):
    """tests 域逐点表述：与生产文件用**同一实现、同一词表**（不另写一份口径）。"""
    targets = _iter_tests_domain_files() if paths is None else [os.path.abspath(p) for p in paths]
    points = []
    for path in targets:
        found, _history = _collect_default_claims(path)
        points.extend(found)
    return points


# ------------------------------------------------------------------
# 用例
# ------------------------------------------------------------------
def test_scanned_targets_are_the_real_files():
    """防「路径参数写错导致假绿」：两个被测目标必须存在且真的在讲本键。"""
    for label, path in (("settings_service", SETTINGS_SERVICE_PY), ("rag", RAG_PY)):
        assert os.path.isfile(path), "%s 目标文件不存在：%s" % (label, path)
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        assert (KEY in src) or (CONST_NAME in src), \
            "%s 目标 %s 里既无键名也无常量名，扫它没有意义" % (label, path)


def test_settings_default_for_key_is_true():
    """代码缺省（唯一事实源）：SETTINGS_DEFAULTS 本键必须是 True。

    判别力：把表内值改成 False 的突变体下本用例必红。
    """
    defaults = _settings_defaults(SETTINGS_SERVICE_PY)
    assert KEY in defaults, "本键没登记进 SETTINGS_DEFAULTS，消费点会拿到 None"
    value = defaults[KEY]
    assert value is True, (
        "SETTINGS_DEFAULTS[%r] 必须是布尔 True（当前 %r）。本键的缺省取值由 W-ISO-2 定为"
        "开启（个人信息保护最小化），改成 False 属行为变更，需单独授权并同步全部注释。" % (KEY, value))


def test_rag_consumer_reads_the_same_key_name():
    """消费点键名：rag 的 MEMORY_PII_MASK_KEY 字面量 == SETTINGS_DEFAULTS 的键名。"""
    defaults = _settings_defaults(SETTINGS_SERVICE_PY)
    with open(RAG_PY, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    literal = _rag_key_literal(tree)
    assert literal == KEY, "rag 侧常量 %s 的字面量漂移：得到 %r" % (CONST_NAME, literal)
    assert KEY in defaults, "键名 %r 在 SETTINGS_DEFAULTS 里不存在，rag 读到的是隐式兜底" % KEY


def test_rag_hardcoded_fallbacks_match_settings_default():
    """同一口径不得有两份漂移的实现：rag 写死的兜底必须逐项等于表内缺省。"""
    actual = _settings_defaults(SETTINGS_SERVICE_PY)[KEY]
    with open(RAG_PY, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for kind, lineno, value in _rag_hardcoded_bools(tree):
        assert value == actual, (
            "rag.py:%d 的 %s 写死为 %r，与 SETTINGS_DEFAULTS 的 %r 不一致 —— "
            "改缺省必须同批改这里，否则「未设置」与「表内缺省」会分叉。" % (lineno, kind, value, actual))


@pytest.mark.parametrize("label,path", (
    ("rag", RAG_PY),
    ("settings_service", SETTINGS_SERVICE_PY),
), ids=["rag", "settings_service"])
def test_comment_default_statements_agree_with_code(label, path):
    """全文件口径：注释/docstring 里每一条可判定的现行缺省表述都必须等于代码实测缺省。

    上一轮 mustFix 3 的第二半：原先是 `claims == {actual}` 的**全文件集合相等**，
    红了也定位不到行；现改为逐点断言（消息带 file:line），集合语义的漏洞（「同文件里
    还有一句说对」掩盖漂移）一并由锚点口径补上。
    """
    actual = _code_default()
    claims, _history = _collect_default_claims(path)
    _assert_points_agree(claims, actual, "文件 %s（%s 档）" % (path, label))


@pytest.mark.parametrize("anchor_id", ANCHOR_IDS, ids=list(ANCHOR_IDS))
def test_anchor_positions_state_the_code_default(anchor_id):
    """锚点口径（本波新增）：7 个现行缺省必须正面表态的位置，每个都要有**至少一条**可判定表述。

    与全文件口径的分工：全文件口径只能检查「说了的要说对」，锚点口径额外要求
    「在必须说话的地方确实说了、且说得可判定」。于是把锚点表述换成词表认不出的写法
    （上一轮审计实测的 `当前取值 False` 形态）或被删空，都表现为锚点零表述 → 翻红。
    """
    target, _kind, _selector = _ANCHOR_INDEX[anchor_id]
    _check_anchor(anchor_id, _anchor_default_path(target), _code_default())


@pytest.mark.parametrize("case_id,target,anchor_id,old,new,expect", (
    ("rag_key_block_reworded_out_of_vocabulary", "rag", "rag_key_block",
     "记忆语料 PII 脱敏开关：settings 键 memory_pii_mask_enabled，缺省 True。",
     "记忆语料 PII 脱敏开关：settings 键 memory_pii_mask_enabled，当前取值 False。",
     "red"),
    ("rag_key_block_deleted", "rag", "rag_key_block",
     "# 记忆语料 PII 脱敏开关：settings 键 memory_pii_mask_enabled，缺省 True。\n",
     "", "red"),
    ("settings_table_block_reworded_out_of_vocabulary", "settings",
     "settings_table_entry_block",
     "缺省 True（遵循个人信息保护最小化原则", "当前取值 False（遵循个人信息保护最小化原则",
     "red"),
    ("settings_module_bullet_reworded", "settings", "settings_module_bullet",
     "W-ISO-2 改判为缺省 True；现行缺省为开启", "W-ISO-2 改判为当前取值 False；现行取值为开",
     "red"),
    ("pii_mask_test_fix_paragraph_reworded", "pii_mask_test",
     "pii_mask_test_fix_paragraph",
     "缺省 True（W-ISO-2 改判落地", "当前取值 False（W-ISO-2 改判落地",
     "red"),
    ("rag_key_block_reworded_inside_vocabulary", "rag", "rag_key_block",
     "memory_pii_mask_enabled，缺省 True。", "memory_pii_mask_enabled，缺省即开。", "green"),
), ids=("rag_key_block_reworded_out_of_vocabulary", "rag_key_block_deleted",
        "settings_table_block_reworded_out_of_vocabulary",
        "settings_module_bullet_reworded",
        "pii_mask_test_fix_paragraph_reworded",
        "rag_key_block_reworded_inside_vocabulary"))
def test_anchor_statement_rewording_or_deletion_flips_the_anchor_red(
        tmp_path, case_id, target, anchor_id, old, new, expect):
    """锚点口径的判别力自证（突变体在 tmp_path 副本上，仓库文件只读）。

    `expect="red"` 的三个靶子同时做**两件事**，缺一不可：
      1) 锚点判据必须翻红（新判据有效）；
      2) 全文件口径在该突变体上**仍然一致**（`{实际缺省}` 不变）—— 这条断言把
         「上一轮被打穿的正是全文件口径」钉成机器事实，避免本波只靠文案声称覆盖增强。
    `expect="green"` 的对照证明锚点判据不是恒红：换成词表内另一条同义写法（缺省即开）
    仍然放行。
    """
    src_path = _anchor_default_path(target)
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert src.count(old) == 1, (
        "突变靶子失效：%s 在 %s 里出现 %d 次（应为 1 次），改靶后本用例就是空跑"
        % (old, src_path, src.count(old)))
    mutant = tmp_path / ("anchor_mutant_%s.py" % target)
    mutant.write_text(src.replace(old, new), encoding="utf-8")
    mutant_path = os.path.abspath(str(mutant))
    actual = _code_default()

    if expect == "red":
        with pytest.raises(AssertionError):
            _check_anchor(anchor_id, mutant_path, actual)
        # 全文件口径在同一突变体上仍然「一致」—— 这正是它挡不住换措辞的原因。
        surviving, _h = _collect_default_claims(mutant_path)
        agreeing = [p for p in surviving if not p.contradiction]
        assert {p.value for p in agreeing} == {actual}, (
            "预期「全文件口径在这个突变体上仍然是绿的」，实际得到 %r；说明该突变体同时"
            "打穿了两档口径，锚点用例的第 2 条断言需要重新设计。"
            % sorted({p.value for p in agreeing}))
    else:
        _check_anchor(anchor_id, mutant_path, actual)


def test_tests_domain_default_statements_agree_with_code():
    """tests 域口径（本波新增，对应上一轮 mustFix 2）：tests / demo/tests 两域的表述同受钉。

    上一轮的洞：守卫扫描集只有 rag.py + settings_service.py 两个生产文件，于是
    `demo/tests/test_memory_pii_mask.py` 的 docstring 写「缺省 False」、用例名写
    「default_is_disabled」时**零机器防线**（同一文件另一处 docstring 与 body 都在说
    True，三方互相打架无人发现）。本用例把同一实现、同一词表伸到两域，并要求两域合计
    的可判定表述不为零（把话说光同样翻红）。
    """
    actual = _code_default()
    points = _tests_domain_points()
    _assert_points_agree(points, actual, "tests 域（%s）" % "、".join(_iter_tests_domain_files()))


def test_tests_domain_case_names_do_not_contradict_their_own_default_assertion():
    """名字口径（本波新增，对应上一轮 mustFix 1 的第二半）：用例名不得与自己的缺省断言反着说。

    上一轮的形态：`test_default_is_disabled_and_does_not_alter_write` 的 body 写着
    `assert settings_service.get_bool(rag.MEMORY_PII_MASK_KEY) is True`，三方（名字 /
    同文件 docstring / body）里只有名字在说反话，而守卫扫描集里没有 tests 域 → 零机器防线。
    本判据把「body 断言本键缺省为 True，名字却用否认式词形」变成硬错误，逐点报 file:line。
    """
    actual = _code_default()
    offenders = []
    for path in _iter_tests_domain_files():
        for lineno, name, polarity, token in _name_body_conflicts(path):
            offenders.append("%s:%d %s() body 断言 get_bool(%s) is %r，"
                             "用例名却含 %r —— 名字与断言方向相反" % (path, lineno, name, KEY,
                                                                     polarity, token))
    assert not offenders, (
        "tests 域里有 %d 处用例名与自身缺省断言互相矛盾（代码缺省实测 %r）：\n  %s"
        % (len(offenders), actual, "\n  ".join(offenders)))


def test_misnamed_default_case_flips_the_name_guard_red(tmp_path):
    """上一条的判别力自证：把用例名改回上一轮那个说反话的名字，判据必须翻红。

    同时断言「只改 docstring 不改名字」不会被本判据误伤 —— 名字判据与表述判据各管一件事，
    避免两条判据退化成同一件事的两份实现。
    """
    src_path = PII_MASK_TEST_PY
    with open(src_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    # 只打「def 行」这一个靶子：该文件 docstring 里也提到这两个名字（讲改名史），
    # 全文替换会把 docstring 一起改掉，那样反证就同时动了两个判据、失去定位意义。
    good_def = ("def test_default_enabled_masks_write_and_explicit_off_is_byte_identical(")
    bad_def = "def test_default_is_disabled_and_does_not_alter_write("
    assert src.count(good_def) == 1, (
        "反证靶子失效：%s 在 %s 里出现 %d 次（应为 1 次）" % (good_def, src_path, src.count(good_def)))
    mutant = tmp_path / "test_name_guard_mutant.py"
    mutant.write_text(src.replace(good_def, bad_def), encoding="utf-8")
    mutant_path = os.path.abspath(str(mutant))

    conflicts = _name_body_conflicts(mutant_path)
    assert conflicts, (
        "把用例名改回说反话的旧名，名字判据却什么都没抓到 —— 判据恒真，不予交付。")
    assert any("is_disabled" in token for _l, _n, _p, token in conflicts), conflicts
    assert not _name_body_conflicts(src_path), (
        "真树上的 %s 竟然被名字判据判成矛盾 —— 判据误伤，会因噪声被顺手关掉。" % src_path)


def test_tests_domain_scan_roots_are_reachable_and_exemption_is_frozen():
    """覆盖面自校准：每个根都要真的走得到，豁免面只能是本守卫文件自己。

    三条各挡一种假绿：
      - 根被删/改名 → 「根不存在」翻红；
      - 单文件根被 `os.walk` 静默跳过（本仓既发事故形态）→ 「每个根至少 1 个 .py」翻红；
      - 有人往豁免列表里塞第二个文件来蒙过判据 → 冻结集合相等断言翻红。
    """
    walked = _walk_tests_domain_py_files()
    by_root = {}
    for label, path in walked:
        by_root.setdefault(label, []).append(path)
    for root in TESTS_DOMAIN_ROOTS:
        label = os.path.relpath(root, _REPO_ROOT)
        assert by_root.get(label), (
            "扫描根 %s 一个 .py 都没捞到（根被改名/删掉，或单文件根被 os.walk 静默跳过）" % root)
    assert by_root[os.path.relpath(_TESTS_DIR, _REPO_ROOT)], "tests/ 目录域没有产出文件"
    assert by_root[os.path.relpath(_DEMO_TESTS_DIR, _REPO_ROOT)], "demo/tests/ 目录域没有产出文件"
    assert _iter_tests_domain_files(), (
        "tests 域里没有任何文件提到本键的标识符 —— 要么扫描面缩水了，要么两个测试文件"
        "都被改名/删除；两者都必须显式处理。")
    assert set(_SELF_EXEMPT) == {os.path.abspath(__file__)}, (
        "豁免面必须只有本守卫文件自己（想扩豁免请连理由一起改这里，并先证明被豁免文件"
        "确实按构造无法满足判据）。")


def test_tests_domain_exemption_is_load_bearing():
    """自证豁免不是空转：本文件若不豁免，就会被自己的判据判红。

    上一轮审核批评「守卫复刻了第二份实现 / 判据恒真」。这里给出与本文件对称的证据：
    把本文件自己喂进同一判据，必须得到「自相矛盾」或「与代码缺省不等」的逐点结果。
    """
    actual = _code_default()
    own_points, _history = _collect_default_claims(os.path.abspath(__file__))
    assert own_points, (
        "本守卫文件自身没有任何可判定的本键缺省表述 —— 那豁免就是空转，判据覆盖面"
        "声明需要重写（本文件的 docstring 必然引用两种取值，正常不会走到这里）。")
    bad = [p for p in own_points if p.contradiction or p.value != actual]
    assert bad, (
        "本文件自己的表述竟然全部与代码缺省一致（%r）—— 说明豁免面被高估，"
        "应把本文件纳入正常判据并改写 docstring。" % [(p.lineno, p.value) for p in own_points])


@pytest.mark.parametrize("case_id,statement,expect", (
    ("wrong_default_statement_in_tests_domain",
     "开关 `memory_pii_mask_enabled` 缺省 False = 逐字节零行为变化。", "red"),
    ("out_of_vocabulary_default_statement",
     "开关 `memory_pii_mask_enabled` 当前取值 False（没有任何词表词）。", "red_via_empty"),
    ("right_default_statement_in_tests_domain",
     "开关 `memory_pii_mask_enabled` 缺省 True，默认写入即脱敏。", "green"),
), ids=["wrong_default_statement_in_tests_domain",
        "out_of_vocabulary_default_statement",
        "right_default_statement_in_tests_domain"])
def test_tests_domain_flips_red_on_a_wrong_statement(tmp_path, case_id, statement, expect):
    """tests 域判据的判别力自证（夹具落在 tmp_path，不进仓库、不进 live 目录）。

    第三态 `red_via_empty` 对应「换成词表认不出的措辞」：该句不再可判定 → 域内合计
    表述为零 → 「删光表述同样翻红」那条非空断言接管。这条正是上一轮 mustFix 3 里
    「词表门放行自然中文错表述」的靶子。
    """
    fixture = tmp_path / "test_domain_fixture_default_statement.py"
    fixture.write_text(u'# -*- coding: utf-8 -*-\n"""夹具：%s\n"""\n\nX = 1\n' % statement,
                       encoding="utf-8")
    actual = _code_default()
    points = _tests_domain_points([str(fixture)])
    if expect == "red":
        assert points, "夹具表述应当可判定，否则本靶子中不了"
        assert any(p.value is False for p in points), points
        with pytest.raises(AssertionError):
            _assert_points_agree(points, actual, "夹具 %s" % fixture)
    elif expect == "red_via_empty":
        assert not points, (
            "夹具里的「词表外」措辞竟然被判定了（%r）—— 说明词表比宣称的更宽，"
            "本用例的第三态需要改写。" % [(p.text, p.value) for p in points])
        with pytest.raises(AssertionError):
            _assert_points_agree(points, actual, "夹具 %s" % fixture)
    else:
        _assert_points_agree(points, actual, "夹具 %s" % fixture)


def test_history_block_exists_exactly_once_and_only_in_rag():
    """「缺省值变更史」只在 rag.py 登记一份；其他文件只准指向它。"""
    _rag_claims, rag_history = _collect_default_claims(RAG_PY)
    _st_claims, st_history = _collect_default_claims(SETTINGS_SERVICE_PY)
    assert len(rag_history) == 1, (
        "rag.py 里以 %r 起头的变更史注释块应有且只有一份，实际 %d 份。"
        % (HISTORY_SENTINEL, len(rag_history)))
    assert not st_history, (
        "settings_service.py 里出现了第二份变更史块 —— 同一口径只保留一份，"
        "该文件只写现行缺省并指向 rag.py 的变更史。")


def test_history_block_records_wave_commit_and_conclusion():
    """变更史必须答对四问：何时、由哪一波、依据什么、False 还是不是设计意图。"""
    _claims, rag_history = _collect_default_claims(RAG_PY)
    assert rag_history, (
        "rag.py 里没有以 %r 起头的变更史块，先补块再谈要素（别让本用例退化成索引越界）。"
        % HISTORY_SENTINEL)
    block = rag_history[0]
    required = ("W-9", "W-ISO-2", "False", "True", "057f3c6", "2026-10-03", "漏改", "现行")
    missing = [tok for tok in required if tok not in block]
    assert not missing, (
        "变更史块缺少必要事实 %r（须含引入波 W-9、改判波 W-ISO-2、commit 057f3c6、日期 "
        "2026-10-03、两种取值、漏改成因与「False 非现行意图」的结论）。" % missing)


def test_settings_service_defers_to_single_history_source():
    """settings_service 侧必须留一条指向 rag.py 变更史块的指引，避免第二处口径。"""
    with open(SETTINGS_SERVICE_PY, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert "变更史" in src, (
        "settings_service.py 未指向变更史唯一登记处（app/services/rag.py 的 %s 上方注释块）。"
        % CONST_NAME)
    assert CONST_NAME in src, (
        "settings_service.py 的指引要写到常量名 %s，光说「rag.py」定位不到块。" % CONST_NAME)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

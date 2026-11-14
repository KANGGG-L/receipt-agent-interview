# -*- coding: utf-8 -*-
"""`.env.example` 与代码实际读取的环境变量必须对得上（E 项）。

两边的错误都会静默伤人：
- 模板里写着但代码零读取的键 → 运维按模板设了完全无效（本次确认的两个历史残留：
  `OPENCODE_RECOGNITION_MODEL` / `OPENCODE_AUDIT_MODEL`，全仓只出现在 .env* 里）；
- 代码读取但模板没声明的键 → 运维不知道这些开关存在，只能靠读源码发现。

本文件把「读取面」钉成一份可复算的清单，并**双向**核对：
1. 清单里每个键都真的在 demo/app 源码里出现（防止清单本身腐烂）；
2. 清单 ⊆ 模板声明（补声明）；
3. 模板里**生效**（非注释）的键 ⊆ 代码里出现过的键（防死键回归）；
4. **代码里被读取的键 ⊆ 清单 ∪ 待登记台账**（P0 收口波 T3 新增的反向核对）。

why 第 4 条必须存在（上一轮审核 mustFix 第 2 条）：1～3 全是**单向包含**，新键只要
不登记进 `ENV_READ_KEYS` 就永远不会被任何一条报出来 —— 2026-10-20 落地 `CHROMA_PATH`
时正是这样漏掉的（当时三条用例全绿）。而 `demo/.env.example` 的文件头把这份清单当
权威指过来，清单少一键就等于模板在说谎。第 4 条用静态扫描器从源码反推「被读取的键」，
和清单做减法，多出来的键必须显式进 `ENV_READ_KEYS_DEFERRED` 并写明理由，否则翻红。

静态扫描器必须能穿过**间接层**：`rag_paths.py` 把键名放在模块常量里、再经本地
`_env(name)` 与 `for name in (_ENV_CHROMA_PATH, _ENV_RAG_DIR)` 喂给 `os.environ.get`。
`test_scanner_resolves_the_indirect_key_reads` 专门钉这一点 —— 扫不到 CHROMA_PATH 的
扫描器等于把第 4 条写成恒真判据。

真正无法静态定值的站点（如 `embeddings._resolve_pair` 里按键名列表逐个试的
`os.environ.get(base_env)`）冻结在 `ENV_READ_DYNAMIC_SITES` 里，出现新的不透明站点即
翻红；残留盲区：同文件同名变量再增加一处读取不会被该清单抓到。
"""

import ast
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

DEMO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_EXAMPLE = os.path.join(DEMO, ".env.example")
ENV_FILE = os.path.join(DEMO, ".env")

# 代码实际读取的环境变量（2026-09-20 用 grep `os.environ` / `os.getenv` 全量比对得出；
# 下面 test_every_listed_key_appears_in_source 会对本清单逐项复核，防止它腐烂）。
# 2026-10-10 移除 4 个 vendor 专属键（DASHSCOPE_AUDIT_MODEL / QWEN_VL_MODEL /
# SILICONFLOW_AUDIT_MODEL / SILICONFLOW_MODEL）——双腿装配已全面迁移到 OPENAI_REC_* /
# OPENAI_AUD_*，这四个键在 demo/app 源码里已零读取（见下方自守卫用例的报腐输出）。
# 2026-10-20（P0 收口波 T3）：反向核对上线后一次补齐 11 个键 —— CHROMA_PATH 是 T1 新增
# 的高优先级向量落点键（上一轮审核 mustFix 第 2 条点名它漏登）；其余 10 个是反向核对
# 当场扫出的「.env.example 已声明、这份台账漏登」的键，补进来不需要动模板。
# **新增键必须同时登记到这里**，否则 test_source_env_reads_are_registered_or_deferred
# 直接翻红 —— 这正是这条清单从「单向」变成「双向」的意义。
ENV_READ_KEYS = frozenset({
    "AUDIT_ENGINE", "AUDIT_MODEL",
    "APP_ENV", "APP_PORT", "APP_RELOAD", "APP_SECRET_KEY", "ENV",
    "AUTH_ENABLED", "BACKUP_DIR", "BACKUP_INTERVAL_HOURS", "BACKUP_KEEP",
    "CHROMA_PATH",
    "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL",
    "DB_PATH", "DEMO_TOKEN_SECRET", "DISABLE_BACKGROUND_JOBS",
    "ENGINE_CALL_TIMEOUT", "EVALSET_DIR", "EXTRACT_PROMPT_VERSION",
    "GUARDIAN_INTERVAL_MINUTES", "INTEGRITY_CHECK_INTERVAL_HOURS",
    "MOCK_QWEN_FLASH",
    "OPENAI_API_KEY", "OPENAI_AUD_API_KEY", "OPENAI_AUD_BASE_URL", "OPENAI_AUD_MODEL",
    "OPENAI_BASE_URL", "OPENAI_MODEL",
    "OPENAI_PARSE_API_KEY", "OPENAI_PARSE_BASE_URL", "OPENAI_PARSE_MODEL",
    "OPENAI_REC_API_KEY", "OPENAI_REC_BASE_URL", "OPENAI_REC_CALL_TIMEOUT",
    "OPENAI_REC_MODEL",
    "PARSING_SWEEP_INTERVAL_SECONDS",
    "RAG_DIR", "RAG_EMBEDDING_PROVIDER",
    "RECOGNITION_ENGINE", "RECOGNITION_MODEL",
    "SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL",
})

# 代码确实读取、但**尚未**登记进上面清单的键 → 待登记台账（键 → 理由）。
# 它是「反向核对」唯一的豁免通道，因此必须逐条给理由，且由
# `test_unregistered_reads_match_the_deferred_ledger` 钉成**恰好相等**：
# 新键进来不加解释即翻红；把某个键补进 ENV_READ_KEYS 后不删这里的条目也翻红
# （台账只能缩不能长）。本波独占文件只有本文件 + 两处 conftest，不含
# `demo/.env.example`，故「进清单」必须先补模板声明的这 3 个键留给主 agent 同批收口；
# PYTEST_CURRENT_TEST 则永久不该出现在模板里（它不是运维可设的配置面）。
ENV_READ_KEYS_DEFERRED = {
    "ARTIFACTS_DIR": "产物目录落盘隔离点（supervisor._memory_log_path 读），真实运维开关；"
                     "demo/.env.example 尚无声明行，登记进清单需与模板声明同批改",
    "RECEIPT_OFFLINE_EVAL": "离线评测开关；demo/.env.example 尚无声明行，"
                            "登记进清单需与模板声明同批改",
    "VENDOR_PRECHECK_ENABLED": "预判供应商开关（计划 §1 开关实况表里线上为 False）；"
                               "demo/.env.example 尚无声明行，登记进清单需与模板声明同批改",
    "PYTEST_CURRENT_TEST": "pytest 注入的运行期探针（代码只用它判断「是否在测试里」），"
                           "登记进清单/模板会诱导运维去设置一个无意义的环境变量",
    # 2026-11 计费 / 身份 v2 / 持久任务波次新增的读取键。demo/.env.example 尚无
    # 声明行，按台账规则逐条给理由；待模板同批补齐声明后再移入 ENV_READ_KEYS。
    "BILLING_ACCEPT_NEW_CHECKOUT": "计费发布门禁运行时开关（billing/release_gate.runtime_flags）；模板未声明",
    "BILLING_AUTO_RENEW": "计费发布门禁运行时开关（billing/release_gate.runtime_flags）；模板未声明",
    "BILLING_ENFORCEMENT_ENABLED": "计费执行总开关（billing/policy）；模板未声明",
    "BILLING_ENVIRONMENT": "计费环境标识（billing/policy）；模板未声明",
    "BILLING_POLICY_JSON": "退款/试用策略覆盖（billing/policy）；模板未声明",
    "BILLING_WEBHOOK_SECRET": "支付回调签名密钥（billing/lifecycle）；模板未声明，且不宜写进示例模板",
    "DURABLE_TASKS_ENABLED": "持久化后台任务开关（background_tasks）；模板未声明",
    "IDENTITY_V2_ENABLED": "身份 v2 灰度开关（auth / identity_store）；模板未声明",
}

# 静态无法定值的键名读取站点：(相对 demo/ 的文件路径, 变量名)。冻结口径见模块 docstring。
ENV_READ_DYNAMIC_SITES = frozenset({
    ("app/services/embeddings.py", "base_env"),
    ("app/services/embeddings.py", "key_env"),
    # 计费发布门禁用 _flag(name) 统一读开关，键名由调用方传入，静态无法定值。
    ("app/billing/release_gate.py", "name"),
})

# 已确认零读取、已从模板移除的历史残留键。
# 前两个是历史重命名残留；后四个随「本机 CLI 引擎（opencode / codebuddy）2026-09-02 弃用」
# 一并删除 —— 对应的模型类、常驻进程管理器、插件与 EngineKind 成员都已从代码中删除，
# 代码再也不会读这四个键。
DEAD_KEYS = (
    "OPENCODE_RECOGNITION_MODEL", "OPENCODE_AUDIT_MODEL",
    "OPENCODE_BIN", "CODEBUDDY_BIN", "CODEBUDDY_MODEL", "CODEBUDDY_PERMISSION_MODE",
)

_ASSIGN_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")
_COMMENTED_ASSIGN_RE = re.compile(r"^\s*#\s*([A-Z][A-Z0-9_]*)\s*=")
_KEY_SHAPE_RE = re.compile(r"[A-Z][A-Z0-9_]{2,}\Z")

# 表示「读环境变量」的调用属性名与它们的首参语义：
#   os.environ[K] / os.environ.get(K, ...) / .setdefault(K, ...) / .pop(K, ...) / os.getenv(K)
_ENV_LOOKUP_METHODS = ("get", "setdefault", "pop")


def _app_sources():
    for path in glob.glob(os.path.join(DEMO, "app", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            yield path, f.read()


def _parse_env_file(path):
    """返回 (生效键, 被注释掉的键)。"""
    active, commented = set(), set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = _COMMENTED_ASSIGN_RE.match(line)
            if m:
                commented.add(m.group(1))
                continue
            m = _ASSIGN_RE.match(line)
            if m:
                active.add(m.group(1))
    return active, commented


# ---------------------------------------------------------------
# 反向核对用的静态扫描器（测试侧工具，不是产品实现的第二份）
# ---------------------------------------------------------------
def _is_key_literal(node):
    """节点是否为「形如环境变量键名」的字符串字面量。"""
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and _KEY_SHAPE_RE.match(node.value) is not None)


def _env_lookup_key_node(node):
    """把「读取环境变量」的表达式节点映射到表示键名的子节点；非读取站点返回 None。"""
    if isinstance(node, ast.Subscript):
        base = node.value
        if not (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                and base.value.id == "os" and base.attr == "environ"):
            return None
        index_cls = getattr(ast, "Index", None)
        if index_cls is not None and isinstance(node.slice, index_cls):  # py<3.9 兼容
            return node.slice.value
        return node.slice
    if not (isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Attribute)):
        return None
    func = node.func
    owner = func.value
    # os.environ.<get|setdefault|pop>("KEY", ...)
    if (isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)
            and owner.value.id == "os" and owner.attr == "environ"
            and func.attr in _ENV_LOOKUP_METHODS):
        return node.args[0]
    # os.getenv("KEY", ...)
    if (isinstance(owner, ast.Name) and owner.id == "os" and func.attr == "getenv"):
        return node.args[0]
    return None


def _key_name_bindings(source_tree):
    """模块内 `NAME = "字面量"` 定值表 + `for NAME in (A, B)` 的候选元素表。

    为什么需要它：`rag_paths.py` 不写字面量键名，而是 `_ENV_CHROMA_PATH = "CHROMA_PATH"`
    再 `for name in (_ENV_CHROMA_PATH, _ENV_RAG_DIR): _env(name)`。只看字面量的扫描器对
    这种写法是瞎的，反向核对就会对 CHROMA_PATH 永久漏检（= 恒真判据）。
    """
    consts, for_elements = {}, {}
    for node in ast.walk(source_tree):
        if isinstance(node, ast.Assign) and _is_key_literal(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value.value
        elif (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set))):
            for_elements.setdefault(node.target.id, []).extend(node.iter.elts)
    return consts, for_elements


def _env_reader_param_indexes(source_tree):
    """本文件里「把某个入参直接喂给 os.environ 查询」的函数：函数名 → 参数下标。

    `rag_paths._env(name)` 就是这一类：真正的读取发生在 helper 内部，调用点只给出
    `name`。不认得 helper 就认不得 helper 的调用点读了哪些键。
    """
    out = {}
    for func in (n for n in ast.walk(source_tree) if isinstance(n, ast.FunctionDef)):
        params = [a.arg for a in func.args.args]
        indexes = set()
        for node in ast.walk(func):
            key_node = _env_lookup_key_node(node)
            if isinstance(key_node, ast.Name) and key_node.id in params:
                indexes.add(params.index(key_node.id))
        if indexes:
            out[func.name] = sorted(indexes)
    return out


def _resolve_key_expr(node, consts, for_elements, seen=frozenset()):
    """把键名表达式定值为「候选字面量集合」；解不开时返回变量名供调用方记动态站点。"""
    if _is_key_literal(node):
        return {node.value}, set()
    if isinstance(node, ast.Name):
        if id(node) in seen:  # 防自引用元组之类的无限递归
            return set(), {node.id}
        if node.id in consts:
            return {consts[node.id]}, set()
        if node.id in for_elements:
            keys, dyn = set(), set()
            for element in for_elements[node.id]:
                sub_keys, sub_dyn = _resolve_key_expr(
                    element, consts, for_elements, seen | {id(node)})
                keys |= sub_keys
                dyn |= sub_dyn
            if keys:
                return keys, set()
            return set(), {node.id}
        return set(), {node.id}
    if isinstance(node, ast.JoinedStr):
        return set(), {"f-string"}
    return set(), {"<unsupported>"}


_SCAN_CACHE = {}


def _scan_source_env_reads():
    """扫描 demo/app/**/*.py，返回 (静态可定值的键 → 文件集合, 动态站点集合)。

    结果按进程缓存一次（75 个文件的 ast 解析不重复付 4 次）。
    """
    if "result" in _SCAN_CACHE:
        return _SCAN_CACHE["result"]
    keys = {}
    dynamic = set()
    for path, source in _app_sources():
        rel = os.path.relpath(path, DEMO)
        tree = ast.parse(source)
        consts, for_elements = _key_name_bindings(tree)
        readers = _env_reader_param_indexes(tree)

        def record(key, where):
            keys.setdefault(key, set()).add(where)

        def mark_dynamic(tag, where):
            dynamic.add((where, tag))

        for node in ast.walk(tree):
            # (a) 直接读取站点
            key_node = _env_lookup_key_node(node)
            if key_node is not None:
                resolved, unresolved = _resolve_key_expr(key_node, consts, for_elements)
                for key in resolved:
                    record(key, rel)
                if not resolved:
                    for tag in unresolved:
                        mark_dynamic(tag, rel)
            # (b) helper 调用站点：helper 的参数位上给了什么键名
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in readers):
                for index in readers[node.func.id]:
                    if index >= len(node.args):
                        continue
                    resolved, unresolved = _resolve_key_expr(
                        node.args[index], consts, for_elements)
                    for key in resolved:
                        record(key, rel)
                    if not resolved:
                        for tag in unresolved:
                            mark_dynamic("helper:%s:%s" % (node.func.id, tag), rel)
    _SCAN_CACHE["result"] = (keys, dynamic)
    return keys, dynamic


def test_every_listed_key_appears_in_source():
    """清单里的每个键必须真的在 demo/app 源码里出现（防清单腐烂）。"""
    all_src = "\n".join(src for _, src in _app_sources())
    missing = [k for k in sorted(ENV_READ_KEYS) if f'"{k}"' not in all_src]
    assert not missing, f"这些键已不再被代码读取，请从清单移除: {missing}"


def test_all_read_keys_are_declared_in_example():
    """代码读取的键必须在 .env.example 里声明（生效或注释均可）。"""
    active, commented = _parse_env_file(ENV_EXAMPLE)
    declared = active | commented
    missing = sorted(ENV_READ_KEYS - declared)
    assert not missing, f".env.example 漏声明这些代码实际读取的键: {missing}"


def test_no_dead_keys_in_example():
    """模板里**生效**的键必须真被代码读取，否则就是又一个「设了没用」的键。"""
    active, _ = _parse_env_file(ENV_EXAMPLE)
    all_src = "\n".join(src for _, src in _app_sources())
    dead = [k for k in sorted(active) if f'"{k}"' not in all_src]
    assert not dead, f".env.example 里这些生效键代码零读取: {dead}"


# ---------------------------------------------------------------
# 4. 反向核对：源码里被读取的键 ⊆ 清单 ∪ 待登记台账
# ---------------------------------------------------------------
def test_scanner_resolves_the_indirect_key_reads():
    """扫描器必须能穿过 rag_paths 的常量 + helper + for 元组间接层读到键名。

    这条是第 4 项判据的**自校准**：CHROMA_PATH / RAG_DIR 在 demo/app 里唯一的读取形态
    就是间接的（`_ENV_CHROMA_PATH` 常量 + `_env(name)` + `for name in (...)`）。若扫描器
    退回「只认字面量」，反向核对就对这两个键永久漏检、变成恒真判据 —— 本用例先翻红。
    回滚修复同样翻红：把 rag_paths 的间接读取改回写字面量以外的任何形式（例如删掉
    CHROMA_PATH 这一档）时，这里读不到 CHROMA_PATH 即红。
    """
    scanned, _ = _scan_source_env_reads()
    for key in ("CHROMA_PATH", "RAG_DIR", "DB_PATH"):
        assert key in scanned, f"扫描器读不到 {key}，反向核对对它已失效（见本用例 docstring）"
    readers_of_chroma = {os.path.basename(p) for p in scanned["CHROMA_PATH"]}
    assert "rag_paths.py" in readers_of_chroma, (
        f"CHROMA_PATH 的读取点必须包含 rag_paths.py（新键优先级的唯一实现处），"
        f"实际 {sorted(readers_of_chroma)}")


def test_source_env_reads_are_registered_or_deferred():
    """源码里被读取的环境变量键必须都在清单内，或显式待在待登记台账里。

    这是「防止下一位再加隐形键」的那一条：新键既不进清单又不进台账（带理由）→ 翻红。
    """
    scanned, _ = _scan_source_env_reads()
    invisible = sorted(set(scanned) - ENV_READ_KEYS - set(ENV_READ_KEYS_DEFERRED))
    assert not invisible, (
        "这些环境变量在 demo/app 源码里被读取，却既不在 ENV_READ_KEYS 清单、也不在"
        "ENV_READ_KEYS_DEFERRED 待登记台账里（= 运维看不见的隐形键）：%s" % invisible)


def test_unregistered_reads_match_the_deferred_ledger():
    """待登记台账必须与「实际未登记的读取键」**恰好相等**，且每条都有理由。

    双向相等才让台账不至于变成第二份豁免黑洞：漏一条（新键悄悄不登记）多一条
    （键已进清单或已不再被读取却还挂在台账上）都翻红。
    """
    scanned, _ = _scan_source_env_reads()
    unregistered = set(scanned) - ENV_READ_KEYS
    assert unregistered == set(ENV_READ_KEYS_DEFERRED), (
        f"待登记台账与实际未登记的读取键不一致，需同步："
        f"台账缺 {sorted(unregistered - set(ENV_READ_KEYS_DEFERRED))}、"
        f"台账多 {sorted(set(ENV_READ_KEYS_DEFERRED) - unregistered)}")
    assert not (ENV_READ_KEYS & set(ENV_READ_KEYS_DEFERRED)), \
        "同一个键不得同时出现在清单与待登记台账里"
    no_reason = sorted(k for k, v in ENV_READ_KEYS_DEFERRED.items() if not (v or "").strip())
    assert not no_reason, f"这些未登记键没写理由: {no_reason}"


def test_dynamic_key_read_sites_are_the_frozen_ones():
    """静态定不了值的键名读取站点必须还是那几处；出现新的不透明通道即翻红。

    盲区（诚实记账，见模块 docstring）：同一文件里同名变量再增加一处读取不会新增条目。
    """
    _, dynamic = _scan_source_env_reads()
    assert dynamic == set(ENV_READ_DYNAMIC_SITES), (
        f"出现新的「静态无法定值」环境变量读取站点，需确认是否要登记进清单/台账："
        f"实际 {sorted(dynamic)}，冻结 {sorted(ENV_READ_DYNAMIC_SITES)}")


def test_new_key_chroma_path_is_registered_and_declared():
    """点名钉住本波补的 CHROMA_PATH：既在清单、也在模板里以生效形式声明。

    why 单独一条：反向核对靠扫描器（间接层一断就整体失明），这条用「清单/模板」两个
    显式视图各钉一次，任一侧被回滚都能定位到具体是哪一侧漏了。
    """
    assert "CHROMA_PATH" in ENV_READ_KEYS, (
        "CHROMA_PATH 是 rag_paths.resolve_rag_dir() 优先级最高的环境变量档，"
        "必须登记在读取面台账里")
    active, commented = _parse_env_file(ENV_EXAMPLE)
    assert "CHROMA_PATH" in active, (
        f"CHROMA_PATH 必须以生效形式声明在 .env.example（当前 active={sorted(active)}、"
        f"commented={sorted(commented)}）")


def test_deprecated_open_code_keys_are_not_active():
    """两个历史残留键不得再以生效形式出现在 .env / .env.example。"""
    active, _ = _parse_env_file(ENV_EXAMPLE)
    assert not [k for k in DEAD_KEYS if k in active]
    if os.path.exists(ENV_FILE):
        live_active, _ = _parse_env_file(ENV_FILE)
        assert not [k for k in DEAD_KEYS if k in live_active], (
            ".env 里仍生效的 OPENCODE_* 模型键零读取，应移除或注释")


@pytest.mark.parametrize("key", DEAD_KEYS)
def test_dead_keys_are_really_unread(key):
    """前提复核：这两个键确实零读取（不允许出现在 app 源码里）。"""
    hits = [path for path, src in _app_sources() if f'"{key}"' in src or f"'{key}'" in src]
    assert not hits, f"这些文件竟引用了 {key}，本项「零读取」结论需重新评估: {hits}"


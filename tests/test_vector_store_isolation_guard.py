# -*- coding: utf-8 -*-
"""P0-T3 防复发守卫：测试/脚本一律不得把向量写进生产向量目录 `<demo>/.rag_chroma`。

背景（缺口 G1，计划 §2/§3）
--------------------------
`demo/tests/test_uat_031_sku_merge_batch_migration.py:29` 这类文件在**模块 import 期**就
设 `os.environ["DB_PATH"]` 指向临时库，而 conftest 的 `_isolate_rag_dir` 是 **per-test
monkeypatch**，覆盖不到 import 期；在 T1 之前 `rag_paths.resolve_rag_dir()` 的优先级里又
没有「随 `DB_PATH` 派生」这一档 —— 于是「DB 外置、向量目录没外置」的运行把向量写进
生产目录。2026-10-20 实测：live 52 条向量里有 40 条是夹具产物（`tenant_uat_a_*` 30 条 /
`tenant_uat_b_*` 10 条，vendor 全是 `Vendor_A` / `Vendor_B`），巡检 `consistent=false`、
孤儿 20。纪律（「跑测前必须同时设 DB_PATH 与 RAG_DIR」）挡不住下一次换形态的复发，
本文件把它变成**可执行判据**。

两条腿
------
腿 A（端到端指纹守卫）：以「`DB_PATH` 指向 tmp 库、且**不设** `RAG_DIR`/`CHROMA_PATH`」
的形态，在**子进程里真 import** `app.services.rag` 并调用 `rag._store()` —— 这是
「会建 store 的最小路径」，且 import 期解析正是事故窗口（在进程内断言
`rag.PERSIST_DIR` 会被 conftest 的夹具改写过，证不了 import 期的行为）。Chroma 构造被
替换成记录器（真实 Chroma 不启动、一个字节都不落盘），但**目录解析是真跑的**。判据：
  1. 解析结果不得等于生产向量目录（`<demo>/.rag_chroma` / `<repo>/.rag_chroma`）；
  2. 解析结果必须落在 `DB_PATH` 同目录；
  3. 解析结果必须落在仓库之外；
  4. `<demo>/.rag_chroma` 指纹（相对路径集合 + 总字节数 + chroma.sqlite3 md5）与
     `demo/receipt_demo.db` 的**三元组**（主文件 / `-wal` / `-shm` 三个 md5）在探针前后
     逐项不变。why 三件而不是主文件一件：`demo/receipt_demo.db` 实测
     `journal_mode=wal`，未 checkpoint 的写入只在 `-wal` 里 —— 只看主文件时「刚写了一批
     单据」与「什么都没动」的 md5 可以完全相同（同族坑：§9 的「DB 走副本」只拷主文件时
     SQLite 直接报 `unable to open database file`，实测见
     `test_leg_a_db_fingerprint_catches_wal_sidecar_only_change`）。

对 T1 的依赖（判据 2/3 的可证性来源）
------------------------------------
判据 2/3 依赖 T1 的派生分支（`resolve_rag_dir()` 的「随外置 `DB_PATH` 派生
`<DB 同目录>/<DB 名>.rag_chroma`」）。T1 未落地时该解析结果是 `<demo>/.rag_chroma`，
本文件的腿 A 必然为红 —— 那是「守卫先于修复复现问题」的顺序要求（计划 §3 T3 验收判据）。
T1 已落地（2026-10-20），所以现在腿 A 为绿；但这份绿必须是**可翻红**的：
`test_leg_a_judge_catches_mutant_rag_paths_without_derived_branch` 用 `/tmp` 上的
rag_paths 突变体（删掉派生分支）钉住这条依赖关系本身 —— 去掉派生分支 → 判据转红，
且同一突变体上「只看函数名在册」的旧判据仍为真（`test_leg_b_derived_branch_is_wired_
into_the_resolver` 把这个差集当判据，审核第 4 条）。

腿 B（结构性扫描守卫）
--------------------
B1 生产树（`demo/app`、`demo/scripts`、`<repo>/scripts`）里除**唯一实现模块**外，
   不得再出现「自行拼接 `.rag_chroma` 默认目录」的可执行字符串常量（=第二份解析口径）。
B2 唯一实现只能有**一份定义**（全仓 `def resolve_rag_dir` 计数 == 1），否则「复制整份
   rag_paths」可以绕过 B1 的文件豁免。
B3 优先级链不得被复刻：`os.environ.get("RAG_DIR"/"CHROMA_PATH", <缺省>)` 这种「带缺省值
   的读」即第二份链（旧缺陷 P6-X2 D1 的形态）。
B4 落点不得被指回 live：任何把 `RAG_DIR`/`CHROMA_PATH` 或 `persist_directory=` 指向
   「自行拼接的默认目录」的代码（含测试文件）即失败 —— 这等于主动关掉隔离。
   名字追踪做**传递闭包**（不动点迭代）：`_base = join(cwd, ".rag_chroma")` →
   `_deep = _base + "/nested"` → `os.environ["RAG_DIR"] = _deep` 这类两跳/多跳别名同样
   判红（上一轮审核实测「只追一跳」时该形态零命中，故本条现在的范围声明是「闭包」，
   不再只是「一跳」）。闭包只沿「赋值引用了已标记名字」扩张，不猜运行期值。
   B4 认的落点入口只有四类：`os.environ[K] =`、`os.environ.setdefault(K, …)`、
   `os.environ.update({K: …})`、`Chroma/PersistentClient(..., persist_directory=…)`；
   把拼出来的缺省目录经**普通 dict 字面量**喂给某个 helper 的 kwarg（如
   `extra_env={"RAG_DIR": <tmp 替身>}`）**不在 B4 判定内** —— 静态区分不了「替身目录」
   与「真 live」，判它会把这个文件自己的反证夹具判成违规。该形态由腿 A 的端到端指纹
   与 B5 的 DB_PATH 清单负责。写明边界即不保留超范围声明（审核第 6 条给的二选一，
   本波取「做闭包 + 写清入口边界」）。
   说明：测试文件里 `os.path.join(_DEMO_DIR, ".rag_chroma")` 作为**断言目标**（「本用例
   不得写入它」）是合法且必要的，B1 因此只作用于生产树；B4 判的是「同一表达式流进落点
   入口」，方向不同，不是放宽判据。
B5 全仓测试文件「模块 import 期设置 `DB_PATH`」的形态：命中清单必须**能被 T1 的派生
   分支自动兜住**。「兜不兜得住」不再由守卫自己复刻一套字符串规则 —— 取值是可读字面量
   时**直接问产品实现**（把该字面量塞进 `DB_PATH` 后调
   `rag_paths.externalized_db_path()` / `derived_rag_dir_from_db()`），于是
   「未外置」三形态（空 / 相对名 / realpath 等于 live，含**指向 live 的软链**这类只有
   产品才认的 realpath 形态）与守卫结论逐项一致；守卫只补一条产品不管的策略：
   **派生目录 realpath 落在仓库内 → uncovered**（与腿 A `_judge_store_target` 同口径）。
   取值静态展开后没有任何可读路径片段（`guard_db_path()` / `os.environ.get('DB_PATH')`
   这类不透明调用）→ 第三态 **unverifiable**：单列清单并计入报告，绝不记 covered。
   入口通道除 `os.environ[...] =` / `setdefault` 外，还覆盖 `os.environ.update({...})`、
   字典字面量喂给 `env=` / `env_overrides=`、`dict(os.environ, DB_PATH=...)` 与
   「模块期字典字面量赋值」；读环境变量（`os.environ.get("RAG_DIR")`）**不**算写入。
   并顺带扫子进程 env 通道的同款形态（`_playwright_server.py` 起活体服务那条）。

自校准（否则扫描器失效时判据恒绿）
----------------------------------
腿 A、腿 B 各有 `/tmp` 假目标用例：故意写坏的假实现必须被抓到、干净实现必须零命中，
且「换一个不存在的输入」命中数必须为 0。假目标清单（`test_leg_b_scanners_catch_planted_fakes`
的 A~I + `test_leg_b_b4_tracks_transitive_alias` 的四段）逐条对应本波改动的判据：
  - A 复刻缺省目录 / 复刻优先级链 / `os.environ[...] =`、`.update({...})`、`.setdefault(...)`
    三条通道各指回缺省目录（B1/B3/B4）；
  - C 模块期 live 相对名与空值（B5 uncovered）；D 临时库 + 同函数显式设 RAG_DIR（covered）；
  - E 查不到定义的裸名字、G `guard_db_path()` / `os.environ.get('DB_PATH')` / `cfg.db_path`
    三类不透明取值（**必须 unverifiable，不得记 covered**）；
  - F `os.environ.update({...})` / `env_overrides={...}` / `dict(os.environ, DB_PATH=...)`
    三条新通道；H 只读 `os.environ.get("RAG_DIR")` 不得点亮「已显式设」；
  - I `DB_PATH` 落在假仓库内（**必须 uncovered**）；
  - 两跳 / 三跳别名 + 干净临时目录对照（B4 传递闭包）。
腿 A 另加三条：
  - `/tmp` 上的 rag_paths **突变体**（删掉派生分支）→ 判据必须转红（钉住对 T1 的依赖）；
  - 同一突变体上「只看函数名」的旧判据仍为真、而本文件的**接线判据**必须转红
    （审核第 4 条：派生分支的存在性要靠「函数体内真的调用了
    `derived_rag_dir_from_db(`」来判，不是靠函数名在册）；
  - `RAG_DIR` 指到 `/tmp` 替身目录 + **真实 Chroma 落盘** → 证明「解析结果 == 字节落点」，
    且落点等于（替身）生产目录时判据必报警 —— 真 live 被写时本守卫一定转红。
指纹判据本身也在 live 目录的 /tmp 副本上校准 —— 前作 P29 踩过「为了校准指纹而真往
live 写临时文件」的坑（计划 09-22 §10 记录），本文件全程对 live 只读。DB 侧车同样在
副本上校准：只改 `-wal` 字节、主文件 md5 不变时，三元组必须翻红（否则判据 4 对 WAL 库
是恒绿的）；并在 live 的 /tmp 副本上实测「只拷主文件打不开 / 齐拷才有行」这条 §9 伪影。

本文件**不**证明的事（写清范围，免得后波误读）
--------------------------------------------
`monkeypatch.setenv("DB_PATH", ...)` 这类 **per-test** 通道不在 B5 的判定范围里 —— 它晚
于 conftest 的 autouse `_isolate_rag_dir`，不是 G1 的事故窗口；「autouse 隔离夹具对模块
import 期就建引擎的文件同样生效」需要 conftest 侧的证据用例，归 F-B2 波（依赖登记在
交付报告的 still_open，不由本文件代证）。

红线遵守方式
------------
不 import `app.db` / `app.services.rag`（本文件自身只 import stdlib + 按路径加载的
`rag_paths` 副本）；需要产品模块的形态一律走子进程并显式传 `DB_PATH` 指向 tmp。
B5 的「问产品实现」也走**按路径加载的 rag_paths 副本**（该模块只 import os，加载零
副作用），并在 try/finally 里临时改写 + 还原 `os.environ["DB_PATH"]`；`/tmp` 假目标树
用的是同一套加载手法（把副本放进假 `app/services/` 下，令 `app_dir()` 指向假 demo 根），
所以「假树里的 live」与「仓库里的 live」由产品自己的定位规则决定，守卫不另算一份。
`demo/.rag_chroma` 与 `demo/receipt_demo.db`（含 `-wal` / `-shm`）只读：指纹 = 遍历 +
读 md5，全程不写、不删、不建集合。
"""

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")

# 生产向量目录：`<repo>/.rag_chroma` 目前不存在，但它是 R7 之前的历史落点，
# 缺省口径一旦再随 cwd 漂移就会原地复活，所以一并列为禁止目标（口径同
# tests/test_rag_embedding_wiring.py 与 tests/test_memory_vector_consistency.py）。
_LIVE_RAG_DIRS = (
    os.path.join(_DEMO_DIR, ".rag_chroma"),
    os.path.join(_REPO_ROOT, ".rag_chroma"),
)
_LIVE_DB = os.path.join(_DEMO_DIR, "receipt_demo.db")
_CHROMA_DB_FILE = "chroma.sqlite3"

# 扫描根（腿 B）
_PRODUCTION_ROOTS = (
    os.path.join(_DEMO_DIR, "app"),
    os.path.join(_DEMO_DIR, "scripts"),
    os.path.join(_REPO_ROOT, "scripts"),
)
_TEST_ROOTS = (_TESTS_DIR, os.path.join(_DEMO_DIR, "tests"))
_ALL_ROOTS = _PRODUCTION_ROOTS + _TEST_ROOTS

# 唯一实现模块（B1 的豁免对象）；豁免判据是「该文件定义了 resolve_rag_dir」，
# 而不是硬编码文件名 —— B2 再钉住「这样的文件全仓只能有一个」。
_RAG_PATHS_REPO = os.path.join(_DEMO_DIR, "app", "services", "rag_paths.py")

# 「自行拼接默认向量目录」的字符串字面量形态（AST 常量，不含注释/文档字符串）
def _is_default_rag_dir_literal(value):
    """.rag_chroma / ./.rag_chroma / <目录>/.rag_chroma —— 即缺省向量目录的拼接片段。"""
    if not isinstance(value, str):
        return False
    return (value == ".rag_chroma" or value == "./.rag_chroma"
            or value == "/.rag_chroma" or value.endswith("/.rag_chroma"))


# 静态判据里代表「已外置到临时位置」的标记（腿 B B5 只用于**非字面量**取值的保守判定；
# 字面量取值一律问产品实现，见 judge_db_path_value）。`tmpdir` 是 pytest 内置夹具名，
# 本仓多个脚本用它当临时目录（实测 backfill_vendor_sku_alias.py:459 等 3 处），
# 早期清单缺它导致这些取值退化到「不可判」。
_TMP_MARKERS = ("tempfile", "mkdtemp", "tmp_path", "tmp_path_factory", "tmpdir",
                "_TMP", "_tmpdir", "sandbox", "TemporaryDirectory",
                "/tmp", "/private/tmp", "/var/folders")
# 匹配一律走小写集合：`_SANDBOX`（demo/tests/test_conftest_rag_isolation.py 的模块期
# 临时目录名）与 `sandbox` 必须是同一档证据，否则同一个形态换个大小写就退化成 unverifiable。
_TMP_MARKER_SET = tuple(sorted({m.lower() for m in _TMP_MARKERS}))

_ENV_TARGET_KEYS = ("RAG_DIR", "CHROMA_PATH")
_DB_ENV_KEY = "DB_PATH"
# B5 认的全部 env 写入键（DB 侧 + 向量侧）：向量侧的写入用于判「同作用域是否已显式隔离」。
_ENV_WRITE_KEYS = (_DB_ENV_KEY,) + _ENV_TARGET_KEYS


# =====================================================================
# 通用工具：只读指纹 / md5 / Python 文件枚举 / AST 辅助
# =====================================================================
def _md5(path):
    if not os.path.isfile(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_fingerprint(root):
    """目录指纹三元组（计划 §7.3 的口径）：相对路径集合 + 总字节数 + chroma.sqlite3 md5。

    why 三元组而不是 mtime/size：只读打开会让 mtime 抖动（本仓实测过假阳性），而
    Chroma 的 sqlite 页写入可能不改主库文件大小 —— 只有内容哈希抓得到。
    why 不复用 tests/test_memory_vector_consistency.py 的同名辅助函数：那个模块在
    import 期就改 `os.environ["DB_PATH"]`/`RAG_DIR`，按文件路径 import 它会把它的
    环境副作用带进本用例；这里是**测试辅助**（产品解析口径仍只有 rag_paths 一份）。
    """
    if not os.path.isdir(root):
        return None
    files = []
    total = 0
    for dirpath, _dirs, filenames in os.walk(root):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            files.append(os.path.relpath(fp, root))
            total += os.path.getsize(fp)
    return {"files": sorted(files), "total_bytes": total,
            "chroma_sqlite3_md5": _md5(os.path.join(root, _CHROMA_DB_FILE))}


def _db_fingerprint(db_path):
    """WAL 库的三元组指纹：主文件 / `-wal` / `-shm` 三个 md5（缺文件为 None）。

    why（审核第 8 条）：`demo/receipt_demo.db` 实测 `journal_mode=wal`。只取主文件时，
    「写了一批事务但尚未 checkpoint」与「一个字节都没动」的主文件 md5 可以相同 ——
    判据 4 对 WAL 库就是恒绿的。三件一起进指纹，任何一次真实写入（含 sqlite 打开 WAL
    库时对 `-shm`/`-wal` 的更新）都必被抓到。
    """
    return {"main": _md5(db_path),
            "wal": _md5(db_path + "-wal"),
            "shm": _md5(db_path + "-shm")}


def _py_files(roots):
    """枚举扫描根下的 .py（跳过 __pycache__ / 依赖目录）；不存在的根静默跳过。"""
    out = []
    for base in roots:
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", ".venv", "node_modules", ".git")]
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _parse(path):
    """返回 (tree, source) 或 (None, 错误说明)。"""
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        return ast.parse(src, filename=path), src
    except (SyntaxError, UnicodeDecodeError, OSError) as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _unparse(node):
    """AST 节点回写成源码（Python 3.9 起可用）；失败返回空串。"""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _docstring_constant_ids(tree):
    """模块 / 类 / 函数文档字符串对应的 Constant 节点 id 集合。

    why 要排除：本仓大量注释与文档字符串为了说明历史会引用旧表达式
    （如 `os.environ.get("RAG_DIR", "./.rag_chroma")`），把它们当命中就是误报；
    只有**可执行**字符串常量才是「又写了一份口径」。
    """
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                val = body[0].value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    ids.add(id(val))
    return ids


def _rel(path):
    return os.path.relpath(path, _REPO_ROOT)


# =====================================================================
# 腿 A 判据：store 落点必须既不是 live、又在 DB 同目录、又不在仓库内
# =====================================================================
def _judge_store_target(resolved, db_path, live_dirs=_LIVE_RAG_DIRS):
    """返回违规说明列表（空 = 通过）。纯函数，便于用 /tmp 假目标校准。

    三条判据的分工：
      - 「不等于 live」直接钉住本 Task 的目标（不写生产目录）；
      - 「在 DB 同目录」钉住 T1 的派生契约（DB↔向量同源，否则巡检继续报孤儿）；
      - 「不在仓库内」钉住派生分支的副作用（DB 若写在仓库内，派生目录也会落在仓库内）。
    """
    violations = []
    if not resolved:
        return ["解析结果为空 —— 无法判定向量落点"]
    if not os.path.isabs(resolved):
        violations.append("解析结果不是绝对路径（随 cwd 变即 R7 复发）: %r" % resolved)
    abs_resolved = os.path.abspath(resolved)
    for live in live_dirs:
        if os.path.realpath(abs_resolved) == os.path.realpath(live):
            violations.append("向量落点等于生产向量目录: %s" % live)
    if db_path:
        if os.path.dirname(abs_resolved) != os.path.dirname(os.path.abspath(db_path)):
            violations.append("向量落点未与 DB 同目录（DB↔向量必须同源，否则巡检报孤儿）: "
                              "resolved=%r db_dir=%r"
                              % (abs_resolved, os.path.dirname(os.path.abspath(db_path))))
        if abs_resolved == os.path.abspath(db_path):
            violations.append("向量落点等于 DB 文件本身: %s" % abs_resolved)
    inside_repo = os.path.realpath(abs_resolved) == _REPO_ROOT or \
        os.path.realpath(abs_resolved).startswith(_REPO_ROOT + os.sep)
    if inside_repo:
        violations.append("向量落点落在仓库内（派生分支把污染通道从 live 换成了仓库）: %s"
                          % abs_resolved)
    return violations


# =====================================================================
# 腿 B 扫描器（root 参数化：既扫仓库，也用于 /tmp 假目标校准）
# =====================================================================
def _files_defining_resolve(roots):
    """返回定义了 `resolve_rag_dir` 的文件（B1 豁免对象，同时供 B2 计数）。"""
    out = []
    for path in _py_files(roots):
        tree, _src = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name == "resolve_rag_dir":
                out.append(path)
                break
    return out


def scan_default_rag_dir_assembly(roots, exempt_files=()):
    """B1：可执行代码里自行拼接缺省向量目录的位置。

    豁免 = 定义唯一实现的文件（`_files_defining_resolve`）；其余任何拼接缺省目录的文件
    都是第二份解析口径。返回 [{"file","lineno","literal"}]。
    """
    exempt = set(os.path.realpath(p) for p in exempt_files)
    hits = []
    for path in _py_files(roots):
        tree, _src = _parse(path)
        if tree is None:
            continue
        if os.path.realpath(path) in exempt:
            continue
        docs = _docstring_constant_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and _is_default_rag_dir_literal(node.value) \
                    and id(node) not in docs:
                hits.append({"file": _rel(path), "lineno": getattr(node, "lineno", 0),
                             "literal": node.value})
    return sorted(hits, key=lambda h: (h["file"], h["lineno"]))


def scan_priority_chain_fallbacks(roots):
    """B3：`os.environ.get("RAG_DIR"/"CHROMA_PATH", <缺省>)` 形态 = 复刻优先级链。

    只读回原值以便还原（`os.environ.get("RAG_DIR")` 单参、`saved = {...}` 字典推导）
    不算复刻 —— 判据是「读环境变量时又带了一份缺省落点」，那正是旧缺陷形态。
    """
    hits = []
    for path in _py_files(roots):
        tree, _src = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("get", "getenv") or len(node.args) < 2:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value in _ENV_TARGET_KEYS:
                try:
                    default_src = ast.unparse(node.args[1])
                except Exception:
                    default_src = "?"
                hits.append({"file": _rel(path), "lineno": node.lineno,
                             "expr": "%s(%r, %s)" % (node.func.attr, first.value,
                                                     default_src)})
    return sorted(hits, key=lambda h: (h["file"], h["lineno"]))


def _name_bindings_with_default_dir_literal(tree):
    """文件内「值里含缺省向量目录字面量」的名字集合（**传递闭包**，用于 B4）。

    why 闭包（审核第 6 条）：只追一跳时，
        `_base = os.path.join(os.getcwd(), ".rag_chroma")`
        `_deep = _base + "/nested"`
        `os.environ["RAG_DIR"] = _deep`
    这种两跳别名对 B4 零命中（实测：一跳 1 命中、两跳 0 命中），等于「指回 live 即红」
    这条声明超出实现范围。改判据为不动点迭代：先标记「值里直接含缺省目录字面量」的名字，
    再把「值里引用了已标记名字」的目标名字并入，直到集合不再增长。
    边界（写清可实现范围，不留超范围声明）：闭包只沿**赋值**扩张；跨函数返回值、
    运行期拼接（`_x = dir_of_call()`）不在静态能力内，那类形态由腿 A 的端到端指纹兜。
    """
    pairs = []
    for node in ast.walk(tree):
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if value is None:
            continue
        try:
            src = ast.unparse(value)
        except Exception:
            src = ""
        seed = (any(_is_default_rag_dir_literal(c.value) for c in ast.walk(value)
                    if isinstance(c, ast.Constant))
                or src.endswith(".rag_chroma"))
        pairs.append((tuple(t.id for t in targets if isinstance(t, ast.Name)), value, seed))

    names = set(t for ts, _v, seed in pairs for t in ts if seed)
    while True:
        grown = set(names)
        for targets, value, _seed in pairs:
            if not targets or any(t in names for t in targets):
                continue
            if any(isinstance(c, ast.Name) and c.id in names for c in ast.walk(value)):
                grown.update(targets)
        if grown == names:
            return names
        names = grown


def scan_store_target_pointed_at_live(roots):
    """B4：把向量落点入口指向「自行拼接的缺省目录」。

    落点入口（写进**进程 env** 的那一类 + 直接构造 store 的那一类）：
      `os.environ["RAG_DIR"|"CHROMA_PATH"] = <含缺省目录拼接>`、
      `os.environ.setdefault(K, <同上>)`、`os.environ.update({K: <同上>})`（新，与 B5
      的通道清单对齐）、`Chroma(..., persist_directory=<同上>)`、`PersistentClient(<同款>)`。
    取值侧用 `_name_bindings_with_default_dir_literal` 的**传递闭包**，所以
    `_base → _deep → os.environ["RAG_DIR"] = _deep` 这种多跳别名逃不掉（审核第 6 条）。
    范围声明：把「拼出来的缺省目录」经**普通 dict 字面量**喂给某个 helper 的 kwarg
    （如 `extra_env={"RAG_DIR": <tmp 替身>}`）**不在 B4 判定内** —— 静态上无法区分
    「替身目录」与「真 live」，判它会把腿 A 自己的反证夹具判成违规；该形态的落点结论由
    腿 A 的端到端指纹与 B5 的 DB_PATH 清单负责。这一条是可实现范围，不是放宽判据。
    这一条对生产树与测试树同样生效（测试文件把隔离反向指回 live 是最直接的自杀形态）。
    """
    hits = []
    for path in _py_files(roots):
        tree, _src = _parse(path)
        if tree is None:
            continue
        bad_names = _name_bindings_with_default_dir_literal(tree)

        def _tainted(expr_node):
            try:
                src = ast.unparse(expr_node)
            except Exception:
                src = "?"
            return src, any(
                _is_default_rag_dir_literal(c.value) for c in ast.walk(expr_node)
                if isinstance(c, ast.Constant)) \
                or any(isinstance(c, ast.Name) and c.id in bad_names
                       for c in ast.walk(expr_node)) \
                or _is_default_rag_dir_literal(src)

        def _flag(lineno, expr_node, why):
            src, tainted = _tainted(expr_node)
            if tainted:
                hits.append({"file": _rel(path), "lineno": lineno,
                             "why": "%s -> %s" % (why, src)})

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute) \
                            and t.value.attr == "environ" \
                            and isinstance(t.slice, ast.Constant) \
                            and t.slice.value in _ENV_TARGET_KEYS:
                        _flag(node.lineno, node.value, "env[%r]" % t.slice.value)
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            # 新补的两条进程 env 通道（与 B5 通道清单对齐）
            if fname in ("update",) and len(node.args) == 1 \
                    and _targets_os_environ(node):
                for key, v in _dict_env_entries(node.args[0]):
                    if key in _ENV_TARGET_KEYS:
                        _flag(node.lineno, v, "environ.update()[%r]" % key)
            if fname == "setdefault" and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value in _ENV_TARGET_KEYS and len(node.args) > 1:
                _flag(node.lineno, node.args[1], "environ.setdefault(%r,...)"
                      % node.args[0].value)
            # mock.patch.dict(os.environ, {...}) / dict(os.environ, RAG_DIR=...)
            for arg in node.args:
                for key, v in _dict_env_entries(arg):
                    if key in _ENV_TARGET_KEYS and _targets_os_environ(node):
                        _flag(node.lineno, v, "%s(...)[%r]" % (fname, key))
            if fname in ("Chroma", "PersistentClient"):
                for kw in node.keywords:
                    if kw.arg in ("persist_directory", "path", "settings"):
                        _flag(node.lineno, kw.value, "%s(%s=...)" % (fname, kw.arg))
            for kw in node.keywords:
                if kw.arg == "persist_directory":
                    _flag(node.lineno, kw.value, "persist_directory=")
    return sorted(hits, key=lambda h: (h["file"], h["lineno"]))


def _targets_os_environ(call_node):
    """该调用的接收者或首个实参是否明确指向 `os.environ`（B4 只判「写进程 env」这一类）。"""
    func = call_node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute) \
            and func.value.attr == "environ":
        return True                      # os.environ.update(...)
    return any(isinstance(a, ast.Attribute) and a.attr == "environ"
               for a in call_node.args)  # mock.patch.dict(os.environ, {...})


def _merge_bindings(tree, func):
    """模块别名表 + （若有）函数内别名表，用于把 `path`/`_TMP` 这类名字展开成可读原式。"""
    out = {}
    for st in tree.body:
        if isinstance(st, ast.Assign):
            try:
                src = ast.unparse(st.value)
            except Exception:
                continue
            for t in st.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = src
    if func is None:
        return out
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            try:
                src = ast.unparse(node.value)
            except Exception:
                continue
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, src)
    return out


def _expand_value(node, bindings, hops=0):
    """把取值**节点**归一成「静态可读原式」，返回 (原式, 展开档位)。

    档位三态（审核第 3 条：不得把「不可判」当「已证」）：
      "literal"  展开到**字符串字面量**（可原样喂给产品实现去问结论）；
      "readable" 展开到带可读路径片段的表达式（`os.path.join(_TMP, 'x.db')` 这类拼接），
                 守卫只能做保守判定，但表达式里至少有一个路径/临时目录证据；
      "opaque"   展开到底仍是**不透明取值**：查不到定义的裸名字、下标（`sandbox['db_path']`）、
                 属性（`ctx.db_path`）、以及**没有任何字符串常量的函数调用**
                 （`guard_db_path()` / `os.environ.get('DB_PATH')` / `_fresh_db()`）。
    why 按节点而不是按文本：`f'...{uuid.uuid4().hex[:8]}.db'` 里就有 `[8]`，用文本猜
    「是不是字典下标」会把大量合法取值误判成不可判（本仓实测踩过）。
    """
    if node is None:
        return "", "opaque"
    if isinstance(node, ast.Name):
        src = bindings.get(node.id)
        if src is None or hops >= 4:
            return node.id, "opaque"
        try:
            sub = ast.parse(src, mode="eval").body
        except SyntaxError:
            return src, "opaque"
        return _expand_value(sub, bindings, hops + 1)
    if isinstance(node, (ast.Subscript, ast.Attribute)):
        return _unparse(node), "opaque"
    text = _unparse(node)
    if not text:
        return "", "opaque"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return text, "literal"
    if _expression_has_path_hint(node):
        return text, "readable"
    return text, "opaque"


def _expression_has_path_hint(node):
    """表达式里是否存在「静态可读的路径证据」：路径形态的字符串常量或临时目录标记。

    env 键名本身（`os.environ.get('DB_PATH')` 里的 'DB_PATH'）不是路径证据 —— 那正是
    上一轮审核说的「covered 里有一批根本没有可读路径」。本波实测的精确数：旧实现把
    5 条零路径证据的取值记成 covered（import_sku_alias_approvals.py:331 的
    `guard_db_path()`、memory_extract_replay.py:129 与 test_experiment_guardian.py:66 的
    `os.environ.get('DB_PATH')`、test_resolve_cache_single_impl.py:212/243 的 `_fresh_db()`）；
    审核给的 10 条里另外 5 条要么同作用域已显式设 RAG_DIR/CHROMA_PATH（独立保证，仍算
    covered），要么根本在旧扫描漏掉的 env 通道上（本波补齐后才进清单）。
    """
    for c in ast.walk(node):
        if isinstance(c, ast.Constant) and isinstance(c.value, str):
            v = c.value
            if v in _ENV_WRITE_KEYS:
                continue
            if "/" in v or "\\" in v or v.endswith(".db") or v.endswith(".sqlite"):
                return True
        if isinstance(c, ast.Name) and c.id.lower() in _TMP_MARKER_SET:
            return True
    low = _unparse(node).lower()
    return any(m in low for m in _TMP_MARKER_SET)


def _has_tmp_marker(text):
    """原式里是否出现临时目录标记（大小写不敏感：`_SANDBOX` 与 `sandbox` 同一档）。"""
    low = (text or "").lower()
    return [m for m in _TMP_MARKER_SET if m in low]


def _concrete_literal_path(text):
    """取值原式是「引号字面量」时返回其字面内容（可直接当运行期 DB_PATH），否则 None。"""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return None


def _product_resolver(path=None):
    """按文件路径加载唯一实现 `rag_paths`（只 import os，加载零副作用），带缓存。

    默认加载仓库真身；`/tmp` 假目标用例把副本放进假 `app/services/` 下再传 path，于是
    产品自己的 `app_dir()` / `default_db_path()` 指向假 demo 根 —— 「未外置三形态」由
    产品定位规则决定，守卫不复刻（审核第 2 条）。
    """
    path = path or _RAG_PATHS_REPO
    key = os.path.realpath(path)
    mod = _PRODUCT_CACHE.get(key)
    if mod is None:
        mod = _load_rag_paths_copy(path, "guard_rag_paths_%s" % hashlib.md5(
            key.encode("utf-8")).hexdigest()[:8])
        _PRODUCT_CACHE[key] = mod
    return mod


_PRODUCT_CACHE = {}


def judge_db_path_value(value_src, repo_root=_REPO_ROOT, product=None):
    """B5 判据：这个 `DB_PATH` 取值能否被 T1 的派生分支自动兜住。

    「兜不兜得住」三态：covered / uncovered / unverifiable。

    1. 取值是**可读字面量** → 问产品实现：把它塞进 `DB_PATH` 后调
       `rag_paths.externalized_db_path()` 与 `derived_rag_dir_from_db()`。于是
       空值 / 全空白 / 相对名 / 显式 live 绝对路径 / **指向 live 库的软链**（只有产品
       的 realpath 比较认得）全部与产品逐项一致，守卫不再自己复刻一套字符串规则
       （审核第 2 条）。
    2. 产品判「已外置」，但派生目录 realpath 落在仓库内 → **uncovered**（审核第 7 条：
       派生分支把污染通道从 live 换成仓库内同样算违规，与腿 A `_judge_store_target`
       的「不在仓库内」同口径；产品自己不管这一条，是守卫的合法附加策略）。
    3. 取值是拼接式表达式（非字面量）：有临时目录标记 → covered（保守但可核对）；
       展开后**没有任何路径证据** → unverifiable，不得记 covered（审核第 3 条）。

    `repo_root` / `product` 参数化是为了让 `/tmp` 假目标树用**同一份**判据校准：把
    rag_paths 副本放进假树即可，「什么算 live」由产品的 `default_db_path()` 决定，
    守卫里没有第二个 live 常量参与判定（`_LIVE_DB` 只服务腿 A 的指纹）。
    """
    text = (value_src or "").strip()
    if not text:
        return "unverifiable", "取值无法静态展开（下标/属性/查不到定义的名字）"
    concrete = _concrete_literal_path(text)
    if concrete is not None:
        return _judge_concrete_via_product(concrete, repo_root, product or _product_resolver())
    if not _expression_has_path_hint_from_text(text):
        return "unverifiable", (
            "取值展开后没有任何可读路径片段（不透明函数调用/未知名字）→ 不得记 covered：%s"
            % text)
    markers = _has_tmp_marker(text)
    if markers:
        return "covered", "静态可读为临时目录库（含 %s 标记）→ 派生分支必触发" % markers[0]
    return "unverifiable", "取值可读但既无临时目录标记也不是字面量，无法证明外置：%s" % text


def _expression_has_path_hint_from_text(text):
    """`_expression_has_path_hint` 的文本入口（judge 只拿到展开后的原式）。"""
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        return False
    return _expression_has_path_hint(node)


def _judge_concrete_via_product(concrete, repo_root, product):
    """把字面量塞进 DB_PATH，用产品自己的 `externalized_db_path()` / 派生函数下结论。

    临时改写的只有 `DB_PATH` 一个键，并在 finally 里精确还原（本文件其余部分对环境的
    读取都在子进程里，不受这里影响）。
    """
    saved = os.environ.get("DB_PATH")
    try:
        os.environ["DB_PATH"] = concrete
        externalized = product.externalized_db_path()
        derived = product.derived_rag_dir_from_db()
    finally:
        if saved is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = saved
    if not externalized:
        return "uncovered", (
            "产品实现 rag_paths.externalized_db_path() 判定未外置（空 / 相对名 / realpath "
            "等于 live 库三形态之一，软链同样按 realpath 判）→ 派生分支不触发，"
            "向量落回缺省的生产目录：DB_PATH=%r" % concrete)
    if _path_inside(realpath_of(repo_root), realpath_of(derived)):
        return "uncovered", (
            "产品判定已外置，但派生向量目录 realpath 落在仓库内（把污染通道从 live 换成了"
            "仓库内，与腿 A「不在仓库内」同口径）：DB_PATH=%r → derived=%r"
            % (concrete, derived))
    return "covered", "产品判定已外置且派生目录在仓库外：%s" % derived


def realpath_of(path):
    """realpath，空值原样返回（保持产品「未外置＝空串」的语义）。"""
    return os.path.realpath(path) if path else path


def _path_inside(root, candidate):
    """candidate 是否等于或位于 root 之下（比较前先 realpath，root 为空则 False）。"""
    if not root or not candidate:
        return False
    return candidate == root or candidate.startswith(root + os.sep)


def _dict_env_entries(node):
    """字典字面量里键名属于 `_ENV_WRITE_KEYS` 的 (key, value_node) 项。"""
    if not isinstance(node, ast.Dict):
        return
    for k, v in zip(node.keys, node.values):
        if v is not None and isinstance(k, ast.Constant) and k.value in _ENV_WRITE_KEYS:
            yield k.value, v


def _walk_no_nested_def(node):
    """前序遍历，但**不进入**函数/类/lambda 体（模块期判定必须只看得见顶层语句）。

    why：`for st in tree.body: ast.walk(st)` 会钻进 `def f(): os.environ["DB_PATH"]=...`，
    于是函数体内的写入被记成「模块 import 期」形态（实测把 44 条 env 通道误升为 module，
    直接冲淡了 B5 的主判据）。模块期与函数期是两个不同的事故窗口，不能混。
    注意递归而不是「跳过当前再压子节点」—— 后者对 root 本身就是 `def` 的顶层语句会把
    函数体当成模块期展开（第一版就踩了这个）。
    """
    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
        return
    for child in ast.iter_child_nodes(node):
        for sub in _walk_no_nested_def(child):
            yield sub


def _iter_env_writes(container, shallow=False):
    """某作用域内**全部**已知的 env 写入通道：产出 (lineno, key, value_node, channel)。

    通道清单（审核第 5 条：每加一条都有对应的判别式假目标用例，见
    `test_leg_b_scanners_catch_planted_fakes` 的 F/G/H/I 四个新假目标）：
      env-subscript  `os.environ["DB_PATH"] = v`                    （历史）
      dict-subscript `env["DB_PATH"] = v`                           （历史，函数体内已认）
      setdefault     `<dict>.setdefault("DB_PATH", v)`              （历史）
      update         `os.environ.update({...})` / `env.update({...})`（新）
      call-kw        `dict(os.environ, DB_PATH=v)`                   （新）
      call-dictarg   `f(env={"DB_PATH": v})` / `f(env_overrides={...})` /
                     `mock.patch.dict(os.environ, {...})`             （新）
      dict-literal   `env = {"DB_PATH": v}`（随后喂给 env= 的构造点）  （新）
    **只读不算写**：`os.environ.get("RAG_DIR")` 早期被「args[0] 是 env 键名的 Call」误认
    成写入，于是「同函数已显式设 RAG_DIR」会被一次读操作点亮 —— 本函数只认真正的写入。
    `shallow=True` 时不进入嵌套的 def/class（模块期判定用）。
    """
    out = []
    roots = container if isinstance(container, (list, tuple)) else [container]
    for root in roots:
        iterator = _walk_no_nested_def(root) if shallow else ast.walk(root)
        for node in iterator:
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                value = node.value
                targets = getattr(node, "targets", None) or [node.target]
                for t in targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                            and isinstance(t.slice.value, str) \
                            and t.slice.value in _ENV_WRITE_KEYS:
                        is_environ = isinstance(t.value, ast.Attribute) \
                            and t.value.attr == "environ"
                        out.append((node.lineno, t.slice.value, value,
                                    "env-subscript" if is_environ else "dict-subscript"))
                out.extend((node.lineno, k, v, "dict-literal")
                           for k, v in _dict_env_entries(value))
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if fname == "setdefault" and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value in _ENV_WRITE_KEYS and len(node.args) > 1:
                out.append((node.lineno, node.args[0].value, node.args[1], "setdefault"))
            if fname == "update" and len(node.args) == 1:
                out.extend((node.lineno, k, v, "update")
                           for k, v in _dict_env_entries(node.args[0]))
            for kw in node.keywords:
                if kw.arg in _ENV_WRITE_KEYS:
                    out.append((node.lineno, kw.arg, kw.value, "call-kw"))
                out.extend((node.lineno, k, v, "call-dictarg")
                           for k, v in _dict_env_entries(kw.value))
            for a in node.args:
                out.extend((node.lineno, k, v, "call-dictarg")
                           for k, v in _dict_env_entries(a))
    dedup = {}
    for lineno, key, value_node, channel in out:
        dedup.setdefault((lineno, key), (lineno, key, value_node, channel))
    return sorted(dedup.values(), key=lambda x: (x[0], x[1]))


def scan_db_path_entry_points(roots, repo_root=_REPO_ROOT, product=None):
    """腿 B B5：扫「模块 import 期设 DB_PATH」与「函数内 env 设 DB_PATH」两种形态。

    返回 (records, parse_errors)。records 元素：
      {file, lineno, scope, channel, value, status, path_evidence, reason}
        scope = "module"  模块顶层语句（T3 主目标：早于任何 autouse 夹具）
        scope = "env-dict(...)"  函数内的 env 写入（起活体服务/子进程的通道）；同函数若
                                 也写 RAG_DIR/CHROMA_PATH 则标为「显式设」，不依赖派生分支
        status ∈ {"covered","uncovered","unverifiable"}（见 judge_db_path_value）
        path_evidence = 取值是否静态可读（False 即「本条判据没看过任何真实路径」，
                        审核第 3 条要求的可核对字段）
    """
    records = []
    parse_errors = []
    product = product or _product_resolver()

    def _record(path, lineno, scope, value_node, bindings, explicit, channel):
        value, grade = _expand_value(value_node, bindings)
        readable = grade in ("literal", "readable")
        if explicit:
            status, reason = "covered", "同作用域已显式设 %s（不依赖派生分支）" % ",".join(explicit)
        else:
            status, reason = judge_db_path_value(
                value if readable else "", repo_root=repo_root, product=product)
        records.append({"file": _rel(path), "lineno": lineno, "scope": scope,
                        "channel": channel, "value": value, "status": status,
                        "explicit": list(explicit),
                        "path_evidence": readable, "reason": reason})

    for path in _py_files(roots):
        tree, err = _parse(path)
        if tree is None:
            parse_errors.append("%s: %s" % (_rel(path), err))
            continue

        # (1) 模块顶层的 env 写入：早于任何 autouse 夹具，是 G1 的主窗口。
        #     shallow=True —— 钻进函数体就会把「函数期通道」冒充「import 期通道」。
        module_bindings = _merge_bindings(tree, None)
        for st in tree.body:
            for lineno, key, value_node, channel in _iter_env_writes([st], shallow=True):
                if key == _DB_ENV_KEY:
                    _record(path, lineno, "module", value_node, module_bindings, [], channel)

        # (2) 函数内的 env 写入（子进程通道；也是「同文件不设 RAG_DIR」的实际形态）
        for func in [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            func_bindings = _merge_bindings(tree, func)
            writes = _iter_env_writes(func)
            keys = set(k for _l, k, _v, _c in writes)
            if _DB_ENV_KEY not in keys:
                continue
            explicit = sorted(k for k in keys if k in _ENV_TARGET_KEYS)
            scope = "env-dict(显式设 %s)" % ",".join(explicit) if explicit \
                else "env-dict(仅 DB_PATH，靠派生分支)"
            for lineno, key, value_node, channel in writes:
                if key != _DB_ENV_KEY:
                    continue
                _record(path, lineno, scope, value_node, func_bindings, explicit, channel)
    return sorted(records, key=lambda r: (r["file"], r["lineno"])), parse_errors


# =====================================================================
# 腿 A：子进程探针（真 import + 真解析，Chroma 构造换成记录器）
# =====================================================================
_STORE_PROBE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import app.services.rag as rag
from app.services import rag_paths

captured = []
class _RecordingChroma:
    def __init__(self, **kwargs):
        captured.append({k: str(v) for k, v in kwargs.items() if k != 'embedding_function'})
    def add_texts(self, *a, **k):
        raise AssertionError('探针不得真的写向量')

rag._vs = None
rag._vs_tenant = {}

MODE = sys.argv[2] if len(sys.argv) > 2 else 'stub'
if MODE == 'stub':
    rag.Chroma = _RecordingChroma

rag._store()                      # 全局桶（default 租户）
rag._store(tenant_id='t3_probe')  # 租户桶（夹具向量当年就是落在这里）

if MODE == 'stub':
    persist_dirs = [c.get('persist_directory') for c in captured]
    store_paths_exist = [os.path.isdir(d or '') for d in persist_dirs]
    chroma_files = [os.path.exists(os.path.join(d or '', 'chroma.sqlite3'))
                    for d in persist_dirs]
else:
    # real 模式：真实 Chroma 打开解析出的目录，用来证明「解析结果 == 字节落点」，
    # 该模式下调用方必须把 RAG_DIR 指到 /tmp 替身目录（绝不指 live）。
    d = str(rag.PERSIST_DIR)
    persist_dirs = [d, d]
    store_paths_exist = [os.path.isdir(d)]
    chroma_files = [os.path.exists(os.path.join(d, 'chroma.sqlite3'))]

out = {
    'mode': MODE,
    'persist_dirs': persist_dirs,
    'collection_names': [c.get('collection_name') for c in captured] or
                        [rag._tenant_collection_name(None), rag._tenant_collection_name('t3_probe')],
    'rag_persist_dir': str(rag.PERSIST_DIR),
    'resolved_now': rag_paths.resolve_rag_dir(),
    'env': {k: os.environ.get(k) for k in ('DB_PATH', 'RAG_DIR', 'CHROMA_PATH')},
    'store_paths_exist': store_paths_exist,
    'chroma_files': chroma_files,
}
print(json.dumps(out))
"""


def _clean_env(db_path, extra=None):
    """构造子进程环境：三个落盘环境变量一律清空后按需注入（红线 1）。"""
    env = {k: v for k, v in os.environ.items()
           if k not in ("DB_PATH", "RAG_DIR", "CHROMA_PATH")}
    env["RAG_EMBEDDING_PROVIDER"] = "hash"   # 离线档，同 conftest；绝不出网
    env["DB_PATH"] = db_path
    if extra:
        env.update(extra)
    return env


def _run_store_probe(db_path, mode, extra_env=None):
    """在子进程里真 import rag 并走 `_store()`；mode='stub' 不启动真实 Chroma。"""
    proc = subprocess.run(
        [sys.executable, "-c", _STORE_PROBE, _DEMO_DIR, mode],
        cwd=_REPO_ROOT,               # 从仓库根跑：R7 的老事故形态所在
        env=_clean_env(db_path, extra_env),
        capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, "探针子进程失败(mode=%s): %s" % (
        mode, proc.stderr[-3000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="session")
def leg_a_probe(tmp_path_factory):
    """跑一次「DB_PATH 外置 + 不设 RAG_DIR/CHROMA_PATH」的最小建 store 路径。

    指纹必须在探针前后各取一次（所以整体做成一个会话级夹具，多条断言共用同一份观测）。
    """
    work = tmp_path_factory.mktemp("leg_a")
    db_path = str(work / "t3_leg_a.db")
    before_rag = _dir_fingerprint(_LIVE_RAG_DIRS[0])
    before_repo = _dir_fingerprint(_LIVE_RAG_DIRS[1])
    before_db = _db_fingerprint(_LIVE_DB)

    payload = _run_store_probe(db_path, "stub")

    after_rag = _dir_fingerprint(_LIVE_RAG_DIRS[0])
    after_repo = _dir_fingerprint(_LIVE_RAG_DIRS[1])
    after_db = _db_fingerprint(_LIVE_DB)

    payload.update({"db_path": db_path,
                    "before_rag": before_rag, "after_rag": after_rag,
                    "before_repo": before_repo, "after_repo": after_repo,
                    "before_db": before_db, "after_db": after_db})
    return payload


def test_leg_a_store_target_is_db_sibling_not_production_dir(leg_a_probe):
    """腿 A 判据 1-3：落点既不是生产目录，也在 DB 同目录、也不在仓库内。

    依赖关系（有意的红灯）：判据 2/3 依赖 T1 的派生分支。T1 未落地时解析结果是
    `<demo>/.rag_chroma`，本用例必须转红 —— 见 `_judge_store_target` 上方的说明。
    """
    p = leg_a_probe
    resolved = p["persist_dirs"][0]
    violations = _judge_store_target(resolved, p["db_path"])
    assert violations == [], "向量落点违规：\n%s\n探针输出=%s" % (
        "\n".join(violations), json.dumps({k: p[k] for k in
                                           ("persist_dirs", "collection_names",
                                            "resolved_now", "env")}, ensure_ascii=False))


def test_leg_a_store_probe_consumes_the_single_resolver(leg_a_probe):
    """`_store()` 用的目录必须就是唯一实现解析出来的那一个（不许有第四处口径）。"""
    p = leg_a_probe
    assert p["rag_persist_dir"] == p["resolved_now"] == p["persist_dirs"][0], \
        "rag.PERSIST_DIR / rag_paths.resolve_rag_dir() / Chroma(persist_directory=...) "\
        "三者必须同源，实际=%r" % (p["rag_persist_dir"], p["resolved_now"], p["persist_dirs"])
    # 全局桶与租户桶同一目录（当年 uat_a/uat_b 正是租户桶落进 live）
    assert set(p["persist_dirs"]) == {p["persist_dirs"][0]}, \
        "租户桶与全局桶必须同目录：%s" % p["persist_dirs"]
    assert any("tenant_t3_probe_" in n for n in p["collection_names"]), \
        "探针必须真的建过租户 collection（否则本用例没走建 store 路径）：%s" % p["collection_names"]


def test_leg_a_no_real_chroma_write_and_live_untouched(leg_a_probe):
    """腿 A 判据 4：真实 Chroma 没启动（落盘目录不存在），live 指纹与 DB 三元组逐项不变。"""
    p = leg_a_probe
    assert p["store_paths_exist"] == [False, False], \
        "探针把 Chroma 换成记录器后，解析出的目录不得被创建出来：%s" % p["store_paths_exist"]
    assert p["env"]["RAG_DIR"] is None and p["env"]["CHROMA_PATH"] is None, \
        "探针形态必须是「只外置 DB_PATH」（否则腿 A 退化成 T1 的覆盖用例）：%s" % p["env"]

    # 逐项对比（不用「整体相等」掩盖差异类别）
    assert p["after_rag"] is not None, "生产向量目录必须仍存在（本波不授权清退/删除）"
    assert p["after_rag"]["files"] == p["before_rag"]["files"], \
        "相对路径集合变化（新增/删除了向量分片目录）：\nbefore=%s\nafter =%s" % (
            p["before_rag"]["files"], p["after_rag"]["files"])
    assert p["after_rag"]["total_bytes"] == p["before_rag"]["total_bytes"], \
        "总字节数变化：%r -> %r" % (p["before_rag"]["total_bytes"],
                                   p["after_rag"]["total_bytes"])
    assert p["after_rag"]["chroma_sqlite3_md5"] == p["before_rag"]["chroma_sqlite3_md5"], \
        "chroma.sqlite3 内容变化：%s -> %s" % (p["before_rag"]["chroma_sqlite3_md5"],
                                              p["after_rag"]["chroma_sqlite3_md5"])
    # 审核第 8 条：live 库是 WAL 库（实测 journal_mode=wal），主文件 md5 单独不足以证明
    # 「没被写」—— 三件逐项比，且前置断言侧车**在册**（缺侧车说明指纹本身退化成一件）。
    for part in ("main", "wal", "shm"):
        assert p["before_db"][part] is not None, \
            "live 库指纹缺了 %s 这一件（`demo/receipt_demo.db%s` 不存在？）：%r" % (
                part, "-" + part if part != "main" else "", p["before_db"])
        assert p["after_db"][part] == p["before_db"][part], \
            "live 库 %s 内容变化：%s -> %s" % (part, p["before_db"][part],
                                              p["after_db"][part])
    # `<repo>/.rag_chroma` 是历史落点：不存在必须继续不存在（存在则说明缺省又随 cwd 漂了）
    assert p["before_repo"] is None and p["after_repo"] is None, \
        "<repo>/.rag_chroma 不应存在（缺省已收敛到 <demo>/.rag_chroma）"


def test_leg_a_db_fingerprint_catches_wal_sidecar_only_change(tmp_path):
    """DB 三元组本身必须能抓到「只动 -wal / 只动 -shm」的变化（否则判据 4 对 WAL 库恒绿）。

    审核第 8 条的自校准：构造一个 WAL 库的 /tmp 替身（主文件字节固定，只改侧车），
    断言「主文件 md5 不变但三元组必须变」。若在替身上抓不到，live 那条逐项对比就是
    一句恒真断言。顺带记录 §9 复现命令的同族伪影：只拷主文件的 WAL 库无法被打开。
    """
    db = tmp_path / "wal.db"
    db.write_bytes(b"main-bytes-never-change")
    (tmp_path / "wal.db-wal").write_bytes(b"wal-v1")
    (tmp_path / "wal.db-shm").write_bytes(b"shm-v1")
    base = _db_fingerprint(str(db))
    assert base["main"] == _md5(str(db)) and base["wal"] and base["shm"], \
        "三元组前置：三件都要取到值：%r" % base

    (tmp_path / "wal.db-wal").write_bytes(b"wal-v2-longer")
    after_wal = _db_fingerprint(str(db))
    assert after_wal["main"] == base["main"], "本用例的前提是主文件字节不动"
    assert after_wal["wal"] != base["wal"], \
        "只改 -wal 时三元组没变化 —— 判据 4 对 WAL 库是恒绿的（审核第 8 条未修）"
    assert after_wal != base, "整个指纹字典必须能反映侧车变化"

    (tmp_path / "wal.db-shm").write_bytes(b"shm-v2")
    after_shm = _db_fingerprint(str(db))
    assert after_shm["shm"] != after_wal["shm"], \
        "只改 -shm 时三元组没变化 —— `-shm` 那一件形同虚设"

    # 侧车不存在时不得把「缺失」和「相同内容」混为一谈（None 也是一档状态）
    (tmp_path / "wal.db-wal").unlink()
    assert _db_fingerprint(str(db))["wal"] is None, "侧车删除后必须显式记为 None"

    # §9 同族伪影的**实证**（只读、走 live 的 /tmp 副本）：WAL 库只拷主文件时未
    # checkpoint 的事务全丢，连库都打不开；三件齐拷才拿得到真实行数。这就是「DB 走副本
    # 却没拷侧车 → db_rows=0 / 孤儿」伪影的机理，也是本波把侧车纳入指纹的理由。
    only_main = tmp_path / "live_main_only.db"
    shutil.copyfile(_LIVE_DB, str(only_main))
    assert _db_fingerprint(str(only_main))["wal"] is None, \
        "只拷主文件的副本没有侧车 —— 指纹必须显式呈现这个缺口"
    full = tmp_path / "live_full.db"
    shutil.copyfile(_LIVE_DB, str(full))
    for suffix in ("-wal", "-shm"):
        if os.path.exists(_LIVE_DB + suffix):
            shutil.copyfile(_LIVE_DB + suffix, str(full) + suffix)
    con = sqlite3.connect("file:%s?mode=ro" % full, uri=True)
    try:
        full_rows = con.execute("select count(*) from vendor_memory").fetchone()[0]
    finally:
        con.close()
    assert full_rows > 0, "齐拷副本读到 0 行（live 前置失效），本对照失去意义"

    wal_bytes = os.path.getsize(_LIVE_DB + "-wal") if os.path.exists(
        _LIVE_DB + "-wal") else 0
    if wal_bytes:
        # 有未 checkpoint 的 WAL 内容时，只拷主文件的副本必须读不出数据：
        # 要么直接打不开，要么行数少于齐拷副本 —— 两种都构成 §9 伪影。
        try:
            con = sqlite3.connect("file:%s?mode=ro" % only_main, uri=True)
            try:
                partial_rows = con.execute(
                    "select count(*) from vendor_memory").fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error:
            partial_rows = None
        assert partial_rows is None or partial_rows < full_rows, (
            "只拷主文件却读出了与齐拷副本相同的数据 —— live 已不是 WAL 库或 -wal 是空的，"
            "本用例与「侧车进指纹」的前提都要重审（wal=%d 字节）" % wal_bytes)


def test_leg_a_real_store_bytes_land_where_resolver_says(tmp_path):
    """腿 A 的 /tmp 假目标（真实落盘）：字节确实落在解析出的目录，且判据能抓到「替身 live」。

    why 需要这条：主探针把 Chroma 换成记录器，证明的是「`_store()` 把解析结果交给了
    Chroma」；本条把记录器换成**真实 Chroma**，但落点用 `RAG_DIR` 显式钉在 /tmp 替身
    目录上，于是同时钉住两件事：
      1. 「解析结果 == 字节落点」—— 判据不是在验一张纸；
      2. 一旦落点等于（替身）生产目录，`_judge_store_target` 必须报警 —— 即真 live
         被写时本守卫一定转红，不是「读不到」。
    全程不碰真 live：替身目录在 tmp_path，且用例前后各取一次 live 指纹做对照。
    """
    stand_in_live = tmp_path / "fake_demo" / ".rag_chroma"
    db_path = str(tmp_path / "outside" / "t3_real.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(str(stand_in_live), exist_ok=True)

    before = _dir_fingerprint(_LIVE_RAG_DIRS[0])
    out = _run_store_probe(db_path, "real", extra_env={"RAG_DIR": str(stand_in_live)})
    after = _dir_fingerprint(_LIVE_RAG_DIRS[0])

    # (1) 真实 Chroma 的字节落在解析结果里（替身目录被写）
    assert out["chroma_files"] == [True], \
        "真实 Chroma 未在解析出的目录写出 chroma.sqlite3 —— 判据 1 只是在验纸：%s" % out
    assert out["rag_persist_dir"] == str(stand_in_live) == out["resolved_now"], \
        "落点必须等于解析结果：%s" % out

    # (2) 判据必须把「落点等于生产目录」这种形态判红（这里用替身目录当生产目录）
    violations = _judge_store_target(out["rag_persist_dir"], db_path,
                                     live_dirs=(str(stand_in_live),))
    assert any("生产向量目录" in v for v in violations), \
        "落点等于生产目录时判据没报警 —— 腿 A 恒绿：%s" % violations

    # (3) 真 live 一个字节都没动
    assert after == before, "真 live 向量目录被写脏：\nbefore=%r\nafter=%r" % (before, after)


# =====================================================================
# 腿 A 自校准 / 反证（全部在 /tmp，仓库文件只读）
# =====================================================================
def _load_rag_paths_copy(copy_path, name):
    """按文件路径加载 rag_paths 副本（该模块只 import os，加载无副作用）。"""
    spec = importlib.util.spec_from_file_location(name, copy_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _strip_derived_branch(source):
    """删掉 resolve_rag_dir 函数体里所有含 `derived` 的行，返回 (新源码, 命中行数)。

    这是「腿 A 依赖 T1 派生分支」的反证手法：不改仓库文件，只在 /tmp 副本上做减法突变。
    """
    tree = ast.parse(source)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "resolve_rag_dir"), None)
    if fn is None:
        return source, 0
    lines = source.splitlines(keepends=True)
    drop = set()
    for stmt in fn.body:
        body_text = "".join(lines[stmt.lineno - 1: max(stmt.end_lineno or stmt.lineno,
                                                       stmt.lineno)])
        if "derived" in body_text:
            drop.update(range(stmt.lineno - 1, max(stmt.end_lineno or stmt.lineno,
                                                   stmt.lineno)))
    new = "".join(l for i, l in enumerate(lines) if i not in drop)
    return new, len(drop)


def test_leg_a_judge_catches_mutant_rag_paths_without_derived_branch(tmp_path):
    """反证：rag_paths 去掉派生分支后，腿 A 判据必须转红（否则腿 A 是恒绿）。

    /tmp 假目标：把唯一实现复制到 `<tmp>/fake_app/app/services/` 下（原件与突变体**同目录**，
    这样两者的 `app_dir()` 都是 `<tmp>/fake_app`，即同一替身 live 目录 —— 差异只剩
    「有没有派生分支」）。替身目录让反证可以完全离开仓库：本用例绝不向真 live 目录写
    任何东西（前作 P29 踩过「为校准指纹而真往 live 写临时文件」的坑）。
    """
    fake_services = tmp_path / "fake_app" / "app" / "services"
    fake_services.mkdir(parents=True)
    pristine = fake_services / "rag_paths.py"
    # 用例开始先抓仓库真身的 md5 基线：本用例只做「读原件 + 写 /tmp 副本」，最后用它
    # 证明原件没被写过。审核第 1 条：原来这里写的是
    # `_md5(_RAG_PATHS_REPO) == _md5_of_repo_rag_paths()`，而右边就是对同一个文件取
    # md5 —— 结构上不可能失败（恒真自比），所以那句已删除，「原件只读」改由**基线**证明。
    md5_repo_baseline = _md5(_RAG_PATHS_REPO)
    original_src = open(_RAG_PATHS_REPO, encoding="utf-8").read()
    pristine.write_text(original_src, encoding="utf-8")
    md5_pristine = _md5(str(pristine))
    assert md5_pristine == md5_repo_baseline, "副本与原件内容不一致，后面的对照失去意义"

    mutant_src, dropped = _strip_derived_branch(original_src)
    assert dropped > 0, "突变补丁没命中 resolve_rag_dir 里的派生分支，本反证失去判别力"
    assert "derived_rag_dir_from_db(" not in mutant_src.split("def resolve_rag_dir")[1], \
        "突变后 resolve_rag_dir 仍在调用派生分支，反证不成立"
    mutant = fake_services / "rag_paths_mutant.py"
    mutant.write_text(mutant_src, encoding="utf-8")
    md5_mutant = _md5(str(mutant))
    assert md5_mutant != md5_pristine

    # 审核第 4 条的反证：同一突变体上「只看函数名」的旧判据仍然为真，而本文件的
    # 「函数体内真的调用了派生分支」判据必须转红 —— 这条对照就是新旧判据的差。
    assert _criterion_derived_declared_by_name(mutant_src) is True, \
        "旧判据（函数名在册）在突变体上竟然也失败，说明本对照没有区分度"
    assert _criterion_derived_wired_in_body(mutant_src) is False, \
        "新判据在「摘掉派生调用」的突变体上没有转红 —— 它同样抓不到接线丢失"
    assert _criterion_derived_wired_in_body(original_src) is True, \
        "新判据在仓库真身上就是红的（T1 未落地或函数被改写？）"

    fake_live = str(tmp_path / "fake_app" / ".rag_chroma")
    os.makedirs(fake_live, exist_ok=True)
    (tmp_path / "fake_app" / ".rag_chroma" / _CHROMA_DB_FILE).write_bytes(b"fake-live")
    db_outside = str(tmp_path / "outside" / "t3_mutant.db")
    os.makedirs(os.path.dirname(db_outside), exist_ok=True)

    saved = dict(os.environ)
    try:
        for k in ("DB_PATH", "RAG_DIR", "CHROMA_PATH"):
            os.environ.pop(k, None)
        os.environ["DB_PATH"] = db_outside

        clean_mod = _load_rag_paths_copy(str(pristine), "t3_rag_paths_pristine")
        mutant_mod = _load_rag_paths_copy(str(mutant), "t3_rag_paths_mutant")
        clean_resolved = clean_mod.resolve_rag_dir()
        mutant_resolved = mutant_mod.resolve_rag_dir()

        # 前提：突变体确实退化成「缺省 = 替身 live 目录」，且与原件结论不同
        assert mutant_resolved == os.path.join(str(tmp_path / "fake_app"), ".rag_chroma"), \
            "突变体应落回替身缺省目录，实际=%r" % mutant_resolved
        assert mutant_resolved != clean_resolved, \
            "原件与突变体解析结果相同 → 派生分支判据没有判别力"

        violations = _judge_store_target(mutant_resolved, db_outside,
                                        live_dirs=(fake_live,))
        assert violations, "腿 A 判据没抓到「没有派生分支」的形态 —— 它是恒绿的"
        assert any("生产向量目录" in v for v in violations), violations
        assert any("DB 同目录" in v for v in violations), violations

        # 对照：原件解析结果在同样判据下必须零违规
        assert _judge_store_target(clean_resolved, db_outside, live_dirs=(fake_live,)) == [], \
            "干净实现被判违规（判据过严或原件未落地派生分支）：%r" % clean_resolved
    finally:
        os.environ.clear()
        os.environ.update(saved)

    # 还原：/tmp 副本回写原内容，md5 必须回到 pristine 值；仓库真身必须回到用例开始时
    # 抓的基线（同一个值比同一个值才是真判据，见上面的注释）。
    mutant.write_text(original_src, encoding="utf-8")
    assert _md5(str(mutant)) == md5_pristine
    assert _md5(str(pristine)) == md5_pristine
    assert _md5(_RAG_PATHS_REPO) == md5_repo_baseline, (
        "仓库里的 rag_paths 真身在本用例内被改写过 —— 「原件只读」这条红线被违反")


def _criterion_derived_declared_by_name(source):
    """旧判据（审核第 4 条判定为恒真的那一种）：函数名在册即算「有派生分支」。

    保留它只为了在反证里当**对照**（证明新判据严格更强），不参与任何断言结论。
    """
    names = {n.name for n in ast.walk(ast.parse(source))
             if isinstance(n, ast.FunctionDef)}
    return "derived_rag_dir_from_db" in names or "externalized_db_path" in names


def _criterion_derived_wired_in_body(source):
    """新判据：`resolve_rag_dir` 的**函数体内**真的调用了派生分支，且派生分支自己
    真的消费了 `externalized_db_path()`。

    与 :810 那条反证里已在用的口径一致（「函数体内出现 `derived_rag_dir_from_db(`」），
    搬到仓库真身上；只收集 FunctionDef 名字是判不出「摘掉调用」的。
    """
    tree = ast.parse(source)
    by_name = {n.name: n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    resolver = by_name.get("resolve_rag_dir")
    if resolver is None:
        return False
    if not _calls_in_body(resolver, "derived_rag_dir_from_db"):
        return False
    derived = by_name.get("derived_rag_dir_from_db")
    return derived is not None and _calls_in_body(derived, "externalized_db_path")


def _calls_in_body(func, callee):
    """函数体内（不含函数名本身）是否出现 `callee(` 调用。"""
    return any(isinstance(n, ast.Call)
               and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == callee
               for n in ast.walk(func))


def test_leg_a_fingerprint_judge_is_not_tautological(tmp_path):
    """指纹判据本身必须能抓到变化（否则腿 A 判据 4 恒绿）—— 全在 live 的 /tmp 副本上做。"""
    live_copy = tmp_path / "rag_copy"
    shutil.copytree(_LIVE_RAG_DIRS[0], str(live_copy))
    base = _dir_fingerprint(str(live_copy))
    assert base is not None and base["files"], "副本必须是空/非空一致的前置"

    # (1) 新增文件 → 相对路径集合必须变
    (live_copy / "junk.tmp").write_bytes(b"x")
    assert _dir_fingerprint(str(live_copy))["files"] != base["files"], \
        "指纹抓不到新增文件，无法充当「无写入」判据"
    (live_copy / "junk.tmp").unlink()
    assert _dir_fingerprint(str(live_copy)) == base, "撤销变化后必须回到原值"

    # (2) 只改内容、不改大小 → 只有 chroma.sqlite3 的 md5 能抓到
    chroma = live_copy / _CHROMA_DB_FILE
    original = chroma.read_bytes()
    chroma.write_bytes(original[:len(original) - 1] + b"\x00")
    got = _dir_fingerprint(str(live_copy))
    assert got["chroma_sqlite3_md5"] != base["chroma_sqlite3_md5"], \
        "指纹抓不到库文件内容变化（Chroma 打开持久目录会写 migration 表）"
    chroma.write_bytes(original)
    assert _dir_fingerprint(str(live_copy)) == base

    # (3) 不存在的输入 → None（调用方据此断言「仍不存在」）
    assert _dir_fingerprint(str(tmp_path / "nope")) is None


# =====================================================================
# 腿 B：结构性扫描守卫（对仓库真身生效）
# =====================================================================
def test_leg_b_no_second_copy_of_default_rag_dir_assembly():
    """B1：生产树里除唯一实现外不得再拼接缺省向量目录。

    测试树里同样的拼接（`os.path.join(_DEMO_DIR, ".rag_chroma")`）是**断言目标**
    ——「本用例不得写入 live」的钉桩，不是第二份解析实现，因此 B1 不判它失败；
    但清单必须被回显并可核对：一旦其中任何一处把同一个路径喂给落点入口
    （`RAG_DIR`/`CHROMA_PATH` 赋值、`persist_directory=`、`Chroma(...)`），
    B4（作用于全部四棵树）立刻转红。分工写在两处判据里，不是放宽 B1。
    """
    roots = list(_PRODUCTION_ROOTS)
    implementers = _files_defining_resolve(roots)
    hits = scan_default_rag_dir_assembly(roots, exempt_files=implementers)
    assert hits == [], (
        "生产代码出现了第二份「缺省向量目录」拼接（G1/①类：同一口径多份实现只改一份）："
        "%s；唯一实现文件=%s" % (hits, [_rel(p) for p in implementers]))

    test_hits = scan_default_rag_dir_assembly(
        list(_TEST_ROOTS), exempt_files=_files_defining_resolve(list(_TEST_ROOTS)))
    by_file = {}
    for h in test_hits:
        by_file.setdefault(h["file"], []).append(h["lineno"])
    print("\nB1 测试树回显（断言目标，非实现；变成落点即被 B4 判红）：%d 处 / %d 个文件"
          % (len(test_hits), len(by_file)))
    for f, lns in sorted(by_file.items()):
        print("  %-56s %s" % (f, lns))
    assert test_hits, "扫描器在测试树零命中 —— 说明判据已失效（现有钉桩文件不该消失）"
    # 每条回显都必须能被 B4 的落点判据覆盖（即：确实存在「只断言不落地」的对照关系）
    assert all(os.path.isfile(os.path.join(_REPO_ROOT, f)) for f in by_file), \
        "回显清单里有已不存在的文件，扫描器路径口径已坏：%s" % sorted(by_file)


def test_leg_b_single_implementation_of_resolve_rag_dir():
    """B2：全仓 `def resolve_rag_dir` 只能有一份（防止整份复制 rag_paths 绕开 B1）。"""
    implementers = _files_defining_resolve(list(_ALL_ROOTS))
    assert len(implementers) == 1, \
        "resolve_rag_dir 的定义必须唯一（同一口径只保留一份实现），实际=%s" % [
            _rel(p) for p in implementers]
    assert os.path.realpath(implementers[0]) == os.path.realpath(_RAG_PATHS_REPO), \
        "唯一实现应在 demo/app/services/rag_paths.py，实际=%s" % _rel(implementers[0])


def test_leg_b_no_priority_chain_fallback_copy():
    """B3：不得出现「读 RAG_DIR/CHROMA_PATH 时自带缺省落点」的复刻（P6-X2 D1 形态）。"""
    hits = scan_priority_chain_fallbacks(list(_ALL_ROOTS))
    assert hits == [], "复刻了优先级链（带缺省值的环境变量读）：%s" % hits


def test_leg_b_no_store_target_pointed_at_live_default():
    """B4：任何落点入口（env 写入 / persist_directory）都不得指向自拼的缺省目录。"""
    hits = scan_store_target_pointed_at_live(list(_ALL_ROOTS))
    assert hits == [], "向量落点被指向「自行拼接的缺省目录」（等于主动关掉隔离）：%s" % hits


def test_leg_b_module_time_db_path_is_covered_by_derived_branch():
    """B5：模块期/子进程 env 设 DB_PATH 的形态，必须能被 T1 的派生分支兜住。

    命中清单同时是本 Task 的「复现证据」：`demo/tests/test_uat_031_...py:29` 这类形态
    必须仍在扫描结果里（守卫不是靠文件消失变绿的）。

    三态口径（审核第 3 条）：covered / uncovered / **unverifiable**。unverifiable 是
    「守卫没能力证明」，不得混进 covered；它单独成清单、计入报告并在模块期判失败。
    """
    records, parse_errors = scan_db_path_entry_points(list(_ALL_ROOTS))
    assert parse_errors == [], "有文件无法静态解析，扫描器对其失效：%s" % parse_errors

    module_hits = [r for r in records if r["scope"] == "module"]
    env_hits = [r for r in records if r["scope"].startswith("env-dict")]
    uncovered = [r for r in records if r["status"] == "uncovered"]
    unverifiable = [r for r in records if r["status"] == "unverifiable"]
    # 模块 import 期是 G1 的主窗口：这里「静态不可判」也判失败（不许用间接写法躲判据）
    module_unverifiable = [r for r in module_hits if r["status"] == "unverifiable"]
    env_unverifiable = [r for r in env_hits if r["status"] == "unverifiable"]

    lines = ["", "B5 命中清单（模块期 %d 处 / 子进程 env 通道 %d 处；三态计数 "
             "covered=%d uncovered=%d unverifiable=%d）：" % (
                 len(module_hits), len(env_hits),
                 sum(1 for r in records if r["status"] == "covered"),
                 len(uncovered), len(unverifiable))]
    for r in records:
        lines.append("  %-58s:%-4d %-14s %-32s %-30s %s" % (
            r["file"], r["lineno"], r["channel"], r["scope"], r["value"],
            "" if r["status"] == "covered" else "<== %s：%s" % (r["status"], r["reason"])))
    lines.append("子进程 env 通道 unverifiable（单列清单，回显供复核，不判失败）：%d 处"
                 % len(env_unverifiable))
    for r in env_unverifiable:
        lines.append("    %s:%d %s -> %s" % (r["file"], r["lineno"], r["channel"], r["value"]))
    print("\n".join(lines))

    # (a) 三态必须把清单分完（不得有第四态被下游静默忽略），且**两个事故窗口都得有命中**
    assert all(r["status"] in ("covered", "uncovered", "unverifiable") for r in records), \
        "出现了未定义的判定态：%s" % sorted({r["status"] for r in records})
    assert module_hits and env_hits, (
        "模块期命中=%d / 子进程 env 通道命中=%d —— 任一档归零说明对应扫描通道已失效，"
        "本用例的绿不再有意义" % (len(module_hits), len(env_hits)))

    # (b) 核心不变式：**没有任何一条 covered 是靠「看不见取值」得来的**
    #     —— 要么同作用域显式设了 RAG_DIR/CHROMA_PATH（与 DB_PATH 无关的独立保证），
    #     要么取值本身静态可读（字面量或带临时目录/路径证据的表达式）。
    fake_covered = [r for r in records if r["status"] == "covered"
                    and not r["explicit"] and not r["path_evidence"]]
    assert fake_covered == [], (
        "以下记录被判 covered 却既没有显式向量隔离、也没有可读取值 —— "
        "「把不可判当已证」的形态又回来了（审核第 3 条）：%s" % fake_covered)

    assert any(r["file"].endswith("test_uat_031_sku_merge_batch_migration.py")
               and r["scope"] == "module" for r in module_hits), (
        "扫描器没抓到 G1 的原始形态（test_uat_031 模块期设 DB_PATH）—— "
        "腿 B 的主目标已失效，本守卫不可信")
    assert uncovered == [], (
        "存在 T1 派生分支兜不住的 DB_PATH 形态（这些运行会把向量写进生产目录，"
        "或把派生目录写进仓库内）：%s" % uncovered)
    assert module_unverifiable == [], (
        "模块 import 期出现静态不可判的 DB_PATH 写法，无法证明派生分支能兜住，"
        "需改成可判写法或扩展本判据（不得放宽）：%s" % module_unverifiable)
    # (c) env 通道的 unverifiable 不得为 0，且**不能只由本文件自己的夹具贡献** —— 否则
    #     「清单里看得见 unverifiable」可能只是扫描器扫到了自己，产品树退化照样绿。
    assert env_unverifiable, (
        "子进程 env 通道一条 unverifiable 都没有：说明扫描器把所有取值都判成了可证 —— "
        "本波实测至少有 memory_acceptance_check.py:513 的 ctx.db_path 这类形态，"
        "看不见它=通道扫描退化")
    foreign_unverifiable = [r for r in env_unverifiable
                            if not r["file"].endswith("test_vector_store_isolation_guard.py")]
    assert foreign_unverifiable, (
        "env 通道的 unverifiable 全部来自本文件自己（%d 条）—— 对生产/其它测试树的"
        "不透明取值失去可见性，本条判据退化" % len(env_unverifiable))
    # 审核第 3 条点名的四处（上一轮被记成 covered 的不透明取值）—— 本波**回显**它们在
    # 清单里的实际判定，供复核；硬判据是上面的 (b)(c) 与假目标用例
    # `test_opaque_db_path`（把分类退回旧实现时那条必红），因此这里不钉「这些文件必须
    # 一直不透明」，免得后波把它们修好了反而把守卫判红。
    named = ("demo/scripts/import_sku_alias_approvals.py",
             "demo/scripts/memory_extract_replay.py",
             "demo/tests/test_experiment_guardian.py",
             "demo/tests/test_resolve_cache_single_impl.py")
    lines2 = ["审核第 3 条点名形态的当前判定："]
    for n in named:
        got = [r for r in records if r["file"] == n]
        lines2.append("  %-52s %s" % (n, sorted({r["status"] for r in got}) or "已不在清单"))
    print("\n".join(lines2))


def test_leg_b_derived_branch_is_wired_into_the_resolver():
    """B5 前提（审核第 4 条）：派生分支不是「函数名在册」，而是**真被 resolve_rag_dir 调用**。

    旧判据只收集 FunctionDef 名字，实测「摘掉 resolve_rag_dir 里的派生调用、函数名仍在」
    照样 PASS —— 那条判据恒真。本用例改用 `_criterion_derived_wired_in_body`（函数体内
    出现 `derived_rag_dir_from_db(`，且派生函数体内出现 `externalized_db_path(`），
    并在同一份 /tmp 突变体上对照：旧判据为真、新判据为红（差集就是本用例的判别力）。
    """
    src = open(_RAG_PATHS_REPO, encoding="utf-8").read()
    assert _criterion_derived_wired_in_body(src), (
        "唯一实现的 resolve_rag_dir 没有真的调用派生分支（或派生分支没消费 "
        "externalized_db_path）→ 腿 A 的红灯将永远无法转绿")
    assert _criterion_derived_declared_by_name(src), "对照项：函数名判据本身也得为真"

    mutant_src, dropped = _strip_derived_branch(src)
    assert dropped > 0, "突变补丁没命中派生分支，本对照失去判别力"
    assert _criterion_derived_declared_by_name(mutant_src) is True, (
        "旧判据在突变体上竟然也失败了 —— 说明新旧判据没有差别，本用例失去意义")
    assert _criterion_derived_wired_in_body(mutant_src) is False, (
        "新判据在「摘掉派生调用」的突变体上仍为真 → 它和旧判据一样抓不到接线丢失")

    # 反向突变：只摘掉派生分支内部对 externalized_db_path 的消费（链断在第二环）
    tree = ast.parse(src)
    derived_fn = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "derived_rag_dir_from_db")
    second_leg = "".join(src.splitlines(keepends=True)[:derived_fn.lineno - 1]) + \
        "def derived_rag_dir_from_db():\n    return ''\n"
    assert _calls_in_body(ast.parse(second_leg).body[0], "externalized_db_path") is False, \
        "第二环突变没生效"
    assert _criterion_derived_wired_in_body(second_leg) is False, (
        "派生分支被掏空成常量时新判据仍为真 —— 链的第二环没判到")


# =====================================================================
# 腿 B 自校准（/tmp 假目标必须被抓到；不存在的输入必须零命中）
# =====================================================================
def test_leg_b_scanners_catch_planted_fakes(tmp_path):
    """扫描器（B1/B3/B4/B5 与定义计数 B2）对「故意写坏的假实现」必须有判别力。

    否则「零命中」可能只是扫描器失效 —— 本项目反复踩过恒真用例（计划 §3 通用验收 ①）。
    """
    fake_root = tmp_path / "fake_pkg"
    fake_root.mkdir()

    # 假实现 A：复刻缺省目录 + 复刻优先级链 + 把落点指回缺省目录（三种写入通道各一条）
    (fake_root / "bad_paths.py").write_text(
        'import os\n'
        'PERSIST_DIR = os.environ.get("RAG_DIR", "./.rag_chroma")\n'
        'os.environ["RAG_DIR"] = os.path.join(os.getcwd(), ".rag_chroma")\n'
        'os.environ.update({"CHROMA_PATH": os.path.join(os.getcwd(), ".rag_chroma")})\n'
        'os.environ.setdefault("RAG_DIR", "./.rag_chroma")\n',
        encoding="utf-8")
    # 假实现 B（干净）：只消费唯一实现
    (fake_root / "clean_paths.py").write_text(
        'from app.services import rag_paths\n'
        'PERSIST_DIR = rag_paths.resolve_rag_dir()\n'
        '# 历史口径曾写作 ./.rag_chroma（注释里出现不算命中）\n',
        encoding="utf-8")
    # 假用例 C：模块期把 DB_PATH 设成 live 相对名 / 空值
    (fake_root / "test_bad_db_path.py").write_text(
        'import os\n'
        'os.environ["DB_PATH"] = "./receipt_demo.db"\n'
        'os.environ.setdefault("DB_PATH", "receipt_demo.db")\n',
        encoding="utf-8")
    # 假用例 D（干净）：模块期外置到临时库 + 同函数同时设 RAG_DIR
    (fake_root / "test_good_db_path.py").write_text(
        'import os, tempfile\n'
        '_TMP = tempfile.mkdtemp(prefix="good_")\n'
        'os.environ["DB_PATH"] = os.path.join(_TMP, "good.db")\n'
        'def _start():\n'
        '    env = dict(os.environ)\n'
        '    env["DB_PATH"] = os.path.join(_TMP, "child.db")\n'
        '    env["RAG_DIR"] = os.path.join(_TMP, "rag")\n'
        '    return env\n'
        'def _start_bare():\n'
        '    env = dict(os.environ)\n'
        '    env["DB_PATH"] = os.path.join(_TMP, "child2.db")\n'
        '    return env\n',
        encoding="utf-8")
    # 假用例 E（模块期间接写法）：DB_PATH 指向查不到定义的裸名字 → 不可判，须被抓
    (fake_root / "test_ghost_db_path.py").write_text(
        'import os\n'
        'os.environ["DB_PATH"] = unresolved_name\n',
        encoding="utf-8")
    # 假用例 F（新通道）：os.environ.update({...}) 与 env= 字典字面量 / dict(os.environ, ...)
    (fake_root / "test_channel_db_path.py").write_text(
        'import os, tempfile\n'
        '_T = tempfile.mkdtemp()\n'
        'os.environ.update({"DB_PATH": "./receipt_demo.db", "RAG_DIR": _T})\n'
        'def _serve(env_overrides=None):\n'
        '    env = os.environ.copy()\n'
        '    env.update(env_overrides or {})\n'
        '    return env\n'
        '_serve(env_overrides={"DB_PATH": "./receipt_demo.db"})\n'
        'def _kw():\n'
        '    return dict(os.environ, DB_PATH="./receipt_demo.db")\n',
        encoding="utf-8")
    # 假用例 G（不可判的**函数调用**取值）：审核第 3 条 —— 不得记 covered
    (fake_root / "test_opaque_db_path.py").write_text(
        'import os\n'
        'def _pick():\n'
        '    return "whatever"\n'
        'os.environ["DB_PATH"] = _pick()\n'
        'os.environ["DB_PATH"] = os.environ.get("DB_PATH")\n'
        'cfg = object()\n'
        'os.environ["DB_PATH"] = cfg.db_path\n',
        encoding="utf-8")
    # 假用例 H（只读不写）：读 RAG_DIR 不得把该函数点亮成「已显式设 RAG_DIR」
    (fake_root / "test_readonly_env.py").write_text(
        'import os, tempfile\n'
        '_T = tempfile.mkdtemp()\n'
        'def _start():\n'
        '    saved_rag = os.environ.get("RAG_DIR")\n'
        '    os.environ["DB_PATH"] = os.path.join(_T, "x.db")\n'
        '    return saved_rag\n',
        encoding="utf-8")
    # 假用例 I（字面量指向假树内部）：审核第 7 条 —— 派生目录落进仓库内即 uncovered
    (fake_root / "test_inrepo_db_path.py").write_text(
        'import os\n'
        'os.environ["DB_PATH"] = %r\n' % str(fake_root / "inside" / "case.db"),
        encoding="utf-8")
    # 假树自带一份唯一实现：让「产品结论」按假树的 live 定位来算（守卫不复刻规则）
    fake_services = fake_root / "app" / "services"
    fake_services.mkdir(parents=True)
    shutil.copyfile(_RAG_PATHS_REPO, str(fake_services / "rag_paths.py"))
    # 假实现 E/F：两份 `def resolve_rag_dir`（B2 要抓到「定义不再唯一」）
    (fake_root / "rag_paths_copy.py").write_text(
        'import os\n'
        'def resolve_rag_dir(rag_dir=None):\n'
        '    return os.path.join(os.getcwd(), ".rag_chroma")\n',
        encoding="utf-8")
    (fake_root / "rag_paths_copy2.py").write_text(
        'import os\n'
        'def resolve_rag_dir(rag_dir=None):\n'
        '    return os.path.abspath("./.rag_chroma")\n',
        encoding="utf-8")

    roots = [str(fake_root)]
    clean_exempt = _files_defining_resolve(roots)

    hits_a = scan_default_rag_dir_assembly(roots, exempt_files=())
    assert any(h["file"].endswith("bad_paths.py") for h in hits_a), \
        "B1 没抓到复刻缺省目录的假实现：%s" % hits_a
    assert not any(h["file"].endswith("clean_paths.py") for h in hits_a), \
        "B1 把消费唯一实现的干净文件误判为命中：%s" % hits_a
    assert any(h["literal"] == "./.rag_chroma" for h in hits_a if
               h["file"].endswith("bad_paths.py")), hits_a

    hits_chain = scan_priority_chain_fallbacks(roots)
    assert any(h["file"].endswith("bad_paths.py") for h in hits_chain), \
        "B3 没抓到带缺省值的环境变量读：%s" % hits_chain

    hits_target = scan_store_target_pointed_at_live(roots)
    bad_target = [h for h in hits_target if h["file"].endswith("bad_paths.py")]
    assert bad_target, \
        "B4 没抓到把 RAG_DIR 指回自拼缺省目录的假实现：%s" % hits_target
    assert not any(h["file"].endswith("test_good_db_path.py") for h in hits_target), \
        "B4 误报了干净假用例：%s" % hits_target
    # 三条写入通道各须一条命中（漏一条就是 B4 与 B5 的通道清单再次不一致）
    for want in ("env['RAG_DIR']", "environ.update()", "environ.setdefault("):
        assert any(want in h["why"] for h in bad_target), \
            "B4 漏了通道 %r：%s" % (want, [h["why"] for h in bad_target])

    product = _product_resolver(str(fake_services / "rag_paths.py"))
    records, perr = scan_db_path_entry_points(
        roots, repo_root=str(fake_root), product=product)
    assert perr == []

    def _for(name):
        return [r for r in records if r["file"].endswith(name)]

    bad = _for("test_bad_db_path.py")
    good = _for("test_good_db_path.py")
    ghost = _for("test_ghost_db_path.py")
    assert bad and all(r["status"] == "uncovered" for r in bad), \
        "B5 没抓到兜不住的模块期 DB_PATH 形态：%s" % bad
    assert good and all(r["status"] == "covered" for r in good), \
        "B5 把兜得住的形态误判为违规：%s" % good
    bare = [r for r in good if r["scope"].startswith("env-dict(仅 DB_PATH")]
    assert bare, "B5 没识别「子进程 env 只设 DB_PATH」这条通道（靠派生分支兜）：%s" % good
    # 模块期「静态不可判」也必须被抓（不许用间接写法绕过可证性）
    assert ghost and all(r["status"] == "unverifiable" and r["scope"] == "module"
                         for r in ghost), \
        "B5 没把模块期查不到定义的裸名字标为 unverifiable：%s" % ghost

    # 审核第 3 条：不透明函数调用 / 属性取值 —— 旧实现记 covered，本波必须记 unverifiable
    opaque = _for("test_opaque_db_path.py")
    assert len(opaque) == 3, "三条不透明取值都该进清单，实际=%s" % opaque
    assert all(r["status"] == "unverifiable" and r["path_evidence"] is False
               for r in opaque), \
        "不透明取值被当成已证（审核第 3 条未修）：%s" % opaque

    # 审核第 5 条：update / env= 字典字面量 / dict(os.environ, DB_PATH=...) 三条新通道
    chan = _for("test_channel_db_path.py")
    assert any(r["channel"] == "update" for r in chan), (
        "os.environ.update({...}) 通道没被扫到：%s" % chan)
    assert any(r["channel"] == "call-dictarg" for r in chan), (
        "字典字面量喂给 env_overrides= 的通道没被扫到（实测 memory_acceptance_check.py:514 "
        "就是这个形态）：%s" % chan)
    assert any(r["channel"] == "call-kw" for r in chan), (
        "dict(os.environ, DB_PATH=...) 通道没被扫到：%s" % chan)
    # 三条都写的是假树的 live 相对名 → 全须 uncovered
    assert all(r["status"] == "uncovered" for r in chan), (
        "新通道里的兜不住形态没被判 uncovered：%s" % chan)

    # 只读不算写：该函数的 DB_PATH 记录必须是「仅 DB_PATH，靠派生分支」，不是「显式设 RAG_DIR」
    readonly = _for("test_readonly_env.py")
    assert readonly, "只读假用例没进清单（扫描器退化）"
    assert all("显式设" not in r["scope"] for r in readonly), (
        "一次 os.environ.get(\"RAG_DIR\") 被误判成「同函数已显式设 RAG_DIR」：%s"
        % readonly)

    # 审核第 7 条：产品判已外置、但派生目录在仓库内 → uncovered
    inrepo = _for("test_inrepo_db_path.py")
    assert inrepo and all(r["status"] == "uncovered" and "仓库内" in r["reason"]
                          for r in inrepo), (
        "「派生向量目录落进仓库内」没判 uncovered（与腿 A 不同口径，审核第 7 条未修）：%s"
        % inrepo)

    implementers = _files_defining_resolve(roots)
    assert len(implementers) == 3, \
        "B2 没抓到「整份复制唯一实现」的假文件（假树自带副本 + 两份故意复制 = 3）：%s" % implementers
    assert clean_exempt and len(clean_exempt) == 3, "豁免表应等于定义唯一实现的文件"


def test_leg_b_b4_tracks_transitive_alias(tmp_path):
    """B4 的名字追踪必须是传递闭包（审核第 6 条：两跳别名实测零命中）。

    三段递进：一跳 / 两跳 / 三跳 + 「别名再喂给 persist_directory」。同时给出一条
    **干净**对照（同形状但拼的是临时目录）证明不是「一律判红」的糊判据。
    """
    root = tmp_path / "alias_pkg"
    root.mkdir()
    (root / "two_hop.py").write_text(
        'import os\n'
        '_base = os.path.join(os.getcwd(), ".rag_chroma")\n'
        '_deep = _base + "/nested"\n'
        'os.environ["RAG_DIR"] = _deep\n',
        encoding="utf-8")
    (root / "three_hop.py").write_text(
        'import os\n'
        'a = ".rag_chroma"\n'
        'b = os.path.join(os.getcwd(), a)\n'
        'c = b.rstrip("/") + "/x"\n'
        'from langchain_community.vectorstores import Chroma\n'
        'Chroma(persist_directory=c)\n',
        encoding="utf-8")
    (root / "clean_alias.py").write_text(
        'import os, tempfile\n'
        'base = tempfile.mkdtemp(prefix="rag_")\n'
        'deep = os.path.join(base, "v1")\n'
        'os.environ["RAG_DIR"] = deep\n',
        encoding="utf-8")
    (root / "one_hop_baseline.py").write_text(
        'import os\n'
        'base = os.path.join(os.getcwd(), ".rag_chroma")\n'
        'os.environ["RAG_DIR"] = base\n',
        encoding="utf-8")

    hits = scan_store_target_pointed_at_live([str(root)])
    files = {h["file"].split("/")[-1] for h in hits}
    assert "one_hop_baseline.py" in files, "连一跳都抓不到 —— 闭包改造把基础能力弄丢了"
    assert "two_hop.py" in files, \
        "两跳别名（审核第 6 条的实测形态）仍然零命中，闭包没生效：%s" % hits
    assert "three_hop.py" in files, \
        "三跳 + 喂给 persist_directory 的形态没抓到：%s" % hits
    assert "clean_alias.py" not in files, \
        "同形状的临时目录别名被误判（判据退化成一律判红）：%s" % hits

    # 闭包本身的形状：只沿赋值扩张，不猜运行期值
    (root / "runtime_alias.py").write_text(
        'import os\n'
        'base = call_returns_dir()\n'
        'os.environ["RAG_DIR"] = base\n',
        encoding="utf-8")
    assert not any(h["file"].endswith("runtime_alias.py")
                   for h in scan_store_target_pointed_at_live([str(root)])), \
        "闭包不该把「值来自未知调用」当命中（那是误报，不是判别力）"


@pytest.mark.parametrize("form,expected", [
    # (form, 期望结论) —— 期望值手写，不读任何实现，否则「对拍」就变成自比
    ("", "uncovered"),
    ("   ", "uncovered"),
    ("receipt_demo.db", "uncovered"),
    ("./receipt_demo.db", "uncovered"),
    (_LIVE_DB, "uncovered"),
    ("/tmp/vm_fa_wave/outside/x.db", "covered"),
    (os.path.join(_DEMO_DIR, "tmp_case.db"), "uncovered"),
    (os.path.join(_REPO_ROOT, "tests", "tmp_case.db"), "uncovered"),
])
def test_leg_b_db_path_externalization_agrees_with_product(form, expected):
    """B5 的「未外置」结论必须与产品实现逐项一致（审核第 2 条：守卫复刻了一份规则）。

    判据侧现在**直接问** `rag_paths.externalized_db_path()` /
    `derived_rag_dir_from_db()`；本用例同时钉两件事：
      1. 守卫结论 == 手写期望（八形态：空 / 全空白 / 相对名 / ./相对名 / live 绝对 /
         tmp 绝对 / <repo>/demo 内 / <repo>/tests 内）；
      2. 守卫结论 == 「产品原始结论 + 仓库内这条附加策略」—— 即两者不可能各说各话。
    指向 live 的软链形态由 `test_leg_b_db_path_symlink_to_live_matches_product` 单独验
    （它还要断言产品自己的前置，塞进参数表会让两条判据混在一起）。
    """
    verdict, _reason = judge_db_path_value(repr(form))
    assert verdict == expected, "守卫对 %r 的结论=%r，期望=%r" % (form, verdict, expected)

    product = _product_resolver()
    saved = os.environ.get("DB_PATH")
    try:
        os.environ["DB_PATH"] = form
        externalized = bool(product.externalized_db_path())
        derived = product.derived_rag_dir_from_db()
    finally:
        if saved is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = saved
    from_product = "uncovered" if (not externalized or
                                   _path_inside(realpath_of(_REPO_ROOT),
                                                realpath_of(derived))) else "covered"
    assert verdict == from_product, (
        "守卫结论与产品口径分叉（审核第 2 条的「第二份实现」又出现了）：form=%r "
        "守卫=%r 产品+附加策略=%r externalized=%r derived=%r"
        % (form, verdict, from_product, externalized, derived))


def test_leg_b_db_path_symlink_to_live_matches_product(tmp_path):
    """指向 live 库的软链：产品按 realpath 判「未外置」，守卫必须同样判 uncovered。

    审核第 2 条的实测分叉点：旧守卫只看字符串前缀，把这条判成 covered ——
    于是「DB_PATH=<tmp>/link.db → demo/receipt_demo.db」的运行，向量会照旧落进
    `demo/.rag_chroma`，而守卫清单里它是「已被派生分支兜住」的绿。
    """
    link = str(tmp_path / "link_to_live.db")
    os.symlink(_LIVE_DB, link)
    try:
        product = _product_resolver()
        saved = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = link
            product_says_externalized = bool(product.externalized_db_path())
        finally:
            if saved is None:
                os.environ.pop("DB_PATH", None)
            else:
                os.environ["DB_PATH"] = saved
        assert product_says_externalized is False, (
            "前置失效：产品现在把指向 live 的软链算作外置了，本用例的对照需要重审")
        verdict, reason = judge_db_path_value(repr(link))
        assert verdict == "uncovered", (
            "守卫把「指向 live 的软链」判成 %s（%s）—— 与产品分叉，这条污染通道会被记成绿"
            % (verdict, reason))
    finally:
        os.remove(link)


def test_leg_b_module_time_repo_internal_db_path_is_uncovered(tmp_path):
    """审核第 7 条：模块期 `DB_PATH=<repo>/demo/tmp_case.db` 必须判 uncovered。

    腿 A 的 `_judge_store_target` 有「落点在仓库内即违规」这条，B5 早期没有 ——
    于是「派生分支确实触发了，但把向量目录派生进了仓库」这种形态在清单里是绿的。
    本用例在 /tmp 的一个**结构等同**的假仓库上跑（不碰真仓库的目录树）。
    """
    fake_repo = tmp_path / "fake_repo"
    services = fake_repo / "app" / "services"
    services.mkdir(parents=True)
    shutil.copyfile(_RAG_PATHS_REPO, str(services / "rag_paths.py"))
    cases_dir = fake_repo / "tests"
    cases_dir.mkdir()
    (cases_dir / "conftest_like.py").write_text(
        'import os\n'
        'os.environ["DB_PATH"] = %r\n' % str(fake_repo / "tests" / "tmp_case.db"),
        encoding="utf-8")
    records, perr = scan_db_path_entry_points(
        [str(cases_dir)], repo_root=str(fake_repo),
        product=_product_resolver(str(services / "rag_paths.py")))
    assert perr == [] and records, "假仓库没扫出记录，本用例失去意义：%r" % records
    for r in records:
        assert r["status"] == "uncovered", (
            "DB_PATH 在仓库内、派生向量目录也在仓库内，却被判 %s（审核第 7 条未修）：%s"
            % (r["status"], r))
    # 同一份取值放到仓库外就该 covered —— 证明判据不是「一律判红」
    (cases_dir / "outside_like.py").write_text(
        'import os\n'
        'os.environ["DB_PATH"] = %r\n' % str(tmp_path / "outside" / "case.db"),
        encoding="utf-8")
    out_records, _ = scan_db_path_entry_points(
        [str(cases_dir)], repo_root=str(fake_repo),
        product=_product_resolver(str(services / "rag_paths.py")))
    outside = [r for r in out_records if r["file"].endswith("outside_like.py")]
    assert outside and all(r["status"] == "covered" for r in outside), \
        "仓库外的同形态取值没有回到 covered（判据过严）：%s" % outside


def test_leg_b_scanners_return_zero_for_missing_input(tmp_path):
    """对照：换一个不存在的输入（以及空目录），各扫描器的命中数必须为 0。"""
    missing = [str(tmp_path / "does_not_exist_at_all")]
    empty_dir = tmp_path / "empty_pkg"
    empty_dir.mkdir()

    assert scan_default_rag_dir_assembly(missing) == []
    assert scan_priority_chain_fallbacks(missing) == []
    assert scan_store_target_pointed_at_live(missing) == []
    rec, perr = scan_db_path_entry_points(missing)
    assert rec == [] and perr == []

    assert scan_default_rag_dir_assembly([str(empty_dir)]) == []
    assert scan_priority_chain_fallbacks([str(empty_dir)]) == []
    assert scan_store_target_pointed_at_live([str(empty_dir)]) == []
    rec, perr = scan_db_path_entry_points([str(empty_dir)])
    assert rec == [] and perr == []

    assert _judge_store_target(str(empty_dir / "x.rag_chroma"),
                               str(empty_dir / "x.db"), live_dirs=()) == []

    # 本波新增/改写的辅助函数同样要有「空输入」对照，否则它们的零命中同样不可信
    assert _iter_env_writes([]) == []
    assert _iter_env_writes(ast.parse("x = 1\n").body[0]) == []
    assert _name_bindings_with_default_dir_literal(ast.parse("x = 1\n")) == set()
    assert _expression_has_path_hint(ast.parse("1", mode="eval").body) is False
    assert _concrete_literal_path("os.path.join(a, 'b')") is None
    assert _db_fingerprint(str(empty_dir / "nope.db")) == {"main": None, "wal": None,
                                                           "shm": None}
    assert _path_inside("", "/tmp/x") is False
    assert realpath_of("") == ""

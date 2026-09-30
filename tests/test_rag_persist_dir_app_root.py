# -*- coding: utf-8 -*-
"""P8a-R7 回归：RAG 向量目录缺省解析改为**应用根基准**，且只保留一份实现。

背景（P6·X2 审计 D1，severity high；同时是 R7「写错 store」与 H3「测试写 live 向量库」
的同源根因）：
- `rag.py` 的 `PERSIST_DIR = os.environ.get("RAG_DIR", "./.rag_chroma")` 是 **cwd 相对**
  的 —— 生产从 `demo/` 起进程写 `demo/.rag_chroma`，而从仓库根起的脚本写
  `<repo>/.rag_chroma`：同一 memory_id 的向量可能落进另一个 store，巡检随即报
  「DB 有行、向量缺失」。
- 巡检工具 `memory_consistency._resolve_rag_dir()` **复刻了同一份口径** —— 只改
  `rag.py` 会让巡检在非 `demo/` cwd 下按另一条相对路径找库，给出「一致 / 缺失」的
  **假结论**。

本文件钉四件事：
1. 两个不同 cwd（仓库根与 `demo/`）下分别 import，`rag.PERSIST_DIR` 与
   `memory_consistency._resolve_rag_dir()` 都解析为**同一绝对路径** `<demo>/.rag_chroma`；
2. 显式 `RAG_DIR` 覆盖仍优先、且两处都尊重它（测试隔离依赖它，不得被破坏）；
3. 两处**共用同一实现**（不是「两份复刻恰好相等」）：替换唯一实现后巡检结果随之改变；
4. 全仓生产代码（`demo/app/**`）不得再出现 cwd 相对缺省口径的字符串常量
   —— 即「是否还有第三处」由用例而非肉眼保障。

隔离与零写入（红线）
--------------------
探针子进程把 `DB_PATH` 指向临时库：`import app.services.rag` 会连带 `app.db`，其
import 期迁移块会按 `DB_PATH` 执行 ALTER，绝不能让 `DB_PATH` 落回 live。

缺省解析必然指向生产向量库 `<demo>/.rag_chroma`（这正是修复后的设计），因此**不能**
用「live 向量库 md5 前后不变」作零写入证据：本仓多波次并行时，别的进程可能在这段窗口
内改动它，那会把外部写入误记成本用例的副作用（已实测遇到一次）。本用例改用**结构性
证明**：探针进程内断言 `rag._vs is None` / `rag._vs_tenant == {}` —— 只要没有打开过
Chroma 客户端，就不可能有任何向量写入；这既不依赖 live 状态，也不受并发写者影响。
"""

import ast
import json
import os
import subprocess
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
_EXPECTED_DEFAULT = os.path.join(_DEMO_DIR, ".rag_chroma")

# 探针：在子进程里 import 两侧的解析入口并打印结果 + 进程内 Chroma 句柄状态。
# why 必须用子进程：`rag.PERSIST_DIR` 与 conftest 的 autouse 夹具都在 import / 夹具期
# 就固定了取值，只有「换一个 cwd 重新 import 一次」才能真正验证 cwd 无关性。
_PROBE = (
    "import json, sys;"
    "sys.path.insert(0, sys.argv[1]);"
    "from app.services import rag, rag_paths;"
    "from app.services import memory_consistency as mc;"
    "print(json.dumps({'rag': rag.PERSIST_DIR,"
    " 'mc': mc._resolve_rag_dir(),"
    " 'default': rag_paths.default_rag_dir(),"
    " 'vs': rag._vs is None,"
    " 'vs_tenant': rag._vs_tenant}))"
)


def _resolve_in_subprocess(cwd, rag_dir_env, tag):
    """在 cwd 下 import 两侧解析入口，返回探测结果 dict。

    - `rag_dir_env=None` 表示**移除** `RAG_DIR`（验证缺省口径）；
    - `DB_PATH` 恒指向临时库（见模块 docstring 的红线说明）。
    """
    env = dict(os.environ)
    env["DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="ak1_db_%s_" % tag), "resolve.db")
    if rag_dir_env is None:
        env.pop("RAG_DIR", None)
    else:
        env["RAG_DIR"] = rag_dir_env
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, _DEMO_DIR],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, \
        "子进程失败(cwd=%s): %s" % (cwd, proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _assert_no_chroma_opened(out):
    """结构性零写入证据：探针进程从未打开过 Chroma 客户端。"""
    assert out["vs"] is True, "探针不得打开 Chroma 客户端（一旦打开就可能写 live 向量库）"
    assert out["vs_tenant"] == {}, "探针不得为任何租户打开 Chroma 客户端"


# -------------------------------------------------------------
# (a) 两个不同 cwd 下 import → 同一绝对路径
# -------------------------------------------------------------
def test_default_resolves_to_same_abs_path_from_two_cwds():
    root_out = _resolve_in_subprocess(_REPO_ROOT, None, "root")
    demo_out = _resolve_in_subprocess(_DEMO_DIR, None, "demo")

    assert root_out["rag"] == demo_out["rag"] == _EXPECTED_DEFAULT, \
        "rag.PERSIST_DIR 缺省必须与 cwd 无关且等于 <demo>/.rag_chroma：root=%r demo=%r" % (
            root_out["rag"], demo_out["rag"])
    assert root_out["mc"] == demo_out["mc"] == _EXPECTED_DEFAULT, \
        "巡检工具的缺省解析必须与 rag 一致：root=%r demo=%r" % (
            root_out["mc"], demo_out["mc"])
    assert root_out["default"] == _EXPECTED_DEFAULT
    assert os.path.isabs(root_out["rag"]) and os.path.isabs(root_out["mc"]), \
        "缺省必须是绝对路径（否则仍然依赖 cwd）"

    _assert_no_chroma_opened(root_out)
    _assert_no_chroma_opened(demo_out)


# -------------------------------------------------------------
# (b) 显式 RAG_DIR 覆盖仍优先，且两处都尊重
# -------------------------------------------------------------
def test_explicit_rag_dir_override_wins_in_both_modules():
    override = tempfile.mkdtemp(prefix="ak1_override_rag_")

    root_out = _resolve_in_subprocess(_REPO_ROOT, override, "root_ovr")
    demo_out = _resolve_in_subprocess(_DEMO_DIR, override, "demo_ovr")

    for out in (root_out, demo_out):
        assert out["rag"] == os.path.abspath(override), \
            "RAG_DIR 显式覆盖必须优先于缺省：%r" % (out["rag"],)
        assert out["mc"] == os.path.abspath(override), \
            "巡检工具必须同样尊重 RAG_DIR：%r" % (out["mc"],)
        _assert_no_chroma_opened(out)

    # 覆盖目录里不得被探针建出向量库（连目录都不该被打开/创建）
    assert not os.path.exists(os.path.join(override, "chroma.sqlite3")), \
        "只读探针不得在 RAG_DIR 下创建向量库"


# -------------------------------------------------------------
# (c) 两处共用同一实现（不是两份复刻恰好相等）
# -------------------------------------------------------------
def test_both_modules_share_one_implementation(monkeypatch):
    """替换唯一实现后，巡检的解析结果必须随之改变。

    why 这条比「两处结果相等」硬：若巡检自己复刻了一份解析逻辑，改 `rag_paths` 不会
    影响它 —— 那正是 P6·X2 的 D1 形态（同一口径多份实现只改一份）。这里同时钉住
    `rag.py` 的源码级调用点，防止将来有人把 `os.environ.get("RAG_DIR", ...)` 写回去。
    """
    import app.services.rag_paths as rag_paths
    from app.services import memory_consistency as mc

    monkeypatch.delenv("RAG_DIR", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    assert mc._resolve_rag_dir() == rag_paths.default_rag_dir() == _EXPECTED_DEFAULT

    monkeypatch.setattr(rag_paths, "resolve_rag_dir",
                        lambda *a, **k: "/tmp/__delegated__")
    assert mc._resolve_rag_dir() == "/tmp/__delegated__", \
        "巡检工具必须委托 rag_paths 的唯一实现，而不是自己复刻一份"

    rag_src = open(os.path.join(_DEMO_DIR, "app", "services", "rag.py"),
                   encoding="utf-8").read()
    assert "rag_paths.resolve_rag_dir()" in rag_src, \
        "rag.py 的 PERSIST_DIR 必须走唯一实现 rag_paths.resolve_rag_dir()"


# -------------------------------------------------------------
# (d) 不存在第三处：生产代码不得再有 cwd 相对缺省口径的字符串常量
# -------------------------------------------------------------
def _cwd_relative_rag_default_literals(root=None):
    """扫 root（缺省 `demo/app/**`）下的 `*.py`，返回含 cwd 相对缺省口径字符串常量的位置。

    why 用 AST 的字符串常量而不是 grep：注释里为了说明历史会引用旧表达式
    （例如 `os.environ.get("RAG_DIR", "./.rag_chroma")` 的注释），grep 会把它们当残留
    误报；AST 只看真实字符串常量，命中的一定是「代码里又写了一份口径」。

    root 可传入临时目录 —— 反证用例用同一个函数扫一份故意写坏的实现，保证扫描器
    本身被验证过（否则「零命中」可能只是扫描器失效）。
    """
    base = root or os.path.join(_DEMO_DIR, "app")
    offenders = []
    needle = "./.rag_chroma"
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == needle:
                    offenders.append("%s:%d" % (os.path.relpath(path, base),
                                                getattr(node, "lineno", 0)))
    return sorted(set(offenders))


def test_no_third_copy_of_cwd_relative_rag_default():
    offenders = _cwd_relative_rag_default_literals()
    assert offenders == [], \
        "生产代码里又出现了 cwd 相对缺省向量目录口径（①类「多份实现」）：%s" % offenders


def test_third_copy_scanner_is_not_tautological(tmp_path):
    """反证：同一个扫描函数必须能抓到一份真的复刻，且对干净实现返回空。

    why：本项目反复踩过「恒真用例比没有更糟」（给了已被守护的假象）。这里用**同一个**
    `_cwd_relative_rag_default_literals` 扫两份临时实现：一份旧口径必须命中，一份走
    `rag_paths` 的干净实现必须零命中 —— 两个方向都钉住。
    """
    legacy = tmp_path / "legacy_rag.py"
    legacy.write_text(
        '# -*- coding: utf-8 -*-\n'
        'import os\n'
        'PERSIST_DIR = os.environ.get("RAG_DIR", "./.rag_chroma")\n',
        encoding="utf-8")
    clean = tmp_path / "clean_rag.py"
    clean.write_text(
        '# -*- coding: utf-8 -*-\n'
        'from app.services import rag_paths\n'
        'PERSIST_DIR = rag_paths.resolve_rag_dir()\n'
        '# 历史口径曾写作 ./.rag_chroma（注释里出现不算命中）\n',
        encoding="utf-8")

    assert _cwd_relative_rag_default_literals(str(tmp_path)) == ["legacy_rag.py:3"], \
        "扫描器判据失效：既没抓到故意写下的旧口径，或把注释/干净实现误报为命中"

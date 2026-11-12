# -*- coding: utf-8 -*-
"""P0 收口波 T3：`demo/conftest.py` 的向量库隔离夹具必须有**本目录**的证据用例。

背景（上一轮审核 mustFix 第 8 条 / 计划 §3 T3 第二项交付）
--------------------------------------------------------
`_isolate_rag_dir` / `_sync_rag_dir` / `_loaded_rag_modules` 在 `tests/conftest.py` 与
`demo/conftest.py` 各有一份实现（pytest 只加载各目录自己的 conftest，不做跨目录夹具继承，
见 demo/conftest.py:523-524 的自述），但证据用例此前只有 tests 一侧 ——
`tests/test_conftest_rag_isolation.py:31` 的 `_CONFTEST_PATH` 只指向 tests 那个文件。
于是 demo/conftest.py:431/452/468 这三份夹具逻辑**无人消费**：把它改坏、整段删掉，
全仓不会有任何用例翻红。本文件把这三份逻辑在 demo 侧钉住。

覆盖什么
--------
1. **G1 关键时序**：本文件在**模块 import 期**就设 `os.environ["DB_PATH"]`（复刻
   `demo/tests/test_uat_031_sku_merge_batch_migration.py:29` 的形态，早于任何 per-test
   autouse 夹具），并在同一窗口里留下一个指向别处的 `CHROMA_PATH`（上一轮 mustFix 第 3
   条实测：宿主环境残留该键会**越级压过**夹具设的 `RAG_DIR`，审计沙箱里
   `rag.PERSIST_DIR` 就被这样劫走过）。夹具必须把向量落点压回本轮 tmp_path。
2. **夹具契约（两处 conftest 各钉一次）**：直接驱动夹具函数本体，在 setup 前塞进
   `CHROMA_PATH`，要求 setup 之后它不存在、且产品唯一实现 `rag_paths.resolve_rag_dir()`
   给出的落点等于夹具设的目录。回滚任一处 `delenv("CHROMA_PATH")` → 对侧立即翻红。
3. **交付物定点**：脏缓存 `_vs` / `_vs_tenant` 必须真被清、别名模块必须按**源码路径**
   而非模块名被识别（tests 侧同款判据，指向 demo 侧实现）。
4. **实证**：夹具生效期间 `rag.ingest_memory` 产出的向量落在本轮 tmp_path，两个 live
   向量库的 `chroma.sqlite3` 在用例前后 md5 逐字节不变。

为什么文件名带 `demo_` 前缀（而不是审核建议的 `test_conftest_rag_isolation.py`）
------------------------------------------------------------------------------
与 `tests/test_conftest_rag_isolation.py` 同名会让合跑**直接中断**：本仓没有 pytest ini
（既没开 `--import-mode=importlib`，`tests/` 与 `demo/tests/` 也都没有 `__init__.py`），
默认 prepend 模式下两个目录里的同名模块只能存在一份。实测把两侧同名的这两个文件放在
同一条 pytest 命令里，collection 阶段就 `Interrupted: 1 error during collection`
（"import file mismatch"）。仓库里已有两处同款地雷（`test_engine_cards_unification_standard.py`
与 `test_gt_review_api.py` 各自合跑时报 15 / 25 个 "fixture not found"），不该再添第三处，
故本文件取唯一名。要沿用审核建议的名字，得先给全仓配 `import-mode=importlib`，那是另一件事。

自校准（判据不恒真的机器证据）
------------------------------
- `_autouse_fixture_hits(cf, "<不存在的夹具名>")` 必须为 0，`"_isolate_rag_dir"` 必须为 1
  （见 `test_autouse_fixture_lookup_self_calibrates`）；
- 把 `demo/conftest.py` 的 `monkeypatch.delenv("CHROMA_PATH", raising=False)` 摘掉 →
  `test_fixture_contract_neutralises_high_priority_chroma_path[demo]` 翻红；
- 把夹具的 `_sync_rag_dir(...)` 调用摘掉 → `test_import_period_loaded_rag_is_rewritten` 翻红。

红线①：跑本文件前必须 `export DB_PATH` 与 `RAG_DIR` 指向 /tmp，并确认 `CHROMA_PATH`
没有残留指向仓库内（三个键里任何一格指向 live 目录都可能把向量写进
`demo/.rag_chroma`）。全程离线 hash 嵌入档、假数据，零外部调用、零 live 写入。
"""

import hashlib
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import types

import pytest

_DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.abspath(os.path.join(_DEMO_DIR, os.pardir))
_CONFTEST_PATH = os.path.join(_DEMO_DIR, "conftest.py")
_TESTS_CONFTEST_PATH = os.path.join(_REPO_ROOT, "tests", "conftest.py")

if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

# 两个 live 向量库目录（此处独立复算，不从 conftest 取，避免「用被测实现证明被测实现」）。
_LIVE_RAG_DIRS = (
    os.path.join(_DEMO_DIR, ".rag_chroma"),
    os.path.join(_REPO_ROOT, ".rag_chroma"),
)
_LIVE_CHROMA_FILES = tuple(os.path.join(d, "chroma.sqlite3") for d in _LIVE_RAG_DIRS)

# -----------------------------------------------------------------------
# 模块 import 期就动环境变量 —— 这正是 G1 的事故形态（早于任何 per-test 夹具）
# -----------------------------------------------------------------------
# 落点全部在 /tmp：本文件绝不把向量指回仓库内。
_SANDBOX = tempfile.mkdtemp(prefix="vm_t3_demo_conftest_rag_")
_MODULE_DB_PATH = os.path.join(_SANDBOX, "module_period.db")
# 模拟宿主 shell / CI 里残留的高优先级键（优先级高于夹具设的 RAG_DIR）。
_STRAY_CHROMA_PATH = os.path.join(_SANDBOX, "stray_host_chroma")

_ENV_AT_IMPORT = {name: os.environ.get(name) for name in ("DB_PATH", "CHROMA_PATH", "RAG_DIR")}
os.environ["DB_PATH"] = _MODULE_DB_PATH
os.environ["CHROMA_PATH"] = _STRAY_CHROMA_PATH

import app.services.rag as rag  # noqa: E402  （就在设完这两个键之后 import：夹具必须兜住它）

# 此刻 rag 已加载并固化了一个 PERSIST_DIR（值取决于它此前是否已被别的模块带进来），
# per-test 夹具还没跑过任何一次 —— 这就是要兜住的窗口。
_PERSIST_DIR_AT_IMPORT = os.path.abspath(str(rag.PERSIST_DIR))


def _load_conftest_by_path(label, path):
    """按文件路径加载 conftest（装载器形态照抄
    demo/tests/test_conftest_db_isolation_judgement.py:34-45）。

    pytest 只把 conftest 当配置加载，测试里要直接引用它的辅助函数与夹具本体，
    故显式按路径载入一份独立的模块对象，不借用 sys.modules 里的插件实例。
    """
    spec = importlib.util.spec_from_file_location("conftest_under_test_%s" % label, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def demo_cf():
    return _load_conftest_by_path("demo", _CONFTEST_PATH)


@pytest.fixture(scope="module")
def tests_cf():
    return _load_conftest_by_path("tests", _TESTS_CONFTEST_PATH)


@pytest.fixture(scope="module", autouse=True)
def _cleanup_import_period_env():
    """把本文件在模块 import 期改过的环境变量还给进程。

    只在**收尾**还原，setup 什么都不做 —— 否则「夹具清掉了 CHROMA_PATH」这件事就成了
    本文件自己造成的假象（自证恒真）。setup 保持惰性，第一个用例的 autouse 夹具才是
    唯一把残留键清掉的代码。
    """
    yield
    for name, value in _ENV_AT_IMPORT.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(_SANDBOX, ignore_errors=True)


def _fixture_function(fixture_definition):
    """取夹具的函数本体（pytest 8 起 @pytest.fixture 返回 FixtureFunctionDefinition）。

    拿不到就 assert 失败而不是静默跳过：pytest 换内部结构时这条判据必须**响**，
    不能变成「扫不到就算通过」。
    """
    for attr in ("_fixture_function", "__wrapped__", "_original_func"):
        func = getattr(fixture_definition, attr, None)
        if callable(func):
            return func
    raise AssertionError("无法取到夹具函数本体（pytest 内部结构变更？），本用例判据不可信")


def _fixture_marker(fixture_definition):
    """夹具的 marker（scope / autouse），兼容 pytest 7 与 8 两种载体。"""
    return (getattr(fixture_definition, "_fixture_function_marker", None)
            or getattr(fixture_definition, "_ispytestfixturefunction", None))


def _autouse_fixture_hits(module, name):
    """conftest 模块里名为 name 且标了 autouse 的夹具数量（自校准用的计数）。"""
    obj = getattr(module, name, None)
    if obj is None:
        return 0
    marker = _fixture_marker(obj)
    return 1 if (marker is not None and getattr(marker, "autouse", False)) else 0


def _drive_fixture(fixture_definition, sandbox_root, monkeypatch):
    """直接驱动一次夹具的 setup 阶段，返回 (target, teardown_callable)。

    为什么要点火驱动而不是只读用例里的 env：`CHROMA_PATH` 的越级发生在**夹具 setup
    之前**，而 per-test 夹具在每个用例开始时早就跑完了，用例体内已经看不到那个窗口。
    驱动本体就能在受控窗口里复现「宿主残留高优先级键 → 夹具是否清掉」。
    """
    func = _fixture_function(fixture_definition)
    generator = func(pathlib.Path(str(sandbox_root)), monkeypatch)
    next(generator)  # 跑到 yield，即 setup 完成
    return os.environ.get("RAG_DIR"), generator.close


# ---------------------------------------------------------------
# 1. 自校准：夹具登记计数不可能是恒真
# ---------------------------------------------------------------
def test_autouse_fixture_lookup_self_calibrates(demo_cf):
    """计数工具必须「真名命中 1、假名命中 0」，否则后面的结构断言毫无意义。

    上一轮审核抓到过「只做 `{n.name for n in ast.walk(tree)}`、只验名字」的准恒真判据，
    这里先把尺子校准一遍：换一个不存在的夹具名 → 命中数必须为 0。
    """
    assert _autouse_fixture_hits(demo_cf, "_isolate_rag_dir") == 1, \
        "demo/conftest.py 的向量隔离夹具必须是 autouse 夹具（否则兜不住任何用例）"
    assert _autouse_fixture_hits(demo_cf, "_isolate_rag_dir_bogus_name_v9") == 0, \
        "不存在的夹具名却命中了，说明计数工具本身不可信"
    # 反向：普通辅助函数不得被当成夹具
    assert _autouse_fixture_hits(demo_cf, "_sync_rag_dir") == 0, \
        "_sync_rag_dir 是夹具内部函数，不是夹具"
    assert _autouse_fixture_hits(demo_cf, "_loaded_rag_modules") == 0, \
        "_loaded_rag_modules 是夹具内部函数，不是夹具"


def _is_inside_repo(path):
    """路径是否落在仓库树内（含 live 向量库与 live 库）。"""
    return path == _REPO_ROOT or path.startswith(_REPO_ROOT + os.sep)


# ---------------------------------------------------------------
# 2. G1 关键时序：模块 import 期就设 DB_PATH / 就 import rag 的文件也能被夹具兜住
# ---------------------------------------------------------------
def test_import_period_loaded_rag_is_rewritten(tmp_path):
    """本文件在 import 期就把 rag 加载了，夹具必须把已加载模块的落点改写回 tmp_path。

    夹具对「import 期」的唯一有效手段就是 `_sync_rag_dir(_loaded_rag_modules(), target)`
    （env 只能管住之后才 import 的模块）。摘掉那行 → 本用例翻红。
    """
    expected = os.path.abspath(os.path.join(str(tmp_path), "rag_chroma"))
    assert os.path.abspath(str(rag.PERSIST_DIR)) == expected, (
        f"rag.PERSIST_DIR 必须被夹具改到本轮临时目录，实际 {rag.PERSIST_DIR}；"
        f"本文件 import 期捕获的是 {_PERSIST_DIR_AT_IMPORT}")
    # 兜住的是「改写」而不是「碰巧还没 import」：import 期的值必然不是本轮目录
    assert _PERSIST_DIR_AT_IMPORT != expected, (
        "import 期捕获的 PERSIST_DIR 竟等于本轮 tmp_path，说明本文件没有真的复现"
        "「夹具跑在 import 之后」这一时序，判据已失去意义")
    # 改写必须连缓存一起清掉，否则沿用旧目录句柄照样写旧库
    assert rag._vs is None and rag._vs_tenant == {}, \
        "进程内 collection 缓存必须清空，否则隔离只做一半"


def test_import_period_env_leaves_no_live_path():
    """import 期这两个键的取值都不许指向仓库内（本文件自身不得成为污染源）。

    这是「先复现再修」的前提：如果模拟残留键本身指向 live，那跑这个文件就是在写 live。
    """
    for name, value in (("DB_PATH", _MODULE_DB_PATH), ("CHROMA_PATH", _STRAY_CHROMA_PATH)):
        resolved = os.path.abspath(value)
        assert resolved.startswith(os.path.abspath(_SANDBOX) + os.sep), \
            f"{name} 的模拟残留必须落在沙箱 {_SANDBOX} 里，实际 {value}"
        assert not _is_inside_repo(resolved), f"{name} 指向仓库内：{value}"


def test_default_vector_dir_is_live_so_the_fixture_is_not_a_no_op():
    """反证「夹具不是空转」：把三个覆盖通道全清空后，产品缺省落点就是 live 向量库。

    没有夹具（也没有任何 env 覆盖）时，任何一次向量写入都会落进
    `demo/.rag_chroma` —— 这正是 live 里那批夹具 collection 的来历（G1）。
    缺省若哪天不再指 live，本用例翻红，上面那两条的「兜住」才有意义。
    """
    from app.services import rag_paths

    assert os.path.abspath(rag_paths.default_rag_dir()) in {
        os.path.abspath(d) for d in _LIVE_RAG_DIRS}, \
        f"缺省向量目录应落在 live 向量库之一，实际 {rag_paths.default_rag_dir()}"
    # 本文件 import 期只设了 DB_PATH（指向 tmp）：派生分支保证「夹具还没跑」时也不写 live
    # （通用派生口径由 tests/test_rag_dir_follows_db_path.py 钉，这里只验本文件那一格取值）
    saved = {k: os.environ.get(k) for k in ("RAG_DIR", "CHROMA_PATH", "DB_PATH")}
    try:
        os.environ.pop("RAG_DIR", None)
        os.environ["DB_PATH"] = _MODULE_DB_PATH
        resolved = rag_paths.resolve_rag_dir()
        assert _is_inside_repo(resolved) is False, \
            f"import 期取值解析到仓库内：{resolved}"
        assert resolved == os.path.abspath(
            os.path.join(_SANDBOX, "module_period.rag_chroma")), f"派生结果异常: {resolved}"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------
# 3. 夹具契约：高优先级的 CHROMA_PATH 不得越级压过夹具设的 RAG_DIR（两处各钉一次）
# ---------------------------------------------------------------
@pytest.mark.parametrize("label", ("tests", "demo"), ids=["tests", "demo"])
def test_fixture_contract_neutralises_high_priority_chroma_path(
        label, request, tmp_path):
    """驱动夹具本体：setup 前塞一个指向别处的 CHROMA_PATH，setup 后它必须不生效。

    判据用的是**产品唯一实现** `rag_paths.resolve_rag_dir()`，不是本用例自己重算的
    优先级表：夹具若只 `setenv("RAG_DIR")` 而不删 `CHROMA_PATH`（上一轮 mustFix 第 3 条
    的漏洞形态），解析器就会返回那个「别处」的目录 → 立即翻红。两处 conftest 是两份
    独立实现，所以各钉一次，改坏任意一处都能定位到是哪一处。
    """
    cf = request.getfixturevalue("%s_cf" % label)
    fixture_definition = getattr(cf, "_isolate_rag_dir", None)
    assert fixture_definition is not None, f"{label}/conftest.py 里找不到 _isolate_rag_dir"

    stray = os.path.abspath(os.path.join(str(tmp_path), "stray_outranking_dir"))
    sandbox_root = os.path.join(str(tmp_path), "driven")
    os.makedirs(sandbox_root, exist_ok=True)
    saved = {k: os.environ.get(k) for k in ("RAG_DIR", "CHROMA_PATH", "DB_PATH")}
    os.environ["CHROMA_PATH"] = stray        # 模拟宿主残留（夹具 setup 之前就在那儿）
    os.environ["DB_PATH"] = _MODULE_DB_PATH
    monkeypatch = pytest.MonkeyPatch()
    teardown = None
    try:
        rag_dir_after_setup, teardown = _drive_fixture(
            fixture_definition, sandbox_root, monkeypatch)
        expected = os.path.abspath(os.path.join(sandbox_root, "rag_chroma"))
        assert "CHROMA_PATH" not in os.environ, (
            f"[{label}] 夹具没清掉高优先级的 CHROMA_PATH，残留值 {stray} 会越级压过 "
            f"RAG_DIR —— 隔离只做一半（上一轮 mustFix 第 3 条）")
        assert rag_dir_after_setup == expected, \
            f"[{label}] 夹具设的 RAG_DIR 应为 {expected}，实际 {rag_dir_after_setup}"
        from app.services import rag_paths
        assert rag_paths.resolve_rag_dir() == expected, (
            f"[{label}] 夹具生效后产品解析出的落点必须等于夹具目录，"
            f"实际 {rag_paths.resolve_rag_dir()}")
        assert os.path.abspath(stray) != expected, \
            "残留键必须真的指向「别处」，否则上面的不生效断言无从谈起"
    finally:
        if teardown is not None:
            teardown()
        monkeypatch.undo()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------
# 4. 交付物定点：脏缓存要真清、别名模块要按源码路径识别（demo 侧实现）
# ---------------------------------------------------------------
def test_sync_rag_dir_clears_dirty_cache(demo_cf, tmp_path):
    """`_sync_rag_dir` 只改常量不清 `_vs` / `_vs_tenant` 等于隔离一半。

    先注入「上一个用例留下的旧目录句柄」再调用，避免新进程首次运行时缓存本来就空
    造成的空转断言（tests 侧同款判据，这里指向 demo 侧那份实现）。
    """
    sentinel = object()
    rag._vs = sentinel
    rag._vs_tenant = {"t1": sentinel}
    rag.PERSIST_DIR = os.path.join(str(tmp_path), "stale_dir_to_be_replaced")

    target = os.path.join(str(tmp_path), "fresh_rag")
    demo_cf._sync_rag_dir([rag], target)
    assert os.path.abspath(str(rag.PERSIST_DIR)) == os.path.abspath(target)
    assert rag._vs is None, "全局 collection 缓存必须被清空"
    assert rag._vs_tenant == {}, "租户 collection 缓存必须被清空"


def test_loaded_rag_modules_matches_alias_by_source(demo_cf, tmp_path):
    """别名模块（同源码、不同模块名）必须一并被发现并改指。

    why：`demo/` 没有 `__init__.py`，`from app.services import rag` 与
    `from demo.app.services import rag` 会各按源码导入一次，得到两个模块实例。只按
    模块名匹配时，别名实例会逃过隔离继续写 live 向量库。用假模块注入 sys.modules，
    不真的 import 别名（避免给整轮会话塞进第二个实例）。
    """
    alias_name = "demo.app.services.rag"
    fake = types.ModuleType(alias_name)
    fake.__file__ = os.path.join(_DEMO_DIR, "app", "services", "rag.py")
    fake.PERSIST_DIR = "stale"
    fake._store = lambda *a, **k: None

    saved = sys.modules.get(alias_name)
    sys.modules[alias_name] = fake
    try:
        assert fake in demo_cf._loaded_rag_modules(), \
            "按源码路径匹配的别名模块必须被发现（demo/conftest.py 那份实现）"
        fake._vs = object()
        fake._vs_tenant = {"t": object()}
        target = os.path.join(str(tmp_path), "alias_rag")
        demo_cf._sync_rag_dir([fake], target)
        assert os.path.abspath(str(fake.PERSIST_DIR)) == os.path.abspath(target)
        assert fake._vs is None and fake._vs_tenant == {}
    finally:
        if saved is None:
            sys.modules.pop(alias_name, None)
        else:
            sys.modules[alias_name] = saved


def test_rag_dir_is_derived_from_per_test_tmp_path(demo_cf, tmp_path):
    """夹具落点必须跟着 tmp_path 走：逐用例独立目录，才消得掉跨用例同名 collection 串扰。

    判据形态：拿两个不同的 sandbox_root 各驱动一次夹具，两次给出的 RAG_DIR 必须互不相同
    且都等于「各自 tmp 子目录 / rag_chroma」；若夹具改成会话级固定目录（或干脆写死
    `<demo>/.rag_chroma`），第一次驱动就会翻红。
    """
    from app.services import rag_paths

    # (a) 本用例在自己这轮夹具里看到的生效落点，就是本轮 tmp_path 派生的那个
    in_session = rag_paths.resolve_rag_dir()
    assert in_session == os.path.abspath(os.path.join(str(tmp_path), "rag_chroma")), in_session
    assert not _is_inside_repo(in_session), f"生效向量目录落在仓库内：{in_session}"

    # (b) 换一个根目录驱动一次夹具，必须得到另一个落点
    roots = [os.path.join(str(tmp_path), "drive_%d" % i) for i in (1, 2)]
    targets = []
    saved = {k: os.environ.get(k) for k in ("RAG_DIR", "CHROMA_PATH")}
    monkeypatch = pytest.MonkeyPatch()
    closers = []
    try:
        for root in roots:
            os.makedirs(root, exist_ok=True)
            target, closer = _drive_fixture(demo_cf._isolate_rag_dir, root, monkeypatch)
            closers.append(closer)
            targets.append(target)
    finally:
        for closer in closers:
            closer()
        monkeypatch.undo()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert targets[0] != targets[1], f"两轮驱动得到同一个目录，隔离不是逐用例的：{targets}"
    for root, target in zip(roots, targets):
        assert os.path.abspath(target) == os.path.abspath(os.path.join(root, "rag_chroma"))


# ---------------------------------------------------------------
# 5. 实证：夹具期间产生的向量落在 tmp，live 的 chroma.sqlite3 逐字节不变
# ---------------------------------------------------------------
def _md5(path):
    """文件 md5；不存在返回 None（用于「不存在 → 仍不存在」的比较）。"""
    if not os.path.exists(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_vectors_written_during_demo_tests_land_in_tmp_not_live(tmp_path):
    """端到端：approve 回写路径（`ingest_memory`）在夹具期间只能写本轮临时目录。

    这一条同时是「live 零污染」的机器证据 —— 两个 live `chroma.sqlite3` 的 md5 前后
    必须逐项相等（含本文件 import 期那个残留键存在的情形）。
    """
    live_before = {p: _md5(p) for p in _LIVE_CHROMA_FILES}

    rag.ingest_memory(
        vendor="隔離探針供應商_demo側",
        items_text="芥蘭 8斤 @11 = 88",
        notes="版式：手寫短單（P0 收口波 T3 demo 側夹具探針）",
        tenant_id="demo_conftest_rag_probe",
    )

    tmp_chroma = os.path.join(str(tmp_path), "rag_chroma", "chroma.sqlite3")
    assert os.path.exists(tmp_chroma), f"向量必须落在临时目录，期望存在 {tmp_chroma}"
    assert os.path.abspath(os.path.dirname(tmp_chroma)) == os.path.abspath(str(rag.PERSIST_DIR))

    live_after = {p: _md5(p) for p in _LIVE_CHROMA_FILES}
    assert live_after == live_before, \
        f"live 向量库 md5 必须逐字节不变，before={live_before} after={live_after}"

# -*- coding: utf-8 -*-
"""P0-T2 回归：conftest 必须把向量库落盘目录隔离到临时目录，禁止测试写 live 向量库。

背景（Gap 05 §H3）：`rag.PERSIST_DIR` 的缺省落点是向量库目录（`app/services/rag_paths.py`
按应用根定位，P8a-R7 前为 cwd 相对 `"./.rag_chroma"`），而 conftest 过去只隔离了 DB 与
图片目录 —— 测试写入会落到 **live 向量库 `demo/.rag_chroma`**（P8a-R7 前从仓库根跑还会
落到 `<repo>/.rag_chroma`）。live 向量库里那批 vendor 为测试夹具名（「测试供应商」/
「測試供應商」）的 orphan 向量即由此而来。

本文件做三件事：
1. 断言 autouse 夹具把 `RAG_DIR` 与 `rag.PERSIST_DIR` 都指向本轮 tmp_path，并
   已清空 `rag._vs` / `rag._vs_tenant` 进程内缓存；
2. 反证：rag 的缺省向量目录确实等于 live 向量库目录（说明上面那条断言
   不是空转 —— 没有夹具时写的就是 live）；
3. 实证：测试期间经 `rag.ingest_memory` 产生的向量文件落在 tmp_path，且两个
   live 向量库的 chroma.sqlite3 在用例前后 md5 逐字节不变。

全程离线假数据（char-n-gram 嵌入），零外部调用。
"""

import hashlib
import importlib.util
import os
import sys
import types

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
_CONFTEST_PATH = os.path.join(_TESTS_DIR, "conftest.py")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.services.rag as rag  # noqa: E402

# 两个 live 向量库目录。此处独立复算（不从 conftest 取），避免「用被测实现证明
# 被测实现」；rag 的缺省落点即其中之一（`demo/.rag_chroma`，见 P8a-R7）。
_LIVE_RAG_DIRS = (
    os.path.join(_DEMO_DIR, ".rag_chroma"),
    os.path.join(_REPO_ROOT, ".rag_chroma"),
)
_LIVE_CHROMA_FILES = tuple(os.path.join(d, "chroma.sqlite3") for d in _LIVE_RAG_DIRS)


def _load_conftest():
    """按文件路径加载 tests/conftest.py，直接断言其辅助函数（同 demo/tests 的既有做法）。"""
    spec = importlib.util.spec_from_file_location("tests_conftest_under_test", _CONFTEST_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _md5(path):
    """文件 md5；文件不存在返回 None（用于「不存在 → 仍不存在」的比较）。"""
    if not os.path.exists(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _effective_rag_dir():
    return os.path.abspath(str(rag.PERSIST_DIR))


# -------------------------------------------------------------
# 1. 夹具效果：env 与模块常量都指向本轮 tmp_path，且缓存已清
# -------------------------------------------------------------
def test_rag_dir_and_persist_dir_point_to_tmp(tmp_path):
    expected = os.path.join(str(tmp_path), "rag_chroma")
    assert os.environ.get("RAG_DIR") == expected, \
        f"RAG_DIR 必须指向本轮临时目录，实际 {os.environ.get('RAG_DIR')}"
    assert os.path.abspath(str(rag.PERSIST_DIR)) == os.path.abspath(expected), \
        f"rag.PERSIST_DIR 必须同步指向本轮临时目录，实际 {rag.PERSIST_DIR}"
    assert rag._vs is None, "进程内全局 collection 缓存必须清空，否则会沿用旧目录"
    assert rag._vs_tenant == {}, "进程内租户 collection 缓存必须清空"


def test_effective_rag_dir_is_not_live_and_inside_tmp(tmp_path):
    assert _effective_rag_dir().startswith(str(tmp_path) + os.sep), \
        f"生效向量目录必须落在 tmp_path 内，实际 {rag.PERSIST_DIR}"
    assert _effective_rag_dir() not in {os.path.abspath(d) for d in _LIVE_RAG_DIRS}, \
        f"生效向量目录绝不能是 live 向量库，实际 {rag.PERSIST_DIR}"


# -------------------------------------------------------------
# 2. 反证：缺省向量目录确实是 live —— 说明修复不是空转
# -------------------------------------------------------------
def test_default_rag_dir_is_a_live_vector_store():
    """rag 的缺省 PERSIST_DIR 落在 live 向量库之一（`demo/.rag_chroma`）。

    P8a-R7 起缺省改为**应用根基准**（不再随 cwd 变），落点仍是 live 向量库
    `demo/.rag_chroma` —— 夹具若不存在，测试写入就会落到这里，这正是要拦的路径。
    """
    from app.services import rag_paths
    default_dir = rag_paths.default_rag_dir()
    assert default_dir in {os.path.abspath(d) for d in _LIVE_RAG_DIRS}, \
        f"缺省向量目录应落在 live 向量库之一，实际 {default_dir}"
    assert _effective_rag_dir() != default_dir, \
        "夹具生效时不应再用缺省的 live 目录"


# -------------------------------------------------------------
# 3. 交付物定点：脏缓存必须被清、别名模块必须被识别
# -------------------------------------------------------------
def test_sync_rag_dir_clears_dirty_cache(tmp_path):
    """缓存清空必须真的生效 —— 只改 PERSIST_DIR 不清 `_vs`/`_vs_tenant` 等于隔离一半。

    这里刻意先注入「上一个用例留下的旧目录句柄」再调用辅助函数，避免上面那条
    「新进程首次运行时缓存本来就是空的」造成空转断言。
    """
    cf = _load_conftest()
    sentinel = object()
    rag._vs = sentinel
    rag._vs_tenant = {"t1": sentinel}
    rag.PERSIST_DIR = "/tmp/stale_rag_dir_should_be_replaced"

    target = str(tmp_path / "fresh_rag")
    cf._sync_rag_dir([rag], target)
    assert os.path.abspath(str(rag.PERSIST_DIR)) == target
    assert rag._vs is None, "全局 collection 缓存必须被清空"
    assert rag._vs_tenant == {}, "租户 collection 缓存必须被清空"


def test_rag_modules_matched_by_source_file_not_module_name(tmp_path):
    """别名模块（同源码、不同模块名，如 demo.app.services.rag）必须一并被识别并改指。

    why：只用模块名匹配时，`from demo.app.services import rag` 得到的第二个实例会
    逃过隔离、继续写 live 向量库 —— 这就是「同一根因多份实例」那类缺陷。
    这里用假模块注入 sys.modules，不真的 import 别名（避免给整轮会话塞进第二个实例）。
    """
    cf = _load_conftest()
    alias_name = "demo.app.services.rag"
    fake = types.ModuleType(alias_name)
    fake.__file__ = os.path.join(_DEMO_DIR, "app", "services", "rag.py")
    fake.PERSIST_DIR = "stale"
    fake._store = lambda *a, **k: None

    saved = sys.modules.get(alias_name)
    sys.modules[alias_name] = fake
    try:
        assert fake in cf._loaded_rag_modules(), "按源码路径匹配的别名模块必须被发现"

        fake._vs = object()
        fake._vs_tenant = {"t": object()}
        target = str(tmp_path / "alias_rag")
        cf._sync_rag_dir([fake], target)
        assert os.path.abspath(str(fake.PERSIST_DIR)) == target
        assert fake._vs is None and fake._vs_tenant == {}
    finally:
        if saved is None:
            sys.modules.pop(alias_name, None)
        else:
            sys.modules[alias_name] = saved


# -------------------------------------------------------------
# 4. 实证：测试期间产生的向量落 tmp_path，live 向量库 md5 不变
# -------------------------------------------------------------
def test_vectors_written_during_test_land_in_tmp_not_live(tmp_path):
    live_before = {p: _md5(p) for p in _LIVE_CHROMA_FILES}

    rag.ingest_memory(
        vendor="隔離探針供應商",
        items_text="菜心 10斤 @8 = 80",
        notes="版式：手寫長單（P0-T2 隔離探針）",
        tenant_id="rag_isolation_probe",
    )

    tmp_chroma = os.path.join(str(tmp_path), "rag_chroma", "chroma.sqlite3")
    assert os.path.exists(tmp_chroma), \
        f"测试产生的向量必须落在临时目录，期望存在 {tmp_chroma}"
    assert os.path.abspath(os.path.dirname(tmp_chroma)) == _effective_rag_dir()

    live_after = {p: _md5(p) for p in _LIVE_CHROMA_FILES}
    assert live_after == live_before, \
        f"live 向量库 md5 必须逐字节不变，before={live_before} after={live_after}"

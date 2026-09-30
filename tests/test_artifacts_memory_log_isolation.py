# -*- coding: utf-8 -*-
"""Q3-M4 补充回归：**生产写入路径**（supervisor._finalize）必须走隔离落点。

与 tests/test_artifacts_isolation_root.py 的分工（不重复）：
- 那份断言「落点被重定向」+「直接往落点写一行后仓库文件指纹不变」；
- 本份补上它覆盖不到的一半：**真调一次 `_finalize`**（识别管线收尾，生产上唯一往
  artifacts/memory/parse_log.jsonl 追加的代码路径），证明「落盘走的就是
  `_memory_log_path()`」这条接线真的存在。只测前者时，若哪天 `_finalize` 改成
  自己拼路径（不再调用 `_memory_log_path()`），那份用例仍会通过（写的是测试自己
  指定的路径）—— 本仓②类缺陷「定义了没接线」正是这个形态。

另附一条反证：按 supervisor.py 的同一口径独立复算缺省落点，确认它就在仓库的
artifacts/memory 下 —— 说明「重定向」不是空转（没有夹具时写的就是仓库）。

全部只断言行为，不绑定具体重定向机制（模块常量改指或 `ARTIFACTS_DIR` 环境变量），
以免下游换实现时用例假失败。
"""

import os
import sys
import time

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

from app.chains import supervisor  # noqa: E402

# 仓库内落点：独立复算（不从 supervisor 取当前值 —— 夹具已把它改指 tmp），
# 口径与 supervisor.py 的 _MEMORY_LOG_PATH 一致（chains/ 往上三级到仓库根）。
_REPO_PARSE_LOG = os.path.join(_REPO_ROOT, "artifacts", "memory", "parse_log.jsonl")
_DEFAULT_PATH_BY_SOURCE = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(supervisor.__file__)),
    "..", "..", "..", "artifacts", "memory", "parse_log.jsonl"))


def _read_text(path):
    """读文本；文件不存在返回 None（用于「不存在 → 仍不存在」的比较）。"""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _finalize_once(supplier_marker):
    """走一次生产写入路径：`_finalize` 的状态字段取最小集。

    receipt_id=None ⇒ `_reflow_from_state` 首行即 return（不回流评测候选）；
    data=None ⇒ success=False，但不影响文件层落盘（那段与成功与否无关）。
    """
    state = {"attempt": 1, "status": "parsed", "data": None, "receipt_id": None,
             "supplier": supplier_marker, "doc_form": "", "image_path": "",
             "use_grey": False}
    supervisor._finalize(state, time.time())


def test_default_path_would_be_inside_repo_so_redirect_is_not_vacuous():
    """反证：没有夹具时，落点就是仓库内 artifacts/memory/parse_log.jsonl。"""
    assert _DEFAULT_PATH_BY_SOURCE == os.path.abspath(_REPO_PARSE_LOG), \
        "按源码复算的缺省落点应与仓库内路径一致，实际 %s" % _DEFAULT_PATH_BY_SOURCE
    assert _DEFAULT_PATH_BY_SOURCE.startswith(_REPO_ROOT + os.sep)
    assert os.path.abspath(supervisor._memory_log_path()) != _DEFAULT_PATH_BY_SOURCE, \
        "夹具生效时不应再用缺省的仓库内落点"


def test_finalize_writes_into_isolated_file_and_not_into_repo(tmp_path):
    """生产写入路径落在隔离落点，且本次调用没有往仓库内写下任何标记。"""
    marker = "q3-m4-finalize-probe-%d" % os.getpid()
    isolated = os.path.abspath(supervisor._memory_log_path())
    assert isolated.startswith(str(tmp_path) + os.sep), \
        "隔离落点必须在本轮 tmp_path 内，实际 %s" % isolated

    _finalize_once(marker)

    assert os.path.exists(isolated), "生产写入路径必须落到隔离落点文件 %s" % isolated
    with open(isolated, "r", encoding="utf-8", errors="replace") as f:
        isolated_text = f.read()
    assert marker in isolated_text, \
        "本次 _finalize 的记录必须出现在隔离落点里（否则说明 _finalize 不再走 _memory_log_path()）"

    repo_text_after = _read_text(_REPO_PARSE_LOG)
    assert marker not in (repo_text_after or ""), \
        "仓库内 parse_log.jsonl 不得出现本次调用写下的标记（测试期间无仓库内写入）"

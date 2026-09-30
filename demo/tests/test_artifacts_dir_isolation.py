# -*- coding: utf-8 -*-
"""Q3-M4 回归：测试期间不得往仓库内 artifacts/ 追加写。

why：supervisor 的 `_finalize` 每完成一次识别就向仓库内
`artifacts/memory/parse_log.jsonl` **追加**一行，而该落点按模块 __file__ 计算，
DB / RAG / 图片三条隔离面都覆盖不到它 —— 修复前任何真正跑识别链路的用例（含门禁、
重试、租户等不调 VLM 的分支用例）都会往仓库里留下记录（实测该文件已累积 1.4MB）。

本用例覆盖 demo/conftest.py 的 `_isolate_artifacts_dir`（与 tests/conftest.py 同名
夹具互为镜像，故两处各有一份断言）。断言两件事：
1. `supervisor._memory_log_path()` 落在本轮 tmp_path 下、且不在仓库 artifacts/ 内；
2. 往该路径真写一行后，仓库内 artifacts/memory/parse_log.jsonl 的存在性 / 大小 /
   md5 三项完全不变（三项一起给，避免「文件在但内容变了」被大小骗过）。
"""

import hashlib
import os

# demo/tests/<file> → 仓库根
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ARTIFACTS = os.path.abspath(os.path.join(_REPO_ROOT, "artifacts"))
_REPO_LOG = os.path.join(_REPO_ARTIFACTS, "memory", "parse_log.jsonl")


def _fingerprint(path):
    """返回 (是否存在, 字节数, md5)；不存在时后两项为 None。"""
    if not os.path.exists(path):
        return (False, None, None)
    with open(path, "rb") as f:
        data = f.read()
    return (True, len(data), hashlib.md5(data).hexdigest())


def test_memory_log_path_redirected_out_of_repo(tmp_path):
    """落盘路径必须被隔离面重定向到本轮临时目录。"""
    from app.chains import supervisor

    p = os.path.abspath(supervisor._memory_log_path())
    assert p.startswith(str(tmp_path) + os.sep), "落盘路径不在本轮临时目录下：%s" % p
    assert not (p == _REPO_LOG or p.startswith(_REPO_ARTIFACTS + os.sep)), \
        "落盘路径落在仓库 artifacts/ 内：%s" % p


def test_memory_log_write_does_not_touch_repo(tmp_path):
    """真写一行后，仓库内记忆日志的三项指纹必须不变。"""
    from app.chains import supervisor

    before = _fingerprint(_REPO_LOG)
    p = supervisor._memory_log_path()
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"q3_m4": "isolation-probe"}\n')

    after = _fingerprint(_REPO_LOG)
    assert after == before, \
        "仓库内 artifacts 记忆日志被写入：before=%r after=%r" % (before, after)

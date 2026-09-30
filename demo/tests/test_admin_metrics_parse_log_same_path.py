# -*- coding: utf-8 -*-
"""AH2：管理台 `/api/admin/metrics` 的 parse_log 读路径必须与写路径**同源**。

why（缺陷形态）：`admin_metrics` 原先用 **4 个按 `__file__` 拼出来的候选路径**读
`artifacts/memory/parse_log.jsonl`，而落盘方 `supervisor._finalize` 走的是
`supervisor._memory_log_path()`（它按 `ARTIFACTS_DIR` 重定向）。于是设了 ARTIFACTS_DIR
的场景（容器 / 测试 / 任何隔离运行）下**写侧落临时目录、读侧仍找仓库**，管理台文件层
tokens / elapsed / success 读到的是陈旧或不存在的那一份 —— 而且「读到了旧文件」与
「本来就没有文件」在读数上同形（都是 0），运维无法分辨。

本文件钉住三条：
1. 功能：往 `_memory_log_path()` 指的文件写两行 → 端点必须读到这两行，且
   `data.file.path` 就是该路径（旧实现会读到仓库那份 → count=0，故本用例即是判别式）。
2. 跟随：`ARTIFACTS_DIR` 变更 / `_memory_log_path` 被替换时，读侧随之改变
   （证明它**调用**同一函数，而不是各算各的）。
3. 结构：端点函数体里不得再出现任何 `__file__` 候选路径解析（本仓①类缺陷：
   同一根因多份实现 —— 这里的两份是「写侧 helper」与「读侧候选表」）。
   并断言写侧 `_finalize` 仍走 `_memory_log_path()`（读写两侧同源的另一半）。

反证口径（交付说明给实测记录与三个 md5，不写进用例）：把读路径改回 `__file__` 候选
实现 → 第 1 条必须失败（仓库那份不存在 → count=0 且无 path 字段）。

隔离：DB_PATH / RAG_DIR 指向临时目录；`ARTIFACTS_DIR` 由 demo/conftest.py 的 autouse
夹具指向本轮 tmp_path，故本文件不会往仓库内 artifacts/ 写任何字节。
"""

import ast
import importlib
import inspect
import os
import sys
import tempfile
import textwrap
import uuid

import pytest

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="admin_metrics_parse_log_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_metrics_log.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402

_URL = "/api/admin/metrics"


def _fresh_db():
    """每个用例独立数据库文件（端点要查 ai_decision_log，库必须真实建出）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, "metrics_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _metrics(client):
    resp = client.get(_URL, headers={"X-Role": "admin"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _write_parse_log(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")


# -------------------------------------------------------------
# 1. 功能判别式：读的就是写侧那份文件
# -------------------------------------------------------------
def test_read_path_reads_the_side_that_writes():
    """往 `_memory_log_path()` 指的文件写两行 → 端点必须读到它，并回报该路径。

    旧实现（4 个 `__file__` 候选）在 ARTIFACTS_DIR 生效时会去找仓库
    `artifacts/memory/parse_log.jsonl`，本用例环境下那份不存在 → file.count=0，
    故本条同时是「读写同源」的判别式。
    """
    _fresh_db()
    from app.chains import supervisor

    target = supervisor._memory_log_path()
    _write_parse_log(target, [
        '{"tokens_total": 100, "elapsed_ms": {"total": 1000.0}, "success": true}',
        '{"tokens_total": 300, "elapsed_ms": {"total": 3000.0}, "success": false}',
    ])

    data = _metrics(_client())
    f = data["file"]
    assert f["path"] == str(target), f
    assert f["count"] == 2, f
    assert f["avg_tokens"] == 200.0, f
    assert f["avg_elapsed"] == 2000.0, f
    assert f["success_rate"] == 0.5, f


def test_explicit_artifacts_dir_is_followed(monkeypatch, tmp_path):
    """显式设 ARTIFACTS_DIR → 读侧解析出的文件必须在它下面（容器/测试场景）。"""
    _fresh_db()
    from app.chains import supervisor

    override = tmp_path / "revised_artifacts"
    monkeypatch.setenv("ARTIFACTS_DIR", str(override))

    target = supervisor._memory_log_path()
    assert os.path.abspath(target) == os.path.abspath(
        str(override / "memory" / "parse_log.jsonl")), target
    _write_parse_log(target, ['{"tokens_total": 7, "elapsed_ms": {"total": 70.0},'
                              ' "success": true}'])

    data = _metrics(_client())
    assert data["file"]["path"] == str(target), data["file"]
    assert data["file"]["count"] == 1, data["file"]


def test_read_path_calls_the_shared_path_function(monkeypatch, tmp_path):
    """把 `_memory_log_path` 换成别的实现 → 读侧必须跟着走。

    why：这是「真调用 vs 各算各的」的守卫。若有人把路径在端点里再算一遍，
    被替换的 helper 就不再影响读数，本用例立刻失败。
    """
    _fresh_db()
    from app.chains import supervisor

    custom = tmp_path / "elsewhere" / "parse_log.jsonl"
    _write_parse_log(str(custom), ['{"tokens_total": 5, "elapsed_ms": {"total": 50.0},'
                                   ' "success": true}'])
    monkeypatch.setattr(supervisor, "_memory_log_path", lambda: str(custom))

    data = _metrics(_client())
    assert data["file"]["path"] == str(custom), data["file"]
    assert data["file"]["count"] == 1, data["file"]


# -------------------------------------------------------------
# 2. 结构：不得保留第二份路径解析；写侧仍走同一 helper
# -------------------------------------------------------------
def _code_body(fn):
    """函数体源码（去掉文档字符串）—— 供「不得有第二份路径解析」这类结构断言用。

    why 必须去掉文档字符串：本修复的 docstring 刻意写明了「原先按 __file__ 拼候选」
    这一缺陷形态，直接对整段源码断言 __file__ 不存在会把自己的说明文字当成代码命中。
    """
    src = inspect.getsource(fn)
    dedented = textwrap.dedent(src)
    tree = ast.parse(dedented)
    body = tree.body[0].body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    assert body, "函数体为空？"
    body_src = "\n".join(dedented.splitlines()[body[0].lineno - 1:])
    return textwrap.dedent(body_src)


def test_no_second_path_resolution_in_endpoint():
    """端点**代码体**里不得再出现按 `__file__` 拼 artifacts 路径的写法。

    （文档字符串里的缺陷说明不算代码，见 `_code_body`。）
    """
    from app import api_admin

    code = _code_body(api_admin.admin_metrics)
    tree = ast.parse(code)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "__file__" not in names, "端点体里仍有按 __file__ 拼的路径解析"
    assert "candidates" not in names, "端点体里仍留着路径候选表"
    # 代码体里的字符串字面量不得再出现 artifacts 路径（注释里的缺陷说明不算代码）
    consts = [n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [c for c in consts if "artifacts" in c], \
        "端点体里仍有硬编码的 artifacts 路径: %s" % [c for c in consts if "artifacts" in c]
    # 读路径必须真调用同一 helper
    called = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr == "_memory_log_path"]
    assert called, "读路径必须复用 supervisor._memory_log_path()"


def test_writer_still_uses_the_same_helper():
    """写侧（`supervisor._finalize`）必须仍走 `_memory_log_path()` —— 同源的另一半。"""
    from app.chains import supervisor

    assert "_memory_log_path()" in inspect.getsource(supervisor._finalize), \
        "写侧不再走 _memory_log_path()，读写同源失效"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

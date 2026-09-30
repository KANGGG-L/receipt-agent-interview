# -*- coding: utf-8 -*-
"""R10（波次 P11）：读路径失败计数器的**生产消费方** —— GET /api/admin/memory-metrics。

why：`rag.get_retrieve_failure_counts()`（P4a·U2 建的读侧可观测）此前**零生产消费方**
—— 计数器一直在涨、warning 一直在打，却没有任何人能读到聚合结果，属本仓高发的
「定义了没接线」：能力存在但等于不存在。本文件把「端点确实消费了 rag 的计数」写成断言。

why 必须自校准：只断言「字段存在 + counts 是 dict」的用例，在「端点根本没接通、永远返回
空 dict」时**同样通过** —— 那是恒真用例（本仓：恒真用例比没有用例更糟，会给人以已被守护的
假象）。因此这里人为触发一次真实降级（`_note_retrieve_failure`），断言端点返回的对应阶段
计数恰好 +1；「真为 0」与「没接通」由此可区分。

覆盖：
1. 正常路径：retrieve_failures 存在，含 counts(dict) / process_scoped / note。
2. 自校准：触发一次降级后，端点返回的 counts[该阶段] 恰好 +1。
3. 只读：端点自身不得改变计数（读的是快照副本，不消费、不清零）。
4. 容错：计数读取抛异常 → 端点仍 200，retrieve_failures 带 error 且 counts 为空 dict，
   status / metrics / memory_enabled 不受影响（一个诊断计数器坏了不得打崩度量端点）。
5. 度量脚本失败（status=error）时，retrieve_failures 仍随返回体给出。
6. note 必须自述「进程级 / 重启归零」——否则读者会把它当历史累计（口径误读）。

反证口径（交付说明给实测记录，不写进用例）：把端点里的 `_collect_retrieve_failures()`
调用注掉（或改成返回 `{}`）后，第 2 条必须失败。

隔离：本模块 DB_PATH / RAG_DIR 全部指向临时目录（在首次 import app.* 之前设置），
零网络、零 live 库写入、零 live 向量库写入。
"""

import importlib
import os
import sys
import tempfile
import uuid

import pytest

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="mm_retrieve_failures_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_mm_rf.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, "rf_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _get(client):
    return client.get("/api/admin/memory-metrics", headers={"X-Role": "admin"})


# -------------------------------------------------------------
# 1. 正常路径：字段存在、结构完整、note 自述进程级
# -------------------------------------------------------------
def test_retrieve_failures_field_present_and_structured(monkeypatch):
    monkeypatch.setattr(rag, "_RETRIEVE_FAILURE_COUNTS", {})
    _db = _fresh_db()
    client = _client()

    ret = _get(client)
    assert ret.status_code == 200, ret.text
    body = ret.json()
    assert body["status"] == "success", body

    rf = body["retrieve_failures"]
    assert isinstance(rf, dict), rf
    assert isinstance(rf["counts"], dict), rf
    assert rf["counts"] == {}, rf
    assert rf["process_scoped"] is True, rf
    # 口径说明必须自述「进程级 / 重启归零」，否则读者当成历史累计（R10 的明确要求）
    assert "进程级" in rf["note"] and "重启" in rf["note"], rf["note"]
    assert "error" not in rf, rf


# -------------------------------------------------------------
# 2. 自校准：端点确实消费了 rag 的计数（人为触发一次降级 → 该阶段 +1）
# -------------------------------------------------------------
def test_endpoint_consumes_counts_self_calibrated(monkeypatch):
    monkeypatch.setattr(rag, "_RETRIEVE_FAILURE_COUNTS", {})
    _db = _fresh_db()
    client = _client()

    before = _get(client).json()["retrieve_failures"]["counts"]
    assert before == {}, before

    rag._note_retrieve_failure(rag.RETRIEVE_STAGE_EXACT,
                               RuntimeError("自校准：人为触发一次读路径降级"))

    after = _get(client).json()["retrieve_failures"]["counts"]
    assert after.get(rag.RETRIEVE_STAGE_EXACT) == \
        before.get(rag.RETRIEVE_STAGE_EXACT, 0) + 1, (before, after)

    # 阶段名取自 rag 的唯一常量口径（端点不得自造第二套阶段名）
    assert rag.RETRIEVE_STAGE_EXACT in after, after


# -------------------------------------------------------------
# 3. 只读：端点调用本身不改变计数
# -------------------------------------------------------------
def test_endpoint_call_does_not_mutate_counts(monkeypatch):
    monkeypatch.setattr(rag, "_RETRIEVE_FAILURE_COUNTS", {})
    _db = _fresh_db()
    client = _client()

    rag._note_retrieve_failure(rag.RETRIEVE_STAGE_KEYWORD,
                               RuntimeError("自校准：另起一个阶段"))
    before = rag.get_retrieve_failure_counts()

    for _ in range(3):
        assert _get(client).status_code == 200

    assert rag.get_retrieve_failure_counts() == before, "端点改动了计数（应为只读快照）"


# -------------------------------------------------------------
# 4. 容错：计数读取抛异常 → 200 + error 标记，其余字段不受影响
# -------------------------------------------------------------
def test_count_read_failure_is_tolerated(monkeypatch):
    _db = _fresh_db()
    client = _client()

    monkeypatch.setattr(rag, "get_retrieve_failure_counts",
                        lambda: (_ for _ in ()).throw(RuntimeError("模拟计数读取失败")))

    ret = _get(client)
    assert ret.status_code == 200, ret.text
    body = ret.json()
    # 度量取数本身没坏，故 status 仍为 success；只有这一个字段降级
    assert body["status"] == "success", body
    rf = body["retrieve_failures"]
    assert rf["counts"] == {}, rf
    assert rf.get("error"), rf
    assert rf["process_scoped"] is True, rf


# -------------------------------------------------------------
# 5. 度量脚本失败（status=error）时，retrieve_failures 仍随返回体给出
# -------------------------------------------------------------
def test_retrieve_failures_present_when_metrics_fail(monkeypatch):
    _db = _fresh_db()
    client = _client()

    from app import api_admin

    def _boom():
        raise RuntimeError("模拟度量脚本不可加载")

    monkeypatch.setattr(api_admin, "_load_memory_metrics_module", _boom)
    ret = _get(client)
    assert ret.status_code == 200, ret.text
    body = ret.json()
    assert body["status"] == "error", body
    assert body["metrics"] is None, body
    assert isinstance(body.get("retrieve_failures"), dict), body


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

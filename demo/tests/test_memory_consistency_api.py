# -*- coding: utf-8 -*-
"""X1：记忆一致性巡检的**生产触发方**回归测试（B0 审核 mustFix 未闭环项）。

why：`app/services/memory_consistency.py` 早已实现巡检（`check_memory_consistency`
能检出「DB 有行无向量 / 向量有 mid 无行 / 交集」），也已被 tests/test_memory_vector_
consistency.py 覆盖 —— 但它此前**零生产触发方**，属本仓高发的「定义了没接线」：
工具写好了却没人跑，巡检结果永远不会被任何人看到。本文件钉住它的两个消费方：
  1. `GET /api/admin/memory-consistency`（只读端点，与启动自检同一份实现）；
  2. `app/main.py` 的启动自检 `_check_memory_consistency_startup`（容错、只告警）。

覆盖：
1. 端点结构：含 `consistent` 与三个计数（交集 / 孤儿向量 / 缺向量行）。
2. 判别式：构造一个不一致的临时库 → 端点报 `consistent=False` 并点名具体字段。
3. 端点确实**消费巡检返回值**（哨兵注入）：去掉巡检调用/改成硬编码即失败。
4. 只读：临时库 md5 不变；live 库与两个 live 向量库 md5/指纹不变。
5. 权限：匿名 401；staff / owner 403；admin 200。
6. 失败不抛 500：巡检异常 → 200 + status=error + report=None。
7. 启动自检**已注册在 app 上**、会真的调用巡检、不一致时告警、且绝不阻断启动。

反证口径（交付说明给实测记录与三个 md5，不写进用例）：把端点里的
`mc.check_memory_consistency(...)` 调用去掉（返回硬编码报告）→ 第 2、3 条必须失败；
把启动自检里的巡检调用去掉 → 第 7 条必须失败。

隔离：本模块 DB_PATH / RAG_DIR 全部指向临时目录（首次 import app.* 之前设置），
零网络、零 live 库写入、零 live 向量库写入。
"""

import hashlib
import importlib
import logging
import os
import sqlite3
import sys
import tempfile
import uuid

import pytest

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_consistency_api_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_consistency_api.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_DEMO_DIR, ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402

_LIVE_DB = os.path.join(_DEMO_DIR, "receipt_demo.db")
_LIVE_RAG_DIRS = [os.path.join(_DEMO_DIR, ".rag_chroma"),
                  os.path.join(_REPO_ROOT, ".rag_chroma")]


# -------------------------------------------------------------
# 环境与取数辅助
# -------------------------------------------------------------
def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。

    `importlib.reload(db)` 会重跑模块级 `_make_engine()`，从而按新 DB_PATH 建表
    （真实 schema），再往库里写种子数据。端点/启动自检都读 `db.DB_PATH`，因此跟随。
    """
    os.environ["DB_PATH"] = os.path.join(_TMP, "cons_%s.db" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _seed_active_rows(rows):
    """写 active 记忆行：rows = [(memory_id, vendor)]，tenant_id 固定 default。"""
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.executemany(
            "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample, status) "
            "VALUES (?, ?, 'default', '', '', 'active')",
            [(mid, vendor) for mid, vendor in rows])
        conn.commit()
    finally:
        conn.close()


def _write_vectors(entries, collection="vendor_memory"):
    """往**当前 RAG_DIR**写向量：entries = [(document, memory_id 或 None)]。

    刻意传 `embedding_function=None` + 显式向量 —— 否则 chromadb 会加载默认的
    ONNX 嵌入模型（联网/下载），测试就不再是离线的。RAG_DIR 由 demo/conftest.py 的
    autouse 夹具指向本轮 tmp_path，巡检端点在**同一进程**里按 RAG_DIR 解析，看到同一目录。
    """
    import chromadb

    rag_dir = os.environ["RAG_DIR"]
    client = chromadb.PersistentClient(path=str(rag_dir))
    col = client.get_or_create_collection(collection, embedding_function=None)
    metas = []
    for _doc, mid in entries:
        meta = {"vendor": "一致性探針供應商"}
        if mid is not None:
            meta["memory_id"] = mid
        metas.append(meta)
    col.add(
        ids=["vec-%d" % i for i in range(len(entries))],
        embeddings=[[0.1 + 0.01 * i] * 8 for i in range(len(entries))],
        documents=[doc for doc, _mid in entries],
        metadatas=metas,
    )
    client.clear_system_cache()


def _get_consistency(client, tenant=None):
    url = "/api/admin/memory-consistency"
    if tenant is not None:
        url += "?tenant=" + tenant
    return client.get(url, headers={"X-Role": "admin"})


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _rag_fingerprint():
    """live 向量库指纹：目录不存在记 "ABSENT"，否则记 (文件数, sqlite md5)。"""
    out = []
    for d in _LIVE_RAG_DIRS:
        if not os.path.isdir(d):
            out.append((d, "ABSENT"))
            continue
        files = []
        for root, _dirs, names in os.walk(d):
            for n in sorted(names):
                files.append(os.path.join(root, n))
        sqlite = os.path.join(d, "chroma.sqlite3")
        digest = _md5(sqlite) if os.path.isfile(sqlite) else "-"
        out.append((d, (len(files), digest)))
    return out


# -------------------------------------------------------------
# 1. 端点结构：consistent + 三个计数
# -------------------------------------------------------------
def test_endpoint_reports_consistent_with_three_counts():
    _fresh_db()
    _seed_active_rows([("mid-1", "德利行")])
    _write_vectors([("已確認明細", "mid-1")])

    body = _get_consistency(_client()).json()
    assert body["status"] == "success", body
    report = body["report"]
    assert report is not None, body

    # 三个计数：交集 / 孤儿向量 / 缺向量行 —— 都必须在（前端/运维据此判断）
    assert report["intersection"] == ["mid-1"], report
    assert report["orphan_mids"] == [], report
    assert report["missing_vector_mids"] == [], report
    assert report["consistent"] is True, report
    assert len(report["intersection"]) == 1
    assert len(report["orphan_mids"]) == 0 and len(report["missing_vector_mids"]) == 0
    assert report["db_rows_active"] == 1 and report["vectors"] == 1, report


# -------------------------------------------------------------
# 2. 判别式：不一致的临时库 → 端点报出不一致并点名字段
# -------------------------------------------------------------
def test_endpoint_reports_inconsistency_with_specific_fields():
    """DB 有 active 行无向量，且向量库有 DB 里不存在的 mid —— 两向不一致都要报出。

    why：只报「不一致」而不点名具体 mid 的巡检没有可操作性；本用例把两侧的
    具体字段都钉死（漏报任一方向的实现都会失败）。
    """
    _fresh_db()
    _seed_active_rows([("mid-db-only", "祥興")])
    _write_vectors([("孤兒向量", "mid-orphan")])

    body = _get_consistency(_client()).json()
    assert body["status"] == "success", body
    report = body["report"]

    assert report["consistent"] is False, report
    assert report["missing_vector_mids"] == ["mid-db-only"], report
    assert report["orphan_mids"] == ["mid-orphan"], report
    assert report["intersection"] == [], report
    assert report["db_rows_active"] == 1, report
    assert report["store_present"] is True, report


# -------------------------------------------------------------
# 3. 端点确实消费巡检返回值（哨兵注入）—— 去掉调用/硬编码即失败
# -------------------------------------------------------------
def test_endpoint_surfaces_check_output_not_hardcoded(monkeypatch):
    """把巡检函数换成返回**哨兵报告** → 端点必须原样透出该报告。

    why：这是「定义了没接线」的守卫。若有人把端点改成返回硬编码的
    `consistent=True`（或另写一份巡检），本用例立刻失败。
    """
    from app.services import memory_consistency as mc

    sentinel = {
        "db_path": "/哨兵/记忆库.db", "rag_dir": "/哨兵/向量库",
        "store_present": True, "tenant_scope": "all",
        "db_rows": 7, "db_rows_active": 7, "db_rows_inactive": 0,
        "db_rows_without_memory_id": 0, "db_error": None, "store_error": None,
        "vectors": 3, "stores": [],
        "intersection": ["哨兵命中"],
        "orphan_mids": ["哨兵孤儿"],
        "missing_vector_mids": ["哨兵缺失"],
        "consistent": False,
    }
    monkeypatch.setattr(mc, "check_memory_consistency",
                        lambda *a, **k: sentinel)

    body = _get_consistency(_client()).json()
    assert body["status"] == "success", body
    assert body["report"] == sentinel, body


# -------------------------------------------------------------
# 4. 只读：临时库 md5 不变 + live 库/向量库指纹不变
# -------------------------------------------------------------
def test_endpoint_is_read_only():
    _fresh_db()
    _seed_active_rows([("mid-ro", "大生")])
    _write_vectors([("文檔", "mid-ro")])
    client = _client()
    _get_consistency(client)                        # 预热

    before_live = _md5(_LIVE_DB) if os.path.isfile(_LIVE_DB) else "ABSENT"
    before_tmp = _md5(db.DB_PATH)
    before_rag = _rag_fingerprint()

    for _ in range(3):
        assert _get_consistency(client).status_code == 200

    assert _md5(db.DB_PATH) == before_tmp, "巡检写了自己的库（应为 mode=ro 只读）"
    live_after = _md5(_LIVE_DB) if os.path.isfile(_LIVE_DB) else "ABSENT"
    assert live_after == before_live, "端点动了 live 库"
    assert _rag_fingerprint() == before_rag, "端点动了 live 向量库"


# -------------------------------------------------------------
# 5. 权限：仅 admin
# -------------------------------------------------------------
def test_only_admin_can_read():
    _fresh_db()
    client = _client()

    anon = client.get("/api/admin/memory-consistency")
    assert anon.status_code == 401, anon.text

    for role in ("staff", "owner"):
        ret = client.get("/api/admin/memory-consistency", headers={"X-Role": role})
        assert ret.status_code == 403, (role, ret.status_code, ret.text)

    ok = client.get("/api/admin/memory-consistency", headers={"X-Role": "admin"})
    assert ok.status_code == 200, ok.text


# -------------------------------------------------------------
# 6. 失败不抛 500：巡检异常 → 200 + status=error + report=None
# -------------------------------------------------------------
def test_check_failure_returns_error_field_not_500(monkeypatch):
    _fresh_db()
    client = _client()

    from app.services import memory_consistency as mc

    def _boom(*_a, **_k):
        raise RuntimeError("模拟巡检不可用")

    monkeypatch.setattr(mc, "check_memory_consistency", _boom)
    ret = _get_consistency(client)
    assert ret.status_code == 200, ret.text
    body = ret.json()
    assert body["status"] == "error", body
    assert "msg" in body and body["msg"], body
    # report=None 是「不得显示一致」的契约（前端/运维据此显示错误态而非「一致」）
    assert body["report"] is None, body


# -------------------------------------------------------------
# 7. 启动自检：已注册 + 真调用 + 不一致告警 + 绝不阻断
# -------------------------------------------------------------
def test_startup_handler_registered_on_app():
    """启动自检必须**注册在 app 上**，而不只是定义了一个函数。

    why：本项目反复出现「函数写好了但没接到触发点」；只断言函数存在会漏掉这种缺陷。
    """
    from app.main import app as fastapi_app

    names = [getattr(fn, "__name__", "") for fn in fastapi_app.router.on_startup]
    assert "_check_memory_consistency_startup" in names, names


def test_startup_selfcheck_calls_check_and_warns_on_inconsistency(monkeypatch, caplog):
    """启动自检必须真的调用巡检，并在不一致时把三个数字打进 startup 告警。"""
    from app import main as main_mod
    from app.services import memory_consistency as mc

    called = {}

    def _fake_check(*a, **k):
        called["called"] = True
        return {
            "db_path": "/x/mem.db", "rag_dir": "/x/rag", "store_present": True,
            "tenant_scope": "all", "db_rows": 2, "db_rows_active": 2,
            "db_rows_inactive": 0, "db_rows_without_memory_id": 0,
            "db_error": None, "store_error": None, "vectors": 1, "stores": [],
            "intersection": [], "orphan_mids": ["o1"], "missing_vector_mids": ["m1", "m2"],
            "consistent": False,
        }

    monkeypatch.setattr(mc, "check_memory_consistency", _fake_check)
    with caplog.at_level(logging.WARNING, logger="startup"):
        main_mod._check_memory_consistency_startup()

    assert called.get("called"), "启动自检必须真的调用巡检（否则仍是定义了没接线）"
    msgs = [r.getMessage() for r in caplog.records if r.name == "startup"]
    joined = " ".join(msgs)
    assert "不一致" in joined, msgs
    assert "孤儿向量=1" in joined and "缺向量行=2" in joined, msgs


def test_startup_selfcheck_never_raises_on_failure(monkeypatch, caplog):
    """巡检崩溃只能 warning，绝不允许冒泡阻断启动。"""
    from app import main as main_mod
    from app.services import memory_consistency as mc

    def _boom(*_a, **_k):
        raise RuntimeError("模拟巡检崩溃")

    monkeypatch.setattr(mc, "check_memory_consistency", _boom)
    with caplog.at_level(logging.WARNING, logger="startup"):
        main_mod._check_memory_consistency_startup()   # 不得抛

    msgs = [r.getMessage() for r in caplog.records if r.name == "startup"]
    assert any("启动巡检失败" in m for m in msgs), msgs


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

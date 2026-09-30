# -*- coding: utf-8 -*-
"""P0-T4：孤儿向量生成器修复 + 向量 metadata 完整性闸门 测试。

why（见 docs/06-adds-on/05-VendorMemory现状与大厂标准Gap分析.md 的 H2/M1）：
DB 写失败时旧实现是 `except Exception: stored_new = True` 静默放行，随后继续调用
`_store(tid).add_documents(...)` 把向量写进 Chroma —— 而那次写入用的 memory_id
从未落库。于是产生「向量存在、DB 无行」的孤儿：既不可按 memory_id 级联删除
（P1-T1 的删除键就是 memory_id），也不参与治理过滤（allowed_mids 门），只能人工清库。
live 实测 DB↔向量 memory_id 交集为 0、179 条向量全被门拦掉，正是这批孤儿。

覆盖：
1. ingest_memory：DB 抛错 → 新增向量 **0** 条 + 日志含 error
2. approve_pending_to_memory：同一根因的**第二份实现**，同样必须 0 条
   （本仓高发缺陷①「修了 A 漏了 B」，两条链路必须一起改）
3. 完整性闸门 `_vector_write_allowed`：memory_id / tenant_id / doc metadata
   三项缺任一即拒（这是「向量 metadata 必带 memory_id 与 tenant_id」的落点）
4. 正向对照：落库正常时仍写 2 条向量且 metadata 齐备 —— 证明第 1/2 条的
   「0 条」断言不是恒真（否则「没写向量」可能只是向量链路整体坏了）

反向证明（人工执行，见任务报告）：把 `_persist_memory_row` 的 `return None`
改回旧行为（失败也继续写向量）→ 用例 1/2 的「新增向量 0 条」必须失败（实测新增 2 条）；
逐字节还原并核 md5 后重新转绿。

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
"""

import importlib
import logging
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_orphan_guard_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_orphan_guard.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402


def _fresh_db():
    """每个用例独立数据库文件 + 重置 Chroma 进程内缓存，避免跨用例串扰。

    向量库目录在模块内共享，故每个用例用**各自的租户 id**（collection 彼此独立），
    否则「新增向量数」的差值断言会被上一个用例的残留污染。
    """
    os.environ["DB_PATH"] = os.path.join(_TMP, f"orphan_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _vector_count(tenant_id):
    """该租户 collection 当前的向量条数。"""
    return rag._store(tenant_id)._collection.count()


def _raise_db_failure(*_args, **_kwargs):
    """模拟 DB 落库失败（磁盘/锁/约束等真实故障的统一形态）。"""
    raise RuntimeError("simulated vendor_memory persist failure")


def _error_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


# -------------------------------------------------------------
# 1. ingest_memory：DB 落库失败 -> 一条向量都不写
# -------------------------------------------------------------
def test_ingest_memory_db_failure_writes_no_vector(monkeypatch, caplog):
    _db = _fresh_db()
    vendor = "孤兒商戶A"
    tid = "p0t4_fail_a"
    before = _vector_count(tid)

    monkeypatch.setattr(rag.db, "upsert_vendor_memory", _raise_db_failure)
    with caplog.at_level(logging.ERROR, logger="app.services.rag"):
        ret = rag.ingest_memory(vendor, "菜心 2斤 @10 = 20", notes="版式：手寫單",
                                tenant_id=tid, receipt_id=77)

    assert ret == vendor, "返回值契约不变（调用方 approve 端点依赖它）"
    assert _vector_count(tid) == before, \
        "DB 落库失败时不得写入任何向量，否则产生不可回溯的孤儿"
    assert _db.list_vendor_memory(vendor, tenant_id=tid) == [], "DB 也确实没有行"
    errs = _error_messages(caplog)
    assert errs, "DB 落库失败必须记 error（旧实现是静默 pass）"
    assert any("vector write skipped" in m for m in errs), \
        f"error 日志需说明已跳过向量写入，实际 {errs}"


# -------------------------------------------------------------
# 2. approve_pending_to_memory：同根因的第二份实现（防「修 A 漏 B」）
# -------------------------------------------------------------
def test_approve_pending_db_failure_writes_no_vector(monkeypatch, caplog):
    _db = _fresh_db()
    vendor = "孤兒商戶B"
    tid = "p0t4_fail_b"
    before = _vector_count(tid)

    monkeypatch.setattr(rag.db, "upsert_vendor_memory", _raise_db_failure)
    with caplog.at_level(logging.ERROR, logger="app.services.rag"):
        ret = rag.approve_pending_to_memory(vendor, "供应商 X 反馈纠偏：品名認錯要糾正",
                                            tenant_id=tid,
                                            source_receipt_ids=["901", "902"])

    assert ret == "供应商 X 反馈纠偏：品名認錯要糾正", "返回值契约不变"
    assert _vector_count(tid) == before, \
        "第二条写入链路同样不得写孤儿向量（同一根因两份实现必须一起改）"
    assert _db.list_vendor_memory(vendor, tenant_id=tid) == []
    assert any("vector write skipped" in m for m in _error_messages(caplog))


# -------------------------------------------------------------
# 3. 完整性闸门：metadata 必带 memory_id 与 tenant_id
# -------------------------------------------------------------
def test_vector_write_gate_requires_memory_id_and_tenant_id():
    from langchain_core.documents import Document

    ok = Document(page_content="x", metadata={"memory_id": "m1", "tenant_id": "t1"})
    assert rag._vector_write_allowed("m1", "t1", [ok]) is True

    # 主键缺失/空白
    assert rag._vector_write_allowed("", "t1", [ok]) is False
    assert rag._vector_write_allowed("   ", "t1", [ok]) is False
    assert rag._vector_write_allowed(None, "t1", [ok]) is False
    # 租户缺失/空白
    assert rag._vector_write_allowed("m1", "", [ok]) is False
    assert rag._vector_write_allowed("m1", None, [ok]) is False
    # doc metadata 缺 memory_id / 缺 tenant_id：任一条不齐即整批拒写
    no_mid = Document(page_content="x", metadata={"tenant_id": "t1"})
    assert rag._vector_write_allowed("m1", "t1", [no_mid]) is False
    no_tid = Document(page_content="x", metadata={"memory_id": "m1"})
    assert rag._vector_write_allowed("m1", "t1", [no_tid]) is False
    # 多 doc 中只有一条不合规也必须拒（notes/sample 两条都要可回溯）
    assert rag._vector_write_allowed("m1", "t1", [ok, no_mid]) is False


# -------------------------------------------------------------
# 4. 正向对照：落库正常时向量链路仍然工作（证明上文「0 条」不是恒真）
# -------------------------------------------------------------
def test_ingest_memory_success_writes_two_traceable_vectors():
    _db = _fresh_db()
    vendor = "正常商戶C"
    tid = "p0t4_ok"
    before = _vector_count(tid)

    rag.ingest_memory(vendor, "菜心 2斤 @10 = 20", notes="版式：手寫單",
                      tenant_id=tid, receipt_id=88)

    rows = _db.list_vendor_memory(vendor, tenant_id=tid)
    assert len(rows) == 1, "落库正常时应恰好一行"
    mid = rows[0]["memory_id"]

    got = rag._store(tid)._collection.get()
    assert len(got["ids"]) == before + 2, "notes + sample 两条向量（与旧行为一致）"
    metas = got["metadatas"]
    assert all(m.get("memory_id") == mid for m in metas), \
        "每条向量的 memory_id 必须等于 DB 行的 memory_id（非孤儿）"
    assert all(m.get("tenant_id") == tid for m in metas), "tenant_id 必带"

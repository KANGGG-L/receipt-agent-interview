# -*- coding: utf-8 -*-
"""P8a-R8 回归：幂等复用分支必须保证「DB 有行 ⇒ 向量存在」。

背景（为什么需要这个文件）
--------------------------
`ingest_memory` / `approve_pending_to_memory` 在 DB 幂等去重命中、**复用既有
memory_id** 时都曾直接 `return`、跳过向量写入。于是当首次写入落进另一个 store
（P8a-R7 修的 cwd 相对路径就是这条路的实例）或当时 Chroma 写失败被降级成 warning
时，就出现「DB 有 active 行、生产向量库无该 mid」的脱钩 —— 检索按 mid 拿不到该行、
巡检报 missing_vector_mids，而写入路径**永不自愈**（live 实测缺失 5 条）。
修复＝复用分支先只读查该 mid 是否已在目标 collection，缺则用**调用方同一套 docs**
补写并留痕（`_sync_memory_vectors`，两条链路共用的唯一实现）。

覆盖
----
1. ingest_memory 路径：删掉某 mid 的向量 → 同内容再调 → 向量被补回，且 DB 仍一行、
   复用同一 memory_id（去重语义不变）。
2. approve_pending_to_memory 路径：同一根因的**第二份实现**，同样必须补回
   （本仓高发缺陷①「修了 A 漏了 B」，两条写入链路要一起改）。
3. 幂等不变：连续两次同内容 ingest → DB 一行、向量条数不增长（防「每次调用翻倍」）。
4. 向量库不可用（`_store` 抛错）→ 已成功的 DB 写入不得变成异常，降级留痕（warning）。
5. 结构性守卫：`rag.py` 全源只有**一处** `add_documents`，且两条写入链路都经
   `_sync_memory_vectors`（防再出现第二份「写入 + try/except」实现）。

反向证明（人工执行，见任务报告）
--------------------------------
把 `_sync_memory_vectors` 的补写分支去掉（`reusing` 时直接 return，等价于修复前的
`if not stored_new: return`）→ 用例 1/2 的「向量被补回」断言必须失败；逐字节还原并核
md5 后重新转绿。三个 md5 记在交付报告里。

隔离（红线）
------------
`DB_PATH` / `RAG_DIR` 在**首次 import app.* 之前**指向临时路径（与 tests/conftest.py
的 autouse 夹具双保险）：`import app.services.rag` 会连带 `app.db`，其 import 期迁移块
会按 `DB_PATH` 执行 ALTER，绝不能让 `DB_PATH` 落回 live 库；向量落点也不能是 live
向量库。每个用例用独立租户 id，向量 collection 彼此独立，避免跨用例串扰。
"""

import ast
import importlib
import logging
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_idem_vector_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_idem.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402

_RAG_SOURCE = os.path.abspath(os.path.join(_DEMO_DIR, "app", "services", "rag.py"))


def _fresh_db():
    """每个用例独立数据库文件 + 重置 Chroma 进程内缓存，避免跨用例串扰。

    向量目录由本用例的租户 id 区分（collection 彼此独立）；DB 则换文件重载，
    否则同模块内前一个用例落下的 active 行会参与后一个用例的去重判定。
    """
    os.environ["DB_PATH"] = os.path.join(_TMP, f"idem_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _vector_count(tenant_id):
    """该租户 collection 当前的向量条数。"""
    return rag._store(tenant_id)._collection.count()


def _vector_mids(tenant_id):
    """该租户 collection 里全部向量的 memory_id 列表。"""
    got = rag._store(tenant_id)._collection.get()
    return [m.get("memory_id") for m in (got.get("metadatas") or [])]


def _delete_vectors(tenant_id, mid):
    """删掉该 memory_id 的全部向量（模拟「首次写入落进别的 store / 当时写失败」）。"""
    rag._store(tenant_id)._collection.delete(where={"memory_id": mid})


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# -------------------------------------------------------------
# 1. ingest_memory：复用既有 memory_id 时必须补回缺失向量
# -------------------------------------------------------------
def test_ingest_reuse_backfills_missing_vector():
    _fresh_db()
    vendor = "冪等商戶A"
    tid = "p8a_r8_a"
    args = dict(notes="版式：手寫單", tenant_id=tid)

    rag.ingest_memory(vendor, "菜心 2斤 @10 = 20", receipt_id=101, **args)
    rows = db.list_vendor_memory(vendor, tenant_id=tid)
    assert len(rows) == 1, "首次写入应恰好一行"
    mid = rows[0]["memory_id"]
    assert _vector_count(tid) == 2, "首次写入应有 notes + sample 两条向量"

    # 制造「DB 有行、向量缺失」的脱钩
    _delete_vectors(tid, mid)
    assert _vector_count(tid) == 0
    assert rag._memory_vectors_present(rag._store(tid), mid) is False

    # 同内容再走一次：DB 幂等复用同一 memory_id，复用分支必须把向量补回
    rag.ingest_memory(vendor, "菜心 2斤 @10 = 20", receipt_id=102, **args)

    rows2 = db.list_vendor_memory(vendor, tenant_id=tid)
    assert len(rows2) == 1, "去重语义不变：DB 仍只有一行"
    assert rows2[0]["memory_id"] == mid, "复用既有 memory_id"
    assert _vector_count(tid) == 2, "复用分支必须用同一套 docs 补回两条向量"
    assert _vector_mids(tid) == [mid, mid], "补回的向量必须指向被复用的 mid（非孤儿）"


# -------------------------------------------------------------
# 2. approve_pending_to_memory：同根因的第二份实现（防「修 A 漏 B」）
# -------------------------------------------------------------
def test_approve_reuse_backfills_missing_vector():
    _fresh_db()
    vendor = "冪等商戶B"
    tid = "p8a_r8_b"
    content = "供应商 X 反馈纠偏：品名認錯要糾正"

    rag.approve_pending_to_memory(vendor, content, tenant_id=tid,
                                  source_receipt_ids=["901"])
    rows = db.list_vendor_memory(vendor, tenant_id=tid)
    assert len(rows) == 1
    mid = rows[0]["memory_id"]
    assert _vector_count(tid) == 1, "approve 自由文本路径应写 1 条向量"

    _delete_vectors(tid, mid)
    assert _vector_count(tid) == 0

    rag.approve_pending_to_memory(vendor, content, tenant_id=tid,
                                  source_receipt_ids=["902"])

    rows2 = db.list_vendor_memory(vendor, tenant_id=tid)
    assert len(rows2) == 1, "去重语义不变"
    assert rows2[0]["memory_id"] == mid, "复用既有 memory_id"
    assert _vector_count(tid) == 1, "approve 复用分支同样必须补回向量"
    assert _vector_mids(tid) == [mid]


# -------------------------------------------------------------
# 3. 幂等不变：同内容重复调用不得让向量膨胀
# -------------------------------------------------------------
def test_repeat_same_content_keeps_one_row_and_no_vector_growth():
    _fresh_db()
    vendor = "冪等商戶C"
    tid = "p8a_r8_c"
    args = dict(notes="版式：手寫單", tenant_id=tid)

    rag.ingest_memory(vendor, "菜心 2斤 @10 = 20", receipt_id=201, **args)
    first = _vector_count(tid)
    assert first == 2

    # 第二次同内容：向量已在 → 不得重复写（否则每次调用都翻倍）
    rag.ingest_memory(vendor, "菜心 2斤 @10 = 20", receipt_id=202, **args)
    rag.ingest_memory(vendor, "菜心 2斤 @10 = 20", receipt_id=203, **args)

    assert len(db.list_vendor_memory(vendor, tenant_id=tid)) == 1, "DB 仍只有一行"
    assert _vector_count(tid) == first, "向量条数不得膨胀（复用且已在 → 直接返回）"


# -------------------------------------------------------------
# 4. 向量库不可用：DB 写入不得变成异常，降级留痕
# -------------------------------------------------------------
def test_vector_store_unavailable_degrades_without_raising(monkeypatch, caplog):
    _fresh_db()
    vendor = "冪等商戶D"
    tid = "p8a_r8_d"
    args = dict(notes="版式：手寫單", tenant_id=tid)

    rag.ingest_memory(vendor, "菜心 2斤 @10 = 20", receipt_id=301, **args)
    mid = db.list_vendor_memory(vendor, tenant_id=tid)[0]["memory_id"]
    _delete_vectors(tid, mid)  # 制造「向量缺失」，迫使复用分支尝试补写

    def _boom(*_a, **_k):
        raise RuntimeError("simulated vector store unavailable")

    monkeypatch.setattr(rag, "_store", _boom)
    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        ret = rag.ingest_memory(vendor, "菜心 2斤 @10 = 20", receipt_id=302, **args)

    assert ret == vendor, "返回值契约不变"
    assert len(db.list_vendor_memory(vendor, tenant_id=tid)) == 1, \
        "向量库故障不得把已成功的 DB 写入弄成异常（DB 行为准）"
    assert any("degraded" in m and "vector missing" in m for m in _warnings(caplog)), \
        f"向量补写失败必须降级留痕 warning，实际 {_warnings(caplog)}"


# -------------------------------------------------------------
# 5. 结构性守卫：写入形态只有一份，两条链路都经 _sync_memory_vectors
# -------------------------------------------------------------
def test_single_vector_write_implementation_and_both_links_wired():
    src = open(_RAG_SOURCE, encoding="utf-8").read()
    tree = ast.parse(src)

    add_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_documents"
    ]
    assert len(add_calls) == 1, (
        "rag.py 只允许一处 add_documents（向量写入形态的唯一实现）；"
        f"实际 {len(add_calls)} 处 @ {[n.lineno for n in add_calls]}"
    )

    for fn_name in ("ingest_memory", "approve_pending_to_memory"):
        fn = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == fn_name), None)
        assert fn is not None, f"缺少 {fn_name}"
        used = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_sync_memory_vectors" in used, (
            f"{fn_name} 必须经 _sync_memory_vectors 写向量（不得各自实现一份）"
        )

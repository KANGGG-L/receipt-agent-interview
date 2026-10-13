# -*- coding: utf-8 -*-
"""U1 审计链：人工 approve 落库的记忆行必须记得住"这条是谁批准写入的"。

背景（P3w 审核报出的审计链断点）：`vendor_memory.written_by` 列在 db.py:307、
ALTER 在 :801、映射在 memory_facts.ROW_FIELDS、读侧在 db.py:3702 —— 唯独**写入侧
全部缺位**：`rag.approve_pending_to_memory` 没有 `written_by` 形参，内部造 MemoryFact
时走缺省空串；`api_memory.approve_pending` 里 `who = _actor(request)`（:81）手上已经
有操作者身份，却只用于状态流转与系统审计日志，落库的记忆行上没有写入者。后果：
治理/合规视角问"这条记忆是谁批的、依据什么"时无从追溯。

本用例走完整链路（HTTP 端点 → api_memory 透传 → rag 落库 → DB 行），断言的
是**裸 SQL 读出的列值**，不是 ORM 属性上的假象：

1. 结构化候选（LLM 抽取）：written_by **等于该次请求的操作者**；
2. 两个不同操作者各批一条：各自的行记各自的写入者 —— 这条专门排除"恰好落了
   一个全局常量/兜底身份"的假通过；
3. 旧 pending 行（无结构化载荷，走自由文本回落路径）：同样记下操作者（两条路径
   都写，否则另一条路径的审计链仍是断的 —— 本仓高发的「修 A 漏 B」）；
4. 向后兼容：不传 written_by 的旧调用方仍能写入、不报错，该列为空值（不编造
   "system" 之类的写入者）。

判别式说明（反证三处 md5 见交付报告，不写进用例）：
- 去掉 `api_memory.approve_pending` 里的 `written_by=who` 透传 → 用例 1/2/3 必须失败；
- 去掉 `approve_pending_to_memory` 向 `_pending_memory_fact` 的 written_by 传参
  → 用例 1/2 必须失败；
- 去掉自由文本分支的 written_by → 用例 3 必须失败。
四条取值（结构化的传递 / 两名操作者 / 自由文本路径 / 缺省兼容）各写一条，
任一分支被改坏都有对应用例报红。

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 库/向量库写入。
"""

import importlib
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）。
# app.db 在 import 期执行 _make_engine() 的迁移块（ALTER），不带 DB_PATH 就会打到 live 库。
_TMP = tempfile.mkdtemp(prefix="memory_written_by_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_written_by.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

# 自由文本路径下仍必须保持 NULL 的事实载荷列（written_by 不在其中：它是审计列，
# 不描述事实内容，两条路径都应能落库）。
_FACT_PAYLOAD_COLUMNS = ("fact_kind", "fact_key", "fact_value",
                         "trust_level", "valid_from")


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与既有记忆用例同款隔离）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"wb_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _headers(forged_email):
    """演示态 admin。X-Email 是伪造值，解析结果必须是 admin@demo.hk，不是这个头。"""
    return {"X-Role": "admin", "X-Email": forged_email}


def _raw_vendor_row(path, memory_id):
    """裸 SQL 读一行：只有从库里读出来才算"真写入"（ORM 属性可能是假象）。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM vendor_memory WHERE memory_id = ?", (memory_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _seed_supplier(path, name, tenant_id="default"):
    """插一条供应商档案，返回其 id（结构化路径要把锚点真的落库需要档案存在）。"""
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO suppliers (name, tenant_id, active) VALUES (?,?,1)",
            (name, tenant_id))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _only_row(_db, vendor):
    rows = _db.list_vendor_memory(vendor, tenant_id="default")
    assert len(rows) == 1, f"一次 approve 应恰好落一条记忆，实得 {len(rows)} 条"
    return _raw_vendor_row(_db.DB_PATH, rows[0]["memory_id"])


# -------------------------------------------------------------
# 1. 结构化候选：written_by == 本次请求的操作者
# -------------------------------------------------------------
def test_approve_records_operator_for_structured_candidate():
    _db = _fresh_db()
    vendor = "審計鏈結構化商戶"
    sid = _seed_supplier(_db.DB_PATH, vendor)
    pid, _ = _db.enqueue_pending_memory(
        vendor, "alias：本吧 → 本地牛腩", tenant_id="default",
        source_kind="extract", source_receipt_ids=[901], supplier_id=sid,
        fact_kind="alias", fact_key="alias:本吧", fact_value="本地牛腩")

    forged = "auditor.a@demo.hk"
    resp = _client().post(f"/api/memory/pending/{pid}/approve",
                          headers=_headers(forged))
    assert resp.status_code == 200, resp.text

    raw = _only_row(_db, vendor)
    assert raw["written_by"] == "admin@demo.hk", (
        f"演示态批准者必须是 admin@demo.hk，实际 {raw['written_by']!r}"
        "（只断言「有值」不够：落了别的常量同样追溯不到角色）")
    assert raw["written_by"] != forged, "伪造的 X-Email 不得写入审计列"


# -------------------------------------------------------------
# 2. 两名操作者各批一条：各自的行记各自的写入者（排除"全局常量"假通过）
# -------------------------------------------------------------
def test_written_by_tracks_the_actual_operator():
    """批准接口只允许 admin，演示态因此只有 admin@demo.hk 这一个身份。

    两个伪造邮箱都必须被丢掉。列本身不是写死常量：直接调用落库函数并传入
    不同 written_by 时，两行必须记下各自的参数。
    """
    _db = _fresh_db()
    client = _client()
    http_seen = {}
    for forged in ("auditor.one@demo.hk", "auditor.two@demo.hk"):
        vendor = f"審計鏈操作者商戶{forged.split('.')[1]}"
        sid = _seed_supplier(_db.DB_PATH, vendor)
        pid, _ = _db.enqueue_pending_memory(
            vendor, "unit：斤 → 公斤", tenant_id="default",
            source_kind="extract", supplier_id=sid,
            fact_kind="unit", fact_key="unit:斤", fact_value="公斤")
        resp = client.post(f"/api/memory/pending/{pid}/approve",
                           headers=_headers(forged))
        assert resp.status_code == 200, resp.text
        http_seen[forged] = _only_row(_db, vendor)["written_by"]

    assert http_seen == {
        "auditor.one@demo.hk": "admin@demo.hk",
        "auditor.two@demo.hk": "admin@demo.hk",
    }, f"伪造邮箱不得成为写入者（实得 {http_seen!r}）"

    direct = {}
    for operator in ("audit.one@demo.hk", "audit.two@demo.hk"):
        vendor = f"審計鏈直写商戶{operator.split('.')[1]}"
        sid = _seed_supplier(_db.DB_PATH, vendor)
        rag.approve_pending_to_memory(
            vendor, "unit：斤 → 公斤", tenant_id="default", supplier_id=sid,
            fact_kind="unit", fact_key="unit:斤", fact_value="公斤",
            written_by=operator)
        direct[operator] = _only_row(_db, vendor)["written_by"]
    assert direct == {
        "audit.one@demo.hk": "audit.one@demo.hk",
        "audit.two@demo.hk": "audit.two@demo.hk",
    }, f"显式写入者必须原样落库（实得 {direct!r}）：两行同值说明写路径丢了参数"


# -------------------------------------------------------------
# 3. 旧 pending 行（自由文本回落路径）：同样记下操作者
# -------------------------------------------------------------
def test_free_text_fallback_also_records_operator():
    _db = _fresh_db()
    vendor = "審計鏈自由文本商戶"
    # 不带任何结构化载荷 → 走自由文本回落路径（旧反馈蒸馏行的形态）
    pid, _ = _db.enqueue_pending_memory(
        vendor, "供應商 反饋糾偏：品名認錯要糾正", tenant_id="default")

    forged = "auditor.legacy@demo.hk"
    resp = _client().post(f"/api/memory/pending/{pid}/approve",
                          headers=_headers(forged))
    assert resp.status_code == 200, resp.text

    raw = _only_row(_db, vendor)
    assert raw["written_by"] == "admin@demo.hk", (
        f"自由文本路径也必须记已解析的批准者（实际 {raw['written_by']!r}）："
        "两条 approve 落库路径只接一条，另一条的审计链仍是断的")
    assert raw["written_by"] != forged, "自由文本路径同样不得采信 X-Email"
    for col in _FACT_PAYLOAD_COLUMNS:
        assert raw[col] is None, (
            f"自由文本路径不得写事实载荷列 {col}（实际 {raw[col]!r}）："
            "written_by 是审计列，与 fact_kind/trust_level 这类事实内容不是一回事")


# -------------------------------------------------------------
# 4. 向后兼容：旧调用方不传 written_by 仍能写入且该列为空
# -------------------------------------------------------------
def test_legacy_call_without_written_by_still_works():
    _db = _fresh_db()
    # 4a 自由文本路径的旧签名（旧调用方的调用形态）
    vendor_a = "審計鏈兼容商戶A"
    rag.approve_pending_to_memory(vendor_a, "供應商反饋糾偏：舊調用形態",
                                  tenant_id="default")
    raw_a = _only_row(_db, vendor_a)
    assert not raw_a["written_by"], (
        f"不传 written_by 时该列必须为空（实际 {raw_a['written_by']!r}）："
        "缺省不得编造写入者")

    # 4b 结构化载荷但同样不传 written_by
    vendor_b = "審計鏈兼容商戶B"
    sid = _seed_supplier(_db.DB_PATH, vendor_b)
    rag.approve_pending_to_memory(vendor_b, "layout：表格式", tenant_id="default",
                                  supplier_id=sid, fact_kind="layout",
                                  fact_key="layout:default", fact_value="表格式")
    raw_b = _only_row(_db, vendor_b)
    assert not raw_b["written_by"], (
        f"结构化路径不传 written_by 时该列也必须为空（实际 {raw_b['written_by']!r}）")
    assert raw_b["fact_kind"] == "layout", "缺省 written_by 不得影响结构化载荷落库"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

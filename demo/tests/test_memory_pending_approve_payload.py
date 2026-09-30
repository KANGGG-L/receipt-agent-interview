# -*- coding: utf-8 -*-
"""P3w-O2：approve 必须消费待审行自身的来源与结构化载荷（端到端判别式用例）。

背景（P3fix-a 审核 M2）：P3a 已把 LLM 抽取的候选连同 supplier_id / fact_kind /
fact_key / fact_value 写进 pending_memory，但 approve 路径把它们全丢了 ——
`api_memory.approve_pending` 不透传，`rag.approve_pending_to_memory` 还把
source_kind 硬编码成 'feedback_distilled'。后果不是"功能失效"而是"数据被编"：
抽取候选被记成用户反馈蒸馏（而 source_kind 正是 trust_level 与「低可信不得覆盖高可信」
防投毒判定的输入），结构化五列永远 NULL。

本用例走完整链路（HTTP 端点 → api_memory 透传 → rag 落库 → DB 行 + 向量），
断言的都是**裸 SQL 读出的列值**与**向量层实测**，不是 ORM 属性上的假象：

1. 结构化候选：source_kind='extract'、supplier_id 非 NULL 且等于档案 id、
   fact_kind / fact_key / fact_value 五列真实落库；同 memory_id 的向量可被检索注入。
2. 旧 pending 行（无载荷）：仍走自由文本路径，source_kind 回落 feedback_distilled、
   载荷列保持 NULL（向后兼容，行为与新增形参前一致）。
3. 载荷非法（白名单外的 kind）：不写半个结构化行、不阻断 approve —— 回落自由文本且
   留下 warning（I2「降级可接受、静默不行」），来源仍如实透传。

判别式说明（反证三处 md5 见交付报告，不写进用例）：
- 把 `rag.approve_pending_to_memory` 的 source_kind 改回硬编码 'feedback_distilled'
  → 用例 1 的 source_kind 断言必须失败；
- 把结构化分支强制走 `fact = None`（等于不消费载荷）→ 用例 1 的 fact_kind 断言必须失败；
- 三者取值（来源 / 载荷 / 非法载荷）各写一条，任一分支被改坏都有对应用例报红。

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 库/向量库写入。
"""

import importlib
import logging
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）。
# app.db 在 import 期执行 _make_engine() 的迁移块（ALTER），不带 DB_PATH 就会打到 live 库。
_TMP = tempfile.mkdtemp(prefix="memory_pending_approve_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_pending_approve.db")
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

_FACT_PAYLOAD_COLUMNS = ("fact_kind", "fact_key", "fact_value")


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与既有记忆用例同款隔离）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"pa_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _admin():
    return {"X-Role": "admin"}


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
    """插一条供应商档案，返回其 id。

    why 必须真有档案：`db._memory_anchor_for_write` 只认"档案存在"的锚点，不存在的 id
    会被告警并回落名字解析 —— 没有档案就无法验证锚点是否真的落库。
    """
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


def _vector_ids(tenant_id, mid):
    """该 memory_id 在向量层命中的分块 id 列表（空 = 没写向量）。"""
    return rag._store(tenant_id)._collection.get(where={"memory_id": mid})["ids"]


# -------------------------------------------------------------
# 1. 结构化候选：五列真实落库 + 来源如实 + 向量可检索
# -------------------------------------------------------------
def test_approve_consumes_structured_payload_end_to_end():
    _db = _fresh_db()
    vendor = "抽取候選商戶E2E"
    sid = _seed_supplier(_db.DB_PATH, vendor)
    pid, is_new = _db.enqueue_pending_memory(
        vendor, "alias：本吧 → 本地牛腩", tenant_id="default",
        source_kind="extract", source_receipt_ids=[777],
        supplier_id=sid, fact_kind="alias", fact_key="alias:本吧",
        fact_value="本地牛腩")
    assert is_new is True

    client = _client()
    resp = client.post(f"/api/memory/pending/{pid}/approve", headers=_admin())
    assert resp.status_code == 200, resp.text

    rows = _db.list_vendor_memory(vendor, tenant_id="default")
    assert len(rows) == 1, "一次 approve 应恰好落一条记忆"
    mid = rows[0]["memory_id"]
    raw = _raw_vendor_row(_db.DB_PATH, mid)
    assert raw is not None, "落库后必须能按 memory_id 从裸 SQL 读到该行"

    # 来源不再被硬编码：抽取候选必须记成 extract，而不是 feedback_distilled
    assert raw["source_kind"] == "extract", (
        f"来源被谎报：实际 {raw['source_kind']!r}（抽取候选记成用户反馈，"
        "会让「低可信不得覆盖高可信」的防投毒判据建立在错的来源上）")
    # 主体锚点真实落库（NULL 表示读侧根本按不了 (tenant, supplier) 收窄）
    assert raw["supplier_id"] is not None, "supplier_id 未落库（NULL）"
    assert int(raw["supplier_id"]) == sid, (
        f"锚点必须等于档案 id {sid}，实际 {raw['supplier_id']!r}（挂错供应商）")
    # 结构化三列真实落库（不是只在 pending 里）
    assert raw["fact_kind"] == "alias"
    assert raw["fact_key"] == "alias:本吧"
    assert raw["fact_value"] == "本地牛腩"
    # source_ref 仍可回溯触发单据
    assert "777" in (raw["source_ref"] or ""), "source_ref 丢失触发来源"

    # 向量层同一 memory_id 可被检索到（DB↔向量一致，检索通道才可能注入）
    assert _vector_ids("default", mid), "结构化路径也必须写向量（否则巡检报缺向量）"
    ctx = rag.retrieve_context(vendor, tenant_id="default")
    assert "本吧" in ctx, f"记忆未被注入：{ctx!r}"

    # 状态机流转正常
    assert _db.get_pending_memory(pid, tenant_id="default")["status"] == "approved"


# -------------------------------------------------------------
# 2. 旧 pending 行（无载荷）：自由文本路径不变 + 载荷列保持 NULL
# -------------------------------------------------------------
def test_approve_legacy_pending_row_keeps_free_text_path():
    _db = _fresh_db()
    vendor = "遺留審批商戶"
    pid, _ = _db.enqueue_pending_memory(
        vendor, "供應商 反饋糾偏：品名認錯要糾正", tenant_id="default")

    client = _client()
    resp = client.post(f"/api/memory/pending/{pid}/approve", headers=_admin())
    assert resp.status_code == 200, resp.text

    rows = _db.list_vendor_memory(vendor, tenant_id="default")
    assert len(rows) == 1
    raw = _raw_vendor_row(_db.DB_PATH, rows[0]["memory_id"])
    assert raw["source_kind"] == "feedback_distilled", (
        "无载荷的旧 pending 行必须回落 feedback_distilled（兼容既有契约）")
    for col in _FACT_PAYLOAD_COLUMNS:
        assert raw[col] is None, (
            f"自由文本路径不得写 {col}（实际 {raw[col]!r}）：NULL 表示「没有结构化载荷」")
    assert "反饋糾偏" in (raw["notes"] or "")


# -------------------------------------------------------------
# 3. 载荷非法：闸门拦下 + 可见降级 + 来源仍如实透传
# -------------------------------------------------------------
def test_approve_invalid_payload_falls_back_with_warning(caplog):
    _db = _fresh_db()
    vendor = "非法載荷商戶"
    sid = _seed_supplier(_db.DB_PATH, vendor)
    # kind='sku' 不在 memory_facts 白名单里（入队不校验，approve 侧必须自己挡）
    pid, _ = _db.enqueue_pending_memory(
        vendor, "sku：x → y", tenant_id="default", source_kind="extract",
        supplier_id=sid, fact_kind="sku", fact_key="sku:x", fact_value="y")

    client = _client()
    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        resp = client.post(f"/api/memory/pending/{pid}/approve", headers=_admin())
    assert resp.status_code == 200, resp.text

    rows = _db.list_vendor_memory(vendor, tenant_id="default")
    assert len(rows) == 1, "非法载荷不得阻断 approve（记录该记，只是降级）"
    raw = _raw_vendor_row(_db.DB_PATH, rows[0]["memory_id"])
    for col in _FACT_PAYLOAD_COLUMNS:
        assert raw[col] is None, (
            f"过不了写入闸门的候选不得写成结构化行：{col}={raw[col]!r}")
    assert raw["source_kind"] == "extract", "来源透传与载荷是否合法无关"
    assert any("回落自由文本落库" in rec.getMessage() for rec in caplog.records), (
        "降级必须留痕（静默降级会让「候选一直进不来」永久不可观测）")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

# -*- coding: utf-8 -*-
"""D8：pending 行的 provenance 解析失败不得静默清空（判别式用例）。

背景（修复前）：`api_memory.approve_pending` 里
    refs = json.loads(row.get('source_receipt_ids_json') or '[]')
的 except 分支把 refs 置空且**无任何痕迹**。而 refs 正是 approve 落库时写进
vendor_memory.source_ref 的 provenance 载体 —— 它坏了就静默变成"无来源"，
事后无法回溯这条记忆是由哪几张单据得来的（本仓高发的「异常被静默吞掉导致功能
静默失效」类）。

本用例的三条判据缺一不可：
(a) approve 仍成功（降级不等于阻断：**不得因 provenance 损坏而拒绝批准**）；
(b) 落库行的 source_ref 为 "[]"（降级结果正确、可预期）；
(c) 日志里留下该 warning。

(c) 之前先做 caplog 自校准：注入一条已知哨兵日志并断言它被采集到，证明采集
通道有效 —— 否则"没抓到 warning"无法区分是产品真的没告警、还是采集器根本没接上
（本仓已发生过恒真用例给人"已被守护"的假象）。

判别式（反证三处 md5 见交付报告，不写进用例）：把新增的 logger.warning 去掉，
(c) 必须失败 —— 自校准哨兵仍会被抓到，故失败点精确落在"产品 warning 缺失"，
而不是采集通道失效。

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
_TMP = tempfile.mkdtemp(prefix="memory_pending_provenance_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_pending_provenance.db")
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


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与既有记忆用例同款隔离）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"prov_{uuid.uuid4().hex[:8]}.db")
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


def _corrupt_source_receipt_ids(path, pid, bad_text):
    """把 pending 行的 provenance 载体改成非法内容。

    why 不通过 `enqueue_pending_memory` 构造：它内部固定 json.dumps 一个数组，
    永远产不出损坏载体 —— 只有直接改库才能复现"历史脏行"这一真实形态。
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE pending_memory SET source_receipt_ids_json=? WHERE id=?",
            (bad_text, pid))
        conn.commit()
    finally:
        conn.close()


def _raw_pending_text(path, pid):
    """读回被篡改后的载体原文（确认脏数据确实落到了库里，而不是改错了地方）。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT source_receipt_ids_json FROM pending_memory WHERE id=?",
            (pid,)).fetchone()
        return row[0] if row is not None else None
    finally:
        conn.close()


# -------------------------------------------------------------
# 主用例：非法 JSON → approve 仍成功 + source_ref 降级为 "[]" + 有 warning
# -------------------------------------------------------------
def test_broken_provenance_json_degrades_with_warning(caplog):
    _db = _fresh_db()
    vendor = "來源損壞商戶"
    pid, _ = _db.enqueue_pending_memory(
        vendor, "供應商 反饋糾偏：來源損壞仍要能批准", tenant_id="default")
    _corrupt_source_receipt_ids(_db.DB_PATH, pid, "{这不是合法 JSON")
    assert _raw_pending_text(_db.DB_PATH, pid) == "{这不是合法 JSON", (
        "用例构造失败：脏载体没落进库里，后续断言会测到别的路径")

    client = _client()
    with caplog.at_level(logging.WARNING, logger="app.api_memory"):
        # caplog 自校准：先注入一条已知哨兵并断言它被采集到，证明采集通道有效。
        # 没有这一步，(c) 的失败就无法区分「产品没告警」与「采集器没接上」。
        logging.getLogger("app.api_memory").warning("[calibration] 采集通道自检哨兵")
        resp = client.post(f"/api/memory/pending/{pid}/approve", headers=_admin())

    # (a) 降级不等于阻断：provenance 坏了也必须能批准成功
    assert resp.status_code == 200, resp.text
    assert any("[calibration]" in rec.getMessage() for rec in caplog.records), (
        "caplog 自校准失败：采集通道没接上，本用例的 warning 断言不可信")

    rows = _db.list_vendor_memory(vendor, tenant_id="default")
    assert len(rows) == 1, "provenance 损坏不得阻断 approve（记忆本身该照常落库）"
    raw = _raw_vendor_row(_db.DB_PATH, rows[0]["memory_id"])
    assert raw is not None

    # (b) 降级结果正确：source_ref 是空数组（不是 NULL、也不是半截字符串）
    assert raw["source_ref"] == "[]", (
        f"损坏载体必须稳定降级为 '[]'，实际 {raw['source_ref']!r}")

    # (c) 必须留痕：行 id 与原始文本都要出现在 warning 里，否则事后无法定位脏行
    warnings = [rec.getMessage() for rec in caplog.records
                if rec.levelno >= logging.WARNING]
    hit = [m for m in warnings if "provenance 载体解析失败" in m]
    assert hit, (
        f"provenance 解析失败必须留 warning（静默清空会让「记忆无来源」永久不可观测）：{warnings!r}")
    assert str(pid) in hit[0], "warning 必须带上 pending 行 id，否则无法定位脏行"
    assert "{这不是合法 JSON" in hit[0], "warning 必须带上被截断的原始文本，否则无法排查"

    # 状态机流转正常（与成功路径一致）
    assert _db.get_pending_memory(pid, tenant_id="default")["status"] == "approved"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

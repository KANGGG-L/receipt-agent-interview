# -*- coding: utf-8 -*-
"""FP-4：TTL 到期行（valid_to < now）不占 W-4 容量计数。

缺陷：`db._evict_vendor_memory_over_capacity` 的容量计数只看 status=='active'、不看
valid_to。D4 设计下到期行 status 不迁移，于是到期行永远占坑 —— 容量满员后每次写入
都会误淘一条 active 好行。修复：容量统计排除「active 且 valid_to 非空、可解析、且
now > valid_to（严格大于）」的行；到期行本身不迁移/不删/不归档（治理视图仍可见）。

本文件覆盖 SPEC FP-4 §4.4 的判别式判据：
(c1) 容量满员（5 条 active 无 valid_to）+ 写入第 6 条（valid_to 已过期）：
     第 6 条入库成功、5 条原 active 行全部仍是 active、无淘汰审计 ——
     反证：把排除逻辑去掉（到期行计入），此用例必然翻红（会误淘一条好行）；
(c2) 阳性对照：同样写满 6 条但全部无过期行 → 触发淘汰（防把容量闸整个弄失效）；
(c3) 边界逐个构造：valid_to 为 NULL / 非法格式 / 恰好等于 now → 一律**计入**容量
     （解析不出来宁可算入，与 _memory_row_expired 读侧口径一致；恰等按严格 > 计入）；
     过期 1 小时 → 不计入。frozen 时钟保证「恰等」确定性。

全部离线：每例独立临时 DB / RAG 目录，零外部调用、零 live 写入。
"""

import importlib
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_capacity_ttl_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_capacity_ttl.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from app.services import settings_service  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}

_LIMIT_KEY = "memory_max_active_per_vendor"


def _fresh_db():
    """每个用例独立数据库文件 + 独立向量目录，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"capttl_{uuid.uuid4().hex[:8]}.db")
    os.environ["RAG_DIR"] = os.path.join(_TMP, f"rag_{uuid.uuid4().hex[:8]}")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _set_limit(n):
    settings_service.set_value(_LIMIT_KEY, n)


def _ingest(vendor, tenant, text, notes="", valid_to=None):
    """走生产写入链路落一条记忆（DB 行 + 触发容量淘汰检查），返回其 memory_id。"""
    mid, _is_new = db.upsert_vendor_memory(
        vendor, notes, text, tenant_id=tenant, source_kind="manual",
        valid_to=valid_to)
    return mid


def _rows(vendor, tenant):
    return db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)


def _status_of(vendor, tenant, memory_id):
    for r in _rows(vendor, tenant):
        if r["memory_id"] == memory_id:
            return r["status"]
    return None


def _set_valid_to(_db, memory_id, value):
    """直接改行的 valid_to（None → 真正的 NULL，区别于缺省空串）。"""
    s = _db.get_session()
    try:
        row = s.query(_db._VendorMemoryRow).filter(
            _db._VendorMemoryRow.memory_id == memory_id).first()
        assert row is not None
        row.valid_to = value
        s.commit()
    finally:
        s.close()


def _eviction_audits():
    return [e for e in db.read_system_audit_log()
            if e.get("action") == "capacity_eviction"]


# -------------------------------------------------------------
# (c1) 容量满员 + 写入一条已过期记忆：不淘汰任何 active 好行
# -------------------------------------------------------------
def test_expired_write_at_full_capacity_does_not_evict():
    _db = _fresh_db()
    vendor, tenant = "容量TTL商戶A", "capttl_t1"
    _set_limit(5)

    # 5 条无 valid_to 的 active 行 = 恰好满员
    mids = [_ingest(vendor, tenant, f"品項 {i} 單價 {i}", notes=f"seed{i}")
            for i in range(5)]
    assert all(r["status"] == "active" for r in _rows(vendor, tenant))

    # 第 6 条写入即「valid_to 已过期」的记忆：不占容量坑 → 不得触发淘汰。
    # （修复前：计数 6 > 5，会误淘一条好行 —— 本用例的判别点。）
    expired_mid = _ingest(vendor, tenant, "過期品項 7斤", notes="expired",
                          valid_to=(datetime.now() - timedelta(hours=1)).isoformat())

    rows = _rows(vendor, tenant)
    assert len(rows) == 6, rows
    # 5 条原 active 行全部仍是 active
    assert all(_status_of(vendor, tenant, m) == "active" for m in mids), mids
    # 过期行照常入库、且 status 不迁移（D4：到期只影响召回与容量，不改治理态）
    assert _status_of(vendor, tenant, expired_mid) == "active"
    assert _eviction_audits() == [], _eviction_audits()


# -------------------------------------------------------------
# (c2) 阳性对照：同样写满 6 条但全部无过期行 → 触发淘汰
# -------------------------------------------------------------
def test_all_valid_rows_still_evict_over_limit():
    _db = _fresh_db()
    vendor, tenant = "容量TTL商戶B", "capttl_t2"
    _set_limit(5)

    for i in range(6):
        _ingest(vendor, tenant, f"品項 {i} 單價 {i}", notes=f"seed{i}")

    rows = _rows(vendor, tenant)
    assert len(rows) == 6, rows
    active = [r for r in rows if r["status"] == "active"]
    archived = [r for r in rows if r["status"] == "archived"]
    # 6 > 5：淘汰 1 条（最旧的，同分按 created_at/rowid 破平）——容量闸未被 FP-4 弄失效
    assert len(active) == 5 and len(archived) == 1, (active, archived)
    audits = _eviction_audits()
    assert audits, "无过期行的超限写入必须照常触发淘汰"
    old_payload = audits[-1]["old"] if isinstance(audits[-1]["old"], dict) else {}
    # 本场景没有任何行被排除出计数
    assert old_payload.get("excluded_expired") == 0, old_payload


# -------------------------------------------------------------
# (c3) 边界：NULL / 非法格式 / 恰好等于 now → 计入容量；过期 1 小时 → 不计入
# -------------------------------------------------------------
def test_boundary_rows_count_into_capacity(monkeypatch):
    _db = _fresh_db()
    vendor, tenant = "容量TTL商戶C", "capttl_t3"
    limit = 3
    _set_limit(limit)

    # 恰等边界需要确定性：冻结 db 层 datetime.now() 到秒级 T（_parse_iso_dt 仍用真
    # fromisoformat，解析出的 valid_to 与 T 严格相等 → 按「严格 >」计入容量）。
    T = datetime.now().replace(microsecond=0)

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return T

    monkeypatch.setattr(db, "datetime", _FrozenDT)

    # 3 行边界行：NULL / 非法格式 / 恰好 == now（全部应**计入**）
    m_null = _ingest(vendor, tenant, "邊界NULL", notes="b_null")
    _set_valid_to(_db, m_null, None)
    m_bad = _ingest(vendor, tenant, "邊界非法", notes="b_bad")
    _set_valid_to(_db, m_bad, "not-a-date")
    m_eq = _ingest(vendor, tenant, "邊界恰等", notes="b_eq",
                   valid_to=T.isoformat())

    # 此时 3 条 active 已达上限（若边界行不计入，这里 count 只有 0~2 也到不了上限，
    # 下一步写入就不会淘汰 —— 用「写满即溢出」反证边界行确实被计入）
    assert _eviction_audits() == [], _eviction_audits()

    # 再写 1 条好的 → 计数 4 > 3 → 必须淘汰 1 条；若任一边界行被错误排除，
    # 计数 <= 3、不会产生淘汰审计 —— 淘汰发生本身即「边界行计入」的证据。
    _ingest(vendor, tenant, "普通品項 8斤", notes="normal")
    audits = _eviction_audits()
    assert audits, "NULL/非法/恰等边界行必须计入容量（计数 4 > 上限 3 应触发淘汰）"
    old_payload = audits[-1]["old"] if isinstance(audits[-1]["old"], dict) else {}
    assert old_payload.get("excluded_expired") == 0, old_payload
    # 被淘汰的是最旧的边界行（同分按 created_at 破平）；其余行不动
    statuses = {m: _status_of(vendor, tenant, m)
                for m in (m_null, m_bad, m_eq)}
    assert sorted(statuses.values()).count("archived") == 1, statuses
    # 淘汰不迁移边界行的语义：恰等行未被误判过期（它最多被按普通候选淘汰，不因 valid_to 单独处理）
    assert all(v in ("active", "archived") for v in statuses.values())


def test_expired_1h_row_excluded_from_capacity(monkeypatch):
    """F4-5 对照面：valid_to 已过 1 小时（严格 >）→ 不计入。"""
    _db = _fresh_db()
    vendor, tenant = "容量TTL商戶D", "capttl_t4"
    limit = 2
    _set_limit(limit)

    _ingest(vendor, tenant, "好品項 1", notes="good1")
    m_expired = _ingest(vendor, tenant, "過期品項 2", notes="expired1h",
                        valid_to=(datetime.now() - timedelta(hours=1)).isoformat())
    # 好行 1 + 过期 1：若过期行被计入，此时已达上限；排除后 count=1
    assert _eviction_audits() == [], _eviction_audits()

    # 再写 1 条好的 → 计入后 count=2 == limit → 仍不淘汰（过期行没占坑）
    _ingest(vendor, tenant, "好品項 3", notes="good3")
    assert _eviction_audits() == [], _eviction_audits()
    assert _status_of(vendor, tenant, m_expired) == "active"  # 不迁移不归档

    # 第 3 条好的 → count=3 > 2 → 淘汰 1 条（最旧的好行），审计含 excluded_expired=1
    _ingest(vendor, tenant, "好品項 4", notes="good4")
    audits = _eviction_audits()
    assert audits, "超限后必须照常淘汰"
    old_payload = audits[-1]["old"] if isinstance(audits[-1]["old"], dict) else {}
    assert old_payload.get("excluded_expired") == 1, old_payload


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))

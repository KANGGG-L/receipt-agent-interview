# -*- coding: utf-8 -*-
"""P1-T3：同 key 冲突 supersede + TTL 过期不注入 测试。

覆盖：
1. 同 (tenant, vendor, fact_key) 写两次 → 只有最新一条 active 且**只注入最新一条**；
   旧行保留为 status='superseded' + superseded_by 指向新 memory_id（历史可查）
2. 连写三次 → supersede 链完整（旧1->新2、旧2->新3），仍只有一条 active
3. 读侧同键去重（第二道防线）：人为造出同键两条 active 行（模拟 supersede 两步
   操作中途失败/外部脚本直写）→ 仍只注入最新一条
4. TTL（created_at + memory_ttl_days）过期行不注入，但治理视图仍可见
5. valid_to 过期行不注入，但治理视图仍可见（TTL=0 时同样生效）
6. memory_ttl_days=0（缺省）＝永不过期：千天前写入的行照样注入（防"默认即行为
   变更"）
7. 空 fact_key / 空 vendor 显式拒绝（ValueError）且不落行 —— 空键会让 supersede
   退化成"永远追加"，是本任务要消除的污染源
8. 遗留物理形态重建路径（pre-T2 库）重建后同样带三列（防「同一根因多份实现只改
   一份」：ALTER 迁移块与重建的 target_cols 是两份实现）

判别式说明（哪条断言由哪个机制提供，反证记录见任务报告）：
- 断言 A（include_archived=True 视图里旧行 status='superseded'）由**写侧 supersede**
  独立支撑；
- 断言 B（默认视图只返 1 条、注入文本无旧 marker）由**读侧过滤**独立支撑；
- 断言 C（第 3 条用例）由**读侧同键去重**独立支撑。

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
"""

import importlib
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_supersede_ttl_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_supersede.db")
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
    """每个用例独立数据库文件，避免跨用例串扰（与 P1-T1/T2 用例同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"supersede_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _set_ttl(_db, days):
    """写 TTL 配置（settings 实时读库，故直接落 app_settings 即生效）。"""
    _db.set_app_setting("memory_ttl_days", str(int(days)))


def _iso(delta_days):
    return (datetime.now() + timedelta(days=delta_days)).isoformat(timespec="seconds")


def _sql(path, stmt, params=()):
    conn = sqlite3.connect(path)
    try:
        conn.execute(stmt, params)
        conn.commit()
    finally:
        conn.close()


def _raw_active_row(path, mid, vendor, tenant, key, notes, ts):
    """裸 SQL 插一条 active 记忆行：用来造出"同键两条 active"的异常态。

    为什么必须绕开写侧：supersede 正常工作时同键只会剩一条 active，异常态只能
    人为构造（正是读侧去重要防的场景）。decay_score 给 0.7（高于归档阈值）以免
    被注入侧的另一道门挡住，保证测的是去重本身。
    """
    _sql(path, (
        "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample,"
        " version, source_kind, source_ref, created_at, updated_at, hit_count,"
        " last_hit_at, decay_score, status, fact_key, superseded_by, valid_to)"
        " VALUES (?,?,?,?,'',1,'manual','[]',?,?,0,'',0.7,'active',?,'','')"
    ), (mid, vendor, tenant, notes, ts, ts, key))


# -------------------------------------------------------------
# 1. 同键写两次：只 latest active、只 latest 注入、旧行 supersede 留痕
# -------------------------------------------------------------
def test_same_fact_key_supersedes_old_row():
    _db = _fresh_db()
    vendor, tenant, key = "取代商戶", "t3_t1", "unit:斤"
    r1 = _db.save_memory_fact(
        tenant, vendor, key, notes="OLD_MARKER 單位習慣：斤", sample="")
    r2 = _db.save_memory_fact(
        tenant, vendor, key, notes="NEW_MARKER 單位習慣：公斤", sample="")

    assert r1["is_new"] is True and r2["is_new"] is True
    assert r2["superseded"] == [r1["memory_id"]], \
        f"第二次写入必须 supersede 第一条，实际 {r2['superseded']}"

    # 断言 B：默认（注入口径）只返最新一条
    active = _db.list_vendor_memory(vendor, tenant_id=tenant)
    assert len(active) == 1, f"同键应只剩一条 active，实际 {len(active)}"
    assert "NEW_MARKER" in active[0]["notes"]
    assert active[0]["fact_key"] == key, "键必须落库（否则后续 supersede 找不到它）"

    # 断言 A：旧行没被删，是 superseded 状态且指向新行（历史可查/可回溯）
    hist = _db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)
    assert len(hist) == 2, "旧行必须保留以存历史，不得物理删除"
    old = [h for h in hist if h["memory_id"] == r1["memory_id"]][0]
    assert old["status"] == "superseded"
    assert old["superseded_by"] == r2["memory_id"]

    # 注入面（真实检索链路）：新值进、旧值不进
    ctx = rag.retrieve_context(vendor, tenant_id=tenant)
    assert "NEW_MARKER" in ctx, "最新事实应被注入"
    assert "OLD_MARKER" not in ctx, "被 supersede 的旧事实不得再注入"


# -------------------------------------------------------------
# 2. 连写三次：supersede 链完整，active 恒为 1
# -------------------------------------------------------------
def test_supersede_chain_over_three_writes():
    _db = _fresh_db()
    vendor, tenant, key = "鏈式商戶", "t3_t2", "alias:本吧"
    mids = []
    for i in range(3):
        r = _db.save_memory_fact(
            tenant, vendor, key, notes=f"CHAIN_MARKER_{i}", sample="")
        mids.append(r["memory_id"])
    assert len(set(mids)) == 3, "每次写入必须是新行（追加式不可变）"

    active = _db.list_vendor_memory(vendor, tenant_id=tenant)
    assert len(active) == 1 and "CHAIN_MARKER_2" in active[0]["notes"]

    hist = {h["memory_id"]: h for h in _db.list_vendor_memory(
        vendor, tenant_id=tenant, include_archived=True)}
    assert len(hist) == 3
    assert hist[mids[0]]["superseded_by"] == mids[1], "链：旧1 -> 新2"
    assert hist[mids[1]]["superseded_by"] == mids[2], "链：旧2 -> 新3"
    assert hist[mids[2]]["superseded_by"] in ("", None), "最新一条不被任何行取代"
    assert hist[mids[0]]["status"] == hist[mids[1]]["status"] == "superseded"


# -------------------------------------------------------------
# 3. 读侧同键去重（第二道防线）
# -------------------------------------------------------------
def test_read_side_dedupe_keeps_newest_only():
    _db = _fresh_db()
    vendor, tenant, key = "重複商戶", "t3_t3", "unit:件"
    _raw_active_row(_db.DB_PATH, "dup_old", vendor, tenant, key,
                    "DUP_OLD_MARKER", _iso(-2))
    _raw_active_row(_db.DB_PATH, "dup_new", vendor, tenant, key,
                    "DUP_NEW_MARKER", _iso(-1))

    rows = _db.list_vendor_memory(vendor, tenant_id=tenant)
    assert [r["memory_id"] for r in rows] == ["dup_new"], \
        "同键两条 active（异常态）时，读侧必须只保留最新一条（rowid DESC 首见）"

    ctx = rag.retrieve_context(vendor, tenant_id=tenant)
    assert "DUP_NEW_MARKER" in ctx and "DUP_OLD_MARKER" not in ctx

    # 治理视图不过滤：两条都可见，供人工排查为何出现同键并存
    hist = _db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)
    assert len(hist) == 2


# -------------------------------------------------------------
# 4. TTL 过期（created_at + memory_ttl_days）不注入，治理视图仍可见
# -------------------------------------------------------------
def test_ttl_expired_by_created_at_not_injected():
    _db = _fresh_db()
    vendor, tenant = "過期商戶A", "t3_t4"
    _set_ttl(_db, 7)
    _db.upsert_vendor_memory(
        vendor, "TTL_OLD_MARKER 30 天前的記憶", "", tenant_id=tenant,
        source_kind="manual", created_at=_iso(-30))

    assert _db.list_vendor_memory(vendor, tenant_id=tenant) == [], \
        "created_at + TTL 已过的行不得出现在默认（注入）口径"
    ctx = rag.retrieve_context(vendor, tenant_id=tenant)
    assert "TTL_OLD_MARKER" not in ctx

    hist = _db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)
    assert len(hist) == 1, "治理视图必须仍能看到过期行（否则等于静默丢数据）"


# -------------------------------------------------------------
# 5. valid_to 过期不注入（TTL=0 时同样生效）
# -------------------------------------------------------------
def test_valid_to_expired_not_injected_independent_of_ttl():
    _db = _fresh_db()
    vendor, tenant, key = "過期商戶B", "t3_t5", "layout:手寫長單"
    _set_ttl(_db, 0)  # 只用 valid_to 单条控制过期
    r = _db.save_memory_fact(tenant, vendor, key, notes="VOID_MARKER 已失效")
    assert _db.list_vendor_memory(vendor, tenant_id=tenant)[0]["valid_to"] == "", \
        "TTL=0 时不应写显式失效期"

    _sql(_db.DB_PATH, "UPDATE vendor_memory SET valid_to = ? WHERE memory_id = ?",
         (_iso(-1), r["memory_id"]))

    assert _db.list_vendor_memory(vendor, tenant_id=tenant) == []
    ctx = rag.retrieve_context(vendor, tenant_id=tenant)
    assert "VOID_MARKER" not in ctx
    hist = _db.list_vendor_memory(vendor, tenant_id=tenant, include_archived=True)
    assert len(hist) == 1 and hist[0]["valid_to"] != ""


# -------------------------------------------------------------
# 6. TTL=0（缺省）＝永不过期：防「默认值即行为变更」
# -------------------------------------------------------------
def test_ttl_zero_means_never_expire():
    _db = _fresh_db()
    vendor, tenant = "長壽商戶", "t3_t6"
    assert _db.memory_ttl_days() == 0, "缺省 TTL 必须是 0（不过期）"
    _db.upsert_vendor_memory(
        vendor, "FOREVER_MARKER 千天前的記憶", "", tenant_id=tenant,
        source_kind="manual", created_at=_iso(-1000))

    rows = _db.list_vendor_memory(vendor, tenant_id=tenant)
    assert len(rows) == 1, "TTL=0 时不得因创建时间久远而停止注入"
    assert "FOREVER_MARKER" in rag.retrieve_context(vendor, tenant_id=tenant)


# -------------------------------------------------------------
# 7. 空 fact_key / 空 vendor 显式拒绝，且不落行
# -------------------------------------------------------------
def test_empty_fact_key_or_vendor_rejected_without_write():
    _db = _fresh_db()
    for bad_key in ("", "   ", None):
        with pytest.raises(ValueError):
            _db.save_memory_fact("t3_t7", "拒絕商戶", bad_key, notes="X")
    with pytest.raises(ValueError):
        _db.save_memory_fact("t3_t7", "", "unit:斤", notes="X")
    assert _db.list_vendor_memory("拒絕商戶", tenant_id="t3_t7",
                                  include_archived=True) == [], \
        "被拒绝的写入不得留下任何行"


# -------------------------------------------------------------
# 8. 遗留物理形态重建路径同样带三列（防两份实现只改一份）
# -------------------------------------------------------------
def test_legacy_rebuild_includes_new_columns():
    path = os.path.join(_TMP, f"legacy_{uuid.uuid4().hex[:8]}.db")
    suffix = uuid.uuid4().hex[:6]
    conn = sqlite3.connect(path)
    c = conn.cursor()
    # pre-T2 形态：vendor 单键主键，仅 notes/sample 两列
    c.execute("CREATE TABLE vendor_memory (vendor VARCHAR NOT NULL, notes TEXT,"
              " sample TEXT, PRIMARY KEY (vendor))")
    c.execute("INSERT INTO vendor_memory (vendor, notes, sample) VALUES (?,?,?)",
              (f"虛擬商戶{suffix}", "版式備註A", "樣例A"))
    conn.commit()
    conn.close()

    os.environ["DB_PATH"] = path
    importlib.reload(db)  # 触发重建 + 幂等 ALTER
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(vendor_memory)")}
        rows = conn.execute("SELECT vendor, notes, status FROM vendor_memory").fetchall()
        conn.close()
        assert {"fact_key", "superseded_by", "valid_to"} <= cols, \
            f"重建路径必须同步带 P1-T3 三列，实际 {sorted(cols)}"
        assert len(rows) == 1 and rows[0][1] == "版式備註A", "重建不得丢行"
        assert rows[0][2] == "active"
    finally:
        # 复原到本模块的隔离库，避免后续用例（同一进程）拿到这个遗留库
        _fresh_db()

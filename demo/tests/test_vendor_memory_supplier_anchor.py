# -*- coding: utf-8 -*-
"""P2-T1：VendorMemory 的 supplier_id 主体锚点（写侧解析 / 读侧优先 / 回填脚本）测试。

背景（why 需要这一批）：记忆此前只按 `vendor` 自由文本归属，读侧靠
`rag._vendor_related` 的子串/核心词猜。这套名字口径有两个反向的毛病：
1. **漏召回**：同一供应商的不同写法（中段英文注、英译别名）不是子串关系时命中不了；
2. **错召回**：互相包含的不同商号（"永隆" 与 "永隆行"）会互相放行 → 他商记忆进
   prompt（这条用例把 `_vendor_related` 为 True 直接断言出来，证明锚点不是装饰）。
supplier_id 是供应商档案主键：同一 id 才是同一供应商，名字降为别名。

覆盖：
1. `db.resolve_supplier_id` 三步口径（精确名 → 归一 key → 拿不准返回 None），
   且**绝不新建供应商档案**；
2. 写侧接线：`upsert_vendor_memory` 自动解析并落 supplier_id（两条写入链路共用它）；
   解析不出时留 NULL（读侧回退名字口径，非降级开关）；
3. 读侧优先锚点：同一供应商的多种写法命中同一条记忆（其中**中段英文注**那一组是
   判别式 —— 关掉锚点分支即失败，见任务报告的反证）；
4. 读侧防泄漏：锚点把"互相包含的另一个商号"的记忆挡在外面；
5. 锚点按租户收窄（跨租户不命中）；
6. 锚点零命中且无 vendor 名时返回空列表，**绝不返回全量**（本仓已出现 3 次的
   越权形态）；
7. 回填脚本：缺省预演不写库、--apply 才写、重跑零改动（幂等），且归档行也回填
   （live 现有 9 行全部 archived，不回填则这些记忆将来 revive 时归属丢失）。
8. J3 写入侧锚点的**来源**（补齐 P2-T1 的最后一环）：调用方给的权威 id 优先于按名
   解析；拿不到锚点时写 NULL **且必须留痕**；跨租户的显式锚点被拒绝；approve 端点
   端到端以单据行上的 `receipts.supplier_id` 锚定记忆；`rag.ingest_memory` 透传。

判别式说明：第 3 条用例里"档案全名（中段英文注）"那组断言由**锚点分支**独立支撑；
繁简/空格两组在纯名字口径下同样通过，属于行为保持型守卫，不是判别式。第 4 条由锚点
分支独立支撑（用例内先断言名字口径确实会放行，故它必然在无锚点时失败）。第 8 条的
三条（显式锚点优先 / ingest_memory 透传 / approve 端到端）都构造成"按名解析会落到
另一个 id"的局面，故各自在缺该接线时必然失败（反证实测见交付报告）。

全部离线假数据：模块级临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
"""

import importlib
import logging
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="memory_supplier_anchor_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_anchor.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import app.services.rag as rag  # noqa: E402
from scripts import backfill_vendor_memory_supplier as backfill  # noqa: E402

rag.PERSIST_DIR = os.environ["RAG_DIR"]
rag._vs = None
rag._vs_tenant = {}


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与 P1-T1/T2/T3 用例同款）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"anchor_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    rag.PERSIST_DIR = os.environ["RAG_DIR"]
    rag._vs = None
    rag._vs_tenant = {}
    return db


def _sql(stmt, params=()):
    """直连目标库执行一条语句（用例内造历史形态用；与 db.DB_PATH 同库）。"""
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(stmt, params)
        conn.commit()
    finally:
        conn.close()


def _sql_one(stmt, params=()):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        row = conn.execute(stmt, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _supplier_id_of(memory_id):
    return _sql_one(
        "SELECT supplier_id FROM vendor_memory WHERE memory_id = ?", (memory_id,))


def _supplier_count():
    return int(_sql_one("SELECT COUNT(*) FROM suppliers") or 0)


def _mk_supplier(name, tenant_id="default"):
    sid, err = db.create_supplier(name, tenant_id=tenant_id)
    assert err is None, f"建供应商档案失败: {err}"
    assert sid is not None
    return int(sid)


# -------------------------------------------------------------
# 1. resolve_supplier_id 口径
# -------------------------------------------------------------
def test_resolve_supplier_id_exact_canonical_and_none():
    _fresh_db()
    sid = _mk_supplier("永記 (Wing Kee) 食品")
    before = _supplier_count()

    # 精确名
    assert db.resolve_supplier_id("永記 (Wing Kee) 食品") == sid
    # 归一 key：去英文括号注 + 繁转简 → 同一个档案
    assert db.resolve_supplier_id("永記食品") == sid
    assert db.resolve_supplier_id("永记食品") == sid
    # 拿不准 → None，且**绝不新建档案**
    assert db.resolve_supplier_id("完全不存在的商號 XYZ") is None
    assert db.resolve_supplier_id("") is None
    assert db.resolve_supplier_id(None) is None
    assert _supplier_count() == before, "解析不得新建供应商档案"


def test_resolve_supplier_id_scoped_by_tenant():
    """解析只在**同租户**内匹配：两个租户各有一份档案时，各自解析回自己的 id。

    注意 suppliers.name 有全局唯一约束，故两个租户的档案名不能逐字相同 —— 用
    归一 key 相同、写法不同的名字（繁简/英文注差异）构造。断言里带一条"跨租户查
    另一个租户的名字仍返回本租户档案"，证明匹配被 scoped 收窄而非全局乱撞。
    """
    _fresh_db()
    s1 = _mk_supplier("九龍豆品廠", tenant_id="t1")
    s2 = _mk_supplier("九龍豆品廠 (Kowloon Bean)", tenant_id="t2")
    assert s1 != s2 and db.get_supplier(s1, tenant_id="t2") is None

    assert db.resolve_supplier_id("九龍豆品廠", "t1") == s1
    assert db.resolve_supplier_id("九龍豆品廠 (Kowloon Bean)", "t2") == s2
    # 用 t2 的写法在 t1 里查：只能落到 t1 自己的档案（归一 key 命中），绝不跨租户
    assert db.resolve_supplier_id("九龍豆品廠 (Kowloon Bean)", "t1") == s1


# -------------------------------------------------------------
# 2. 写侧接线
# -------------------------------------------------------------
def test_upsert_writes_resolved_supplier_id():
    _fresh_db()
    sid = _mk_supplier("永記 (Wing Kee) 食品")
    mid, is_new = db.upsert_vendor_memory(
        "永記食品", "锚点用例标记A", "sample-A", tenant_id="default")
    assert is_new is True
    assert _supplier_id_of(mid) == sid, "写侧应自动解析并落 supplier_id"

    # 解析不出 → 留 NULL（读侧回退名字口径），且不新建档案
    before = _supplier_count()
    mid2, _ = db.upsert_vendor_memory(
        "無檔案商號 ZZZ", "锚点用例标记B", "sample-B", tenant_id="default")
    assert _supplier_id_of(mid2) is None
    assert _supplier_count() == before


def test_dedup_hit_backfills_missing_supplier_id():
    """去重命中复用旧行时补锚点：旧行是锚点列上线前写入的（supplier_id 为 NULL）。"""
    _fresh_db()
    mid, _ = db.upsert_vendor_memory(
        "順發行", "旧行内容", "sample-old", tenant_id="default")
    assert _supplier_id_of(mid) is None  # 当时没有档案
    sid = _mk_supplier("順發行")          # 之后才建档案
    mid2, is_new = db.upsert_vendor_memory(
        "順發行", "旧行内容", "sample-old", tenant_id="default")
    assert is_new is False and mid2 == mid
    assert _supplier_id_of(mid) == sid, "去重命中应把缺失的锚点补上"


# -------------------------------------------------------------
# 3. 读侧优先锚点：多写法命中同一条记忆
# -------------------------------------------------------------
def test_variant_names_hit_same_memory_via_anchor():
    _fresh_db()
    _mk_supplier("永記 (Wing Kee) 食品")
    mid, _ = db.upsert_vendor_memory(
        "永記食品", "锚点标记A 版式：手写单", "sample-A", tenant_id="default")
    assert _supplier_id_of(mid) is not None

    # 判别式：档案全名（中段英文注写法）与记忆里的写法**名字口径不相关**
    # （子串/核心词都匹配不上），只有锚点能救回来。先把这个前提断言出来，
    # 防止用例退化成恒真。
    assert rag._vendor_related("永記 (Wing Kee) 食品", "永記食品") is False
    ctx = rag.retrieve_context("永記 (Wing Kee) 食品", tenant_id="default")
    assert "锚点标记A" in ctx

    # 行为保持型守卫（纯名字口径也能过，非判别式）：繁简 / 空格写法同样命中
    for variant in ("永记食品", "永記 食品"):
        ctx2 = rag.retrieve_context(variant, tenant_id="default")
        assert "锚点标记A" in ctx2, f"{variant} 应命中同一条记忆"


def test_anchor_prevents_cross_supplier_leak():
    _fresh_db()
    s_yonglong = _mk_supplier("永隆")
    _mk_supplier("永隆行")
    mid_a, _ = db.upsert_vendor_memory(
        "永隆", "永隆专属标记", "sample-yonglong", tenant_id="default")
    db.upsert_vendor_memory(
        "永隆行", "永隆行专属标记", "sample-yonglonghing", tenant_id="default")
    assert _supplier_id_of(mid_a) == s_yonglong

    # 前提：名字口径会把"永隆行"的相关度判定为真（子串），即无锚点时会串味
    assert rag._vendor_related("永隆", "永隆行") is True

    ctx = rag.retrieve_context("永隆", tenant_id="default")
    assert "永隆专属标记" in ctx
    assert "永隆行专属标记" not in ctx, "锚点必须挡住另一商号的记忆"


def test_anchor_scoped_by_tenant():
    _fresh_db()
    s_t1 = _mk_supplier("九龍豆品廠", tenant_id="t1")
    _mk_supplier("九龍豆品廠 (Kowloon Bean)", tenant_id="t2")
    mid, _ = db.upsert_vendor_memory(
        "九龍豆品廠", "t1 专属标记", "sample-t1", tenant_id="t1")
    assert _supplier_id_of(mid) == s_t1

    assert "t1 专属标记" in rag.retrieve_context("九龍豆品廠", tenant_id="t1")
    # t2 用 t2 的写法检索：解析到 t2 的档案，t1 的锚点行不得被召回
    assert "t1 专属标记" not in rag.retrieve_context(
        "九龍豆品廠 (Kowloon Bean)", tenant_id="t2")


def test_anchor_miss_falls_back_to_name_and_never_returns_all():
    """锚点零命中：有 vendor 名则回退名字口径；无 vendor 名则空列表（绝不返全量）。"""
    _fresh_db()
    sid = _mk_supplier("鴻運餐飲")
    # 一条未锚定的遗留行（供应商档案后建，名字相关）
    db.upsert_vendor_memory("鴻運餐飲", "遗留未锚定标记", "s", tenant_id="default")
    _sql("UPDATE vendor_memory SET supplier_id = NULL WHERE vendor = ?", ("鴻運餐飲",))

    rows = db.list_vendor_memory("鴻運餐飲", tenant_id="default", supplier_id=sid)
    assert [r["vendor"] for r in rows] == ["鴻運餐飲"], "锚点零命中应回退名字口径"
    assert rows[0]["supplier_id"] is None

    # 没有 vendor 名又没有锚点命中 → 空，不得把整个租户的记忆倒出来
    db.upsert_vendor_memory("別家商號", "别家标记", "s2", tenant_id="default")
    assert db.list_vendor_memory(None, tenant_id="default", supplier_id=999999) == []


# -------------------------------------------------------------
# 4. 回填脚本
# -------------------------------------------------------------
def test_backfill_script_preview_apply_and_idempotent():
    _fresh_db()
    # 一条 active + 一条 archived（live 现有 9 行全部 archived，必须一并回填）
    mid_active, _ = db.upsert_vendor_memory(
        "順發行", "回填用例标记1", "s1", tenant_id="default")
    mid_arch, _ = db.upsert_vendor_memory(
        "何文田凍肉", "回填用例标记2", "s2", tenant_id="default")
    _sql("UPDATE vendor_memory SET status = 'archived' WHERE memory_id = ?",
         (mid_arch,))
    assert _supplier_id_of(mid_active) is None
    assert _supplier_id_of(mid_arch) is None

    sid1 = _mk_supplier("順發行")
    sid2 = _mk_supplier("何文田凍肉")
    suppliers_before = _supplier_count()

    # 预演：只报告，不写库
    stats = backfill.collect(write=False)
    assert stats["pending_rows"] == 2
    assert stats["matched"] == 2, "归档行也必须参与回填"
    assert stats["unmatched"] == 0
    assert stats["updated"] == 0
    assert _supplier_id_of(mid_active) is None, "预演不得写库"
    assert _supplier_id_of(mid_arch) is None

    # --apply：落笔
    stats = backfill.collect(write=True)
    assert stats["updated"] == 2
    assert _supplier_id_of(mid_active) == sid1
    assert _supplier_id_of(mid_arch) == sid2

    # 幂等：重跑零行改动（且不新建档案）
    stats = backfill.collect(write=True)
    assert stats["pending_rows"] == 0
    assert stats["matched"] == 0
    assert stats["updated"] == 0
    assert _supplier_count() == suppliers_before


def test_backfill_script_cli_preview_does_not_write(capsys):
    """CLI 缺省（不带 --apply）只预演 —— 防止误跑一次就改写归属。"""
    _fresh_db()
    mid, _ = db.upsert_vendor_memory("祥興凍肉", "CLI 预演标记", "s", tenant_id="default")
    _mk_supplier("祥興凍肉")
    assert _supplier_id_of(mid) is None

    assert backfill.main([]) == 0
    out = capsys.readouterr().out
    assert "预演（不写库）" in out
    assert "加 --apply 才写" in out
    assert _supplier_id_of(mid) is None, "CLI 预演不得写库"

    assert backfill.main(["--apply"]) == 0
    assert _supplier_id_of(mid) is not None


def test_backfill_script_leaves_unmatched_null():
    """匹配不到的行保持 NULL（读侧按名字回退），且脚本不新建档案。"""
    _fresh_db()
    mid, _ = db.upsert_vendor_memory(
        "七月餐室", "买方名脏数据", "s", tenant_id="default")
    _sql("UPDATE vendor_memory SET status = 'archived' WHERE memory_id = ?", (mid,))
    before = _supplier_count()

    stats = backfill.collect(write=True)
    assert stats["pending_rows"] == 1
    assert stats["matched"] == 0
    assert stats["unmatched"] == 1
    assert stats["updated"] == 0
    assert _supplier_id_of(mid) is None
    assert _supplier_count() == before, "脚本不得新建供应商档案"


def test_legacy_rebuild_keeps_supplier_id_column():
    """遗留物理形态重建路径（vendor_memory schema 的第二份实现）必须带上新列。

    防「同一根因多份实现只改一份」：ALTER 迁移块与 _rebuild_legacy_vendor_memory 的
    target_cols 是两份实现，只改一份就会出现"重建后的库缺列"（且 ALTER 不会再补，
    因为重建把表整个换掉了）。
    """
    path = os.path.join(_TMP, f"legacy_anchor_{uuid.uuid4().hex[:8]}.db")
    conn = sqlite3.connect(path)
    try:
        # pre-T2 形态：vendor 单键主键，仅 notes/sample 两列
        conn.execute("CREATE TABLE vendor_memory (vendor VARCHAR NOT NULL, notes TEXT,"
                     " sample TEXT, PRIMARY KEY (vendor))")
        conn.execute("INSERT INTO vendor_memory (vendor, notes, sample) VALUES (?,?,?)",
                     ("順發行", "版式備註A", "樣例A"))
        conn.commit()
    finally:
        conn.close()

    os.environ["DB_PATH"] = path
    importlib.reload(db)  # 触发重建 + 幂等 ALTER
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(vendor_memory)")}
            rows = conn.execute(
                "SELECT vendor, notes, supplier_id FROM vendor_memory").fetchall()
        finally:
            conn.close()
        assert "supplier_id" in cols, f"重建路径必须带 P2-T1 新列，实际 {sorted(cols)}"
        assert len(rows) == 1 and rows[0][1] == "版式備註A", "重建不得丢行"
        assert rows[0][2] is None, "重建对新列应填空 NULL（不臆测归属）"
    finally:
        _fresh_db()  # 还原隔离库，避免污染同文件后续用例


# -------------------------------------------------------------
# 5. J3：写入侧锚点的来源（权威 id 优先 / 拿不到为 NULL 且留痕 / approve 端到端）
# -------------------------------------------------------------
def _db_record_texts(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.db"]


def _anchor_warnings(caplog):
    """app.db 里与锚点相关的告警行（用于"必须留痕"与"不得噪音"两侧断言）。"""
    return [m for m in _db_record_texts(caplog) if "supplier_id" in m]


def test_explicit_supplier_id_wins_over_name_resolution():
    """调用方给的权威锚点优先于按名解析 —— J3 要补的就是这条通道。"""
    _fresh_db()
    sid_authoritative = _mk_supplier("順發行")
    sid_by_name = _mk_supplier("臨時寫法商號")
    # 前提：不给锚点时本函数会解析到另一个 id（没有这条，用例会退化成恒真）
    assert db.resolve_supplier_id("臨時寫法商號", "default") == sid_by_name
    assert sid_by_name != sid_authoritative

    mid, _ = db.upsert_vendor_memory(
        "臨時寫法商號", "J3 显式锚点标记", "sample-explicit", tenant_id="default",
        supplier_id=sid_authoritative)
    assert _supplier_id_of(mid) == sid_authoritative, (
        "调用方给的权威锚点必须优先于按 vendor 名解析")


def test_unresolvable_anchor_writes_null_with_warning(caplog):
    """拿不到锚点 → 列写 NULL（绝不编造）**且必须留痕**（降级可接受、静默不行）。"""
    _fresh_db()
    before = _supplier_count()
    with caplog.at_level(logging.WARNING, logger="app.db"):
        mid, _ = db.upsert_vendor_memory(
            "無檔案商號 ZZZ", "J3 未锚定标记", "sample-unanchored", tenant_id="default")

    assert _supplier_id_of(mid) is None, "解析不出必须写 NULL，不得编造 id"
    assert _supplier_count() == before, "解析不出时不得新建供应商档案"

    msgs = _anchor_warnings(caplog)
    joined = "\n".join(msgs)
    assert msgs, f"锚点解析不出必须打 warning，实际 app.db 日志: {_db_record_texts(caplog)}"
    assert "無檔案商號 ZZZ" in joined, "warning 必须带 vendor（否则不知道该行是谁的）"
    assert "default" in joined, "warning 必须带 tenant_id（否则分辨不出租户错位）"

    # 自校准：解析得出时不得出现这条 warning —— 否则上面的断言会被噪音恒真满足
    caplog.clear()
    sid_ok = _mk_supplier("有檔案商號 YYY")
    with caplog.at_level(logging.WARNING, logger="app.db"):
        mid_ok, _ = db.upsert_vendor_memory(
            "有檔案商號 YYY", "J3 已锚定标记", "sample-anchored", tenant_id="default")
    assert _supplier_id_of(mid_ok) == sid_ok
    assert not _anchor_warnings(caplog), (
        f"解析成功时不得打锚点告警，实际: {_db_record_texts(caplog)}")


def test_cross_tenant_supplier_id_rejected_and_falls_back(caplog):
    """跨租户的显式锚点必须拒绝。

    why：读侧（list_vendor_memory / rag.retrieve_context）按 tenant + anchor 双维收窄，
    把别的租户的档案 id 落到本租户行上 → 该行永远召不回且零日志（本仓③类缺陷形态）。
    """
    _fresh_db()
    sid_t2 = _mk_supplier("九龍豆品廠 (Kowloon Bean)", tenant_id="t2")
    with caplog.at_level(logging.WARNING, logger="app.db"):
        mid, _ = db.upsert_vendor_memory(
            "九龍豆品廠", "J3 跨租户锚点标记", "sample-cross", tenant_id="default",
            supplier_id=sid_t2)
    assert _supplier_id_of(mid) is None, "跨租户锚点必须被拒绝；default 里也没有同名档案"
    assert _anchor_warnings(caplog), "拒绝跨租户锚点必须留痕"

    # 行为保持：同一个 id 在它自己的租户里是合法锚点（拒绝的是错配，不是这个 id 本身）
    caplog.clear()
    mid_t2, _ = db.upsert_vendor_memory(
        "九龍豆品廠 (Kowloon Bean)", "J3 本租户标记", "sample-own-tenant",
        tenant_id="t2", supplier_id=sid_t2)
    assert _supplier_id_of(mid_t2) == sid_t2


def test_ingest_memory_forwards_supplier_id():
    """rag.ingest_memory 必须把权威锚点透传到 DB 列（新增形参的消费方之一）。"""
    _fresh_db()
    sid_authoritative = _mk_supplier("順發行")
    sid_by_name = _mk_supplier("臨時寫法商號")
    assert db.resolve_supplier_id("臨時寫法商號", "default") == sid_by_name

    rag.ingest_memory("臨時寫法商號", "- 菜心 2斤 @10 = 20",
                      notes="版式：手写单", tenant_id="default",
                      receipt_id=999001, supplier_id=sid_authoritative)

    rows = db.list_vendor_memory("臨時寫法商號", tenant_id="default")
    assert len(rows) == 1
    assert rows[0]["supplier_id"] == sid_authoritative, (
        "ingest_memory 必须把权威锚点透传到 DB（否则锚点只停在 API 层）")


def _client():
    """真实 ASGI 客户端（approve 端到端用例用；导入时才触发 app.main 的建表）。"""
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _approve_receipt(_db, client, vendor, supplier_id):
    """造一张 parsed 单据（行上带权威锚点）并走真实 approve 端点。"""
    rid = _db.create_receipt_record(
        supplier_name=vendor, supplier_id=supplier_id, status="parsed",
        tenant_id="default")
    _db.set_receipt_items(rid, [{
        "name": "菜心", "raw_name": "菜心", "quantity": 2, "unit": "斤",
        "raw_unit": "斤", "unit_price": 10, "amount": 20,
        "sku_id": None, "cost_center_id": None, "confidence": 0.9,
        "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
        "price_diff_percent": 0.0, "unit_conversion_warning": "",
        "fuzzy_candidates": [], "entity_candidates": [],
    }])
    row = _db.get_receipt_row(rid, tenant_id="default")
    resp = client.post(f"/api/receipt/{rid}/approve", json={"version": row.version},
                       headers={"X-Role": "owner", "X-Tenant-Id": "default"})
    assert resp.status_code == 200, resp.text
    return rid


def test_approve_endpoint_anchors_memory_to_receipt_supplier_id():
    """端到端：approve 回写必须以**单据行上的** supplier_id 锚定记忆。

    判别式构造：单据的 supplier_name 在档案表里不存在（会触发 approve 的自动建档），
    而行上 supplier_id 指向另一个已存在的档案。若 approve 不透传锚点，db 侧只能按名
    解析 → 落到自动新建的那个档案（id 不同）→ 本用例失败。
    """
    _db = _fresh_db()
    sid_authoritative = _mk_supplier("順發行")
    vendor = "順發行（新寫法）"
    # 前提：approve 之前该名字没有档案，故"按名解析"这条路此刻必然拿不到权威 id
    assert _db.resolve_supplier_id(vendor, "default") is None

    _approve_receipt(_db, _client(), vendor, sid_authoritative)

    rows = _db.list_vendor_memory(vendor, tenant_id="default")
    assert len(rows) == 1, "approve 必须回写一条记忆"
    assert rows[0]["supplier_id"] == sid_authoritative, (
        "approve 回写必须用单据行上的权威 supplier_id 锚定，而不是按名反查")

    # 对照前提：approve 的"供应商自动建档"把该名字建成了**另一个**档案 —— 故没有
    # 显式锚点时按名解析必然落到它上面（这条让上面的断言有判别力，不是恒真）
    auto = _db.find_supplier_by_name(vendor, tenant_id="default")
    assert auto is not None and int(auto.id) != sid_authoritative
    assert _db.resolve_supplier_id(vendor, "default") == int(auto.id)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

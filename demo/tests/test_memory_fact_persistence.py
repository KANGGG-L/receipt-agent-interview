# -*- coding: utf-8 -*-
"""P3-T1（K2）结构化事实落库面：vendor_memory 五列 + pending 队列 fact 载荷。

背景：`memory_facts.ROW_FIELDS` 声明了 14 个行字段，而 `VendorMemoryRow` 只有 9 个 ——
`MemoryFact.to_row()` 产出的 fact_kind / fact_value / trust_level / valid_from /
written_by **没有落点**（事实构造得出来、落不进库）。本用例锁定"声明即落点"。

覆盖：
1. schema 锁定：ROW_FIELDS 每一个成员都在 vendor_memory 里有真实列；pending_memory 有
   supplier_id / fact_kind / fact_key / fact_value / reject_reason 五列。
2. 四类 kind 的 MemoryFact 写入后 `from_row` 往返等价（同一份映射的两个方向）。
3. 五列均被**真写入**：断言取自裸 SQL 的列值（非缺省值），不是 ORM 属性上的假象。
4. 遗留路径（不传 fact 的旧调用）仍能写入、supersede 仍生效，且五列保持 **NULL**
   （不是 ""/1.0：读侧 `MemoryFact.from_row` 把 NULL 视为"没有该字段"，填缺省会让一行
   自由文本看起来像一条合法事实，trust_level 填 1.0 更是凭空给最高信任）。
5. 去重命中复用旧行时补载荷（只补空值，不覆盖已有事实内容）。
6. pending 队列承载 fact 载荷、遗留入队路径五列为 NULL、载荷经 `_pending_row_to_dict`
   透出（否则等于只写列没有消费方）。
7. 两条迁移路径都带五列：幂等 ALTER（老库补列）与 `_rebuild_legacy_vendor_memory`
   （pre-T2 物理形态重建 —— 它是 vendor_memory schema 的第二份实现，漏登记＝重建后缺列，
   而 ALTER 块跑在重建之前、不会补第二次）。

判别式说明（反证记录见任务报告）：
- 断言 A（五列往返等价 + 非缺省值）由**载荷列写入**独立支撑：去掉 trust_level 的写入
  → 本文件必须失败；
- 断言 B（遗留行五列为 NULL）由**不写载荷**支撑，与 A 是同一判据的另一个取值；
- 断言 C（重建/ALTER 两路都带列）由**两处 schema 登记**支撑。

全部离线假数据：独立临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
"""

import importlib
import os
import sqlite3
import sys
import tempfile
import uuid
from dataclasses import replace

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）。
# app.db 在 import 期执行 _make_engine() 的迁移块（ALTER），不带 DB_PATH 就会打到 live 库。
_TMP = tempfile.mkdtemp(prefix="memory_fact_persistence_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_fact.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
from app.services.memory_facts import (  # noqa: E402
    KIND_ALIAS,
    KIND_LAYOUT,
    KIND_PRICE_RANGE,
    KIND_UNIT,
    ROW_FIELDS,
    SOURCE_EXTRACT,
    MemoryFact,
    validate_fact,
)

# 本任务补的五列（验收对象）。刻意单独点名：只断言 "ROW_FIELDS ⊆ 列集合" 时，
# 若断言写错方向（把子集写成超集）会静默变绿。
FACT_PAYLOAD_COLUMNS = ("fact_kind", "fact_value", "trust_level", "valid_from",
                        "written_by")

PENDING_NEW_COLUMNS = ("supplier_id", "fact_kind", "fact_key", "fact_value",
                       "reject_reason")

# 四类 kind 各一条，值刻意取非缺省（trust_level ≠ 1.0、valid_from 非空、written_by 非空）
_KIND_CASES = (
    (KIND_ALIAS, "alias:本吧", "本地牛腩", "2026-01-01", 0.62),
    (KIND_UNIT, "unit:斤", "司馬斤", "2026-02-03", 0.75),
    (KIND_LAYOUT, "layout:手寫長單", "手寫/熱敏混合", "", 1.0),
    (KIND_PRICE_RANGE, "price_range:本地牛腩", "58-66 HKD/斤", "2026-03-05", 0.6),
)


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰（与既有记忆用例同款隔离）。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"fact_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _table_columns(path, table):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _raw_row(path, memory_id):
    """裸 SQL 读一行（绕开 ORM）：只有从库里读出来才算"真写入"。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM vendor_memory WHERE memory_id = ?", (memory_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _pending_raw(path, pending_id):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM pending_memory WHERE id = ?", (pending_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _seed_supplier(path, name, tenant_id="default"):
    """插一条供应商档案，返回其 id。

    why 必须真有档案：写下的事实要带 supplier_id 锚点，而 upsert 侧只认"档案存在"的
    锚点（不存在的 id 会被告警并回落名字解析，见 db._memory_anchor_for_write）——
    没有档案就无法验证锚点是否真的落库。
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


def _make_fact(kind, key, value, valid_from, trust, supplier_id):
    fact = MemoryFact(
        kind=kind, fact_key=key, value=value, supplier_id=str(supplier_id),
        tenant_id="default", source_kind=SOURCE_EXTRACT, trust_level=trust,
        valid_from=valid_from, written_by="memory_extract@v1")
    ok, reason = validate_fact(fact)
    assert ok, f"夹具事实必须合法，实得拒绝理由：{reason}"
    return fact


# -------------------------------------------------------------
# 1. schema 锁定：声明即落点
# -------------------------------------------------------------
def test_row_fields_all_have_real_columns():
    _db = _fresh_db()
    cols = _table_columns(_db.DB_PATH, "vendor_memory")
    missing = [c for c in ROW_FIELDS if c not in cols]
    assert missing == [], (
        f"vendor_memory 缺 ROW_FIELDS 成员 {missing} —— "
        "to_row() 产出的字段没有落点，事实构造得出来、落不进库")
    for col in FACT_PAYLOAD_COLUMNS:
        assert col in cols, f"本任务必须补上的列缺失：{col}"

    pcols = _table_columns(_db.DB_PATH, "pending_memory")
    for col in PENDING_NEW_COLUMNS:
        assert col in pcols, f"pending_memory 缺字段级 diff 载荷列：{col}"


# -------------------------------------------------------------
# 2 + 3. 四类 kind 往返等价 & 五列真写入
# -------------------------------------------------------------
def test_fact_roundtrip_for_all_kinds_with_real_values():
    _db = _fresh_db()
    vendor = "錨點商戶"
    sid = _seed_supplier(_db.DB_PATH, vendor)

    for kind, key, value, valid_from, trust in _KIND_CASES:
        fact = _make_fact(kind, key, value, valid_from, trust, sid)
        res = _db.save_memory_fact(
            "default", vendor, key, notes=f"{kind} 備註", fact=fact)
        assert res["is_new"] is True, f"{kind} 首次写入必须是新行"

        raw = _raw_row(_db.DB_PATH, res["memory_id"])
        assert raw is not None, "写入后必须能在库里按 memory_id 查到该行"
        # 五列真写入（裸 SQL 读出的值，不是 ORM 属性）
        assert raw["fact_kind"] == kind
        assert raw["fact_value"] == value
        assert raw["written_by"] == "memory_extract@v1"
        assert raw["valid_from"] == valid_from
        assert raw["trust_level"] is not None, "trust_level 未被写入（NULL）"
        assert abs(float(raw["trust_level"]) - trust) < 1e-9, (
            f"trust_level 必须是事实里的 {trust}，实际 {raw['trust_level']!r}"
            "（写错会静默改变按信任排序/降权的结果）")

        # 往返等价：from_row(to_row(f)) 全字段一致。
        # 唯一的归一：supplier_id 列是 INTEGER，SQLite 亲和性把 '4242' 存成 4242，
        # 故期望值用 int 形态对齐（这不是缺字段，是列类型决定的存储形态）。
        got = MemoryFact.from_row(raw)
        expected = replace(fact, memory_id=res["memory_id"], supplier_id=int(sid))
        assert got == expected, (
            f"{kind} 往返不等价：{got!r} != {expected!r}")


# -------------------------------------------------------------
# 4. 遗留路径：仍能写入 + supersede 仍生效 + 五列为 NULL
# -------------------------------------------------------------
def test_legacy_write_keeps_fact_columns_null():
    _db = _fresh_db()
    vendor, tenant, key = "遺留商戶", "default", "unit:斤"
    r1 = _db.save_memory_fact(tenant, vendor, key, notes="LEGACY_OLD")
    r2 = _db.save_memory_fact(tenant, vendor, key, notes="LEGACY_NEW")

    # 向后兼容：不带 fact 的调用行为与新增形参前一致（supersede 照常工作）
    assert r2["superseded"] == [r1["memory_id"]], \
        "遗留调用路径的 supersede 语义不得被本任务改变"

    for mid in (r1["memory_id"], r2["memory_id"]):
        raw = _raw_row(_db.DB_PATH, mid)
        for col in FACT_PAYLOAD_COLUMNS:
            assert raw[col] is None, (
                f"遗留路径不得写 {col}（实际 {raw[col]!r}）：NULL 表示"
                "「没有该字段」，填缺省值会让自由文本行长得像一条合法事实")


# -------------------------------------------------------------
# 5. 去重命中：补空载荷，但不改写已有事实内容
# -------------------------------------------------------------
def test_dedup_hit_backfills_payload_but_never_overwrites():
    _db = _fresh_db()
    vendor, tenant, key = "去重商戶", "default", "alias:本吧"
    notes = "同一段內容（去重键含 notes/sample）"
    first = _db.save_memory_fact(tenant, vendor, key, notes=notes)
    raw0 = _raw_row(_db.DB_PATH, first["memory_id"])
    assert raw0["fact_kind"] is None, "前置条件：首写未带事实载荷"

    # 同 (vendor, tenant, notes, sample) 再写一次并带事实 → 命中幂等去重，补上载荷
    fact_a = MemoryFact(kind=KIND_ALIAS, fact_key=key, value="本吧→本地牛腩",
                        supplier_id="1", tenant_id=tenant, trust_level=0.55,
                        written_by="memory_extract@v1")
    dup = _db.save_memory_fact(tenant, vendor, key, notes=notes, fact=fact_a)
    assert dup["is_new"] is False and dup["memory_id"] == first["memory_id"], \
        "同一内容必须命中去重复用既有行（本任务不得改变该语义）"
    raw1 = _raw_row(_db.DB_PATH, first["memory_id"])
    assert raw1["fact_kind"] == KIND_ALIAS and raw1["fact_value"] == "本吧→本地牛腩"
    assert abs(float(raw1["trust_level"]) - 0.55) < 1e-9
    assert raw1["written_by"] == "memory_extract@v1"

    # 再一次（同内容）带**不同**事实值 → 命中复用，只补空值，绝不改写已有事实
    fact_b = MemoryFact(kind=KIND_UNIT, fact_key=key, value="改寫值（不得生效）",
                        supplier_id="1", tenant_id=tenant, trust_level=0.9,
                        written_by="other@v1")
    dup2 = _db.save_memory_fact(tenant, vendor, key, notes=notes, fact=fact_b)
    assert dup2["is_new"] is False
    raw2 = _raw_row(_db.DB_PATH, first["memory_id"])
    assert raw2["fact_kind"] == KIND_ALIAS, "复用旧行时不得覆盖已有 fact_kind"
    assert raw2["fact_value"] == "本吧→本地牛腩", "复用旧行时不得改写已有事实内容"
    assert abs(float(raw2["trust_level"]) - 0.55) < 1e-9, \
        "复用旧行时不得改写已有信任分（改写要经 supersede 语义）"


# -------------------------------------------------------------
# 6. pending 队列承载 fact 载荷
# -------------------------------------------------------------
def test_pending_payload_persisted_and_legacy_row_null():
    _db = _fresh_db()
    vendor = "待審商戶"
    pid, is_new = _db.enqueue_pending_memory(
        vendor, "別名：本吧 → 本地牛腩", tenant_id="default",
        source_kind="extract", source_receipt_ids=[7],
        supplier_id=4242, fact_kind=KIND_ALIAS, fact_key="alias:本吧",
        fact_value="本地牛腩")
    assert is_new is True
    raw = _pending_raw(_db.DB_PATH, pid)
    assert raw["supplier_id"] == 4242
    assert raw["fact_kind"] == KIND_ALIAS
    assert raw["fact_key"] == "alias:本吧"
    assert raw["fact_value"] == "本地牛腩"
    assert raw["reject_reason"] is None, "正常待审行不得凭空带拒绝理由"

    # 载荷必须透出给审核侧（否则等于只写列、无消费方）
    d = _db.get_pending_memory(pid, tenant_id="default")
    assert d["fact_kind"] == KIND_ALIAS and d["fact_value"] == "本地牛腩"
    assert d["supplier_id"] == 4242 and d["reject_reason"] is None

    # 拒绝理由（memory_facts.validate_fact 的 reason 落这里）
    pid2, _ = _db.enqueue_pending_memory(
        "落選商戶", "候選被閘門拒絕", tenant_id="default", source_kind="extract",
        reject_reason="kind 非法：'sku'")
    assert _db.get_pending_memory(
        pid2, tenant_id="default")["reject_reason"] == "kind 非法：'sku'"

    # 遗留路径（不传载荷）：仍能入队，五列保持 NULL
    pid3 = _db.enqueue_pending_memory(
        "反饋商戶", "供應商 反饋糾偏：呢行品名認錯咗", tenant_id="default")[0]
    for col in PENDING_NEW_COLUMNS:
        assert _pending_raw(_db.DB_PATH, pid3)[col] is None, \
            f"遗留入队路径不得写 {col}（NULL＝无字段级载荷，界面退回整段文本视图）"

    # 非法 supplier_id 不得被写成字符串（SQLite 会把 'abc' 原样存成文本，之后零报错）
    pid4, _ = _db.enqueue_pending_memory(
        "髒錨點商戶", "錨點非法", tenant_id="default", supplier_id="abc")
    assert _pending_raw(_db.DB_PATH, pid4)["supplier_id"] is None


# -------------------------------------------------------------
# 7a. 幂等 ALTER 路径：老库补五列、存量行 NULL、不动数据
# -------------------------------------------------------------
def test_alter_path_adds_fact_columns_to_existing_db():
    path = os.path.join(_TMP, f"alter_{uuid.uuid4().hex[:8]}.db")
    conn = sqlite3.connect(path)
    try:
        # 载荷五列上线前的形态（memory_id 已是主键，故不会触发重建分支）
        conn.execute(
            "CREATE TABLE vendor_memory ("
            " memory_id VARCHAR NOT NULL PRIMARY KEY, vendor VARCHAR,"
            " tenant_id VARCHAR(64), notes TEXT, sample TEXT, version INTEGER,"
            " source_kind VARCHAR(32), source_ref TEXT, created_at TEXT,"
            " updated_at TEXT, hit_count INTEGER, last_hit_at TEXT,"
            " decay_score FLOAT, status VARCHAR(16), fact_key TEXT,"
            " superseded_by VARCHAR, valid_to TEXT, supplier_id INTEGER)")
        conn.execute(
            "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample,"
            " status, decay_score) VALUES ('legacy_row','老商戶','default','舊註記','',"
            "'active',0.7)")
        conn.commit()
    finally:
        conn.close()

    os.environ["DB_PATH"] = path
    importlib.reload(db)  # 触发幂等 ALTER
    try:
        cols = _table_columns(path, "vendor_memory")
        for col in FACT_PAYLOAD_COLUMNS:
            assert col in cols, f"幂等 ALTER 未补列：{col}"

        raw = _raw_row(path, "legacy_row")
        assert raw["notes"] == "舊註記", "迁移不得改动存量数据"
        for col in FACT_PAYLOAD_COLUMNS:
            assert raw[col] is None, (
                f"ADD COLUMN 刻意不带 DEFAULT：存量行 {col} 必须为 NULL，"
                f"实际 {raw[col]!r}（带 DEFAULT 会让老库与新库给出不同读侧结果）")

        # 补列后新写入能落五列（否则"补了列但写不进去"）
        sid = _seed_supplier(path, "老商戶")
        fact = _make_fact(KIND_ALIAS, "alias:新", "新牛腩", "2026-04-01", 0.7, sid)
        res = db.save_memory_fact("default", "老商戶", "alias:新", notes="新", fact=fact)
        assert _raw_row(path, res["memory_id"])["fact_kind"] == KIND_ALIAS
    finally:
        _fresh_db()  # 复原本模块的隔离库，避免后续用例拿到这个遗留库


# -------------------------------------------------------------
# 7b. 物理重建路径（pre-T2 形态）同样带五列 —— schema 的第二份实现
# -------------------------------------------------------------
def test_legacy_rebuild_includes_fact_columns():
    path = os.path.join(_TMP, f"rebuild_{uuid.uuid4().hex[:8]}.db")
    suffix = uuid.uuid4().hex[:6]
    conn = sqlite3.connect(path)
    try:
        # pre-T2 形态：vendor 单键主键，仅 notes/sample 两列 → 触发数据保全式重建
        conn.execute("CREATE TABLE vendor_memory (vendor VARCHAR NOT NULL, notes TEXT,"
                     " sample TEXT, PRIMARY KEY (vendor))")
        conn.execute("INSERT INTO vendor_memory (vendor, notes, sample) VALUES (?,?,?)",
                     (f"重建商戶{suffix}", "重建備註", "重建樣例"))
        conn.commit()
    finally:
        conn.close()

    os.environ["DB_PATH"] = path
    importlib.reload(db)  # 触发重建 + 幂等 ALTER
    try:
        cols = _table_columns(path, "vendor_memory")
        missing = [c for c in ROW_FIELDS if c not in cols]
        assert missing == [], (
            f"重建路径必须同步登记全部 ROW_FIELDS，缺 {missing}"
            "（重建分支从 target_cols 建新表，而 ALTER 块跑在重建之前、不会补第二次）")

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM vendor_memory")]
        finally:
            conn.close()
        assert len(rows) == 1, "重建不得丢行"
        assert rows[0]["notes"] == "重建備註"
        for col in FACT_PAYLOAD_COLUMNS:
            assert rows[0][col] is None, (
                f"重建时缺失列必须填 NULL（不是 ''）：{col} 实际 {rows[0][col]!r}")
    finally:
        _fresh_db()


# -------------------------------------------------------------
# 8. 形参未接线防线：fact 载荷里若混入治理列名，必须被挡下
# -------------------------------------------------------------
def test_governance_columns_not_overwritten_by_payload():
    _db = _fresh_db()
    vendor, tenant, key = "越權載荷商戶", "tenant_x", "alias:z"
    # 行字典口径：键是 memory_facts.ROW_FIELDS 的列名（与 from_row 一致，不混用领域名）
    res = _db.save_memory_fact(
        tenant, vendor, key, notes="治理列不得被载荷覆盖",
        fact={"fact_kind": KIND_ALIAS, "fact_key": key, "fact_value": "本地牛腩",
              "supplier_id": "", "tenant_id": tenant, "trust_level": 0.5,
              # 载荷里塞治理列：status 写成 archived、tenant_id 写成 tenant_x
              "status": "archived"})
    raw = _raw_row(_db.DB_PATH, res["memory_id"])
    assert raw["tenant_id"] == tenant, "载荷不得覆盖租户（否则越权注入口径被改写）"
    assert raw["status"] == "active", "载荷不得覆盖状态机（supersede/归档由写侧决定）"
    assert raw["fact_kind"] == KIND_ALIAS and raw["fact_value"] == "本地牛腩"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

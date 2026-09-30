# -*- coding: utf-8 -*-
"""P9：供应商命名归一工具（`scripts/normalize_supplier_names.py`）的判别式用例。

为什么需要这一批：工具是**写库**的（`--apply` 改三张表），而它的失效形态全都是
「看上去做了、其实没接全」——正是本仓高发的①/②类缺陷：
  A. 只改 `suppliers.name` 不改 `receipts.supplier_name` → 「档案叫 A、单据叫 B」，
     name-based 解析（`db.resolve_supplier_id`）在旧名上断掉；
  B. 候选规范名已被占用却直接改名 → 变成两条档案（物理 UNIQUE 索引也会直接报错）；
  C. 预演却写了库（本工具缺省必须零写入）；
  D. 第二次 `--apply` 又改一遍（幂等破了）。

故每个用例都断言**可观测的库内事实**（三张表的行值 / 改动行数），
去掉对应实现步骤必然有用例失败（证据见任务报告）。

全部离线假数据：模块级临时 DB + 临时 RAG 目录，零外部调用、零 live 库写入。
"""

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="normalize_supplier_names_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_norm.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.db as db  # noqa: E402
import normalize_supplier_names as norm  # noqa: E402


# -------------------------------------------------------------
# 夹具
# -------------------------------------------------------------
def _fresh_db():
    """每个用例独立数据库文件 + 独立（不存在的）RAG 目录。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, "norm_%s.db" % uuid.uuid4().hex[:8])
    os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_%s" % uuid.uuid4().hex[:8])
    importlib.reload(db)
    return db.DB_PATH


def _q(sql, params=()):
    conn = sqlite3.connect(db.DB_PATH)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _snapshot():
    """三张表的可观测事实（预演不写库 / 幂等的判据）。"""
    return {
        "suppliers": _q("SELECT id, name FROM suppliers ORDER BY id"),
        "receipts": _q("SELECT id, supplier_name, supplier_id FROM receipts ORDER BY id"),
        "vendor_memory": _q(
            "SELECT memory_id, vendor, supplier_id, status FROM vendor_memory"
            " ORDER BY memory_id"),
    }


def _insert_supplier_direct(name, tenant_id="default", active=1):
    """直接 INSERT 供应商档案。

    why 不走 `db.create_supplier`：它有 U-06「归一 key 命中即复用既有档案」的去重，
    构造**脏名档案与干净档案并存**的冲突场景时会被它并成一条（实测：先建
    `金百加咖啡` 再 create_supplier(`金百加咖啡(已入账)`) 会返回干净档案的 id）。
    live 上的脏名档案本就是在去重上线前/其他路径产生的，故这里直接落行。
    """
    conn = sqlite3.connect(db.DB_PATH)
    try:
        cur = conn.execute(
            "INSERT INTO suppliers (name, supplier_code, active, tenant_id)"
            " VALUES (?,?,?,?)", (name, "SUP-TEST", active, tenant_id))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _insert_memory(mid, vendor, supplier_id=None, status="active", tenant_id="default"):
    """直接 INSERT 记忆行：`db.upsert_vendor_memory` 会按名解析锚点，构造不出「未锚定」形态。"""
    conn = sqlite3.connect(db.DB_PATH)
    try:
        conn.execute(
            "INSERT INTO vendor_memory (memory_id, vendor, tenant_id, notes, sample,"
            " status, supplier_id) VALUES (?,?,?,?,?,?,?)",
            (mid, vendor, tenant_id, "note-" + mid, "sample-" + mid, status, supplier_id))
        conn.commit()
    finally:
        conn.close()


def _build_dirty():
    """脏名 + 无冲突：1 条档案、2 张已锚定单据 + 1 张仅按旧名的未锚定单据、2 条生效记忆。

    返回 archive id。
    """
    _fresh_db()
    sid = _insert_supplier_direct("佳發行(已核对)")
    db.create_receipt_record(supplier_name="佳發行(已核对)", supplier_id=sid,
                             tenant_id="default", status="parsed")
    db.create_receipt_record(supplier_name="佳發行(已核对)", supplier_id=sid,
                             tenant_id="default", status="parsed")
    db.create_receipt_record(supplier_name="佳發行(已核对)",
                             tenant_id="default", status="parsed")   # 未锚定
    _insert_memory("m-anchored", "佳發行(已核对)", supplier_id=sid)
    _insert_memory("m-unanchored", "佳發行(已核对)")
    return sid


# -------------------------------------------------------------
# ① 检测 + ⑤ 默认预演零写入
# -------------------------------------------------------------
def test_dry_run_detects_dirty_and_writes_nothing():
    sid = _build_dirty()
    before = _snapshot()
    stats = norm.collect(write=False)
    assert stats["dirty_total"] == 1
    it = stats["items"][0]
    assert it["supplier_id"] == sid
    assert it["kind"] == "status_suffix"
    assert it["proposal"] == "佳發行" and it["action"] == "rename"
    # 先量化：单据数（按锚点 + 仅按旧名未锚定）与生效记忆数
    assert it["receipts_anchored"] == 2
    assert it["receipts_by_name_only"] == 1
    assert it["receipts_total"] == 3
    assert it["mem_active"] == 2 and it["mem_total"] == 2
    assert stats["changed"] == 0
    assert before == _snapshot(), "预演不得写库"


def test_paren_numeric_id_is_detected_and_stripped():
    _fresh_db()
    sid = _insert_supplier_direct("7-ELEVEN HK (1542)")
    stats = norm.collect(write=False)
    assert stats["dirty_total"] == 1
    it = stats["items"][0]
    assert it["supplier_id"] == sid
    assert it["kind"] == "paren_id" and it["proposal"] == "7-ELEVEN HK"
    # 剥完就是干净名（重命名后不会又被自己判脏）
    assert norm._strip_status_suffix("7-ELEVEN HK") is None


def test_location_notes_and_clean_names_are_not_dirty():
    """地点注（新都城/將軍澳）与正常商号不得误判 —— 词表外一律不剥。"""
    _fresh_db()
    for name in ("七月烤魚 (新都城)", "金灣餐廳（將軍澳）", "德利行 Tak Lee Hong", "鴻運餐飲"):
        _insert_supplier_direct(name)
    stats = norm.collect(write=False)
    assert stats["dirty_total"] == 0, stats["items"]


def test_embedded_status_word_is_review_only():
    """无括号的状态词（如 `直通审批测试店`）：报「需人工复核」，--apply 零改动。"""
    _fresh_db()
    sid = _insert_supplier_direct("直通审批测试店")
    db.create_receipt_record(supplier_name="直通审批测试店", supplier_id=sid,
                             tenant_id="default", status="parsed")
    before = _snapshot()
    stats = norm.collect(write=True)
    it = stats["items"][0]
    assert it["kind"] == "embedded_status"
    assert it["action"] == "review" and it["proposal"] is None
    assert stats["changed"] == 0
    assert _snapshot() == before


# -------------------------------------------------------------
# ③ --apply 三表同步（关键风险点）
# -------------------------------------------------------------
def test_apply_syncs_three_tables_and_keeps_supplier_id_relation():
    sid = _build_dirty()
    stats = norm.collect(write=True)
    assert stats["changed"] == 6, stats  # suppliers 1 + receipts 3 + memory 2
    assert stats["changed_by_table"] == {"suppliers": 1, "receipts": 3,
                                         "vendor_memory": 2}
    # 表一：档案
    assert _q("SELECT name FROM suppliers WHERE id=?", (sid,)) == [("佳發行",)]
    # 表二：单据（按 supplier_id 锚定的行 + 未锚定但带旧名的行，一并改）
    assert _q("SELECT DISTINCT supplier_name FROM receipts") == [("佳發行",)]
    # 关系未丢：锚定行的 supplier_id 仍是原档案 id
    assert _q("SELECT supplier_id FROM receipts ORDER BY id") == [(sid,), (sid,), (None,)]
    # 表三：记忆
    assert _q("SELECT DISTINCT vendor FROM vendor_memory") == [("佳發行",)]
    assert _q("SELECT DISTINCT supplier_id FROM vendor_memory WHERE vendor='佳發行'"
              " ORDER BY supplier_id") == [(None,), (sid,)]
    # 改名后 name-based 解析指向同一档案（这正是同步三表要保住的能力）
    assert db.resolve_supplier_id("佳發行", tenant_id="default") == sid


def test_apply_is_idempotent_second_run_changes_zero():
    _build_dirty()
    first = norm.collect(write=True)
    assert first["changed"] == 6
    snap = _snapshot()
    second = norm.collect(write=True)
    assert second["items"] == []            # 脏名已被清掉，不再被检出
    assert second["changed"] == 0
    assert second["changed_by_table"] == {"suppliers": 0, "receipts": 0,
                                          "vendor_memory": 0}
    assert _snapshot() == snap


def test_apply_updates_receipts_anchored_to_archive_under_other_names():
    """按锚点回写：锚到该档案但写着别的写法的行也要一起改（否则档案名与单据名错位）。"""
    _fresh_db()
    sid = _insert_supplier_direct("佳發行(已核对)")
    rid = db.create_receipt_record(supplier_name="佳發行 Tak Lee Hong", supplier_id=sid,
                                   tenant_id="default", status="parsed")
    stats = norm.collect(write=True)
    it = stats["items"][0]
    assert it["receipts_to_change"] == 1
    assert it["receipts_to_change_by_other_name"] == 1
    assert _q("SELECT supplier_name, supplier_id FROM receipts WHERE id=?", (rid,)) == \
        [("佳發行", sid)]


# -------------------------------------------------------------
# ② 重名冲突：报「应走合并」，绝不自动重命名
# -------------------------------------------------------------
def test_exact_name_collision_reports_merge_and_never_renames():
    _fresh_db()
    keep, err = db.create_supplier("金百加咖啡", tenant_id="default")
    assert err is None and keep is not None
    drop = _insert_supplier_direct("金百加咖啡(已入账)")
    rid = db.create_receipt_record(supplier_name="金百加咖啡(已入账)", supplier_id=drop,
                                   tenant_id="default", status="parsed")
    _insert_memory("m-merge-1", "金百加咖啡(已入账)", supplier_id=drop)
    before = _snapshot()
    stats = norm.collect(write=True)
    assert stats["rename"] == 0 and stats["merge"] == 1
    it = stats["items"][0]
    assert it["action"] == "merge" and it["proposal"] == "金百加咖啡"
    assert it["collision"]["type"] == "exact_name"
    assert it["collision"]["supplier_id"] == keep
    assert it["merge_into"]["keep_supplier_id"] == keep
    assert it["merge_into"]["drop_supplier_id"] == drop
    assert it["receipts_total"] == 1 and it["mem_active"] == 1
    assert stats["changed"] == 0
    assert _snapshot() == before, "冲突项不得自动重命名、也不得自动合并"
    assert _q("SELECT supplier_name FROM receipts WHERE id=?", (rid,)) == \
        [("金百加咖啡(已入账)",)], "冲突项的单据不得被改挂"


def test_canonical_key_collision_also_reports_merge():
    """无同名但归一 key 相同（同一供应商的另一种写法）→ 也必须报合并。"""
    _fresh_db()
    keep, err = db.create_supplier("祥興冷凍食品", tenant_id="default")
    assert err is None and keep is not None
    drop = _insert_supplier_direct("祥興凍肉(店员已核对)")
    before = _snapshot()
    stats = norm.collect(write=True)
    assert stats["merge"] == 1 and stats["rename"] == 0
    it = stats["items"][0]
    assert it["collision"]["type"] == "canonical_key"
    assert it["collision"]["supplier_id"] == keep
    assert it["proposal"] == "祥興凍肉"
    assert stats["changed"] == 0 and _snapshot() == before


# -------------------------------------------------------------
# 向量重建提示（只提示，不自动重建）
# -------------------------------------------------------------
def test_vector_hint_gives_counts_and_check_command():
    _build_dirty()
    stats = norm.collect(write=False)
    v = stats["vector_hint"]
    assert v["affected_active_memories"] == 2      # 两条生效记忆（含未锚定的那条）
    assert v["store_present"] is False             # 临时 RAG_DIR 不存在 → 不创建、不报错
    assert v["with_vector"] == 0
    # 库不存在 → 「有没有向量」无从判断，必须报未知而不是折成 0（也不得报成「缺向量」）
    assert v["missing_vector"] is None and v["note"]
    assert "memory_consistency" in v["check_command"]
    assert "重建" in v["rebuild_hint"]


# -------------------------------------------------------------
# CLI 接线
# -------------------------------------------------------------
def test_cli_json_and_human_output(capsys):
    _build_dirty()
    assert norm.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["db_path"] == os.path.abspath(db.DB_PATH)
    assert payload["items"][0]["proposal"] == "佳發行"
    assert payload["changed"] == 0
    # 缺省（不带 --apply）是预演：人读输出必须明说未写库
    assert norm.main([]) == 0
    out = capsys.readouterr().out
    assert "预演（不写库）" in out and "--apply" in out
    assert _q("SELECT name FROM suppliers") == [("佳發行(已核对)",)]

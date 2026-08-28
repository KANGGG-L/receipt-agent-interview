# -*- coding: utf-8 -*-
"""遗留数据库物理形态迁移专项测试（本轮授权 vendor_memory / skus 两表重建）。

覆盖：
1. pre-T2 形态 vendor_memory（vendor 单键主键）自动重建为 memory_id 唯一主键，
   全部列数据保全（缺失治理列按默认值填充、memory_id 用 Python UUID 回填）
2. 行为闭环：重建后的旧库同 vendor 追加第二条记忆可成功（不再 IntegrityError）
3. T2 复合主键形态 (vendor, tenant_id) 同样触发重建
4. 含 name 全局 UNIQUE 的 skus 重建为按租户命名空间（无 name 唯一约束），
   数据保全；行为闭环：跨租户同名 SKU 可创建
5. 幂等：同一库连续两次 import，第二次零动作零异常（schema 指纹不变）
6. 新形态库（create_all 直建）零动作：二次 import schema 指纹不变
7. 失败路径（R5b）：拷贝阶段抛异常时旧表数据完好、无 __rebuild 残留、
   import/函数调用不抛出、日志含 warning

数据一律假数据（虚拟商户/虚拟食材，随机后缀防串扰）；临时库用 tempfile 并清理。
"""

import atexit
import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="legacy_db_migration_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_legacy.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402


# -------------------------------------------------------------
# 夹具：裸 SQL 构造旧形态库 + 受控 import
# -------------------------------------------------------------
def _build_legacy_db(path, with_vendor_memory=True, with_skus=True,
                     vendor_memory_shape="pre_t2", skus_shape="unique_index"):
    """用裸 SQL 构造 pre-T2/T2 形态的 vendor_memory 与含 name 唯一约束的 skus。"""
    suffix = uuid.uuid4().hex[:6]
    conn = sqlite3.connect(path)
    c = conn.cursor()
    if with_vendor_memory:
        if vendor_memory_shape == "pre_t2":
            # T2 之前：vendor 单键主键，仅 notes/sample 两列
            c.execute("CREATE TABLE vendor_memory ("
                      "vendor VARCHAR NOT NULL, notes TEXT, sample TEXT,"
                      " PRIMARY KEY (vendor))")
            c.execute("INSERT INTO vendor_memory (vendor, notes, sample)"
                      " VALUES (?, ?, ?)",
                      (f"虚拟商户甲{suffix}", "版式备注A", "样例A"))
            c.execute("INSERT INTO vendor_memory (vendor, notes, sample)"
                      " VALUES (?, ?, ?)",
                      (f"虚拟商户乙{suffix}", "版式备注B", "样例B"))
        elif vendor_memory_shape == "t2_composite":
            # T2 期间新建： (vendor, tenant_id) 复合主键
            c.execute("CREATE TABLE vendor_memory ("
                      "vendor VARCHAR NOT NULL, tenant_id VARCHAR(64) NOT NULL,"
                      "notes TEXT, sample TEXT,"
                      " PRIMARY KEY (vendor, tenant_id))")
            c.execute("INSERT INTO vendor_memory (vendor, tenant_id, notes, sample)"
                      " VALUES (?, ?, ?, ?)",
                      (f"虚拟商户甲{suffix}", "default", "版式备注A", "样例A"))
            c.execute("INSERT INTO vendor_memory (vendor, tenant_id, notes, sample)"
                      " VALUES (?, ?, ?, ?)",
                      (f"虚拟商户乙{suffix}", "tenantX", "版式备注B", "样例B"))
    if with_skus:
        if skus_shape == "unique_index":
            # create_all 旧形态：name 列上生成 UNIQUE 索引（ix_skus_name UNIQUE）
            c.execute("CREATE TABLE skus ("
                      "id INTEGER NOT NULL, name VARCHAR, category VARCHAR,"
                      "base_unit VARCHAR, min_stock_alert FLOAT, current_stock FLOAT,"
                      "last_unit_price FLOAT, sku_code VARCHAR, active INTEGER,"
                      " PRIMARY KEY (id))")
            c.execute("CREATE UNIQUE INDEX ix_skus_name ON skus (name)")
        elif skus_shape == "unique_column":
            # 更老形态：name 列约束 UNIQUE（sqlite_autoindex）
            c.execute("CREATE TABLE skus ("
                      "id INTEGER NOT NULL, name VARCHAR UNIQUE, category VARCHAR,"
                      "base_unit VARCHAR, min_stock_alert FLOAT, current_stock FLOAT,"
                      "last_unit_price FLOAT, sku_code VARCHAR, active INTEGER,"
                      " PRIMARY KEY (id))")
        c.execute("INSERT INTO skus (name, category, base_unit, current_stock,"
                  " last_unit_price, active) VALUES (?, ?, ?, ?, ?, ?)",
                  (f"虚拟食材甲{suffix}", "蔬菜", "斤", 5.5, 12.5, 1))
        c.execute("INSERT INTO skus (name, category, base_unit, current_stock,"
                  " last_unit_price, active) VALUES (?, ?, ?, ?, ?, ?)",
                  (f"虚拟食材乙{suffix}", "肉类", "斤", 0.0, 33.0, 1))
    conn.commit()
    conn.close()
    return {"suffix": suffix}


def _import_db(path):
    """DB_PATH 指向目标库后重新 import app.db（触发迁移/重建）。"""
    os.environ["DB_PATH"] = path
    importlib.reload(db)
    return db


def _schema_fingerprint(path):
    """库 schema 指纹：sqlite_master 全量定义 + 各表行数 + 索引定义。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c = conn.cursor()
    master = sorted(c.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall())
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    counts = []
    for t in tables:
        if t.startswith("sqlite_"):
            continue
        counts.append((t, c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]))
    counts.sort()
    conn.close()
    return {"master": master, "counts": counts}


# -------------------------------------------------------------
# 1+2. pre-T2 vendor_memory：物理主键重建 + 数据保全 + 行为闭环
# -------------------------------------------------------------
def test_pre_t2_vendor_memory_rebuilt_with_data_preserved():
    path = os.path.join(_TMP, f"vm_pre_t2_{uuid.uuid4().hex[:8]}.db")
    planted = _build_legacy_db(path, with_skus=False, vendor_memory_shape="pre_t2")
    _db = _import_db(path)

    conn = sqlite3.connect(path)
    c = conn.cursor()
    cols = {r[1]: r for r in c.execute("PRAGMA table_info(vendor_memory)")}
    # 物理形态已修正：memory_id 是唯一主键列
    pk_cols = [r[1] for r in c.execute("PRAGMA table_info(vendor_memory)") if r[5] > 0]
    assert pk_cols == ["memory_id"], f"memory_id 应为唯一物理主键，实际 {pk_cols}"
    assert cols["vendor"][5] == 0, "vendor 不再是主键"
    # 唯一索引已补建
    idx = {r[1] for r in c.execute("PRAGMA index_list(vendor_memory)")}
    assert "ux_vendor_memory_memory_id" in idx, "重建后必须补建 memory_id 唯一索引"

    # 数据保全：行数一致、字段值一致、治理列按默认值填充
    rows = c.execute("SELECT vendor, notes, sample, tenant_id, memory_id,"
                     " version, source_kind, source_ref, status, hit_count,"
                     " decay_score FROM vendor_memory ORDER BY vendor").fetchall()
    conn.close()
    assert len(rows) == 2, "重建不得丢行"
    vendors = {r[0] for r in rows}
    assert f"虚拟商户甲{planted['suffix']}" in vendors
    assert f"虚拟商户乙{planted['suffix']}" in vendors
    for r in rows:
        assert r[1] in ("版式备注A", "版式备注B") and r[2] in ("样例A", "样例B")
        assert r[3] == "default", "pre-T2 缺失 tenant_id 列应按 default 填充"
        assert r[4], "memory_id 必须回填 UUID"
        assert len(str(r[4])) == 32, "memory_id 应为 uuid4().hex"
        assert r[5] == 1 and r[6] == "manual" and r[7] == "[]" and r[8] == "active"
        assert r[9] == 0 and r[10] == 1.0


def test_rebuilt_legacy_db_allows_second_memory_same_vendor():
    """A 项行为闭环：旧库同 vendor 追加第二条记忆不再被 vendor 主键拦截。"""
    path = os.path.join(_TMP, f"vm_append_{uuid.uuid4().hex[:8]}.db")
    planted = _build_legacy_db(path, with_skus=False, vendor_memory_shape="pre_t2")
    _db = _import_db(path)

    vendor = f"虚拟商户甲{planted['suffix']}"
    mid1, new1 = _db.upsert_vendor_memory(vendor, "追加记忆一", "样例X",
                                          tenant_id="default")
    mid2, new2 = _db.upsert_vendor_memory(vendor, "追加记忆二", "样例Y",
                                          tenant_id="default")
    assert new1 and new2, "同 vendor 两条不同记忆均应写入成功"
    assert mid1 != mid2
    # 1 条重建保全的存量记忆 + 2 条新追加记忆（fuzzy 门会把同名前缀的
    # 存量行一并召回，这里用 notes 全集做精确断言）
    rows = _db.list_vendor_memory(vendor, tenant_id="default")
    assert len(rows) == 3, "存量 1 条 + 追加 2 条应共 3 条"
    assert {r["notes"] for r in rows} == {"版式备注A", "追加记忆一", "追加记忆二"}


def test_t2_composite_pk_vendor_memory_rebuilt():
    """T2 复合主键 (vendor, tenant_id) 形态同样触发重建。"""
    path = os.path.join(_TMP, f"vm_t2_{uuid.uuid4().hex[:8]}.db")
    planted = _build_legacy_db(path, with_skus=False,
                               vendor_memory_shape="t2_composite")
    _db = _import_db(path)

    conn = sqlite3.connect(path)
    c = conn.cursor()
    pk_cols = [r[1] for r in c.execute("PRAGMA table_info(vendor_memory)") if r[5] > 0]
    assert pk_cols == ["memory_id"]
    rows = c.execute("SELECT vendor, tenant_id, notes FROM vendor_memory"
                     " ORDER BY vendor").fetchall()
    conn.close()
    assert len(rows) == 2
    assert dict((r[0], r[1]) for r in rows)[f"虚拟商户甲{planted['suffix']}"] == "default"
    assert dict((r[0], r[1]) for r in rows)[f"虚拟商户乙{planted['suffix']}"] == "tenantX"


# -------------------------------------------------------------
# 3. skus：name 全局 UNIQUE 重建为按租户命名空间 + 数据保全 + 行为闭环
# -------------------------------------------------------------
def test_legacy_skus_unique_name_rebuilt_to_tenant_namespace():
    for shape in ("unique_index", "unique_column"):
        path = os.path.join(_TMP, f"skus_{shape}_{uuid.uuid4().hex[:8]}.db")
        planted = _build_legacy_db(path, with_vendor_memory=False,
                                   skus_shape=shape)
        _db = _import_db(path)

        conn = sqlite3.connect(path)
        c = conn.cursor()
        # 无任何覆盖 name 的唯一索引
        for idx in c.execute("PRAGMA index_list(skus)").fetchall():
            idx_name, is_unique = idx[1], idx[2]
            if not is_unique:
                continue
            cols = [r[2] for r in c.execute(f"PRAGMA index_info({idx_name})")]
            assert cols != ["name"], f"{shape}: name 仍存在唯一索引 {idx_name}"
        # 数据保全：id/字段值不变，缺失 tenant_id 按 default 填充
        rows = c.execute("SELECT id, name, category, base_unit, current_stock,"
                         " last_unit_price, active, tenant_id FROM skus"
                         " ORDER BY id").fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0][0] == 1 and rows[1][0] == 2, "SKU id 必须保全（外键引用）"
        assert rows[0][1] == f"虚拟食材甲{planted['suffix']}"
        assert rows[0][4] == 5.5 and rows[1][4] == 0.0
        assert rows[0][5] == 12.5 and rows[1][5] == 33.0
        assert all(r[7] == "default" for r in rows)


def test_rebuilt_skus_allow_cross_tenant_same_name():
    """B 项行为闭环：重建后跨租户同名 SKU 通过 scoped 检查后可创建。"""
    path = os.path.join(_TMP, f"skus_cross_{uuid.uuid4().hex[:8]}.db")
    planted = _build_legacy_db(path, with_vendor_memory=False,
                               skus_shape="unique_index")
    _db = _import_db(path)

    name = f"虚拟食材甲{planted['suffix']}"
    # 同租户重名仍被 scoped 检查拦截（应用层是唯一防重线）
    sku_id, err = _db.create_sku(name, tenant_id="default")
    assert sku_id is None and err == "SKU_NAME_CONFLICT"
    # 跨租户同名可创建（旧库会在此处 IntegrityError 500）
    sku_b, err_b = _db.create_sku(name, tenant_id="tenantB")
    assert err_b is None and sku_b, "跨租户同名 SKU 应可创建"
    row_b = _db.get_sku(sku_b, tenant_id="tenantB")
    assert row_b is not None and row_b.tenant_id == "tenantB"


# -------------------------------------------------------------
# 4. 幂等：二次 import 零动作零异常
# -------------------------------------------------------------
def test_rebuild_idempotent_second_import_zero_action():
    path = os.path.join(_TMP, f"idem_{uuid.uuid4().hex[:8]}.db")
    planted = _build_legacy_db(path, vendor_memory_shape="pre_t2",
                               skus_shape="unique_index")
    _db = _import_db(path)  # 第一次 import：触发重建
    fp1 = _schema_fingerprint(path)

    _db = _import_db(path)  # 第二次 import：必须零动作零异常
    fp2 = _schema_fingerprint(path)

    assert fp1 == fp2, "第二次 import schema 必须零变化"
    # 重建用临时表不得残留
    tmp_tables = [t for t, _ in fp2["counts"]
                  if "rebuild" in t or "__" in t]
    assert tmp_tables == [], f"不得残留重建临时表: {tmp_tables}"
    # 数据仍在
    counts = dict(fp2["counts"])
    assert counts.get("vendor_memory") == 2 and counts.get("skus") == 2


# -------------------------------------------------------------
# 5. 新形态库：零动作
# -------------------------------------------------------------
def test_new_shape_db_untouched_on_import():
    path = os.path.join(_TMP, f"fresh_{uuid.uuid4().hex[:8]}.db")
    _db = _import_db(path)  # 全新库由 create_all 建出正确形态
    # 先写一条业务数据，确保后续 import 不动任何行
    vendor = f"新形態商戶{uuid.uuid4().hex[:6]}"
    _db.upsert_vendor_memory(vendor, "新形態備註", "樣本", tenant_id="default")
    fp1 = _schema_fingerprint(path)

    _db = _import_db(path)
    fp2 = _schema_fingerprint(path)
    assert fp1 == fp2, "新形态库二次 import 必须零动作"

    conn = sqlite3.connect(path)
    c = conn.cursor()
    pk_cols = [r[1] for r in c.execute("PRAGMA table_info(vendor_memory)") if r[5] > 0]
    assert pk_cols == ["memory_id"], "新库 vendor_memory 本就应是 memory_id 主键"
    for idx in c.execute("PRAGMA index_list(skus)").fetchall():
        if idx[2]:
            cols = [r[2] for r in c.execute(f"PRAGMA index_info({idx[1]})")]
            assert cols != ["name"], "新库 skus.name 不应有唯一索引"
    conn.close()
    assert len(_db.list_vendor_memory(vendor, tenant_id="default")) == 1


# -------------------------------------------------------------
# 6. 失败路径（R5b）：拷贝阶段抛异常 → 旧表数据完好、无 __rebuild 残留、
#    调用/import 不抛出、日志含 warning（caplog）
# -------------------------------------------------------------
class _TrapColumns(set):
    """列名集合替身：对陷阱列的成员判断直接抛错，精确命中重建的拷贝阶段。

    _rebuild_legacy_vendor_memory 在逐行拷贝循环里用 `col in cols` 判断旧表
    是否携带治理列；对 memory_id 的第一次成员判断即抛出，异常发生在
    CREATE 临时表之后、DROP 旧表之前（任何失败路径都不得先删旧表）。
    """

    def __init__(self, items, trap_col):
        super().__init__(items)
        self._trap = trap_col

    def __contains__(self, item):
        if item == self._trap:
            raise RuntimeError("模拟拷贝阶段故障")
        return super().__contains__(item)


def test_rebuild_copy_phase_failure_keeps_old_table(caplog, monkeypatch):
    import logging

    from sqlalchemy import create_engine as _create_engine

    path = os.path.join(_TMP, f"fail_{uuid.uuid4().hex[:8]}.db")
    planted = _build_legacy_db(path, with_skus=False,
                               vendor_memory_shape="pre_t2")
    engine = _create_engine(f"sqlite:///{path}")

    real_columns = db._table_columns

    def _trapped_columns(conn, table):
        if table == "vendor_memory":
            return _TrapColumns({"vendor", "notes", "sample"}, "memory_id")
        return real_columns(conn, table)

    monkeypatch.setattr(db, "_table_columns", _trapped_columns)
    with caplog.at_level(logging.WARNING, logger="app.db"):
        # 失败路径不得向调用方抛出（「失败只告警不阻断启动」铁律）
        db._rebuild_legacy_vendor_memory(engine)
    hit_warnings = [r.getMessage() for r in caplog.records
                    if "vendor_memory legacy rebuild skipped" in r.getMessage()
                    and "模拟拷贝阶段故障" in r.getMessage()]
    assert hit_warnings, "拷贝阶段失败必须 logger.warning 留痕且含原始异常信息"

    conn = sqlite3.connect(path)
    c = conn.cursor()
    # 旧表数据完好：物理形态未被动过（vendor 仍是主键），两行原始数据一个不少
    pk_cols = [r[1] for r in c.execute("PRAGMA table_info(vendor_memory)")
               if r[5] > 0]
    assert pk_cols == ["vendor"], "失败路径不得改动旧表物理形态"
    rows = c.execute("SELECT vendor, notes, sample FROM vendor_memory"
                     " ORDER BY vendor").fetchall()
    tmp_tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name LIKE '%__rebuild%'")]
    conn.close()
    assert len(rows) == 2, "失败路径不得丢行"
    assert f"虚拟商户甲{planted['suffix']}" in {r[0] for r in rows}
    assert f"虚拟商户乙{planted['suffix']}" in {r[0] for r in rows}
    assert tmp_tables == [], f"失败路径不得残留 __rebuild 临时表: {tmp_tables}"

    # 解除注入后走完整 import 路径：不抛出，迁移在完好的旧表上正常完成
    monkeypatch.undo()
    _db = _import_db(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    pk_cols = [r[1] for r in c.execute("PRAGMA table_info(vendor_memory)")
               if r[5] > 0]
    rows = c.execute("SELECT vendor, notes FROM vendor_memory").fetchall()
    conn.close()
    assert pk_cols == ["memory_id"], "恢复后 import 应正常完成重建"
    assert len(rows) == 2, "恢复后重建仍须保全全部数据"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

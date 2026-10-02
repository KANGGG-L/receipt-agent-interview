# -*- coding: utf-8 -*-
"""WS3 回归：app_settings 三种物理形态的兼容与「零重建」幂等（简化后设计）。

设计定稿（取代先前的复合主键方案）：
- `app_settings` 只承载**全局桶**，形态回到 `key` 单列主键 + `value`
  （历史 `insert ... on conflict(key) do update` 契约因此天然成立）；
- 门店覆盖全部由 `app_setting_overrides`（复合主键 `(key, tenant_id)`）承载；
- 对已漂移的旧库（复合主键 `(key, tenant_id)` + `key` 单列唯一约束，等价于 live 库
  「复合主键 + 命名唯一索引」的物理形态）与原始旧库（`key` 单列主键、无 `tenant_id`
  列）都**不再做任何重建**，物理形态原样保留即可照常读写 —— 这正是「消除冗余」后
  必须守住的兼容性。

覆盖：
1. 新库形态：`app_settings` 单列主键、`app_setting_overrides` 存在、无多余唯一索引
2. 原始旧库（单列主键、无 tenant_id）：升上来后照常工作 + 二次启动零动作
3. 已漂移库（复合主键 + key 单列唯一约束）：照常工作（关键回归保护）+ 二次启动零动作
4. 三种形态下「手写省略 tenant_id 的 INSERT」均成功

全部离线假数据：临时库用裸 SQL 构造旧形态，零外部调用、零 live 写入。
"""

import atexit
import importlib
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="app_settings_rebuild_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_app_settings.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402

_LEGACY_ROWS = (
    ("blur_laplacian_threshold", "30.0"),
    ("legacy_custom_key", "legacy_value"),
)


def _build_legacy_db(path):
    """裸 SQL 构造**原始旧库**形态：`app_settings (key)` 单列主键，无 tenant_id 列。"""
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("CREATE TABLE app_settings ("
              " key VARCHAR NOT NULL, value TEXT, PRIMARY KEY (key))")
    for key, value in _LEGACY_ROWS:
        c.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                  (key, value))
    conn.commit()
    conn.close()


def _build_drifted_db(path):
    """裸 SQL 构造**已漂移库**形态：复合主键 (key, tenant_id) + `key` 单列唯一约束
    + tenant_id 服务端默认值（与 worktree live 库「复合主键 + 命名唯一索引」的物理
    形态等价，只是唯一约束由 SQLite 自动索引 `sqlite_autoindex_*` 承担）。
    额外塞一条历史中间态残留的门店覆盖行（tenant_id != 'default'）。"""
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("CREATE TABLE app_settings ("
              " key VARCHAR NOT NULL UNIQUE,"
              " tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
              " value TEXT, PRIMARY KEY (key, tenant_id))")
    for key, value in _LEGACY_ROWS:
        c.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                  (key, value))
    c.execute("INSERT INTO app_settings (key, tenant_id, value) VALUES (?, ?, ?)",
              ("store_only_key", "shopA", "shop_override"))
    conn.commit()
    conn.close()


def _import_db(path):
    """DB_PATH 指向目标库后重新 import app.db（触发 create_all / 幂等迁移）。"""
    os.environ["DB_PATH"] = path
    importlib.reload(db)
    return db


def _connect(path):
    return sqlite3.connect(path)


def _table_names(path):
    conn = _connect(path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _columns(path, table):
    conn = _connect(path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _pk_cols(path, table):
    conn = _connect(path)
    try:
        rows = [r for r in conn.execute(f"PRAGMA table_info({table})") if r[5] > 0]
        rows.sort(key=lambda r: r[5])
        return [r[1] for r in rows]
    finally:
        conn.close()


def _index_names(path, table):
    conn = _connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA index_list({table})")}
    finally:
        conn.close()


def _has_single_col_unique_key_index(path):
    """app_settings 上是否存在覆盖单列 key 的唯一索引/约束（含 SQLite 自动索引）。"""
    conn = _connect(path)
    try:
        for idx in conn.execute("PRAGMA index_list(app_settings)").fetchall():
            name, is_unique = idx[1], idx[2]
            if not is_unique:
                continue
            cols = [r[2] for r in
                    conn.execute(f"PRAGMA index_info({name})").fetchall()]
            if cols == ["key"]:
                return True
        return False
    finally:
        conn.close()


def _schema_fingerprint(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        master = sorted(conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall())
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        counts = []
        for t in names:
            if t.startswith("sqlite_"):
                continue
            counts.append((t, conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]))
        counts.sort()
        return {"master": master, "counts": counts}
    finally:
        conn.close()


def _residue_rebuild_tables(path):
    conn = _connect(path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name LIKE '%__rebuild%'")]
    finally:
        conn.close()


def _handwritten_insert(path, key, value):
    """手写 INSERT：显式省略 tenant_id 列（历史脚本/测试的写法）。"""
    conn = _connect(path)
    try:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def _handwritten_upsert(path, key, value):
    """手写 ON CONFLICT(key) upsert（历史契约，必须在三种形态下都成立）。"""
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value))
        conn.commit()
    finally:
        conn.close()


def _read_value(path, key, table="app_settings"):
    conn = _connect(path)
    try:
        row = conn.execute(
            f"SELECT value FROM {table} WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# -------------------------------------------------------------
# 1. 新库形态：单列主键 + overrides 表存在 + 手写 INSERT/ON CONFLICT 均成功
# -------------------------------------------------------------
def test_fresh_db_single_pk_shape_and_handwritten_insert():
    path = os.path.join(_TMP, f"as_fresh_{uuid.uuid4().hex[:8]}.db")
    _db = _import_db(path)

    assert _pk_cols(path, "app_settings") == ["key"], "新库 app_settings 应为单列主键 key"
    assert "tenant_id" not in _columns(path, "app_settings"), \
        "新库 app_settings 不应再有 tenant_id 列"
    assert "app_setting_overrides" in _table_names(path), "overrides 表应被建出"
    assert _pk_cols(path, "app_setting_overrides") == ["key", "tenant_id"]
    named_idx = [n for n in _index_names(path, "app_settings")
                 if not n.startswith("sqlite_autoindex_")]
    assert named_idx == [], f"单列主键下不应再有额外命名唯一索引：{named_idx}"
    assert _residue_rebuild_tables(path) == []

    # 手写省略 tenant_id 的 INSERT 成功
    _handwritten_insert(path, "handwritten_new", "hv")
    assert _read_value(path, "handwritten_new") == "hv"
    # ON CONFLICT(key) upsert 成功
    _handwritten_upsert(path, "handwritten_new", "hv2")
    assert _read_value(path, "handwritten_new") == "hv2"
    assert _db.get_app_setting("handwritten_new") == "hv2"


# -------------------------------------------------------------
# 2. 原始旧库（单列主键、无 tenant_id）：照常工作 + 二次启动零动作
# -------------------------------------------------------------
def test_legacy_single_pk_db_keeps_working_and_idempotent(caplog):
    path = os.path.join(_TMP, f"as_legacy_{uuid.uuid4().hex[:8]}.db")
    _build_legacy_db(path)

    with caplog.at_level(logging.WARNING, logger="app.db"):
        _db = _import_db(path)
    first_logs = [r.getMessage() for r in caplog.records]

    # 形态零改动：仍是单列主键、无 tenant_id 列
    assert _pk_cols(path, "app_settings") == ["key"]
    assert "tenant_id" not in _columns(path, "app_settings")
    # 绝不重建 app_settings（旧方案会在这里触发数据保全式重建）
    assert not [m for m in first_logs
                if "app_settings" in m and ("rebuilt" in m or "rebuild" in m)], first_logs
    assert _residue_rebuild_tables(path) == []

    # 数据保全：存量两行仍在，读取照常
    assert _db.get_app_setting("legacy_custom_key") == "legacy_value"
    assert _db.list_app_settings()["blur_laplacian_threshold"] == "30.0"

    # overrides 表被幂等建出（旧库也需要门店覆盖的归属表）
    assert "app_setting_overrides" in _table_names(path)

    # 手写省略 tenant_id 的 INSERT 成功
    _handwritten_insert(path, "legacy_script_key", "handwritten")
    assert _db.get_app_setting("legacy_script_key") == "handwritten"

    # 二次 import 零动作（schema 指纹不变、无 app_settings 告警）
    fp1 = _schema_fingerprint(path)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.db"):
        _import_db(path)
    second_logs = [r.getMessage() for r in caplog.records]
    assert _schema_fingerprint(path) == fp1, "二次重载 schema 必须零变化"
    assert not [m for m in second_logs if "app_settings" in m], \
        f"二次重载不得再动 app_settings：{second_logs}"


# -------------------------------------------------------------
# 3. 已漂移库（复合主键 + key 单列唯一约束）：照常工作（关键回归保护）
# -------------------------------------------------------------
def test_drifted_composite_pk_db_keeps_working_and_idempotent(caplog):
    path = os.path.join(_TMP, f"as_drifted_{uuid.uuid4().hex[:8]}.db")
    _build_drifted_db(path)

    with caplog.at_level(logging.WARNING, logger="app.db"):
        _db = _import_db(path)

    # 物理形态原样保留：不重建（主键仍复合）、不删唯一索引 —— 这是回归保护重点
    assert _pk_cols(path, "app_settings") == ["key", "tenant_id"], \
        "已漂移库物理主键不得被改写"
    assert _has_single_col_unique_key_index(path), \
        "已漂移库的 key 单列唯一索引/约束必须保留（ON CONFLICT(key) 依赖它）"
    assert _residue_rebuild_tables(path) == []

    # 全局读取照常（存量全局行按 key 命中）
    assert _db.get_app_setting("legacy_custom_key") == "legacy_value"
    assert _db.get_app_setting("blur_laplacian_threshold") == "30.0"

    # 历史残留的门店覆盖行已迁到 app_setting_overrides；全局桶不再误命中它
    assert _db.get_app_setting("store_only_key") is None
    assert _db.get_app_setting("store_only_key", tenant_id="shopA") == "shop_override"
    assert _read_value(path, "store_only_key", "app_settings") is None
    assert _read_value(path, "store_only_key", "app_setting_overrides") == "shop_override"

    # ORM 写全局：UPDATE 存量键
    _db.set_app_setting("legacy_custom_key", "updated")
    assert _db.get_app_setting("legacy_custom_key") == "updated"
    # ORM 写全局：INSERT 新键（依赖 tenant_id 服务端默认值，仍落 'default'）
    _db.set_app_setting("drifted_new_key", "z")
    assert _db.get_app_setting("drifted_new_key") == "z"
    conn = _connect(path)
    try:
        tid = conn.execute("SELECT tenant_id FROM app_settings WHERE key = ?",
                           ("drifted_new_key",)).fetchone()[0]
    finally:
        conn.close()
    assert tid == "default", "省略 tenant_id 的 ORM 写入应落 'default'"

    # 手写省略 tenant_id 的 INSERT 成功
    _handwritten_insert(path, "drifted_hw_key", "hw")
    assert _db.get_app_setting("drifted_hw_key") == "hw"
    # 手写 ON CONFLICT(key) upsert 成功（依赖被保留的 key 单列唯一约束）
    _handwritten_upsert(path, "drifted_hw_key", "hw2")
    assert _read_value(path, "drifted_hw_key") == "hw2"

    # 二次 import 零动作（不再搬迁、不再重建）
    fp1 = _schema_fingerprint(path)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.db"):
        _import_db(path)
    second_logs = [r.getMessage() for r in caplog.records]
    assert _schema_fingerprint(path) == fp1, "二次重载 schema 必须零变化"
    assert not [m for m in second_logs if "moved" in m], \
        f"二次重载不得再搬迁覆盖行：{second_logs}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

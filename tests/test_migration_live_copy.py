# -*- coding: utf-8 -*-
"""安全加固第二批：迁移验证（D16 payments/departments 租户列 + D17 receipts.supplier_id）。

铁律（对齐任务硬约束 4）：
- 只在 **受控源库** 与 **live 库副本** 上执行迁移，绝不触碰 live 库 demo/receipt_demo.db；
- 副本来源：把 live 库拷到 scratchpad（$COMMANDCODE_SCRATCHPAD）或本用例临时目录；
- 用例结束断言 live 库文件 sha256 未变（零写入）。

覆盖：
1. 主体断言（确定性）：在**受控源库**（本用例自建 legacy 表 + 已知行）上跑迁移，
   逐行断言 `receipts.id -> supplier_id` 的精确期望值，刻意覆盖：
   ① 精确名可直连；② 仅归一 key 可连（如 祥興 vs 祥興凍肉(有限公司)）；③ 空 supplier_name
   必须保持 NULL；④ 无档案的孤儿名必须保持 NULL 且**不凭空建档**；⑤ 跨租户同名不误挂。
   并断言二次执行为 no-op、行数零丢失、供应商档案数不变。
2. live 库副本只保留**结构性 / 不变式**断言：三个新列存在、payments/departments 回填
   'default'、空名行未挂、档案数不变、行数零丢失、二次执行 no-op、live 库 sha256 不变。

为什么不再对 live 数据做「独立重算逐项相等」：live 库是可变的输入（并发工作流会新增
供应商/单据、识别回写会改写 supplier_name），用「当前 live 库」重算一遍期望映射再与
「迁移当时」的落库结果比对，必然把「数据在迁移之后被改动」误报成产品缺陷。迁移是
`(receipts.supplier_name, suppliers 快照)` 的纯函数；它的正确性只能由构造好的受控
输入来判定，live 库只用于验证「列/幂等/零丢失/零写入」这类与数据内容无关的不变式。
"""

import hashlib
import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid

import pytest

_TMP = tempfile.mkdtemp(prefix="migration_live_copy_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_live_copy.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402

_LIVE_DB = os.path.join(_DEMO_DIR, "receipt_demo.db")


# -------------------------------------------------------------
# 只读工具（均以 mode=ro 打开，绝不写库）
# -------------------------------------------------------------
def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _table_counts(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tabs = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        out = {}
        for t in tabs:
            if t.startswith("sqlite_"):
                continue
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out
    finally:
        conn.close()


def _fingerprint(path):
    """schema 定义 + 三个新列/关联数据的完整指纹（幂等断言用）。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        master = sorted(conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall())
        receipt_sids = sorted(conn.execute(
            "SELECT id, supplier_id FROM receipts").fetchall())
        dept_tids = sorted(conn.execute(
            "SELECT id, tenant_id FROM departments").fetchall())
        pay_tids = sorted(conn.execute(
            "SELECT id, tenant_id FROM payments").fetchall())
        return {"master": master, "receipt_sids": receipt_sids,
                "dept_tids": dept_tids, "pay_tids": pay_tids}
    finally:
        conn.close()


def _supplier_count(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    finally:
        conn.close()


def _supplier_id_map(path):
    """返回 {receipt_id: supplier_id}（含 NULL，逐行读取，供精确期望值比对）。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT id, supplier_id FROM receipts ORDER BY id")}
    finally:
        conn.close()


def _import_db(path):
    os.environ["DB_PATH"] = path
    return importlib.reload(db)


def _copy_live(dst_dir):
    os.makedirs(dst_dir, exist_ok=True)
    copy_path = os.path.join(dst_dir, f"live_copy_{uuid.uuid4().hex[:8]}.db")
    shutil.copy2(_LIVE_DB, copy_path)
    return copy_path


def _copy_dst_dir(tmp_path):
    """副本落点：优先 scratchpad（任务硬约束 4 指定），否则本用例临时目录。"""
    scratch = os.environ.get("COMMANDCODE_SCRATCHPAD", "").strip()
    if scratch and os.path.isdir(scratch):
        return scratch
    return str(tmp_path)


# -------------------------------------------------------------
# 受控源库：刻意构造 5 类情形，逐行给出精确期望值
# -------------------------------------------------------------
# （id, name, tenant_id）：档案表。id 显式指定，期望值可直接写死。
_CONTROLLED_SUPPLIERS = [
    (1, "七月餐室", "default"),           # 精确名可直连
    (2, "祥興凍肉(有限公司)", "default"),  # 仅归一 key 可连：canonical('祥興') == '祥兴'
    (3, "金百加咖啡", "default"),          # 仅归一 key 可连：canonical('金百加') == '金百加'
    (10, "雙租戶供應商", "t2"),            # 只属于 t2 租户，用于跨租户隔离验证
]

# （id, supplier_name, tenant_id）：单据表（legacy 形态：无 supplier_id 列）。
_CONTROLLED_RECEIPTS = [
    (1, "七月餐室", "default"),            # ① 精确名直连 -> 1
    (2, "祥興", "default"),                # ② 仅归一 key（精确名不等于档案名）-> 2
    (3, "金百加", "default"),              # ② 仅归一 key -> 3
    (4, "", "default"),                    # ③ 空串 -> NULL
    (5, None, "default"),                  # ③ NULL -> NULL
    (6, "   ", "default"),                 # ③ 纯空白（归一 key 为空）-> NULL
    (7, "無檔案供應商XYZ", "default"),      # ④ 孤儿名 -> NULL（且不得凭空建档）
    (8, "雙租戶供應商", "default"),         # ⑤ 跨租户同名 -> NULL（t2 档案不得被 default 挂上）
    (9, "雙租戶供應商", "t2"),              # ⑤ 同租户正例 -> 10
    (10, "七月餐室", "t2"),                 # ⑤ 跨租户（t2 无该档案）-> NULL
]

CONTROLLED_EXPECTED_SUPPLIER_ID = {
    1: 1, 2: 2, 3: 3,
    4: None, 5: None, 6: None,
    7: None,
    8: None, 9: 10, 10: None,
}


def _create_controlled_source_db(path):
    """建 legacy 形态的受控源库：receipts 无 supplier_id 列，只含回填所需的最小列。

    刻意把 receipts 造成「老库」形态（仅 id / supplier_name / tenant_id），迁移里的
    ADD COLUMN 与幂等回填才有真实对象可作用；其余业务表由迁移的 create_all 兜底建出。
    """
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE TABLE suppliers ("
            " id INTEGER PRIMARY KEY,"
            " name TEXT DEFAULT '',"
            " tenant_id VARCHAR(64) DEFAULT 'default');"
            "CREATE TABLE receipts ("
            " id INTEGER PRIMARY KEY,"
            " supplier_name TEXT,"
            " tenant_id VARCHAR(64) DEFAULT 'default');"
            "CREATE TABLE payments (id INTEGER PRIMARY KEY);"
            "CREATE TABLE departments (id INTEGER PRIMARY KEY);")
        conn.executemany(
            "INSERT INTO suppliers (id, name, tenant_id) VALUES (?, ?, ?)",
            _CONTROLLED_SUPPLIERS)
        conn.executemany(
            "INSERT INTO receipts (id, supplier_name, tenant_id) VALUES (?, ?, ?)",
            _CONTROLLED_RECEIPTS)
        conn.execute("INSERT INTO payments (id) VALUES (1)")
        conn.execute("INSERT INTO departments (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()


def _assert_controlled_backfill(actual_map):
    """对受控库逐行断言精确期望值（含 NULL 行；不是子集、不是「非空」）。"""
    assert actual_map == CONTROLLED_EXPECTED_SUPPLIER_ID, (
        "受控源库回填结果必须逐行等于精确期望映射：\n"
        f"  actual  ={actual_map}\n"
        f"  expected={CONTROLLED_EXPECTED_SUPPLIER_ID}")


def test_controlled_source_backfill_exactness(tmp_path):
    """主体断言：受控源库上的 D17 回填逐行精确、幂等、零丢失、不凭空建档。"""
    controlled = os.path.join(str(tmp_path), "controlled_source.db")
    _create_controlled_source_db(controlled)

    counts_before = _table_counts(controlled)
    suppliers_before = _supplier_count(controlled)

    _import_db(controlled)  # 触发 create_all + DDL 迁移 + D17 回填

    conn = sqlite3.connect(f"file:{controlled}?mode=ro", uri=True)
    try:
        # 迁移后列必须存在（legacy 库由 ADD COLUMN 补齐）
        for table, col in (("receipts", "supplier_id"),
                           ("payments", "tenant_id"),
                           ("departments", "tenant_id")):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert col in cols, f"{table}.{col} 迁移后必须存在"
        # D16 存量行回填 default
        assert all((r[0] or "") == "default" for r in conn.execute(
            "SELECT DISTINCT tenant_id FROM departments"))
        assert conn.execute(
            "SELECT COUNT(*) FROM payments WHERE tenant_id IS NULL OR tenant_id=''"
        ).fetchone()[0] == 0
    finally:
        conn.close()

    # D17：逐行精确期望值
    _assert_controlled_backfill(_supplier_id_map(controlled))

    # 空名行必须保持 NULL（显式再点一次，避免「恰好没匹配上」掩盖空名被误挂）
    conn = sqlite3.connect(f"file:{controlled}?mode=ro", uri=True)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM receipts WHERE supplier_id IS NOT NULL "
            "  AND (supplier_name IS NULL OR TRIM(supplier_name) = '')").fetchone()[0] == 0, \
            "空/纯空白 supplier_name 行不得被挂任何 supplier_id"
    finally:
        conn.close()

    # ④ 孤儿名不得凭空建档
    assert _supplier_count(controlled) == suppliers_before, \
        "迁移不得凭空创建供应商档案（孤儿名必须保持 NULL）"

    # 行数零丢失
    counts_after = _table_counts(controlled)
    for table, n in counts_before.items():
        assert counts_after.get(table) == n, \
            f"{table} 行数变化：{n} -> {counts_after.get(table)}"

    # 二次执行为 no-op
    fp1 = _fingerprint(controlled)
    _import_db(controlled)
    fp2 = _fingerprint(controlled)
    assert fp1 == fp2, "二次迁移必须零变化（schema 与关联数据均 no-op）"


# -------------------------------------------------------------
# live 库副本：只做结构性 / 不变式断言（与数据内容无关）
# -------------------------------------------------------------
def test_live_copy_migration(tmp_path):
    if not os.path.exists(_LIVE_DB):
        pytest.skip(f"live 库不存在：{_LIVE_DB}")

    live_hash_before = _sha256(_LIVE_DB)
    copy_path = _copy_live(_copy_dst_dir(tmp_path))
    counts_before = _table_counts(copy_path)
    suppliers_before = _supplier_count(copy_path)

    _db = _import_db(copy_path)  # 触发 _make_engine() 全量幂等迁移

    conn = sqlite3.connect(f"file:{copy_path}?mode=ro", uri=True)
    try:
        # 1. 三个新列存在
        for table, col in (("payments", "tenant_id"),
                           ("departments", "tenant_id"),
                           ("receipts", "supplier_id")):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert col in cols, f"{table}.{col} 迁移后必须存在"

        # 2. 存量行回填 default
        assert all((r[0] or "") == "default" for r in conn.execute(
            "SELECT DISTINCT tenant_id FROM departments"))
        assert conn.execute(
            "SELECT COUNT(*) FROM departments WHERE tenant_id IS NULL OR tenant_id=''"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM payments WHERE tenant_id IS NULL OR tenant_id=''"
        ).fetchone()[0] == 0

        # 3. 不变式：空 supplier_name 行不得被挂；无 id 关系不得被编造
        empty_linked = conn.execute(
            "SELECT COUNT(*) FROM receipts WHERE supplier_id IS NOT NULL "
            "  AND (supplier_name IS NULL OR TRIM(supplier_name) = '')").fetchone()[0]
        assert empty_linked == 0, "空 supplier_name 行不得被匹配"

        bad_link = conn.execute(
            "SELECT COUNT(*) FROM receipts r WHERE r.supplier_id IS NOT NULL "
            "  AND NOT EXISTS (SELECT 1 FROM suppliers s WHERE s.id = r.supplier_id)"
        ).fetchone()[0]
        assert bad_link == 0, "supplier_id 必须指向真实存在的档案（不得编造外键）"
    finally:
        conn.close()

    # 4. 行数零丢失 + 档案数不变（不得凭空建档）
    counts_after = _table_counts(copy_path)
    for table, n in counts_before.items():
        assert counts_after.get(table) == n, \
            f"{table} 行数变化：{n} -> {counts_after.get(table)}"
    assert counts_before.get("receipts", 0) > 0, "副本应包含单据数据"
    assert _supplier_count(copy_path) == suppliers_before, \
        "迁移不得凭空创建供应商档案"

    # 5. 二次执行为 no-op
    fp1 = _fingerprint(copy_path)
    _db = _import_db(copy_path)
    fp2 = _fingerprint(copy_path)
    assert fp1 == fp2, "二次迁移必须零变化（schema 与关联数据均 no-op）"

    # 6. live 库零写入
    assert _sha256(_LIVE_DB) == live_hash_before, \
        "迁移必须只在副本上执行，live 库 sha256 不得变化"

    # 清理本用例的副本（scratchpad 落点也一并清，避免残留）
    try:
        os.remove(copy_path)
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

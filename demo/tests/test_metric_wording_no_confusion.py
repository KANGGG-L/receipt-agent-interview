# -*- coding: utf-8 -*-
"""R9：「SKU 匹配率」两个口径的防混读守卫（不改任何计算逻辑）。

why 需要这一批：同一段时间里有两个数字都被叫作「SKU 匹配率」，但口径完全不同：
- `memory_metrics.py` 报 `23.73%`（= 163/687）—— 它只数 `receipt_items.matched=1` 这个
  **落库标记**；
- `backfill_vendor_sku_alias.py --replay` 的「基线(全局口径)」报 `36.10%`（= 248/687）——
  它是**复放了匹配逻辑**（`_match_sku`，含核心词归一）之后的命中占比。
两者在汇报里并列出现时会被当成互相矛盾（本仓已多次因口径混读被误导）。R9 的处置是
**只改口径命名与自述、不动计算**：两侧各加一个显式键名（`matched_flag_rate` /
`simulated_match_rate`）与一条互相指向的 `note`，并补本文件把这件事钉成断言。

本文件四类断言：
1. **键名不相同**（防未来又把两侧改回同名）——并且两侧引用对侧键名的常量必须一致；
2. **两侧 note 都真的包含对侧键名**（在真实输出里核，不是核常量：防「定义了没接线」）；
3. **数值必须等于用 SQL 直接数 `receipt_items.matched=1` / 明细行总数**，且先自校准
   （用两份已知比例的临时库证明这条等式不是恒真、分子与分母两个自由度都被钉住）；
4. 两侧都显式输出分子与分母（便于看出差异来自分子口径而不是分母）。

反证（实测记录见交付说明）：把某一侧的 note 字段里的对侧引用删掉 → 第 2 类对应用例
必须失败；逐字节还原后必须再全绿（给出「反证前 / 反证后 / 还原后」三个 md5）。

隔离：模块级临时 DB + 临时 RAG 目录（必须在首次 import app.* 之前设置）。`backfill`
顶层 `import app.db` 会在**导入期**按 DB_PATH 执行 `_make_engine()` 的迁移块，故隔离必须
发生在 import 之前 —— 不带 DB_PATH 跑就是打 live 库。
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* / scripts 之前设置）
_TMP = tempfile.mkdtemp(prefix="metric_wording_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_wording.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
import backfill_vendor_sku_alias as bvsa  # noqa: E402

# memory_metrics 刻意不 import app.db（见其 docstring），这里按文件路径加载：
# 既是独立模块实例（便于逐个用例取数），也顺带证明它自身可独立加载。
_mm_spec = importlib.util.spec_from_file_location(
    "memory_metrics_wording", os.path.join(_SCRIPTS_DIR, "memory_metrics.py"))
mm = importlib.util.module_from_spec(_mm_spec)
_mm_spec.loader.exec_module(mm)


# -------------------------------------------------------------
# 夹具
# -------------------------------------------------------------
def _fresh_db():
    """每个用例独立数据库文件（真实 schema：手抄 DDL 会让「列名不匹配」逃过测试）。"""
    path = os.path.join(_TMP, "wording_%s.db" % uuid.uuid4().hex[:8])
    db.DB_PATH = path
    db._make_engine()
    return path


def _seed_items(path, rows):
    """rows = [(item_id, receipt_id, name, matched)]；顺带造出对应 receipts 行。"""
    conn = sqlite3.connect(path)
    try:
        rids = sorted({r[1] for r in rows})
        conn.executemany(
            "INSERT INTO receipts (id, supplier_name, status) VALUES (?, ?, 'parsed')",
            [(rid, "甲供應商") for rid in rids])
        conn.executemany(
            "INSERT INTO receipt_items (id, receipt_id, name, matched) VALUES (?, ?, ?, ?)",
            list(rows))
        conn.commit()
    finally:
        conn.close()


def _sql_matched_flag_rate(path):
    """独立口径：直接用 SQL 数 matched=1 / 明细行总数（与被测脚本的计算路径无关）。"""
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        total, matched = conn.execute(
            "select count(*), sum(case when matched=1 then 1 else 0 end) from receipt_items"
        ).fetchone()
    finally:
        conn.close()
    return round(100.0 * (matched or 0) / total, 2) if total else 0.0


# -------------------------------------------------------------
# 1. 键名必须不相同（防未来又改成同名）
# -------------------------------------------------------------
def test_two_metric_key_names_are_distinct():
    """两侧口径名必须是同一对、且互不相同；对侧引用常量也必须彼此一致。"""
    assert mm.SKU_MATCH_FLAG_KEY == "matched_flag_rate"
    assert bvsa.SIMULATED_MATCH_RATE_KEY == "simulated_match_rate"
    assert mm.SKU_MATCH_FLAG_KEY != bvsa.SIMULATED_MATCH_RATE_KEY, \
        "两个口径不能再用同一个名字（这正是 R9 要修的问题）"
    # 交叉引用用的常量两侧必须指向同一个名字，否则文案会指向一个不存在的键
    assert mm.SIMULATED_MATCH_RATE_KEY == bvsa.SIMULATED_MATCH_RATE_KEY
    assert bvsa.MATCHED_FLAG_RATE_KEY == mm.SKU_MATCH_FLAG_KEY


# -------------------------------------------------------------
# 2. 两侧 note 必须交叉引用对侧键名（在真实输出里核）
# -------------------------------------------------------------
def test_memory_metrics_output_note_cross_references_other_metric():
    """memory_metrics 的 note 必须点名 simulated_match_rate，且声明不可混读。"""
    path = _fresh_db()
    _seed_items(path, [(1, 1, "菜心", 1), (2, 1, "腩肉", 0)])

    obj = mm.collect(db_path=path)["sku_match"]
    assert obj["incomparable_with"] == bvsa.SIMULATED_MATCH_RATE_KEY
    assert bvsa.SIMULATED_MATCH_RATE_KEY in obj["note"], \
        "note 必须指向另一侧的口径名（否则读者仍会把两个数当成一回事）"
    assert "不可混读" in obj["note"]
    # 键名与值都真的落地（防「定义了没接线」）
    assert obj["matched_flag_rate"] == obj["rate_pct"] == 50.0
    assert (obj["numerator"], obj["denominator"]) == (1, 2)


def test_backfill_replay_note_cross_references_other_metric(monkeypatch):
    """backfill --replay 报告的 note 必须反向点名 matched_flag_rate。

    放在最后一个用例位置：replay() 会 reload db 模块并改写 os.environ["DB_PATH"]，
    这是既有行为（见 test_avoided_duplicate_creation_metric.py 的同款说明）。
    """
    src = _fresh_db()
    _seed_items(src, [(1, 1, "菜心", 1), (2, 1, "腩肉", 0)])
    monkeypatch.setattr(bvsa, "_SOURCE_DB_PATH", src)

    report = bvsa.replay()
    sim = report[bvsa.SIMULATED_MATCH_RATE_KEY]
    assert sim["incomparable_with"] == mm.SKU_MATCH_FLAG_KEY
    assert mm.SKU_MATCH_FLAG_KEY in sim["note"], \
        "note 必须反向指向 memory_metrics 的口径名"
    assert "不可混读" in sim["note"]
    # 分子/分母显式输出，且值取自既有 baseline 读数（不改计算逻辑）
    assert sim["numerator"] == report["baseline_matched"]
    assert sim["denominator"] == report["items"]
    assert sim["rate_pct"] == report["baseline_rate"]


# -------------------------------------------------------------
# 3. 数值等于 SQL 直数（含自校准：证明这条等式不恒真）
# -------------------------------------------------------------
def test_matched_flag_rate_equals_sql_count_with_self_calibration():
    """`matched_flag_rate` 必须等于 SQL 直数的 matched=1 / 明细行总数。

    自校准分三步，缺一步这条等式就可能是个恒真的假绿：
    ① 已知数据（1/4）上先证明 SQL 期望值算得对；
    ② 换一份不同比例的库（3/4），读数必须跟着变 —— 证明它真的在读数据；
    ③ 用「单据数」当分母会得到另一个数（1/1=100%）—— 证明分母这个自由度过被钉住。
    """
    # ① 已知比例：4 条明细、1 条 matched → 25.0%
    path = _fresh_db()
    _seed_items(path, [(1, 1, "a", 1), (2, 1, "b", 0), (3, 1, "c", 0), (4, 1, "d", 0)])
    expected = _sql_matched_flag_rate(path)
    assert expected == 25.0, "自校准①：SQL 期望值在已知数据上必须先算对"

    obj = mm.collect(db_path=path)["sku_match"]
    assert obj["matched_flag_rate"] == expected
    assert obj["rate_pct"] == expected, "旧键必须与新键同值（兼容，不改计算逻辑）"
    assert (obj["numerator"], obj["denominator"]) == (1, 4)

    # ② 换一份比例不同的库：读数必须跟着变（证明不是恒真）
    path2 = _fresh_db()
    _seed_items(path2, [(1, 1, "a", 1), (2, 1, "b", 1), (3, 1, "c", 1), (4, 1, "d", 0)])
    expected2 = _sql_matched_flag_rate(path2)
    assert expected2 == 75.0 and expected2 != expected
    assert mm.collect(db_path=path2)["sku_match"]["matched_flag_rate"] == expected2 == 75.0

    # ③ 分母若被写成「单据数」（这里是 1）会给出 100.0，与正确答案不同 —— 被等式挡住
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        n_receipts = conn.execute("select count(*) from receipts").fetchone()[0]
    finally:
        conn.close()
    assert n_receipts == 1
    assert round(100.0 * 1 / n_receipts, 2) != expected, \
        "错误分母（单据数）必须与正确答案不同，否则这条等式钉不住分母"


if __name__ == "__main__":   # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

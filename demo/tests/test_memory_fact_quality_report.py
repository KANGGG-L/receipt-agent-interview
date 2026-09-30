# -*- coding: utf-8 -*-
"""AL1：记忆事实质量巡检（`scripts/memory_fact_quality_report.py`）的判别式用例。

why（存在的理由）
----------------
一个「质量巡检」最容易退化成**恒真报告**：无论数据多脏都打印一堆 OK。所以本文件用
**临时构造的假数据**反向验证三类判据**不是恒真**：
- 每类都造一个 LOW 与一个 OK 的对照，断言分类正确；
- 每类都验证**边界**：刚好在阈值上/下的两条，分类必须相反（防止阈值写反或把 `<=` / `<`
  写错方向）；
- 断言巡检对「结构不同但相似度更低」的数据**不误报**（繁简异体不是边缘映射）；
- 断言 `--fail-on-low` 的退出码语义（有 LOW → 1，无 LOW → 0）。

反证口径（交付报告给实测记录）：把 `_classify_alias` 改成恒返回 OK，
`test_alias_*` 必须失败 —— 它断言「只差尾缀的别名必须被判 LOW」。

隔离：本文件不 import `app.db`（归一实现的 import 副作用由脚本自身强制导向临时库）；
DB_PATH / RAG_DIR 在任何 app.* import 之前指向临时目录。
"""

import os
import sqlite3
import sys
import tempfile

import pytest

# 隔离环境：必须在首次 import app.* 之前设置（与同目录其它用例同款）
_TMP = tempfile.mkdtemp(prefix="mem_fact_quality_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_al1.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.join(_DEMO_DIR, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import memory_fact_quality_report as qr  # noqa: E402


# -------------------------------------------------------------
# 假数据构造
# -------------------------------------------------------------
def _mk_db(path, vm_rows=(), sku_rows=()):
    """建临时库。

    vm_rows: (memory_id, vendor, tenant_id, status, fact_kind, fact_key, fact_value, notes)
    sku_rows: (id, name, base_unit, tenant_id)
    """
    con = sqlite3.connect(path)
    try:
        con.execute(
            "create table vendor_memory(memory_id text, vendor text, tenant_id text,"
            " status text, fact_kind text, fact_key text, fact_value text, notes text)")
        con.execute(
            "create table skus(id integer primary key, name text, base_unit text,"
            " tenant_id text)")
        for r in vm_rows:
            con.execute("insert into vendor_memory values(?,?,?,?,?,?,?,?)", r)
        for s in sku_rows:
            con.execute("insert into skus(id,name,base_unit,tenant_id) values(?,?,?,?)", s)
        con.commit()
    finally:
        con.close()
    return path


def _rows_by_mid(report):
    return {d["memory_id"]: d for d in report["details"]}


def _verdict(report, mid):
    return _rows_by_mid(report)[mid]["verdict"]


def _hit(report, mid):
    return _rows_by_mid(report)[mid]["criterion_hit"]


# -------------------------------------------------------------
# 判据一：alias 边缘映射
# -------------------------------------------------------------
def test_alias_identical_after_normalize_is_low(tmp_path):
    """归一后完全相同 = 不承载映射信息 → LOW（不是 OK）。"""
    db = _mk_db(str(tmp_path / "a.db"), vm_rows=[
        ("m1", "供应商A", "default", "active", "alias", "alias:白糖", "白糖", ""),
    ])
    rep = qr.collect(db)
    assert _verdict(rep, "m1") == qr.LOW
    assert "归一后相同" in _hit(rep, "m1")


def test_alias_tail_only_is_low_and_middle_diff_is_ok(tmp_path):
    """只差尾缀 → LOW；繁简异体（词干中部差异）→ OK（两类必须分开）。"""
    db = _mk_db(str(tmp_path / "a.db"), vm_rows=[
        # 前缀：归一后 写法是 标准名的后缀，残差 = 前缀 `老泥：`
        ("m_tail", "供应商A", "default", "active", "alias",
         "alias:好味豬油也 (420g X 12)", "老泥：好味豬油也 (420g X 12)", ""),
        # 词干中部：有機菜心 → 有机菜心（真实且有用的写法桥）
        ("m_mid", "供应商A", "default", "active", "alias", "alias:有機菜心", "有机菜心", ""),
    ])
    rep = qr.collect(db)
    assert _verdict(rep, "m_tail") == qr.LOW
    assert "首/尾续接" in _hit(rep, "m_tail")
    assert _verdict(rep, "m_mid") == qr.OK
    assert "不在首/尾" in _hit(rep, "m_mid")


def test_alias_residual_boundary_flips_at_threshold(tmp_path):
    """边界：残差恰好等于阈值判 LOW，阈值 +1 判 OK（上下两条分类相反）。"""
    tail16 = "ABCDEFGHIJKLMNOP"     # 16 个字符
    tail17 = "ABCDEFGHIJKLMNOPQ"    # 17 个字符
    db = _mk_db(str(tmp_path / "a.db"), vm_rows=[
        ("m_eq", "供应商A", "default", "active", "alias",
         "alias:菜心", "菜心" + tail16, ""),
        ("m_gt", "供应商A", "default", "active", "alias",
         "alias:菜心", "菜心" + tail17, ""),
    ])
    rep = qr.collect(db, alias_tail_max=16)
    assert _verdict(rep, "m_eq") == qr.LOW
    assert _verdict(rep, "m_gt") == qr.OK
    assert "残差 16<=16" in _hit(rep, "m_eq")
    assert "残差 17>16" in _hit(rep, "m_gt")


# -------------------------------------------------------------
# 判据二：price_range 伪区间
# -------------------------------------------------------------
def test_price_range_min_equals_max_is_low_and_wide_is_ok(tmp_path):
    db = _mk_db(str(tmp_path / "p.db"), vm_rows=[
        ("p_eq", "供应商A", "default", "active", "price_range",
         "price_range:急凍牛肋條", "40.00-40.00", "入库 15 次"),
        ("p_ok", "供应商A", "default", "active", "price_range",
         "price_range:菜心", "10.00-20.00", ""),
    ])
    rep = qr.collect(db)
    assert _verdict(rep, "p_eq") == qr.LOW
    assert "min==max" in _hit(rep, "p_eq")
    assert _verdict(rep, "p_ok") == qr.OK
    # 样本数从 notes 文本解析（读不到就不给）
    assert _rows_by_mid(rep)["p_eq"]["samples"] == 15
    assert "samples" not in _rows_by_mid(rep)["p_ok"]


def test_price_range_width_boundary_flips_at_threshold(tmp_path):
    """边界：宽度/中位价 恰好等于阈值判 OK，略低于阈值判 LOW（上下两条相反）。"""
    db = _mk_db(str(tmp_path / "p.db"), vm_rows=[
        # 下界 97.5 / 上界 102.5 → 中位 100、宽 5 → 比值 0.05（恰好等于阈值）
        ("p_eq", "供应商A", "default", "active", "price_range",
         "price_range:甲", "97.50-102.50", ""),
        # 下界 98 / 上界 102 → 中位 100、宽 4 → 比值 0.04（低于阈值）
        ("p_lt", "供应商A", "default", "active", "price_range",
         "price_range:乙", "98.00-102.00", ""),
    ])
    rep = qr.collect(db, price_width_ratio_max=0.05)
    assert _verdict(rep, "p_eq") == qr.OK
    assert _verdict(rep, "p_lt") == qr.LOW


# -------------------------------------------------------------
# 判据三：unit 无差异
# -------------------------------------------------------------
def test_unit_same_as_base_unit_is_low_and_diff_is_ok(tmp_path):
    db = _mk_db(str(tmp_path / "u.db"),
                vm_rows=[
                    ("u_same", "供应商A", "default", "active", "unit",
                     "unit:菜心", "斤", ""),
                    ("u_diff", "供应商A", "default", "active", "unit",
                     "unit:菜心", "公斤", ""),
                    # 回落形态 unit:#<sku_id>
                    ("u_by_id", "供应商A", "default", "active", "unit",
                     "unit:#1", "斤", ""),
                    # SKU 档案不存在 → 未解析，不判 LOW
                    ("u_unresolved", "供应商A", "default", "active", "unit",
                     "unit:不存在的品名", "斤", ""),
                ],
                sku_rows=[(1, "菜心", "斤", "default")])
    rep = qr.collect(db)
    assert _verdict(rep, "u_same") == qr.LOW
    assert "与 SKU 基准单位" in _hit(rep, "u_same")
    assert _verdict(rep, "u_diff") == qr.OK
    assert _verdict(rep, "u_by_id") == qr.LOW
    assert _verdict(rep, "u_unresolved") == qr.OK
    assert "未解析出" in _hit(rep, "u_unresolved")


def test_unit_ambiguous_sku_name_is_not_low(tmp_path):
    """同名两行 base_unit 不一致 → 视为未解析，不判 LOW（不凭空断言）。"""
    db = _mk_db(str(tmp_path / "u.db"),
                vm_rows=[("u_amb", "供应商A", "default", "active", "unit",
                          "unit:菜心", "斤", "")],
                sku_rows=[(1, "菜心", "斤", "default"), (2, "菜心", "公斤", "default")])
    rep = qr.collect(db)
    assert _verdict(rep, "u_amb") == qr.OK
    assert "未解析出" in _hit(rep, "u_amb")


# -------------------------------------------------------------
# 汇总 / 范围 / 退出码
# -------------------------------------------------------------
def test_summary_counts_low_per_kind(tmp_path):
    db = _mk_db(str(tmp_path / "s.db"), vm_rows=[
        ("a1", "供应商A", "default", "active", "alias", "alias:白糖", "白糖", ""),
        ("a2", "供应商A", "default", "active", "alias", "alias:有機菜心", "有机菜心", ""),
        ("l1", "供应商A", "default", "active", "layout", "layout:doc_form",
         "printed_delivery_note", ""),
        ("p1", "供应商A", "default", "active", "price_range",
         "price_range:甲", "5.00-5.00", ""),
    ])
    rep = qr.collect(db)
    assert rep["summary"]["alias"] == {"total": 2, "low": 1, "ratio_pct": 50.0}
    assert rep["summary"]["layout"]["low"] == 0
    assert rep["summary"]["price_range"]["low"] == 1
    assert rep["summary"]["__all__"] == {"total": 4, "low": 2, "ratio_pct": 50.0}
    assert rep["low_total"] == 2


def test_archived_excluded_by_default(tmp_path):
    db = _mk_db(str(tmp_path / "ar.db"), vm_rows=[
        ("m_active", "供应商A", "default", "active", "alias", "alias:白糖", "白糖", ""),
        ("m_arch", "供应商A", "default", "archived", "alias", "alias:白糖", "白糖", ""),
    ])
    assert qr.collect(db)["row_total"] == 1
    assert qr.collect(db, include_archived=True)["row_total"] == 2


def test_missing_db_reports_error_without_creating(tmp_path):
    """库不存在 → 报告错误且**不创建文件**（只读红线）。"""
    missing = str(tmp_path / "nope.db")
    rep = qr.collect(missing)
    assert rep.get("error")
    assert not os.path.exists(missing)


def test_fail_on_low_exit_code(tmp_path, capsys):
    low_db = _mk_db(str(tmp_path / "low.db"), vm_rows=[
        ("m1", "供应商A", "default", "active", "alias", "alias:白糖", "白糖", "")])
    ok_db = _mk_db(str(tmp_path / "ok.db"), vm_rows=[
        ("m1", "供应商A", "default", "active", "alias",
         "alias:有機菜心", "有机菜心", "")])
    assert qr.main(["--db-path", low_db, "--fail-on-low"]) == 1
    assert qr.main(["--db-path", ok_db, "--fail-on-low"]) == 0
    # 默认（不带 --fail-on-low）永远 0：它只是报告
    assert qr.main(["--db-path", low_db]) == 0


# -------------------------------------------------------------
# 判据自述（每类都要能解释「为何算低信息量 + 边界怎么定」）
# -------------------------------------------------------------
def test_criteria_texts_present_and_substantive():
    for name in ("_CRITERIA_ALIAS", "_CRITERIA_PRICE_RANGE", "_CRITERIA_UNIT"):
        text = getattr(qr, name)
        assert len(text) > 50, name
        assert "边界" in text, name
        assert "为何算低信息量" in text, name


def test_layout_is_not_judged_low(tmp_path):
    """layout 不在三类判据内 → 一律 OK（不假装有判据）。"""
    db = _mk_db(str(tmp_path / "l.db"), vm_rows=[
        ("l1", "供应商A", "default", "active", "layout", "layout:doc_form",
         "printed_delivery_note", "")])
    rep = qr.collect(db)
    assert _verdict(rep, "l1") == qr.OK
    assert "无判据" in _hit(rep, "l1")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

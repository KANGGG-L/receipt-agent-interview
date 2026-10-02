# -*- coding: utf-8 -*-
"""残渣行判别规则测试（SPEC_PHASE1 §3.3）—— 阴性不误伤是第一验收项。

判别式唯一出处：``demo/scripts/residue_rules.py``（本文件不复制任何正则）。
真实数据量级断言（@pytest.mark.eval）：对 DB_PATH 指向的 live 副本全量 645 行判定，
各规则行数 = §3.1 表值、合计 93、'0T' 7 行；副本不是 live 校准版（receipts≠659）时 skip。
"""

import os
import sqlite3

import pytest

from residue_rules import RESIDUE_RULES, classify, is_unmatchable

# 运行方注入的环境库路径（import 期捕获——conftest 的 autouse DB 隔离夹具会在用例期
# 改写 os.environ["DB_PATH"]，量级断言要对的是运行方指定的 live 校准副本，不是测试临时库）
_AMBIENT_DB_PATH = os.environ.get("DB_PATH")

# -------------------------------------------------------------
# X-正例逐规则（每条规则 1 个真实形态正例，SPEC §3.3 表）
# -------------------------------------------------------------
POSITIVE_CASES = [
    ("INV/202402000013", None, "R-INV"),
    ("七月烤魚[將軍澳]-1994", None, "R-FIXTURE-JULY"),
    ("测试长单项1", None, "R-FIXTURE-LONGITEM"),
    ("雞蛋", "默認來源測試", "R-FIXTURE-SUPPLIER"),
    ("?????? (25kg)", None, "R-QUESTION"),
    ("xxx5KG", None, "R-XXX"),
    ("菜心", "T7证据验证临时单", "R-T7-RECEIPT"),
]


@pytest.mark.parametrize("name,supplier,expected_rule", POSITIVE_CASES)
def test_positive_each_rule(name, supplier, expected_rule):
    assert classify(name, supplier) == (True, expected_rule)


def test_rule_ids_complete():
    """七条规则 id 与规格 §3.1 表逐条对齐（防改名/漏条）。"""
    assert [r["id"] for r in RESIDUE_RULES] == [
        "R-INV", "R-FIXTURE-JULY", "R-FIXTURE-LONGITEM", "R-FIXTURE-SUPPLIER",
        "R-QUESTION", "R-XXX", "R-T7-RECEIPT",
    ]
    for rule in RESIDUE_RULES:
        assert rule["scope"] in ("name", "receipt")
        # 逐条必须带 description（出处行数与归纳来源，SPEC §3.1）
        assert rule.get("description"), rule["id"]


# -------------------------------------------------------------
# X-阴性对照（关键）：阴性不误伤是本工具第一验收项
# -------------------------------------------------------------
NEGATIVE_CASES = [
    ("特級菜心", None),
    ("菜心", "德利行"),
    ("半殼生蠔", None),
    ("江團魚", None),
    ("INVOICE 號碼", None),   # 无 `/`，不得命中 R-INV
    ("xxx醬油", None),        # 无 KG 后缀，不得命中 R-XXX
    ("inv2024", None),        # 无斜杠，不得命中 R-INV
]


@pytest.mark.parametrize("name,supplier", NEGATIVE_CASES)
def test_negative_not_flagged(name, supplier):
    assert classify(name, supplier) == (False, None)


# -------------------------------------------------------------
# X-0T：'0T' 型 OCR 废码是单独类别，与残渣分开
# -------------------------------------------------------------
def test_zero_t_is_separate_category():
    assert is_unmatchable("0T") is True
    assert is_unmatchable(" 0T ") is True        # strip 后判定
    assert classify("0T", None) == (False, None)  # 是废码**不是**残渣


@pytest.mark.parametrize("name", ["0TX", "10T", "OT", "0t"])
def test_zero_t_generalization_stays_narrow(name):
    """泛化形态（^[0-9][A-Z]$）不启用——宁窄勿宽（含小写 0t 也不命中）。"""
    assert is_unmatchable(name) is False


# -------------------------------------------------------------
# X-receipt 级规则：name 合法但供应商是夹具/临时单 → 仍判残渣
# -------------------------------------------------------------
def test_receipt_scope_rule_fires_on_legal_name():
    assert classify("菜心", "默認來源測試") == (True, "R-FIXTURE-SUPPLIER")
    assert classify("白菜", "T7证据验证临时单") == (True, "R-T7-RECEIPT")
    # 供应商名带首尾空格也要命中（strip 语义）
    assert classify("菜心", " 默認來源測試 ") == (True, "R-FIXTURE-SUPPLIER")


def test_receipt_scope_requires_supplier():
    """scope=receipt 规则在 supplier 缺失时不凭空命中。"""
    assert classify("菜心", None) == (False, None)
    assert classify("菜心", "德利行") == (False, None)


# -------------------------------------------------------------
# 真实数据量级（eval 门禁：DB_PATH 副本 receipts≠659 时 skip）
# -------------------------------------------------------------
def _live_copy_receipt_count():
    db_path = _AMBIENT_DB_PATH
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        try:
            return conn.execute("SELECT count(*) FROM receipts").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


@pytest.mark.eval
def test_real_corpus_rule_row_counts():
    if _live_copy_receipt_count() != 659:
        pytest.skip("DB_PATH 未指向 live 校准副本（receipts≠659），量级断言仅对校准副本生效")
    conn = sqlite3.connect("file:%s?mode=ro" % _AMBIENT_DB_PATH, uri=True)
    try:
        rows = conn.execute(
            "SELECT i.name, r.supplier_name FROM receipt_items i"
            " JOIN receipts r ON r.id = i.receipt_id").fetchall()
    finally:
        conn.close()
    assert len(rows) == 645
    counts = {}
    unmatchable = 0
    for name, supplier in rows:
        is_res, rule_id = classify(name, supplier)
        if is_res:
            counts[rule_id] = counts.get(rule_id, 0) + 1
        elif is_unmatchable(name):
            unmatchable += 1
    # §3.1 表值（2026-09-27 副本 md5=7cc31409… 实测校准）
    assert counts == {
        "R-INV": 21, "R-FIXTURE-JULY": 26, "R-FIXTURE-LONGITEM": 20,
        "R-FIXTURE-SUPPLIER": 16, "R-QUESTION": 6, "R-XXX": 2, "R-T7-RECEIPT": 2,
    }
    assert sum(counts.values()) == 93
    assert unmatchable == 7

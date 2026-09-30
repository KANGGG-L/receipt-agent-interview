# -*- coding: utf-8 -*-
"""D26 回归：回写 supplier_name 时同步刷新 receipts.supplier_id（D17 锚点陈旧缺陷）。

背景（why 需要这一批）：save_parsed_data 用识别出的 vendor 覆盖
receipts.supplier_name，却从不更新 receipts.supplier_id。同一单据被重新识别、供应商名
被改写后，supplier_id 仍指向旧供应商 —— 名与 id 互相矛盾（独立复核实测：receipt 45
supplier_name=鴻運餐飲 却挂 supplier_id=4=德利利行，而同名 10 条都是 5）。

判别式设计（非恒真）：把 `_refresh_receipt_supplier_anchor` 的调用去掉后，
①「改写到有档案名 → 锚点不刷新」与 ②「改写到无档案名 → 陈旧锚点不被清成 NULL」
两条断言都会失败；③ 幂等用例在刷新逻辑存在/缺失两种实现下都须通过（不靠它反证）。

全程隔离临时库 + 空库自建档案，零外部调用、零 live 库读写。
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))

from app import db  # noqa: E402
from app.models import DocForm, ReceiptData, ReceiptItem  # noqa: E402
from app.services import receipt_utils  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每用例独立临时库；跑完还原（与 test_parsed_with_warnings 同款隔离）。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_supplier_anchor.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_db_path
    db._make_engine()


def _mk_supplier(name, tenant_id="default"):
    sid, err = db.create_supplier(name, tenant_id=tenant_id)
    assert err is None, f"建供应商档案失败: {err}"
    assert sid is not None
    return int(sid)


def _data(vendor, tenant_id="default"):
    return ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor=vendor,
        date="2026-09-23",
        items=[ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=10, amount=10)],
        total=10.0,
        payment_marked=False,
        confidence=0.9,
    )


_RESULT = {"status": "parsed", "raw": "", "use_grey": 0, "audit_result": {}}


def _supplier_count():
    return len(db.list_suppliers(include_inactive=True, tenant_id=None))


# -------------------------------------------------------------
# ① 改写到「有档案」的名字 → supplier_id 同步刷新为该档案
# -------------------------------------------------------------
def test_rewrite_to_archived_name_refreshes_anchor():
    sup_a = _mk_supplier("甲供應商")
    sup_b = _mk_supplier("乙供應商")
    # 单据初始锚定在 A（模拟上一次识别写下的名与 id 一致的状态）
    rid = db.create_receipt_record(supplier_name="甲供應商", supplier_id=sup_a,
                                   tenant_id="default", status="parsing")
    assert db.get_receipt_row(rid).supplier_id == sup_a

    # 重新识别：供应商名被改写为 B
    receipt_utils.save_parsed_data(rid, _data("乙供應商"), _RESULT)

    row = db.get_receipt_row(rid)
    assert row.supplier_name == "乙供應商"
    assert row.supplier_id == sup_b, "改写到有档案的名字后锚点必须跟随刷新"
    assert row.supplier_id != sup_a, "陈旧锚点不得保留"


# -------------------------------------------------------------
# ② 改写到「无档案」的名字 → supplier_id 置 NULL 且不新建档案
# -------------------------------------------------------------
def test_rewrite_to_unarchived_name_nulls_anchor_without_creating_supplier():
    sup_a = _mk_supplier("甲供應商")
    rid = db.create_receipt_record(supplier_name="甲供應商", supplier_id=sup_a,
                                   tenant_id="default", status="parsing")
    before = _supplier_count()

    receipt_utils.save_parsed_data(rid, _data("查無此商號XYZ"), _RESULT)

    row = db.get_receipt_row(rid)
    assert row.supplier_name == "查無此商號XYZ"
    assert row.supplier_id is None, "解析不到档案时陈旧锚点必须被清成 NULL"
    assert _supplier_count() == before, "解析不到档案时绝不凭空新建供应商档案"
    assert db.find_supplier_by_name("查無此商號XYZ", tenant_id="default") is None


# -------------------------------------------------------------
# ③ 名字未变 → 结果与刷新前完全一致（幂等）
# -------------------------------------------------------------
def test_unchanged_name_keeps_anchor_identical_and_idempotent():
    sup_a = _mk_supplier("甲供應商")
    rid = db.create_receipt_record(supplier_name="甲供應商", supplier_id=sup_a,
                                   tenant_id="default", status="parsing")
    before = _supplier_count()

    receipt_utils.save_parsed_data(rid, _data("甲供應商"), _RESULT)
    row1 = db.get_receipt_row(rid)
    assert row1.supplier_name == "甲供應商"
    assert row1.supplier_id == sup_a, "名字未变时锚点必须与刷新前完全一致"

    # 再落一次：结果仍逐字段一致，且不产生任何新档案
    receipt_utils.save_parsed_data(rid, _data("甲供應商"), _RESULT)
    row2 = db.get_receipt_row(rid)
    assert row2.supplier_name == row1.supplier_name
    assert row2.supplier_id == row1.supplier_id
    assert _supplier_count() == before


def test_unchanged_unarchived_name_stays_null():
    """名字未变且本就无档案（NULL）→ 刷新后仍为 NULL（保持/达成一致，非恒真反证）。"""
    rid = db.create_receipt_record(supplier_name="無檔案商號", supplier_id=None,
                                   tenant_id="default", status="parsing")
    before = _supplier_count()
    receipt_utils.save_parsed_data(rid, _data("無檔案商號"), _RESULT)
    row = db.get_receipt_row(rid)
    assert row.supplier_id is None
    assert _supplier_count() == before


# -------------------------------------------------------------
# 归一通道：精确名不中、canonical_supplier_key 命中
# -------------------------------------------------------------
def test_canonical_key_channel_matches_traditional_variant():
    # 档案名与识别名繁简不同，精确匹配不中，靠 canonical 归一命中
    sup = _mk_supplier("興隆行")
    rid = db.create_receipt_record(supplier_name="", supplier_id=None,
                                   tenant_id="default", status="parsing")

    receipt_utils.save_parsed_data(rid, _data("兴隆行"), _RESULT)

    row = db.get_receipt_row(rid)
    assert row.supplier_name == "兴隆行"
    assert row.supplier_id == sup, "canonical 归一通道应覆盖繁简/别名差异"


def test_canonical_key_channel_matches_english_annotation():
    # 档案带英文括号注，识别名是纯中文简称 → canonical 去括号注后命中
    sup = _mk_supplier("新鴻興食品貿易有限公司 (New Hip Hing Trading)")
    rid = db.create_receipt_record(supplier_name="", supplier_id=None,
                                   tenant_id="default", status="parsing")

    receipt_utils.save_parsed_data(rid, _data("新鴻興食品貿易有限公司"), _RESULT)

    assert db.get_receipt_row(rid).supplier_id == sup


# -------------------------------------------------------------
# 租户收窄：不得挂到其它租户的同名档案上
# -------------------------------------------------------------
def test_tenant_isolation_does_not_cross_anchor():
    _mk_supplier("丁供應商", tenant_id="t2")
    before = _supplier_count()
    rid = db.create_receipt_record(supplier_name="", supplier_id=None,
                                   tenant_id="default", status="parsing")

    receipt_utils.save_parsed_data(rid, _data("丁供應商"), _RESULT)

    row = db.get_receipt_row(rid)
    assert row.supplier_id is None, "当前租户无该档案时不得跨租户错挂"
    assert _supplier_count() == before


# -------------------------------------------------------------
# 读取失败不得把锚点误清成 NULL（区分「无档案」与「读不到」）
# -------------------------------------------------------------
def test_read_failure_keeps_existing_anchor(monkeypatch, caplog):
    sup_a = _mk_supplier("甲供應商")
    rid = db.create_receipt_record(supplier_name="甲供應商", supplier_id=sup_a,
                                   tenant_id="default", status="parsing")

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(receipt_utils.db, "list_suppliers", _boom)
    with caplog.at_level(logging.WARNING, logger="app.services.receipt_utils"):
        receipt_utils.save_parsed_data(rid, _data("甲供應商"), _RESULT)

    assert db.get_receipt_row(rid).supplier_id == sup_a, \
        "读取档案失败属可恢复故障，不得当成无档案把锚点清空"
    assert any("读取供应商档案失败" in r.getMessage() for r in caplog.records)

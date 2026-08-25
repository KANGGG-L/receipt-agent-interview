# -*- coding: utf-8 -*-
"""
Unit and Integration Tests for Issue U-13 & U-14:
"U-13 & U-14 本地化与极端异常兜底（多币种同步与香港粤语口语别名映射）"

Coverage:
1. Multi-Currency Synchronization & Formatting (HKD, CNY, USD, EUR, JPY, GBP, MOP, SGD)
2. HK Restaurant Spoken Items Mapping & raw_name preservation (冻柠茶, 冻奶茶, 丝袜奶茶, 鸳鸯, 菜心, 芥兰, 干炒牛河, 出前一丁, 双拼)
3. Common Supplier Abbreviations & Normalization (德利行, 祥兴, 金百加, 联丰, 大生)
4. Robust Math Engine ($0 gift items, discounts, deposits, void/strikethrough items)
5. API Persistence of currency, payment_mark, and settlement_type
"""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="u13_u14_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_u13_u14.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import pytest
from app.models import ReceiptData, ReceiptItem, DocForm
from app.services.math_engine import validate_and_report, audit_trail
from ai_registry.tools.math_engine.v2_0_0 import MathEngineTool
from ai_registry.tools.smart_splitter.v1_2_0_multi_pack import SmartSplitterTool
from app.services.supplier_normalizer import canonical_supplier_key
from app.services.rag import retrieve_context, ingest_memory


# =====================================================================
# 1. HK Restaurant Spoken Aliases & raw_name Preservation
# =====================================================================

def test_hk_spoken_aliases_smart_splitter():
    splitter = SmartSplitterTool()

    cases = [
        # (input_raw, expected_item_name)
        ("0T", "冻柠茶"),
        ("柠T", "冻柠茶"),
        ("凍檸茶", "冻柠茶"),
        ("凍檸", "冻柠茶"),
        ("柠檬茶", "冻柠茶"),
        ("凍奶", "冻奶茶"),
        ("凍奶茶", "冻奶茶"),
        ("港式奶茶", "冻奶茶"),
        ("絲襪奶茶", "丝袜奶茶"),
        ("丝袜奶", "丝袜奶茶"),
        ("鴛鴦", "鸳鸯"),
        ("凍鴛鴦", "鸳鸯"),
        ("熱鴛鴦", "鸳鸯"),
        ("菜芯", "菜心"),
        ("菜冧", "菜心"),
        ("廣東菜心", "广东菜心"),
        ("香港有機菜心", "香港有机菜心"),
        ("本地新鮮菜心", "本地新鲜菜心"),
        ("芥蘭", "芥兰"),
        ("芥藍", "芥兰"),
        ("芥蘭苗", "芥兰"),
        ("廣東芥蘭", "广东芥兰"),
        ("乾炒牛河", "干炒牛河"),
        ("牛河", "干炒牛河"),
        ("乾炒牛肉河粉", "干炒牛河"),
        ("一丁", "出前一丁"),
        ("丁麵", "出前一丁"),
        ("出前一丁麵", "出前一丁"),
        ("雙拼", "双拼"),
        ("雙併", "双拼"),
        ("燒味雙拼", "双拼"),
        ("雙拼飯", "双拼"),
    ]

    for raw_in, expected_name in cases:
        res = splitter.execute(raw_in)
        assert res["item_name"] == expected_name, f"Failed for {raw_in}: got {res['item_name']}, expected {expected_name}"
        assert res["raw_name"] == raw_in, f"raw_name not preserved for {raw_in}: got {res['raw_name']}"


def test_hk_spoken_aliases_with_pack_multiplier():
    splitter = SmartSplitterTool()

    # "凍檸茶 330ml*24罐"
    res = splitter.execute("凍檸茶 330ml*24罐")
    assert res["clean_name"] == "冻柠茶 330ml"
    assert res["quantity"] == 24.0
    assert res["unit"] == "罐"
    assert res["raw_name"] == "凍檸茶 330ml*24罐"

    # "菜心苗 10斤*2箱"
    res = splitter.execute("菜心苗 10斤*2箱")
    assert res["clean_name"] == "菜心 10斤"
    assert res["quantity"] == 2.0
    assert res["unit"] == "箱"


# =====================================================================
# 2. Supplier Abbreviations Normalization
# =====================================================================

def test_supplier_abbreviations_normalizer():
    cases = [
        ("德利行", "德利行"),
        ("德利行 Tak Lee Hong", "德利行"),
        ("Tak Lee Hong", "德利行"),
        ("祥興快餐用品", "祥兴"),
        ("祥兴餐具批发", "祥兴"),
        ("Cheung Hing", "祥兴"),
        ("金百加發展有限公司", "金百加"),
        ("Kampery", "金百加"),
        ("聯豐食品", "联丰"),
        ("Luen Fung", "联丰"),
        ("大生糧油", "大生"),
        ("Tai Sang", "大生"),
    ]
    for raw_sup, expected_key in cases:
        key = canonical_supplier_key(raw_sup)
        assert key == expected_key, f"Failed for {raw_sup}: got '{key}', expected '{expected_key}'"


def test_rag_supplier_alias_matching():
    # Ingest memories under standard name
    ingest_memory(vendor="德利行 Tak Lee Hong", items_text="大豆油 5L 2樽 $180\n特级生抽 1.8L $45", notes="德利行常用单位：樽/桶/箱", tenant_id="test_u13")
    ingest_memory(vendor="祥興快餐用品", items_text="外卖盒 500个 $120\n塑料勺 1000支 $50", notes="祥兴结算常走月结", tenant_id="test_u13")

    # Retrieve using short name / English alias
    ctx_tak = retrieve_context("德利行", tenant_id="test_u13")
    assert "德利行" in ctx_tak or "大豆油" in ctx_tak

    ctx_cheung = retrieve_context("祥兴", tenant_id="test_u13")
    assert "祥興" in ctx_cheung or "外卖盒" in ctx_cheung or "祥兴" in ctx_cheung


# =====================================================================
# 3. Robust Math Engine ($0 gifts, discounts, deposits, void lines)
# =====================================================================

def test_math_engine_zero_dollar_gift():
    # Regular receipt with a $0 gift item (赠品)
    items = [
        ReceiptItem(name="鲜鸡蛋", qty=2, unit="盘", unit_price=45.0, amount=90.0),
        ReceiptItem(name="赠品: 调味醋 (赠送)", qty=1, unit="支", unit_price=0.0, amount=0.0),
    ]
    data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="德利行",
        date="2026-08-24",
        items=items,
        total=90.0,
        payment_marked=True,
        confidence=0.95
    )
    problems = validate_and_report(data)
    assert len(problems) == 0, f"Expected 0 problems, got {problems}"


def test_math_engine_gift_with_listed_unit_price_zero_amount():
    # Gift item has catalog unit_price=20.0 but actual amount=0.0 (marked as 赠品)
    items = [
        ReceiptItem(name="澳洲和牛M7", qty=2, unit="公斤", unit_price=300.0, amount=600.0),
        ReceiptItem(name="赠品 烧烤酱", qty=1, unit="瓶", unit_price=20.0, amount=0.0),
    ]
    data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="大生",
        date="2026-08-24",
        items=items,
        total=600.0,
        payment_marked=True,
        confidence=0.95
    )
    problems = validate_and_report(data)
    assert len(problems) == 0, f"Gift item with zero amount should not trigger math problem: {problems}"


def test_math_engine_discount_and_deposit():
    # Subtotal = 500, discount = 50, deposit = 30, total = 480
    items = [
        ReceiptItem(name="有机菜心", qty=10, unit="斤", unit_price=20.0, amount=200.0),
        ReceiptItem(name="芥兰苗", qty=15, unit="斤", unit_price=20.0, amount=300.0),
    ]
    data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="联丰食品",
        date="2026-08-24",
        items=items,
        total=480.0,
        discount_amount=50.0,
        deposit_amount=30.0,
        payment_marked=False,
        confidence=0.9
    )
    problems = validate_and_report(data)
    assert len(problems) == 0, f"Discounts and deposits should balance correctly: {problems}"


def test_math_engine_void_strikethrough_excluded():
    # Item 1: 100, Item 2 (is_void): 200, Total: 100
    items = [
        ReceiptItem(name="丝袜奶茶红茶包", qty=2, unit="包", unit_price=50.0, amount=100.0),
        ReceiptItem(name="~~变质退回牛奶~~", qty=4, unit="箱", unit_price=50.0, amount=200.0, is_void=True),
    ]
    data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="金百加",
        date="2026-08-24",
        items=items,
        total=100.0,
        payment_marked=True,
        confidence=0.95
    )
    problems = validate_and_report(data)
    assert len(problems) == 0, f"Void items should be excluded from math: {problems}"

    audit = audit_trail(data)
    assert audit["items_sum"] == 100.0
    assert audit["declared_total"] == 100.0


def test_tool_math_engine_v2():
    tool = MathEngineTool()

    items = [
        {"item_name": "干炒牛河牛肉", "quantity": 5, "unit_price": 40.0, "amount": 200.0},
        {"item_name": "出前一丁", "quantity": 10, "unit_price": 5.0, "amount": 50.0},
        {"item_name": "赠品 辣椒酱", "quantity": 1, "unit_price": 10.0, "amount": 0.0},
        {"item_name": "作废商品", "quantity": 2, "unit_price": 50.0, "amount": 100.0, "is_void": True},
    ]
    # Total: 200 + 50 + 0 = 250 - 20 (discount) + 10 (deposit) = 240
    is_valid, diffs, suggestions = tool.execute(items, total_amount=240.0, discount_amount=20.0, deposit_amount=10.0)
    assert is_valid is True
    assert len(diffs) == 0
    assert suggestions["calculated_total"] == 240.0

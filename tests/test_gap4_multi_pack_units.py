# -*- coding: utf-8 -*-
"""
Gap 4 专项测试：复合包装乘数解耦、计件单位保护与流水号混合剥离回归测试集。
"""

import pytest
from ai_registry.tools.smart_splitter.v1_2_0_multi_pack import SmartSplitterTool


@pytest.fixture
def splitter():
    return SmartSplitterTool()


def test_multi_pack_decoupling(splitter):
    """测试各类复合包装乘数与计件单位精准拆解。"""
    cases = [
        ("大豆油 5L*2樽", "大豆油 5L", 2.0, "樽"),
        ("可口可乐 330ml*24罐", "可口可乐 330ml", 24.0, "罐"),
        ("海皇特级生抽 1.8L*6支", "海皇特级生抽 1.8L", 6.0, "支"),
        ("急冻牛肋条 2kg*5包", "急冻牛肋条 2kg", 5.0, "包"),
        ("李锦记旧庄蚝油 510g*12樽", "李锦记旧庄蚝油 510g", 12.0, "樽"),
        ("鲜鸡蛋 30只*3盘", "鲜鸡蛋 30只", 3.0, "盘"),
        ("特级绿茶 250ml*24盒", "特级绿茶 250ml", 24.0, "盒"),
        ("百事可乐 330ml x 12 听", "百事可乐 330ml", 12.0, "听"),
        ("纯正花生油 900ml*4瓶", "纯正花生油 900ml", 4.0, "瓶"),
    ]

    for raw, exp_name, exp_qty, exp_unit in cases:
        res = splitter.execute(raw)
        assert res["item_name"] == exp_name, f"品名拆解错误: {res['item_name']} != {exp_name} (原值: {raw})"
        assert res["quantity"] == exp_qty, f"数量拆解错误: {res['quantity']} != {exp_qty} (原值: {raw})"
        assert res["unit"] == exp_unit, f"单位拆解错误: {res['unit']} != {exp_unit} (原值: {raw})"


def test_multi_pack_with_sku_code(splitter):
    """测试复合包装同时携带流水号/条形码时的双重拆解。"""
    res = splitter.execute("大豆油 5L*2樽_1787140420")
    assert res["item_name"] == "大豆油 5L"
    assert res["quantity"] == 2.0
    assert res["unit"] == "樽"
    assert res["item_code"] == "1787140420"

    res2 = splitter.execute("A08-可口可乐 330ml*24罐 [LOT20240815]")
    assert res2["item_name"] == "可口可乐 330ml"
    assert res2["quantity"] == 24.0
    assert res2["unit"] == "罐"
    assert res2["item_code"] in ["A08", "LOT20240815"]


def test_protected_single_items_not_broken(splitter):
    """测试常规单一食材及含数字品牌不被错误破坏。"""
    cases = [
        ("7喜汽水", "7喜汽水"),
        ("1664啤酒 330ml", "1664啤酒 330ml"),
        ("三花淡奶", "三花淡奶"),
        ("特级有机菜心", "特级有机菜心"),
        ("澳洲和牛M7", "澳洲和牛M7"),
        ("3头优质鲍鱼", "3头优质鲍鱼"),
    ]

    for raw, exp_name in cases:
        res = splitter.execute(raw)
        assert res["item_name"] == exp_name, f"受保护品名被破坏: {res['item_name']} != {exp_name}"

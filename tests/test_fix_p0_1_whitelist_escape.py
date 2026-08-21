# -*- coding: utf-8 -*-
"""
P0-1 修复专项测试：合法商品白名单优先匹配与防误判为折让/押金/运费（支持简体与繁体中文全量用例）。
"""

import pytest
from ai_registry.tools.item_sanitizer.v1_3_0_notes_clean import ItemSanitizerTool as ItemSanitizerV130
from ai_registry.tools.item_sanitizer.v1_2_0_fee_clean import ItemSanitizerTool as ItemSanitizerV120


def test_v130_legit_items_not_classified_as_fees():
    sanitizer = ItemSanitizerV130()
    
    # 1. 验证简体与繁体优惠套餐、折扣菜、押金收条等不被误判为折让或费用
    cases = [
        {"name": "特级优惠套餐 A", "quantity": 1, "unit": "套", "unit_price": 68.0, "amount": 68.0},
        {"name": "特級優惠套餐 B", "quantity": 1, "unit": "套", "unit_price": 78.0, "amount": 78.0},
        {"name": "主厨折扣菜：清蒸石斑", "quantity": 1, "unit": "份", "unit_price": 128.0, "amount": 128.0},
        {"name": "主廚折扣菜：紅燒石斑", "quantity": 1, "unit": "份", "unit_price": 138.0, "amount": 138.0},
        {"name": "生滚电话粥", "quantity": 2, "unit": "碗", "unit_price": 25.0, "amount": 50.0},
        {"name": "生滾電話粥", "quantity": 2, "unit": "碗", "unit_price": 25.0, "amount": 50.0},
        {"name": "九龙酱油 500ml", "quantity": 5, "unit": "支", "unit_price": 18.0, "amount": 90.0},
        {"name": "九龍特級生抽 500ml", "quantity": 5, "unit": "支", "unit_price": 22.0, "amount": 110.0},
        {"name": "押金收条", "quantity": 1, "unit": "张", "unit_price": 0.0, "amount": 0.0},
        {"name": "押金收條", "quantity": 1, "unit": "張", "unit_price": 0.0, "amount": 0.0},
    ]

    for item in cases:
        fee_type, val = sanitizer.classify_fee_item(item)
        assert fee_type == "none", f"合法商品被误判为费用: {item['name']} -> {fee_type}"
        assert val == 0.0

    # 2. 验证 filter_and_extract_all 完整保留这些合法菜品（繁简双语）
    clean_items, fees, notes, dropped = sanitizer.filter_and_extract_all(cases)
    assert len(clean_items) == len(cases), f"合法商品被错误剔除: clean={len(clean_items)}, expected={len(cases)}"
    assert fees["discount_amount"] == 0.0
    assert fees["deposit_amount"] == 0.0
    assert fees["delivery_fee"] == 0.0


def test_v130_traditional_stamps_and_disclaimers_filtered():
    """验证繁体中文印章、免责条款能被准确过滤。"""
    sanitizer = ItemSanitizerV130()
    
    # 繁体印章与经手人
    assert sanitizer.is_stamp_or_annotation("現金收訖") is True
    assert sanitizer.is_stamp_or_annotation("已結清") is True
    assert sanitizer.is_stamp_or_annotation("司廚簽收") is True
    assert sanitizer.is_stamp_or_annotation("經手人: 陳大文") is True

    # 繁体免责声明
    assert sanitizer.is_disclaimer_or_contact("貨物出門恕不退換") is True
    assert sanitizer.is_disclaimer_or_contact("如有遺失概不負責") is True
    assert sanitizer.is_disclaimer_or_contact("查詢熱綫: 23881234") is True
    assert sanitizer.is_disclaimer_or_contact("轉數快 FPS ID: 1234567") is True


def test_v120_legit_items_not_noise():
    sanitizer = ItemSanitizerV120()

    # 1. 验证 is_noise_item 对繁简白名单商品均返回 False
    for name in ["优惠套餐", "優惠套餐", "折扣菜", "主廚折扣菜", "电话粥", "電話粥", "押金收条", "押金收條"]:
        is_noise, reason = sanitizer.is_noise_item(name, 1, 50, 50)
        assert is_noise is False, f"白名单商品被误判为噪声: {name} ({reason})"

    # 2. 验证 filter_and_extract_fees 完整保留繁简白名单菜品
    raw_items = [
        {"name": "優惠套餐", "quantity": 1, "unit_price": 50.0, "amount": 50.0},
        {"name": "押金收條", "quantity": 1, "unit_price": 5.0, "amount": 5.0},
        {"name": "整單折讓 -$20", "quantity": 1, "unit_price": 20.0, "amount": 20.0},
        {"name": "膠筐押金 +$40", "quantity": 1, "unit_price": 40.0, "amount": 40.0},
        {"name": "現金收訖", "quantity": 1, "unit_price": 0.0, "amount": 0.0},
    ]
    clean_items, fees, removed = sanitizer.filter_and_extract_fees(raw_items)
    clean_names = [it["name"] for it in clean_items]
    assert "優惠套餐" in clean_names
    assert "押金收條" in clean_names
    assert len(clean_items) == 2
    assert fees["discount_amount"] == 20.0
    assert fees["deposit_amount"] == 40.0

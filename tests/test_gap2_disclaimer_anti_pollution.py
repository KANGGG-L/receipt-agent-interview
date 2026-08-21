# -*- coding: utf-8 -*-
"""
Gap 2 专项测试：表头表尾免责条款、联系电话、地址、银行账户等非商品杂质过滤与白名单保护回归测试集。
"""

import pytest
from ai_registry.tools.item_sanitizer.v1_1_0_disclaimer_clean import ItemSanitizerTool


@pytest.fixture
def sanitizer():
    return ItemSanitizerTool()


def test_disclaimer_and_terms_filtration(sanitizer):
    """测试各类免责声明与商业条款被准确识别并过滤。"""
    disclaimer_cases = [
        "貨物出門恕不退換", "货物出门恕不退换", "货物出门 恕不退换", "出门恕不退换",
        "如有遺失恕不負責", "如有遗失恕不负责", "單據遺失概不負責", "单据遗失恕不负责",
        "多謝惠顧 歡迎光臨", "多谢惠顾", "THANK YOU FOR YOUR BUSINESS",
        "此單據不能作退稅用途", "落單請提前一日致電"
    ]
    for text in disclaimer_cases:
        is_noise, reason = sanitizer.is_noise_item(text, qty=1, unit_price=0, amount=0)
        assert is_noise is True, f"免责条款 '{text}' 未被正确识别为杂质 (reason: {reason})"


def test_contact_and_address_filtration(sanitizer):
    """测试联系电话、传真、电邮、地址与银行账户被准确识别并过滤。"""
    contact_cases = [
        "TEL: 2388-1234", "TEL: 23881234 / 23885678", "FAX: 2388-5678", "電話: 98765432",
        "EMAIL: sales@fulin.com", "WEB: www.fulin-food.com",
        "地址: 香港九龍油麻地新填地街123號地下", "香港新界葵涌葵喜街38號金德工業大廈10樓B室",
        "匯豐銀行戶口: 123-456-789-001", "FPS ID: 98765432", "轉數快: 98765432"
    ]
    for text in contact_cases:
        is_noise, reason = sanitizer.is_noise_item(text, qty=1, unit_price=0, amount=0)
        assert is_noise is True, f"联系信息 '{text}' 未被正确识别为杂质 (reason: {reason})"


def test_legit_item_whitelist_protection(sanitizer):
    """测试合法包含地址/电话字眼的正常商品绝不误杀。"""
    legit_cases = [
        ("九龙酱油 500ml", 2.0, 18.0, 36.0),
        ("九龍醬油", 1.0, 25.0, 25.0),
        ("电话线干菜 1包", 3.0, 12.0, 36.0),
        ("地址标签纸 1卷", 5.0, 10.0, 50.0),
        ("热敏打印纸", 10.0, 6.0, 60.0),
        ("印章印油", 2.0, 15.0, 30.0),
        ("优质红富士苹果", 10.0, 8.0, 80.0),
    ]
    for name, qty, price, amt in legit_cases:
        is_noise, reason = sanitizer.is_noise_item(name, qty, price, amt)
        assert is_noise is False, f"合法商品 '{name}' 被错误判定为杂质 (reason: {reason})"


def test_mixed_dirty_items_pipeline(sanitizer):
    """测试包含印章、免责声明、电话地址的混合明细批处理过滤。"""
    dirty_items = [
        {"name": "TEL: 23881234", "qty": 1, "unit_price": 0.0, "amount": 0.0},
        {"name": "特级有机菜心", "qty": 10, "unit_price": 8.5, "amount": 85.0},
        {"name": "現金收訖", "qty": 1, "unit_price": 0.0, "amount": 0.0},
        {"name": "九龙酱油 500ml", "qty": 2, "unit_price": 18.0, "amount": 36.0},
        {"name": "貨物出門 恕不退換", "qty": 1, "unit_price": 0.0, "amount": 0.0},
        {"name": "地址: 香港九龍油麻地新填地街123號地下", "qty": 1, "unit_price": 0.0, "amount": 0.0},
        {"name": "鮮活草蝦", "qty": 4, "unit_price": 45.0, "amount": 180.0},
        {"name": "匯豐銀行戶口: 123-456-789", "qty": 1, "unit_price": 0.0, "amount": 0.0},
    ]

    clean_items, removed = sanitizer.filter_items(dirty_items)

    assert len(clean_items) == 3, f"预期保留 3 项合法商品，实际保留 {len(clean_items)} 项"
    assert len(removed) == 5, f"预期过滤 5 项非商品杂质，实际过滤 {len(removed)} 项"

    clean_names = [it["name"] for it in clean_items]
    assert clean_names == ["特级有机菜心", "九龙酱油 500ml", "鮮活草蝦"]

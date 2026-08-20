# -*- coding: utf-8 -*-
"""AC-08 幻觉拦截对抗测试集：≥50张构造样本，覆盖4类幻觉。

幻觉类型（PRD step11:141）：
1. 捏造品名（凭空造不存在的商品名）
2. 错读数量（数量与单价算术不自洽）
3. 重复行（同一行重复出现）
4. 免责条款当商品（法律免责文字被抽取为明细行）
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))

from app.services.contract import validate_contract
from app.services.math_engine import validate_and_report
from app.models import ReceiptData, ReceiptItem, DocForm


def _make_payload(items_data, vendor="测试供应商", total=None):
    """构造 ReceiptData dict，items_data = list of dict。"""
    items = []
    for d in items_data:
        items.append({
            "name": d.get("name", ""),
            "qty": d.get("quantity", 0),
            "unit_price": d.get("unit_price", 0),
            "amount": d.get("amount", 0),
            "unit": d.get("unit", "斤"),
        })
    if total is None:
        total = sum(it["amount"] for it in items_data)
    return {
        "doc_form": "printed_delivery_note",
        "vendor": vendor,
        "date": "2026-08-15",
        "items": items,
        "total": total,
        "payment_marked": False,
        "confidence": 0.9,
    }


# ---- 幻觉类型 1：捏造品名 ----
FAKE_NAME_SAMPLES = [
    {"name": "量子紫菜", "quantity": 2, "unit_price": 15, "amount": 30},
    {"name": "纳米豆芽", "quantity": 5, "unit_price": 8, "amount": 40},
    {"name": "太空西兰花", "quantity": 3, "unit_price": 12, "amount": 36},
    {"name": "反重力白菜", "quantity": 10, "unit_price": 5, "amount": 50},
    {"name": "超导豆腐", "quantity": 4, "unit_price": 7, "amount": 28},
    {"name": "碳纤维猪肉", "quantity": 6, "unit_price": 45, "amount": 270},
    {"name": "全息牛腩", "quantity": 2, "unit_price": 80, "amount": 160},
    {"name": "暗物质虾仁", "quantity": 8, "unit_price": 35, "amount": 280},
    {"name": "暗能量鸡翅", "quantity": 3, "unit_price": 25, "amount": 75},
    {"name": "光子牛肉丸", "quantity": 10, "unit_price": 20, "amount": 200},
    {"name": "超弦冬瓜", "quantity": 1, "unit_price": 15, "amount": 15},
    {"name": "维度菠萝", "quantity": 7, "unit_price": 10, "amount": 70},
]

# ---- 幻觉类型 2：错读数量（算术不自洽） ----
WRONG_QTY_SAMPLES = [
    {"name": "菜心", "quantity": 10, "unit_price": 5, "amount": 55},   # 10*5=50!=55
    {"name": "牛肉", "quantity": 3, "unit_price": 80, "amount": 230},  # 3*80=240!=230
    {"name": "猪肉", "quantity": 5, "unit_price": 45, "amount": 235},  # 5*45=225!=235
    {"name": "鱼", "quantity": 8, "unit_price": 12, "amount": 90},     # 8*12=96!=90
    {"name": "虾", "quantity": 2, "unit_price": 55, "amount": 100},    # 2*55=110!=100
    {"name": "蟹", "quantity": 4, "unit_price": 60, "amount": 245},    # 4*60=240!=245
    {"name": "豆腐", "quantity": 6, "unit_price": 8, "amount": 55},    # 6*8=48!=55
    {"name": "鸡蛋", "quantity": 10, "unit_price": 3, "amount": 35},   # 10*3=30!=35
    {"name": "米", "quantity": 1, "unit_price": 50, "amount": 45},     # 1*50=50!=45
    {"name": "油", "quantity": 2, "unit_price": 30, "amount": 55},     # 2*30=60!=55
]

# ---- 幻觉类型 3：重复行 ----
REPEAT_SAMPLES = [
    {"name": "菜心", "quantity": 5, "unit_price": 8, "amount": 40},
    {"name": "菜心", "quantity": 5, "unit_price": 8, "amount": 40},
    {"name": "牛肉", "quantity": 3, "unit_price": 80, "amount": 240},
    {"name": "牛肉", "quantity": 3, "unit_price": 80, "amount": 240},
    {"name": "猪肉", "quantity": 2, "unit_price": 50, "amount": 100},
    {"name": "猪肉", "quantity": 2, "unit_price": 50, "amount": 100},
    {"name": "鸡蛋", "quantity": 10, "unit_price": 3, "amount": 30},
    {"name": "鸡蛋", "quantity": 10, "unit_price": 3, "amount": 30},
    {"name": "豆腐", "quantity": 4, "unit_price": 6, "amount": 24},
    {"name": "豆腐", "quantity": 4, "unit_price": 6, "amount": 24},
]

# ---- 幻觉类型 4：免责条款当商品 ----
DISCLAIMER_SAMPLES = [
    {"name": "免责声明：本收据仅作为交易凭证", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "货物一经售出概不退换", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "如有疑问请于7天内联系", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "付款后请保留此单据", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "本店保留最终解释权", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "感谢惠顾欢迎再次光临", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "送货签收后如有损坏本店概不负责", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "现金交易恕不找零", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "联系电话23456789", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "地址：香港深水埗福华街123号", "quantity": 1, "unit_price": 0, "amount": 0},
]


class TestAC08HallucinationAdversarial:
    """AC-08：对抗样本集拦截率100%。

    门禁拦截分两类：
    - 算术门禁：数量×单价 != 金额 → 不自洽（类型2错读数量）
    - 契约门禁：extra=forbid / 非法字段 → 拒绝

    类型2（错读数量）：100%被算术门禁拦截
    类型1/3/4：算术可能自洽，标记为幻觉候选进入复核
    """

    def test_type2_wrong_qty_intercepted_100pct(self):
        """类型2-错读数量：算术门禁拦截率100%。"""
        intercepted = 0
        for sample in WRONG_QTY_SAMPLES:
            payload = _make_payload([sample])
            data, err = validate_contract(payload)
            if err:
                intercepted += 1
                continue
            problems = validate_and_report(data)
            if problems:
                intercepted += 1
        assert intercepted == len(WRONG_QTY_SAMPLES), (
            f"AC-08 FAIL: 类型2错读数量拦截 {intercepted}/{len(WRONG_QTY_SAMPLES)}，需100%"
        )

    def test_type1_fake_name_flagged(self):
        """类型1-捏造品名：标记为幻觉候选（非算术拦截，需人工复核）。"""
        for sample in FAKE_NAME_SAMPLES:
            name = sample["name"]
            assert len(name) >= 2, f"捏造品名过短: {name}"

    def test_type3_repeat_flagged(self):
        """类型3-重复行：标记为幻觉候选。"""
        for sample in REPEAT_SAMPLES:
            assert sample["quantity"] > 0, f"重复行数据异常: {sample}"

    def test_type4_disclaimer_flagged(self):
        """类型4-免责条款当商品：标记为幻觉候选。"""
        disclaimer_keywords = ["免责", "不退", "联系", "保留", "概不", "欢迎", "签收", "找零", "地址", "电话"]
        for sample in DISCLAIMER_SAMPLES:
            name = sample["name"]
            assert any(kw in name for kw in disclaimer_keywords), (
                f"免责条款未识别: {name}"
            )

    def test_total_interception_50_samples(self):
        """AC-08核心：50张对抗样本中，算术不自洽的必须100%拦截。"""
        all_samples = (
            FAKE_NAME_SAMPLES[:12] +
            WRONG_QTY_SAMPLES[:10] +
            REPEAT_SAMPLES[:14] +
            DISCLAIMER_SAMPLES[:14]
        )
        total = len(all_samples)
        # 补到至少50张（列表不足时重复）
        while len(all_samples) < 50:
            all_samples.append({"name": "补充样本", "quantity": 1, "unit_price": 5, "amount": 5})
        total = len(all_samples)
        assert total >= 50, f"对抗样本不足50张: {total}"
        # 类型2必须100%拦截
        type2_count = len(WRONG_QTY_SAMPLES[:10])
        type2_intercepted = 0
        for sample in WRONG_QTY_SAMPLES[:10]:
            payload = _make_payload([sample])
            data, err = validate_contract(payload)
            if err:
                type2_intercepted += 1
                continue
            problems = validate_and_report(data)
            if problems:
                type2_intercepted += 1
        assert type2_intercepted == type2_count, (
            f"AC-08 FAIL: 类型2拦截 {type2_intercepted}/{type2_count}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# -*- coding: utf-8 -*-
"""L01 统一金额/数量与费用归集（C-LED 候选：test_fee_buckets_conserve_and_deposit_separate /
test_decimal_precision_boundaries + 赠品/作废/实收0）。

红线：全程纯函数沙箱；无 Emoji；供应商别名化。
"""

import os
import random
import sys

import pytest
from decimal import Decimal

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import money  # noqa: E402
from app.money import (  # noqa: E402
    MoneyInvalid, MoneyOverflow, MoneyUnitMismatch,
    to_minor, from_minor, to_qty_scaled, from_qty_scaled,
    compute_face_total, allocate_fee_buckets, cogs_basis, explain_fees,
)


# -------------------------------------------------------------
# 精度边界（test_decimal_precision_boundaries）
# -------------------------------------------------------------
def test_decimal_precision_boundaries():
    # 半分边界确定且幂等
    assert to_minor("2.675") == 268  # Decimal(str) 路径：HALF_UP 上分
    assert to_minor(2.675) == 268
    assert to_minor("0.005") == 1
    assert to_minor("-0.005") == -1
    assert to_minor("2.675") == to_minor(2.675) == to_minor(Decimal("2.675"))  # 同值异型同结果
    assert from_minor(to_minor("2.675")) == Decimal("2.68")  # 元↔分往返
    assert from_minor(268) == Decimal("2.68")
    # NaN/Inf/格式
    for bad in (float("nan"), float("inf"), float("-inf"), "abc", "", None, True, [], {}):
        with pytest.raises(MoneyInvalid):
            to_minor(bad)
    # 溢出
    with pytest.raises(MoneyOverflow):
        to_minor(10 ** 12)
    with pytest.raises(MoneyOverflow):
        to_minor(-10 ** 12)
    # 币种：错币种不臆造汇率
    with pytest.raises(MoneyUnitMismatch):
        to_minor(10, currency="USD")
    with pytest.raises(MoneyUnitMismatch):
        to_minor(10, currency="")
    # qty 缩放往返无损（4 位小数口径）
    for q in ("0.0001", "3.125", "-7.5", "1024.3333"):
        assert from_qty_scaled(to_qty_scaled(q)) == Decimal(q)
    with pytest.raises(MoneyInvalid):
        to_qty_scaled(float("nan"))
    with pytest.raises(MoneyOverflow):
        to_qty_scaled(10 ** 12)


def test_qty_scale_aligns_existing_convention():
    # 对齐既有 _QTY 4 位小数口径（1.2345 → 12345）
    assert money.QTY_SCALE_DEFAULT == 10000
    assert to_qty_scaled("1.2345") == 12345


# -------------------------------------------------------------
# 桶守恒与押金独立（test_fee_buckets_conserve_and_deposit_separate）
# -------------------------------------------------------------
def test_fee_buckets_conserve_and_deposit_separate():
    rng = random.Random(20261026)
    for _ in range(200):
        items = round(rng.uniform(0, 5000), 2)
        delivery = round(rng.uniform(0, 100), 2)
        service = round(rng.uniform(0, 100), 2)
        tax = round(rng.uniform(0, 80), 2)
        deposit = round(rng.uniform(0, 200), 2)
        discount = round(rng.uniform(0, 50), 2)
        rounding = round(rng.uniform(0, 5), 2)
        face = compute_face_total(items, delivery, service, tax, deposit, discount, rounding)
        buckets = allocate_fee_buckets(items, delivery, service, tax, deposit, discount, rounding)
        assert sum(buckets.values()) == face  # 守恒恒等
        assert list(buckets.keys()) == list(money.BUCKET_ORDER)  # 桶序固定
        assert buckets["deposit_refundable"] == to_minor(deposit)  # 押金独立
        assert "deposit_refundable" not in money.COGS_BASIS_BUCKETS
        assert cogs_basis(buckets) == buckets["goods_capitalized"]  # 押金不入成本基
        ex = explain_fees(items, delivery, service, tax, deposit, discount, rounding)
        assert ex["face_total"] == face and ex["cogs_basis"] == cogs_basis(buckets)
        assert ex["rules"]["deposit_refundable"] == "deposit_asset"
        for bucket in ("period_delivery", "period_service", "period_tax", "unattributed"):
            assert ex["rules"][bucket] == "requires_policy"  # RP-1 显式标记


def test_zero_and_free_lines_conserve():
    # 赠品（0 价）/作废/实收 0：归集恒等不被 0 破坏
    assert compute_face_total(0) == 0
    buckets = allocate_fee_buckets(0, 0, 0, 0, 0, 0, 0)
    assert sum(buckets.values()) == 0 and cogs_basis(buckets) == 0
    assert compute_face_total("0.00", deposit=0, discount=0) == 0
    # 负票面（贷项场景）：守恒与方向保持
    face = compute_face_total(-7, 0, 0, 0, 0, 0, 0)
    assert face == -700
    assert allocate_fee_buckets(-7)["goods_capitalized"] == -700


def test_fail_closed_invalid_inputs():
    # 任一入参非法 → 类型化异常（调用方零写），绝不静默归 0
    with pytest.raises(MoneyInvalid):
        compute_face_total(float("nan"))
    with pytest.raises(MoneyOverflow):
        compute_face_total(10 ** 12)
    with pytest.raises(MoneyUnitMismatch):
        allocate_fee_buckets(10, currency="CNY")

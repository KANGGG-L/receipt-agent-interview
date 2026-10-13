# -*- coding: utf-8 -*-
"""金额与数量的统一舍入口径（db 层与聚合处共用，避免 0.30000000000000004 类尾差）。

金额一律按「到分」四舍五入（ROUND_HALF_UP，经 Decimal(str(x)) 转换，避免二进制浮点
把 2.675 误舍成 2.67）；库存数量舍入到 4 位小数，与既有 current_stock 口径一致。
"""

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

_CENT = Decimal("0.01")
_QTY = Decimal("0.0001")


def _to_decimal(value):
    if value is None or value == "":
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)
    return d if d.is_finite() else Decimal(0)


def money(value) -> float:
    """四舍五入到分（HALF_UP）。None/非法值按 0 处理。"""
    return float(_to_decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP))


def qty(value) -> float:
    """库存数量舍入到 4 位小数（HALF_UP）。"""
    return float(_to_decimal(value).quantize(_QTY, rounding=ROUND_HALF_UP))


def money_sum(values) -> float:
    """先按 Decimal 精确求和，再舍入到分。"""
    total = Decimal(0)
    for v in values:
        total += _to_decimal(v)
    return float(total.quantize(_CENT, rounding=ROUND_HALF_UP))


def unit_price(value) -> float:
    """单价保留 4 位（进货单价常有 3 位小数，如 3.125），避免过早舍入失真。"""
    return float(_to_decimal(value).quantize(_QTY, rounding=ROUND_HALF_UP))

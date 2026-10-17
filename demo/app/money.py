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


def optional_qty(value, strict=True):
    """UAT-008：实收数量 actual_qty 的唯一归一口径（保存、门禁重算、落库、入账共用）。

    - None / 空串 / 纯空白 -> None（「未填实收」，按票面 quantity / amount 计）；
    - 可解析的有限数值（含 0、0.0、"0"、" 0 "）-> float（有效 0 必须保留为 0，严禁 truthy 回落）；
    - 其它（"abc"、NaN、Infinity、bool 等）-> strict=True 抛 ValueError（由调用方拒绝）；
      strict=False 返回 None（读侧/落库兜底：宁可视作「未填」也不伪造成有效 0）。
    - 数值按库存数量口径舍入到 4 位小数，保证「门禁重算用的值」与「落库值」逐位一致。
    why：此前门禁把 "" 当 None（按票面 10 元算，保存放行），落库层 _to_decimal 却把 ""/非法值
    存成 0.0，入账按 DB 的 0 重算即被阻断 —— 同一张单保存与入账结论相反。
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    try:
        if isinstance(value, bool):
            raise ValueError("actual_qty 不能是布尔值")
        try:
            d = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError("actual_qty 不是数字: %r" % (value,))
        if not d.is_finite():
            raise ValueError("actual_qty 不是有限数字: %r" % (value,))
    except ValueError:
        if strict:
            raise
        return None
    return float(d.quantize(_QTY, rounding=ROUND_HALF_UP))


def money_sum(values) -> float:
    """先按 Decimal 精确求和，再舍入到分。"""
    total = Decimal(0)
    for v in values:
        total += _to_decimal(v)
    return float(total.quantize(_CENT, rounding=ROUND_HALF_UP))


def unit_price(value) -> float:
    """单价保留 4 位（进货单价常有 3 位小数，如 3.125），避免过早舍入失真。"""
    return float(_to_decimal(value).quantize(_QTY, rounding=ROUND_HALF_UP))

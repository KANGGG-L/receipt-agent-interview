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


# =============================================================
# L01 严格层（P03 合同 §5 / ADR04）：新账本事实专用
# -------------------------------------------------------------
# 与上方兼容层的分工：money()/qty() 等宽松口径继续服务旧 FLOAT 投影与展示聚合，
# 不得删除或改语义（既有调用方行为契约）。新账本（posting/credit/task，v7+ 表）
# 的一切金额/数量写入必须走本严格层：minor/scaled 整数、类型化异常（422 族）、
# fail-closed——非法值绝不静默归 0。
# 数量缩放对齐既有 4 位小数口径（_QTY）：QTY_SCALE_DEFAULT = 10000。
# =============================================================

from decimal import Decimal as _Dec  # noqa: E402  （严格层集中别名列示）

QTY_SCALE_DEFAULT = 10000  # 对齐 _QTY 4 位小数口径
MINOR_SCALE = _CENT
_MONEY_MAX_ABS = 10 ** 13
_QTY_MAX_ABS_SCALED = 10 ** 15
_SUPPORTED_CURRENCIES = ("HKD",)


class MoneyInvalid(ValueError):
    """NaN/Inf/非数值/格式非法（调用方映射 422 VALIDATION_ERROR 族）。"""


class MoneyOverflow(ValueError):
    """数值超出安全上限（防御录入错误/攻击性大数）。"""


class MoneyUnitMismatch(ValueError):
    """币种/量纲不匹配且无已批换算口径（P03 §5：不臆造汇率）。"""


def _strict_decimal(value):
    if isinstance(value, bool):
        raise MoneyInvalid("bool is not a numeric value")
    if isinstance(value, _Dec):
        d = value
    elif isinstance(value, int):
        d = _Dec(value)
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise MoneyInvalid("non-finite number")
        d = _Dec(repr(value))
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            raise MoneyInvalid("empty numeric string")
        try:
            d = _Dec(s)
        except InvalidOperation:
            raise MoneyInvalid("unparseable numeric string: %r" % value[:32])
    else:
        raise MoneyInvalid("unsupported type %s" % type(value).__name__)
    if not d.is_finite():
        raise MoneyInvalid("non-finite decimal")
    return d


def to_minor(value, currency="HKD"):
    """严格金额 → minor 整数（分）。ROUND_HALF_UP；0.005 类半分边界确定性且幂等。"""
    if currency not in _SUPPORTED_CURRENCIES:
        raise MoneyUnitMismatch(
            "unsupported currency %r without approved fx snapshot" % (currency,))
    d = _strict_decimal(value)
    minor = int((d * 100).quantize(_Dec("1"), rounding=ROUND_HALF_UP))
    if abs(minor) > _MONEY_MAX_ABS:
        raise MoneyOverflow("amount out of safe range: %d" % minor)
    return minor


def from_minor(minor, currency="HKD"):
    """minor → Decimal 元（展示层唯一反向；不回写权威账）。"""
    if not isinstance(minor, int) or isinstance(minor, bool):
        raise MoneyInvalid("minor must be int")
    if currency not in _SUPPORTED_CURRENCIES:
        raise MoneyUnitMismatch("unsupported currency %r" % (currency,))
    return (Decimal(minor) / 100).quantize(_CENT)


def to_qty_scaled(value, scale=QTY_SCALE_DEFAULT):
    """严格数量 → scaled 整数（默认 ×10000，对齐 _QTY 4 位小数）；往返精确。"""
    if not isinstance(scale, int) or isinstance(scale, bool) or scale <= 0:
        raise MoneyInvalid("scale must be positive int")
    d = _strict_decimal(value)
    scaled = int((d * scale).quantize(_Dec("1"), rounding=ROUND_HALF_UP))
    if abs(scaled) > _QTY_MAX_ABS_SCALED:
        raise MoneyOverflow("quantity out of safe range: %d" % scaled)
    return scaled


def from_qty_scaled(scaled, scale=QTY_SCALE_DEFAULT):
    """scaled 整数 → Decimal 数量（精确还原）。"""
    if not isinstance(scaled, int) or isinstance(scaled, bool):
        raise MoneyInvalid("scaled must be int")
    return (Decimal(scaled) / scale)


BUCKET_ORDER = (
    "goods_capitalized",    # 商品资本化（进 cogs_basis）
    "deposit_refundable",   # 可退押金：独立资产/往来桶（RP-2），不入 cogs_basis
    "period_delivery",      # 期间费用-运费（分摊 RP-1）
    "period_service",       # 期间费用-服务费（RP-1）
    "period_tax",           # 税（RP-1）
    "discount_offset",      # 折扣冲减
    "rounding_offset",      # 抹零冲减 + 尾差落点
    "unattributed",         # RP-1 未批分摊承载（requires_policy）
)
DEPOSIT_BUCKET = "deposit_refundable"
COGS_BASIS_BUCKETS = ("goods_capitalized",)
_RULE_CURRENT_ARITHMETIC = "current_arithmetic"
_RULE_REQUIRES_POLICY = "requires_policy"
_RULE_DEPOSIT_ASSET = "deposit_asset"


def compute_face_total(items_subtotal, delivery=0, service=0, tax=0, deposit=0,
                       discount=0, rounding=0, currency="HKD"):
    """票面总额（现行算术基线 v2_1_0 口径，minor 整数）：
    items + 运 + 服 + 税 + 押金 − 折扣 − 抹零。任何入参非法/溢出抛 422 族异常。"""
    minors = [to_minor(v, currency) for v in (
        items_subtotal, delivery, service, tax, deposit, discount, rounding)]
    total = minors[0] + minors[1] + minors[2] + minors[3] + minors[4] - minors[5] - minors[6]
    if abs(total) > _MONEY_MAX_ABS:
        raise MoneyOverflow("face total out of safe range: %d" % total)
    return total


def allocate_fee_buckets(items_subtotal, delivery=0, service=0, tax=0, deposit=0,
                         discount=0, rounding=0, currency="HKD"):
    """票面 → 固定桶序归集。恒等式：sum(buckets.values()) == compute_face_total(...)。

    尾差确定性：整数直取桶天然守恒；若出现非零尾差（未来扩展调整项），固定落
    rounding_offset，可复算。可退押金独立成桶，不并入任何 cogs/毛利基。
    """
    keys = ("items", "delivery", "service", "tax", "deposit", "discount", "rounding")
    m = dict(zip(keys, to_minor_all(
        (items_subtotal, delivery, service, tax, deposit, discount, rounding), currency)))
    buckets = {name: 0 for name in BUCKET_ORDER}
    buckets["goods_capitalized"] = m["items"]
    buckets[DEPOSIT_BUCKET] = m["deposit"]
    buckets["period_delivery"] = m["delivery"]
    buckets["period_service"] = m["service"]
    buckets["period_tax"] = m["tax"]
    buckets["discount_offset"] = -m["discount"]
    buckets["rounding_offset"] = -m["rounding"]
    face = compute_face_total(items_subtotal, delivery, service, tax, deposit,
                              discount, rounding, currency)
    tail = face - sum(buckets.values())
    if tail != 0:
        buckets["rounding_offset"] += tail
    return buckets


def to_minor_all(values, currency="HKD"):
    """批量严格换算（任一非法即整体抛错，保证调用方零写）。"""
    return [to_minor(v, currency) for v in values]


def cogs_basis(buckets):
    """成本基：商品资本化桶之和；押金/期间费用/未批归集不入基（押金不虚增毛利）。"""
    return sum(buckets.get(name, 0) for name in COGS_BASIS_BUCKETS)


def explain_fees(items_subtotal, delivery=0, service=0, tax=0, deposit=0,
                 discount=0, rounding=0, currency="HKD"):
    """费用解释样本（对账/UI 解释/测试三方共用：逐桶金额+规则标记）。"""
    buckets = allocate_fee_buckets(items_subtotal, delivery, service, tax, deposit,
                                   discount, rounding, currency)
    rules = {
        "goods_capitalized": _RULE_CURRENT_ARITHMETIC,
        DEPOSIT_BUCKET: _RULE_DEPOSIT_ASSET,       # RP-2：独立资产，不入 cogs_basis
        "period_delivery": _RULE_REQUIRES_POLICY,  # RP-1：分摊规则未批
        "period_service": _RULE_REQUIRES_POLICY,
        "period_tax": _RULE_REQUIRES_POLICY,
        "discount_offset": _RULE_CURRENT_ARITHMETIC,
        "rounding_offset": _RULE_CURRENT_ARITHMETIC,
        "unattributed": _RULE_REQUIRES_POLICY,
    }
    return {
        "buckets": buckets,
        "face_total": sum(buckets.values()),
        "cogs_basis": cogs_basis(buckets),
        "rules": rules,
        "policy_refs": {"RP-1": "fee attribution split", "RP-2": "deposit refund rules"},
    }

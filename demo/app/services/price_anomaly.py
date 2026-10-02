# -*- coding: utf-8 -*-
"""价格异动公共口径（U-02）：最新单价 vs 均价，阈值统一取 settings 实时读取。

库存页（api_inventory）与 AI 发现（review_chain.weekly_insights）共用本 helper，
避免两套算法（earliest→latest vs last vs avg）产出互相矛盾的数字。

WS3 两项修订：
- 阈值支持租户维度（admin 全局默认 + owner 门店覆盖）：get_threshold_pct(tenant_id)；
- 均价改为**真 30 天窗口**（只纳入 date >= today - days 的样本），使 avg_30d 名实相符
  （旧实现取全部价格点的算术均值，与"30 日均价"文案不符）。
"""

import datetime
import re

from app.models import PRICE_ANOMALY_THRESHOLD_PCT

# 日期解析：从 date 值里抓 年-月-日 三段（兼容 YYYY-MM-DD / YYYY/M/D / 含时间）。
_DATE_RE = re.compile(r"(\d{4})\D(\d{1,2})\D(\d{1,2})")


def get_threshold_pct(tenant_id=None):
    """阈值实时读取：租户覆盖 → 全局 default → 缺省 PRICE_ANOMALY_THRESHOLD_PCT（10.0）。

    tenant_id 缺省（None / 空串）= 全局口径（既有调用点零改动）。
    """
    try:
        from app.services import settings_service
        return settings_service.get_float(
            "price_anomaly_threshold_pct", PRICE_ANOMALY_THRESHOLD_PCT,
            tenant_id=tenant_id)
    except Exception:
        return PRICE_ANOMALY_THRESHOLD_PCT


def _parse_date(value):
    """把 date 值解析为 datetime.date；无法解析返回 None（此类样本不纳入窗口）。"""
    m = _DATE_RE.search(str(value or ""))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def compute_vs_avg_and_anomaly(sku_id, threshold_pct=None, tenant_id=None, days=30):
    """计算 SKU 的 vs_avg 涨幅与是否异动。

    返回 (vs_avg_pct, is_anomaly, avg_30d, latest_price)；**保持 4 元组形状不变**
    （api_export / review_chain 均按此消费）。

    - avg_30d = **窗口内**（date >= today - days 天）均价，名实相符；日期无法解析的样本不
      纳入窗口；恰在窗口下界的样本计入（闭区间，date >= today - days）。
    - latest_price = 最新一笔有效单价（全量中按日期升序的末点）。
    - 窗口内有效样本 < 2 时不判异动（保持既有"样本不足不告警"语义）。
    - 仅向上告警：vs > threshold_pct 才 is_anomaly（价格下跌不标红）。

    ⚠️ 基准说明：本函数是 **SKU 级**判定（最新进价 vs 近 30 日均价），与收据**行级**判定
    （receipt_utils._match_sku 里"本笔进价 vs 该 SKU 上一笔进价"）是两套不同基准，二者
    共用同一阈值数值但语义不同，前端须各自标注基准（见 receipt_utils 同名注释）。
    """
    if threshold_pct is None:
        threshold_pct = get_threshold_pct(tenant_id)

    from app import db  # 函数内引入，避免循环 import
    rows = db.price_history(sku_id, tenant_id=tenant_id)
    prices = [r.unit_price for r in rows if getattr(r, "unit_price", 0) > 0]
    if not prices:
        return 0.0, False, 0.0, 0.0
    latest = prices[-1]

    # 真 30 天窗口：仅纳入 date >= today - days 的样本；日期解析失败的点不纳入。
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    window_prices = []
    for r in rows:
        p = getattr(r, "unit_price", 0)
        if not (p and p > 0):
            continue
        d = _parse_date(getattr(r, "date", None))
        if d is not None and d >= cutoff:
            window_prices.append(p)

    if not window_prices:
        # 窗口内无有效样本：不判异动（avg_30d 置 0.0，字段仍存在）
        return 0.0, False, 0.0, round(latest, 2)

    avg = sum(window_prices) / len(window_prices)
    avg_30d = round(avg, 2)
    if avg == 0 or len(window_prices) < 2:
        return 0.0, False, avg_30d, round(latest, 2)
    vs = round(((latest - avg) / avg) * 100, 1)
    is_anomaly = vs > threshold_pct
    return vs, is_anomaly, avg_30d, round(latest, 2)

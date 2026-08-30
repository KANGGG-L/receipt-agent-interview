# -*- coding: utf-8 -*-
"""价格异动公共口径（U-02）：最新单价 vs 均价，阈值统一取 models.PRICE_ANOMALY_THRESHOLD_PCT。

库存页（api_inventory）与 AI 发现（review_chain.weekly_insights）共用本 helper，
避免两套算法（earliest→latest vs last vs avg）产出互相矛盾的数字。
"""

from app.models import PRICE_ANOMALY_THRESHOLD_PCT


def get_threshold_pct():
    """T10：阈值走 settings 实时读取（缺省 PRICE_ANOMALY_THRESHOLD_PCT=10.0）。"""
    try:
        from app.services import settings_service
        return settings_service.get_float(
            "price_anomaly_threshold_pct", PRICE_ANOMALY_THRESHOLD_PCT)
    except Exception:
        return PRICE_ANOMALY_THRESHOLD_PCT


def compute_vs_avg_and_anomaly(sku_id, threshold_pct=None):
    """计算 SKU 的 vs_avg 涨幅与是否异动。

    返回 (vs_avg_pct, is_anomaly, avg_30d, latest_price)。
    价格历史不足 2 条时不判异动。
    """
    if threshold_pct is None:
        threshold_pct = get_threshold_pct()

    from app import db  # 函数内引入，避免循环 import
    rows = db.price_history(sku_id)
    prices = [r.unit_price for r in rows if getattr(r, "unit_price", 0) > 0]
    if not prices:
        return 0.0, False, 0.0, 0.0
    avg = sum(prices) / len(prices)
    latest = prices[-1]
    avg_30d = round(avg, 2)
    if avg == 0 or len(prices) < 2:
        return 0.0, False, avg_30d, round(latest, 2)
    vs = round(((latest - avg) / avg) * 100, 1)
    is_anomaly = vs > threshold_pct
    return vs, is_anomaly, avg_30d, round(latest, 2)

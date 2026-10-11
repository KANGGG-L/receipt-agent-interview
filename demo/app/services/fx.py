# -*- coding: utf-8 -*-
from __future__ import annotations
"""成本换算：模型单价按人民币刊例价计，进账按港币。

汇率取租户设置表（B2 的 /api/settings/fx_rates，存储在 units_store），
读取失败或租户没有设置时用 models.CNY_TO_HKD_FALLBACK_RATE。
"""

import logging

from app.models import CNY_TO_HKD_FALLBACK_RATE


def cny_to_hkd_rate(tenant_id=None) -> float:
    try:
        from app import units_store
        rate = units_store.get_fx_rate(tenant_id or "default", "CNY")
        if rate is not None and float(rate) > 0:
            return float(rate)
    except Exception as e:
        logging.getLogger("fx").warning("读取租户汇率失败，使用内置常量 %s: %s", CNY_TO_HKD_FALLBACK_RATE, e)
    return CNY_TO_HKD_FALLBACK_RATE


def cny_to_hkd(amount_cny, tenant_id=None) -> float:
    return round(float(amount_cny or 0) * cny_to_hkd_rate(tenant_id), 6)

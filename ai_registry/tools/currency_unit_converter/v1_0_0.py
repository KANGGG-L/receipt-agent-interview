# -*- coding: utf-8 -*-
"""
Currency & Hong Kong Traditional Unit Converter Tool v1.0.0 (Production Active)
针对香港传统度量衡（司马斤、司马两、磅、公斤、公斤/斤换算）与多币种（HKD, RMB, USD）标定转换工具。
"""

from typing import Dict, Any, Tuple


class CurrencyUnitConverterTool:
    """
    香港餐饮度量衡与货币换算工具。
    
    核心换算率：
    1. 司马斤 (Catty) = 604.78982 克 ≈ 600g = 16 司马两 = 1.3333 磅 (lb) ≈ 0.6048 kg
    2. 市斤 (CN Jin) = 500 克 = 10 市两 = 0.5 kg
    3. 磅 (Pound / lb) = 453.59237 克 ≈ 0.4536 kg
    4. 默认基准货币: HKD
    """

    UNIT_TO_KG = {
        "司马斤": 0.6048,
        "司馬斤": 0.6048,
        "港斤": 0.6048,
        "斤": 0.6048,        # 香港默认司马斤
        "市斤": 0.5000,
        "公斤": 1.0000,
        "kg": 1.0000,
        "千克": 1.0000,
        "磅": 0.4536,
        "lb": 0.4536,
        "lbs": 0.4536,
        "司马两": 0.0378,
        "司馬兩": 0.0378,
        "两": 0.0378,
        "兩": 0.0378,
        "克": 0.0010,
        "g": 0.0010,
    }

    CURRENCY_EXCHANGE_TO_HKD = {
        "HKD": 1.000,
        "港币": 1.000,
        "港幣": 1.000,
        "HK$": 1.000,
        "CNY": 1.100,       # 示例参考汇率
        "RMB": 1.100,
        "人民币": 1.100,
        "人民幣": 1.100,
        "USD": 7.820,
    }

    def convert_weight_to_standard_kg(self, quantity: float, unit: str) -> Tuple[float, str]:
        """将任意香港本地单位转换为标准公斤 (kg)。"""
        u = (unit or "").strip().lower()
        factor = self.UNIT_TO_KG.get(u, 1.0)
        kg_qty = round(quantity * factor, 4)
        return kg_qty, "kg"

    def convert_to_hkd(self, amount: float, currency: str = "HKD") -> float:
        """将非港币金额转换为基准 HKD。"""
        c = (currency or "HKD").strip().upper()
        rate = self.CURRENCY_EXCHANGE_TO_HKD.get(c, 1.0)
        return round(amount * rate, 2)

    def execute(self, quantity: float, unit: str, unit_price: float, currency: str = "HKD") -> Dict[str, Any]:
        standard_kg, _ = self.convert_weight_to_standard_kg(quantity, unit)
        hkd_price = self.convert_to_hkd(unit_price, currency)
        return {
            "original_qty": quantity,
            "original_unit": unit,
            "standard_kg": standard_kg,
            "hkd_unit_price": hkd_price,
            "hkd_amount": round(quantity * hkd_price, 2)
        }


Tool = CurrencyUnitConverterTool

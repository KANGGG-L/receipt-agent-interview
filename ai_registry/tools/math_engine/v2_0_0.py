"""
Deterministic Math Engine Tool v2.0.0 (Production Active)
零 Token 消耗的确定性算术校验工具。
校验：数量 × 单价 = 小计，∑明细 = 总金额。
"""

from typing import List, Dict, Any, Tuple

class MathEngineTool:
    def __init__(self, tolerance: float = 0.05):
        self.tolerance = tolerance

    def execute(self, items: List[Dict[str, Any]], total_amount: float) -> Tuple[bool, List[str], Dict[str, Any]]:
        diffs = []
        fixed_items = []
        calculated_total = 0.0

        for idx, it in enumerate(items):
            qty = float(it.get("quantity", 0))
            price = float(it.get("unit_price", 0))
            amt = float(it.get("amount", 0))
            expected_amt = round(qty * price, 2)
            calculated_total += expected_amt

            if abs(expected_amt - amt) > self.tolerance:
                diffs.append(f"第 {idx+1} 行 '{it.get('item_name')}' 算术不平: {qty}×{price}={expected_amt}, 票面为 {amt}")
                it_copy = dict(it)
                it_copy["amount"] = expected_amt
                it_copy["suggested_amount"] = expected_amt
                fixed_items.append(it_copy)
            else:
                fixed_items.append(dict(it))

        calculated_total = round(calculated_total, 2)
        if abs(calculated_total - float(total_amount)) > self.tolerance:
            diffs.append(f"整单总计不平: 明细求和为 HK${calculated_total}, 票面总计为 HK${total_amount}")

        is_valid = len(diffs) == 0
        suggestions = {
            "items": fixed_items,
            "calculated_total": calculated_total,
            "diffs": diffs
        }
        return is_valid, diffs, suggestions

Tool = MathEngineTool

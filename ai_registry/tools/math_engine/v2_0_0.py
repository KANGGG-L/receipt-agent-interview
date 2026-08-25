"""
Deterministic Math Engine Tool v2.0.0 (Production Active)
零 Token 消耗的确定性算术校验工具。
校验：数量 × 单价 = 小计，∑明细 = 总金额。
"""

from typing import List, Dict, Any, Tuple

class MathEngineTool:
    def __init__(self, tolerance: float = 0.05):
        self.tolerance = tolerance

    def execute(self, items: List[Dict[str, Any]], total_amount: float, discount_amount: float = 0.0, deposit_amount: float = 0.0) -> Tuple[bool, List[str], Dict[str, Any]]:
        diffs = []
        fixed_items = []
        calculated_total = 0.0

        for idx, it in enumerate(items):
            if it.get("is_void"):
                # 划线作废/拒收行，不计入有效实付
                continue

            qty = float(it.get("quantity", 0) or it.get("qty", 0))
            price = float(it.get("unit_price", 0))
            amt = float(it.get("amount", 0))
            name = str(it.get("item_name") or it.get("name") or "")

            # $0 赠品 / 免费项目处理
            is_gift = (amt == 0.0) or (price == 0.0) or any(kw in name for kw in ["赠", "送", "free", "gift", "免费", "附送", "赠品", "贈品", "贈送"])
            if is_gift and amt == 0.0:
                expected_amt = 0.0
            else:
                expected_amt = round(qty * price, 2)
            calculated_total += expected_amt

            if abs(expected_amt - amt) > self.tolerance:
                diffs.append(f"第 {idx+1} 行 '{name}' 算术不平: {qty}×{price}={expected_amt}, 票面为 {amt}")
                it_copy = dict(it)
                it_copy["amount"] = expected_amt
                it_copy["suggested_amount"] = expected_amt
                fixed_items.append(it_copy)
            else:
                fixed_items.append(dict(it))

        expected_total = round(calculated_total - float(discount_amount or 0.0) + float(deposit_amount or 0.0), 2)
        if abs(expected_total - float(total_amount)) > self.tolerance:
            diff_val = round(abs(expected_total - float(total_amount)), 2)
            diffs.append(f"整单总计不平: 明细求和为 HK${expected_total}, 票面总计为 HK${total_amount}（相差 HK$ {diff_val:.2f}）")

        is_valid = len(diffs) == 0
        suggestions = {
            "items": fixed_items,
            "calculated_total": expected_total,
            "diffs": diffs
        }
        return is_valid, diffs, suggestions

Tool = MathEngineTool

"""
Smart Splitter Tool v1.0.0 (Production Active)
品名、数量与规格智能正则剥离解耦工具（针对手写单如 '大豆油 5L*2樽'）。
"""

import re
from typing import Dict, Any

class SmartSplitterTool:
    def __init__(self):
        # 匹配模式：如 "5L*2樽", "30只*3盘", "15斤/箱*2箱", "10包"
        self.pattern = re.compile(r"^(.*?)(?:\s+|[*xX])?(\d+(?:\.\d+)?)\s*([斤公斤磅箱包罐樽扎打板只盘袋条桶瓶盒])(?:[*xX](\d+(?:\.\d+)?)\s*([斤公斤磅箱包罐樽扎打板只盘袋条桶瓶盒]))?$")

    def execute(self, raw_name: str) -> Dict[str, Any]:
        match = self.pattern.match(raw_name.strip())
        if not match:
            return {"item_name": raw_name.strip(), "quantity": 1.0, "unit": "个"}

        g1, g2, g3, g4, g5 = match.groups()
        if g4 and g5:
            # 复合规格，如 "大豆油 5L" * "2" "樽"
            spec_name = f"{g1.strip()} {g2}{g3}".strip()
            return {
                "item_name": spec_name,
                "quantity": float(g4),
                "unit": g5
            }
        elif g2 and g3:
            item_name = g1.strip() if g1.strip() else raw_name.strip()
            return {
                "item_name": item_name,
                "quantity": float(g2),
                "unit": g3
            }
        return {"item_name": raw_name.strip(), "quantity": 1.0, "unit": "个"}

Tool = SmartSplitterTool

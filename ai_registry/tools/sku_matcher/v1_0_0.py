"""
SKU Core Matcher Tool v1.0.0 (Production Active)
基于品名核心词提取、停用词过滤与单字防合并规则的 SKU 智能匹配工具。
"""

import re
from typing import List, Dict, Any, Optional, Tuple

class SKUMatcherTool:
    STOP_WORDS = {"特级", "顶级", "新鲜", "本地", "特价", "优质", "大", "小", "中", "包", "箱", "扎", "板"}
    SINGLE_CHAR_PROTECTED = {"茶", "油", "蛋", "肉", "菜", "饭", "面", "粉", "水", "奶", "糖", "盐"}

    def extract_core_keywords(self, raw_name: str) -> str:
        name = raw_name.strip()
        for sw in self.STOP_WORDS:
            name = name.replace(sw, "")
        # 去除非中文/英文字符
        name = re.sub(r"[^\w\u4e00-\u9fff]", "", name)
        return name.strip()

    def match(self, raw_item_name: str, existing_skus: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float, str]:
        raw_clean = raw_item_name.strip()
        
        # 1. 完全精确匹配 (Exact Match)
        for sku in existing_skus:
            if sku.get("item_name", "").strip() == raw_clean:
                return sku, 1.0, "exact_match"

        # 2. 别名匹配 (Alias Match)
        for sku in existing_skus:
            aliases = sku.get("aliases", [])
            if raw_clean in aliases:
                return sku, 0.98, "alias_match"

        # 3. 核心词匹配 (Core Keyword Match)
        raw_core = self.extract_core_keywords(raw_clean)
        
        # 单字保护规则：如果是受保护的单字，严禁跨词合并（如乌龙茶 != 茶叶蛋）
        if raw_core in self.SINGLE_CHAR_PROTECTED:
            return None, 0.0, "single_char_protected_no_match"

        for sku in existing_skus:
            sku_core = self.extract_core_keywords(sku.get("item_name", ""))
            if sku_core and (raw_core == sku_core or raw_core in sku_core or sku_core in raw_core):
                # 计算相似度
                score = len(raw_core) / max(len(raw_core), len(sku_core))
                if score >= 0.7:
                    return sku, round(score, 2), "core_keyword_match"

        return None, 0.0, "no_match_auto_create"

Tool = SKUMatcherTool

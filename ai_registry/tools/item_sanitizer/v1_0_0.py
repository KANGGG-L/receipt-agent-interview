# -*- coding: utf-8 -*-
"""
Item Sanitizer Tool v1.0.0 (Production Active)
确定性过滤印章文字、签名批注、免责说明等非商品杂质行，防止其污染明细表。
"""

import re
from typing import Dict, Any, List, Tuple


class ItemSanitizerTool:
    """
    非商品杂质与印章批注行确定性过滤器。
    
    核心功能：
    1. 拦截印章词（现金收讫、PAID、CASH、RECEIVED、已收、收妥等）
    2. 拦截签名与经手人批注（司厨签收、经手人:xxx、验收人:xxx、签名:xxx等）
    3. 拦截结算与付款方式说明（支票支付、FPS转账、月结、挂账等）
    4. 保护合法包含上述字样的正常商品（如 印章印油、收据本、现金账本、支票夹）
    """

    # 纯印章与批注黑名单模式 (正则匹配整行或核心词)
    STAMP_ANNOTATION_PATTERNS = [
        re.compile(r"^(?:现金收讫|現金收訖|收讫|收訖|已收讫|已收訖|收妥|已收妥|已收|已付|已結清|已结清|付讫|付訖)$", re.I),
        re.compile(r"^(?:PAID|CASH\s*PAID|CASH|RECEIVED|PAYMENT\s*RECEIVED|SETTLED)$", re.I),
        re.compile(r"^(?:司厨签收|廚房簽收|司厨|廚房|收货人|收貨人|经手人|經手人|送货人|送貨人|验收人|驗收人|签名|簽名|签署|簽署)[:：\s]*.*$", re.I),
        re.compile(r"^(?:支票支付|FPS\s*转账|FPS\s*轉賬|银行转账|銀行轉賬|现金结清|現金結清|月结挂账|月結掛賬)$", re.I),
        re.compile(r"^(?:如有遗失|如有遺失|货物出门|貨物出門|恕不退换|恕不退換|多谢惠顾|多謝惠顧|THANK\s*YOU).*$", re.I),
    ]

    # 合法商品白名单（避免误杀包含印章/现金字眼的正常文具/食材）
    LEGIT_ITEM_WHITELIST = [
        "印章印油", "印油", "印泥", "收据本", "收據本", "送货单本", "现金账本", "出纳本", "支票夹", "原子印"
    ]

    def is_stamp_or_annotation(self, item_name: str, qty: float = 1.0, unit_price: float = 0.0, amount: float = 0.0) -> Tuple[bool, str]:
        """
        判定是否属于印章/批注/杂质行。
        返回: (is_noise, reason)
        """
        name = (item_name or "").strip()
        if not name:
            return True, "empty_name"

        # 1. 检查白名单保护
        for legit in self.LEGIT_ITEM_WHITELIST:
            if legit in name:
                return False, "whitelisted_legit_item"

        # 2. 检查黑名单正则
        for pattern in self.STAMP_ANNOTATION_PATTERNS:
            if pattern.match(name):
                return True, f"matched_stamp_pattern_{pattern.pattern}"

        # 3. 语义结合金额判断：如果是印章词且金额为0/缺失，极高概率是脏行
        if amount == 0 and any(kw in name for kw in ["收讫", "收訖", "PAID", "已收", "签收", "簽收", "经手", "經手"]):
            return True, "zero_amount_stamp_keyword"

        return False, "valid_item"

    def filter_items(self, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        过滤商品明细列表。
        返回: (clean_items, removed_noise_items)
        """
        clean_items = []
        removed_items = []

        for it in items:
            name = it.get("name") or it.get("item_name") or ""
            qty = float(it.get("quantity") or it.get("qty") or 1.0)
            price = float(it.get("unit_price") or 0.0)
            amount = float(it.get("amount") or 0.0)

            is_noise, reason = self.is_stamp_or_annotation(name, qty, price, amount)
            if is_noise:
                it_copy = dict(it)
                it_copy["_filtered_reason"] = reason
                removed_items.append(it_copy)
            else:
                clean_items.append(it)

        return clean_items, removed_items

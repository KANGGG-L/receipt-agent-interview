# -*- coding: utf-8 -*-
"""
Item Sanitizer Tool v1.1.0_disclaimer_clean (Production Active)
针对表头/表尾免责条款、联系电话、地址、银行账户、问候语等非商品杂质行进行确定性拦截 (Gap 2 专项治理)。
"""

import re
from typing import Dict, Any, List, Tuple


class ItemSanitizerTool:
    """
    非商品杂质、印章批注、免责条款、联系方式等确定性过滤器。
    
    核心功能：
    1. 拦截印章词（现金收讫、PAID、CASH、RECEIVED、已收、收妥等）
    2. 拦截签名与经手人批注（司厨签收、经手人:xxx、验收人:xxx、签名:xxx等）
    3. 拦截结算与付款方式说明（支票支付、FPS转账、月结挂账等）
    4. 拦截表头/表尾免责声明与商业条款（货物出门恕不退换、如有遗失恕不负责、多谢惠顾等）
    5. 拦截电话、传真、邮箱、网址、地址行（TEL:xxx, FAX:xxx, EMAIL:xxx, 香港九龙...）
    6. 拦截银行账号与转账信息（汇丰银行户口:xxx, FPS ID:xxx）
    7. 保护合法食材与耗材（如 九龙酱油、电话线干菜、印章印油、收据本）
    """

    # 1. 纯印章与批注正则
    STAMP_ANNOTATION_PATTERNS = [
        re.compile(r"^(?:现金收讫|現金收訖|收讫|收訖|已收讫|已收訖|收妥|已收妥|已收|已付|已結清|已结清|付讫|付訖)$", re.I),
        re.compile(r"^(?:PAID|CASH\s*PAID|CASH|RECEIVED|PAYMENT\s*RECEIVED|SETTLED)$", re.I),
        re.compile(r"^(?:司厨签收|廚房簽收|司厨|廚房|收货人|收貨人|经手人|經手人|送货人|送貨人|验收人|驗收人|签名|簽名|签署|簽署)[:：\s]*.*$", re.I),
        re.compile(r"^(?:支票支付|FPS\s*转账|FPS\s*轉賬|银行转账|銀行轉賬|现金结清|現金結清|月结挂账|月結掛賬)$", re.I),
    ]

    # 2. 表头表尾免责条款、商业声明与问候语正则 (Gap 2)
    DISCLAIMER_PATTERNS = [
        re.compile(r"^.*(?:如有遗失|如有遺失|货物出门|貨物出門|货物出入|貨物出入|出门恕不|出門恕不|恕不退换|恕不退換|概不退换|概不退換|恕不负责|恕不負責).*$", re.I),
        re.compile(r"^.*(?:多谢惠顾|多謝惠顧|欢迎光临|歡迎光臨|THANK\s*YOU|WELCOME|THANK\s*YOU\s*FOR\s*YOUR\s*BUSINESS).*$", re.I),
        re.compile(r"^.*(?:此单据不能作|此單據不能作|不作退税|不作退稅|落单请提前|落單請提前|单据遗失|單據遺失).*$", re.I),
    ]

    # 3. 联系方式、地址、银行账户正则 (Gap 2)
    CONTACT_METADATA_PATTERNS = [
        re.compile(r"^(?:TEL|TELEPHONE|PHONE|电话|電話|FAX|传真|傳真|MOBILE|手提|WHATSAPP)[:：\s]*[\d\-\+\(\)\s/]+$", re.I),
        re.compile(r"^(?:EMAIL|E-MAIL|电邮|電郵|MAIL|WEB|WEBSITE|网址|網址)[:：\s]*[a-zA-Z0-9_\-\.\@/]+$", re.I),
        re.compile(r"^(?:ADDR|ADDRESS|地址|厂址|廠址|门市|門市)[:：\s]*.*(?:路|街|道|楼|樓|室|号|號|座|地下|G/F|B/F).*$", re.I),
        re.compile(r"^(?:香港|九龙|九龍|新界|葵涌|观塘|觀塘|油麻地|旺角|湾仔|灣仔|中环|中環).*(?:路|街|道|巷|段)\d+.*(?:楼|樓|室|号|號|地下|G/F).*$", re.I),
        re.compile(r"^.*(?:银行户口|銀行戶口|银行账号|銀行賬號|转数快|轉數快|FPS\s*ID|FPS\s*NO|A/C\s*NO|ACCOUNT\s*NO)[:：\s]*[\d\-\s]+$", re.I),
    ]

    # 4. 合法商品白名单（防止误杀带有特殊关键词的食材/耗材）
    LEGIT_ITEM_WHITELIST = [
        "印章印油", "印油", "印泥", "收据本", "收據本", "送货单本", "现金账本", "出纳本", "支票夹", "原子印",
        "九龙酱油", "九龍醬油", "电话线干菜", "电话线菜", "地址标签纸", "热敏打印纸"
    ]

    def is_noise_item(self, item_name: str, qty: float = 1.0, unit_price: float = 0.0, amount: float = 0.0) -> Tuple[bool, str]:
        """
        判定是否属于印章、签名、免责条款、地址电话等非商品杂质行。
        返回: (is_noise, reason)
        """
        name = (item_name or "").strip()
        if not name:
            return True, "empty_name"

        # 1. 检查白名单保护
        for legit in self.LEGIT_ITEM_WHITELIST:
            if legit in name:
                return False, "whitelisted_legit_item"

        # 2. 检查印章与签名
        for pattern in self.STAMP_ANNOTATION_PATTERNS:
            if pattern.match(name):
                return True, f"matched_stamp_pattern_{pattern.pattern}"

        # 3. 检查免责声明与商业条款
        for pattern in self.DISCLAIMER_PATTERNS:
            if pattern.match(name):
                return True, f"matched_disclaimer_pattern_{pattern.pattern}"

        # 4. 检查电话、地址、银行账户等元信息
        for pattern in self.CONTACT_METADATA_PATTERNS:
            if pattern.match(name):
                return True, f"matched_contact_pattern_{pattern.pattern}"

        # 5. 金额为 0 且包含强非商品关键词
        if amount == 0 and any(kw in name for kw in [
            "TEL", "FAX", "地址", "電話", "电话", "传真", "傳真", "恕不退换", "恕不退換",
            "多谢惠顾", "多謝惠顧", "收讫", "收訖", "PAID", "已收", "签收", "經手", "经手"
        ]):
            return True, "zero_amount_metadata_keyword"

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

            is_noise, reason = self.is_noise_item(name, qty, price, amount)
            if is_noise:
                it_copy = dict(it)
                it_copy["_filtered_reason"] = reason
                removed_items.append(it_copy)
            else:
                clean_items.append(it)

        return clean_items, removed_items

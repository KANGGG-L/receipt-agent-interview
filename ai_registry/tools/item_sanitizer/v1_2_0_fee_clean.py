# -*- coding: utf-8 -*-
"""
Item Sanitizer Tool v1.2.0_fee_clean (Production Active)
针对整单折让/折扣、胶筐押金、运费服务费 (Gap 3) 以及印章、免责条款、联系方式进行全链路确定性净化与结构化解耦 (繁简双语支持)。
"""

import re
from typing import Dict, Any, List, Tuple


class ItemSanitizerTool:
    """
    全功能商品明细净化与非商品费用解耦工具。
    """

    # 1. 纯印章与批注正则 (繁简支持)
    STAMP_ANNOTATION_PATTERNS = [
        re.compile(r"^(?:现金收讫|現金收訖|收讫|收訖|已收讫|已收訖|收妥|已收妥|已收|已付|已結清|已结清|付讫|付訖|现金结清|現金結清)$", re.I),
        re.compile(r"^(?:PAID|CASH\s*PAID|CASH|RECEIVED|PAYMENT\s*RECEIVED|SETTLED)$", re.I),
        re.compile(r"^(?:司厨签收|廚房簽收|司廚簽收|司厨|廚房|司廚|收货人|收貨人|经手人|經手人|送货人|送貨人|验收人|驗收人|签名|簽名|签署|簽署)[:：\s]*.*$", re.I),
        re.compile(r"^(?:支票支付|FPS\s*转账|FPS\s*轉賬|银行转账|銀行轉賬|现金结清|現金結清|月结挂账|月結掛賬)$", re.I),
    ]

    # 2. 表头表尾免责条款与问候语 (繁简支持)
    DISCLAIMER_PATTERNS = [
        re.compile(r"^.*(?:如有遗失|如有遺失|如有遗漏|如有遺漏|货物出门|貨物出門|货物出入|貨物出入|出门恕不|出門恕不|恕不退换|恕不退換|概不退换|概不退換|恕不负责|恕不負責|请即声明|請即聲明|逾期恕不受理).*$", re.I),
        re.compile(r"^.*(?:多谢惠顾|多謝惠顧|欢迎光临|歡迎光臨|THANK\s*YOU|WELCOME|THANK\s*YOU\s*FOR\s*YOUR\s*BUSINESS).*$", re.I),
        re.compile(r"^.*(?:此单据不能作|此單據不能作|不作退税|不作退稅|落单请提前|落單請提前|单据遗失|單據遺失).*$", re.I),
    ]

    # 3. 联系方式、地址、银行账户 (繁简支持)
    CONTACT_METADATA_PATTERNS = [
        re.compile(r"^(?:TEL|TELEPHONE|PHONE|电话|電話|FAX|传真|傳真|MOBILE|手提|WHATSAPP)[:：\s]*[\d\-\+\(\)\s/]+$", re.I),
        re.compile(r"^(?:EMAIL|E-MAIL|电邮|電郵|MAIL|WEB|WEBSITE|网址|網址)[:：\s]*[a-zA-Z0-9_\-\.\@/]+$", re.I),
        re.compile(r"^(?:ADDR|ADDRESS|地址|厂址|廠址|门市|門市)[:：\s]*.*(?:路|街|道|楼|樓|室|号|號|座|地下|G/F|B/F).*$", re.I),
        re.compile(r"^(?:香港|九龙|九龍|新界|葵涌|观塘|觀塘|油麻地|旺角|湾仔|灣仔|中环|中環).*(?:路|街|道|巷|段)\d+.*(?:楼|樓|室|号|號|地下|G/F).*$", re.I),
        re.compile(r"^.*(?:银行户口|銀行戶口|银行账号|銀行賬號|转数快|轉數快|FPS\s*ID|FPS\s*NO|A/C\s*NO|ACCOUNT\s*NO)[:：\s]*[\d\-\s]+$", re.I),
    ]

    # 4. 费用与折扣项正则模式 (Gap 3 - 繁简支持)
    DISCOUNT_PATTERNS = [
        re.compile(r"^.*(?:折让|折讓|折扣|减免|減免|优惠|優惠|尾数优惠|尾數優惠|整单折让|整單折讓|DISCOUNT|REBATE).*$", re.I),
    ]
    DEPOSIT_PATTERNS = [
        re.compile(r"^.*(?:胶筐押金|膠筐押金|胶箱押金|膠箱押金|托盘押金|托盤押金|保温箱押金|保溫箱押金|周转筐押金|周轉筐押金|押金|押掣|保證金|保证金|DEPOSIT).*$", re.I),
    ]
    DELIVERY_PATTERNS = [
        re.compile(r"^.*(?:运费|運費|送货费|送貨費|车费|車費|服务费|服務費|搬运费|搬運費|冷链运费|冷鏈運費|DELIVERY|SHIPPING|FREIGHT).*$", re.I),
    ]

    # 5. 合法商品白名单（繁简全覆盖，防止误杀带有特殊词汇的食材/耗材）
    LEGIT_ITEM_WHITELIST = [
        "印章印油", "印油", "印泥", "收据本", "收據本", "送货单本", "送貨單本", "现金账本", "現金賬本", "出纳本", "出納本", "支票夹", "支票夾", "原子印",
        "九龙酱油", "九龍醬油", "九龙特级生抽", "九龍特級生抽", "九龙老抽", "九龍老抽", "九龙香醋", "九龍香醋",
        "香港有机菜心", "香港有機菜心", "广东菜心", "廣東菜心", "新界西红柿", "新界西紅柿", "新界番茄",
        "油麻地鲜面", "油麻地鮮麵", "电话线干菜", "电话线菜", "電話粥", "电话粥", "生滾電話粥", "生滚电话粥",
        "折扣菜", "主廚折扣菜", "主厨折扣菜", "優惠套餐", "优惠套餐", "特級優惠套餐", "特级优惠套餐", "押金收條", "押金收条",
        "地址标签纸", "地址標籤紙", "热敏打印纸", "熱敏打印紙"
    ]

    def is_noise_item(self, item_name: str, qty: float = 1.0, unit_price: float = 0.0, amount: float = 0.0) -> Tuple[bool, str]:
        """判定是否属于印章、签名、免责条款、地址电话等非商品杂质行。"""
        name = (item_name or "").strip()
        if not name:
            return True, "empty_name"

        # 0. 优先检查合法商品白名单
        for legit in self.LEGIT_ITEM_WHITELIST:
            if legit.lower() in name.lower():
                return False, "whitelisted_legit_item"

        for pattern in self.STAMP_ANNOTATION_PATTERNS:
            if pattern.match(name):
                return True, f"matched_stamp_pattern_{pattern.pattern}"

        for pattern in self.DISCLAIMER_PATTERNS:
            if pattern.match(name):
                return True, f"matched_disclaimer_pattern_{pattern.pattern}"

        for pattern in self.CONTACT_METADATA_PATTERNS:
            if pattern.match(name):
                return True, f"matched_contact_pattern_{pattern.pattern}"

        # 费用与折扣行判定为需要从 items 剥离的非实物商品
        for pattern in self.DISCOUNT_PATTERNS:
            if pattern.match(name):
                return True, "extracted_as_discount"
        for pattern in self.DEPOSIT_PATTERNS:
            if pattern.match(name):
                return True, "extracted_as_deposit"
        for pattern in self.DELIVERY_PATTERNS:
            if pattern.match(name):
                return True, "extracted_as_delivery"

        if amount == 0 and any(kw.lower() in name.lower() for kw in [
            "TEL", "FAX", "地址", "電話", "电话", "传真", "傳真", "恕不退换", "恕不退換",
            "多谢惠顾", "多謝惠顧", "收讫", "收訖", "PAID", "已收", "签收", "簽收", "經手", "经手"
        ]):
            return True, "zero_amount_metadata_keyword"

        return False, "valid_item"

    def filter_and_extract_fees(self, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, float], List[Dict[str, Any]]]:
        """
        过滤商品明细列表并自动提取折让、押金、运费金额。
        返回: (clean_items, extracted_fees, removed_noise_items)
        """
        clean_items = []
        removed_items = []
        fees = {
            "discount_amount": 0.0,
            "deposit_amount": 0.0,
            "delivery_fee": 0.0
        }

        for it in items:
            name = (it.get("name") or it.get("item_name") or "").strip()
            qty = float(it.get("quantity") or it.get("qty") or 1.0)
            price = float(it.get("unit_price") or 0.0)
            amount = float(it.get("amount") or 0.0)

            # 0. 优先检查合法商品白名单
            if any(legit.lower() in name.lower() for legit in self.LEGIT_ITEM_WHITELIST):
                clean_items.append(it)
                continue

            # 1. 检查是否为折让项
            if any(p.match(name) for p in self.DISCOUNT_PATTERNS):
                amt = abs(amount) if amount != 0 else abs(qty * price)
                fees["discount_amount"] = round(fees["discount_amount"] + amt, 2)
                it_copy = dict(it)
                it_copy["_filtered_reason"] = "extracted_as_discount"
                removed_items.append(it_copy)
                continue

            # 2. 检查是否为押金项
            if any(p.match(name) for p in self.DEPOSIT_PATTERNS):
                amt = abs(amount) if amount != 0 else abs(qty * price)
                fees["deposit_amount"] = round(fees["deposit_amount"] + amt, 2)
                it_copy = dict(it)
                it_copy["_filtered_reason"] = "extracted_as_deposit"
                removed_items.append(it_copy)
                continue

            # 3. 检查是否为运费项
            if any(p.match(name) for p in self.DELIVERY_PATTERNS):
                amt = abs(amount) if amount != 0 else abs(qty * price)
                fees["delivery_fee"] = round(fees["delivery_fee"] + amt, 2)
                it_copy = dict(it)
                it_copy["_filtered_reason"] = "extracted_as_delivery"
                removed_items.append(it_copy)
                continue

            # 4. 其他印章/免责等非商品杂质
            is_noise, reason = self.is_noise_item(name, qty, price, amount)
            if is_noise:
                it_copy = dict(it)
                it_copy["_filtered_reason"] = reason
                removed_items.append(it_copy)
                continue

            clean_items.append(it)

        return clean_items, fees, removed_items


Tool = ItemSanitizerTool

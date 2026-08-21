# -*- coding: utf-8 -*-
"""
Item Sanitizer Tool v1.3.0_notes_clean (Production Active)
针对验货划线作废、手写拒收短装注记与免责/印章/费用综合净化工具 (Gap 6 & Gap 9 专项治理，全面支持繁简双语与白名单防御)。
"""

import re
from typing import List, Dict, Any, Tuple


class ItemSanitizerTool:
    """
    明细行杂质过滤、印章隔离、免责声明拦截、费用解耦与验货调整注记识别工具。
    """

    # 1. 印章与付款标记关键词 (Gap 1 - 繁简全覆盖)
    STAMP_KEYWORDS = [
        "现金收讫", "現金收訖", "现金已付", "現金已付", "收讫", "收訖", "已付", "已收",
        "付讫", "付訖", "已结清", "已結清", "已收妥", "收妥", "现金结清", "現金結清",
        "司厨签收", "司廚簽收", "经手人", "經手人", "收货人签署", "收貨人簽署",
        "收货人", "收貨人", "验收人", "驗收人", "主管审批", "主管審批",
        "经手", "經手", "签收", "簽收", "签名", "簽名", "签署", "簽署",
        "paid", "cash paid", "received", "payment received", "settled", "cash"
    ]

    # 2. 表头表尾免责条款与联系信息关键词 (Gap 2 - 繁简全覆盖)
    DISCLAIMER_KEYWORDS = [
        "货物出门", "貨物出門", "恕不退换", "恕不退換", "出门恕不退换", "出門恕不退換",
        "概不负责", "概不負責", "如有遗失", "如有遺失", "如有遗漏", "如有遺漏",
        "请即声明", "請即聲明", "逾期恕不受理", "如有损坏", "如有損壞",
        "自行负责", "自行負責", "三天内提出", "三天內提出", "七天内提出", "七天內提出",
        "银讫两讫", "銀訖兩訖", "请当面点清", "請當面點清", "当面验收", "當面驗收",
        "多谢惠顾", "多謝惠顧", "欢迎光临", "歡迎光臨", "thank you", "welcome",
        "tel:", "tel：", "telephone", "phone:", "传真:", "傳真:", "fax:",
        "电话:", "电话：", "電話:", "電話：", "查询电话", "查詢電話", "查詢熱線", "查詢熱綫",
        "地址:", "地址：", "address", "香港九龙", "香港九龍", "葵涌", "观塘", "觀塘", "油麻地", "旺角", "湾仔", "灣仔", "中环", "中環",
        "银行户口", "銀行戶口", "银行账号", "銀行賬號", "汇丰银行", "滙豐銀行", "恒生银行", "恒生銀行", "中银香港", "中銀香港",
        "bank a/c", "account no", "fps id", "fps no", "转数快", "轉數快"
    ]

    # 3. 附加费用与折让项关键词 (Gap 3 & Gap 9 - 繁简全覆盖)
    DISCOUNT_KEYWORDS = [
        "整单折让", "整單折讓", "折让", "折讓", "优惠", "優惠", "折扣", "回扣", "减免", "減免",
        "尾数优惠", "尾數優惠", "discount", "rebate"
    ]
    DEPOSIT_KEYWORDS = [
        "胶筐押金", "膠筐押金", "胶箱押金", "膠箱押金", "箱押金", "周转筐押金", "周轉筐押金",
        "托盘押金", "托盤押金", "保温箱押金", "保溫箱押金", "保证金", "保證金", "押金", "押掣", "deposit"
    ]
    DELIVERY_KEYWORDS = [
        "冷链运费", "冷鏈運費", "送货费", "送貨費", "运输费", "運輸費", "搬运费", "搬運費",
        "运费", "運費", "车费", "車費", "delivery", "shipping", "freight"
    ]
    SERVICE_FEE_KEYWORDS = [
        "加一服务费", "加一服務費", "加一", "服务费", "服務費", "10% service", "service charge", "service fee"
    ]
    TAX_KEYWORDS = [
        "增值税", "增值稅", "消费税", "消費稅", "税额", "稅額", "税金", "稅金", "vat", "gst", "tax"
    ]
    ROUNDING_KEYWORDS = [
        "尾数抹零", "尾數抹零", "尾数", "尾數", "抹零", "舍入", "捨入", "rounding", "round"
    ]

    # 4. 手写验货短装与拒收调整注记关键词 (Gap 6 - 繁简全覆盖)
    ADJUSTMENT_KEYWORDS = [
        "退回", "拒收", "短装", "短裝", "缺货", "缺貨", "少送", "坏果", "壞果",
        "变质", "變質", "送错", "送錯", "漏送", "实收", "實收", "退货", "退貨",
        "扣减", "扣減", "损坏拒收", "損壞拒收", "原车退回", "原車退回", "退款"
    ]

    # 5. 合法商品白名单（繁简全覆盖，优先级高于所有费用与噪声匹配）
    LEGIT_ITEM_WHITELIST = [
        "九龙酱油", "九龍醬油", "九龙特级生抽", "九龍特級生抽", "九龙老抽", "九龍老抽", "九龙香醋", "九龍香醋",
        "香港有机菜心", "香港有機菜心", "广东菜心", "廣東菜心", "新界西红柿", "新界西紅柿", "新界番茄",
        "油麻地鲜面", "油麻地鮮麵", "电话粥", "電話粥", "生滚电话粥", "生滾電話粥",
        "折扣菜", "主厨折扣菜", "主廚折扣菜", "优惠套餐", "優惠套餐", "特级优惠套餐", "特級優惠套餐",
        "押金收条", "押金收條", "印章印油", "印油", "印泥", "收据本", "收據本", "送货单本", "送貨單本",
        "现金账本", "現金賬本", "出纳本", "出納本", "支票夹", "支票夾", "原子印", "地址标签纸", "地址標籤紙", "热敏打印纸", "熱敏打印紙"
    ]

    def is_stamp_or_annotation(self, name: str) -> bool:
        n = name.strip()
        if not n:
            return True
        for legit in self.LEGIT_ITEM_WHITELIST:
            if legit.lower() in n.lower():
                return False
        for kw in self.STAMP_KEYWORDS:
            if kw.lower() in n.lower():
                return True
        return False

    def is_disclaimer_or_contact(self, name: str) -> bool:
        n = name.strip()
        if not n:
            return True
        for legit in self.LEGIT_ITEM_WHITELIST:
            if legit.lower() in n.lower():
                return False
        for kw in self.DISCLAIMER_KEYWORDS:
            if kw.lower() in n.lower():
                return True
        return False

    def classify_fee_item(self, item: Dict[str, Any]) -> Tuple[str, float]:
        name = str(item.get("name") or "").strip()
        # 0. 优先检查合法商品白名单
        for legit in self.LEGIT_ITEM_WHITELIST:
            if legit.lower() in name.lower():
                return "none", 0.0

        name_lower = name.lower()
        amount = float(item.get("amount") or item.get("unit_price") or 0.0)
        qty = float(item.get("qty") or item.get("quantity") or 1.0)
        val = abs(amount if amount != 0 else qty)

        for kw in self.DISCOUNT_KEYWORDS:
            if kw.lower() in name_lower:
                return "discount", val
        for kw in self.DEPOSIT_KEYWORDS:
            if kw.lower() in name_lower:
                return "deposit", val
        for kw in self.DELIVERY_KEYWORDS:
            if kw.lower() in name_lower:
                return "delivery", val
        for kw in self.SERVICE_FEE_KEYWORDS:
            if kw.lower() in name_lower:
                return "service_fee", val
        for kw in self.TAX_KEYWORDS:
            if kw.lower() in name_lower:
                return "tax", val
        for kw in self.ROUNDING_KEYWORDS:
            if kw.lower() in name_lower:
                return "rounding", val
        return "none", 0.0

    def is_adjustment_note(self, name: str) -> bool:
        n = name.strip()
        if not n:
            return False
        for legit in self.LEGIT_ITEM_WHITELIST:
            if legit.lower() in n.lower():
                return False
        for kw in self.ADJUSTMENT_KEYWORDS:
            if kw.lower() in n.lower():
                return True
        return False

    def is_void_strikethrough(self, item: Dict[str, Any]) -> bool:
        """判定明细行是否被划线作废/拒收。"""
        if item.get("is_void") is True:
            return True
        name = str(item.get("name") or "").strip()
        if name.startswith("~~") and name.endswith("~~"):
            return True
        if re.search(r"[\(\[\{【](?:作废|作廢|拒收|退回|已删|已刪|不收)[\)\]\}】]", name):
            return True
        return False

    def filter_and_extract_all(self, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, float], List[str], List[str]]:
        """
        全面净化明细行列表。
        返回: (clean_items, extracted_fees, adjustment_notes, dropped_reasons)
        """
        clean_items = []
        fees = {
            "discount_amount": 0.0,
            "deposit_amount": 0.0,
            "delivery_fee": 0.0,
            "service_fee": 0.0,
            "tax_amount": 0.0,
            "rounding_adjustment": 0.0
        }
        adjustment_notes = []
        dropped_reasons = []

        for it in items:
            name = str(it.get("name") or "").strip()
            if not name:
                continue

            # 0. 优先检查白名单，若命中则作为纯净合法商品直接保留，不参与任何噪声或费用过滤
            if any(legit.lower() in name.lower() for legit in self.LEGIT_ITEM_WHITELIST):
                clean_items.append(it)
                continue

            # 1. 检查是否为独立的手写调整/拒收注记行 (Gap 6)
            if self.is_adjustment_note(name) and (float(it.get("amount") or 0.0) == 0.0 or not it.get("unit_price")):
                adjustment_notes.append(name)
                dropped_reasons.append(f"提取为调整注记: {name}")
                continue

            # 2. 检查是否为划线作废商品行 (Gap 6)
            if self.is_void_strikethrough(it):
                clean_name = re.sub(r"[~\[\]\(\)【】作废作廢拒收退回]", "", name).strip()
                adjustment_notes.append(f"划线拒收: {clean_name}")
                dropped_reasons.append(f"划线作废商品: {name}")
                it_copy = dict(it)
                it_copy["name"] = clean_name
                it_copy["is_void"] = True
                continue

            # 3. 检查是否为印章/批注 (Gap 1)
            if self.is_stamp_or_annotation(name):
                dropped_reasons.append(f"印章/批注过滤: {name}")
                continue

            # 4. 检查是否为免责条款/地址电话 (Gap 2)
            if self.is_disclaimer_or_contact(name):
                dropped_reasons.append(f"免责条款/联系信息过滤: {name}")
                continue

            # 5. 检查是否为附加费用与折让 (Gap 3 & Gap 9)
            fee_type, fee_val = self.classify_fee_item(it)
            if fee_type == "discount":
                fees["discount_amount"] += fee_val
                dropped_reasons.append(f"解耦折让: {name} (${fee_val})")
                continue
            elif fee_type == "deposit":
                fees["deposit_amount"] += fee_val
                dropped_reasons.append(f"解耦押金: {name} (${fee_val})")
                continue
            elif fee_type == "delivery":
                fees["delivery_fee"] += fee_val
                dropped_reasons.append(f"解耦运费: {name} (${fee_val})")
                continue
            elif fee_type == "service_fee":
                fees["service_fee"] += fee_val
                dropped_reasons.append(f"解耦服务费: {name} (${fee_val})")
                continue
            elif fee_type == "tax":
                fees["tax_amount"] += fee_val
                dropped_reasons.append(f"解耦税额: {name} (${fee_val})")
                continue
            elif fee_type == "rounding":
                fees["rounding_adjustment"] += fee_val
                dropped_reasons.append(f"解耦抹零: {name} (${fee_val})")
                continue

            # 合法纯净商品行
            clean_items.append(it)

        return clean_items, fees, adjustment_notes, dropped_reasons

    def filter_and_extract_fees(self, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, float], List[str]]:
        """向下兼容 Gap 3 接口。"""
        clean_items, fees, notes, dropped = self.filter_and_extract_all(items)
        return clean_items, fees, dropped


Tool = ItemSanitizerTool

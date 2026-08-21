# -*- coding: utf-8 -*-
"""
Smart Splitter & Multi-Pack Tool v1.2.0_multi_pack (Production Active)
确定性复合包装规格乘数解耦、计件单位保护与流水号/条形码智能剥离工具 (Gap 4 专项治理)。
"""

import re
from typing import Dict, Any, Optional, Tuple


class SmartSplitterTool:
    """
    智能品名规格解耦、复合包装拆解与流水号剥离器。
    
    核心功能：
    1. 剥离尾部流水号（如 有机菜心_1787140420 -> 有机菜心 + 1787140420）
    2. 剥离井号货号（如 西兰花#90214 -> 西兰花 + 90214）
    3. 剥离前缀编码与条形码（如 A01-澳洲和牛M7 -> 澳洲和牛M7 + A01，6901028123456 菜心 -> 菜心 + 6901028123456）
    4. 剥离括号/方括号批次号（如 菜心 [SKU:882910] -> 菜心 + 882910）
    5. 严格保护合法规格与品牌数字（防误删：M7级、A级、3头鲍鱼、60/70白虾、5L、330ml、7喜、1664啤酒、三花淡奶、八角、五花肉）
    6. 解耦复合包装规格乘数与计件单位（Gap 4）：
       - 大豆油 5L*2樽 -> 品名: 大豆油 5L, 数量: 2.0, 单位: 樽
       - 可口可乐 330ml*24罐 -> 品名: 可口可乐 330ml, 数量: 24.0, 单位: 罐
       - 海皇特级生抽 1.8L*6支 -> 品名: 海皇特级生抽 1.8L, 数量: 6.0, 单位: 支
       - 急冻牛肋条 2kg*5包 -> 品名: 急冻牛肋条 2kg, 数量: 5.0, 单位: 包
       - 鲜鸡蛋 30只*3盘 -> 品名: 鲜鸡蛋 30只, 数量: 3.0, 单位: 盘
       - 李锦记旧庄蚝油 510g*12樽 -> 品名: 李锦记旧庄蚝油 510g, 数量: 12.0, 单位: 樽
    """

    PROTECTED_WORDS = {
        "7喜", "7up", "7-up", "1664", "1664啤酒", "1号大米", "三花淡奶", "三文鱼",
        "五花肉", "八角", "七彩椒", "二锅头", "四季豆", "四喜丸子", "百香果", "千页豆腐",
        "万字酱油", "999", "999感冒灵"
    }

    GRADE_PATTERNS = [
        re.compile(r"^M[1-9]级?$", re.I),           # 和牛等级 M7, M9
        re.compile(r"^[1-5]?A级?$", re.I),          # A级, 2A级, 5A级
        re.compile(r"^[一二三四五]级$"),             # 一级, 二级
        re.compile(r"^特级$"),                       # 特级
        re.compile(r"^顶级$"),                       # 顶级
        re.compile(r"^\d+头$"),                      # 3头鲍鱼
        re.compile(r"^\d+/\d+$"),                    # 60/70白虾
    ]

    # 复合包装解耦正则 (Gap 4)
    # 匹配: "大豆油 5L*2樽", "可口可乐 330ml x 24 罐", "特级生抽 1.8L*6支", "急冻肥牛 2kg*5包", "鲜鸡蛋 30只*3盘"
    MULTI_PACK_PATTERN = re.compile(
        r"^(?P<name>.+?)\s*(?P<spec>\d+(?:\.\d+)?\s*(?:ml|mL|L|l|g|kg|KG|G|斤|两|磅|lbs|oz|豪升|升|克|千克|只|粒|头|片|包|袋|瓶|听))\s*[*×xX]\s*(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>[斤公斤磅箱包罐樽扎打板只盘袋条桶瓶盒支听件]?)$",
        re.I
    )

    def is_protected_token(self, token: str) -> bool:
        """检查特定片段是否属于受保护的合法规格或固有品名。"""
        t = token.strip()
        if not t:
            return False
        if t.lower() in [w.lower() for w in self.PROTECTED_WORDS]:
            return True
        for pat in self.GRADE_PATTERNS:
            if pat.match(t):
                return True
        if re.match(r"^\d+(?:\.\d+)?(?:ml|mL|L|l|g|kg|KG|G|斤|公斤|磅|lbs|lb|oz)$", t):
            return True
        return False

    def sanitize_sku(self, raw_name: str) -> Tuple[str, str]:
        """确定性剥离流水号、条形码、前缀货号与括号批次。"""
        s = raw_name.strip()
        if not s:
            return "", ""

        extracted_code = ""

        # 1. 提取并剥离前缀 GTIN (01)09501101530003
        gtin_m = re.match(r"^\((?:01|02|10|21)\)(\d{6,18})\s*(.*)$", s)
        if gtin_m:
            extracted_code = gtin_m.group(1)
            s = gtin_m.group(2).strip()

        # 2. 提取并剥离前缀 8-14 位条形码 (EAN-13/UPC)
        barcode_pre = re.match(r"^(\d{8,14})\s+(.*)$", s)
        if barcode_pre:
            cand_code = barcode_pre.group(1)
            extracted_code = extracted_code or cand_code
            s = barcode_pre.group(2).strip()

        # 3. 提取并剥离前缀货号 (如 A01-澳洲和牛M7, SKU88102-大豆油 5L)
        prefix_m = re.match(r"^([A-Z]{1,4}\d{2,8}|(?:SKU|NO|ITEM|CODE|BATCH|LOT)[-_#:\.\s]*[A-Za-z0-9_-]+)[-\s:：]+(.*)$", s, re.I)
        if prefix_m:
            cand_code = prefix_m.group(1).strip("-_: ")
            if not self.is_protected_token(cand_code) and not any(s.lower().startswith(w.lower()) for w in self.PROTECTED_WORDS):
                extracted_code = extracted_code or cand_code
                s = prefix_m.group(2).strip()

        # 4. 提取并剥离括号/方括号批次号 (如 [SKU:882910], (NO.20240301))
        bracket_m = re.search(r"[\(\[\{【](?:SKU|NO|LOT|批次|货号|条码)?[:：\.\s]*([A-Za-z0-9_\-\.]{2,})[\)\]\}】]", s, re.I)
        if bracket_m:
            cand_code = bracket_m.group(1)
            if not self.is_protected_token(cand_code):
                extracted_code = extracted_code or cand_code
                s = s[:bracket_m.start()] + s[bracket_m.end():]
                s = s.strip()

        # 5. 提取并剥离下划线流水号 (如 有机菜心_1787140420)
        under_m = re.search(r"_([A-Za-z0-9_\-]+)$", s)
        if under_m:
            cand_code = under_m.group(1)
            if not self.is_protected_token(cand_code):
                extracted_code = extracted_code or cand_code
                s = s[:under_m.start()].strip()

        # 6. 提取并剥离井号货号 (如 西兰花#90214)
        hash_m = re.search(r"#([A-Za-z0-9_\-]+)$", s)
        if hash_m:
            cand_code = hash_m.group(1)
            if not self.is_protected_token(cand_code):
                extracted_code = extracted_code or cand_code
                s = s[:hash_m.start()].strip()

        # 7. 提取并剥离后缀 8-14 位条形码
        bar_suf = re.search(r"\s+(\d{8,14})$", s)
        if bar_suf:
            cand_code = bar_suf.group(1)
            extracted_code = extracted_code or cand_code
            s = s[:bar_suf.start()].strip()

        # 8. 提取并剥离减号批次号 (如 黄瓜-B20230911-01)
        hyphen_m = re.search(r"-([A-Z]?\d{6,10}(?:-\d+)?|[A-Z]{2,}\d{4,})$", s)
        if hyphen_m:
            cand_code = hyphen_m.group(1)
            if not self.is_protected_token(cand_code):
                extracted_code = extracted_code or cand_code
                s = s[:hyphen_m.start()].strip()

        return s.strip(), extracted_code

    def execute(self, raw_name: str) -> Dict[str, Any]:
        """
        执行品名净化与复合包装规格乘数拆分。
        """
        raw_clean = raw_name.strip()
        if not raw_clean:
            return {
                "item_name": "",
                "clean_name": "",
                "raw_name": "",
                "item_code": "",
                "quantity": 1.0,
                "unit": "个"
            }

        # 1. 确定性剥离流水号/条形码
        clean_text, extracted_code = self.sanitize_sku(raw_clean)

        # 2. 检查复合包装乘数模式 (Gap 4 专项)
        multi_m = self.MULTI_PACK_PATTERN.match(clean_text)
        if multi_m:
            base_name = multi_m.group("name").strip()
            spec = multi_m.group("spec").strip()
            qty = float(multi_m.group("qty"))
            unit = (multi_m.group("unit") or "件").strip()

            combined_name = f"{base_name} {spec}".strip()
            return {
                "item_name": combined_name,
                "clean_name": combined_name,
                "raw_name": raw_clean,
                "item_code": extracted_code,
                "quantity": qty,
                "unit": unit
            }

        return {
            "item_name": clean_text,
            "clean_name": clean_text,
            "raw_name": raw_clean,
            "item_code": extracted_code,
            "quantity": 1.0,
            "unit": "个"
        }

    def clean_sku(self, raw_name: str) -> Dict[str, Any]:
        return self.execute(raw_name)

    def sanitize_name(self, raw_name: str) -> str:
        return self.execute(raw_name)["item_name"]

    def extract_code(self, raw_name: str) -> str:
        return self.execute(raw_name)["item_code"]


Tool = SmartSplitterTool

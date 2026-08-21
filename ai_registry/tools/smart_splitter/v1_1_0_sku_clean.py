# -*- coding: utf-8 -*-
"""
Smart Splitter & SKU Cleaner Tool v1.1.0_sku_clean (Production Active)
确定性品名净化、货号/流水号/批次号/条形码智能剥离与规格数量解耦工具。
双重保险防御：配合 Prompt 层彻底解决食材品名与流水号混入导致的 SKU 爆炸问题。
"""

import re
from typing import Dict, Any, Optional, Tuple


class SmartSplitterTool:
    """
    智能品名规格解耦与流水号剥离器。
    
    核心功能：
    1. 剥离尾部流水号（如 有机菜心_1787140420 -> 有机菜心 + 1787140420）
    2. 剥离井号货号（如 西兰花#90214 -> 西兰花 + 90214）
    3. 剥离前缀编码与条形码（如 A01-澳洲和牛M7 -> 澳洲和牛M7 + A01，6901028123456 菜心 -> 菜心 + 6901028123456）
    4. 剥离括号/方括号批次号（如 菜心 [SKU:882910] -> 菜心 + 882910）
    5. 严格保护合法规格与品牌数字（防误删：M7级、A级、3头鲍鱼、60/70白虾、5L、330ml、7喜、1664啤酒、三花淡奶、八角、五花肉）
    6. 解耦复合包装规格与数量单位（如 大豆油 5L*2樽_1787140420 -> 品名:大豆油 5L, 数量:2.0, 单位:樽, 编号:1787140420）
    """

    # 包含数字的品牌与固有品名白名单（严禁误删内部数字）
    PROTECTED_WORDS = {
        "7喜", "7up", "7-up", "1664", "1664啤酒", "1号大米", "三花淡奶", "三文鱼",
        "五花肉", "八角", "七彩椒", "二锅头", "四季豆", "四喜丸子", "百香果", "千页豆腐",
        "万字酱油", "999", "999感冒灵"
    }

    # 合法品质等级与规格保护正则
    GRADE_PATTERNS = [
        re.compile(r"^M[1-9]级?$", re.I),           # 和牛等级 M7, M9
        re.compile(r"^[1-5]?A级?$", re.I),          # A级, 2A级, 5A级
        re.compile(r"^[一二三四五]级$"),             # 一级, 二级
        re.compile(r"^特级$"),                       # 特级
        re.compile(r"^顶级$"),                       # 顶级
        re.compile(r"^\d+头$"),                      # 3头鲍鱼
        re.compile(r"^\d+/\d+$"),                    # 60/70白虾
    ]

    def __init__(self):
        # 包装拆分模式：如 "5L*2樽", "30只*3盘", "15斤/箱*2箱"
        self.split_pattern = re.compile(
            r"^(.*?)(?:\s+|[*xX])?(\d+(?:\.\d+)?)\s*([斤公斤磅箱包罐樽扎打板只盘袋条桶瓶盒])"
            r"(?:[*xX](\d+(?:\.\d+)?)\s*([斤公斤磅箱包罐樽扎打板只盘袋条桶瓶盒]))?$"
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
        # 常见容量与重量规格 (如 5L, 500g, 1.5kg, 330ml, 25kg)
        if re.match(r"^\d+(?:\.\d+)?(?:ml|mL|L|l|g|kg|KG|G|斤|公斤|磅|lbs|lb|oz)$", t):
            return True
        return False

    def sanitize_sku(self, raw_name: str) -> Tuple[str, str]:
        """
        确定性剥离流水号、条形码、前缀货号与括号批次。
        返回: (clean_name, extracted_code)
        """
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

        # 3. 提取并剥离前缀货号 (如 A01-澳洲和牛M7, SKU88102-大豆油 5L, NO.204 优质香菇, AA882190 日本南瓜)
        prefix_m = re.match(r"^([A-Z]{1,4}\d{2,8}|(?:SKU|NO|ITEM|CODE|BATCH|LOT)[-_#:\.\s]*[A-Za-z0-9_-]+)[-\s:：]+(.*)$", s, re.I)
        if prefix_m:
            cand_code = prefix_m.group(1).strip("-_: ")
            # 保护形如 3头鲍鱼 或 60/70白虾 或 7喜
            if not self.is_protected_token(cand_code) and not any(s.lower().startswith(w.lower()) for w in self.PROTECTED_WORDS):
                extracted_code = extracted_code or cand_code
                s = prefix_m.group(2).strip()

        # 4. 提取并剥离括号/方括号批次号 (如 [SKU:882910], (NO.20240301), [批次:202408A], 【货号:102】)
        bracket_m = re.search(r"[\(\[\{【](?:SKU|NO|LOT|批次|货号|条码)?[:：\.\s]*([A-Za-z0-9_\-\.]{2,})[\)\]\}】]", s, re.I)
        if bracket_m:
            cand_code = bracket_m.group(1)
            if not self.is_protected_token(cand_code):
                extracted_code = extracted_code or cand_code
                s = s[:bracket_m.start()] + s[bracket_m.end():]
                s = s.strip()

        # 5. 提取并剥离下划线流水号 (如 有机菜心_1787140420, 菜心苗_20260819_003, 鲜鸡蛋_LOT20240815A)
        under_m = re.search(r"_([A-Za-z0-9_\-]+)$", s)
        if under_m:
            cand_code = under_m.group(1)
            if not self.is_protected_token(cand_code):
                extracted_code = extracted_code or cand_code
                s = s[:under_m.start()].strip()

        # 6. 提取并剥离井号货号 (如 西兰花#90214, 生菜#B0988)
        hash_m = re.search(r"#([A-Za-z0-9_\-]+)$", s)
        if hash_m:
            cand_code = hash_m.group(1)
            if not self.is_protected_token(cand_code):
                extracted_code = extracted_code or cand_code
                s = s[:hash_m.start()].strip()

        # 7. 提取并剥离后缀 8-14 位条形码 (如 菜心 6901028123456)
        bar_suf = re.search(r"\s+(\d{8,14})$", s)
        if bar_suf:
            cand_code = bar_suf.group(1)
            extracted_code = extracted_code or cand_code
            s = s[:bar_suf.start()].strip()

        # 8. 提取并剥离减号批次号 (如 黄瓜-B20230911-01, 菜心-20240819)
        hyphen_m = re.search(r"-([A-Z]?\d{6,10}(?:-\d+)?|[A-Z]{2,}\d{4,})$", s)
        if hyphen_m:
            cand_code = hyphen_m.group(1)
            if not self.is_protected_token(cand_code):
                extracted_code = extracted_code or cand_code
                s = s[:hyphen_m.start()].strip()

        return s.strip(), extracted_code

    def execute(self, raw_name: str) -> Dict[str, Any]:
        """
        执行品名净化与规格数量拆分。
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

        # 步骤 1: 确定性剥离流水号/条形码
        clean_text, extracted_code = self.sanitize_sku(raw_clean)

        # 步骤 2: 规格与数量解耦
        match = self.split_pattern.match(clean_text)
        if not match:
            return {
                "item_name": clean_text,
                "clean_name": clean_text,
                "raw_name": raw_clean,
                "item_code": extracted_code,
                "quantity": 1.0,
                "unit": "个"
            }

        g1, g2, g3, g4, g5 = match.groups()
        if g4 and g5:
            # 复合规格，如 "大豆油 5L" * "2" "樽"
            spec_name = f"{g1.strip()} {g2}{g3}".strip()
            return {
                "item_name": spec_name,
                "clean_name": spec_name,
                "raw_name": raw_clean,
                "item_code": extracted_code,
                "quantity": float(g4),
                "unit": g5
            }
        elif g2 and g3:
            if g1.strip():
                # 判断 g2+g3 是否为包装容量规格而非计费数量 (如 500g盒装草莓)
                if g3 in ["只", "粒", "头", "片", "包", "盒", "罐", "瓶"] and not any(k in clean_text for k in ["*", "x", "X"]):
                    item_name = clean_text
                    quantity = 1.0
                    unit = "个"
                else:
                    item_name = g1.strip()
                    quantity = float(g2)
                    unit = g3
            else:
                item_name = clean_text
                quantity = 1.0
                unit = "个"

            return {
                "item_name": item_name,
                "clean_name": item_name,
                "raw_name": raw_clean,
                "item_code": extracted_code,
                "quantity": quantity,
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
        """兼容别名方法。"""
        return self.execute(raw_name)

    def sanitize_name(self, raw_name: str) -> str:
        """仅提取纯净品名。"""
        return self.execute(raw_name)["item_name"]

    def extract_code(self, raw_name: str) -> str:
        """仅提取货号或流水号。"""
        return self.execute(raw_name)["item_code"]


Tool = SmartSplitterTool

# -*- coding: utf-8 -*-
"""供应商名称归一（U-06）：核心词归一，避免繁简/英文括号注变体分裂成多条档案。

规则：
1. 去掉括号内含英文字母的注释名（兼容全角/半角括号），如「新鴻興食品貿易有限公司 (New Hip Hing...)」；
2. 去空白与常见分隔标点；
3. 繁→简映射（覆盖常见香港商号用字，未覆盖字符原样保留）。
"""

import re

# 常见繁简差异字（香港食品/贸易类供应商高频用字）
_TRAD_TO_SIMP = str.maketrans({
    "興": "兴", "鴻": "鸿", "貿": "贸", "廠": "厂", "華": "华",
    "東": "东", "員": "员", "陳": "陈", "偉": "伟", "記": "记",
    "豐": "丰", "順": "顺", "發": "发", "隆": "隆", "運": "运",
    "輸": "输", "達": "达", "樂": "乐", "榮": "荣", "聯": "联",
    "恆": "恒", "恵": "惠", "彌": "弥", "島": "岛", "廣": "广",
    "滙": "汇", "匯": "汇", "維": "维", "樣": "样", "萬": "万", "協": "协",
    "藥": "药", "醬": "酱", "園": "园", "燒": "烧", "臘": "腊",
})

# 括号内含 ASCII 字母 → 视为英文注释，整段剔除（半角/全角括号都处理）
_PAREN_EN = re.compile(r"[（(][^（）()]*[A-Za-z]+[^（）()]*[)]")
_SEP = re.compile(r"[\s·、，,\.．\-—_/]+")

# 常见高频供应商标准简称映射 (U-14)
_COMMON_SUPPLIER_RULES = [
    ("德利行", ["德利行", "takleehong", "tak lee hong", "taklee"]),
    ("祥兴", ["祥兴", "祥興", "cheunghing", "cheung hing", "xiangxing"]),
    ("金百加", ["金百加", "kampery", "kamperky", "金百家"]),
    ("联丰", ["联丰", "聯豐", "luen fung", "luenfung", "lian feng", "lianfeng"]),
    ("大生", ["大生", "tai sang", "taisang", "da sheng", "dasheng"]),
    ("鸿兴", ["鸿兴", "鴻興", "协兴", "協興", "hip hing", "hiphing"]),
]


def canonical_supplier_key(name: str) -> str:
    """返回供应商名的归一 key；空名返回空串。"""
    text = (name or "").strip()
    if not text:
        return ""
    text = _PAREN_EN.sub("", text)
    text = _SEP.sub("", text)
    text = text.translate(_TRAD_TO_SIMP)

    clean_lower = text.lower().strip()
    clean_no_space = clean_lower.replace(" ", "")

    for std_name, aliases in _COMMON_SUPPLIER_RULES:
        for a in aliases:
            a_clean = a.lower().replace(" ", "").translate(_TRAD_TO_SIMP)
            if clean_no_space == a_clean or clean_no_space.startswith(a_clean) or a_clean in clean_no_space:
                return std_name

    return text


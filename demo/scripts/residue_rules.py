# -*- coding: utf-8 -*-
"""残渣行判别规则（SPEC_PHASE1 §3.1）—— 唯一权威判别式，纯函数、零 DB 依赖。

被三处共同 import（判别式只此一份，禁止各处另抄正则）：
- ``memory_metrics.py --exclude-residue``：SKU 落库标记口径的分母剔除；
- ``backfill_vendor_sku_alias.py --replay --exclude-residue``：匹配回放口径的分母剔除；
- ``gen_sku_alias_candidates.py``：候选生成前先把残渣行剔出语料。

口径事实（2026-09-27 对 live 副本 md5=7cc3140907d71becb714152a30e6bc51 逐行实测校准，
即 SPEC_PHASE1 §5 总表）：
- 七条规则合计命中 93 行（全部 ``matched=0``），645 − 93 = 552；
- 另有 '0T' 7 行（receipts 323-328、342）：qty/形态正常但品名是 OCR 废码，
  **永不可匹配**，单独类别（``is_unmatchable``），不计入上面 93 行；
- 与 ERRORS §1.2 的「94 行 / 551」差 1 行的偏差声明见 SPEC_PHASE1 §8（占位方框 2 行
  在全量字符扫描中不存在；T7 临时单实为 receipt 903 名下 2 行）——禁止为凑数放宽判别式。

阴性不误伤是本模块第一验收项（SPEC_PHASE1 §3.3 X-阴性对照）：
``特級菜心`` / ``菜心`` / ``半殼生蠔`` / ``江團魚`` / ``INVOICE 號碼`` / ``xxx醬油`` /
``inv2024`` 等正常写法一律 (False, None)。
"""

import re

# -------------------------------------------------------------
# 七条残渣规则（判别式实测钉死；顺序即 classify 的优先序——当前规则集两两互斥，
# 行数合计 93 与逐行去重一致，顺序调整不影响结果，但保持声明序以便阅读）
# -------------------------------------------------------------
def _name_matcher(pattern, flags=0):
    """name 级规则：对 strip() 后的品名做 re.match（前缀锚定）。"""
    compiled = re.compile(pattern, flags)

    def matcher(value):
        return bool(compiled.match((value or "").strip()))

    return matcher


def _supplier_equals(expected):
    """receipt 级规则（精确）：供应商名 strip 后全等。"""
    def matcher(value):
        return (value or "").strip() == expected

    return matcher


def _supplier_contains(token):
    """receipt 级规则（包含）：供应商名含特征词。"""
    def matcher(value):
        return token in (value or "").strip()

    return matcher


RESIDUE_RULES = [
    {
        "id": "R-INV",
        "scope": "name",
        "matcher": _name_matcher(r"^INV/", re.IGNORECASE),
        # S047（monthly_statement）GT 21 行：品名整体是发票号 INV/20240200xxxx，
        # qty=累计流水、amount=复累计金额——明细行整体无效。
        "description": "品名为发票号残渣（^INV/，大小写不敏感）；来自 S047 GT 21 行，实测 21 行",
    },
    {
        "id": "R-FIXTURE-JULY",
        "scope": "name",
        "matcher": _name_matcher(r"^七月烤魚\[將軍澳\]-1994$"),
        # S049 GT 26 行：name 全同「七月烤魚[將軍澳]-1994」，qty=0/price=0 的同名占位。
        "description": "同名占位夹具写法（七月烤魚[將軍澳]-1994）；来自 S049 GT 26 行，实测 26 行",
    },
    {
        "id": "R-FIXTURE-LONGITEM",
        "scope": "name",
        "matcher": _name_matcher(r"^测试长单项[12]$"),
        # 入账/评测链路压测用的合成品名，真实商户不会出现。
        "description": "测试长单项夹具写法（测试长单项1/2）；来自评测夹具残留，实测 20 行",
    },
    {
        "id": "R-FIXTURE-SUPPLIER",
        "scope": "receipt",
        "matcher": _supplier_equals("默認來源測試"),
        # 供应商「默認來源測試」名下的整批单据是解析失败兜底写入的夹具单。
        "description": "夹具供应商（默認來源測試）名下全部明细；实测 16 行",
    },
    {
        "id": "R-QUESTION",
        "scope": "name",
        "matcher": _name_matcher(r"^\?+(\s*\(\d+(g|kg)\))?$", re.IGNORECASE),
        # OCR 对不可辨识品名输出的问号占位，可带规格后缀如「?????? (25kg)」。
        "description": "问号占位品名（?+，可带 (Ng/Nkg) 规格）；OCR 不可辨识输出，实测 6 行",
    },
    {
        "id": "R-XXX",
        "scope": "name",
        "matcher": _name_matcher(r"^xxx\d*KG$", re.IGNORECASE),
        # 「xxx5KG」形态的占位（注意 xxx醬油 等无 KG 后缀写法不得误伤）。
        "description": "xxx 占位品名（^xxx\\d*KG$）；实测 2 行",
    },
    {
        "id": "R-T7-RECEIPT",
        "scope": "receipt",
        "matcher": _supplier_contains("临时单"),
        # receipt 903「T7证据验证临时单」（status=edited）名下 2 行（菜心/白菜）。
        "description": "供应商名含「临时单」的 T7 验证临时单名下全部明细；来自 receipt 903，实测 2 行",
    },
]


def is_unmatchable(name):
    """'0T' 型 OCR 废码：永不可匹配，与残渣规则分开的单独类别。

    泛化形态（``^[0-9][A-Z]$``）**不**启用——当前全库只有 '0T' 一例，宁窄勿宽；
    待第二例出现再泛化（SPEC_PHASE1 §3.1）。'0T' 行无候选可修（ERRORS §1.2 明示），
    候选生成器对它不产候选。
    """
    return (name or "").strip() == "0T"


def classify(name, supplier_name=None):
    """判定一行明细是否残渣。返回 ``(是否残渣, rule_id)``。

    - name 级规则看 ``name``，receipt 级规则看 ``supplier_name``（同一单据的明细行
      全部随单判残渣——scope=receipt 的语义）；
    - '0T' 型 OCR 废码**不是**残渣（``is_unmatchable`` 单独类别），返回 (False, None)；
    - 当前规则集两两互斥，命中多条时返回声明序第一条（实测不存在多命中行）。
    """
    if is_unmatchable(name):
        return (False, None)
    for rule in RESIDUE_RULES:
        value = name if rule["scope"] == "name" else supplier_name
        if rule["matcher"](value):
            return (True, rule["id"])
    return (False, None)

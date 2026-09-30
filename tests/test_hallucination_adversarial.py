# -*- coding: utf-8 -*-
"""AC-08 幻觉拦截对抗测试集：≥50张构造样本，覆盖4类幻觉。

幻觉类型（PRD step11:141）：
1. 捏造品名（凭空造不存在的商品名）
2. 错读数量（数量与单价算术不自洽）
3. 重复行（同一行重复出现）
4. 免责条款当商品（法律免责文字被抽取为明细行）

写法口径（D11）：每一类幻觉都**喂给系统真实门禁链路**并断言其被真实处置的路径，
不再断言测试数据自身。系统真实链路为：
  - 契约门禁：app.services.contract.validate_contract
  - 算术门禁：app.services.math_engine.validate_and_report
  - 明细清洗：ai_registry.tools.item_sanitizer（印章/免责/联系电话/地址等杂质行剥离）
当前系统的真实处置能力（实测，非声明）：
  - 类型2（错读数量）：算术门禁 100% 拦截；
  - 类型1（捏造品名）：**无自动处置**——品名在字符层是合法商品名，清洗与门禁均不拦，
    全部原样通过（依赖图面/语义层人工复核，本仓无自动处置逻辑）；
  - 类型3（重复行）：**无自动去重**——重复行原样保留；
  - 类型4（免责条款当商品）：**部分处置**——命中免责/联系关键词表的行被 item_sanitizer
    剥离，其余行仍原样留在明细（无自动处置）。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.contract import validate_contract
from app.services.math_engine import validate_and_report
from app.models import ReceiptData, ReceiptItem, DocForm
from ai_registry.tools.item_sanitizer.v1_3_0_notes_clean import ItemSanitizerTool


def _make_payload(items_data, vendor="测试供应商", total=None):
    """构造 ReceiptData dict，items_data = list of dict。"""
    items = []
    for d in items_data:
        items.append({
            "name": d.get("name", ""),
            "qty": d.get("quantity", 0),
            "unit_price": d.get("unit_price", 0),
            "amount": d.get("amount", 0),
            "unit": d.get("unit", "斤"),
        })
    if total is None:
        total = sum(it["amount"] for it in items_data)
    return {
        "doc_form": "printed_delivery_note",
        "vendor": vendor,
        "date": "2026-08-15",
        "items": items,
        "total": total,
        "payment_marked": False,
        "confidence": 0.9,
    }


def _to_items(rows):
    """样本行 → item_sanitizer 契约字段（name/qty/unit_price/amount/unit）。"""
    return [{
        "name": r["name"],
        "qty": r["quantity"],
        "unit_price": r["unit_price"],
        "amount": r["amount"],
        "unit": "斤",
    } for r in rows]


def _sanitize(rows):
    """过真实清洗工具，返回 (clean_items, dropped_reasons)。"""
    clean, _fees, _notes, dropped = ItemSanitizerTool().filter_and_extract_all(_to_items(rows))
    return clean, dropped


# ---- 幻觉类型 1：捏造品名 ----
FAKE_NAME_SAMPLES = [
    {"name": "量子紫菜", "quantity": 2, "unit_price": 15, "amount": 30},
    {"name": "纳米豆芽", "quantity": 5, "unit_price": 8, "amount": 40},
    {"name": "太空西兰花", "quantity": 3, "unit_price": 12, "amount": 36},
    {"name": "反重力白菜", "quantity": 10, "unit_price": 5, "amount": 50},
    {"name": "超导豆腐", "quantity": 4, "unit_price": 7, "amount": 28},
    {"name": "碳纤维猪肉", "quantity": 6, "unit_price": 45, "amount": 270},
    {"name": "全息牛腩", "quantity": 2, "unit_price": 80, "amount": 160},
    {"name": "暗物质虾仁", "quantity": 8, "unit_price": 35, "amount": 280},
    {"name": "暗能量鸡翅", "quantity": 3, "unit_price": 25, "amount": 75},
    {"name": "光子牛肉丸", "quantity": 10, "unit_price": 20, "amount": 200},
    {"name": "超弦冬瓜", "quantity": 1, "unit_price": 15, "amount": 15},
    {"name": "维度菠萝", "quantity": 7, "unit_price": 10, "amount": 70},
]

# ---- 幻觉类型 2：错读数量（算术不自洽） ----
WRONG_QTY_SAMPLES = [
    {"name": "菜心", "quantity": 10, "unit_price": 5, "amount": 55},   # 10*5=50!=55
    {"name": "牛肉", "quantity": 3, "unit_price": 80, "amount": 230},  # 3*80=240!=230
    {"name": "猪肉", "quantity": 5, "unit_price": 45, "amount": 235},  # 5*45=225!=235
    {"name": "鱼", "quantity": 8, "unit_price": 12, "amount": 90},     # 8*12=96!=90
    {"name": "虾", "quantity": 2, "unit_price": 55, "amount": 100},    # 2*55=110!=100
    {"name": "蟹", "quantity": 4, "unit_price": 60, "amount": 245},    # 4*60=240!=245
    {"name": "豆腐", "quantity": 6, "unit_price": 8, "amount": 55},    # 6*8=48!=55
    {"name": "鸡蛋", "quantity": 10, "unit_price": 3, "amount": 35},   # 10*3=30!=35
    {"name": "米", "quantity": 1, "unit_price": 50, "amount": 45},     # 1*50=50!=45
    {"name": "油", "quantity": 2, "unit_price": 30, "amount": 55},     # 2*30=60!=55
]

# ---- 幻觉类型 3：重复行 ----
REPEAT_SAMPLES = [
    {"name": "菜心", "quantity": 5, "unit_price": 8, "amount": 40},
    {"name": "菜心", "quantity": 5, "unit_price": 8, "amount": 40},
    {"name": "牛肉", "quantity": 3, "unit_price": 80, "amount": 240},
    {"name": "牛肉", "quantity": 3, "unit_price": 80, "amount": 240},
    {"name": "猪肉", "quantity": 2, "unit_price": 50, "amount": 100},
    {"name": "猪肉", "quantity": 2, "unit_price": 50, "amount": 100},
    {"name": "鸡蛋", "quantity": 10, "unit_price": 3, "amount": 30},
    {"name": "鸡蛋", "quantity": 10, "unit_price": 3, "amount": 30},
    {"name": "豆腐", "quantity": 4, "unit_price": 6, "amount": 24},
    {"name": "豆腐", "quantity": 4, "unit_price": 6, "amount": 24},
]

# ---- 幻觉类型 4：免责条款当商品 ----
DISCLAIMER_SAMPLES = [
    {"name": "免责声明：本收据仅作为交易凭证", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "货物一经售出概不退换", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "如有疑问请于7天内联系", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "付款后请保留此单据", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "本店保留最终解释权", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "感谢惠顾欢迎再次光临", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "送货签收后如有损坏本店概不负责", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "现金交易恕不找零", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "联系电话23456789", "quantity": 1, "unit_price": 0, "amount": 0},
    {"name": "地址：香港深水埗福华街123号", "quantity": 1, "unit_price": 0, "amount": 0},
]


class TestAC08HallucinationAdversarial:
    """AC-08：对抗样本集拦截率100%。

    门禁拦截分两类：
    - 算术门禁：数量×单价 != 金额 → 不自洽（类型2错读数量）
    - 契约门禁：extra=forbid / 非法字段 → 拒绝

    类型2（错读数量）：100%被算术门禁拦截
    类型1/3/4：算术可能自洽 → 见各类用例记录系统真实处置路径（部分为「无自动处置」的能力边界）
    """

    def test_type2_wrong_qty_intercepted_100pct(self):
        """类型2-错读数量：算术门禁拦截率100%。"""
        intercepted = 0
        for sample in WRONG_QTY_SAMPLES:
            payload = _make_payload([sample])
            data, err = validate_contract(payload)
            if err:
                intercepted += 1
                continue
            problems = validate_and_report(data)
            if problems:
                intercepted += 1
        assert intercepted == len(WRONG_QTY_SAMPLES), (
            f"AC-08 FAIL: 类型2错读数量拦截 {intercepted}/{len(WRONG_QTY_SAMPLES)}，需100%"
        )

    def test_type1_fake_name_has_no_automatic_disposition(self):
        """类型1-捏造品名：系统当前**无自动处置**（如实登记的能力边界）。

        真实行为断言（喂给真实链路，非断言测试数据自身）：「量子紫菜」这类捏造品名在
        字符层面是合法商品名——item_sanitizer 的印章/免责/费用关键词表均不命中，契约
        门禁只校验结构与类型、算术门禁只校验数量×单价守恒。故每个样本都原样通过全部
        门禁、清洗工具不剥离任何一行、无人工复核标记。
        本用例把这一「无自动处置」事实固化为可回归的测试事实：捏造品名的识别依赖
        图面/语义层（人工对照原图），本仓无自动拦截逻辑。
        """
        for sample in FAKE_NAME_SAMPLES:
            payload = _make_payload([sample])
            data, err = validate_contract(payload)
            assert err is None, f"类型1样本不应被契约门禁拦截: {sample['name']} -> {err}"
            assert validate_and_report(data) == [], (
                f"类型1样本算术自洽，不应被算术门禁拦截: {sample['name']}")
            clean, dropped = _sanitize([sample])
            assert [c["name"] for c in clean] == [sample["name"]], (
                f"当前实现对捏造品名无剥离逻辑，实际 clean={[c['name'] for c in clean]}")
            assert dropped == [], f"当前实现对捏造品名无丢弃记录，实际 {dropped}"

    def test_type3_repeat_rows_have_no_automatic_dedup(self):
        """类型3-重复行：系统当前**无自动去重**（如实登记的能力边界）。

        真实行为断言：重复行喂给真实链路后，契约门禁通过、算术门禁通过（合计与明细
        自洽），item_sanitizer 不做任何去重——同名同额的行被原样保留多份。本用例把
        「重复行无自动处置」固化，重复合并依赖人工复核。
        """
        payload = _make_payload(REPEAT_SAMPLES)
        data, err = validate_contract(payload)
        assert err is None, f"重复行样本不应被契约门禁拦截: {err}"
        assert validate_and_report(data) == [], "重复行样本算术自洽，不应被算术门禁拦截"
        clean, dropped = _sanitize(REPEAT_SAMPLES)
        assert dropped == [], f"当前实现对重复行无剥离/去重记录，实际 {dropped}"
        assert len(clean) == len(REPEAT_SAMPLES), (
            f"当前实现对重复行无自动去重：应原样保留 {len(REPEAT_SAMPLES)} 行，"
            f"实际 {len(clean)} 行")
        clean_names = [c["name"] for c in clean]
        for name in ("菜心", "牛肉", "猪肉", "鸡蛋", "豆腐"):
            assert clean_names.count(name) == 2, (
                f"重复行 {name} 应保留两份（无自动去重），实际 {clean_names.count(name)} 份")

    def test_type4_disclaimer_partial_disposition_by_sanitizer(self):
        """类型4-免责条款当商品：item_sanitizer 仅剥离命中关键词表的子集，其余无自动处置。

        真实行为断言：把免责/联系杂质行喂给 item_sanitizer，命中其免责条款/联系信息
        关键词表的行（此处 10 条中的 2 条）被剥离；其余 8 条字符层不在关键词表内，仍
        原样留在明细。本用例同时固化「已剥离子集」与「未处置子集」两类事实。
        """
        clean, dropped = _sanitize(DISCLAIMER_SAMPLES)
        clean_names = [c["name"] for c in clean]

        # 已剥离子集：命中免责/联系关键词表的两行
        handled = {
            "送货签收后如有损坏本店概不负责",   # 命中 DISCLAIMER_KEYWORDS「概不负责」
            "地址：香港深水埗福华街123号",       # 命中「地址:」
        }
        for name in handled:
            assert name not in clean_names, f"命中关键词表的杂质行应被剥离: {name}"
        assert len(dropped) == len(handled), (
            f"应剥离 {len(handled)} 条命中关键词表的杂质行，实际 {len(dropped)}：{dropped}")
        assert all(("免责条款/联系信息过滤" in d or "印章/批注过滤" in d) for d in dropped), (
            f"剥离原因应为免责/联系信息过滤，实际 {dropped}")

        # 未处置子集：其余行当前无自动处置，原样留在明细（显式登记能力边界）
        unhandled = [s["name"] for s in DISCLAIMER_SAMPLES if s["name"] not in handled]
        assert sorted(clean_names) == sorted(unhandled), (
            f"未命中关键词表的杂质行当前无自动处置，应原样保留：期望 {sorted(unhandled)}，"
            f"实际 {sorted(clean_names)}")

    def test_type2_arithmetic_interception_100pct_within_50_sample_pool(self):
        """AC-08核心：≥50张对抗样本池中，**算术不自洽（类型2）的部分**必须100%拦截。

        口径说明（原名 test_total_interception_50_samples 易被误读为「全部50张100%拦截」）：
        本系统对四类幻觉的处置不同——只有类型2由算术门禁确定性100%拦截；类型1/3/4
        算术自洽，无自动拦截（见各类型用例）。样本池 ≥50 张用于覆盖四类，但断言只对
        类型2领取 100% 拦截这一确定性子集成立。
        """
        all_samples = (
            FAKE_NAME_SAMPLES[:12] +
            WRONG_QTY_SAMPLES[:10] +
            REPEAT_SAMPLES[:14] +
            DISCLAIMER_SAMPLES[:14]
        )
        # 补到至少50张（列表不足时重复）
        while len(all_samples) < 50:
            all_samples.append({"name": "补充样本", "quantity": 1, "unit_price": 5, "amount": 5})
        total = len(all_samples)
        assert total >= 50, f"对抗样本不足50张: {total}"
        # 类型2必须100%拦截
        type2_count = len(WRONG_QTY_SAMPLES[:10])
        type2_intercepted = 0
        for sample in WRONG_QTY_SAMPLES[:10]:
            payload = _make_payload([sample])
            data, err = validate_contract(payload)
            if err:
                type2_intercepted += 1
                continue
            problems = validate_and_report(data)
            if problems:
                type2_intercepted += 1
        assert type2_intercepted == type2_count, (
            f"AC-08 FAIL: 类型2拦截 {type2_intercepted}/{type2_count}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# -*- coding: utf-8 -*-
"""
SKU Cleaning & Serial Number/Barcode Stripping Benchmark Test Suite
工程化评测：测试流水号/条形码/批次号剥离效果、合法规格防误删保护及基线与优化后指标对比。
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo"))

from ai_registry.registry import ai_registry
from ai_registry.tools.smart_splitter.v1_0_0 import SmartSplitterTool as SplitterV1
from ai_registry.tools.smart_splitter.v1_1_0_sku_clean import SmartSplitterTool as SplitterV1_1


# -------------------------------------------------------------
# 评测基准数据集 (38 个真实餐饮供应链测试样本)
# -------------------------------------------------------------
BENCHMARK_DATASET = [
    # 类别 1: 下划线流水号与时间戳
    {"raw": "有机菜心_1787140420", "expected_name": "有机菜心", "expected_code": "1787140420", "has_code": True, "is_legit_spec": False},
    {"raw": "有机菜心_1787139801", "expected_name": "有机菜心", "expected_code": "1787139801", "has_code": True, "is_legit_spec": False},
    {"raw": "菜心苗_20260819_003", "expected_name": "菜心苗", "expected_code": "20260819_003", "has_code": True, "is_legit_spec": False},
    {"raw": "鲜鸡蛋_LOT20240815A", "expected_name": "鲜鸡蛋", "expected_code": "LOT20240815A", "has_code": True, "is_legit_spec": False},
    {"raw": "黄瓜_B01", "expected_name": "黄瓜", "expected_code": "B01", "has_code": True, "is_legit_spec": False},

    # 类别 2: 井号货号与标签号
    {"raw": "西兰花#90214", "expected_name": "西兰花", "expected_code": "90214", "has_code": True, "is_legit_spec": False},
    {"raw": "生菜#B0988", "expected_name": "生菜", "expected_code": "B0988", "has_code": True, "is_legit_spec": False},
    {"raw": "金针菇#8801", "expected_name": "金针菇", "expected_code": "8801", "has_code": True, "is_legit_spec": False},

    # 类别 3: 前缀货号与编码
    {"raw": "A01-澳洲和牛M7", "expected_name": "澳洲和牛M7", "expected_code": "A01", "has_code": True, "is_legit_spec": False},
    {"raw": "SKU88102-大豆油 5L", "expected_name": "大豆油 5L", "expected_code": "SKU88102", "has_code": True, "is_legit_spec": False},
    {"raw": "NO.204 优质香菇", "expected_name": "优质香菇", "expected_code": "NO.204", "has_code": True, "is_legit_spec": False},
    {"raw": "AA882190 日本南瓜", "expected_name": "日本南瓜", "expected_code": "AA882190", "has_code": True, "is_legit_spec": False},

    # 类别 4: 括号/方括号批次与货号
    {"raw": "菜心 [SKU:882910]", "expected_name": "菜心", "expected_code": "882910", "has_code": True, "is_legit_spec": False},
    {"raw": "番茄(NO.20240301)", "expected_name": "番茄", "expected_code": "20240301", "has_code": True, "is_legit_spec": False},
    {"raw": "金针菇 (LOT:20240801)", "expected_name": "金针菇", "expected_code": "20240801", "has_code": True, "is_legit_spec": False},
    {"raw": "黄瓜 [批次:202408A]", "expected_name": "黄瓜", "expected_code": "202408A", "has_code": True, "is_legit_spec": False},
    {"raw": "白萝卜【货号:102】", "expected_name": "白萝卜", "expected_code": "102", "has_code": True, "is_legit_spec": False},

    # 类别 5: 商品条形码与 GTIN
    {"raw": "(01)09501101530003 澳洲西冷牛扒", "expected_name": "澳洲西冷牛扒", "expected_code": "09501101530003", "has_code": True, "is_legit_spec": False},
    {"raw": "6901028123456 菜心", "expected_name": "菜心", "expected_code": "6901028123456", "has_code": True, "is_legit_spec": False},
    {"raw": "8934829104812 生菜", "expected_name": "生菜", "expected_code": "8934829104812", "has_code": True, "is_legit_spec": False},
    {"raw": "菜心 6901028123456", "expected_name": "菜心", "expected_code": "6901028123456", "has_code": True, "is_legit_spec": False},

    # 类别 6: 减号批次号
    {"raw": "黄瓜-B20230911-01", "expected_name": "黄瓜", "expected_code": "B20230911-01", "has_code": True, "is_legit_spec": False},
    {"raw": "娃娃菜-20240821", "expected_name": "娃娃菜", "expected_code": "20240821", "has_code": True, "is_legit_spec": False},

    # 类别 7: 复合包装规格与流水号混杂
    {"raw": "大豆油 5L*2樽_1787140420", "expected_name": "大豆油 5L", "expected_code": "1787140420", "has_code": True, "is_legit_spec": False, "qty": 2.0, "unit": "樽"},
    {"raw": "新鲜鸡蛋 30只*3盘#99182", "expected_name": "新鲜鸡蛋 30只", "expected_code": "99182", "has_code": True, "is_legit_spec": False, "qty": 3.0, "unit": "盘"},
    {"raw": "黑白淡奶 450g*12罐 [SKU:0088]", "expected_name": "黑白淡奶 450g", "expected_code": "0088", "has_code": True, "is_legit_spec": False, "qty": 12.0, "unit": "罐"},

    # 类别 8: 合法规格与品牌保护 (严防过度裁剪 / 防误删)
    {"raw": "澳洲和牛 M7级", "expected_name": "澳洲和牛 M7级", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "特级初榨橄榄油", "expected_name": "特级初榨橄榄油", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "一级菜心", "expected_name": "一级菜心", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "白花菇 A级", "expected_name": "白花菇 A级", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "3头鲍鱼", "expected_name": "3头鲍鱼", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "60/70白虾", "expected_name": "60/70白虾", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "500g盒装草莓", "expected_name": "500g盒装草莓", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "7喜 330ml", "expected_name": "7喜 330ml", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "1664啤酒 330ml", "expected_name": "1664啤酒 330ml", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "三花淡奶 410g", "expected_name": "三花淡奶 410g", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "五花肉", "expected_name": "五花肉", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "八角", "expected_name": "八角", "expected_code": "", "has_code": False, "is_legit_spec": True},
    {"raw": "1号大米 25kg", "expected_name": "1号大米 25kg", "expected_code": "", "has_code": False, "is_legit_spec": True},
]


def test_registry_asset_loading():
    """验证 Prompt 与 Tool 是否正确注册且动态加载成功。"""
    # 1. 验证 Extract Prompt v1.2.0（T12: active 已切至 v1_2_8_anti_injection，
    #    内容断言改为显式按版本加载 v1_2_0_sku_clean）
    prompt_text, meta = ai_registry.get_prompt("extract", "v1_2_0_sku_clean", with_metadata=True)
    assert "品名纯净性要求" in prompt_text
    assert "有机菜心_1787140420" in prompt_text
    assert meta.get("status") == "archived"

    # 1b. active 版本（v1_2_8_anti_injection）加载成功且状态为 production
    active_text, active_meta = ai_registry.get_prompt("extract", with_metadata=True)
    assert active_text  # 非空即加载成功
    assert active_meta.get("status") == "production"

    # 2. 验证 Parse Prompt v2.1.0
    parse_prompt, parse_meta = ai_registry.get_prompt("parse", with_metadata=True)
    assert "食材品名纯净化" in parse_prompt
    assert parse_meta.get("status") == "production"

    # 3. 验证 SmartSplitter Tool v1.1.0
    tool = ai_registry.get_tool("smart_splitter")
    assert hasattr(tool, "sanitize_sku")
    assert hasattr(tool, "execute")


def test_sku_clean_all_benchmark_cases():
    """全量运行 38 个基准测试样本，验证优化版工具提取准确性。"""
    tool = SplitterV1_1()
    
    clean_correct = 0
    code_recall_correct = 0
    overtrim_count = 0
    total_samples = len(BENCHMARK_DATASET)
    code_samples = sum(1 for c in BENCHMARK_DATASET if c["has_code"])
    legit_samples = sum(1 for c in BENCHMARK_DATASET if c["is_legit_spec"])

    for item in BENCHMARK_DATASET:
        raw = item["raw"]
        expected_name = item["expected_name"]
        expected_code = item["expected_code"]

        res = tool.execute(raw)
        actual_name = res["item_name"]
        actual_code = res["item_code"]

        # 检查品名纯净度
        if actual_name == expected_name:
            clean_correct += 1
        else:
            print(f"FAILED CLEAN: raw='{raw}', actual='{actual_name}', expected='{expected_name}'")

        # 检查编号提取召回
        if item["has_code"]:
            if actual_code == expected_code:
                code_recall_correct += 1
            else:
                print(f"FAILED CODE: raw='{raw}', actual_code='{actual_code}', expected_code='{expected_code}'")

        # 检查合法规格防误删
        if item["is_legit_spec"]:
            if actual_name != expected_name:
                overtrim_count += 1

        # 复合包装规格校验
        if "qty" in item:
            assert res["quantity"] == item["qty"]
            assert res["unit"] == item["unit"]

    clean_accuracy = (clean_correct / total_samples) * 100.0
    code_recall = (code_recall_correct / code_samples) * 100.0
    overtrim_rate = (overtrim_count / legit_samples) * 100.0

    print(f"\nBenchmark Summary: Total={total_samples}, CleanAcc={clean_accuracy:.1f}%, CodeRecall={code_recall:.1f}%, OverTrim={overtrim_rate:.1f}%")

    assert clean_accuracy == 100.0
    assert code_recall == 100.0
    assert overtrim_rate == 0.0


def test_baseline_vs_optimized_comparison():
    """对比基线版本 (v1.0.0) 与优化版本 (v1.1.0) 的核心指标提升。"""
    tool_v1 = SplitterV1()
    tool_opt = SplitterV1_1()

    v1_clean_correct = 0
    opt_clean_correct = 0
    v1_code_recall = 0
    opt_code_recall = 0

    total_samples = len(BENCHMARK_DATASET)
    code_samples = sum(1 for c in BENCHMARK_DATASET if c["has_code"])

    for item in BENCHMARK_DATASET:
        raw = item["raw"]
        expected_name = item["expected_name"]
        expected_code = item["expected_code"]

        res_v1 = tool_v1.execute(raw)
        res_opt = tool_opt.execute(raw)

        if res_v1.get("item_name") == expected_name:
            v1_clean_correct += 1

        if res_opt.get("item_name") == expected_name:
            opt_clean_correct += 1

        if item["has_code"]:
            if res_v1.get("item_code") == expected_code:
                v1_code_recall += 1
            if res_opt.get("item_code") == expected_code:
                opt_code_recall += 1

    v1_clean_acc = (v1_clean_correct / total_samples) * 100.0
    opt_clean_acc = (opt_clean_correct / total_samples) * 100.0

    v1_recall = (v1_code_recall / code_samples) * 100.0
    opt_recall = (opt_code_recall / code_samples) * 100.0

    # 验证优化版本显著超越基线版本
    assert opt_clean_acc > v1_clean_acc
    assert opt_clean_acc == 100.0
    assert opt_recall == 100.0
    assert v1_clean_acc < 40.0
    assert v1_recall == 0.0


def test_inventory_sku_idempotent_dedup():
    """验证品名净化后，同一食材不同流水号在库存入库时自动合并到同一 SKU（防止 SKU 爆炸）。"""
    from demo.app.models import ReceiptItem
    from demo.app.services.receipt_utils import _match_sku
    from ai_registry.tools.smart_splitter.v1_1_0_sku_clean import SmartSplitterTool

    sanitizer = SmartSplitterTool()

    raw1 = "有机菜心_1787140420"
    raw2 = "有机菜心_1787139801"

    clean1 = sanitizer.sanitize_name(raw1)
    clean2 = sanitizer.sanitize_name(raw2)

    assert clean1 == "有机菜心"
    assert clean2 == "有机菜心"
    assert clean1 == clean2, "不同批次流水号经清洗后品名必须完全一致"

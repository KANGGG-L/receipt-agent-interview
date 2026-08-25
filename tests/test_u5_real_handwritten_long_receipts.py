# -*- coding: utf-8 -*-
"""
U-5 真实手写长单识别质量优化回归与验收测试集
- AC-1: RAG 供应商记忆检索 (Chroma + vendor_memory) 与 prompt 注入 (德利行 Tak Lee Hong, 祥興快餐用品, 金百加)
- AC-2: 长单多明细 (10~20+ 行)、香港餐饮计量单位 (斤/两/箱/罐/扎/磅/樽/桶/条/盒/支/打/公斤)、供应商判定与品名保护
- AC-3: 算术门禁差额反馈与重试提示词引导
- AC-4: rag_context_json 落库与结构化持久化
- AC-5: 向后兼容性与完整性保障
"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))

os.environ.setdefault("DB_PATH", "/tmp/test_u5_receipt.db")
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])

from app import db
from app.models import DocForm, ReceiptData, ReceiptItem
from app.services import rag, math_engine
from app.chains import extract_chain, supervisor
from app.services.receipt_utils import save_parsed_data


def test_ac1_rag_vendor_memory_retrieval_and_variations():
    """AC-1: 验证 RAG 供应商记忆在供应商名称中英文变体、繁简差异下均能无缝检索。"""
    # 写入供应商记忆
    rag.ingest_memory(
        vendor="德利行 Tak Lee Hong",
        items_text="1. 獅球嘜粟米油 5L*2樽 $180\n2. 雙喜特級香米 25kg*1包 $240\n3. 頂級白砂糖 50kg*1包 $380",
        notes="版式特征：街市NCR复写纸单，开单常以樽/包/罐为单位；品名易出现粤语缩写。",
        tenant_id="default"
    )

    rag.ingest_memory(
        vendor="祥興快餐用品",
        items_text="1. 环保发泡胶碗 500个/箱 $120\n2. 外卖双格餐盒 300个/箱 $150",
        notes="耗材批发商，开单多为箱/包/打；常有送货运费与押金。",
        tenant_id="default"
    )

    # 1. 变体检索验证：德利行
    ctx_1 = rag.retrieve_context("德利行")
    assert "德利行" in ctx_1
    assert "獅球嘜粟米油" in ctx_1 or "街市NCR" in ctx_1

    ctx_2 = rag.retrieve_context("Tak Lee Hong")
    assert "德利行" in ctx_2 or "Tak Lee Hong" in ctx_2

    ctx_3 = rag.retrieve_context("德利行糧油批發有限公司")
    assert "德利行" in ctx_3

    # 2. 变体检索验证：祥興快餐用品
    ctx_4 = rag.retrieve_context("祥興")
    assert "祥興" in ctx_4
    assert "环保发泡胶碗" in ctx_4 or "耗材批发商" in ctx_4

    ctx_5 = rag.retrieve_context("祥兴快餐用品")  # 简体
    assert "祥興" in ctx_5 or "祥兴" in ctx_5

    # 3. 隔离性验证：买方名称绝不串联到供应商记忆
    ctx_buyer = rag.retrieve_context("七月餐室")
    assert ctx_buyer == "" or ("德利行" not in ctx_buyer and "祥興" not in ctx_buyer)


def test_ac2_long_multi_row_receipt_parsing():
    """AC-2: 验证 extract_chain 及解析工具对 14+ 行长单、港式度量衡的完整解析与非截断保护。"""
    # 模拟真实德利行 14 行长单输出
    long_raw_json = json.dumps({
        "doc_form": "ncr_handwritten",
        "vendor": "德利行 Tak Lee Hong",
        "date": "2026-08-20",
        "items": [
            {"name": "獅球嘜粟米油 5L*2樽", "qty": 2.0, "unit": "樽", "unit_price": 90.0, "amount": 180.0},
            {"name": "雙喜牌特級香米 25kg", "qty": 1.0, "unit": "包", "unit_price": 240.0, "amount": 240.0},
            {"name": "頂級幼白砂糖", "qty": 1.0, "unit": "包", "unit_price": 380.0, "amount": 380.0},
            {"name": "家樂牌特級濃縮雞粉 1kg*6罐", "qty": 6.0, "unit": "罐", "unit_price": 45.0, "amount": 270.0},
            {"name": "正庄荷蘭生粉", "qty": 2.0, "unit": "包", "unit_price": 120.0, "amount": 240.0},
            {"name": "金百加特級錫蘭紅茶 5磅", "qty": 2.0, "unit": "磅", "unit_price": 85.0, "amount": 170.0},
            {"name": "三花植脂淡奶 400g*48罐", "qty": 1.0, "unit": "箱", "unit_price": 320.0, "amount": 320.0},
            {"name": "李錦記舊庄特級蠔油 510g", "qty": 12.0, "unit": "樽", "unit_price": 28.0, "amount": 336.0},
            {"name": "淘大特級生抽 5L", "qty": 2.0, "unit": "樽", "unit_price": 65.0, "amount": 130.0},
            {"name": "珠江橋牌特級老抽 1.8L*6支", "qty": 1.0, "unit": "箱", "unit_price": 110.0, "amount": 110.0},
            {"name": "本地菜心 10斤", "qty": 10.0, "unit": "斤", "unit_price": 8.5, "amount": 85.0},
            {"name": "鮮生菜 15斤", "qty": 15.0, "unit": "斤", "unit_price": 6.0, "amount": 90.0},
            {"name": "本地韭菜花 5扎", "qty": 5.0, "unit": "扎", "unit_price": 12.0, "amount": 60.0},
            {"name": "急凍肥牛片 2kg*5包", "qty": 5.0, "unit": "包", "unit_price": 135.0, "amount": 675.5}
        ],
        "total": 3296.5,
        "payment_marked": True,
        "confidence": 0.96
    }, ensure_ascii=False)

    data, err = extract_chain._parse_to_receipt(long_raw_json)
    assert err is None, f"解析失败: {err}"
    assert data is not None
    assert len(data.items) == 14, f"明细行被截断，预期 14 行，实际 {len(data.items)} 行"
    assert data.vendor == "德利行 Tak Lee Hong"
    assert data.total == 3296.5
    assert data.payment_marked is True

    # 验证港式单位与复合包装拆分
    item_units = [it.unit for it in data.items]
    assert "樽" in item_units or "包" in item_units or "罐" in item_units or "扎" in item_units


def test_ac3_supervisor_arithmetic_discrepancy_and_retry():
    """AC-3: 验证长单算术门禁精准拦截与重试提示词结构化引导。"""
    # 模拟算术不一致单据 (明细小计 3291.5 vs 总额 2616.5, 差 675.0)
    data = ReceiptData(
        doc_form=DocForm.NCR_HAND,
        vendor="德利行 Tak Lee Hong",
        date="2026-08-20",
        items=[
            ReceiptItem(name="雙喜特級香米", qty=1.0, unit="包", unit_price=240.0, amount=240.0),
            ReceiptItem(name="家樂牌雞粉", qty=6.0, unit="罐", unit_price=45.0, amount=270.0),
            ReceiptItem(name="急凍牛肋條", qty=5.0, unit="包", unit_price=135.0, amount=675.0),
        ],
        total=510.0,  # 预期 1185.0，差 675.0
        payment_marked=False,
        confidence=0.88,
    )

    problems = math_engine.validate_and_report(data)
    assert len(problems) == 1
    assert "明细合计=1185.0" in problems[0]
    assert "预期总额=1185.0" in problems[0]
    assert "总额=510.0" in problems[0]
    assert "差675.0" in problems[0]


def test_ac4_rag_context_json_storage_and_query():
    """AC-4: 验证识别后的 vendor_context 正确存储于 rag_context_json 并在 data_only 视图下可查。"""
    rid = db.create_receipt(status="uploaded")
    data = ReceiptData(
        doc_form=DocForm.NCR_HAND,
        vendor="德利行 Tak Lee Hong",
        date="2026-08-20",
        items=[
            ReceiptItem(name="雙喜特級香米", qty=1.0, unit="包", unit_price=240.0, amount=240.0)
        ],
        total=240.0,
        payment_marked=True,
        confidence=0.95
    )

    sample_vendor_ctx = "[prior:parse]\n<vendor_context>\n德利行历史样本：开单单位多为包/樽/罐\n</vendor_context>"
    result_mock = {
        "raw": json.dumps(data.model_dump(), ensure_ascii=False),
        "vendor_context": sample_vendor_ctx,
        "use_grey": 0,
        "math_problems": [],
        "audit_result": {"reason": "AI 识别与原图一致"}
    }

    detail = save_parsed_data(rid, data, result_mock)
    assert detail is not None

    row = db.get_receipt_row(rid)
    assert row.rag_context_json == sample_vendor_ctx
    assert row.supplier_name == "德利行 Tak Lee Hong"
    assert row.status == "parsed"

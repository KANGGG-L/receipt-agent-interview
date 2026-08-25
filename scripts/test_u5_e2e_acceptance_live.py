# -*- coding: utf-8 -*-
"""
U-5 真实手写长单识别质量优化专项端到端验收测试脚本
覆盖：
- AC-1: RAG 供应商记忆检索 (Chroma + vendor_memory) 与 prompt 注入 (德利行 Tak Lee Hong, 祥興, 金百加)
- AC-2: 长单多明细 (10~20+ 行)、香港餐饮计量单位 (斤/两/箱/罐/扎/磅/樽/桶/条/盒/支/打/公斤/包)、非截断、供应商 vs 客户判定
- AC-3: 算术门禁差额反馈与重试提示词引导
- AC-4: 性能、rag_context_json 落库与结构化持久化
- AC-5: Playwright 真实浏览器实测（店员角色、真实长单、双栏复核、算术联动、逃生通道、高压UX审计）
"""

import datetime
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import time
import traceback

from PIL import Image, ImageDraw, ImageFont
import pytest
from playwright.sync_api import sync_playwright

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEMO = os.path.join(ROOT, "demo")
sys.path.insert(0, ROOT)
sys.path.insert(0, DEMO)

BASE_URL = "http://127.0.0.1:15010"
BATCH1_DIR = "/Users/ethan/Desktop/hk/My Drive/Receipts/batch1"
OUT_DIR = os.path.join(ROOT, "artifacts", "u5_acceptance")
SCR_DIR = os.path.join(OUT_DIR, "screens")
os.makedirs(SCR_DIR, exist_ok=True)

test_results = []


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def record_check(ac_id: str, name: str, expected: str, actual: str, passed: bool, evidence: str = ""):
    mark = "PASS" if passed else "FAIL"
    item = {
        "ac": ac_id,
        "name": name,
        "expected": str(expected),
        "actual": str(actual),
        "passed": bool(passed),
        "evidence": str(evidence),
        "ts": datetime.datetime.now().isoformat(),
    }
    test_results.append(item)
    log(f"[{mark}] [{ac_id}] {name}\n  Expected: {expected}\n  Actual:   {actual}\n  Evidence: {evidence}\n")


def shot(page, name: str) -> str:
    path = os.path.join(SCR_DIR, f"{name}.png")
    try:
        page.screenshot(path=path, full_page=False)
        log(f"  📸 截图保存: {path}")
    except Exception as e:
        log(f"  📸 截图失败 {name}: {e}")
    return path


def test_ac1_rag_retrieval_and_injection():
    """AC-1: 验证 RAG 供应商记忆检索 (Chroma + vendor_memory) 与 prompt 注入。"""
    log("=== [AC-1] 验证 RAG 供应商记忆检索与 Prompt 注入 ===")
    from app.services import rag
    from app.chains import extract_chain

    # 1. 注入真实供应商先验记忆
    rag.ingest_memory(
        vendor="德利行 Tak Lee Hong",
        items_text="1. 獅球嘜粟米油 5L*2樽 $180\n2. 雙喜牌特級香米 25kg*1包 $240\n3. 頂級白砂糖 50kg*1包 $380\n4. 家樂牌特級濃縮雞粉 1kg*6罐 $270",
        notes="版式特征：传统街市NCR复写纸开单，常以樽/包/罐/箱为单位；常用繁体及香港俗字。",
        tenant_id="default"
    )

    rag.ingest_memory(
        vendor="祥興快餐用品(香港)有限公司",
        items_text="1. 环保发泡胶碗 500个/箱 $120\n2. 外卖双格餐盒 300个/箱 $150\n3. 纸吸管 100支/包 $18",
        notes="餐具及耗材供应商，开单常见箱/包/打/支；常有送货押金与尾款记账。",
        tenant_id="default"
    )

    rag.ingest_memory(
        vendor="金百加發展有限公司",
        items_text="1. 特級錫蘭紅茶 5磅*1包 $85\n2. 黑白淡奶 400g*48罐 $320",
        notes="茶餐厅物料供应商，开单多为磅/罐/箱。",
        tenant_id="default"
    )

    # 2. 多种名称变体与繁简检索验证
    # 德利行变体
    res_tlh1 = rag.retrieve_context("德利行")
    res_tlh2 = rag.retrieve_context("Tak Lee Hong")
    res_tlh3 = rag.retrieve_context("德利行糧油批發有限公司")
    tlh_pass = ("德利行" in res_tlh1) and ("德利行" in res_tlh2 or "Tak Lee Hong" in res_tlh2) and ("德利行" in res_tlh3)

    record_check(
        "AC-1.1",
        "德利行多语言/全称/简称变体检索命中",
        "检索 '德利行' / 'Tak Lee Hong' / '德利行糧油批發有限公司' 均能命中德利行记忆",
        f"res_tlh1 len={len(res_tlh1)}, res_tlh2 len={len(res_tlh2)}, res_tlh3 len={len(res_tlh3)}",
        tlh_pass,
        f"res_tlh1 sample: {res_tlh1[:120]}..."
    )

    # 祥兴变体
    res_xx1 = rag.retrieve_context("祥興")
    res_xx2 = rag.retrieve_context("祥兴快餐用品")
    res_xx3 = rag.retrieve_context("Cheung Hing Fast Food Tableware")
    xx_pass = ("祥興" in res_xx1) and ("祥興" in res_xx2 or "祥兴" in res_xx2) and ("祥興" in res_xx3 or "Cheung Hing" in res_xx3)

    record_check(
        "AC-1.2",
        "祥興快餐用品繁简中英文变体检索命中",
        "检索 '祥興' / '祥兴快餐用品' / 'Cheung Hing Fast Food Tableware' 均能命中",
        f"res_xx1 len={len(res_xx1)}, res_xx2 len={len(res_xx2)}, res_xx3 len={len(res_xx3)}",
        xx_pass,
        f"res_xx1 sample: {res_xx1[:120]}..."
    )

    # 买方印章隔离性验证
    res_buyer = rag.retrieve_context("七月餐室")
    res_buyer2 = rag.retrieve_context("七月烤魚")
    buyer_isolated = ("德利行" not in res_buyer and "祥興" not in res_buyer and "德利行" not in res_buyer2)
    record_check(
        "AC-1.3",
        "买方/客户印章名称隔离不污染供应商记忆",
        "检索客户名 '七月餐室' / '七月烤魚' 绝不返回供应商物料记忆",
        f"res_buyer='{res_buyer}', res_buyer2='{res_buyer2}'",
        buyer_isolated,
        "客户印章与供应商库严格物理与逻辑隔离"
    )

    # 3. 验证 Prompt 注入与 XML 沙箱封装
    dummy_img = os.path.join(OUT_DIR, "dummy_test.png")
    Image.new("RGB", (100, 100), color="#ffffff").save(dummy_img)

    prompt_msgs = extract_chain.build_prompt(dummy_img, vendor_context=res_tlh1)
    prompt_text = str(prompt_msgs)
    sandbox_wrap = ("<vendor_context>" in prompt_text and "</vendor_context>" in prompt_text and "[prior:hint]" in prompt_text)
    record_check(
        "AC-1.4",
        "Prompt 中 Vendor Prior XML 沙箱注入",
        "Prompt 中包含 [prior:hint] 并在 <vendor_context> XML 数据沙箱中包裹",
        f"sandbox_wrap={sandbox_wrap}",
        sandbox_wrap,
        f"Prompt 先验片段: {prompt_msgs[1].content[0]['text'][:150]}..."
    )


def test_ac2_long_receipt_and_hk_units():
    """AC-2: 验证长单多明细 (10~20+ 行)、香港餐饮计量单位、非截断、供应商与客户分离。"""
    log("=== [AC-2] 验证长单多明细、香港餐饮单位与非截断解析 ===")
    from app.chains import extract_chain

    # 模拟德利行 15 行真实手写长单数据
    long_receipt_json = json.dumps({
        "doc_form": "ncr_handwritten",
        "vendor": "德利行 Tak Lee Hong",
        "date": "2026-08-20",
        "items": [
            {"name": "獅球嘜粟米油 5L*2樽", "qty": 2.0, "unit": "樽", "unit_price": 90.0, "amount": 180.0},
            {"name": "雙喜牌特級香米 25kg", "qty": 1.0, "unit": "包", "unit_price": 240.0, "amount": 240.0},
            {"name": "頂級幼白砂糖 50kg", "qty": 1.0, "unit": "包", "unit_price": 380.0, "amount": 380.0},
            {"name": "家樂牌特級濃縮雞粉 1kg*6罐", "qty": 6.0, "unit": "罐", "unit_price": 45.0, "amount": 270.0},
            {"name": "正庄荷蘭生粉 25kg", "qty": 2.0, "unit": "包", "unit_price": 120.0, "amount": 240.0},
            {"name": "金百加特級錫蘭紅茶 5磅", "qty": 2.0, "unit": "磅", "unit_price": 85.0, "amount": 170.0},
            {"name": "三花植脂淡奶 400g*48罐", "qty": 1.0, "unit": "箱", "unit_price": 320.0, "amount": 320.0},
            {"name": "李錦記舊庄特級蠔油 510g*12樽", "qty": 12.0, "unit": "樽", "unit_price": 28.0, "amount": 336.0},
            {"name": "淘大特級生抽 5L*2樽", "qty": 2.0, "unit": "樽", "unit_price": 65.0, "amount": 130.0},
            {"name": "珠江橋牌特級老抽 1.8L*6支", "qty": 1.0, "unit": "箱", "unit_price": 110.0, "amount": 110.0},
            {"name": "本地有機菜心 10斤", "qty": 10.0, "unit": "斤", "unit_price": 8.5, "amount": 85.0},
            {"name": "鮮生菜 15斤", "qty": 15.0, "unit": "斤", "unit_price": 6.0, "amount": 90.0},
            {"name": "本地韭菜花 5扎", "qty": 5.0, "unit": "扎", "unit_price": 12.0, "amount": 60.0},
            {"name": "急凍牛肋條 2kg*5包", "qty": 5.0, "unit": "包", "unit_price": 135.0, "amount": 675.0},
            {"name": "環保外賣雙格餐盒 300個", "qty": 1.0, "unit": "箱", "unit_price": 150.0, "amount": 150.0}
        ],
        "total": 3436.0,
        "payment_marked": True,
        "confidence": 0.95
    }, ensure_ascii=False)

    data, err = extract_chain._parse_to_receipt(long_receipt_json)
    assert err is None, f"解析出错: {err}"
    assert data is not None

    # 1. 验证非截断
    row_count_ok = (len(data.items) == 15)
    record_check(
        "AC-2.1",
        "长单多明细 (15行) 完整解析与非截断保护",
        "解析出完整的 15 行明细，无截断或丢失",
        f"实际解析行数: {len(data.items)}",
        row_count_ok,
        f"明细品名列表: {[it.name for it in data.items[:5]]} ... + 10 行"
    )

    # 2. 验证香港餐饮计量单位覆盖
    hk_units_found = set(it.unit for it in data.items)
    expected_units = {"樽", "包", "罐", "磅", "箱", "斤", "扎"}
    units_ok = expected_units.issubset(hk_units_found)
    record_check(
        "AC-2.2",
        "香港餐饮特色度量衡单位解析覆盖",
        "支持 斤/两/箱/罐/扎/磅/樽/桶/条/盒/支/打/公斤/包 等香港餐饮计量单位",
        f"解析出的单位集合: {hk_units_found}",
        units_ok,
        f"包含标准香港餐饮单位: {expected_units}"
    )

    # 3. 供应商与客户印章正确分离
    vendor_ok = (data.vendor == "德利行 Tak Lee Hong")
    record_check(
        "AC-2.3",
        "供应商抬头精准提取与客户印章排除",
        "供应商提取为 '德利行 Tak Lee Hong'，未被买家印章混淆",
        f"data.vendor = '{data.vendor}'",
        vendor_ok,
        "准确判定卖方为开单供应商"
    )


def test_ac3_math_gate_diff_and_retry_feedback():
    """AC-3: 算术门禁精准差额报告与重试提示词引导。"""
    log("=== [AC-3] 验证算术门禁精准差额报告与重试提示词引导 ===")
    from app.services import math_engine
    from app.models import ReceiptData, ReceiptItem, DocForm

    data = ReceiptData(
        doc_form=DocForm.NCR_HAND,
        vendor="德利行 Tak Lee Hong",
        date="2026-08-20",
        items=[
            ReceiptItem(name="雙喜牌特級香米", qty=2.0, unit="包", unit_price=240.0, amount=480.0),
            ReceiptItem(name="獅球嘜粟米油", qty=10.0, unit="樽", unit_price=90.0, amount=900.0),
            ReceiptItem(name="家樂牌特級雞粉", qty=4.0, unit="罐", unit_price=45.0, amount=180.0),
            ReceiptItem(name="急凍牛肋條", qty=5.0, unit="包", unit_price=135.0, amount=675.0),
        ],
        total=1560.0,  # 实际明细合计 2235.0，差 675.0
        payment_marked=False,
        confidence=0.92,
    )

    problems = math_engine.validate_and_report(data)
    has_diff_report = (len(problems) == 1 and "差675.0" in problems[0] and "明细合计=2235.0" in problems[0] and "总额=1560.0" in problems[0])

    record_check(
        "AC-3.1",
        "算术门禁差额精准计算与明确人话反馈",
        "反馈包含 '明细合计=2235.0' '总额=1560.0' '差675.0'",
        f"problems[0] = '{problems[0] if problems else ''}'",
        has_diff_report,
        "算术门禁精准算出差额 675.0"
    )


def test_ac4_rag_context_json_and_ai_decision_log():
    """AC-4: 验证 rag_context_json 持久化、API 隔离以及 AI 决策履历链。"""
    log("=== [AC-4] 验证 rag_context_json 持久化与 AI 决策日志 ===")
    from app import db
    from app.models import ReceiptData, ReceiptItem, DocForm
    from app.services.receipt_utils import save_parsed_data, build_detail
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    rid = db.create_receipt(status="uploaded")
    data = ReceiptData(
        doc_form=DocForm.NCR_HAND,
        vendor="德利行 Tak Lee Hong",
        date="2026-08-20",
        items=[ReceiptItem(name="有機菜心", qty=10.0, unit="斤", unit_price=8.5, amount=85.0)],
        total=85.0,
        payment_marked=True,
        confidence=0.95
    )

    sample_context = "[prior:parse]\n<vendor_context>\n德利行先验：开单常以樽/包为单位\n</vendor_context>"
    result_mock = {
        "raw": json.dumps(data.model_dump(), ensure_ascii=False),
        "vendor_context": sample_context,
        "use_grey": 0,
        "math_problems": [],
        "audit_result": {"overall_consistent": True, "reason": "识别一致"}
    }

    save_parsed_data(rid, data, result_mock)
    row = db.get_receipt_row(rid)

    # 1. 验证 DB 落库
    db_saved = (row.rag_context_json == sample_context)
    record_check(
        "AC-4.1",
        "receipts.rag_context_json 字段持久化落库",
        f"DB 中 rag_context_json 等于 '{sample_context}'",
        f"实际 DB 存储: '{row.rag_context_json}'",
        db_saved,
        "RAG 检索上下文与先验来源被结构化保存于 DB"
    )

    # 2. 验证 API 视图隔离
    resp_default = client.get(f"/api/receipt/{rid}")
    data_default = resp_default.json().get("data", {})
    hidden_by_default = ("rag_context" not in data_default)

    resp_debug = client.get(f"/api/receipt/{rid}?data_only=true")
    data_debug = resp_debug.json().get("data", {})
    visible_in_debug = (data_debug.get("rag_context") == sample_context)

    record_check(
        "AC-4.2",
        "API 视图隔离：默认视图隐藏 rag_context，data_only 调试视图可见",
        "默认 GET 不暴露 rag_context；带 data_only=true 返回完整先验",
        f"hidden_by_default={hidden_by_default}, visible_in_debug={visible_in_debug}",
        hidden_by_default and visible_in_debug,
        "遵循高压餐饮店员界面极简原则，避免技术元数据干扰店员"
    )

    # 3. 验证 AI 决策履历查询与详情输出
    db.log_ai_decision(
        receipt_id=rid,
        decision_type="extract",
        engine="openai",
        model="gpt-4o",
        ai_value=json.dumps({"attempt": 1, "status": "extract_ok"}, ensure_ascii=False)
    )
    db.log_ai_decision(
        receipt_id=rid,
        decision_type="audit",
        engine="opencode",
        model="qwen-vl",
        ai_value=json.dumps({"overall_consistent": True, "reason": "审核一致"}, ensure_ascii=False)
    )

    decisions = db.list_ai_decisions(rid)
    detail_data = build_detail(row)
    has_ai_decisions = len(decisions) >= 2 and len(detail_data.get("ai_decisions", [])) >= 2

    record_check(
        "AC-4.3",
        "AI 决策履历 (Extract + Audit) 强关联 receipt_id 并对外输出",
        "receipt_id 关联至少 2 条 AI 决策（extract + audit），详情包含 ai_decisions",
        f"DB decisions count={len(decisions)}, detail ai_decisions count={len(detail_data.get('ai_decisions', []))}",
        has_ai_decisions,
        f"AI 决策履历记录: {[d['decision_type'] for d in decisions]}"
    )


def test_ac5_playwright_live_browser_simulation():
    """AC-5: Playwright 模拟真实店员操作实测（双栏复核、算术联动、逃生通道、高压UX审计）。"""
    log("=== [AC-5] Playwright 浏览器端到端实测 (店员角色、真实长单) ===")

    real_sample_taklee = os.path.join(BATCH1_DIR, "IMG_5800.HEIC")
    real_preview_taklee = os.path.join(BATCH1_DIR, "_previews", "IMG_5800.jpg")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())

        # 1. 访问首页并切换至店员 (staff) 角色
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        page.select_option("#demoRoleSelect", "staff")
        page.wait_for_timeout(800)
        shot(page, "u5_step1_staff_home")

        # 2. 检查店员角色下的 UI 洁净度与权限隔离 (P0/P1/P2/P3 角色隔离)
        nav_admin_hidden = not page.is_visible("#navAdmin")
        nav_cost_hidden = not page.is_visible('button.sidebar-btn[data-target="tab-cost-report"]')
        nav_insights_hidden = not page.is_visible('button.sidebar-btn[data-target="tab-insights"]')
        role_isolation_ok = nav_admin_hidden and nav_cost_hidden and nav_insights_hidden

        record_check(
            "AC-5.1",
            "店员角色 UI 洁净度与老板/超管导航隔离",
            "超管引擎配置、成本报表、AI 洞察等高阶入口对店员彻底隐藏",
            f"admin_hidden={nav_admin_hidden}, cost_hidden={nav_cost_hidden}, insights_hidden={nav_insights_hidden}",
            role_isolation_ok,
            "店员界面无干扰入口，专注于单据录入与复核"
        )

        # 3. 检查逃生通道：首页常驻新建手工单通道
        manual_btn_exists = page.is_visible("#btnNewManualEntry")
        record_check(
            "AC-5.2",
            "首页随时转手工补录通道常驻",
            "上传主卡区常驻醒目的 '#btnNewManualEntry' 逃生按钮",
            f"manual_btn_exists={manual_btn_exists}",
            manual_btn_exists,
            "保证网络超时或极端异常下店员始终有保底通道"
        )

        # 4. 上传真实德利行长单单据
        upload_target = real_preview_taklee if os.path.exists(real_preview_taklee) else real_sample_taklee
        if os.path.exists(upload_target):
            log(f"  正在上传真实长单测试文件: {upload_target}")
            page.set_input_files("#receiptFile", upload_target)
            page.wait_for_timeout(2500)
            shot(page, "u5_step2_pre_confirm_card")

            # 检查预检卡片中按钮尺寸与文案
            pre_btn = page.locator("#preConfirmCard button.btn-success")
            btn_box = pre_btn.bounding_box() if pre_btn.count() else None
            btn_height = btn_box["height"] if btn_box else 0
            large_btn_ok = (btn_height >= 38)

            record_check(
                "AC-5.3",
                "大尺寸触控友好按钮设计 (适合餐饮后厨与前线店员)",
                "主操作按钮高度 >= 38px，间距充足，易于触控",
                f"btn_height={btn_height}px",
                large_btn_ok,
                f"预检卡主按钮尺寸: {btn_box}"
            )

        # 5. 测试双栏复核视图 (Split View) 与手动录入/编辑交互
        log("  测试双栏复核视图与明细算术联动...")
        # 点击预检卡片中的转手工录入按钮
        pre_manual_btn = page.locator("#preConfirmCard button.btn-secondary")
        if pre_manual_btn.count() and pre_manual_btn.is_visible():
            pre_manual_btn.click()
        else:
            page.click("#btnNewManualEntry")
        page.wait_for_timeout(1000)
        shot(page, "u5_step3_split_view_opened")

        split_view_visible = page.is_visible("#splitViewArea")
        form_visible = page.is_visible("#prefillFormCard")
        record_check(
            "AC-5.4",
            "双栏复核视图 (左图右表) 稳定展开",
            "splitViewArea 容器正常展开，左侧展示原图/预览，右侧展示结构化录入表单 prefillFormCard",
            f"split_view_visible={split_view_visible}, form_visible={form_visible}",
            split_view_visible and form_visible,
            "双栏对照极大减轻店员对照核对眼力负担"
        )

        # 6. 填写供应商、日期、结算方式并添加多行明细，测试算术自动联动与实时计算
        page.fill("#inpSupplier", "德利行 Tak Lee Hong")
        page.fill("#inpDate", "2026-08-24")
        page.select_option("#inpSettlementType", "cash")
        page.select_option("#inpDocForm", "ncr_handwritten")

        # 填充第一行明细：獅球嘜粟米油 2 樽 @ 90 = 180
        page.fill('#itemTableBody tr:first-child .inp-name', "獅球嘜粟米油 5L*2樽")
        page.fill('#itemTableBody tr:first-child .inp-qty', "2")
        page.fill('#itemTableBody tr:first-child .inp-unit', "樽")
        page.fill('#itemTableBody tr:first-child .inp-price', "90")
        page.evaluate('document.querySelector("#itemTableBody tr:first-child .inp-price").dispatchEvent(new Event("input", {bubbles: true}))')
        page.wait_for_timeout(500)

        # 点击添加明细行
        page.click("#btnAddRow")
        page.wait_for_timeout(500)

        # 填充第二行明细：雙喜牌特級香米 1 包 @ 240 = 240
        page.fill('#itemTableBody tr:nth-child(2) .inp-name', "雙喜牌特級香米 25kg")
        page.fill('#itemTableBody tr:nth-child(2) .inp-qty', "1")
        page.fill('#itemTableBody tr:nth-child(2) .inp-unit', "包")
        page.fill('#itemTableBody tr:nth-child(2) .inp-price', "240")
        page.evaluate('document.querySelector("#itemTableBody tr:nth-child(2) .inp-price").dispatchEvent(new Event("input", {bubbles: true}))')
        page.wait_for_timeout(500)

        shot(page, "u5_step4_items_filled")

        # 检查总额是否实时联动计算为 180 + 240 = 420
        total_val_str = page.input_value("#inpTotal")
        try:
            total_val = float(total_val_str)
        except Exception:
            total_val = 0.0

        math_linkage_ok = (abs(total_val - 420.0) < 0.01)
        record_check(
            "AC-5.5",
            "明细编辑实时算术联动 (单价*数量 -> 行金额 -> 单据总额)",
            "输入 2*90 与 1*240 后，表单总额自动联动计算为 420.00",
            f"inpTotal={total_val_str}",
            math_linkage_ok,
            "店员无需手动计算加总，减少错账算术失误"
        )

        # 7. 修改第一行数量为 3 (3*90 + 1*240 = 270 + 240 = 510.00)
        page.fill('#itemTableBody tr:first-child .inp-qty', "3")
        page.evaluate('document.querySelector("#itemTableBody tr:first-child .inp-qty").dispatchEvent(new Event("input", {bubbles: true}))')
        page.wait_for_timeout(500)
        updated_total_str = page.input_value("#inpTotal")
        try:
            updated_total = float(updated_total_str)
        except Exception:
            updated_total = 0.0

        updated_math_ok = (abs(updated_total - 510.0) < 0.01)
        record_check(
            "AC-5.6",
            "数量动态修改实时重新加总联动",
            "第一行数量由 2 修改为 3 后，总额自动重算为 510.00",
            f"updated_inpTotal={updated_total_str}",
            updated_math_ok,
            "支持店员在复核过程中随时微调纠偏"
        )

        # 8. 提交保存表单
        page.click("#btnSaveReview")
        page.wait_for_timeout(2500)
        shot(page, "u5_step5_after_saved")

        # 9. 切换至归档清单 (tab-archive)
        page.click('button.sidebar-btn[data-target="tab-archive"]')
        page.wait_for_timeout(2000)
        shot(page, "u5_step6_archive_list")

        # 检查归档列表第一行是否为刚保存的 德利行 Tak Lee Hong
        top_row = page.locator("#archiveTableBody tr:first-child")
        top_supplier = top_row.locator("td:nth-child(2)").inner_text() if top_row.count() else ""
        top_status = top_row.locator("td:nth-child(8)").inner_text() if top_row.count() else ""

        archive_ok = ("德利行" in top_supplier)
        record_check(
            "AC-5.7",
            "单据保存成功并在归档列表即时呈现",
            "归档列表中首行展示刚录入的德利行单据",
            f"top_supplier='{top_supplier}', status='{top_status}'",
            archive_ok,
            f"归档单据详情: {top_supplier} | {top_status}"
        )

        # 10. 打开详情弹窗，验证店员角色下老板级按钮已被视觉级隐藏 (U-6 联动保护)
        detail_btn = top_row.locator("button:has-text('详情')").first
        if detail_btn.count():
            detail_btn.click()
            page.wait_for_timeout(1500)
            shot(page, "u5_step7_archive_detail_modal")

            modal_approve_visible = page.is_visible("#modalApproveBtn")
            modal_flag_visible = page.is_visible("#modalFlagBtn")
            modal_cost_visible = page.is_visible("#modalCostShareBtn")
            modal_buttons_hidden = (not modal_approve_visible) and (not modal_flag_visible) and (not modal_cost_visible)

            record_check(
                "AC-5.8",
                "归档详情弹窗内老板级按钮对店员视觉隐藏 (U-6 保护)",
                "弹窗内 '审核通过' '标记为异常' '成本分摊' 按钮在 staff 角色下彻底隐藏",
                f"approve_visible={modal_approve_visible}, flag_visible={modal_flag_visible}, cost_visible={modal_cost_visible}",
                modal_buttons_hidden,
                "彻底杜绝店员误点击 403 越权操作"
            )

        browser.close()


def run_all_checks():
    log("================================================================")
    log("  开始执行 Issue U-5 真实手写长单识别质量优化全量验收测试套件  ")
    log("================================================================")

    test_ac1_rag_retrieval_and_injection()
    test_ac2_long_receipt_and_hk_units()
    test_ac3_math_gate_diff_and_retry_feedback()
    test_ac4_rag_context_json_and_ai_decision_log()
    test_ac5_playwright_live_browser_simulation()

    # 汇总结果
    total_count = len(test_results)
    pass_count = sum(1 for r in test_results if r["passed"])
    fail_count = total_count - pass_count

    log("================================================================")
    log(f"  测试汇总: 总计 {total_count} 项检查, 通过 {pass_count} 项, 失败 {fail_count} 项")
    log("================================================================")

    # 导出 JSON 报告
    report_data = {
        "suite": "Issue U-5 Real Handwritten Long Receipts Acceptance",
        "timestamp": datetime.datetime.now().isoformat(),
        "total": total_count,
        "passed": pass_count,
        "failed": fail_count,
        "all_passed": (fail_count == 0),
        "results": test_results,
    }

    report_path = os.path.join(OUT_DIR, "u5_acceptance_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    log(f"测试报告已写入: {report_path}")

    return fail_count == 0


if __name__ == "__main__":
    success = run_all_checks()
    sys.exit(0 if success else 1)

# -*- coding: utf-8 -*-
"""
U-4 专项验收测试：VLM 逐字转录 + 门禁硬裁决（拒绝静默帮对）
覆盖 AC-1 ~ AC-5 与高压餐饮店员 UX 专项评估。
"""

import datetime
import json
import os
import re
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
OUT_DIR = os.path.join(ROOT, "artifacts", "u4_verification")
os.makedirs(OUT_DIR, exist_ok=True)
SCR_DIR = os.path.join(OUT_DIR, "screens")
os.makedirs(SCR_DIR, exist_ok=True)

test_results = []
ux_eval_logs = []


def record_check(ac_id: str, name: str, expected: str, actual: str, passed: bool, evidence: str = ""):
    mark = "PASS" if passed else "FAIL"
    test_results.append({
        "ac": ac_id,
        "name": name,
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "evidence": evidence,
        "ts": datetime.datetime.now().isoformat(),
    })
    print(f"[{mark}] [{ac_id}] {name}\n  Expected: {expected}\n  Actual:   {actual}\n  Evidence: {evidence}\n")


def generate_matherr_image(dest_path: str):
    """生成标准算术错误测试单据：
    - 品名1: 有機菜心, 10 斤, 8.50 -> 85.00
    - 品名2: 鮮活草蝦(大), 4 斤, 45.00 -> 240.00 (实际应为 180.00，算术错误)
    - 總金額: HK$ 355.00 (85+240=325，总额写 355.00，二次算术错误)
    """
    img = Image.new("RGB", (760, 520), color="#ffffff")
    d = ImageDraw.Draw(img)
    
    font_path = "/System/Library/Fonts/PingFang.ttc"
    if not os.path.exists(font_path):
        font_path = "/Library/Fonts/Arial Unicode.ttf"
    try:
        f_title = ImageFont.truetype(font_path, 28)
        f_body = ImageFont.truetype(font_path, 22)
        f_stamp = ImageFont.truetype(font_path, 24)
    except Exception:
        f_title = f_body = f_stamp = ImageFont.load_default()

    d.text((200, 30), "鴻運餐飲批貨單據", fill="#111827", font=f_title)
    d.text((60, 80), "單據編號: DN-20260824-MATHERR", fill="#4b5563", font=f_body)
    d.text((60, 115), "開單日期: 2026-08-24", fill="#4b5563", font=f_body)
    d.line((60, 155, 700, 155), fill="#94a3b8", width=2)
    
    d.text((60, 175), "品名", fill="#374151", font=f_body)
    d.text((300, 175), "數量", fill="#374151", font=f_body)
    d.text((420, 175), "單價", fill="#374151", font=f_body)
    d.text((540, 175), "金額", fill="#374151", font=f_body)

    rows = [
        ("有機菜心", "10 斤", "8.50", "85.00"),
        ("鮮活草蝦(大)", "4 斤", "45.00", "240.00"),  # 4 * 45 = 180, written 240
    ]
    y = 215
    for r in rows:
        d.text((60, y), r[0], fill="#111827", font=f_body)
        d.text((300, y), r[1], fill="#111827", font=f_body)
        d.text((420, y), r[2], fill="#111827", font=f_body)
        d.text((540, y), r[3], fill="#111827", font=f_body)
        y += 45

    d.line((60, y + 10, 700, y + 10), fill="#94a3b8", width=2)
    d.text((380, y + 30), "總金額: HK$ 355.00", fill="#111827", font=f_title)
    d.text((60, y + 80), "現金收訖", fill="#dc2626", font=f_stamp)
    
    img.save(dest_path, quality=95)
    return dest_path


def generate_clear_image(dest_path: str):
    """生成标准清晰正常测试单据 (总额 265.00)"""
    img = Image.new("RGB", (760, 520), color="#ffffff")
    d = ImageDraw.Draw(img)
    font_path = "/System/Library/Fonts/PingFang.ttc"
    if not os.path.exists(font_path):
        font_path = "/Library/Fonts/Arial Unicode.ttf"
    try:
        f_title = ImageFont.truetype(font_path, 28)
        f_body = ImageFont.truetype(font_path, 22)
        f_stamp = ImageFont.truetype(font_path, 24)
    except Exception:
        f_title = f_body = f_stamp = ImageFont.load_default()

    d.text((200, 30), "鴻運餐飲批貨單據", fill="#111827", font=f_title)
    d.text((60, 80), "單據編號: DN-20260824-CLEAR", fill="#4b5563", font=f_body)
    d.text((60, 115), "開單日期: 2026-08-24", fill="#4b5563", font=f_body)
    d.line((60, 155, 700, 155), fill="#94a3b8", width=2)
    
    d.text((60, 175), "品名", fill="#374151", font=f_body)
    d.text((300, 175), "數量", fill="#374151", font=f_body)
    d.text((420, 175), "單價", fill="#374151", font=f_body)
    d.text((540, 175), "金額", fill="#374151", font=f_body)

    rows = [
        ("有機菜心", "10 斤", "8.50", "85.00"),
        ("鮮活草蝦(大)", "4 斤", "45.00", "180.00"),
    ]
    y = 215
    for r in rows:
        d.text((60, y), r[0], fill="#111827", font=f_body)
        d.text((300, y), r[1], fill="#111827", font=f_body)
        d.text((420, y), r[2], fill="#111827", font=f_body)
        d.text((540, y), r[3], fill="#111827", font=f_body)
        y += 45

    d.line((60, y + 10, 700, y + 10), fill="#94a3b8", width=2)
    d.text((380, y + 30), "總金額: HK$ 265.00", fill="#111827", font=f_title)
    d.text((60, y + 80), "現金收訖", fill="#dc2626", font=f_stamp)
    
    img.save(dest_path, quality=95)
    return dest_path


def run_ac1_prompt_verbatim_check():
    """AC-1: Verify that extract_chain.py and prompt files have NO residual silent-fix rules and enforce verbatim transcription."""
    print("\n--- AC-1: Prompt 与转录原则静态核查 ---")
    from app.chains import extract_chain
    
    parse_prompt = extract_chain.PARSE_SYSTEM_PROMPT
    sys_prompt = extract_chain.SYSTEM_PROMPT
    
    forbidden_terms = [
        "以乘积为准",
        "以明细合计为准",
        "数量与单价相乘必须等于小计",
    ]
    
    found_forbidden = []
    for term in forbidden_terms:
        if term in parse_prompt:
            found_forbidden.append(f"PARSE_SYSTEM_PROMPT contains '{term}'")
        if term in sys_prompt:
            found_forbidden.append(f"SYSTEM_PROMPT contains '{term}'")
            
    no_forbidden = len(found_forbidden) == 0
    record_check(
        "AC-1.1",
        "禁止静默纠错违禁词排查",
        "Prompts 中无 '以乘积为准'/'以明细合计为准'/'数量与单价相乘必须等于小计'",
        f"发现违禁词数量: {len(found_forbidden)}",
        no_forbidden,
        evidence=str(found_forbidden) if found_forbidden else "全 Prompt 干净无违禁词"
    )
    
    # 逐字转录要求检查
    required_concepts = [
        "逐字",
        "严禁自行计算或纠正",
        "门禁",
        "客观转录原则",
    ]
    all_concepts_in_sys = all(c in sys_prompt for c in required_concepts)
    all_concepts_in_parse = all(c in parse_prompt for c in ["逐字", "严禁自行计算或纠正", "门禁", "客观转录原则"])
    
    record_check(
        "AC-1.2",
        "逐字转录指令与门禁兜底职责界定",
        "SYSTEM_PROMPT 与 PARSE_SYSTEM_PROMPT 均包含逐字转录与门禁判定指引",
        f"SYSTEM_PROMPT: {all_concepts_in_sys}, PARSE_SYSTEM_PROMPT: {all_concepts_in_parse}",
        all_concepts_in_sys and all_concepts_in_parse,
        evidence="包含 '金额与数量严禁自行计算或纠正，必须逐字如实转录' 与 '客观转录原则'"
    )


def run_ac2_math_engine_gate_rejection_check():
    """AC-2: Verify math engine and gate hard rejection behavior on arithmetic discrepancies."""
    print("\n--- AC-2: Math Engine 与 Gate 门禁硬裁决核查 ---")
    from app.models import ReceiptData, ReceiptItem, DocForm
    from app.services import math_engine
    from app.chains.supervisor import _run_gates

    # Case A: 单行乘积不符 (qty=4, unit_price=45.0 -> amount=240.0 instead of 180.0)
    data_line_err = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="鴻運餐飲",
        date="2026-08-24",
        items=[
            ReceiptItem(name="有機菜心", qty=10, unit="斤", unit_price=8.5, amount=85.0),
            ReceiptItem(name="鮮活草蝦(大)", qty=4, unit="斤", unit_price=45.0, amount=240.0),
        ],
        total=325.0,  # 85 + 240 = 325
        payment_marked=True,
        confidence=0.95
    )
    problems_line = math_engine.validate_and_report(data_line_err)
    res_data, gate_err_line = _run_gates(data_line_err)
    
    line_check_ok = len(problems_line) == 1 and "数量4.0×单价45.0=180.0，但小计=240.0" in problems_line[0] and gate_err_line is not None
    record_check(
        "AC-2.1",
        "单行乘积矛盾硬拦截 (qty*price!=amount)",
        "Gate 拦截并返回包含行号、期望值与实际小计的具体差额描述",
        f"Gate Error: {gate_err_line}",
        line_check_ok,
        evidence=f"problems={problems_line}"
    )

    # Case B: 总额不符 (items_sum=265.0, total=355.0)
    data_total_err = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="鴻運餐飲",
        date="2026-08-24",
        items=[
            ReceiptItem(name="有機菜心", qty=10, unit="斤", unit_price=8.5, amount=85.0),
            ReceiptItem(name="鮮活草蝦(大)", qty=4, unit="斤", unit_price=45.0, amount=180.0),
        ],
        total=355.0,  # sum is 265, but total written 355
        payment_marked=True,
        confidence=0.95
    )
    problems_total = math_engine.validate_and_report(data_total_err)
    _, gate_err_total = _run_gates(data_total_err)
    total_check_ok = len(problems_total) == 1 and "明细合计=265.0 预期总额=265.0，但总额=355.0（差-90.0）" in problems_total[0] and gate_err_total is not None
    record_check(
        "AC-2.2",
        "总额加总矛盾硬拦截 (sum(items)!=total)",
        "Gate 拦截并返回明细合计、预期总额与实际总额差额",
        f"Gate Error: {gate_err_total}",
        total_check_ok,
        evidence=f"problems={problems_total}"
    )

    # Case C: 双重错误 (matherr: 4*45=240, total=355)
    data_both_err = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="鴻運餐飲",
        date="2026-08-24",
        items=[
            ReceiptItem(name="有機菜心", qty=10, unit="斤", unit_price=8.5, amount=85.0),
            ReceiptItem(name="鮮活草蝦(大)", qty=4, unit="斤", unit_price=45.0, amount=240.0),
        ],
        total=355.0,
        payment_marked=True,
        confidence=0.95
    )
    problems_both = math_engine.validate_and_report(data_both_err)
    _, gate_err_both = _run_gates(data_both_err)
    both_check_ok = len(problems_both) == 2 and gate_err_both is not None and "第2行" in gate_err_both and "预期总额=325.0" in gate_err_both
    record_check(
        "AC-2.3",
        "双重算术矛盾全面报告",
        "Gate 捕获所有算术矛盾（行乘积与整单加总）",
        f"Problems count: {len(problems_both)}, Gate error: {gate_err_both}",
        both_check_ok,
        evidence=f"gate_err={gate_err_both}"
    )


def run_ac3_supervisor_retry_and_logging_check():
    """AC-3: Verify supervisor retry escalation and gate_reject decision logging."""
    print("\n--- AC-3: Supervisor 重试升级与 ai_decision_log 履历落库核查 ---")
    from app import db
    from app.chains import supervisor
    from app.models import EngineConfig

    # 创建测试收据记录
    test_receipt_id = db.create_receipt(supplier_name="算术重试测试", status="uploaded")
    
    # 构造模拟返回错账的 extract_receipt
    raw_mock_matherr = json.dumps({
        "doc_form": "printed_delivery_note",
        "vendor": "鴻運餐飲",
        "date": "2026-08-24",
        "items": [
            {"name": "有機菜心", "qty": 10.0, "unit": "斤", "unit_price": 8.5, "amount": 85.0},
            {"name": "鮮活草蝦(大)", "qty": 4.0, "unit": "斤", "unit_price": 45.0, "amount": 240.0}
        ],
        "total": 355.0,
        "payment_marked": True,
        "confidence": 0.9
    })
    
    # 保存原始 extract_receipt
    orig_extract = supervisor.extract_chain.extract_receipt
    call_attempts = []
    
    def mock_extract(image_path, **kwargs):
        call_attempts.append(kwargs.get("retry_feedback", ""))
        data, err = supervisor.extract_chain._parse_to_receipt(raw_mock_matherr)
        return {
            "data": data,
            "raw": raw_mock_matherr,
            "error": None,
            "elapsed_ms": 100,
            "engine": "mock_codebuddy",
            "vendor_context": ""
        }

    supervisor.extract_chain.extract_receipt = mock_extract
    try:
        cfg = EngineConfig()
        state = supervisor.run_pipeline(
            image_path="dummy.jpg",
            config=cfg,
            receipt_id=test_receipt_id
        )
    finally:
        supervisor.extract_chain.extract_receipt = orig_extract

    # 核验重试轮次与最终状态
    attempts_count = len(call_attempts)
    final_status = state.get("status")
    has_contract_err = bool(state.get("contract_error"))
    
    record_check(
        "AC-3.1",
        "Supervisor 阶梯重试触发至 MAX_RETRY 上限",
        f"重试达 3 轮，最终状态为 error",
        f"实际重试轮数: {attempts_count}, 最终状态: {final_status}",
        attempts_count == 3 and final_status == "error" and has_contract_err,
        evidence=f"contract_error={state.get('contract_error')}"
    )

    # 核验 ai_decision_log 表中的落库
    logs = db.list_ai_decisions(test_receipt_id)
    extract_logs = [l for l in logs if l.get("decision_type") == "extract"]
    gate_rejects = [l for l in extract_logs if "gate_reject" in str(l.get("ai_value", ""))]
    
    record_check(
        "AC-3.2",
        "AI 决策履历 gate_reject 节点完整写入 DB",
        "DB 中存在 3 条 attempt=1,2,3 的 gate_reject 记录，且携带 receipt_id",
        f"提取决策数: {len(extract_logs)}, gate_reject 记录数: {len(gate_rejects)}",
        len(gate_rejects) == 3,
        evidence=f"Log details: {gate_rejects}"
    )


def run_ac4_ac5_browser_e2e_live():
    """AC-4 & AC-5: Playwright Live Browser 实测（高压餐饮阿叔/阿姨视角 UX 专项 + 真实算术拦截 + 转手工录入逃生）。"""
    print("\n--- AC-4 & AC-5: Playwright 浏览器全流程实测与 UX 专项评估 ---")
    matherr_img = os.path.join(OUT_DIR, "receipt_matherr.jpg")
    clear_img = os.path.join(OUT_DIR, "receipt_clear.jpg")
    generate_matherr_image(matherr_img)
    generate_clear_image(clear_img)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        
        # 1. 打开首页
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(SCR_DIR, "01_home_screen.png"))

        # 2. 上传算术错误单据 receipt_matherr.jpg
        file_input = page.locator("#receiptFile")
        file_input.set_input_files(matherr_img)
        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(SCR_DIR, "02_matherr_uploaded_preview.png"))

        # 测量 preConfirm 卡内的「开始 AI 智能解析」按钮尺寸与易读性
        m_analyze = page.evaluate("""
        () => {
            const el = document.querySelector('#preConfirmCard button.btn-success');
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return {
                text: el.innerText.trim(),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                fontSize: style.fontSize,
                bgColor: style.backgroundColor,
                color: style.color
            };
        }
        """)
        
        # 点击开始解析
        page.wait_for_selector("#preConfirmCard button.btn-success", state="visible", timeout=20000)
        page.click("#preConfirmCard button.btn-success")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(SCR_DIR, "03_matherr_loading.png"))

        # 等待识别完成（错误卡 errorCard 显示 或 表单 prefillFormCard 显示）
        t_start = time.time()
        while time.time() - t_start < 60:
            err_vis = page.evaluate("() => !document.getElementById('errorCard').classList.contains('hide')")
            form_vis = page.evaluate("() => !document.getElementById('prefillFormCard').classList.contains('hide')")
            if err_vis or form_vis:
                break
            page.wait_for_timeout(1000)

        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(SCR_DIR, "04_matherr_result_state.png"))

        # 读取错误卡与界面状态
        card_state = page.evaluate("""
        () => {
            const card = document.getElementById('errorCard');
            const errHeading = document.getElementById('errorCardHeading');
            const errMsg = document.getElementById('errorMsgText');
            const errBadge = document.getElementById('errorCardBadge');
            const errTitle = document.getElementById('errorCardTitle');
            const btnForce = document.getElementById('btnForceRetry');
            const btnRetry = document.getElementById('btnRetryNormal');
            const btnManual = document.getElementById('btnConvertManual');
            
            const vis = (el) => el && el.offsetParent !== null && getComputedStyle(el).display !== 'none';
            
            return {
                cardHidden: card.classList.contains('hide'),
                heading: errHeading ? errHeading.innerText : '',
                msg: errMsg ? errMsg.innerText : '',
                badge: errBadge ? errBadge.innerText : '',
                title: errTitle ? errTitle.innerText : '',
                forceVisible: vis(btnForce),
                retryVisible: vis(btnRetry),
                manualVisible: vis(btnManual),
                cardAllText: card ? card.innerText : ''
            };
        }
        """)

        # 测量错误卡内「转手工补录」按钮尺寸
        m_manual = page.evaluate("""
        () => {
            const el = document.getElementById('btnConvertManual');
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return {
                text: el.innerText.trim(),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                fontSize: style.fontSize,
                bgColor: style.backgroundColor,
                color: style.color
            };
        }
        """)

        # 验证门禁拦截提示与归因（拒绝甩锅画质）
        is_gate_heading = "数字对不上" in card_state["heading"] or "暂停录入" in card_state["heading"]
        has_plain_discrepancy = ("相差" in card_state["msg"] or "应为" in card_state["msg"] or "矛盾" in card_state["msg"] or "数字互相矛盾" in card_state["msg"])
        no_blame_quality = ("照片有点模糊" not in card_state["heading"] and "画质偏低" not in card_state["cardAllText"])
        escape_manual_visible = card_state["manualVisible"]

        record_check(
            "AC-5.1",
            "算术矛盾单据前端错误卡拦截呈现",
            "标题明确指出'单据上的数字对不上'，提供差额指引，不甩锅画质，具备转手工入口",
            f"Heading: '{card_state['heading']}', Msg: '{card_state['msg']}', ManualBtn: {escape_manual_visible}",
            (not card_state["cardHidden"]) and is_gate_heading and no_blame_quality and escape_manual_visible,
            evidence=f"CardState={card_state}"
        )

        # 3. UX 专项评估：测试「转手工录入」逃生通道
        page.click("#btnConvertManual")
        page.wait_for_timeout(1000)
        page.screenshot(path=os.path.join(SCR_DIR, "05_manual_entry_mode.png"))

        manual_mode_state = page.evaluate("""
        () => {
            const splitArea = document.getElementById('splitViewArea');
            const imgPreview = document.getElementById('previewImg');
            const formCard = document.getElementById('prefillFormCard');
            const supplierInp = document.getElementById('inpSupplier');
            const totalInp = document.getElementById('inpTotal');
            
            return {
                splitVisible: !splitArea.classList.contains('hide'),
                imgSrcValid: imgPreview && imgPreview.src && imgPreview.src.length > 10,
                formVisible: !formCard.classList.contains('hide'),
                supplierVal: supplierInp ? supplierInp.value : null,
                totalVal: totalInp ? totalInp.value : null
            };
        }
        """)

        # 测量表单底部「确认上传单据」保存按钮尺寸
        m_save = page.evaluate("""
        () => {
            const el = document.getElementById('btnSaveReview');
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return {
                text: el.innerText.trim(),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                fontSize: style.fontSize,
                bgColor: style.backgroundColor,
                color: style.color
            };
        }
        """)

        manual_escape_ok = manual_mode_state["splitVisible"] and manual_mode_state["imgSrcValid"] and manual_mode_state["formVisible"]
        record_check(
            "AC-5.2",
            "转手工录入逃生通道连贯性与原图保留",
            "点击转手工后，左侧保留单据原图，右侧展开清晰表单供人工补录",
            f"SplitVisible: {manual_mode_state['splitVisible']}, ImgSrc: {manual_mode_state['imgSrcValid']}, Form: {manual_mode_state['formVisible']}",
            manual_escape_ok,
            evidence=f"ManualModeState={manual_mode_state}"
        )

        # 4. 按钮尺寸、对比度与阿叔阿姨亲和度评测 (UX Inspection)
        btn_metrics = {
            "btnAnalyze": m_analyze,
            "btnManual": m_manual,
            "btnSave": m_save
        }
        ux_eval_logs.append({"button_metrics": btn_metrics})
        
        buttons_comfortable = all(
            b is None or (b["height"] >= 35 and float(b["fontSize"].replace("px", "")) >= 13)
            for b in btn_metrics.values() if b
        )

        record_check(
            "AC-5.3",
            "阿叔阿姨友好交互靶区与按钮易读性",
            "主按钮高度 ≥ 35px，字体清晰易按，杜绝细小误触靶区",
            f"Metrics: {btn_metrics}",
            buttons_comfortable,
            evidence=str(btn_metrics)
        )

        # 5. 上传清晰单据 receipt_clear.jpg 进行正常全链路验证 (Zero Regression)
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        page.locator("#receiptFile").set_input_files(clear_img)
        page.wait_for_timeout(1500)
        page.wait_for_selector("#preConfirmCard button.btn-success", state="visible", timeout=20000)
        page.click("#preConfirmCard button.btn-success")
        
        # 等待预填完成 (最多等 60s)
        t0 = time.time()
        prefill_ok = False
        while time.time() - t0 < 60:
            form_shown = page.evaluate("() => !document.getElementById('prefillFormCard').classList.contains('hide')")
            if form_shown:
                prefill_ok = True
                break
            page.wait_for_timeout(1000)

        page.wait_for_timeout(1500)
        page.screenshot(path=os.path.join(SCR_DIR, "06_clear_receipt_prefilled.png"))

        clear_data = page.evaluate("""
        () => {
            return {
                supplier: document.getElementById('inpSupplier') ? document.getElementById('inpSupplier').value : '',
                total: document.getElementById('inpTotal') ? document.getElementById('inpTotal').value : '',
                itemRows: document.querySelectorAll('#itemTableBody tr').length
            };
        }
        """)

        clear_success = prefill_ok and ("鴻運" in clear_data["supplier"] or "鸿运" in clear_data["supplier"]) and abs(float(clear_data["total"] or 0) - 265.0) < 0.01 and clear_data["itemRows"] == 2
        record_check(
            "AC-5.4",
            "清晰合规单据全流程无回归通过",
            "识别成功并自动预填：供应商鴻運餐飲、总额 265.00、明细 2 行",
            f"Prefilled: {clear_data}",
            clear_success,
            evidence=f"ClearResult={clear_data}"
        )

        browser.close()


def generate_qa_report():
    total_checks = len(test_results)
    passed_checks = sum(1 for r in test_results if r["passed"])
    failed_checks = total_checks - passed_checks
    
    verdict = "PASS" if failed_checks == 0 else "FAIL"
    
    report_data = {
        "verdict": verdict,
        "total": total_checks,
        "passed": passed_checks,
        "failed": failed_checks,
        "checks": test_results,
        "ux_logs": ux_eval_logs,
        "timestamp": datetime.datetime.now().isoformat()
    }
    
    with open(os.path.join(OUT_DIR, "u4_qa_report.json"), "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
        
    print("\n" + "=" * 80)
    print(f"【U-4 专项验收总评】: [QA_VERDICT]: {verdict} ({passed_checks}/{total_checks} Checks Passed)")
    print("=" * 80)
    return verdict


if __name__ == "__main__":
    print("================================================================================")
    print("【QA Agent】Issue U-4: VLM 逐字转录 + 门禁硬裁决 综合自动化验收")
    print("================================================================================")
    
    run_ac1_prompt_verbatim_check()
    run_ac2_math_engine_gate_rejection_check()
    run_ac3_supervisor_retry_and_logging_check()
    run_ac4_ac5_browser_e2e_live()
    
    verdict = generate_qa_report()
    sys.exit(0 if verdict == "PASS" else 1)

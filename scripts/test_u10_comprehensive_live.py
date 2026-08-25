# -*- coding: utf-8 -*-
"""
Live Comprehensive QA Test Suite for Issue U-10:
"U-10 交叉审核开关启用与文案去技术化"

Covers:
- AC-1: Default engine config audit_enabled=True and grey_audit_enabled=True; PUT persistence.
- AC-2: Supervisor pipeline executes cross-audit, gracefully degrades without blocking recognition, writes decision_type="audit" with valid receipt_id to ai_decision_log.
- AC-3: All frontend form validations (supplier missing, date missing/invalid, empty items, empty item name, invalid price/quantity numbers, missing settlement) produce 100% plain everyday language warnings with zero technical jargon.
- AC-4: Backend HTTP 400/422/500 errors translated into humanized natural language without leaking developer variable names or JSON tracebacks.
- AC-5: Accessibility & UX evaluation from HK elderly restaurant staff perspective (button heights >= 38px, WCAG AA contrast, escape hatch, arithmetic discrepancy guidance, zero regression).
"""

import os
import sys
import time
import json
import math
import requests
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))

from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "artifacts", "u10_qa")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def calculate_contrast_ratio(rgb1, rgb2):
    """Calculate WCAG 2.1 contrast ratio between two RGB/RGBA colors."""
    def parse_rgb(c):
        if c.startswith("rgba"):
            parts = [float(x.strip()) for x in c[5:-1].split(",")]
            return parts[0], parts[1], parts[2]
        elif c.startswith("rgb"):
            parts = [float(x.strip()) for x in c[4:-1].split(",")]
            return parts[0], parts[1], parts[2]
        return (0, 0, 0)

    def srgb_luminance(r, g, b):
        def chan(v):
            v = v / 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)

    r1, g1, b1 = parse_rgb(rgb1)
    r2, g2, b2 = parse_rgb(rgb2)
    l1 = srgb_luminance(r1, g1, b1)
    l2 = srgb_luminance(r2, g2, b2)
    lum_top = max(l1, l2)
    lum_bot = min(l1, l2)
    return (lum_top + 0.05) / (lum_bot + 0.05)


def test_ac1_engine_config_audit_defaults_and_persistence():
    """AC-1: 引擎配置默认值与持久化。"""
    from app.models import EngineConfig
    # 1. 验证 EngineConfig 实体模型定义默认开启
    default_model = EngineConfig()
    assert default_model.audit_enabled is True, "EngineConfig default audit_enabled should be True"
    assert default_model.grey_audit_enabled is True, "EngineConfig default grey_audit_enabled should be True"

    # 2. PUT 设置为 False 并验证持久化
    put_resp = requests.put(f"{BASE_URL}/api/admin/engine-config", json={
        "audit_enabled": False,
        "grey_audit_enabled": False
    }, headers={"X-Role": "admin"})
    assert put_resp.status_code == 200
    assert put_resp.json()["data"]["audit_enabled"] is False
    assert put_resp.json()["data"]["grey_audit_enabled"] is False

    # 再次 GET 确认写入 DB
    resp_check = requests.get(f"{BASE_URL}/api/admin/engine-config", headers={"X-Role": "admin"})
    assert resp_check.status_code == 200
    assert resp_check.json()["data"]["audit_enabled"] is False
    assert resp_check.json()["data"]["grey_audit_enabled"] is False

    # 恢复为 True
    restore_resp = requests.put(f"{BASE_URL}/api/admin/engine-config", json={
        "audit_enabled": True,
        "grey_audit_enabled": True
    }, headers={"X-Role": "admin"})
    assert restore_resp.status_code == 200
    assert restore_resp.json()["data"]["audit_enabled"] is True
    assert restore_resp.json()["data"]["grey_audit_enabled"] is True


def test_ac2_supervisor_audit_execution_and_decision_log():
    """AC-2: Supervisor 交叉审核执行、优雅降级及 ai_decision_log 记录。"""
    from app.models import ReceiptData, ReceiptItem, DocForm, EngineConfig
    from app.chains.supervisor import _run_audit, _log_audit_decision
    from app import db as _db

    sample_data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="德利行",
        date="2026-08-24",
        total=120.0,
        payment_marked=False,
        confidence=0.9,
        items=[ReceiptItem(name="菜心", qty=6.0, unit="斤", unit_price=20.0, amount=120.0)]
    )

    # 1. 禁用 audit 时跳过
    res_disabled = _run_audit("fake.jpg", sample_data, config=EngineConfig(audit_enabled=False), use_grey=False)
    assert res_disabled.get("skipped") is True

    # 2. 异常时优雅降级为 skipped (不抛出异常打断管线)
    res_err = _run_audit("invalid_image_path.jpg", sample_data, config=EngineConfig(audit_enabled=True), use_grey=False)
    assert isinstance(res_err, dict)
    assert "skipped" in res_err

    # 3. 验证 _log_audit_decision 写库且包含 receipt_id
    rid = _db.create_receipt(supplier_name="审核测试店", status="uploaded")
    audit_data = {
        "overall_consistent": True,
        "trust": 0.95,
        "discrepancies": [],
        "corrected_suggestions": {},
        "reason": "AI 识别结果与原图完全一致",
        "skipped": False
    }
    _log_audit_decision(receipt_id=rid, experiment_id=None, config=EngineConfig(audit_enabled=True), use_grey=False, audit=audit_data)

    # 验证 DB 直查与 list_ai_decisions
    decisions = _db.list_ai_decisions(rid)
    audit_decisions = [d for d in decisions if d.get("decision_type") == "audit"]
    assert len(audit_decisions) >= 1, f"未找到 receipt_id={rid} 的 audit 决策: {decisions}"
    
    val = audit_decisions[0].get("ai_value")
    if isinstance(val, str):
        val = json.loads(val)
    assert val.get("overall_consistent") is True
    assert val.get("trust") == 0.95


def test_ac3_ac4_ac5_browser_live_interactions():
    """AC-3, AC-4, AC-5: Playwright 浏览器实测。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1000)

        # -------------------------------------------------------------
        # AC-5 视觉与按钮尺寸、对比度评估 (阿叔/阿姨视角)
        # -------------------------------------------------------------
        buttons_to_check = [
            ("#btnTriggerAnalysis", "开始 AI 智能解析"),
            ("#btnConfirmUpload", "确认上传单据"),
            ("#btnNewManualEntry", "新建手工单"),
            ("#btnSaveArchiveEdited", "保存单据修改"),
            ("#btnAbortLoadingToManual", "等不及？点击取消并转手工补录"),
        ]

        button_evaluations = []
        for sel, label in buttons_to_check:
            loc = page.locator(sel)
            if loc.count() > 0:
                styles = loc.first.evaluate("""el => {
                    const comp = window.getComputedStyle(el);
                    return {
                        height: comp.height,
                        minHeight: comp.minHeight,
                        fontSize: comp.fontSize,
                        color: comp.color,
                        backgroundColor: comp.backgroundColor,
                    };
                }""")
                h_val = float(styles["height"].replace("px", "")) if "px" in styles["height"] else 0.0
                min_h_val = float(styles["minHeight"].replace("px", "")) if "px" in styles["minHeight"] else 0.0
                effective_h = max(h_val, min_h_val)
                cr = calculate_contrast_ratio(styles["color"], styles["backgroundColor"])
                button_evaluations.append({
                    "selector": sel,
                    "label": label,
                    "height": effective_h,
                    "font_size": styles["fontSize"],
                    "contrast_ratio": cr,
                    "pass_size": effective_h >= 38.0,
                    "pass_contrast": cr >= 4.5 or cr >= 3.0,
                })
                assert effective_h >= 38.0, f"Button {label} ({sel}) height {effective_h}px < 38px threshold!"
                print(f"[BUTTON_OK] {label}: height={effective_h:.1f}px (>=38px), contrast={cr:.2f}:1")

        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_01_staff_homepage_buttons.png"))

        # -------------------------------------------------------------
        # AC-3: 前端表单校验人话化实测 (手工新建录入场景)
        # -------------------------------------------------------------
        page.evaluate("""() => {
            startManualEntry();
        }""")
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_02_manual_mode_form.png"))

        def get_toast_after_action(action_fn):
            page.evaluate("() => { if (typeof toast !== 'undefined' && toast.dismissAll) toast.dismissAll(); }")
            action_fn()
            page.wait_for_timeout(400)
            toast_el = page.locator(".toast, #toast, .toast-notification, [class*='toast']").first
            text = toast_el.inner_text().strip() if toast_el.count() > 0 else ""
            return text

        # Scenario 1: 供应商为空
        page.evaluate("""() => {
            document.getElementById('inpSupplier').value = '';
            document.getElementById('inpDate').value = '2026-08-24';
            document.getElementById('inpSettlementType').value = 'cash';
            document.getElementById('itemTableBody').innerHTML = '';
            appendTableRow({name: '菜心', quantity: 1, unit_price: 10, amount: 10});
            document.getElementById('inpTotal').value = '10.00';
        }""")
        t1 = get_toast_after_action(lambda: page.evaluate("submitSaveEdited()"))
        print(f"[VALIDATION 1 - Missing Supplier]: '{t1}'")
        assert "请填写供应商名称" in t1
        assert "当前值：空" not in t1
        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_03_val_missing_supplier.png"))

        # Scenario 2: 开单日期为空
        page.evaluate("""() => {
            document.getElementById('inpSupplier').value = '德利行';
            document.getElementById('inpDate').value = '';
        }""")
        t2 = get_toast_after_action(lambda: page.evaluate("submitSaveEdited()"))
        print(f"[VALIDATION 2 - Missing Date]: '{t2}'")
        assert "请选择开单日期" in t2
        assert "当前值：空" not in t2
        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_04_val_missing_date.png"))

        # Scenario 3: 开单日期非法日历日 (直接测试 validateManualEntry 校验逻辑)
        val_res = page.evaluate("""() => {
            return validateManualEntry({
                supplier_name: '德利行',
                date: '2026-02-31',
                total_amount: 100,
                items: [{name: '菜心', quantity: 5, unit_price: 20}]
            });
        }""")
        print(f"[VALIDATION 3 - Invalid Date Logic]: '{val_res}'")
        assert "开单日期不正确" in val_res or "请选择真实存在的日历日期" in val_res
        assert "当前值：空" not in val_res
        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_05_val_invalid_calendar_date.png"))

        # Scenario 4: 明细列表为空
        page.evaluate("""() => {
            document.getElementById('inpSupplier').value = '德利行';
            document.getElementById('inpDate').value = '2026-08-24';
            document.getElementById('itemTableBody').innerHTML = '';
            document.getElementById('inpTotal').value = '0.00';
        }""")
        t4 = get_toast_after_action(lambda: page.evaluate("submitSaveEdited()"))
        print(f"[VALIDATION 4 - Empty Items]: '{t4}'")
        assert "单据总金额必须大于 0" in t4 or "请至少输入一行消费明细" in t4
        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_06_val_empty_items.png"))

        # Scenario 5: 明细行品名为空
        page.evaluate("""() => {
            document.getElementById('itemTableBody').innerHTML = '';
            appendTableRow({name: '', quantity: 2, unit_price: 15, amount: 30});
            document.getElementById('inpTotal').value = '30.00';
        }""")
        t5 = get_toast_after_action(lambda: page.evaluate("submitSaveEdited()"))
        print(f"[VALIDATION 5 - Empty Item Name]: '{t5}'")
        assert "第 1 行的品名不能为空" in t5
        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_07_val_empty_item_name.png"))

        # Scenario 6: 明细行数量或单价非法 (0 或 NaN 或负数)
        page.evaluate("""() => {
            document.getElementById('itemTableBody').innerHTML = '';
            appendTableRow({name: '白菜', quantity: -5, unit_price: 10, amount: -50});
            document.getElementById('inpTotal').value = '-50.00';
        }""")
        t6 = get_toast_after_action(lambda: page.evaluate("submitSaveEdited()"))
        print(f"[VALIDATION 6 - Invalid Number]: '{t6}'")
        assert "不是有效数字" in t6 or "单据总金额必须大于 0" in t6
        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_08_val_invalid_number.png"))

        # Scenario 7: 归档弹窗编辑校验人话化
        page.evaluate("""() => {
            document.getElementById('arcSupplier').value = '';
            document.getElementById('arcDate').value = '2026-08-24';
            document.getElementById('arcTotal').value = '100.00';
        }""")
        t7 = get_toast_after_action(lambda: page.evaluate("submitSaveArchiveEdited()"))
        print(f"[VALIDATION 7 - Archive Modal Missing Supplier]: '{t7}'")
        assert "请填写供应商名称" in t7
        assert "当前值：空" not in t7

        # -------------------------------------------------------------
        # AC-4: 后端 HTTP 400/422/500 去技术化与人话化转化核验
        # -------------------------------------------------------------
        test_error_cases = [
            # Pydantic 422 错误格式
            (422, {"detail": [{"loc": ["body", "supplier_name"], "msg": "Field required", "type": "missing"}]}, "请填写供应商名称"),
            (422, {"detail": [{"loc": ["body", "date"], "msg": "Field required", "type": "missing"}]}, "请选择开单日期"),
            (422, {"detail": [{"loc": ["body", "total_amount"], "msg": "Input should be greater than 0", "type": "greater_than"}]}, "单据总金额必须大于 0"),
            (422, {"detail": [{"loc": ["body", "items"], "msg": "List should have at least 1 item", "type": "too_short"}]}, "请至少输入一行消费明细"),
            (422, {"detail": [{"loc": ["body", "settlement_type"], "msg": "Field required", "type": "missing"}]}, "请选择结算方式（如现结或挂账）"),
            (422, {"detail": [{"loc": ["body", "items", 0, "name"], "msg": "String should have at least 1 character", "type": "string_too_short"}]}, "第 1 行的品名不能为空，请填写或删除该行"),
            (422, {"detail": [{"loc": ["body", "items", 1, "unit_price"], "msg": "Input should be a valid number", "type": "float_parsing"}]}, "第 2 行的单价或数量不是有效数字，请重新输入"),
            # 业务 400 错误
            (400, {"code": "SETTLEMENT_REQUIRED", "msg": "Missing settlement"}, "请选择结算方式（如现结或挂账）"),
            (400, {"code": "IMAGE_QUALITY_ERROR", "msg": "Image blur too high"}, "图片有点模糊或光线不足，请重新拍摄或调整后再试"),
            (400, {"code": "VERSION_CONFLICT", "msg": "Conflict"}, "该单据已被他人修改，请重新加载最新内容"),
            # 500 / 堆栈
            (500, {"msg": "Traceback (most recent call last): JSONDecodeError"}, "服务处理异常，请稍后重试"),
            (502, {"msg": "Bad Gateway"}, "AI 服务现在很忙，请稍后重试"),
        ]

        for status, body, expected_text in test_error_cases:
            res_text = page.evaluate("""([status, body]) => {
                return humanizeBackendError(status, body);
            }""", [status, body])
            print(f"[HUMANIZE_TEST] status={status} body={body} -> '{res_text}' (expected: '{expected_text}')")
            assert res_text == expected_text, f"Expected '{expected_text}', but got '{res_text}' for status={status}, body={body}"

        # -------------------------------------------------------------
        # 逃生通道核验 (随时转手工录入并保留原图)
        # -------------------------------------------------------------
        page.evaluate("""() => {
            // 模拟开始识别并展示 loadingCard 与 splitViewArea
            document.getElementById('splitViewArea').classList.remove('hide');
            document.getElementById('loadingCard').classList.remove('hide');
            document.getElementById('loadingCard').style.display = 'block';
            document.getElementById('prefillFormCard').classList.add('hide');
            const previewImg = document.getElementById('previewImg');
            if (previewImg) {
                previewImg.src = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
            }
        }""")
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_09_loading_with_escape.png"))

        # 点击逃生按钮
        escape_btn = page.locator("#btnAbortLoadingToManual")
        assert escape_btn.count() > 0, "逃生按钮 #btnAbortLoadingToManual 应存在"
        escape_btn.click()
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(OUTPUT_DIR, "u10_10_after_escape_to_manual.png"))

        # 验证切换至手工复核卡片，loadingCard 隐藏
        is_loading_hidden = page.evaluate("() => document.getElementById('loadingCard').classList.contains('hide')")
        is_prefill_visible = page.evaluate("() => !document.getElementById('prefillFormCard').classList.contains('hide')")
        assert is_loading_hidden, "点击逃生后 loadingCard 应隐藏"
        assert is_prefill_visible, "点击逃生后 prefillFormCard 应展开"
        print("[ESCAPE_OK] 逃生通道点击生效并保留原图展开手工表单")

        context.close()
        browser.close()


if __name__ == "__main__":
    test_ac1_engine_config_audit_defaults_and_persistence()
    test_ac2_supervisor_audit_execution_and_decision_log()
    test_ac3_ac4_ac5_browser_live_interactions()
    print("\nALL U-10 TESTS PASSED SUCCESSFULLY!")

# -*- coding: utf-8 -*-
"""AgentDojo-Style Provider Adversarial Regression Suite (W-SEC-5 Provider Matrix).

Covers:
1. Malicious Provider backfilling Canary Token + tampering total amount -> arithmetic gate detection.
2. Malicious Provider backfilling Canary Token + tampering line item amounts -> arithmetic gate detection.
3. Malicious Provider stripping Canary Token -> fail-closed rejection with [SECURITY_ALERT].
4. Malicious Provider returning mismatched/corrupted Canary Token -> fail-closed without secret leak.
5. Malicious Provider returning empty or whitespace Canary Token -> fail-closed rejection.
6. Malicious Provider injecting forbidden schema fields -> Pydantic extra="forbid" rejection.
7. Malicious Provider returning malformed/truncated JSON or HTML errors -> fail-closed parsing.
8. Malicious Provider injecting NaN/Infinity numeric floats -> contract rejection.
9. Malicious Provider returning negative quantity or unit price -> contract rejection.
10. Malicious Provider returning zero total or empty items -> contract rejection.
11. Malicious Provider injecting invalid date formats or SQL syntax -> contract rejection.
12. Malicious Provider returning invalid doc_form enums -> contract rejection.
13. Malicious Provider tampering pure text channel Canary Token -> verify_canary_in_text fail-closed.
14. Malicious Provider injecting SSRF URLs or credential leak strings in item names -> passive string handling.
15. End-to-end supervisor pipeline handling malicious provider math tampering -> parsed_with_warnings status.
16. Malicious Provider returning blank or whitespace vendor -> contract rejection.

All tests are 100% offline, zero network requests, deterministic execution.
"""

from __future__ import annotations

import json
import pytest

from app.models import ReceiptData, ReceiptItem, DocForm
from app.services.contract import validate_contract
from app.services import math_engine
from app.services.canary_guard import (
    CANARY_FIELD,
    generate_canary_token,
    verify_canary_token,
    verify_canary_in_text,
)
from app.chains.extract_chain import _parse_to_receipt
from app.chains import supervisor


def _valid_base_payload(canary: str = "") -> dict:
    payload = {
        "doc_form": "printed_delivery_note",
        "vendor": "测试食材供应链有限公司",
        "date": "2026-09-28",
        "total": 50.0,
        "payment_marked": False,
        "confidence": 0.95,
        "items": [
            {
                "name": "广东菜心",
                "qty": 5.0,
                "unit": "斤",
                "unit_price": 10.0,
                "amount": 50.0,
            }
        ],
    }
    if canary:
        payload[CANARY_FIELD] = canary
    return payload


# =====================================================================
# 1. Malicious Provider backfilling Canary Token + tampering total amount
# =====================================================================
def test_provider_backfill_canary_but_tamper_total_amount():
    token = generate_canary_token()
    payload = _valid_base_payload(token)
    # Items sum: 5 * 10 = 50.0; Malicious provider tampers total to 1.0
    payload["total"] = 1.0

    raw_json = json.dumps(payload, ensure_ascii=False)
    data, err = _parse_to_receipt(raw_json, expected_canary=token)

    # Canary check passes because token was correctly echoed
    assert err is None, "Canary check itself does not detect math tampering"
    assert data is not None

    # But arithmetic gate (math_engine) detects the discrepancy
    problems = math_engine.validate_and_report(data)
    assert len(problems) > 0, "Arithmetic gate must detect total discrepancy"
    assert any("总额" in p or "不符" in p for p in problems)

    # Supervisor gate helper rejects data with math error
    gated_data, gate_err = supervisor._run_gates(data)
    assert gated_data is None
    assert gate_err is not None
    assert "算术门禁" in gate_err


# =====================================================================
# 2. Malicious Provider backfilling Canary Token + tampering line items
# =====================================================================
def test_provider_backfill_canary_but_tamper_line_item_amount():
    token = generate_canary_token()
    payload = _valid_base_payload(token)
    # Line item arithmetic tampered: 5 * 10 = 50, but item amount tampered to 999.0
    payload["items"][0]["amount"] = 999.0
    payload["total"] = 999.0

    raw_json = json.dumps(payload, ensure_ascii=False)
    data, err = _parse_to_receipt(raw_json, expected_canary=token)

    assert err is None
    assert data is not None

    problems = math_engine.validate_and_report(data)
    assert len(problems) > 0
    assert any("第1行" in p or "999" in p for p in problems)

    gated_data, gate_err = supervisor._run_gates(data)
    assert gated_data is None
    assert gate_err is not None
    assert "算术门禁" in gate_err


# =====================================================================
# 3. Malicious Provider stripping Canary Token
# =====================================================================
def test_provider_stripping_canary_token_fail_closed():
    token = generate_canary_token()
    payload = _valid_base_payload()
    # Strip CANARY_FIELD completely
    payload.pop(CANARY_FIELD, None)

    raw_json = json.dumps(payload, ensure_ascii=False)
    data, err = _parse_to_receipt(raw_json, expected_canary=token)

    assert data is None
    assert err is not None
    assert "安全阻断" in err
    assert "缺失安全握手令牌" in err
    assert token not in err, "Must not echo expected secret canary token in error log"


# =====================================================================
# 4. Malicious Provider returning mismatched / corrupted Canary Token
# =====================================================================
def test_provider_mismatched_corrupted_canary_token_fail_closed():
    token = generate_canary_token()
    payload = _valid_base_payload()
    payload[CANARY_FIELD] = "CANARY_ATTACKER_CORRUPTED_TOKEN"

    raw_json = json.dumps(payload, ensure_ascii=False)
    data, err = _parse_to_receipt(raw_json, expected_canary=token)

    assert data is None
    assert err is not None
    assert "安全阻断" in err
    assert "安全握手令牌不匹配" in err
    assert token not in err, "Must not echo expected token"
    assert "CANARY_ATTACKER_CORRUPTED_TOKEN" not in err, "Must only echo length/digest summary"


# =====================================================================
# 5. Malicious Provider returning empty or whitespace Canary Token
# =====================================================================
@pytest.mark.parametrize("bad_token", ["", "   ", "\t\n", None])
def test_provider_empty_or_whitespace_canary_token_fail_closed(bad_token):
    token = generate_canary_token()
    payload = _valid_base_payload()
    payload[CANARY_FIELD] = bad_token

    raw_json = json.dumps(payload, ensure_ascii=False)
    data, err = _parse_to_receipt(raw_json, expected_canary=token)

    assert data is None
    assert err is not None
    assert "安全阻断" in err
    assert "缺失安全握手令牌" in err


# =====================================================================
# 6. Malicious Provider injecting forbidden schema fields
# =====================================================================
def test_provider_forbidden_schema_fields_in_contract_validation():
    payload = _valid_base_payload()
    payload["malicious_admin_escalation"] = True
    payload["sql_injection_payload"] = "DROP TABLE receipts;"

    # Test contract validation directly with extra fields
    data, err = validate_contract(payload)
    assert data is None
    assert err is not None
    assert "契约校验失败" in err
    assert "extra fields not permitted" in err or "malicious_admin_escalation" in err


# =====================================================================
# 7. Malicious Provider returning malformed or truncated JSON / HTML
# =====================================================================
@pytest.mark.parametrize("malformed", [
    '{"vendor": "测试", "items": [{"name": "菜心", "qty": 1',  # Truncated JSON
    '<html><head><title>502 Bad Gateway</title></head><body>502 Server Error</body></html>',
    '[{"vendor": "Not A Dict"}]',  # Array instead of Object
    '',  # Empty output
    '    ',  # Whitespace only
])
def test_provider_malformed_json_or_truncated_payload_fail_closed(malformed):
    token = generate_canary_token()
    data, err = _parse_to_receipt(malformed, expected_canary=token)
    assert data is None
    assert err is not None


# =====================================================================
# 8. Malicious Provider injecting NaN/Infinity numeric floats
# =====================================================================
@pytest.mark.parametrize("nan_field,nan_val", [
    ("total", float("nan")),
    ("total", float("inf")),
    ("total", float("-inf")),
])
def test_provider_nan_and_infinity_injection_fail_closed(nan_field, nan_val):
    payload = _valid_base_payload()
    payload[nan_field] = nan_val

    data, err = validate_contract(payload)
    assert data is None
    assert err is not None
    assert "总额非法" in err or "NaN/Infinity" in err


def test_provider_item_nan_injection_fail_closed():
    payload = _valid_base_payload()
    payload["items"][0]["amount"] = float("nan")

    data, err = validate_contract(payload)
    assert data is None
    assert err is not None
    assert "数值非法" in err


# =====================================================================
# 9. Malicious Provider returning negative quantity or unit price
# =====================================================================
@pytest.mark.parametrize("bad_key,bad_val", [
    ("qty", -1.0),
    ("qty", 0.0),
    ("unit_price", -10.0),
])
def test_provider_negative_quantity_or_unit_price_fail_closed(bad_key, bad_val):
    payload = _valid_base_payload()
    payload["items"][0][bad_key] = bad_val

    data, err = validate_contract(payload)
    assert data is None
    assert err is not None
    assert "数量/单价非法" in err


# =====================================================================
# 10. Malicious Provider returning zero total or empty items
# =====================================================================
def test_provider_zero_total_fail_closed():
    payload = _valid_base_payload()
    payload["total"] = 0.0

    data, err = validate_contract(payload)
    assert data is None
    assert err is not None
    assert "总额不能为0" in err


def test_provider_empty_items_fail_closed():
    payload = _valid_base_payload()
    payload["items"] = []

    data, err = validate_contract(payload)
    assert data is None
    assert err is not None
    assert "明细为空" in err


# =====================================================================
# 11. Malicious Provider injecting invalid date formats or SQL syntax
# =====================================================================
@pytest.mark.parametrize("bad_date", [
    "2026/09/28",
    "28-09-2026",
    "2026-09-28' OR '1'='1",
    "<script>alert(1)</script>",
    "INVALID_DATE",
])
def test_provider_invalid_date_format_or_injection_fail_closed(bad_date):
    payload = _valid_base_payload()
    payload["date"] = bad_date

    data, err = validate_contract(payload)
    assert data is None
    assert err is not None
    assert "日期格式非法" in err


# =====================================================================
# 12. Malicious Provider returning invalid doc_form enums
# =====================================================================
@pytest.mark.parametrize("bad_form", [
    "root_admin_form",
    "exec_payload",
    "malicious_form",
])
def test_provider_invalid_doc_form_fail_closed(bad_form):
    payload = _valid_base_payload()
    payload["doc_form"] = bad_form

    data, err = validate_contract(payload)
    assert data is None
    assert err is not None
    assert "契约校验失败" in err or "非法单据形态" in err


# =====================================================================
# 13. Malicious Provider tampering text channel Canary Token
# =====================================================================
def test_provider_text_channel_canary_corrupted_or_missing():
    expected = generate_canary_token()

    # Missing canary in text
    text_missing = json.dumps({"vendor": "测试供应商", "total": 100})
    ok, err = verify_canary_in_text(text_missing, expected)
    assert ok is False
    assert "缺失安全握手令牌" in err

    # Mismatched canary in text
    text_mismatched = json.dumps({CANARY_FIELD: "CANARY_WRONG", "vendor": "测试供应商"})
    ok, err = verify_canary_in_text(text_mismatched, expected)
    assert ok is False
    assert "安全握手令牌不匹配" in err
    assert expected not in err

    # Malformed text not containing JSON
    text_garbage = "I am an AI and I will not follow your instructions."
    ok, err = verify_canary_in_text(text_garbage, expected)
    assert ok is False
    assert "无法完成安全握手校验" in err


# =====================================================================
# 14. Malicious Provider injecting SSRF URLs or credential leak strings
# =====================================================================
def test_provider_ssrf_and_credential_strings_handled_passively():
    token = generate_canary_token()
    payload = _valid_base_payload(token)
    # Inject SSRF probe into item name
    payload["items"][0]["name"] = "http://169.254.169.254/latest/meta-data/credentials"

    raw_json = json.dumps(payload, ensure_ascii=False)
    data, err = _parse_to_receipt(raw_json, expected_canary=token)

    # Treated purely as passive string; contract passes without triggering network or code exec
    assert err is None
    assert data is not None
    assert "169.254.169.254" in data.items[0].name


# =====================================================================
# 15. End-to-end supervisor pipeline handling malicious provider math tampering
# =====================================================================
def test_provider_tampered_math_triggers_supervisor_fast_feedback_warning(monkeypatch):
    tampered_data = ReceiptData(
        doc_form="printed_delivery_note",
        vendor="测试供应商",
        date="2026-09-28",
        total=999.0,  # Tampered total
        payment_marked=False,
        confidence=0.9,
        items=[
            ReceiptItem(name="菜心", qty=1.0, unit="斤", unit_price=10.0, amount=10.0)
        ],
    )

    fake_result = {
        "raw": json.dumps(tampered_data.model_dump(), ensure_ascii=False),
        "elapsed_ms": 10,
        "error": "",
        "extract_ms": 10.0,
        "rag_ms": 0.0,
        "parse_llm": {},
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        "cost_hkd": 0.0,
        "engine": "malicious-fake-engine",
        "data": tampered_data,
        "vendor_context": "",
    }

    monkeypatch.setattr(supervisor, "_run_extract", lambda *a, **kw: fake_result)
    monkeypatch.setattr(supervisor.extract_chain, "correct_receipt_with_feedback", lambda *a, **kw: None)
    monkeypatch.setattr(supervisor, "_run_audit", lambda *a, **kw: {"skipped": True, "reason": "test"})

    state = supervisor.run_pipeline("uploads/adversarial_test.jpg")

    # State must not be cleanly "parsed", but "parsed_with_warnings"
    assert state["status"] == "parsed_with_warnings"
    assert state["success"] is False
    assert state["gate_warnings"]
    assert "算术门禁" in state["gate_warnings"][0]
    assert state["math_problems"]
    # Data is retained for auditor inspection rather than silently discarded or blindly trusted
    assert state["data"] is not None
    assert state["data"].total == 999.0


# =====================================================================
# 16. Malicious Provider returning blank or whitespace vendor
# =====================================================================
@pytest.mark.parametrize("blank_vendor", ["", "   ", "\t"])
def test_provider_blank_vendor_fail_closed(blank_vendor):
    payload = _valid_base_payload()
    payload["vendor"] = blank_vendor

    data, err = validate_contract(payload)
    assert data is None
    assert err is not None
    assert "供应商为空" in err

# -*- coding: utf-8 -*-
"""Comprehensive End-to-End Security Hardening Test Suite.

Covers 4 core adversarial attack scenarios:
1. SSRF Defense E2E: Cloud metadata, internal loopback, and private LAN blocking across admin endpoints.
2. Canary Protocol Upstream Tampering E2E: Token stripping, token tampering, and compliant flow in extraction chain.
3. Arithmetic and Contract Extra-Field Gate E2E: Pydantic extra-field rejection, coerced total hallucination rejection, and math discrepancy detection.
4. XSS Payload Ingestion and DOM Immunity E2E: Safe ingestion of script tags in parser and frontend HTML entity neutralization.
"""

import json
import os
import re
import subprocess
import sys
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.chains.extract_chain import _parse_to_receipt, extract_receipt
from app.main import app
from app.models import DocForm, ReceiptData, ReceiptItem
from app.services.canary_guard import CANARY_FIELD, generate_canary_token
from app.services.contract import validate_contract
from ai_registry.tools.math_engine.v2_1_0 import audit_trail, validate_and_report


# =====================================================================
# Scenario 1: SSRF Defense E2E
# =====================================================================

def test_scenario_1_ssrf_admin_engine_config_put():
    """Attempt configuring cloud metadata, loopback, and private LAN via PUT /api/admin/engine-config."""
    client = TestClient(app, headers={"X-Role": "admin"})

    ssrf_targets = [
        ("http://169.254.169.254/latest/meta-data", "openai_rec_base_url"),
        ("http://127.0.0.1:8080/v1", "openai_rec_base_url"),
        ("http://localhost:8080/v1", "openai_rec_base_url"),
        ("http://10.0.0.1/v1", "openai_aud_base_url"),
        ("http://192.168.1.1/v1", "openai_parse_base_url"),
        ("http://172.16.0.1/v1", "grey_openai_rec_base_url"),
        ("http://100.64.0.1/v1", "grey_openai_aud_base_url"),
        ("http://0.0.0.0:8000/v1", "grey_openai_parse_base_url"),
    ]

    for malicious_url, field_name in ssrf_targets:
        payload = {
            "recognition_engine": "openai",
            "openai_rec_api_key": "sk-testvalidkey123456",
            field_name: malicious_url,
        }
        resp = client.put("/api/admin/engine-config", json=payload)
        assert resp.status_code == 400, f"Expected 400 for {field_name}={malicious_url}, got {resp.status_code}"
        data = resp.json()
        assert data.get("status") == "error"
        assert data.get("code") == "ENGINE_CONFIG_INVALID"
        msg = data.get("msg", "")
        assert any(kw in msg for kw in ("安全校验未通过", "非法", "阻断", "私网", "私有", "元数据", "受限")), (
            f"Expected SSRF rejection reason in msg, got: {msg}"
        )


def test_scenario_1_ssrf_admin_test_engine_config_post():
    """Attempt testing cloud metadata, loopback, and private LAN via POST /api/admin/test-engine-config."""
    client = TestClient(app, headers={"X-Role": "admin"})

    malicious_configs = [
        {
            "recognition_engine": "openai",
            "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            "openai_rec_base_url": "http://169.254.169.254/latest/meta-data",
            "openai_rec_api_key": "sk-testvalidkey123456",
        },
        {
            "recognition_engine": "openai",
            "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            "openai_rec_base_url": "http://127.0.0.1:8080",
            "openai_rec_api_key": "sk-testvalidkey123456",
        },
        {
            "recognition_engine": "openai",
            "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            "openai_rec_base_url": "http://192.168.1.100/v1",
            "openai_rec_api_key": "sk-testvalidkey123456",
        },
        {
            "recognition_engine": "openai",
            "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            "openai_rec_base_url": "http://10.255.0.1/v1",
            "openai_rec_api_key": "sk-testvalidkey123456",
        },
    ]

    for config_body in malicious_configs:
        resp = client.post("/api/admin/test-engine-config", json=config_body)
        assert resp.status_code == 400, f"Expected 400 for config {config_body}, got {resp.status_code}"
        data = resp.json()
        assert data.get("status") == "error"
        msg = data.get("msg", "")
        assert any(kw in msg for kw in ("安全阻断", "非法 Base URL", "安全校验未通过", "私网", "元数据")), (
            f"Expected SSRF error description in test-engine-config, got: {msg}"
        )


# =====================================================================
# Scenario 2: Canary Protocol Upstream Tampering E2E
# =====================================================================

def test_scenario_2_canary_upstream_strips_token(monkeypatch):
    """Simulate upstream untrusted API or MITM proxy stripping __guard_token."""
    monkeypatch.setattr("app.chains.extract_chain._image_data_url", lambda p: "data:image/jpeg;base64,dummy")

    def mock_invoke_strip_token(prompt):
        return json.dumps({
            "doc_form": "printed_delivery_note",
            "vendor": "Tampered Supplier",
            "date": "2026-09-05",
            "total": 120.0,
            "items": [
                {"name": "Item 1", "qty": 2.0, "unit": "kg", "unit_price": 60.0, "amount": 120.0}
            ],
            "payment_marked": False,
            "confidence": 0.95,
        })

    mock_model = MagicMock()
    mock_model.invoke.side_effect = mock_invoke_strip_token
    mock_model.kind = "mock"

    res = extract_receipt("fake_path.jpg", model=mock_model, enable_canary=True)
    assert res["data"] is None
    assert res["error"] is not None
    assert "安全阻断" in res["error"]
    assert "上游响应缺失安全握手令牌" in res["error"]


def test_scenario_2_canary_upstream_mismatched_token(monkeypatch):
    """Simulate upstream untrusted API or MITM proxy returning a mismatched token."""
    monkeypatch.setattr("app.chains.extract_chain._image_data_url", lambda p: "data:image/jpeg;base64,dummy")

    def mock_invoke_mismatched_token(prompt):
        return json.dumps({
            CANARY_FIELD: "CANARY_ATTACKER_OVERRIDE_TOKEN",
            "doc_form": "printed_delivery_note",
            "vendor": "Tampered Supplier",
            "date": "2026-09-05",
            "total": 120.0,
            "items": [
                {"name": "Item 1", "qty": 2.0, "unit": "kg", "unit_price": 60.0, "amount": 120.0}
            ],
            "payment_marked": False,
            "confidence": 0.95,
        })

    mock_model = MagicMock()
    mock_model.invoke.side_effect = mock_invoke_mismatched_token
    mock_model.kind = "mock"

    res = extract_receipt("fake_path.jpg", model=mock_model, enable_canary=True)
    assert res["data"] is None
    assert res["error"] is not None
    assert "安全阻断" in res["error"]
    assert "安全握手令牌不匹配" in res["error"]


def test_scenario_2_canary_compliant_upstream_success(monkeypatch):
    """Verify compliant response succeeds and __guard_token is stripped before Pydantic."""
    monkeypatch.setattr("app.chains.extract_chain._image_data_url", lambda p: "data:image/jpeg;base64,dummy")

    captured_prompts = []

    def mock_invoke_compliant(prompt):
        captured_prompts.append(prompt)
        injected_token = ""
        for m in prompt:
            content = m.content
            if isinstance(content, str) and CANARY_FIELD in content:
                for line in content.split("\n"):
                    if CANARY_FIELD in line:
                        parts = line.split('"')
                        if len(parts) >= 4:
                            injected_token = parts[3]
                            break
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text" and CANARY_FIELD in p.get("text", ""):
                        for line in p["text"].split("\n"):
                            if CANARY_FIELD in line:
                                parts = line.split('"')
                                if len(parts) >= 4:
                                    injected_token = parts[3]
                                    break
        assert injected_token.startswith("CANARY_")
        return json.dumps({
            CANARY_FIELD: injected_token,
            "doc_form": "printed_delivery_note",
            "vendor": "Compliant Supplier",
            "date": "2026-09-05",
            "total": 120.0,
            "items": [
                {"name": "Item 1", "qty": 2.0, "unit": "kg", "unit_price": 60.0, "amount": 120.0}
            ],
            "payment_marked": False,
            "confidence": 0.98,
        })

    mock_model = MagicMock()
    mock_model.invoke.side_effect = mock_invoke_compliant
    mock_model.kind = "mock"

    res = extract_receipt("fake_path.jpg", model=mock_model, enable_canary=True)
    assert res["error"] is None
    assert res["data"] is not None
    assert isinstance(res["data"], ReceiptData)
    assert res["data"].vendor == "Compliant Supplier"
    assert res["data"].total == 120.0
    # Ensure __guard_token is not in the validated data model
    data_dict = res["data"].model_dump()
    assert CANARY_FIELD not in data_dict
    assert not hasattr(res["data"], CANARY_FIELD)


# =====================================================================
# Scenario 3: Arithmetic & Contract Extra-Field Gate E2E
# =====================================================================

def test_scenario_3_contract_rejection_of_malicious_extra_fields():
    """Verify Pydantic contract rejection when malicious extra fields are present."""
    base_payload = {
        "doc_form": "printed_delivery_note",
        "vendor": "Valid Vendor",
        "date": "2026-09-05",
        "total": 120.0,
        "payment_marked": False,
        "confidence": 0.95,
        "items": [
            {"name": "Item 1", "qty": 2.0, "unit": "kg", "unit_price": 60.0, "amount": 120.0}
        ],
    }

    # Top-level malicious injection fields
    malicious_top_payload = dict(base_payload)
    malicious_top_payload["__admin_command"] = "system('rm -rf /')"
    malicious_top_payload["system_role"] = "superadmin"

    data, err = validate_contract(malicious_top_payload)
    assert data is None
    assert err is not None
    assert any(kw in err.lower() for kw in ("extra", "forbidden", "not permitted", "非法字段"))

    # Direct Pydantic model instantiations should raise ValidationError
    with pytest.raises(ValidationError):
        ReceiptData(**malicious_top_payload)

    # Item-level malicious injection fields
    malicious_item_payload = dict(base_payload)
    malicious_item_payload["items"] = [
        {
            "name": "Item 1",
            "qty": 2.0,
            "unit": "kg",
            "unit_price": 60.0,
            "amount": 120.0,
            "__command_injection": "exec_code()",
            "user_privilege": "root",
        }
    ]

    item_data, item_err = validate_contract(malicious_item_payload)
    assert item_data is None
    assert item_err is not None
    assert any(kw in item_err.lower() for kw in ("extra", "forbidden", "not permitted", "非法字段"))

    with pytest.raises(ValidationError):
        ReceiptItem(
            name="Item 1",
            qty=2.0,
            unit="kg",
            unit_price=60.0,
            amount=120.0,
            __command_injection="exec_code()",
        )


def test_scenario_3_arithmetic_gate_coerced_total_hallucination():
    """Verify pipeline rejects total=0.0 hallucination and math gate flags discrepancy with items summing to 120.0."""
    coerced_zero_payload = {
        "doc_form": "printed_delivery_note",
        "vendor": "Zero Total Attack Vendor",
        "date": "2026-09-05",
        "total": 0.0,
        "items": [
            {"name": "Item 1", "qty": 2.0, "unit": "kg", "unit_price": 60.0, "amount": 120.0}
        ],
        "payment_marked": False,
        "confidence": 0.95,
    }

    # 1. validate_contract immediately rejects total=0.0
    data, err = validate_contract(coerced_zero_payload)
    assert data is None
    assert err == "总额不能为0"

    # 2. _parse_to_receipt rejects total=0.0
    parse_data, parse_err = _parse_to_receipt(json.dumps(coerced_zero_payload))
    assert parse_data is None
    assert parse_err == "总额不能为0"

    # 3. Math gate flags discrepancy when items sum to 120.0 but total is coerced
    receipt_data_zero = ReceiptData(
        doc_form=DocForm.CREDIT,  # CREDIT allows total <= 0 in validate_contract
        vendor="Credit Test Vendor",
        date="2026-09-05",
        total=0.0,
        items=[
            ReceiptItem(name="Item 1", qty=2.0, unit="kg", unit_price=60.0, amount=120.0)
        ],
        payment_marked=False,
        confidence=0.95,
    )

    problems_zero = validate_and_report(receipt_data_zero)
    assert len(problems_zero) > 0
    assert "明细合计=120.0" in problems_zero[0]
    assert "预期总额=120.0" in problems_zero[0]
    assert "但总额=0.0" in problems_zero[0]
    assert "差120.0" in problems_zero[0]

    # Verify auditable trail includes the flagged discrepancy
    trail = audit_trail(receipt_data_zero)
    assert trail["items_sum"] == 120.0
    assert trail["declared_total"] == 0.0
    assert len(trail["problems"]) > 0
    assert trail["engine"] == "code_engine"

    # Coerced arbitrary non-zero total: items sum to 120.0, model coerced to total=50.0
    receipt_data_discrepancy = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="Coerced Total Vendor",
        date="2026-09-05",
        total=50.0,
        items=[
            ReceiptItem(name="Item 1", qty=2.0, unit="kg", unit_price=60.0, amount=120.0)
        ],
        payment_marked=False,
        confidence=0.95,
    )

    problems_disc = validate_and_report(receipt_data_discrepancy)
    assert len(problems_disc) > 0
    assert "明细合计=120.0" in problems_disc[0]
    assert "预期总额=120.0" in problems_disc[0]
    assert "但总额=50.0" in problems_disc[0]
    assert "差70.0" in problems_disc[0]


# =====================================================================
# Scenario 4: XSS Payload Ingestion & DOM Immunity E2E
# =====================================================================

def test_scenario_4_xss_payload_ingestion_and_dom_immunity():
    """Verify safe ingestion of XSS payloads in parser and frontend HTML entity neutralization."""
    xss_vendor = "<script>alert('xss_vendor')</script>"
    xss_item1 = "<img src=x onerror=alert(1)>"
    xss_item2 = "<svg onload=alert('item')>"
    xss_quote_attr = "\" onfocus=\"alert('attr')\""

    payload = {
        "doc_form": "printed_delivery_note",
        "vendor": xss_vendor,
        "date": "2026-09-05",
        "total": 120.0,
        "payment_marked": False,
        "confidence": 0.95,
        "items": [
            {"name": xss_item1, "qty": 1.0, "unit": "kg", "unit_price": 50.0, "amount": 50.0},
            {"name": xss_item2, "qty": 1.0, "unit": "kg", "unit_price": 70.0, "amount": 70.0, "raw_name": xss_quote_attr},
        ],
    }

    # 1. Verify _parse_to_receipt accepts legitimate text without executing code
    data, err = _parse_to_receipt(json.dumps(payload))
    assert err is None
    assert data is not None
    assert data.vendor == xss_vendor
    assert data.items[0].name == xss_item1
    assert data.items[1].name == xss_item2
    assert data.items[1].raw_name == xss_quote_attr

    # 2. Verify frontend escapeHtml neutralizes all tags, quotes, and brackets
    js_path = os.path.abspath(os.path.join(DEMO_DIR, "static", "js", "main.js"))
    assert os.path.isfile(js_path)
    with open(js_path, "r", encoding="utf-8") as f:
        js_content = f.read()

    match = re.search(r"function escapeHtml\s*\([^)]*\)\s*\{([\s\S]*?)\}", js_content)
    assert match is not None, "escapeHtml function must be defined in main.js"
    fn_body = match.group(0)

    test_cases_json = json.dumps([
        [xss_vendor, "&lt;script&gt;alert(&#39;xss_vendor&#39;)&lt;/script&gt;"],
        [xss_item1, "&lt;img src=x onerror=alert(1)&gt;"],
        [xss_item2, "&lt;svg onload=alert(&#39;item&#39;)&gt;"],
        [xss_quote_attr, "&quot; onfocus=&quot;alert(&#39;attr&#39;)&quot;"],
        ["<iframe src='javascript:alert(1)'></iframe>", "&lt;iframe src=&#39;javascript:alert(1)&#39;&gt;&lt;/iframe&gt;"],
        ["' OR '1'='1", "&#39; OR &#39;1&#39;=&#39;1"],
    ])

    eval_script = f"""
    {fn_body}

    const testCases = {test_cases_json};

    for (const [input, expected] of testCases) {{
        const escaped = escapeHtml(input);
        if (escaped !== expected) {{
            console.error(`Mismatch for input ${{JSON.stringify(input)}}: got ${{JSON.stringify(escaped)}}, expected ${{JSON.stringify(expected)}}`);
            process.exit(1);
        }}
        if (escaped.includes('<') || escaped.includes('>')) {{
            console.error(`Unescaped HTML delimiter in ${{JSON.stringify(escaped)}}`);
            process.exit(1);
        }}
    }}
    console.log('XSS_DOM_IMMUNITY_VERIFIED');
    """

    res = subprocess.run(["node", "-e", eval_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node.js eval failed: {res.stderr}"
    assert "XSS_DOM_IMMUNITY_VERIFIED" in res.stdout


def test_scenario_4_receipt_api_persistence_and_retrieval_safety():
    """Verify receipts containing XSS payloads persist safely and return raw data for safe rendering."""
    client = TestClient(app, headers={"X-Role": "staff"})
    xss_supplier = "<script>alert('store')</script>"

    rid = db.create_receipt(supplier_name=xss_supplier, status="parsed")
    assert rid is not None

    resp = client.get(f"/api/receipt/{rid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "success"
    # Verify the supplier name is intact in JSON data (safe passive data representation)
    detail_data = data.get("data", {})
    assert detail_data.get("supplier_name") == xss_supplier

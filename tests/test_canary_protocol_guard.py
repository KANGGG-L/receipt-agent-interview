# -*- coding: utf-8 -*-
"""Canary Token 动态协议签名防上游篡改测试集。

验证:
1. Canary Token 生成: 前缀、长度、随机性
2. verify_canary_token: 正常校验通过、__guard_token 弹出
3. verify_canary_token: 篡改阻断 (非字典、缺失 token、token 不匹配)
4. inject_canary_instructions: 双重锚定与多模态结构保持
5. extract_chain 联动与向后兼容: 阻断与放行
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app.services.canary_guard import (
    CANARY_FIELD,
    generate_canary_token,
    inject_canary_instructions,
    verify_canary_token,
)
from app.chains.extract_chain import _parse_to_receipt, extract_receipt


def test_canary_token_generation():
    tokens = [generate_canary_token() for _ in range(50)]
    for token in tokens:
        assert token.startswith("CANARY_")
        assert len(token) >= 12
    # 随机性：50 次生成均不重复
    assert len(set(tokens)) == 50


def test_canary_verification_success():
    token = generate_canary_token()
    payload = {
        CANARY_FIELD: token,
        "doc_form": "printed_delivery_note",
        "vendor": "ABC Supplier",
        "total": 100.0,
    }
    ok, err = verify_canary_token(payload, token)
    assert ok is True
    assert err is None
    # 验证字段必须在校验成功后被弹出，防止破坏 Pydantic extra="forbid"
    assert CANARY_FIELD not in payload

    # 兼容预期 token 两端有空白字符场景
    payload2 = {CANARY_FIELD: token}
    ok2, err2 = verify_canary_token(payload2, f"  {token}  ")
    assert ok2 is True
    assert err2 is None
    assert CANARY_FIELD not in payload2


def test_canary_verification_tampered_fails():
    token = generate_canary_token()

    # 1. 输出非字典结构
    ok, err = verify_canary_token(["not", "a", "dict"], token)
    assert ok is False
    assert "模型输出必须为字典结构" in err

    # 2. 缺失 __guard_token
    payload_missing = {"vendor": "ABC Supplier", "total": 100.0}
    ok, err = verify_canary_token(payload_missing, token)
    assert ok is False
    assert "上游响应缺失安全握手令牌" in err
    assert "System Prompt 遭到代理篡改或剥离" in err

    # 3. __guard_token 为空字符或 None
    payload_empty = {CANARY_FIELD: "", "vendor": "ABC"}
    ok, err = verify_canary_token(payload_empty, token)
    assert ok is False
    assert "上游响应缺失安全握手令牌" in err
    assert CANARY_FIELD not in payload_empty

    payload_none = {CANARY_FIELD: None, "vendor": "ABC"}
    ok, err = verify_canary_token(payload_none, token)
    assert ok is False
    assert "上游响应缺失安全握手令牌" in err
    assert CANARY_FIELD not in payload_none

    # 4. __guard_token 不匹配
    payload_mismatch = {CANARY_FIELD: "CANARY_WRONGTOKEN", "vendor": "ABC"}
    ok, err = verify_canary_token(payload_mismatch, token)
    assert ok is False
    assert "安全握手令牌不匹配" in err
    assert f"预期 {token}" in err
    assert "实际 CANARY_WRONGTOKEN" in err
    assert CANARY_FIELD not in payload_mismatch


def test_inject_canary_instructions():
    token = generate_canary_token()

    # 1. 经典 SystemMessage + HumanMessage 双重锚定
    messages = [
        SystemMessage(content="You are an expert receipt parser."),
        HumanMessage(content="Please process this receipt."),
    ]
    injected = inject_canary_instructions(messages, token)
    assert len(injected) == 2
    assert "[协议安全锚定指令]" in injected[0].content
    assert f'"{CANARY_FIELD}": "{token}"' in injected[0].content
    assert "[协议安全锚定指令]" in injected[1].content
    assert f'"{CANARY_FIELD}": "{token}"' in injected[1].content

    # 2. 存在多个 HumanMessage 时，仅在最后一个 HumanMessage 末尾锚定
    messages_multi = [
        SystemMessage(content="System base"),
        HumanMessage(content="First turn human prompt"),
        HumanMessage(content="Second turn human prompt"),
    ]
    injected_multi = inject_canary_instructions(messages_multi, token)
    assert len(injected_multi) == 3
    assert "[协议安全锚定指令]" in injected_multi[0].content
    assert "[协议安全锚定指令]" not in injected_multi[1].content
    assert "[协议安全锚定指令]" in injected_multi[2].content

    # 3. 缺失 HumanMessage 时自动追加 HumanMessage
    messages_no_human = [
        SystemMessage(content="System only prompt"),
    ]
    injected_no_human = inject_canary_instructions(messages_no_human, token)
    assert len(injected_no_human) == 2
    assert isinstance(injected_no_human[1], HumanMessage)
    assert "[协议安全锚定指令]" in injected_no_human[1].content

    # 4. 缺失 SystemMessage 时仅在 HumanMessage 锚定，不强制新增 SystemMessage
    messages_no_sys = [
        HumanMessage(content="Human only prompt"),
    ]
    injected_no_sys = inject_canary_instructions(messages_no_sys, token)
    assert len(injected_no_sys) == 1
    assert isinstance(injected_no_sys[0], HumanMessage)
    assert "[协议安全锚定指令]" in injected_no_sys[0].content

    # 5. 保留多模态复杂结构 (list of text/image_url)
    multipart_content = [
        {"type": "text", "text": "Vendor prior text"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,12345"}},
        {"type": "text", "text": "Please convert to json"},
    ]
    messages_multipart = [
        SystemMessage(content="System prompt"),
        HumanMessage(content=multipart_content),
    ]
    injected_multi_part = inject_canary_instructions(messages_multipart, token)
    human_content = injected_multi_part[1].content
    assert isinstance(human_content, list)
    assert len(human_content) == 3
    assert human_content[1]["type"] == "image_url"
    assert human_content[2]["type"] == "text"
    assert "Please convert to json" in human_content[2]["text"]
    assert "[协议安全锚定指令]" in human_content[2]["text"]
    assert token in human_content[2]["text"]

    # 6. 多模态末尾为非 text 时，自动向 list 追加 text 分块
    multipart_ends_with_image = [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,9999"}},
    ]
    messages_end_img = [
        HumanMessage(content=multipart_ends_with_image),
    ]
    injected_end_img = inject_canary_instructions(messages_end_img, token)
    content_res = injected_end_img[0].content
    assert isinstance(content_res, list)
    assert len(content_res) == 2
    assert content_res[0]["type"] == "image_url"
    assert content_res[1]["type"] == "text"
    assert "[协议安全锚定指令]" in content_res[1]["text"]


def test_extract_chain_canary_blocking():
    token = generate_canary_token()
    valid_receipt_dict = {
        "doc_form": "printed_delivery_note",
        "vendor": "Test Store",
        "date": "2026-09-05",
        "total": 120.0,
        "items": [
            {
                "name": "Cabbage",
                "qty": 2.0,
                "unit": "kg",
                "unit_price": 60.0,
                "amount": 120.0,
            }
        ],
        "payment_marked": False,
        "confidence": 0.98,
    }

    # 1. 带有正确 Canary Token 的响应能够正常解析
    payload_with_token = dict(valid_receipt_dict)
    payload_with_token[CANARY_FIELD] = token
    raw_valid = json.dumps(payload_with_token)

    data, err = _parse_to_receipt(raw_valid, expected_canary=token)
    assert err is None
    assert data is not None
    assert data.vendor == "Test Store"
    assert data.total == 120.0

    # 2. 期望校验但上游剥离了 Canary Token -> 安全阻断
    raw_no_token = json.dumps(valid_receipt_dict)
    data, err = _parse_to_receipt(raw_no_token, expected_canary=token)
    assert data is None
    assert err is not None
    assert "安全阻断" in err
    assert "上游响应缺失安全握手令牌" in err

    # 3. 期望校验但上游篡改了 Canary Token -> 安全阻断
    payload_tampered = dict(valid_receipt_dict)
    payload_tampered[CANARY_FIELD] = "CANARY_ATTACKER_OVERRIDE"
    raw_tampered = json.dumps(payload_tampered)
    data, err = _parse_to_receipt(raw_tampered, expected_canary=token)
    assert data is None
    assert err is not None
    assert "安全阻断" in err
    assert "安全握手令牌不匹配" in err

    # 4. 向后兼容：若未传入 expected_canary，即使未带 token 也能正常通过
    data_compat, err_compat = _parse_to_receipt(raw_no_token, expected_canary="")
    assert err_compat is None
    assert data_compat is not None
    assert data_compat.vendor == "Test Store"

    # 5. 向后兼容：若未传入 expected_canary 但上游带了残留 __guard_token，自动剥除而不破坏 extra="forbid"
    data_residue, err_residue = _parse_to_receipt(payload_with_token, expected_canary="")
    assert err_residue is None
    assert data_residue is not None
    assert data_residue.vendor == "Test Store"


def test_extract_receipt_e2e_canary_flow(monkeypatch):
    # 模拟图片 URL 转换，避免需要物理文件
    monkeypatch.setattr("app.chains.extract_chain._image_data_url", lambda p: "data:image/jpeg;base64,dummy")

    # 模拟真实 invoke 过程中的 Canary 注入与提取校验
    captured_prompts = []

    def mock_invoke(prompt):
        captured_prompts.append(prompt)
        # 从 prompt 中提取注入的 token
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
        # 模拟模型遵守协议，在输出根节点带上 __guard_token
        return json.dumps({
            CANARY_FIELD: injected_token,
            "doc_form": "printed_delivery_note",
            "vendor": "Mock Vendor",
            "date": "2026-09-05",
            "total": 50.0,
            "items": [
                {"name": "Apples", "qty": 1.0, "unit": "box", "unit_price": 50.0, "amount": 50.0}
            ],
            "payment_marked": False,
            "confidence": 0.99,
        })

    mock_model = MagicMock()
    mock_model.invoke.side_effect = mock_invoke
    mock_model.kind = "mock"

    res = extract_receipt("non_existent_path.jpg", model=mock_model, enable_canary=True)
    assert res["error"] is None
    assert res["data"] is not None
    assert res["data"].vendor == "Mock Vendor"
    assert len(captured_prompts) == 1

    # 模拟上游代理恶意篡改 / 剥除 prompt 导致模型未返回 token
    def mock_invoke_tampered(prompt):
        return json.dumps({
            "doc_form": "printed_delivery_note",
            "vendor": "Attacker Hijacked",
            "date": "2026-09-05",
            "total": 50.0,
            "items": [
                {"name": "Apples", "qty": 1.0, "unit": "box", "unit_price": 50.0, "amount": 50.0}
            ],
            "payment_marked": False,
            "confidence": 0.99,
        })

    mock_model_tampered = MagicMock()
    mock_model_tampered.invoke.side_effect = mock_invoke_tampered
    mock_model_tampered.kind = "mock"

    res_tampered = extract_receipt("non_existent_path.jpg", model=mock_model_tampered, enable_canary=True)
    assert res_tampered["data"] is None
    assert "安全阻断" in res_tampered["error"]

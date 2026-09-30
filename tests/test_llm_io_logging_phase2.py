# -*- coding: utf-8 -*-
"""Phase 2: LLM/VLM I/O 拦截、脱敏与 Cached Token 归一化单元测试。"""

import json
import pytest
from app import db
from app.llm import _normalize_token_usage, OpenAIChatModel
from app.services.llm_logger import (
    sanitize_text,
    sanitize_prompt_messages,
    record_llm_call,
    set_llm_context,
    get_llm_context,
    llm_context,
)
from app.services.parsing_logs import get_parsing_log_detail
from langchain_core.messages import HumanMessage, SystemMessage


def test_normalize_token_usage_cached_tokens():
    """测试 AC-01: Token 归一化提取 cached_tokens。"""
    # 1. OpenAI 格式 prompt_tokens_details.cached_tokens
    u1 = {
        "prompt_tokens": 2048,
        "completion_tokens": 512,
        "total_tokens": 2560,
        "prompt_tokens_details": {"cached_tokens": 1024},
    }
    r1 = _normalize_token_usage(u1)
    assert r1["prompt_tokens"] == 2048
    assert r1["completion_tokens"] == 512
    assert r1["cached_tokens"] == 1024
    assert r1["total_tokens"] == 2560

    # 2. 顶层 cached_tokens
    u2 = {"input_tokens": 1500, "output_tokens": 300, "cached_tokens": 800}
    r2 = _normalize_token_usage(u2)
    assert r2["prompt_tokens"] == 1500
    assert r2["completion_tokens"] == 300
    assert r2["cached_tokens"] == 800
    assert r2["total_tokens"] == 1800

    # 3. 顶层 cache_read_input_tokens
    u3 = {"prompt": 1000, "completion": 200, "cache_read_input_tokens": 400}
    r3 = _normalize_token_usage(u3)
    assert r3["cached_tokens"] == 400

    # 4. 缺失字段或非法输入回落 0
    assert _normalize_token_usage({}) == {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 0}
    assert _normalize_token_usage(None) == {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 0}


def test_db_schema_and_idempotence():
    """测试 AC-02: 数据模型创建与持久化。"""
    assert hasattr(db, "_LlmCallLogRow")
    assert hasattr(db, "LLMCallLogRow")
    s = db.get_session()
    try:
        assert s.query(db._LlmCallLogRow).count() >= 0
    finally:
        s.close()


def test_image_sanitization_and_masking():
    """测试 AC-03 & AC-05: 多模态 Base64 剔除为 image_ref 与敏感信息脱敏。"""
    raw_b64 = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
    prompt = [
        {"role": "system", "content": "系统指令包含 sk-1234567890abcdef12345678 密钥与 Bearer secret_token_for_auth_12345"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "用户身份证是 110101199003072345 请解析。"},
                {"type": "image_url", "image_url": {"url": raw_b64}},
            ]
        }
    ]

    clean = sanitize_prompt_messages(prompt, source_path="/path/to/receipt.jpg")
    clean_str = json.dumps(clean, ensure_ascii=False)

    # 断言 1: 绝不包含 base64 原始大字符串
    assert "base64," not in clean_str
    assert "image_ref" in clean_str

    # 断言 2: API 密钥已脱敏为 sk-***
    assert "sk-1234567890abcdef12345678" not in clean_str
    assert "sk-***" in clean_str

    # 断言 3: Bearer 令牌已脱敏
    assert "secret_token_for_auth_12345" not in clean_str
    assert "Bearer ***" in clean_str

    # 断言 4: 身份证号已正则掩码
    assert "110101199003072345" not in clean_str
    assert "110101********2345" in clean_str


def test_mock_llm_invocation_interception(monkeypatch):
    """测试 mock 模式下全量调用拦截落盘。"""
    monkeypatch.setenv("MOCK_QWEN_FLASH", "1")
    model = OpenAIChatModel(model="qwen3-vl-flash")
    test_rid = 99999

    with llm_context(receipt_id=test_rid, leg="extract", attempt=1):
        res = model.invoke([HumanMessage(content="识别这张单据")])
        assert res.content
        tu = res.response_metadata.get("token_usage")
        assert tu.get("cached_tokens") == 1200

    # 查库校验
    logs = db.list_llm_call_logs_by_receipt(test_rid)
    assert len(logs) >= 1
    log = logs[0]
    assert log["receipt_id"] == test_rid
    assert log["leg"] == "extract"
    assert log["attempt"] == 1
    assert log["status"] == "success"
    assert log["cached_tokens"] == 1200
    assert "新協興" in log["raw_output"]


def test_detail_api_aggregation():
    """测试 AC-06: 详情接口返回 llm_calls 列表及 cached_tokens 聚合。"""
    test_rid = 88888
    # 插入两条不同 leg 的日志
    db.insert_llm_call_log(
        receipt_id=test_rid,
        leg="extract",
        attempt=1,
        engine="openai",
        model="qwen3.5-omni-flash",
        prompt_messages_json=[{"role": "user", "content": "extract prompt"}],
        raw_output="{\"vendor\": \"Vendor_Test\"}",
        prompt_tokens=2000,
        completion_tokens=400,
        cached_tokens=1500,
        total_tokens=2400,
        cost_hkd=0.003,
        elapsed_ms=1500.0,
        status="success",
    )
    db.insert_llm_call_log(
        receipt_id=test_rid,
        leg="audit",
        attempt=1,
        engine="openai",
        model="zai-org/GLM-4.5V",
        prompt_messages_json=[{"role": "user", "content": "audit prompt"}],
        raw_output="{\"overall_consistent\": true}",
        prompt_tokens=500,
        completion_tokens=50,
        cached_tokens=0,
        total_tokens=550,
        cost_hkd=0.0005,
        elapsed_ms=800.0,
        status="success",
    )

    s = db.get_session()
    try:
        # Mock 一个虚拟单据
        r_row = db._ReceiptRow(
            id=test_rid,
            status="parsed",
            supplier_name="测试供应商",
            total_amount=100.0,
            tenant_id="default",
        )
        s.add(r_row)
        s.commit()

        detail = get_parsing_log_detail(s, record_id=test_rid)
        assert detail is not None
        assert "llm_calls" in detail
        calls = detail["llm_calls"]
        assert len(calls) == 2
        assert calls[0]["leg"] == "extract"
        assert calls[0]["leg_label"] == "识别腿 (VLM)"
        assert calls[0]["token_usage"]["cached_tokens"] == 1500
        assert calls[0]["token_usage"]["cache_hit_rate"] == 0.75
        assert calls[1]["leg"] == "audit"
        assert calls[1]["leg_label"] == "审核腿 (Cross-Audit)"

        # 校验顶层 token_usage
        tu = detail["token_usage"]
        assert tu["tokens_cached"] == 1500
    finally:
        # 清理测试数据
        s.query(db._ReceiptRow).filter(db._ReceiptRow.id == test_rid).delete()
        s.query(db._LlmCallLogRow).filter(db._LlmCallLogRow.receipt_id == test_rid).delete()
        s.commit()
        s.close()

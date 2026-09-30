# -*- coding: utf-8 -*-
"""P1-1 Canary 能力边界固化测试集（去误导 + 非破坏性 + 复盘腿补齐）。

最高优先目标——把能力边界钉成测试事实：
canary token 是明文写在发给 provider 的 prompt 里的（System 与 Human 双重锚定），
恶意 provider 只要原样回填即可通过校验，并可随意篡改任何数据字段。实测「回填正确
token + 篡改 total/vendor/date/items」→ verify_canary_token 返回 ok、篡改结果被采纳。
因此它的真实定位是「上游错配 / 静默降级检测」，**不是认证、也不提供完整性保护**。
真正的护栏是契约门禁 / 算术校验 / 交叉审核 / 人审。本文件把这一事实固化成断言，
防止后人误以为 canary 已防住篡改而放松其它护栏。

其余验证:
- token 缺失 / 空 / 不匹配 → fail-closed，且错误文案不含预期 token 原文；
- verify_canary_token 非破坏性：调用后入参 dict 不被修改，清洗副本已剥除 __guard_token；
- 复盘腿（review_chain.run_review）补齐 canary：通过则正常返回、失败则 skipped=True 且不
  含被污染结论；
- 审核腿（audit_chain.run_audit）既有 canary 行为回归。

全程不碰 DB、不发网络请求（provider 均为假实现）。
"""

import json
import logging
import os
import re
import sys

import pytest

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app.services.canary_guard import (
    CANARY_FIELD,
    generate_canary_token,
    verify_canary_token,
)
from app.chains.extract_chain import _parse_to_receipt

_TOKEN_RE = re.compile(r"CANARY_[0-9A-F]{8}")


def _flatten_prompt(prompt) -> str:
    parts = []
    for msg in prompt:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
    return "\n".join(parts)


def _token_in_prompt(prompt) -> str:
    match = _TOKEN_RE.search(_flatten_prompt(prompt))
    return match.group(0) if match else ""


class _EchoProvider:
    """假 provider：记录收到的 prompt；按 echo_token 决定是否回带本轮 token。"""

    kind = "fake"

    def __init__(self, payload: dict, echo_token: bool = True):
        self._payload = payload
        self.echo_token = echo_token
        self.calls = 0
        self.last_prompt = None

    def invoke(self, prompt):
        self.calls += 1
        self.last_prompt = prompt
        payload = dict(self._payload)
        if self.echo_token:
            token = _token_in_prompt(prompt)
            if token:
                payload[CANARY_FIELD] = token
        return json.dumps(payload, ensure_ascii=False)


def _base_receipt() -> dict:
    return {
        "doc_form": "printed_delivery_note",
        "vendor": "测试供应商",
        "date": "2026-09-05",
        "total": 10.0,
        "payment_marked": False,
        "confidence": 0.9,
        "items": [
            {"name": "白菜", "qty": 1.0, "unit": "斤", "unit_price": 10.0, "amount": 10.0}
        ],
    }


# =====================================================================
# 1. 边界固化：canary 不是认证，回填正确 token 即可任意篡改并被采纳
# =====================================================================
def test_canary_is_not_authentication_tampered_payload_is_still_adopted():
    """能力边界固化（勿删/勿弱化）：canary 不是认证，也不提供完整性保护。

    构造「回填正确 token + 篡改 total/vendor/date/items」的响应：校验会通过，被
    篡改的字段会被原样采纳。此断言固化既有事实——防止后人误以为 canary 已防住篡改
    而放松契约/算术/交叉审核/人审护栏。若本用例失败，说明有人在没有引入真正认证
    机制的前提下改变了 canary 的语义，必须连同文档一起复核。
    """
    token = generate_canary_token()
    tampered = {
        CANARY_FIELD: token,                 # 恶意 provider 原样回填明文 token
        "doc_form": "printed_delivery_note",
        "vendor": "攻击者伪造供应商",          # 篡改
        "date": "2099-12-31",                # 篡改
        "total": 999.0,                       # 篡改
        "payment_marked": False,
        "confidence": 0.99,
        "items": [
            {"name": "攻击者伪造品项", "qty": 9.0, "unit": "斤",
             "unit_price": 111.0, "amount": 999.0}   # 篡改
        ],
    }
    data, err = _parse_to_receipt(
        json.dumps(tampered, ensure_ascii=False), expected_canary=token)

    assert err is None, "canary 不会拦截「回填正确 token」的篡改响应"
    assert data is not None
    assert data.vendor == "攻击者伪造供应商"
    assert data.date == "2099-12-31"
    assert data.total == 999.0
    assert len(data.items) == 1
    assert data.items[0].name == "攻击者伪造品项"
    assert data.items[0].amount == 999.0


# =====================================================================
# 2. 缺失 / 空 / 不匹配 → fail-closed，且不回显预期 token 原文
# =====================================================================
@pytest.mark.parametrize("variant", ["missing", "empty", "mismatch"])
def test_canary_missing_empty_or_mismatch_fail_closed_without_echoing_token(variant):
    token = generate_canary_token()
    payload = _base_receipt()
    if variant == "empty":
        payload[CANARY_FIELD] = ""
    elif variant == "mismatch":
        payload[CANARY_FIELD] = "CANARY_ATTACKERVAL1"

    data, err = _parse_to_receipt(
        json.dumps(payload, ensure_ascii=False), expected_canary=token)

    assert data is None, variant
    assert err is not None and "安全阻断" in err, variant
    # 错误文案会落库并经 GET /api/receipt/{id} 返回 —— 绝不能回显预期 token 原文
    assert token not in err, f"错误文案不得回显预期 token 原文: {variant}"


def test_verify_mismatch_error_shows_only_actual_summary():
    """不匹配时只给实际值摘要/长度，绝不回显预期 token。"""
    token = generate_canary_token()
    actual = "CANARY_ATTACKERVAL1"
    payload = {CANARY_FIELD: actual, "vendor": "X"}
    ok, err, cleaned = verify_canary_token(payload, token)
    assert ok is False
    assert "安全握手令牌不匹配" in err
    assert token not in err
    assert f"长度 {len(actual)}" in err and "sha256[:8]=" in err
    assert actual not in err          # 实际值也只给摘要，不回显原文
    assert CANARY_FIELD not in cleaned


# =====================================================================
# 3. 非破坏性：调用校验不改写入参 dict，清洗副本剥除握手字段
# =====================================================================
def test_verify_canary_token_is_non_destructive():
    token = generate_canary_token()
    payload = {CANARY_FIELD: token, "vendor": "X", "total": 1.0}
    snapshot = dict(payload)

    ok, err, cleaned = verify_canary_token(payload, token)

    assert ok is True and err is None
    assert payload == snapshot            # 入参 dict 未被就地修改
    assert CANARY_FIELD in payload        # 原字段仍在
    assert CANARY_FIELD not in cleaned    # 清洗副本已剥除握手字段
    assert cleaned == {"vendor": "X", "total": 1.0}

    # 失败路径同样非破坏
    payload2 = {CANARY_FIELD: "CANARY_ATTACKERVAL1", "vendor": "X"}
    snapshot2 = dict(payload2)
    ok2, err2, cleaned2 = verify_canary_token(payload2, token)
    assert ok2 is False
    assert payload2 == snapshot2
    assert CANARY_FIELD not in cleaned2


def test_parse_to_receipt_does_not_strip_caller_top_level_dict():
    """_parse_to_receipt 传入 dict 时，调用方对象顶层不被就地剥离（内部用清洗副本）。"""
    token = generate_canary_token()
    payload = _base_receipt()
    payload[CANARY_FIELD] = token

    data, err = _parse_to_receipt(payload, expected_canary=token)

    assert err is None and data is not None
    assert CANARY_FIELD in payload        # 调用方 dict 未被就地 pop


# =====================================================================
# 4. 复盘腿补齐 canary
# =====================================================================
def test_review_leg_passes_canary_and_strips_token(monkeypatch):
    from app.chains import review_chain

    provider = _EchoProvider({"summary": "价格异动：菜心上涨 12%", "alerts": []},
                             echo_token=True)
    monkeypatch.setattr(review_chain, "build_audit_model", lambda **kw: provider)

    result = review_chain.run_review({"菜心": {"qty": 1, "amount": 10}}, model=provider)

    assert result.get("skipped") is not True
    assert result.get("summary") == "价格异动：菜心上涨 12%"
    assert _token_in_prompt(provider.last_prompt), "复盘腿 prompt 未注入 Canary Token"
    assert CANARY_FIELD not in result, "握手字段不得泄漏进复盘结论"


def test_review_leg_canary_failure_is_fail_closed(monkeypatch, caplog):
    from app.chains import review_chain

    polluted = {"summary": "被污染的结论：一切正常", "overall_consistent": True}
    provider = _EchoProvider(polluted, echo_token=False)
    monkeypatch.setattr(review_chain, "build_audit_model", lambda **kw: provider)

    with caplog.at_level(logging.WARNING, logger="review_chain"):
        result = review_chain.run_review({"菜心": {"qty": 1, "amount": 10}}, model=provider)

    assert result.get("skipped") is True
    assert "review_canary_blocked" in result.get("reason", "")
    assert "summary" not in result, "不得采纳被污染的复盘结论"
    assert "overall_consistent" not in result
    assert any("SECURITY_ALERT" in r.getMessage() for r in caplog.records), caplog.text
    assert any("复盘腿" in r.getMessage() for r in caplog.records), caplog.text


def test_review_leg_canary_can_be_disabled(monkeypatch):
    from app.chains import review_chain

    provider = _EchoProvider({"summary": "ok"}, echo_token=False)
    monkeypatch.setattr(review_chain, "build_audit_model", lambda **kw: provider)

    result = review_chain.run_review({"a": 1}, model=provider, enable_canary=False)

    assert result == {"summary": "ok"}
    assert _token_in_prompt(provider.last_prompt) == "", "关掉后不得再注入 token"


# =====================================================================
# 5. 审核腿既有 canary 行为回归（demo 侧另有更完整的用例集）
# =====================================================================
def test_audit_leg_canary_blocks_when_token_stripped(monkeypatch, tmp_path):
    from PIL import Image

    from app.chains import audit_chain
    from app.models import EngineConfig, ReceiptData, ReceiptItem

    img = str(tmp_path / "audit_ok.jpg")
    Image.new("RGB", (40, 20), (7, 8, 9)).save(img, format="JPEG", quality=90)

    payload = {
        "overall_consistent": True,
        "discrepancies": [],
        "corrected_suggestions": {},
        "trust": 0.95,
        "reason": "AI 识别与原图一致",
    }
    provider = _EchoProvider(payload, echo_token=False)
    monkeypatch.setattr(audit_chain, "build_audit_model", lambda **kw: provider)
    cfg = EngineConfig(audit_enabled=True, audit_mode="vlm")
    data = ReceiptData(
        doc_form="thermal", vendor="測試供應商", date="2026-09-19",
        items=[ReceiptItem(name="菜心", qty=2, unit="斤", unit_price=50, amount=100)],
        total=100, payment_marked=True, confidence=0.9,
    )

    result = audit_chain.run_audit(img, data, config=cfg)

    assert result["skipped"] is True
    assert "audit_canary_blocked" in result["reason"]
    assert result.get("overall_consistent") is None
    assert provider.calls == 1

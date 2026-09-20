# -*- coding: utf-8 -*-
"""T8（P12+P13）回归：修正腿与审核腿的 Canary 防护缺口。

why: 全仓 6 个 provider 调用点中，此前只有识别腿 / 降级回退腿 / 解析腿有 Canary
注入 + 校验。correct_receipt_with_feedback（解析级修正腿）与 audit_chain.run_audit
的 vlm 分支既无注入也无校验 —— 被污染的 provider 可返回任意 JSON 骗过门禁，或单方面
返回 overall_consistent=true 洗白审核。本文件锁定四件事：
  1. 两条腿都真的把动态 token 注入到发给 provider 的 prompt 里；
  2. 回带正确 token → 正常放行；剥离 token → fail-closed（修正返回 None；
     审核按既有约定优雅 skip，不阻断主链路）；
  3. 反证：把校验短路（等价于"去掉校验"）后同一剥离场景不再阻断，证明用例非恒真；
  4. audit_mode=text（默认、不调 LLM）行为不因 Canary 改变。

不碰 DB、不发网络请求（provider 均为假实现）。
"""

import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.chains import audit_chain, extract_chain
from app.models import EngineConfig, ReceiptData, ReceiptItem
from app.services.canary_guard import CANARY_FIELD, verify_canary_in_text

# 假 provider 回带的合同形态修正 JSON（契约合法，供 _parse_to_receipt 走通）
_CORRECT_PAYLOAD = {
    "doc_form": "printed_delivery_note",
    "vendor": "測試供應商",
    "date": "2026-09-19",
    "items": [{"name": "菜心", "qty": 2, "unit": "斤", "unit_price": 50, "amount": 100}],
    "total": 100,
    "payment_marked": True,
    "confidence": 0.9,
}

# 假审核 provider 的结论 JSON（overall_consistent=true = 洗白门禁的关键字段）
_AUDIT_PAYLOAD = {
    "overall_consistent": True,
    "discrepancies": [],
    "corrected_suggestions": {},
    "trust": 0.95,
    "reason": "AI 识别与原图一致",
}

_TOKEN_RE = re.compile(r"CANARY_[0-9A-F]{8}")


def _token_in_prompt(prompt) -> str:
    """从已注入 Canary 的 prompt 里取回动态 token（证明注入真的发生了）。"""
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
    match = _TOKEN_RE.search("\n".join(parts))
    return match.group(0) if match else ""


class _FakeProvider:
    """假 provider：记录调用次数与收到的 prompt；按 echo_token 决定是否回带 token。"""

    kind = "fake"

    def __init__(self, payload, echo_token=True):
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


@pytest.fixture()
def parse_cfg():
    return EngineConfig(parse_llm_enabled=True)


@pytest.fixture()
def audit_cfg():
    return EngineConfig(audit_enabled=True, audit_mode="vlm")


@pytest.fixture()
def audit_image(tmp_path):
    """审核腿要真读图：生成一张小 JPEG，避免走到解码失败的 skip 分支。"""
    from PIL import Image
    path = str(tmp_path / "audit_ok.jpg")
    Image.new("RGB", (40, 20), (7, 8, 9)).save(path, format="JPEG", quality=90)
    return path


def _audit_data(confidence=0.9):
    return ReceiptData(
        doc_form="thermal",
        vendor="測試供應商",
        date="2026-09-19",
        items=[ReceiptItem(name="菜心", qty=2, unit="斤", unit_price=50, amount=100)],
        total=100,
        payment_marked=True,
        confidence=confidence,
    )


# =====================================================================
# 1. 修正腿（extract_chain.correct_receipt_with_feedback）
# =====================================================================
def test_correction_injects_canary_and_passes_on_echo(monkeypatch, parse_cfg):
    """正例：prompt 里真的注入了动态 token；回带后修正被采纳且 token 不污染契约。"""
    model = _FakeProvider(_CORRECT_PAYLOAD, echo_token=True)
    monkeypatch.setattr(extract_chain, "build_parse_model", lambda **kw: model)

    corrected = extract_chain.correct_receipt_with_feedback(
        '{"vendor": "測試供應商"}', "算术门禁: 明细合计=100 但总额=999", config=parse_cfg)

    assert corrected is not None
    assert model.calls == 1
    token = _token_in_prompt(model.last_prompt)
    assert token, "修正腿 prompt 未注入 Canary Token"
    assert json.loads(corrected)[CANARY_FIELD] == token, "回带的 token 应原样保留在文本里"
    # token 在 _parse_to_receipt 里被 pop，不得触发 extra="forbid" 整单打回
    data, err = extract_chain._parse_to_receipt(corrected)
    assert err is None, err
    assert data is not None and data.vendor == "測試供應商"


def test_correction_stripped_token_is_blocked(monkeypatch, parse_cfg, caplog):
    """剥离 token → fail-closed：修正结果被拒（返回 None），且留下安全告警。"""
    model = _FakeProvider(_CORRECT_PAYLOAD, echo_token=False)
    monkeypatch.setattr(extract_chain, "build_parse_model", lambda **kw: model)

    with caplog.at_level(logging.WARNING, logger="extract_chain"):
        corrected = extract_chain.correct_receipt_with_feedback(
            '{"vendor": "測試供應商"}', "算术门禁: 明细合计=100 但总额=999", config=parse_cfg)

    assert corrected is None, "剥离 token 的修正结果必须被拒（fail-closed）"
    assert model.calls == 1, "阻断必须来自校验，而不是没调模型"
    assert any("SECURITY_ALERT" in r.getMessage() for r in caplog.records), caplog.text
    assert any("修正腿" in r.getMessage() for r in caplog.records), caplog.text


def test_correction_wrong_token_is_blocked(monkeypatch, parse_cfg):
    """错 token（冒充者）同样阻断，不能靠"任意非空 token"过关。"""
    model = _FakeProvider(_CORRECT_PAYLOAD, echo_token=False)
    model._payload = dict(_CORRECT_PAYLOAD, **{CANARY_FIELD: "CANARY_DEADBEEF"})
    monkeypatch.setattr(extract_chain, "build_parse_model", lambda **kw: model)

    assert extract_chain.correct_receipt_with_feedback(
        "{}", "契约失败", config=parse_cfg) is None


def test_correction_reverse_proof_without_verification(monkeypatch, parse_cfg):
    """反证：把校验短路成 always-pass（等价于"去掉校验"）→ 同一剥离场景不再阻断。

    若这条能过而上一条失败，说明上一条不是恒真，而是真的在依赖校验。
    """
    model = _FakeProvider(_CORRECT_PAYLOAD, echo_token=False)
    monkeypatch.setattr(extract_chain, "build_parse_model", lambda **kw: model)
    monkeypatch.setattr(extract_chain, "verify_canary_in_text", lambda *a, **kw: (True, None))

    corrected = extract_chain.correct_receipt_with_feedback(
        '{"vendor": "測試供應商"}', "算术门禁", config=parse_cfg)

    assert corrected is not None, "去掉校验后应放行 → 证明前一条用例的阻断来自校验"


def test_correction_canary_can_be_disabled(monkeypatch, parse_cfg):
    """显式关掉 Canary 时不注入也不校验（保留测试/离线通道的开关语义）。"""
    model = _FakeProvider(_CORRECT_PAYLOAD, echo_token=False)
    monkeypatch.setattr(extract_chain, "build_parse_model", lambda **kw: model)

    corrected = extract_chain.correct_receipt_with_feedback(
        "{}", "算术门禁", config=parse_cfg, enable_canary=False)

    assert corrected is not None
    assert _token_in_prompt(model.last_prompt) == "", "关掉后不得再注入 token"


# =====================================================================
# 2. 审核腿（audit_chain.run_audit，vlm 分支）
# =====================================================================
def test_audit_vlm_injects_canary_and_passes_on_echo(monkeypatch, audit_cfg, audit_image):
    """正例：vlm 分支注入 token，回带后审核结论照常返回，且 token 不泄漏到结果里。"""
    model = _FakeProvider(_AUDIT_PAYLOAD, echo_token=True)
    monkeypatch.setattr(audit_chain, "build_audit_model", lambda **kw: model)

    result = audit_chain.run_audit(audit_image, _audit_data(), config=audit_cfg)

    assert result["skipped"] is False
    assert result["overall_consistent"] is True
    assert result["mode"] == "vlm"
    assert _token_in_prompt(model.last_prompt), "审核腿 prompt 未注入 Canary Token"
    assert CANARY_FIELD not in result, "安全令牌不得泄漏进审核结论"


def test_audit_vlm_stripped_token_is_gracefully_skipped(monkeypatch, audit_cfg,
                                                       audit_image, caplog):
    """剥离 token → 优雅降级为 skipped（绝不返回 overall_consistent=true 洗白门禁）。"""
    model = _FakeProvider(_AUDIT_PAYLOAD, echo_token=False)
    monkeypatch.setattr(audit_chain, "build_audit_model", lambda **kw: model)

    with caplog.at_level(logging.WARNING, logger="audit_chain"):
        result = audit_chain.run_audit(audit_image, _audit_data(), config=audit_cfg)

    assert result["skipped"] is True
    assert "audit_canary_blocked" in result["reason"]
    assert result.get("overall_consistent") is None, "不得留下被污染的审核结论"
    assert result["mode"] == "vlm"
    assert "audit_ms" in result and "token_usage" in result, "skip 结构需与既有约定同构"
    assert model.calls == 1
    assert any("SECURITY_ALERT" in r.getMessage() for r in caplog.records), caplog.text
    assert any("审核腿" in r.getMessage() for r in caplog.records), caplog.text


def test_audit_ondemand_low_confidence_also_guarded(monkeypatch, audit_image):
    """ondemand 低置信度升级到 vlm 时同样受保护（不是只有显式 vlm 才生效）。"""
    model = _FakeProvider(_AUDIT_PAYLOAD, echo_token=False)
    monkeypatch.setattr(audit_chain, "build_audit_model", lambda **kw: model)
    cfg = EngineConfig(audit_enabled=True, audit_mode="ondemand")

    result = audit_chain.run_audit(audit_image, _audit_data(confidence=0.1),
                                  config=cfg)

    assert result["skipped"] is True
    assert "audit_canary_blocked" in result["reason"]


def test_audit_reverse_proof_without_verification(monkeypatch, audit_cfg, audit_image):
    """反证：短路校验后同一剥离场景不再 skip（证明阻断来自校验）。"""
    model = _FakeProvider(_AUDIT_PAYLOAD, echo_token=False)
    monkeypatch.setattr(audit_chain, "build_audit_model", lambda **kw: model)
    monkeypatch.setattr(audit_chain, "verify_canary_in_text", lambda *a, **kw: (True, None))

    result = audit_chain.run_audit(audit_image, _audit_data(), config=audit_cfg)

    assert result["skipped"] is False
    assert result["overall_consistent"] is True


def test_audit_text_mode_unaffected_by_canary(monkeypatch, audit_image):
    """text 模式（默认、不调 LLM）：行为零变化 —— 不注入、不调模型、不出现安全字段。"""
    model = _FakeProvider(_AUDIT_PAYLOAD, echo_token=False)
    monkeypatch.setattr(audit_chain, "build_audit_model", lambda **kw: model)
    cfg = EngineConfig(audit_enabled=True, audit_mode="text")

    result = audit_chain.run_audit(audit_image, _audit_data(), config=cfg)

    assert result["mode"] == "text"
    assert result["skipped"] is False
    assert result["engine"] == "code_engine"
    assert model.calls == 0, "text 模式不得因 Canary 变成调 LLM"
    assert CANARY_FIELD not in result


def test_audit_block_does_not_break_supervisor_path(monkeypatch, audit_cfg, audit_image):
    """阻断结果经 supervisor._run_audit 透传时仍是 skip 形状，不抛异常、不阻断主链路。"""
    from app.chains import supervisor

    model = _FakeProvider(_AUDIT_PAYLOAD, echo_token=False)
    monkeypatch.setattr(audit_chain, "build_audit_model", lambda **kw: model)

    audit = supervisor._run_audit(audit_image, _audit_data(), audit_cfg, False)

    assert audit["skipped"] is True
    assert "audit_canary_blocked" in audit["reason"]


# =====================================================================
# 3. 文本通道校验helper：不过度阻断（围栏输出仍能通过）
# =====================================================================
def test_verify_canary_in_text_accepts_fenced_json():
    """带 markdown 围栏的合法输出不得被误判成"token 被剥离"。"""
    payload = dict(_CORRECT_PAYLOAD, **{CANARY_FIELD: "CANARY_1A2B3C4D"})
    raw = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    ok, err = verify_canary_in_text(raw, "CANARY_1A2B3C4D")
    assert ok is True and err is None


def test_verify_canary_in_text_fails_closed_on_non_json():
    """提取不出 JSON → fail-closed（无法证明握手即视为被剥离）。"""
    ok, err = verify_canary_in_text("抱歉，我无法完成该请求。", "CANARY_1A2B3C4D")
    assert ok is False and err

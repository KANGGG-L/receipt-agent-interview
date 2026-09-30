# -*- coding: utf-8 -*-
"""W-1 / Gap H4：首轮注入开关（memory_first_round_inject_enabled）+ effective_hint 并存。

本文件钉死两件事（均取**模型侧可观测证据**，不用函数返回值自证）：

  W1-a  effective_hint 二选一语义已修：vendor_ctx（记忆先验）与 vendor_hint（用户 hint）
        并存 —— 二者都在最终 prompt 中出现（此前 `vendor_ctx or vendor_hint` 记忆一有
        就把 hint 顶掉）。
  W1-b  首轮注入开关（缺省关 = 与现状逐字节一致）：
        - 关：无 hint 首轮不注入（prompt 无记忆哨兵、无标记）；
        - 开 + 可确定供应商 + 有记忆：首轮 prompt 就含该供应商的结构化事实先验，
          并落「首轮注入」标记（state.rag_hint_source / 快照 / [prior:memory] 前缀）；
        - 开 + 无从确定供应商：不猜、不注入。

反证口径（实测写在交付报告里，不写进用例）：把首轮注入逻辑关掉 → (b) 翻红；
把 effective_hint 改回 `or` → (d) 翻红。

全程离线：临时 DB / 临时 RAG_DIR / 假引擎 / 打桩 vendor_precheck，零网络、零真实模型。
"""

import json
import os
import sys

import pytest
from PIL import Image

os.environ.setdefault("DB_PATH", "/tmp/w1_first_round_inject.db")
os.environ.setdefault("RAG_DIR", "/tmp/w1_first_round_inject_rag")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.chains import extract_chain, supervisor  # noqa: E402
from app.models import EngineConfig  # noqa: E402
from app.services import rag, settings_service, vendor_precheck  # noqa: E402

SENTINEL_ITEM = "W1哨兵菜心"
SENTINEL_LAYOUT = "ncr_handwritten"
HINT_VENDOR = "德利行"
BARE_VENDOR = "W1無記憶商號"


def _valid_json() -> str:
    return json.dumps({
        "vendor": HINT_VENDOR,
        "date": "2026-01-01",
        "doc_form": "ncr_handwritten",
        "payment_marked": False,
        "confidence": 0.9,
        "items": [{"name": "菜心", "qty": 2.0, "unit": "斤",
                   "unit_price": 5.0, "amount": 10.0}],
        "total": 10.0,
    })


class _RecordingModel:
    kind = "openai"

    def __init__(self, output=""):
        self.calls = 0
        self.prompts = []
        self._output = output

    def invoke(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return self._output


def _prompt_text(prompt) -> str:
    parts = []

    def _walk(content):
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, (list, tuple)):
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)

    for msg in prompt or []:
        _walk(getattr(msg, "content", msg))
    return "\n".join(parts)


def _valid_png(tmp_path):
    p = str(tmp_path / "ok.png")
    Image.new("RGB", (40, 20), (7, 7, 7)).save(p, format="PNG")
    return p


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每例独立临时库（本文件要往 vendor_memory / app_settings 写，绝不碰 live 库）。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "w1_first_round_inject.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "_memory_log_path",
                        lambda: str(tmp_path / "parse_log.jsonl"))
    monkeypatch.setattr(extract_chain, "generate_canary_token", lambda: "")
    monkeypatch.setattr(extract_chain, "_dashscope_fallback_available",
                        lambda *a, **k: False)
    monkeypatch.setattr(supervisor, "_run_audit",
                        lambda *a, **k: {"skipped": True, "reason": "test"})


def _seed_memory(vendor=HINT_VENDOR, tenant_id="default"):
    rag.ingest_memory(vendor, f"1. {SENTINEL_ITEM} 2斤 @5 = 10",
                      notes=f"版式：{SENTINEL_LAYOUT}", tenant_id=tenant_id)


def _install_model(monkeypatch, model):
    monkeypatch.setattr(supervisor, "build_recognition_model", lambda *a, **k: model)


def _set_switch(on: bool):
    settings_service.set_value("memory_first_round_inject_enabled", bool(on))


def _stub_precheck(monkeypatch, vendor, enabled=True):
    """打桩 vendor_precheck：is_enabled / predict_vendor 均记录调用。"""
    calls = {"predict": 0}

    monkeypatch.setattr(vendor_precheck, "is_enabled", lambda: enabled)

    def _predict(image_path, cfg=None):
        calls["predict"] += 1
        return vendor

    monkeypatch.setattr(vendor_precheck, "predict_vendor", _predict)
    return calls


def _run(tmp_path, hint="", tenant_id="default", max_rounds=2):
    return supervisor.run_pipeline(
        _valid_png(tmp_path), vendor_hint=hint, tenant_id=tenant_id,
        config=EngineConfig(max_retry_rounds=max_rounds))


# -------------------------------------------------------------
# (a) 开关关：无 hint 首轮不注入（与现状逐字节一致）
# -------------------------------------------------------------
def test_switch_off_no_hint_first_round_no_injection(tmp_path, monkeypatch):
    _seed_memory()
    _set_switch(False)
    pre = _stub_precheck(monkeypatch, HINT_VENDOR, enabled=True)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    state = _run(tmp_path, hint="")

    first_prompt = _prompt_text(model.prompts[0])
    assert SENTINEL_ITEM not in first_prompt, "开关关时无 hint 首轮不得注入记忆"
    assert state["rag_hint_source"] == "", "开关关时不得落首轮注入标记"
    assert not any(s.get("action") == "first_round_inject" for s in state["log"]), \
        "开关关时不得有首轮注入快照"
    assert pre["predict"] == 0, "开关关时不得调用 vendor_precheck（零额外模型调用）"


# -------------------------------------------------------------
# (b) 开关开 + 可确定供应商 + 有记忆：首轮 prompt 出现结构化事实先验 + 落标记
# -------------------------------------------------------------
def test_switch_on_first_round_injects_memory(tmp_path, monkeypatch):
    _seed_memory()
    _set_switch(True)
    _stub_precheck(monkeypatch, HINT_VENDOR, enabled=True)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    retrievals = []
    real_retrieve = extract_chain.retrieve_context

    def _spy(vendor, *a, **kw):
        retrievals.append((vendor, kw.get("tenant_id")))
        return real_retrieve(vendor, *a, **kw)

    monkeypatch.setattr(extract_chain, "retrieve_context", _spy)

    state = _run(tmp_path, hint="")

    assert model.calls == 1 and state["attempt"] == 1, "夹具应首轮即成功"
    assert state["data"] is not None
    # 检索确实发生在首轮（hint 通道），且用的就是预判出的供应商
    assert retrievals == [(HINT_VENDOR, "default")], retrievals

    first_prompt = _prompt_text(model.prompts[0])
    assert SENTINEL_ITEM in first_prompt, \
        f"首轮 prompt 未含记忆明细正文：{first_prompt[:400]!r}"
    assert SENTINEL_LAYOUT in first_prompt, "首轮 prompt 未含记忆版式先验"
    assert '<vendor_context data_only="true"' in first_prompt, \
        "注入必须先过 XML 数据沙箱"

    # 可观测标记：state 标记 + 快照 + rag_context_json 前缀（与重试轮 [prior:retry] 区分）
    assert state["rag_hint_source"] == "first_round"
    assert any(s.get("action") == "first_round_inject" for s in state["log"])
    assert state["vendor_context"].startswith("[prior:hint]"), state["vendor_context"][:120]
    assert "[prior:memory]" in state["vendor_context"], "记忆先验未与 hint 并存"
    assert "[prior:retry]" not in state["vendor_context"]


# -------------------------------------------------------------
# (c) 开关开 + 无从确定供应商：不猜、不注入
# -------------------------------------------------------------
def test_switch_on_no_vendor_no_guess(tmp_path, monkeypatch):
    _seed_memory()
    _set_switch(True)
    _stub_precheck(monkeypatch, "", enabled=True)  # 预判开关开，但预判不出供应商
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    state = _run(tmp_path, hint="")

    first_prompt = _prompt_text(model.prompts[0])
    assert SENTINEL_ITEM not in first_prompt, "无从确定供应商时不得注入记忆（不猜）"
    assert state["rag_hint_source"] == ""


def test_switch_on_precheck_disabled_does_not_call_model(tmp_path, monkeypatch):
    """供应商来源 #2 仅在其自身开关也为开时才发模型调用。"""
    _seed_memory()
    _set_switch(True)
    pre = _stub_precheck(monkeypatch, HINT_VENDOR, enabled=False)
    model = _RecordingModel(output=_valid_json())
    _install_model(monkeypatch, model)

    state = _run(tmp_path, hint="")

    assert pre["predict"] == 0, "vendor_precheck 未开启时不得强行调用预判模型"
    assert state["rag_hint_source"] == ""
    assert SENTINEL_ITEM not in _prompt_text(model.prompts[0])


# -------------------------------------------------------------
# (d) effective_hint 并存：vendor_ctx 与 vendor_hint 同时存在时两者都在 prompt 中
# -------------------------------------------------------------
def test_effective_hint_coexists_hint_and_memory(tmp_path, monkeypatch):
    _seed_memory()
    model = _RecordingModel(output=_valid_json())
    img = _valid_png(tmp_path)

    result = extract_chain.extract_receipt(
        img, vendor_hint=HINT_VENDOR, model=model, config=None,
        tenant_id="default", enable_canary=False)

    prompt = _prompt_text(model.prompts[0])
    assert HINT_VENDOR in prompt, "用户 hint 被记忆先验顶掉了（二选一语义未修）"
    assert SENTINEL_ITEM in prompt, "记忆先验未与 hint 并存"
    # 两个来源在 prompt 中各自成块（hint 块 + memory 块），不再二选一
    assert "[prior:hint]" in prompt, prompt[:400]
    assert "[prior:memory]" in prompt, prompt[:400]
    # rag_context_json 里两个来源各自成块
    assert "[prior:hint]" in result["vendor_context"], result["vendor_context"][:200]
    assert "[prior:memory]" in result["vendor_context"], result["vendor_context"][:200]


# -------------------------------------------------------------
# (e) 既有通道行为不变：hint 通道 / retry 通道
# -------------------------------------------------------------
def test_hint_channel_without_memory_still_bare(tmp_path, monkeypatch):
    """hint 非空但该供应商无记忆 → 只注入裸 hint 名（既有行为不变）。"""
    _seed_memory()  # 记忆只给 HINT_VENDOR
    model = _RecordingModel(output=_valid_json())
    img = _valid_png(tmp_path)

    result = extract_chain.extract_receipt(
        img, vendor_hint=BARE_VENDOR, model=model, config=None,
        tenant_id="default", enable_canary=False)

    prompt = _prompt_text(model.prompts[0])
    assert BARE_VENDOR in prompt
    assert SENTINEL_ITEM not in prompt
    assert result["vendor_context"] == f"[prior:hint]\n{BARE_VENDOR}", result["vendor_context"]


def test_retry_channel_unchanged(tmp_path, monkeypatch):
    """retry 通道（vendor_prior）仍以 [prior:retry] 注入，语义不变。"""
    model = _RecordingModel(output=_valid_json())
    img = _valid_png(tmp_path)

    result = extract_chain.extract_receipt(
        img, vendor_hint="", vendor_prior="历史先验正文", model=model,
        config=None, tenant_id="default", enable_canary=False)

    prompt = _prompt_text(model.prompts[0])
    assert "[prior:retry]" in prompt
    assert "历史先验正文" in prompt
    assert "[prior:retry]" in result["vendor_context"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

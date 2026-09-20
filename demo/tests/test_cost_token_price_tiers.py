# -*- coding: utf-8 -*-
"""成本单价按引擎/模型分档的回归用例（T6/R3）。

why：llm._calc_cost_hkd 新增了 model 形参做按引擎区分单价（omni / vl-flash / GLM-4.5V），
但若产线调用点不传 model，vl-flash 档就永远不可达——所有成本被按默认 omni 档计，
用 vl-flash 时高估约 11 倍、用 SF GLM 审核腿时高估约 2.2 倍，且两处兜底还硬编码了
已废止的 vl-flash 0.15/1M，与档位表自相矛盾。本文件同时守住「档位解析」与
「三个产线调用点的接线」两层，并反证「不传 model 时走显式默认档而非静默错档」。

用例只调纯函数/被 monkeypatch 的落库函数，不写任何真实数据库。
"""

from types import SimpleNamespace

import pytest


def _tu(prompt=1_000_000, completion=100_000):
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


# ---------------- 第一层：档位解析 ----------------

def test_resolve_token_price_omni_tier():
    from app.llm import _resolve_token_price
    assert _resolve_token_price("qwen3.5-omni-flash") == (2.2, 13.3)


def test_resolve_token_price_vl_flash_tier_reachable():
    """vl-flash 必须能解析到自己的档位（历史 ¥0.0022/张 口径的来源）。"""
    from app.llm import _resolve_token_price
    assert _resolve_token_price("qwen3-vl-flash") == (0.15, 1.50)
    # 大小写与完整路径前缀都要能命中（配置里存的是完整模型名）
    assert _resolve_token_price("Qwen/Qwen3-VL-Flash") == (0.15, 1.50)


def test_resolve_token_price_glm_audit_tier():
    """默认审核腿 SF GLM-4.5V 必须落到自己的档位，而不是被按 omni 档高估。"""
    from app.llm import _resolve_token_price
    assert _resolve_token_price("zai-org/GLM-4.5V") == (1.0, 6.0)


def test_resolve_token_price_default_tier_when_model_missing():
    """反证基线：不传 model / 未知模型走显式默认档（omni），不是 vl-flash 旧价。

    若把默认档改回 vl-flash，本用例失败——这正是「静默错档」的哨兵。
    """
    from app.llm import _resolve_token_price
    assert _resolve_token_price("") == (2.2, 13.3)
    # 未收录的模型名（历史注记：此处原用已弃用的 CLI 模型名 opencode/mimo-v2.5-free，
    # 它同样不在档位表里，故换成中性未知名，断言与语义不变）
    assert _resolve_token_price("some-unknown/vendor-model") == (2.2, 13.3)
    assert _resolve_token_price(None) == (2.2, 13.3)


# ---------------- 第二层：按档位计价 ----------------

def test_calc_cost_hkd_by_tier():
    from app.llm import _calc_cost_hkd
    assert _calc_cost_hkd(_tu(), model="qwen3.5-omni-flash") == 3.53
    assert _calc_cost_hkd(_tu(), model="qwen3-vl-flash") == 0.3
    assert _calc_cost_hkd(_tu(), model="zai-org/GLM-4.5V") == 1.6


def test_calc_cost_hkd_default_tier_is_omni_not_vl_flash():
    """不传 model 时按默认 omni 档计（宁可高估不可低估），且与 vl-flash 档差约 11 倍。"""
    from app.llm import _calc_cost_hkd
    default_cost = _calc_cost_hkd(_tu())
    vl_flash_cost = _calc_cost_hkd(_tu(), model="qwen3-vl-flash")
    assert default_cost == 3.53
    assert vl_flash_cost == 0.3
    assert default_cost / vl_flash_cost > 8


def test_calc_cost_hkd_total_only_uses_tier_input_price():
    """仅有 total_tokens 时按该档输入单价近似，不虚增成本。"""
    from app.llm import _calc_cost_hkd
    tu = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 110_000}
    assert _calc_cost_hkd(tu, model="qwen3-vl-flash") == 0.0165
    assert _calc_cost_hkd(tu) == 0.242


# ---------------- 第三层：产线调用点接线 ----------------

def test_extract_cost_from_tokens_passes_tier_name():
    """extract_chain._cost_from_tokens 必须把模型名透传给 _calc_cost_hkd。"""
    from app.chains.extract_chain import _cost_from_tokens, _model_cost_tier_name
    assert _cost_from_tokens(_tu(), "qwen3-vl-flash") == 0.3
    assert _cost_from_tokens(_tu(), "zai-org/GLM-4.5V") == 1.6
    # 不传 → 默认档（反证：vl-flash 价不会凭空生效）
    assert _cost_from_tokens(_tu()) == 3.53
    # 模型对象取名：有 .model 取之，无则空串（回默认档）
    assert _model_cost_tier_name(SimpleNamespace(model="qwen3-vl-flash")) == "qwen3-vl-flash"
    assert _model_cost_tier_name(object()) == ""


def test_log_extract_decision_uses_recognition_model_tier(monkeypatch):
    """supervisor._log_extract_decision 必须按 config.recognition_model 档位计价。"""
    from app.chains import supervisor

    captured = {}

    def _fake_log_ai_decision(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(supervisor.db, "log_ai_decision", _fake_log_ai_decision)

    cfg = SimpleNamespace(recognition_model="qwen3-vl-flash",
                          grey_recognition_model="Qwen/Qwen3-VL-32B-Instruct")
    supervisor._log_extract_decision(
        1, None, cfg, False, 1, "openai", "extract_ok",
        token_usage=_tu(), cost_hkd=None,
    )
    assert captured["extra"]["cost_hkd"] == 0.3
    # 决策落库的 model 字段与计价用的是同一个模型名
    assert captured["model"] == "qwen3-vl-flash"


def test_log_audit_decision_uses_audit_model_tier(monkeypatch):
    """supervisor._log_audit_decision 必须按审核模型（SF GLM）档位计价。"""
    from app.chains import supervisor

    captured = {}

    def _fake_log_ai_decision(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(supervisor.db, "log_ai_decision", _fake_log_ai_decision)

    cfg = SimpleNamespace(audit_engine="openai", audit_model="zai-org/GLM-4.5V")
    audit = {"overall_consistent": True, "trust": 0.9, "discrepancies": [],
             "token_usage": _tu(), "audit_ms": 12.0, "skipped": False}
    supervisor._log_audit_decision(1, None, cfg, False, audit)

    assert captured["extra"]["cost_hkd"] == 1.6
    assert captured["model"] == "zai-org/GLM-4.5V"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

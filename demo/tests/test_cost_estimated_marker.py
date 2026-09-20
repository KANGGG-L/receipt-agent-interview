# -*- coding: utf-8 -*-
"""成本可信度标记（cost_estimated）与成本护栏可判定性回归用例。

why：成本链路里有一串静默兜底——token 解析不出来就记 0、`_calc_cost_hkd` 抛错就转
兜底单价甚至 `0.0`——它们都会把 `extra.cost_hkd` 写成 0 或低估值，而
`guardian` 的成本护栏（cost_rise_pct）读的正是这个值：当 control 的平均成本变成 0，
`c["avg_cost"] > 0` 直接为假，护栏连分支都进不去，「真实成本上涨 200%」在大盘与护栏上
完全不可见。这就是「异常被静默吞掉 -> 功能静默失效」的典型形态。

本文件守住四层：
1. 正常路径：cost_estimated=0 且 cost_hkd 数值不变（不改变正常路径任何数值）；
2. 异常路径：cost_estimated=1 + reason + 告警，让「算不出来」与「真实 0」可区分；
3. 护栏侧：对估算行不再静默给出结论，改为显式告警并把 avg_cost 置为不可判定；
4. 反证：把标记回滚掉（恒为 0）后，第 2/3 层用例必须失败。
"""

import logging
from types import SimpleNamespace

import pytest

import app.llm as llm
from app.chains import supervisor


def _tu(prompt=300_000, completion=300_000):
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _capture(monkeypatch):
    """拦截决策落库，拿到 extra（不写真实库）。"""
    captured = {}

    def _fake_log_ai_decision(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(supervisor.db, "log_ai_decision", _fake_log_ai_decision)
    return captured


# ---------------- 第一层：正常路径不得改变 ----------------

def test_measured_cost_is_not_marked_estimated(monkeypatch):
    """真实 token + 真实单价 -> cost_estimated=0，数值与改动前逐值一致。"""
    cfg = SimpleNamespace(recognition_model="qwen3.5-omni-flash",
                          grey_recognition_model="qwen3.5-omni-flash")
    cap = _capture(monkeypatch)
    supervisor._log_extract_decision(
        1, None, cfg, False, 1, "openai", "extract_ok",
        token_usage=_tu(), cost_hkd=None,
    )
    # 600k tokens：300k*2.2/1M + 300k*13.3/1M = 0.66 + 3.99
    assert cap["extra"]["cost_hkd"] == 4.65
    assert cap["extra"]["tokens_total"] == 600000
    assert cap["extra"]["cost_estimated"] == 0
    assert cap["extra"]["cost_estimated_reason"] == ""


# ---------------- 第二层：异常路径必须留痕 ----------------

def test_cost_zero_with_tokens_is_marked_estimated(monkeypatch, caplog):
    """生产形态：extract_chain 把成本静默吞成 0.0 后传进来 -> 必须标记为估算值。

    这是最关键的一档：有 token 却算出 0 成本，只可能是上游成本链路异常。
    """
    cfg = SimpleNamespace(recognition_model="qwen3.5-omni-flash",
                          grey_recognition_model="qwen3.5-omni-flash")
    cap = _capture(monkeypatch)
    with caplog.at_level(logging.WARNING):
        supervisor._log_extract_decision(
            1, None, cfg, False, 1, "openai", "extract_ok",
            token_usage=_tu(), cost_hkd=0.0,      # <- 生产里 extract_chain 的失败产物
        )
    assert cap["extra"]["cost_hkd"] == 0.0
    assert cap["extra"]["cost_estimated"] == 1
    assert cap["extra"]["cost_estimated_reason"] == "cost_zero_with_tokens"
    assert any("成本链路异常" in str(r.getMessage()) for r in caplog.records), \
        "必须留 warning，否则该异常不可观测"


def test_no_token_usage_is_marked_estimated(monkeypatch):
    """没有任何 token 测量值（引擎未回传 usage）-> 成本不可验证，标记为估算值。"""
    cfg = SimpleNamespace(recognition_model="qwen3.5-omni-flash",
                          grey_recognition_model="qwen3.5-omni-flash")
    cap = _capture(monkeypatch)
    supervisor._log_extract_decision(
        1, None, cfg, False, 1, "openai", "extract_ok",
        token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        cost_hkd=0.0,
    )
    assert cap["extra"]["cost_estimated"] == 1
    assert cap["extra"]["cost_estimated_reason"] == "no_token_usage"


def test_cost_calc_failure_falls_back_and_is_traceable(monkeypatch, caplog):
    """_calc_cost_hkd 与 _resolve_token_price 双双失败 -> 走默认兜底单价，但必须可追溯。"""
    cfg = SimpleNamespace(recognition_model="qwen3.5-omni-flash",
                          grey_recognition_model="qwen3.5-omni-flash")
    cap = _capture(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("模拟成本计算失败")

    monkeypatch.setattr(llm, "_calc_cost_hkd", _boom)
    monkeypatch.setattr(llm, "_resolve_token_price", _boom)
    with caplog.at_level(logging.WARNING):
        supervisor._log_extract_decision(
            1, None, cfg, False, 1, "openai", "extract_ok",
            token_usage=_tu(), cost_hkd=None,
        )
    # 600k * _DEFAULT_COST_IN_PRICE(2.2) / 1M
    assert cap["extra"]["cost_hkd"] == 1.32
    assert cap["extra"]["cost_estimated"] == 1
    assert cap["extra"]["cost_estimated_reason"] == "price_table_failed"
    assert any("单价表解析失败" in str(r.getMessage()) for r in caplog.records)


# ---------------- 第三层 + 反证：护栏可判定性 ----------------

def _seed_experiment(measured_treatment: bool):
    """建一个 running 实验，两侧各 2 行 extract 决策。返回 experiment_id。"""
    from app import db

    exp = db.create_experiment(name="cost-estimated-case", min_sample=2)
    exp_id = exp["id"]
    db.start_experiment(exp_id)
    rid_c = db.create_receipt(supplier_name="C")
    rid_t = db.create_receipt(supplier_name="T")

    def _row(receipt_id, grp, cost, estimated):
        db.log_ai_decision(
            receipt_id=receipt_id, experiment_id=exp_id, grp=grp,
            engine="openai", model="qwen3.5-omni-flash", decision_type="extract",
            field_path="overall", ai_value={"status": "extract_ok"},
            extra={"tokens_total": 600000, "cost_hkd": cost,
                   "cost_estimated": estimated,
                   "cost_estimated_reason": "" if not estimated else "cost_zero_with_tokens",
                   "elapsed_ms": {"total": 1000.0}, "success": True},
        )

    _row(rid_c, "control", 1.55, 0)
    _row(rid_c, "control", 1.55, 0)
    # 真实成本 4.65（涨幅 200%），但若判为估算则不应据此回滚
    _row(rid_t, "treatment", 0.0 if not measured_treatment else 4.65,
         0 if measured_treatment else 1)
    _row(rid_t, "treatment", 0.0 if not measured_treatment else 4.65,
         0 if measured_treatment else 1)
    return exp_id


def test_guardian_rolls_back_on_measured_cost_rise():
    """反证基线：成本为真实测量值时，200% 涨幅必须触发回滚（否则护栏本就是死的）。"""
    from app.services import guardian

    _seed_experiment(measured_treatment=True)
    kinds = [a["kind"] for a in guardian.check_once()]
    assert "rollback" in kinds, "真实成本上涨 200% 必须触发回滚"


def test_guardian_does_not_conclude_from_estimated_costs(caplog):
    """估算行的 cost_hkd 不是测量值：护栏不得据此下结论，且必须显式告警。"""
    from app.services import guardian

    _seed_experiment(measured_treatment=False)
    with caplog.at_level(logging.WARNING):
        actions = guardian.check_once()
    assert actions == [], "估算成本不得作为回滚依据"
    # 估算行的平均成本必须是「不可判定」而不是 0
    rows = guardian._group_metrics(
        [{"grp": "treatment", "decision_type": "extract",
          "extra": {"cost_hkd": 0.0, "cost_estimated": 1, "tokens_total": 0, "success": True}}])
    assert rows["avg_cost"] is None
    assert rows["cost_estimated_count"] == 1
    assert any("成本护栏不可判定" in str(r.getMessage()) for r in caplog.records), \
        "无法判定必须显式告警，否则「没回滚」会被误读成「成本没涨」"


# ---------------- 上游标记透传（防「修 A 漏 B」）----------------

def test_cost_from_tokens_ex_marks_price_table_fallback(monkeypatch):
    """extract_chain 侧（产线活跃的那条兜底）必须回传估算标记与原因。"""
    from app.chains import extract_chain

    def _boom(*a, **k):
        raise RuntimeError("模拟 _calc_cost_hkd 失败")

    monkeypatch.setattr(llm, "_calc_cost_hkd", _boom)
    cost, est, why = extract_chain._cost_from_tokens_ex(_tu(), "qwen3-vl-flash")
    assert est is True
    assert why == "price_table_fallback"
    # 兜底只按该模型输入单价：600k * 0.15 / 1M = 0.09（远低于真实 0.495）
    assert cost == 0.09
    # 兼容旧签名：_cost_from_tokens 仍返回 float
    assert isinstance(extract_chain._cost_from_tokens(_tu(), "qwen3-vl-flash"), float)


def test_upstream_estimated_flag_is_inherited(monkeypatch):
    """上游判为估算的成本必须被 supervisor 继承，不能因本地算出非 0 值就丢掉标记。"""
    cfg = SimpleNamespace(recognition_model="qwen3-vl-flash",
                          grey_recognition_model="qwen3-vl-flash")
    cap = _capture(monkeypatch)
    supervisor._log_extract_decision(
        1, None, cfg, False, 1, "openai", "extract_ok",
        token_usage=_tu(), cost_hkd=1.32,
        cost_estimated_in=True, cost_estimated_reason_in="price_table_fallback",
    )
    assert cap["extra"]["cost_estimated"] == 1
    assert cap["extra"]["cost_estimated_reason"] == "price_table_fallback"


def test_extract_chain_result_carries_cost_marker_keys():
    """extract_receipt 的每个 result 字典都必须同时带 cost_hkd 与成本可信度标记。"""
    import inspect
    from app.chains import extract_chain

    src = inspect.getsource(extract_chain.extract_receipt)
    assert src.count('**_cost_meta()') == 3
    assert src.count('**_cost_meta()') == src.count('"cost_hkd"'), \
        "每个产出 cost_hkd 的 result 都必须同时产出成本可信度标记"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

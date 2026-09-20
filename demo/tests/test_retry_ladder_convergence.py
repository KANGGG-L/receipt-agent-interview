# -*- coding: utf-8 -*-
"""T11/P10 回归：重试阶梯只服务「引擎瞬时故障」，其余失败一律短路不整图重跑。

why: 修复前 supervisor 的重试循环把三类完全不同的失败混为一谈——
  (1) 引擎瞬时故障（上游 5xx / 超时）：换一轮确有可能成功，**需要**重试；
  (2) 输出质量失败（JSON 不可解析 / 契约不过）：下一轮 prompt 与首轮逐字相同
      （error 分支不设 retry_feedback、失败轮不补 RAG 先验），temperature≈0 下
      近确定性复现——整图重跑改变不了结果，却白烧 1-2 轮 VLM（各 40s 级）；
  (3) 确定性失败（图片解码失败 / auth、param 配置错误）：任何引擎都不可能成功。
本文件锁住收敛后的行为（每例都统计真实 invoke 次数，不以 attempt 代打卡数）：
  - 输出质量失败 → attempt=1、invoke=1、走 output_reject_fast；
  - auth 归类的确定性失败 → attempt=1、invoke=1、走 deterministic_fail；
  - 门禁快速反馈（算术）→ attempt=1、invoke=1；
  - 引擎瞬时故障（未归类异常）→ 仍跑满上限、invoke=MAX_RETRY（防"少重试了"被当成修好）；
  - EngineConfig.max_retry_rounds 生效且被钳制到 [1, MAX_RETRY_LIMIT]。

全程离线：隔离临时库 + 假引擎，零网络、零真实引擎调用、零 live 库写入。
"""

import json
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.chains import extract_chain, supervisor
from app.models import EngineConfig


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每例独立临时库（本组用例 receipt_id=None 本不写库，仍强制隔离以防隐式写入）。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_retry_convergence.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


@pytest.fixture(autouse=True)
def isolated_memory_log(monkeypatch, tmp_path):
    """记忆落盘重定向到临时目录，避免污染 artifacts/memory/parse_log.jsonl。"""
    monkeypatch.setattr(supervisor, "_memory_log_path",
                        lambda: str(tmp_path / "parse_log.jsonl"))


@pytest.fixture(autouse=True)
def no_canary(monkeypatch):
    """关闭 Canary 注入：假引擎不会回带 token，避免被 canary 校验拦下干扰断言。"""
    monkeypatch.setattr(extract_chain, "generate_canary_token", lambda: "")


@pytest.fixture(autouse=True)
def stub_audit(monkeypatch):
    """审核腿打桩（走完门禁的成功路径会进入审核；本组只关心重试阶梯）。"""
    monkeypatch.setattr(supervisor, "_run_audit",
                        lambda *a, **k: {"skipped": True, "reason": "test"})


def _valid_png(tmp_path):
    p = str(tmp_path / "ok.png")
    Image.new("RGB", (40, 20), (7, 7, 7)).save(p, format="PNG")
    return p


class _CountingModel:
    """统计 invoke 次数的假引擎：按给定产出返回，或按给定异常抛出。"""

    kind = "openai"  # 配合测试里关闭引擎兜底 → 走"调用失败/产出"返回

    def __init__(self, output="", exc=None):
        self.calls = 0
        self._output = output
        self._exc = exc

    def invoke(self, prompt):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._output


class _AuthError(RuntimeError):
    """模拟 llm.EngineAuthError：category="auth"（P9 归类，重试同一 key 无意义）。"""

    category = "auth"


class _UpstreamError(RuntimeError):
    """模拟 llm.UpstreamServiceError：category="upstream"（服务商侧瞬时故障，需重试）。"""

    category = "upstream"


def _patch_build(monkeypatch, model, counter):
    def _fake_build(*args, **kwargs):
        counter["n"] += 1
        return model
    monkeypatch.setattr(supervisor, "build_recognition_model", _fake_build)
    # 关闭识别引擎的自动兜底：本文件断言的是「重试阶梯」本身，兜底可用会把失败吞掉。
    # 历史注记：兜底原为本机 CodeBuddy CLI（判据是模型 kind != "codebuddy"），
    # 已于 2026-09-02 弃用，现为 DashScope 官方通道兜底（判据见下）。
    monkeypatch.setattr(extract_chain, "_dashscope_fallback_available",
                        lambda *a, **kw: False)


def _log_actions(state):
    return [e.get("action") for e in (state.get("log") or []) if isinstance(e, dict)]


def _run(tmp_path, monkeypatch, model, **cfg_kw):
    counter = {"n": 0}
    _patch_build(monkeypatch, model, counter)
    cfg = EngineConfig(**cfg_kw) if cfg_kw else None
    state = supervisor.run_pipeline(_valid_png(tmp_path), config=cfg)
    assert state["attempt"] == model.calls, "attempt 必须与实际 VLM 调用次数一致"
    return state, counter


# ---------------------------------------------------------------
# 1. 输出质量失败（JSON 不可解析）：一次调用即快速反馈，不再整图重跑
# ---------------------------------------------------------------
def test_output_quality_failure_short_circuits(tmp_path, monkeypatch):
    # 无 JSON 花括号 → _parse_to_receipt 返回「输出不是合法 JSON」，且该返回不带 error_category
    model = _CountingModel(output="抱歉，这张图我看不清。")
    state, counter = _run(tmp_path, monkeypatch, model)

    assert model.calls == 1, f"输出质量失败不得重跑（修复前 3 次），实际 invoke={model.calls}"
    assert counter["n"] == 1
    assert state["attempt"] == 1
    assert state["retry_count"] == 0
    assert state.get("output_reject_fast") is True
    assert not state.get("deterministic_error")
    # 终态仍是既有 error 收口（data 为 None），文案不得伪装成"重试耗尽"
    assert state["status"] == "error"
    assert state["success"] is False
    assert state["data"] is None
    actions = _log_actions(state)
    assert "output_reject_fast" in actions, actions
    assert actions.count("extract_fail") == 1, f"不应重复记失败日志: {actions}"


def test_output_quality_error_marker_matches_key_absence(tmp_path, monkeypatch):
    """锁住判据：JSON/契约失败返回不带 error_category，引擎失败返回带。"""
    # 关闭识别引擎的自动兜底：否则 DashScope 兜底会真的发起调用并返回正常结果，
    # 让「引擎失败」这一段测不到 error_category（本用例只关心判据本身）。
    monkeypatch.setattr(extract_chain, "_dashscope_fallback_available",
                        lambda *a, **kw: False)
    out_ret = extract_chain.extract_receipt(
        _valid_png(tmp_path), model=_CountingModel(output="不是 JSON"), enable_canary=False)
    assert out_ret["error"] and "error_category" not in out_ret
    assert supervisor._is_output_quality_error(out_ret) is True

    boom_ret = extract_chain.extract_receipt(
        _valid_png(tmp_path), model=_CountingModel(exc=RuntimeError("boom")),
        enable_canary=False)
    assert boom_ret["error"] and len(boom_ret["error"]) > 0
    assert "error_category" in boom_ret, "引擎失败必须带归类（否则会被误判成输出质量问题）"
    assert supervisor._is_output_quality_error(boom_ret) is False


# ---------------------------------------------------------------
# 2. 确定性失败（auth 归类）：一次调用即短路
# ---------------------------------------------------------------
def test_auth_category_failure_is_deterministic(tmp_path, monkeypatch):
    model = _CountingModel(exc=_AuthError("invalid api key"))
    state, _ = _run(tmp_path, monkeypatch, model)

    assert model.calls == 1, f"鉴权失败重试同一 key 无意义，实际 invoke={model.calls}"
    assert state["attempt"] == 1
    assert state.get("deterministic_error") is True
    assert not state.get("output_reject_fast")
    assert "deterministic_fail" in _log_actions(state)
    assert state["status"] == "error"


def test_deterministic_category_classifier():
    """判据单测：auth/param 确定性；upstream 与未归类异常仍可重试。"""
    assert supervisor._is_deterministic_engine_error({"error_category": "auth"}) is True
    assert supervisor._is_deterministic_engine_error({"error_category": "param"}) is True
    assert supervisor._is_deterministic_engine_error({"error_category": "upstream"}) is False
    assert supervisor._is_deterministic_engine_error({"error_category": ""}) is False
    assert supervisor._is_deterministic_engine_error({"error": "VLM 调用失败"}) is False
    assert supervisor._is_deterministic_engine_error({"deterministic_error": True}) is True


# ---------------------------------------------------------------
# 3. 引擎瞬时故障：仍跑满上限（防过度收敛）
# ---------------------------------------------------------------
def test_transient_engine_failure_still_retries_to_max(tmp_path, monkeypatch):
    model = _CountingModel(exc=RuntimeError("connection reset"))
    state, _ = _run(tmp_path, monkeypatch, model)

    assert state["attempt"] == supervisor.MAX_RETRY, \
        f"未归类的瞬时故障应重试到 {supervisor.MAX_RETRY} 轮，实际 {state['attempt']}"
    assert model.calls == supervisor.MAX_RETRY
    assert not state.get("deterministic_error")
    assert not state.get("output_reject_fast")
    assert state["status"] == "error"


def test_upstream_category_also_retries(tmp_path, monkeypatch):
    """upstream（5xx/50507）是服务商侧瞬时故障，属设计内重试场景，不得被短路。"""
    model = _CountingModel(exc=_UpstreamError("HTTP 503 / code=50507"))
    state, _ = _run(tmp_path, monkeypatch, model)

    assert model.calls == supervisor.MAX_RETRY
    assert state["attempt"] == supervisor.MAX_RETRY


# ---------------------------------------------------------------
# 4. 门禁快速反馈（算术门禁）：一次调用，不触发额外 VLM
# ---------------------------------------------------------------
def test_fast_feedback_gate_single_vlm_call(tmp_path, monkeypatch):
    payload = json.dumps({
        "vendor": "某供应商",
        "date": "2026-01-01",
        "doc_form": "printed_delivery_note",
        "payment_marked": False,
        "confidence": 0.9,
        "items": [{"name": "菜心", "qty": 1, "unit": "斤",
                   "unit_price": 5.0, "amount": 5.0}],
        "total": 10.0,  # 明细合计 5.0 ≠ 总额 10.0 → 算术门禁
    })
    model = _CountingModel(output=payload)
    state, _ = _run(tmp_path, monkeypatch, model)

    assert model.calls == 1, f"快速反馈类失败不得触发额外 VLM，实际 invoke={model.calls}"
    assert state["attempt"] == 1
    assert "gate_reject_fast" in _log_actions(state)
    assert state["status"] == "parsed_with_warnings"
    assert state["data"] is not None, "带警告仍须保留识别结构供人工复核"


def test_fast_feedback_gate_classifier():
    assert supervisor._is_fast_feedback_gate("算术门禁: 明细合计 5.0 ≠ 总额 10.0") is True
    assert supervisor._is_fast_feedback_gate("契约校验失败: items: 缺失") is True
    assert supervisor._is_fast_feedback_gate("明细为空") is True
    assert supervisor._is_fast_feedback_gate("总额不能为0") is True
    assert supervisor._is_fast_feedback_gate("安全阻断: canary 缺失") is False
    assert supervisor._is_fast_feedback_gate("") is False


# ---------------------------------------------------------------
# 5. 上限可配置且被钳制
# ---------------------------------------------------------------
def test_max_retry_rounds_configurable(tmp_path, monkeypatch):
    model = _CountingModel(exc=RuntimeError("connection reset"))
    state, _ = _run(tmp_path, monkeypatch, model, max_retry_rounds=1)

    assert model.calls == 1
    assert state["attempt"] == 1


def test_resolve_max_retry_clamps():
    assert supervisor.resolve_max_retry(EngineConfig()) == supervisor.MAX_RETRY
    assert supervisor.resolve_max_retry(None) == supervisor.MAX_RETRY
    assert supervisor.resolve_max_retry(EngineConfig(max_retry_rounds=1)) == 1
    # 越界/非法值不在契约层拦，运行时钳制（与 call_timeout_seconds 的处理方式一致）
    cfg = EngineConfig()
    cfg.max_retry_rounds = 0
    assert supervisor.resolve_max_retry(cfg) == 1
    cfg.max_retry_rounds = 99
    assert supervisor.resolve_max_retry(cfg) == supervisor.MAX_RETRY_LIMIT
    cfg.max_retry_rounds = "abc"
    assert supervisor.resolve_max_retry(cfg) == supervisor.MAX_RETRY

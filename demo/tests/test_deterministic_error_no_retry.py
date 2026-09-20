# -*- coding: utf-8 -*-
"""N4 回归：确定性解码失败不得跑满重试阶梯。

why: 前序轮次已让图片解码失败（ImageDecodeError）在 extract_chain 内短路返回带
可读原因的 error dict、不再调用任何引擎。但 supervisor 的重试阶梯仍把它当成
"普通引擎失败"重试 MAX_RETRY 轮——实测 build 次数 3 / invoke 次数 0，
白跑两轮、打三行重复日志。本文件锁住修复后行为：
  - extract_chain 的确定性失败返回携带 deterministic_error=True；
  - supervisor 收到该标记即 break（不再重试），attempt 停在 1；
  - 最终仍走既有 "data is None" error 收口（status=error / success=False /
    错误文案含"图片解码失败"，可透传给前端）；
  - 真正的引擎调用失败不带该标记，必须照旧重试到 MAX_RETRY（本次一并反证，
    防止"少重试了"被误当成"修好了"）。

全程离线：假 .heic + 假引擎，零网络、零真实引擎调用、零 live 库写入。
"""

import json
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.chains import extract_chain, supervisor

_GARBAGE = b"this is definitely not a real image payload"


class _CountingModel:
    """统计 invoke 次数的假引擎（识别链路只用到 kind 与 invoke）。"""

    kind = "openai"

    def __init__(self):
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        return '{"vendor": "不应被调用"}'


class _BoomModel:
    """调用即抛错的假引擎（配合测试里关闭引擎兜底 → 走普通调用失败路径）。"""

    kind = "openai"

    def __init__(self):
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        raise RuntimeError("engine boom")


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每例独立临时库（本组用例不写库，仍强制隔离以防隐式写入 live demo 库）。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_deterministic_retry.db")
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
    """关闭 Canary 注入：本组用例的假引擎不会回带 token，避免被 canary 校验拦下
    而干扰对"重试阶梯/门禁路径"的断言（canary 本身另有专门用例覆盖）。"""
    monkeypatch.setattr(extract_chain, "generate_canary_token", lambda: "")


def _fake_heic(tmp_path):
    """PIL 打不开的假 .heic（非 Web 格式 → 确定性解码失败）。"""
    p = tmp_path / "undecodable.heic"
    with open(p, "wb") as f:
        f.write(_GARBAGE)
    return str(p)


def _valid_png(tmp_path):
    p = str(tmp_path / "ok.png")
    Image.new("RGB", (40, 20), (9, 9, 9)).save(p, format="PNG")
    return p


def _patch_build(monkeypatch, model, counter):
    def _fake_build(*args, **kwargs):
        counter["n"] += 1
        return model
    monkeypatch.setattr(supervisor, "build_recognition_model", _fake_build)
    # 关闭识别引擎的自动兜底：本文件的用例断言的是「首选引擎失败后的重试语义」，
    # 兜底一旦可用就会把失败吞掉（改用例测不到重试轮数）。
    # 历史注记：兜底原先是本机 CodeBuddy CLI，判据是模型 kind != "codebuddy"；
    # CLI 弃用后兜底改为 DashScope 官方通道，判据是 extract_chain._dashscope_fallback_available。
    monkeypatch.setattr(extract_chain, "_dashscope_fallback_available",
                        lambda *a, **kw: False)


def _log_actions(state):
    return [e.get("action") for e in (state.get("log") or []) if isinstance(e, dict)]


# ---------------------------------------------------------------
# 1. 解码失败：attempt 停在第 1 轮，零引擎调用
# ---------------------------------------------------------------
def test_decode_failure_breaks_retry_ladder(tmp_path, monkeypatch):
    model = _CountingModel()
    build_counter = {"n": 0}
    _patch_build(monkeypatch, model, build_counter)

    state = supervisor.run_pipeline(_fake_heic(tmp_path))

    # 不再跑满 3 轮
    assert state["attempt"] == 1, f"确定性失败应停在第 1 轮，实际 attempt={state['attempt']}"
    assert state["retry_count"] == 0
    # 零引擎调用：构建 1 次（不再白跑 2 次），invoke 0 次
    assert build_counter["n"] == 1, f"不应重复构建引擎，实际 build={build_counter['n']}"
    assert model.calls == 0, "解码失败不得调用任何引擎"
    # 最终状态与既有 error 路径一致
    assert state["success"] is False
    assert state["status"] == "error"
    assert state["data"] is None
    # 错误文案可读且提到解码失败（Job 层取 contract_error or last_error 透传前端）
    assert "图片解码失败" in state["last_error"]
    assert "已阻止向识别模型发送损坏数据" in state["last_error"]
    # 显式标记 + 不伪装成"重试耗尽"
    assert state.get("deterministic_error") is True
    actions = _log_actions(state)
    assert "deterministic_fail" in actions
    assert actions.count("extract_fail") == 1, f"不应重复记失败日志: {actions}"
    assert "error_exit" in actions


def test_decode_failure_result_marker_only_on_deterministic_path(tmp_path, monkeypatch):
    """标记只在确定性分支出现：引擎调用失败返回不得携带该键。"""
    from app.chains import extract_chain

    # 关闭引擎兜底，确保第二段走的确实是「首选引擎失败」返回分支
    monkeypatch.setattr(extract_chain, "_dashscope_fallback_available",
                        lambda *a, **kw: False)

    decode_ret = extract_chain.extract_receipt(
        _fake_heic(tmp_path), model=_CountingModel(), enable_canary=False)
    assert decode_ret.get("deterministic_error") is True

    engine_ret = extract_chain.extract_receipt(
        _valid_png(tmp_path), model=_BoomModel(), enable_canary=False)
    assert "VLM 调用失败" in engine_ret["error"]
    assert not engine_ret.get("deterministic_error"), "引擎失败不得带确定性标记"


# ---------------------------------------------------------------
# 2. 既有语义守卫：真正的引擎调用失败仍必须重试到 MAX_RETRY
# ---------------------------------------------------------------
def test_engine_failure_still_retries_to_max(tmp_path, monkeypatch):
    """引擎调用失败（无 deterministic_error）→ 重试语义不变，仍跑满 MAX_RETRY。"""
    model = _BoomModel()
    build_counter = {"n": 0}
    _patch_build(monkeypatch, model, build_counter)

    state = supervisor.run_pipeline(_valid_png(tmp_path))

    assert state["attempt"] == supervisor.MAX_RETRY, \
        f"引擎失败应重试到 {supervisor.MAX_RETRY} 轮，实际 attempt={state['attempt']}"
    assert "VLM 调用失败" in state["last_error"]
    assert state["status"] == "error"
    assert state["success"] is False
    assert not state.get("deterministic_error")
    # 每轮都真的调了引擎
    assert model.calls == supervisor.MAX_RETRY


# ---------------------------------------------------------------
# 3. 契约门禁失败（非确定性、非引擎异常）仍走既有 fast_gate 路径
# ---------------------------------------------------------------
def test_gate_reject_fast_still_keeps_fallback_data(tmp_path, monkeypatch):
    """守卫：本次改动不得影响门禁快速反馈路径（P14/P15 的 parsed_with_warnings）。

    载荷契约合法但算术不平（明细合计 5.0 ≠ 总额 10.0）→ 触发算术门禁快速反馈。
    """
    class _MathBreakingModel:
        kind = "openai"

        def invoke(self, prompt):
            return json.dumps({
                "vendor": "某供应商",
                "date": "2026-01-01",
                "doc_form": "printed_delivery_note",
                "payment_marked": False,
                "confidence": 0.9,
                "items": [{"name": "菜心", "qty": 1, "unit": "斤",
                           "unit_price": 5.0, "amount": 5.0}],
                "total": 10.0,
            })

    _patch_build(monkeypatch, _MathBreakingModel(), {"n": 0})

    state = supervisor.run_pipeline(_valid_png(tmp_path))

    actions = _log_actions(state)
    assert "gate_reject_fast" in actions, f"应走快速反馈路径: {actions}"
    assert state["status"] == "parsed_with_warnings"
    assert state["success"] is False
    assert not state.get("deterministic_error")
    # 快速反馈只跑 1 轮，不应退化成整图重识别
    assert state["attempt"] == 1

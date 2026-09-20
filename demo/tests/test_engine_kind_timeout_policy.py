# -*- coding: utf-8 -*-
"""T9 单元测试：P6 引擎通道误判告警 + P8 超时默认值与硬钳一致性。

why：
- P6 的风险是「模型名以 qwen 开头 + 引擎类型非 openai」时静默切到 QwenChatModel 原生
  SDK 通道（该通道忽略 EngineConfig 的 base_url / api_key / call_timeout_seconds，凭据只
  取环境变量 DASHSCOPE_API_KEY）。没有告警就只能等识别失败后才察觉，故用 caplog 锁住
  「命中时必须打出含通道与忽略项的人话告警」。
- P8 的风险是 EngineConfig.call_timeout_seconds 默认 90 与 llm._resolve_timeout 的硬钳
  min(v,60) 不一致：管理台填 90 实际只生效 60，属误导。用户已拍板「默认改 60、保留硬钳」，
  故同时断言默认值=60 与超90仍被钳到60（防止有人日后顺手把硬钳去掉）。
"""

import logging

import pytest

from app import llm
from app.models import EngineConfig


@pytest.fixture(autouse=True)
def _clear_qwen_warn_registry():
    """清空告警去重集合，避免用例执行顺序影响 caplog 断言。"""
    llm._QWEN_NATIVE_CHANNEL_WARNED.clear()
    yield
    llm._QWEN_NATIVE_CHANNEL_WARNED.clear()


def _llm_warnings(caplog):
    return [r for r in caplog.records if r.name == "llm"]


# -------------------------------------------------------------
# P6：qwen 前缀 + 非 openai 引擎类型必须显式告警
# -------------------------------------------------------------
def test_qwen_model_with_non_openai_kind_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="llm"):
        kind, resolved = llm._resolve_engine("qwen3.5-omni-flash", "codebuddy")

    assert (kind, resolved) == ("qwen", "qwen3.5-omni-flash")
    msgs = [r.getMessage() for r in _llm_warnings(caplog)]
    assert msgs, "qwen 前缀 + 非 openai 引擎类型未打任何告警"
    assert any(
        "qwen3.5-omni-flash" in m and "QwenChatModel" in m and "openai" in m
        for m in msgs
    ), f"告警未点明会走哪条通道：{msgs}"
    assert any(
        "base_url" in m and "call_timeout_seconds" in m and "DASHSCOPE_API_KEY" in m
        for m in msgs
    ), f"告警未点明哪些配置被忽略、凭据从哪里取：{msgs}"


def test_opencode_engine_kind_also_warns(caplog):
    """非 openai 的另一种取值同样命中（避免只覆盖一个枚举值）。"""
    with caplog.at_level(logging.WARNING, logger="llm"):
        llm._resolve_engine("qwen3.5-omni-flash", "opencode")
    assert _llm_warnings(caplog)


def test_qwen_model_with_openai_kind_no_warning(caplog):
    """引擎类型为 openai 时走 OpenAI 兼容通道，不应产生任何告警。"""
    cfg = EngineConfig()
    with caplog.at_level(logging.WARNING, logger="llm"):
        kind, resolved = llm._resolve_engine("qwen3.5-omni-flash", "openai", cfg=cfg)

    assert kind == "openai"
    assert resolved == cfg.openai_rec_model
    assert not _llm_warnings(caplog)


def test_empty_engine_kind_no_warning(caplog):
    """engine_kind 为空（cfg=None 的裸调用）时无 EngineConfig 配置被忽略，不告警。"""
    with caplog.at_level(logging.WARNING, logger="llm"):
        kind, _ = llm._resolve_engine("qwen3.5-omni-flash", "")
    assert kind == "qwen"
    assert not _llm_warnings(caplog)


def test_same_model_and_kind_warns_once(caplog):
    """同一 (模型, 引擎类型) 组合每进程只告警一次，避免每条单据刷屏。"""
    with caplog.at_level(logging.WARNING, logger="llm"):
        llm._resolve_engine("qwen3.5-omni-flash", "codebuddy")
        llm._resolve_engine("qwen3.5-omni-flash", "codebuddy")
    assert len(_llm_warnings(caplog)) == 1


# -------------------------------------------------------------
# P8：超时默认值 60，且硬钳 min(v,60) 保留
# -------------------------------------------------------------
def test_call_timeout_default_is_60():
    assert EngineConfig().call_timeout_seconds == 60


def test_resolve_timeout_default_takes_effect_as_60():
    assert llm._resolve_timeout(EngineConfig()) == 60


def test_resolve_timeout_clamps_above_60_and_keeps_smaller_value():
    # 硬钳保留：填 90（旧默认）与 240 实际都只生效 60
    assert llm._resolve_timeout(EngineConfig(call_timeout_seconds=90)) == 60
    assert llm._resolve_timeout(EngineConfig(call_timeout_seconds=240)) == 60
    # 小于硬钳的值不被抬高
    assert llm._resolve_timeout(EngineConfig(call_timeout_seconds=30)) == 30

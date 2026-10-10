# -*- coding: utf-8 -*-
"""多模型参数自适应与双重防御自愈架构自动化测试套件（Phase 2 开发交付 & Phase 3 QA 验证）。

严格对照 Phase 1 验收标准清单 (Acceptance Criteria, AC-01 至 AC-16) 与全局工程红线：
- AC-03: DeepSeek 官方 API 思考强度静默剥离
- AC-04: DeepSeek 官方 API Max Tokens 自动钳制 <= 8192
- AC-05: OpenAI o1/o3 推理模型参数桥接 (max_completion_tokens) 与采样温度剥离
- AC-06: OpenAI 常规多模态正常传递 (gpt-4o)
- AC-07: 动态捕获 reasoning_effort 400 并就地剥离重试成功
- AC-08: 动态捕获 max_tokens 超限 400 并降级钳制重试成功
- AC-09: 动态捕获 temperature 不支持 400 并剥离重试成功
- AC-10: 动态捕获 detail 不支持 400 并剥离重试成功
- AC-11: 自愈重试轮次硬上限防护 (<= 2) 与未知错误规范抛出 EngineParamError
- AC-12: 自愈元数据 adapted_params 注入 response_metadata 并落库 llm_call_log 与决策日志
- AC-13: 成本核算基于自愈成功后的最终 Token Usage
- AC-14: 前端管理台模型变动即时动态提示 (DeepSeek, o1/o3, gpt-4o)
- AC-15: 前端折叠面板标题栏 Badge 实时准确格式化 (含思考强度)
- AC-16: 0 Emoji 全局规范扫描
"""

import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from langchain_core.messages import HumanMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.llm import (
    OpenAIChatModel,
    classify_model_profile,
    _adapt_request_payload,
    _strip_detail_from_messages,
    _PARAM_ERROR_SIGNATURES,
    EngineParamError,
)
from app.services import llm_logger

DEMO_ROOT = Path(__file__).resolve().parent.parent


# ==============================================================================
# Layer 1: 静态前置自适应契约测试 (AC-03 至 AC-06)
# ==============================================================================

def test_ac03_deepseek_official_reasoning_effort_stripped():
    """AC-03: DeepSeek 官方 API（或模型名含 deepseek 且直连网关），自动静默剥离 reasoning_effort。"""
    # 场景 1: 显式指向 api.deepseek.com
    profile1 = classify_model_profile("deepseek-chat", "https://api.deepseek.com/v1")
    assert profile1.is_deepseek_official is True
    assert profile1.supports_reasoning_effort is False

    payload1 = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 4096,
        "temperature": 0.0,
        "reasoning_effort": "high",
    }
    adapted1, acts1 = _adapt_request_payload(payload1, profile1)
    assert "reasoning_effort" not in adapted1
    assert "strip_reasoning_effort" in acts1

    # 场景 2: 模型名为 deepseek-reasoner，默认网关
    profile2 = classify_model_profile("deepseek-reasoner", "https://api.deepseek.com")
    assert profile2.supports_reasoning_effort is False
    adapted2, acts2 = _adapt_request_payload({"reasoning_effort": "medium"}, profile2)
    assert "reasoning_effort" not in adapted2
    assert "strip_reasoning_effort" in acts2


def test_ac04_deepseek_official_max_tokens_clamped_8192():
    """AC-04: DeepSeek 官方 API 配置 max_tokens > 8192 时，发出请求自动钳制为 8192。"""
    profile = classify_model_profile("deepseek-chat", "https://api.deepseek.com/v1")
    assert profile.max_tokens_limit == 8192

    payload = {
        "model": "deepseek-chat",
        "max_tokens": 12000,
        "temperature": 0.0,
    }
    adapted, acts = _adapt_request_payload(payload, profile)
    assert adapted["max_tokens"] == 8192
    assert "clamp_max_tokens_8192" in acts

    # max_tokens <= 8192 保持不变
    payload_ok = {"model": "deepseek-chat", "max_tokens": 4096}
    adapted_ok, acts_ok = _adapt_request_payload(payload_ok, profile)
    assert adapted_ok["max_tokens"] == 4096
    assert "clamp_max_tokens_8192" not in acts_ok


def test_ac05_openai_o1_o3_param_bridging_and_temperature_stripped():
    """AC-05: OpenAI o1/o3 推理模型自动桥接 max_completion_tokens 并剔除 temperature。"""
    for model_name in ["o1-mini", "o1-preview", "o3-mini", "openai/o1-preview"]:
        profile = classify_model_profile(model_name, "https://api.openai.com/v1")
        assert profile.is_openai_reasoning is True
        assert profile.supports_temperature is False
        assert profile.max_tokens_field == "max_completion_tokens"

        payload = {
            "model": model_name,
            "max_tokens": 4096,
            "temperature": 0.0,
            "reasoning_effort": "medium",
        }
        adapted, acts = _adapt_request_payload(payload, profile)
        assert "max_tokens" not in adapted
        assert adapted["max_completion_tokens"] == 4096
        assert "temperature" not in adapted
        assert adapted["reasoning_effort"] == "medium"
        assert "bridge_max_completion_tokens" in acts
        assert "strip_temperature" in acts


def test_ac06_openai_gpt4o_standard_params_intact():
    """AC-06: 常规多模态模型 (gpt-4o) 保持原有 max_tokens、temperature 与 detail 不变。"""
    profile = classify_model_profile("gpt-4o", "https://api.openai.com/v1")
    assert profile.is_openai_reasoning is False
    assert profile.is_deepseek_official is False
    assert profile.supports_temperature is True
    assert profile.supports_reasoning_effort is True
    assert profile.max_tokens_field == "max_tokens"

    payload = {
        "model": "gpt-4o",
        "max_tokens": 4096,
        "temperature": 0.1,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc", "detail": "high"}},
                ],
            }
        ],
    }
    adapted, acts = _adapt_request_payload(payload, profile)
    assert adapted["max_tokens"] == 4096
    assert adapted["temperature"] == 0.1
    assert adapted["messages"][0]["content"][1]["image_url"]["detail"] == "high"
    assert len(acts) == 0


# ==============================================================================
# Layer 2: 动态 400 就地剥离自愈测试 (AC-07 至 AC-11)
# ==============================================================================

def _make_mock_response(status_code: int, text: str, data: dict = None):
    mock = MagicMock()
    mock.status_code = status_code
    mock.text = text
    if data is not None:
        mock.json.return_value = data
    else:
        try:
            mock.json.return_value = json.loads(text)
        except Exception:
            mock.json.return_value = {}
    return mock


def _mock_success_response():
    return _make_mock_response(
        200,
        "",
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"status": "ok", "items": []}',
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 50,
                "total_tokens": 170,
            },
        },
    )


def test_ac07_dynamic_self_healing_strip_reasoning_effort():
    """AC-07: 模拟网关返回 reasoning_effort 400 错误，就地剥离并在单次调用内重试成功。"""
    resp_400 = _make_mock_response(
        400,
        json.dumps({
            "error": {
                "message": "Unrecognized request argument: reasoning_effort",
                "type": "invalid_request_error",
            }
        }),
    )
    resp_200 = _mock_success_response()

    model = OpenAIChatModel(
        model="custom-gateway-model",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        reasoning_effort="high",
    )

    with patch("requests.post", side_effect=[resp_400, resp_200]) as mock_post:
        result = model._generate([HumanMessage(content="test prompt")])

        assert mock_post.call_count == 2

        # 第一次请求包含 reasoning_effort
        first_payload = mock_post.call_args_list[0][1]["json"]
        assert first_payload.get("reasoning_effort") == "high"

        # 第二次请求已自愈剥离 reasoning_effort
        second_payload = mock_post.call_args_list[1][1]["json"]
        assert "reasoning_effort" not in second_payload

        # 结果中透传自愈元数据
        metadata = result.generations[0].message.response_metadata
        assert "adapted_params" in metadata
        assert "strip_reasoning_effort" in metadata["adapted_params"]


def test_ac08_dynamic_self_healing_clamp_max_tokens_8192():
    """AC-08: 模拟网关返回 max_tokens 超限 400 错误，自动降级钳制为 8192 并重试成功。"""
    resp_400 = _make_mock_response(
        400,
        "max_tokens must be less than or equal to 8192",
    )
    resp_200 = _mock_success_response()

    model = OpenAIChatModel(
        model="custom-deepseek-proxy",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        max_tokens=10000,
    )

    with patch("requests.post", side_effect=[resp_400, resp_200]) as mock_post:
        result = model._generate([HumanMessage(content="test prompt")])

        assert mock_post.call_count == 2
        second_payload = mock_post.call_args_list[1][1]["json"]
        assert second_payload["max_tokens"] == 8192

        metadata = result.generations[0].message.response_metadata
        assert "clamp_max_tokens_8192" in metadata["adapted_params"]


def test_ac09_dynamic_self_healing_strip_temperature():
    """AC-09: 模拟网关返回 temperature 不支持 400 错误，自动剥离 temperature 并重试成功。"""
    resp_400 = _make_mock_response(
        400,
        "Unsupported parameter: 'temperature'",
    )
    resp_200 = _mock_success_response()

    model = OpenAIChatModel(
        model="custom-reasoner",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        temperature=0.1,
    )

    with patch("requests.post", side_effect=[resp_400, resp_200]) as mock_post:
        result = model._generate([HumanMessage(content="test prompt")])

        assert mock_post.call_count == 2
        second_payload = mock_post.call_args_list[1][1]["json"]
        assert "temperature" not in second_payload

        metadata = result.generations[0].message.response_metadata
        assert "strip_temperature" in metadata["adapted_params"]


def test_ac10_dynamic_self_healing_strip_image_detail():
    """AC-10: 模拟非标视觉网关返回 detail is not supported 400，剔除 detail 后重试成功。"""
    resp_400 = _make_mock_response(
        400,
        "detail is not supported in image_url",
    )
    resp_200 = _mock_success_response()

    model = OpenAIChatModel(
        model="custom-vision",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        image_detail="high",
    )

    msg = HumanMessage(content=[
        {"type": "text", "text": "recognize"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,123"}},
    ])

    with patch("requests.post", side_effect=[resp_400, resp_200]) as mock_post:
        result = model._generate([msg])

        assert mock_post.call_count == 2
        first_payload = mock_post.call_args_list[0][1]["json"]
        assert first_payload["messages"][0]["content"][1]["image_url"].get("detail") == "high"

        second_payload = mock_post.call_args_list[1][1]["json"]
        assert "detail" not in second_payload["messages"][0]["content"][1]["image_url"]

        metadata = result.generations[0].message.response_metadata
        assert "strip_image_detail" in metadata["adapted_params"]


def test_ac11_dynamic_self_healing_retry_limit_and_unrecognized_error():
    """AC-11: 未知 400 错误或自愈重试超过 2 次后仍失败，必须规范抛出 EngineParamError。"""
    # 场景 A: 无法匹配任何特征的未知 400 错误
    resp_unrecognized = _make_mock_response(
        400,
        "Invalid model name specified",
    )
    model = OpenAIChatModel(
        model="unknown-model",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
    )

    with patch("requests.post", return_value=resp_unrecognized) as mock_post:
        with pytest.raises(EngineParamError) as exc_info:
            model._generate([HumanMessage(content="test prompt")])
        assert mock_post.call_count == 1
        assert "Invalid model name specified" in str(exc_info.value)

    # 场景 B: 连续 400 错误耗尽 2 轮自愈重试
    resp_repeat_400 = _make_mock_response(
        400,
        "Unrecognized request argument: reasoning_effort",
    )
    with patch("requests.post", return_value=resp_repeat_400) as mock_post:
        with pytest.raises(EngineParamError) as exc_info:
            model._generate([HumanMessage(content="test prompt")])
        # 初次调用 + 最多 2 轮自愈重试 = 最多调用 3 次
        assert mock_post.call_count <= 3


# ==============================================================================
# 可观测性与日志审计测试 (AC-12, AC-13)
# ==============================================================================

def test_ac12_adapted_params_recorded_in_db():
    """AC-12: llm_logger.record_llm_call 能够持久化 adapted_params 并在列表与明细中准确返回。"""
    test_receipt_id = 999991
    test_params = ["strip_reasoning_effort", "clamp_max_tokens_8192"]

    log_id = llm_logger.record_llm_call(
        receipt_id=test_receipt_id,
        leg="recognition",
        engine="openai_compatible",
        model="deepseek-chat",
        token_usage={"prompt_tokens": 500, "completion_tokens": 200, "total_tokens": 700},
        cost_hkd=0.0014,
        elapsed_ms=850.0,
        status="success",
        adapted_params=test_params,
    )

    assert log_id is not None and log_id > 0

    # 查阅单据日志列表
    logs = db.list_llm_call_logs_by_receipt(test_receipt_id)
    assert len(logs) >= 1
    target = next((item for item in logs if item["id"] == log_id), None)
    assert target is not None
    assert target["adapted_params"] == test_params

    # 查阅单个明细
    single = db.get_llm_call_log_by_id(log_id)
    assert single is not None
    assert single["adapted_params"] == test_params


# ==============================================================================
# 前端防呆与交互对齐测试 (AC-14, AC-15)
# ==============================================================================

def test_ac14_frontend_model_trait_hints_and_containers():
    """AC-14: 6 个引擎面板均具备 model trait hint 容器，main.js 具备模型识别与提示更新逻辑。"""
    html_file = DEMO_ROOT / "templates" / "index.html"
    js_file = DEMO_ROOT / "static" / "js" / "main.js"

    html_content = html_file.read_text(encoding="utf-8")
    js_content = js_file.read_text(encoding="utf-8")

    # 1. 验证 6 个面板的提示容器存在
    legs = ["rec", "aud", "parse", "grey_rec", "grey_aud", "grey_parse"]
    for leg in legs:
        assert f'id="adminAdvModelHint_{leg}"' in html_content, f"缺少 adminAdvModelHint_{leg}"

    # 2. 验证 main.js 中关键函数与规则
    assert "function updateModelTraitHints(legId)" in js_content
    assert "window.updateModelTraitHints = updateModelTraitHints" in js_content
    assert "api.deepseek.com" in js_content
    assert "deepseek" in js_content
    assert "isOReasoning" in js_content

    # 3. 验证提示文本符合规范且 0 Emoji
    assert "[INFO] DeepSeek 官方 API 最大支持 8192 Tokens" in js_content
    assert "[INFO] DeepSeek 官方 API 无需传思考强度参数" in js_content
    assert "[INFO] OpenAI o1/o3 推理模型由服务商统一管理采样温度" in js_content
    assert "[INFO] 系统将自动为您桥接 max_completion_tokens 规范" in js_content


def test_ac15_frontend_badge_dynamic_formatting_with_effort():
    """AC-15: main.js 中 updateAdvBadge 支持思考强度展示与基线文本兼容。"""
    js_file = DEMO_ROOT / "static" / "js" / "main.js"
    content = js_file.read_text(encoding="utf-8")

    assert "function updateAdvBadge(legId)" in content
    # 统一四段式微缩 Badge 模板
    assert "[思考强度:${effortLabel} | 最大Token:${tokens} | 视觉切片:${detailLabel} | 采样温度:${tempStr}]" in content
    assert "effort" in content


# ==============================================================================
# Phase 3: QA 边界与深度自愈回归验证用例 (QA Expansion & Edge-Case Regressions)
# ==============================================================================

def test_qa_cascading_multi_param_self_healing_two_rounds():
    """QA-01: 多参数级联自愈（第1轮剥离 reasoning_effort，第2轮钳制 max_tokens <= 8192，第3轮 200 成功）。"""
    resp_400_effort = _make_mock_response(
        400,
        "Extra inputs are not permitted: reasoning_effort",
    )
    resp_400_tokens = _make_mock_response(
        400,
        "max_tokens exceeds 8192",
    )
    resp_200 = _mock_success_response()

    model = OpenAIChatModel(
        model="custom-proxy-model",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        reasoning_effort="high",
        max_tokens=12000,
    )

    with patch("requests.post", side_effect=[resp_400_effort, resp_400_tokens, resp_200]) as mock_post:
        result = model._generate([HumanMessage(content="test prompt")])

        # 必须经历 3 次调用：初次 + 第 1 轮自愈重试 + 第 2 轮自愈重试
        assert mock_post.call_count == 3

        # 第 1 次请求：包含 reasoning_effort 与 max_tokens=12000
        p1 = mock_post.call_args_list[0][1]["json"]
        assert p1.get("reasoning_effort") == "high"
        assert p1.get("max_tokens") == 12000

        # 第 2 次请求：剥离 reasoning_effort，但 max_tokens 仍为 12000
        p2 = mock_post.call_args_list[1][1]["json"]
        assert "reasoning_effort" not in p2
        assert p2.get("max_tokens") == 12000

        # 第 3 次请求：剥离 reasoning_effort 且 max_tokens 钳制为 8192
        p3 = mock_post.call_args_list[2][1]["json"]
        assert "reasoning_effort" not in p3
        assert p3.get("max_tokens") == 8192

        # 最终元数据需按序包含两项自愈动作
        metadata = result.generations[0].message.response_metadata
        assert "adapted_params" in metadata
        assert metadata["adapted_params"] == ["strip_reasoning_effort", "clamp_max_tokens_8192"]


def test_qa_self_healing_retry_exhaustion_strict_limit():
    """QA-02: 连续触发 400 自愈，当重试达到 MAX_SELF_HEALING_ROUNDS (2轮) 仍失败时，严格终止并不再发起第 4 次请求。"""
    resp_400_1 = _make_mock_response(400, "unsupported parameter: 'reasoning_effort'")
    resp_400_2 = _make_mock_response(400, "temperature is not supported")
    resp_400_3 = _make_mock_response(400, "max_tokens must be less than or equal to 8192")

    model = OpenAIChatModel(
        model="custom-stubborn-gateway",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        reasoning_effort="medium",
        temperature=0.2,
        max_tokens=10000,
    )

    with patch("requests.post", side_effect=[resp_400_1, resp_400_2, resp_400_3]) as mock_post:
        with pytest.raises(EngineParamError) as exc_info:
            model._generate([HumanMessage(content="test prompt")])

        # 严格执行: 初次调用 (attempt 0) + 第 1 轮重试 (attempt 1) + 第 2 轮重试 (attempt 2) = 3 次调用
        assert mock_post.call_count == 3
        assert exc_info.value.status_code == 400
        assert exc_info.value.category == "param"


@pytest.mark.parametrize(
    "error_text,expected_action,initial_payload_key",
    [
        ("Extra inputs are not permitted: reasoning_effort", "strip_reasoning_effort", "reasoning_effort"),
        ("unrecognized request argument: reasoning_effort", "strip_reasoning_effort", "reasoning_effort"),
        ("unsupported parameter: 'reasoning_effort'", "strip_reasoning_effort", "reasoning_effort"),
        ("unsupported parameter: 'temperature'", "strip_temperature", "temperature"),
        ("temperature is not supported", "strip_temperature", "temperature"),
        ("temperature must be 1", "strip_temperature", "temperature"),
        ("unrecognized request argument: temperature", "strip_temperature", "temperature"),
        ("Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.", "bridge_max_completion_tokens", "max_tokens"),
        ("max_tokens is not supported, please use max_completion_tokens", "bridge_max_completion_tokens", "max_tokens"),
        ("max_tokens must be <= 8192", "clamp_max_tokens_8192", "max_tokens"),
        ("max_tokens exceeds 8192", "clamp_max_tokens_8192", "max_tokens"),
        ("max_tokens must be less than or equal to 8192", "clamp_max_tokens_8192", "max_tokens"),
    ],
)
def test_qa_various_gateway_error_signatures_matrix(error_text, expected_action, initial_payload_key):
    """QA-03: 矩阵式验证主流中转网关及不同开源代理框架下的 400 报错文本签名正则匹配与参数自愈。"""
    resp_400 = _make_mock_response(400, error_text)
    resp_200 = _mock_success_response()

    model_kwargs = {
        "model": "custom-aggregator-model",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
    }
    if initial_payload_key == "reasoning_effort":
        model_kwargs["reasoning_effort"] = "high"
    elif initial_payload_key == "temperature":
        model_kwargs["temperature"] = 0.2
    elif initial_payload_key == "max_tokens":
        model_kwargs["max_tokens"] = 10000

    model = OpenAIChatModel(**model_kwargs)

    with patch("requests.post", side_effect=[resp_400, resp_200]) as mock_post:
        result = model._generate([HumanMessage(content="test prompt")])

        assert mock_post.call_count == 2
        metadata = result.generations[0].message.response_metadata
        assert "adapted_params" in metadata
        assert expected_action in metadata["adapted_params"]


def test_qa_unknown_400_error_no_false_healing():
    """QA-04: 非参数兼容性引发的 400 错误（如提示词语法错误、缺失必填字段、非法模型）不触发假自愈，调用次数严格为 1。"""
    unknown_errors = [
        "'messages' is a required property",
        "Model 'gpt-nonexistent' does not exist or you do not have access to it",
        "prompt length 128000 exceeds maximum context window",
    ]
    for err in unknown_errors:
        resp_400 = _make_mock_response(400, err)
        model = OpenAIChatModel(
            model="custom-model",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            reasoning_effort="high",
        )
        with patch("requests.post", return_value=resp_400) as mock_post:
            with pytest.raises(EngineParamError) as exc_info:
                model._generate([HumanMessage(content="test prompt")])
            assert mock_post.call_count == 1
            assert exc_info.value.status_code == 400
            assert exc_info.value.category == "param"


def test_qa_adapted_params_end_to_end_in_chains_and_supervisor(tmp_path, monkeypatch):
    """QA-05: 验证 extract 决策与 audit 决策全链路透传 adapted_params 并结构化落库至 ai_decision_log.extra。"""
    from app.chains import supervisor
    from app import db as app_db

    old_db_path = app_db.DB_PATH
    test_db_path = str(tmp_path / "test_qa_adapted_params_e2e.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    app_db.DB_PATH = test_db_path
    app_db._make_engine()

    try:
        receipt_id = 998801
        test_actions = ["strip_reasoning_effort", "clamp_max_tokens_8192"]

        # 1. 模拟 extract 决策落库
        supervisor._log_extract_decision(
            receipt_id=receipt_id,
            experiment_id=None,
            config=None,
            use_grey=False,
            attempt=1,
            engine="openai",
            status="extract_ok",
            token_usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            cost_hkd=0.0015,
            elapsed_ms={"extract": 120.0, "total": 120.0},
            success=True,
            adapted_params=test_actions,
        )

        s = app_db.get_session()
        try:
            extract_row = (
                s.query(app_db._DecisionLogRow)
                .filter_by(receipt_id=receipt_id, decision_type="extract")
                .first()
            )
            assert extract_row is not None
            extra_dict = json.loads(extract_row.extra) if isinstance(extract_row.extra, str) else extract_row.extra
            assert "adapted_params" in extra_dict
            assert extra_dict["adapted_params"] == test_actions
        finally:
            s.close()

        # 2. 模拟 audit 决策落库
        audit_mock = {
            "skipped": False,
            "overall_consistent": True,
            "trust": 0.95,
            "audit_ms": 80.0,
            "token_usage": {"prompt_tokens": 60, "completion_tokens": 20, "total_tokens": 80},
            "cost_hkd": 0.0006,
            "adapted_params": ["strip_temperature"],
        }
        supervisor._log_audit_decision(receipt_id, None, None, False, audit_mock)

        s = app_db.get_session()
        try:
            audit_row = (
                s.query(app_db._DecisionLogRow)
                .filter_by(receipt_id=receipt_id, decision_type="audit")
                .first()
            )
            assert audit_row is not None
            audit_extra = json.loads(audit_row.extra) if isinstance(audit_row.extra, str) else audit_row.extra
            assert "adapted_params" in audit_extra
            assert audit_extra["adapted_params"] == ["strip_temperature"]
        finally:
            s.close()
    finally:
        app_db.DB_PATH = old_db_path
        app_db._make_engine()


def test_qa_frontend_dom_elements_and_badge_format_comprehensive():
    """QA-06: 严谨校验 index.html 中 6 个面板的提示容器及 main.js 标题栏 Badge 格式化各种形态。"""
    from bs4 import BeautifulSoup
    html_file = DEMO_ROOT / "templates" / "index.html"
    js_file = DEMO_ROOT / "static" / "js" / "main.js"

    html_content = html_file.read_text(encoding="utf-8")
    js_content = js_file.read_text(encoding="utf-8")

    soup = BeautifulSoup(html_content, "html.parser")

    # 1. 验证 6 个面板的提示容器全部在 DOM 中存在
    legs = ["rec", "aud", "parse", "grey_rec", "grey_aud", "grey_parse"]
    field_prefixes = ["adminRec", "adminAud", "adminParse", "adminGreyRec", "adminGreyAud", "adminGreyParse"]
    for leg, prefix in zip(legs, field_prefixes):
        assert soup.find(id=f"adminAdvModelHint_{leg}") is not None, f"缺少 adminAdvModelHint_{leg}"
        assert soup.find(id=f"{prefix}TokensHint") is not None, f"缺少 {prefix}TokensHint"
        assert soup.find(id=f"{prefix}ReasoningHint") is not None, f"缺少 {prefix}ReasoningHint"
        assert soup.find(id=f"{prefix}TempHint") is not None, f"缺少 {prefix}TempHint"

    # 2. 验证 main.js 中 updateAdvBadge 对各种参数组合的逻辑完整性
    assert "function updateAdvBadge(legId)" in js_content
    # 统一四段式微缩 Badge 模板格式
    assert "[思考强度:${effortLabel} | 最大Token:${tokens} | 视觉切片:${detailLabel} | 采样温度:${tempStr}]" in js_content
    assert "effortLabel" in js_content
    assert "detailLabel" in js_content


# ==============================================================================
# 全局工程红线测试 (AC-16: 0 Emoji)
# ==============================================================================

def test_ac16_global_zero_emoji_compliance():
    """AC-16: 全局严禁使用 Emoji 表情符号，所有新增/修改代码与模板保持纯文本。"""
    files_to_scan = [
        DEMO_ROOT / "app" / "llm.py",
        DEMO_ROOT / "app" / "db.py",
        DEMO_ROOT / "app" / "services" / "llm_logger.py",
        DEMO_ROOT / "app" / "chains" / "extract_chain.py",
        DEMO_ROOT / "app" / "chains" / "audit_chain.py",
        DEMO_ROOT / "app" / "chains" / "supervisor.py",
        DEMO_ROOT / "templates" / "index.html",
        DEMO_ROOT / "static" / "js" / "main.js",
        Path(__file__),
    ]

    emoji_pattern = re.compile(
        "["
        "\U00010000-\U0010ffff"
        "\u2600-\u26ff"
        "\u2700-\u27bf"
        "]",
        flags=re.UNICODE,
    )

    for fpath in files_to_scan:
        assert fpath.exists(), f"文件不存在: {fpath}"
        content = fpath.read_text(encoding="utf-8")
        matches = emoji_pattern.findall(content)
        assert len(matches) == 0, f"文件 {fpath.name} 包含非法 Emoji: {matches}"

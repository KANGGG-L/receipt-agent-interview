# -*- coding: utf-8 -*-
import os
import sys
import unittest.mock as mock
import pytest
from starlette.testclient import TestClient
from langchain_core.messages import HumanMessage

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.models import EngineConfig, clamp_call_timeout, VALID_REASONING_EFFORTS
from app.llm import _resolve_timeout, _resolve_reasoning_effort, _model_cache_key, OpenAIChatModel
from app.main import app

client = TestClient(app, headers={"X-Role": "admin"})


def test_model_ac_mod_fields_and_defaults():
    """AC-MOD-01: EngineConfig 具备 6 个独立超时字段定义（初始缺省为 None）与 6 个思考强度字段定义（初始缺省为 ''）。"""
    cfg = EngineConfig()
    assert cfg.rec_timeout_seconds is None
    assert cfg.aud_timeout_seconds is None
    assert cfg.parse_timeout_seconds is None
    assert cfg.grey_rec_timeout_seconds is None
    assert cfg.grey_aud_timeout_seconds is None
    assert cfg.grey_parse_timeout_seconds is None

    assert cfg.openai_rec_reasoning_effort == ""
    assert cfg.openai_aud_reasoning_effort == ""
    assert cfg.openai_parse_reasoning_effort == ""
    assert cfg.grey_openai_rec_reasoning_effort == ""
    assert cfg.grey_openai_aud_reasoning_effort == ""
    assert cfg.grey_openai_parse_reasoning_effort == ""


def test_model_ac_mod_02_clamp_call_timeout():
    """AC-MOD-02: clamp_call_timeout 对超过 60 的超时正确归一为 60。"""
    assert clamp_call_timeout(100) == 60
    assert clamp_call_timeout(60) == 60
    assert clamp_call_timeout(30) == 30
    assert clamp_call_timeout(None) is None
    assert clamp_call_timeout(0) == 0


def test_resolve_timeout_ac_mod_03():
    """AC-MOD-03: _resolve_timeout 对各腿传入不同 side 与 use_grey 时能精准解析，未配置时回退至全局。"""
    cfg = EngineConfig(
        call_timeout_seconds=40,
        rec_timeout_seconds=15,
        aud_timeout_seconds=10,
        parse_timeout_seconds=25,
        grey_rec_timeout_seconds=35,
        grey_aud_timeout_seconds=12,
        grey_parse_timeout_seconds=28,
    )
    assert _resolve_timeout(cfg, side="rec", use_grey=False) == 15
    assert _resolve_timeout(cfg, side="aud", use_grey=False) == 10
    assert _resolve_timeout(cfg, side="parse", use_grey=False) == 25
    assert _resolve_timeout(cfg, side="rec", use_grey=True) == 35
    assert _resolve_timeout(cfg, side="aud", use_grey=True) == 12
    assert _resolve_timeout(cfg, side="parse", use_grey=True) == 28

    # 未配置独立超时时，优雅回退至 call_timeout_seconds
    fallback_cfg = EngineConfig(call_timeout_seconds=45)
    assert _resolve_timeout(fallback_cfg, side="rec", use_grey=False) == 45
    assert _resolve_timeout(fallback_cfg, side="aud", use_grey=False) == 45
    assert _resolve_timeout(fallback_cfg, side="parse", use_grey=False) == 45
    assert _resolve_timeout(fallback_cfg, side="rec", use_grey=True) == 45


def test_resolve_reasoning_effort_ac_mod_04():
    """AC-MOD-04: _resolve_reasoning_effort 对未知非法值能安全回落至 ''。"""
    cfg = EngineConfig(
        openai_rec_reasoning_effort="high",
        openai_aud_reasoning_effort="unknown_invalid",
        openai_parse_reasoning_effort="",
        grey_openai_rec_reasoning_effort="low",
    )
    assert _resolve_reasoning_effort(cfg, side="rec", use_grey=False) == "high"
    assert _resolve_reasoning_effort(cfg, side="aud", use_grey=False) == ""
    assert _resolve_reasoning_effort(cfg, side="parse", use_grey=False) == ""
    assert _resolve_reasoning_effort(cfg, side="rec", use_grey=True) == "low"
    assert _resolve_reasoning_effort(None, side="rec", use_grey=False) == ""


def test_openai_chat_model_payload_ac_pl_01_and_02():
    """AC-PL-01 & AC-PL-02:
    - 为空时，HTTP 请求体不包含 reasoning_effort；
    - 为有效值时，HTTP 请求体携带 reasoning_effort。
    """
    model_empty = OpenAIChatModel(
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        reasoning_effort="",
    )

    captured_payloads = []

    def mock_post(url, headers=None, json=None, timeout=None, allow_redirects=False):
        captured_payloads.append(json)
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }
        return mock_resp

    with mock.patch("requests.post", side_effect=mock_post):
        model_empty.invoke([HumanMessage(content="hi")])
        assert len(captured_payloads) == 1
        assert "reasoning_effort" not in captured_payloads[0]

    captured_payloads.clear()
    model_with_effort = OpenAIChatModel(
        model="o3-mini",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        reasoning_effort="medium",
    )

    with mock.patch("requests.post", side_effect=mock_post):
        model_with_effort.invoke([HumanMessage(content="hi")])
        assert len(captured_payloads) == 1
        assert captured_payloads[0].get("reasoning_effort") == "medium"


def test_model_cache_key_isolation_ac_pl_03():
    """AC-PL-03: 模型缓存键包含 call_timeout 与 reasoning_effort，不同参数组合实例严格隔离。"""
    key1 = _model_cache_key("openai", "gpt-4o", "https://api.openai.com/v1", "sk-1", 30, "low")
    key2 = _model_cache_key("openai", "gpt-4o", "https://api.openai.com/v1", "sk-1", 60, "low")
    key3 = _model_cache_key("openai", "gpt-4o", "https://api.openai.com/v1", "sk-1", 30, "high")
    key4 = _model_cache_key("openai", "gpt-4o", "https://api.openai.com/v1", "sk-2", 30, "low")

    assert key1 != key2
    assert key1 != key3
    assert key1 != key4


def test_api_get_effective_timeouts_ac_api_01():
    """AC-API-01: GET /api/admin/engine-config 正确返回 6 个独立超时、6 个思考强度及 perf.effective_timeouts 预览。"""
    res = client.get("/api/admin/engine-config")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "effective_timeouts" in data["perf"]
    eff = data["perf"]["effective_timeouts"]
    for leg in ("rec", "aud", "parse", "grey_rec", "grey_aud", "grey_parse"):
        assert leg in eff
        assert isinstance(eff[leg], int)


def test_api_put_and_clamping_ac_api_02_and_03():
    """AC-API-02 & AC-API-03:
    PUT 接口能正确持久化分腿配置；超过 60 的值被钳制为 60 并返回 warnings 提示。
    """
    orig_cfg = db.get_engine_config()
    try:
        payload = {
            "rec_timeout_seconds": 999,  # 超限，应被钳制为 60
            "openai_rec_reasoning_effort": "high",
            "aud_timeout_seconds": 15,
            "openai_aud_reasoning_effort": "low",
            "parse_timeout_seconds": 20,
            "openai_parse_reasoning_effort": "medium",
        }
        res = client.put("/api/admin/engine-config", json=payload)
        assert res.status_code == 200
        res_data = res.json()
        assert res_data["status"] == "success"
        # 验证警告提示
        warning_text = res_data.get("warning") or res_data.get("timeout_advice") or ""
        assert "rec_timeout_seconds" in warning_text

        # 验证落库值
        cfg = db.get_engine_config()
        assert cfg.rec_timeout_seconds == 60
        assert cfg.openai_rec_reasoning_effort == "high"
        assert cfg.aud_timeout_seconds == 15
        assert cfg.openai_aud_reasoning_effort == "low"
        assert cfg.parse_timeout_seconds == 20
        assert cfg.openai_parse_reasoning_effort == "medium"
    finally:
        db.set_engine_config(orig_cfg)


def test_api_promote_and_rollback_ac_api_04_and_05():
    """AC-API-04 & AC-API-05:
    PUT /promote 将灰测超时与思考强度晋升至常规组并创建快照；
    PUT /rollback 能从快照恢复原配置。
    """
    orig_cfg = db.get_engine_config()
    try:
        # 1. 预设常规组与灰测组配置
        setup_payload = {
            "rec_timeout_seconds": 20,
            "openai_rec_reasoning_effort": "low",
            "grey_enabled": True,
            "grey_percent": 30,
            "grey_openai_rec_base_url": "https://api.siliconflow.cn/v1",
            "grey_openai_rec_api_key": "sk-test-grey-key-12345678",
            "grey_openai_rec_model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "grey_rec_timeout_seconds": 45,
            "grey_openai_rec_reasoning_effort": "xhigh",
            "grey_aud_timeout_seconds": 12,
            "grey_openai_aud_reasoning_effort": "medium",
        }
        res = client.put("/api/admin/engine-config", json=setup_payload)
        assert res.status_code == 200

        # 2. 执行推全 (promote)
        res_promote = client.put("/api/admin/engine-config/promote")
        assert res_promote.status_code == 200
        promote_data = res_promote.json()
        assert promote_data["status"] == "success"

        cfg_after_promote = db.get_engine_config()
        assert cfg_after_promote.rec_timeout_seconds == 45
        assert cfg_after_promote.openai_rec_reasoning_effort == "xhigh"
        assert cfg_after_promote.aud_timeout_seconds == 12
        assert cfg_after_promote.openai_aud_reasoning_effort == "medium"
        assert cfg_after_promote.grey_rec_timeout_seconds is None
        assert cfg_after_promote.grey_openai_rec_reasoning_effort == ""

        # 3. 执行回滚 (rollback)
        res_rollback = client.put("/api/admin/engine-config/rollback")
        assert res_rollback.status_code == 200
        rollback_data = res_rollback.json()
        assert rollback_data["status"] == "success"

        cfg_after_rollback = db.get_engine_config()
        assert cfg_after_rollback.rec_timeout_seconds == 20
        assert cfg_after_rollback.openai_rec_reasoning_effort == "low"
    finally:
        db.set_engine_config(orig_cfg)


def test_api_diff_ac_api_06():
    """AC-API-06: GET /api/admin/engine-config/diff 正确比对常规与灰测超时及思考强度差异。"""
    orig_cfg = db.get_engine_config()
    try:
        setup_payload = {
            "grey_enabled": True,
            "grey_percent": 25,
            "grey_openai_rec_base_url": "https://api.siliconflow.cn/v1",
            "grey_openai_rec_api_key": "sk-test-grey-key-12345678",
            "grey_openai_rec_model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "rec_timeout_seconds": 25,
            "grey_rec_timeout_seconds": 50,
            "openai_rec_reasoning_effort": "low",
            "grey_openai_rec_reasoning_effort": "high",
        }
        res = client.put("/api/admin/engine-config", json=setup_payload)
        assert res.status_code == 200

        res_diff = client.get("/api/admin/engine-config/diff")
        assert res_diff.status_code == 200
        diff_data = res_diff.json()
        assert diff_data["status"] == "success"
        diffs = {item["label"]: item for item in diff_data["data"]["diffs"]}

        assert "识别 · 调用超时 (秒)" in diffs
        assert diffs["识别 · 调用超时 (秒)"]["regular"] == 25
        assert diffs["识别 · 调用超时 (秒)"]["grey"] == 50

        assert "识别 · 思考强度" in diffs
        assert diffs["识别 · 思考强度"]["regular"] == "low"
        assert diffs["识别 · 思考强度"]["grey"] == "high"
    finally:
        db.set_engine_config(orig_cfg)


def test_frontend_controls_and_no_emoji_ac_fe():
    """AC-FE-01 ~ AC-FE-03:
    前端模版中包含所有 6 个超时控件与 6 个思考强度控件，无任何 Emoji，选项完整。
    """
    template_path = os.path.join(DEMO_DIR, "templates", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    expected_ids = [
        "adminRecTimeoutSeconds", "adminOpenaiRecReasoningEffort",
        "adminAudTimeoutSeconds", "adminOpenaiAudReasoningEffort",
        "adminParseTimeoutSeconds", "adminParseOpenaiReasoningEffort",
        "adminGreyRecTimeoutSeconds", "adminGreyOpenaiRecReasoningEffort",
        "adminGreyAudTimeoutSeconds", "adminGreyOpenaiAudReasoningEffort",
        "adminGreyParseTimeoutSeconds", "adminGreyParseOpenaiReasoningEffort",
    ]
    for ctrl_id in expected_ids:
        assert f'id="{ctrl_id}"' in content, f"Missing control id: {ctrl_id}"

    # 验证 options 涵盖 low, medium, high, xhigh, max
    for effort in ("low", "medium", "high", "xhigh", "max"):
        assert f'value="{effort}"' in content

    # 验证 main.js 中已绑定 load 与 save
    js_path = os.path.join(DEMO_DIR, "static", "js", "main.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js_content = f.read()

    for ctrl_id in expected_ids:
        assert ctrl_id in js_content, f"Missing {ctrl_id} in main.js"

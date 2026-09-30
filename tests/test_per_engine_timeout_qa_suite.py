# -*- coding: utf-8 -*-
import os
import sys
import re
import unittest.mock as mock
import pytest
from starlette.testclient import TestClient
from langchain_core.messages import HumanMessage

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.models import (
    EngineConfig,
    clamp_call_timeout,
    VALID_REASONING_EFFORTS,
    EngineKind,
)
from app.llm import (
    _resolve_timeout,
    _resolve_reasoning_effort,
    _model_cache_key,
    OpenAIChatModel,
    build_recognition_model,
    build_audit_model,
    build_parse_model,
    get_timeout_advice,
)
from app.main import app

client = TestClient(app, headers={"X-Role": "admin"})


# -------------------------------------------------------------
# 1. 深度超时解析状态机与多层回退测试 (AC-MOD-02, AC-MOD-03)
# -------------------------------------------------------------

def test_resolve_timeout_complete_fallback_hierarchy(monkeypatch):
    """测试完整的分级回退逻辑：
    独立超时 -> 全局 call_timeout_seconds -> 环境变量 ENGINE_CALL_TIMEOUT -> 默认 30s
    """
    # 1. 优先级 1：独立超时生效
    cfg_full = EngineConfig(
        call_timeout_seconds=40,
        rec_timeout_seconds=18,
        aud_timeout_seconds=8,
        parse_timeout_seconds=12,
        grey_rec_timeout_seconds=22,
        grey_aud_timeout_seconds=9,
        grey_parse_timeout_seconds=14,
    )
    assert _resolve_timeout(cfg_full, side="rec", use_grey=False) == 18
    assert _resolve_timeout(cfg_full, side="aud", use_grey=False) == 8
    assert _resolve_timeout(cfg_full, side="parse", use_grey=False) == 12
    assert _resolve_timeout(cfg_full, side="rec", use_grey=True) == 22
    assert _resolve_timeout(cfg_full, side="aud", use_grey=True) == 9
    assert _resolve_timeout(cfg_full, side="parse", use_grey=True) == 14

    # 2. 优先级 2：独立超时为 None / 0 / 非法值，回退到全局 call_timeout_seconds
    cfg_fallback_global = EngineConfig(
        call_timeout_seconds=45,
        rec_timeout_seconds=None,
        aud_timeout_seconds=0,
        parse_timeout_seconds=None,
    )
    assert _resolve_timeout(cfg_fallback_global, side="rec", use_grey=False) == 45
    assert _resolve_timeout(cfg_fallback_global, side="aud", use_grey=False) == 45
    assert _resolve_timeout(cfg_fallback_global, side="parse", use_grey=False) == 45

    # 3. 优先级 3：cfg 无效或无全局超时，回退到环境变量 ENGINE_CALL_TIMEOUT
    monkeypatch.setenv("ENGINE_CALL_TIMEOUT", "28")
    cfg_empty = EngineConfig(call_timeout_seconds=0)
    assert _resolve_timeout(cfg_empty, side="rec", use_grey=False) == 28
    assert _resolve_timeout(None, side="rec", use_grey=False) == 28

    # 4. 环境变量超过 60s 时受硬上限钳制
    monkeypatch.setenv("ENGINE_CALL_TIMEOUT", "99")
    assert _resolve_timeout(None, side="rec", use_grey=False) == 60

    # 5. 环境变量非法字符串或 <= 0，回退到系统静态缺省 30s
    monkeypatch.setenv("ENGINE_CALL_TIMEOUT", "invalid_number")
    assert _resolve_timeout(None, side="rec", use_grey=False) == 30
    monkeypatch.setenv("ENGINE_CALL_TIMEOUT", "-5")
    assert _resolve_timeout(None, side="rec", use_grey=False) == 30
    monkeypatch.delenv("ENGINE_CALL_TIMEOUT", raising=False)
    assert _resolve_timeout(None, side="rec", use_grey=False) == 30


def test_resolve_timeout_clamping_to_60s():
    """测试独立超时字段超过 60s 时在解析时被截断为 60s。"""
    cfg = EngineConfig(
        call_timeout_seconds=80,
        rec_timeout_seconds=120,
        aud_timeout_seconds=75,
    )
    assert _resolve_timeout(cfg, side="rec", use_grey=False) == 60
    assert _resolve_timeout(cfg, side="aud", use_grey=False) == 60
    assert _resolve_timeout(cfg, side="parse", use_grey=False) == 60


def test_timeout_advice_warning_generation():
    """测试 get_timeout_advice 对各腿超限超时的告警生成。"""
    cfg_normal = EngineConfig(rec_timeout_seconds=30, aud_timeout_seconds=10)
    # 正常配置不告警（假设非 openai 引擎或合规）
    assert "超过硬上限" not in get_timeout_advice(cfg_normal)

    # 独立识别超时超限
    cfg_rec_overflow = EngineConfig(rec_timeout_seconds=65)
    advice = get_timeout_advice(cfg_rec_overflow)
    assert "rec_timeout_seconds=65 超过硬上限 60s" in advice

    # 独立审核超时超限
    cfg_aud_overflow = EngineConfig(aud_timeout_seconds=90)
    advice_aud = get_timeout_advice(cfg_aud_overflow)
    assert "aud_timeout_seconds=90 超过硬上限 60s" in advice_aud


# -------------------------------------------------------------
# 2. 思考强度解析与自适应注入验证 (AC-MOD-04, AC-PL-01, AC-PL-02)
# -------------------------------------------------------------

def test_resolve_reasoning_effort_all_whitelist_values():
    """验证所有白名单档位正确解析，大小写与空格容错。"""
    for effort in ("", "low", "medium", "high", "xhigh", "max"):
        cfg = EngineConfig(
            openai_rec_reasoning_effort=effort,
            openai_aud_reasoning_effort=f" {effort.upper()} " if effort else "",
        )
        assert _resolve_reasoning_effort(cfg, side="rec", use_grey=False) == effort
        assert _resolve_reasoning_effort(cfg, side="aud", use_grey=False) == effort

    # 非法档位归一为空字符串
    cfg_invalid = EngineConfig(openai_rec_reasoning_effort="ultra-high")
    assert _resolve_reasoning_effort(cfg_invalid, side="rec", use_grey=False) == ""


def test_openai_chat_model_payload_and_timeout_transmission():
    """严格断言 OpenAIChatModel 在 requests.post 时的行为：
    - reasoning_effort="" 时 payload 无键
    - reasoning_effort 存在时 payload 注入
    - timeout 严格透传 call_timeout
    """
    captured = {}

    def mock_post(url, headers=None, json=None, timeout=None, allow_redirects=False):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"status": "ok"}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        }
        return resp

    # 1. 空思考强度 + 15s 超时
    model1 = OpenAIChatModel(
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key="sk-test-key",
        call_timeout=15,
        reasoning_effort="",
    )
    with mock.patch("requests.post", side_effect=mock_post):
        model1.invoke([HumanMessage(content="test")])
        assert captured["timeout"] == 15
        assert "reasoning_effort" not in captured["json"]
        assert captured["json"]["model"] == "gpt-4o-mini"

    # 2. 有效思考强度 "low" + 25s 超时
    model2 = OpenAIChatModel(
        model="o3-mini",
        base_url="https://api.openai.com/v1",
        api_key="sk-test-key",
        call_timeout=25,
        reasoning_effort="low",
    )
    with mock.patch("requests.post", side_effect=mock_post):
        model2.invoke([HumanMessage(content="test")])
        assert captured["timeout"] == 25
        assert captured["json"]["reasoning_effort"] == "low"

    # 3. 非法思考强度（直接传入）防护 + 45s 超时
    model3 = OpenAIChatModel(
        model="o1",
        base_url="https://api.openai.com/v1",
        api_key="sk-test-key",
        call_timeout=45,
        reasoning_effort="illegal_tier",
    )
    with mock.patch("requests.post", side_effect=mock_post):
        model3.invoke([HumanMessage(content="test")])
        assert captured["timeout"] == 45
        assert "reasoning_effort" not in captured["json"]


# -------------------------------------------------------------
# 3. 模型构建工厂端到端穿透测试 (AC-PL-03)
# -------------------------------------------------------------

def test_model_builder_factories_leg_isolation():
    """验证 build_recognition_model / build_audit_model / build_parse_model
    对各腿配置的独立超时与思考强度的正确注入与隔离。
    """
    cfg = EngineConfig(
        recognition_engine=EngineKind.OPENAI,
        openai_rec_base_url="https://api.openai.com/v1",
        openai_rec_api_key="sk-rec-key",
        openai_rec_model="gpt-4o",
        rec_timeout_seconds=22,
        openai_rec_reasoning_effort="low",

        audit_engine=EngineKind.OPENAI,
        openai_aud_base_url="https://api.openai.com/v1",
        openai_aud_api_key="sk-aud-key",
        openai_aud_model="gpt-4o-mini",
        aud_timeout_seconds=11,
        openai_aud_reasoning_effort="high",

        parse_llm_engine=EngineKind.OPENAI,
        openai_parse_base_url="https://api.openai.com/v1",
        openai_parse_api_key="sk-parse-key",
        openai_parse_model="gpt-4o-mini",
        parse_timeout_seconds=17,
        openai_parse_reasoning_effort="medium",

        grey_recognition_engine=EngineKind.OPENAI,
        grey_openai_rec_base_url="https://api.openai.com/v1",
        grey_openai_rec_api_key="sk-grey-rec-key",
        grey_openai_rec_model="gpt-4o",
        grey_rec_timeout_seconds=33,
        grey_openai_rec_reasoning_effort="xhigh",

        grey_audit_engine=EngineKind.OPENAI,
        grey_openai_aud_base_url="https://api.openai.com/v1",
        grey_openai_aud_api_key="sk-grey-aud-key",
        grey_openai_aud_model="gpt-4o-mini",
        grey_aud_timeout_seconds=14,
        grey_openai_aud_reasoning_effort="max",

        grey_parse_llm_engine=EngineKind.OPENAI,
        grey_openai_parse_base_url="https://api.openai.com/v1",
        grey_openai_parse_api_key="sk-grey-parse-key",
        grey_openai_parse_model="gpt-4o-mini",
        grey_parse_timeout_seconds=19,
        grey_openai_parse_reasoning_effort="low",
    )

    # 1. 常规识别腿
    rec_model = build_recognition_model(cfg=cfg, use_grey=False)
    assert rec_model.call_timeout == 22
    assert rec_model.reasoning_effort == "low"

    # 2. 灰测识别腿
    grey_rec_model = build_recognition_model(cfg=cfg, use_grey=True)
    assert grey_rec_model.call_timeout == 33
    assert grey_rec_model.reasoning_effort == "xhigh"

    # 3. 常规审核腿
    aud_model = build_audit_model(cfg=cfg, use_grey=False)
    assert aud_model.call_timeout == 11
    assert aud_model.reasoning_effort == "high"
    assert aud_model.temperature == 0.0  # 审核腿确定性保障

    # 4. 灰测审核腿
    grey_aud_model = build_audit_model(cfg=cfg, use_grey=True)
    assert grey_aud_model.call_timeout == 14
    assert grey_aud_model.reasoning_effort == "max"
    assert grey_aud_model.temperature == 0.0

    # 5. 常规解析腿
    parse_model = build_parse_model(cfg=cfg, use_grey=False)
    assert parse_model.call_timeout == 17
    assert parse_model.reasoning_effort == "medium"

    # 6. 灰测解析腿
    grey_parse_model = build_parse_model(cfg=cfg, use_grey=True)
    assert grey_parse_model.call_timeout == 19
    assert grey_parse_model.reasoning_effort == "low"


# -------------------------------------------------------------
# 4. 管理 API 完整 12 字段存取与生命周期 (AC-API-01~06)
# -------------------------------------------------------------

def test_api_engine_config_12_fields_roundtrip():
    """测试 PUT /api/admin/engine-config 写入 12 个新字段，
    并由 GET /api/admin/engine-config 完整读出且数据一致。
    """
    orig_cfg = db.get_engine_config()
    try:
        new_payload = {
            "rec_timeout_seconds": 24,
            "aud_timeout_seconds": 13,
            "parse_timeout_seconds": 16,
            "grey_rec_timeout_seconds": 36,
            "grey_aud_timeout_seconds": 18,
            "grey_parse_timeout_seconds": 21,
            "openai_rec_reasoning_effort": "low",
            "openai_aud_reasoning_effort": "medium",
            "openai_parse_reasoning_effort": "high",
            "grey_openai_rec_reasoning_effort": "xhigh",
            "grey_openai_aud_reasoning_effort": "max",
            "grey_openai_parse_reasoning_effort": "low",
        }
        res_put = client.put("/api/admin/engine-config", json=new_payload)
        assert res_put.status_code == 200

        res_get = client.get("/api/admin/engine-config")
        assert res_get.status_code == 200
        data = res_get.json()["data"]

        for k, v in new_payload.items():
            assert data.get(k) == v, f"Field {k} expected {v}, got {data.get(k)}"

        # 检查 perf 诊断中的实际生效值
        perf = res_get.json()["perf"]["effective_timeouts"]
        assert perf["rec"] == 24
        assert perf["aud"] == 13
        assert perf["parse"] == 16
        assert perf["grey_rec"] == 36
        assert perf["grey_aud"] == 18
        assert perf["grey_parse"] == 21
    finally:
        db.set_engine_config(orig_cfg)


def test_api_illegal_reasoning_effort_sanitization():
    """测试 PUT API 传入非法 reasoning_effort 字符串时，系统自动归一为 ''。"""
    orig_cfg = db.get_engine_config()
    try:
        payload = {
            "openai_rec_reasoning_effort": "super-fast",
            "grey_openai_rec_reasoning_effort": "invalid_tier",
        }
        res = client.put("/api/admin/engine-config", json=payload)
        assert res.status_code == 200

        cfg = db.get_engine_config()
        assert cfg.openai_rec_reasoning_effort == ""
        assert cfg.grey_openai_rec_reasoning_effort == ""
    finally:
        db.set_engine_config(orig_cfg)


def test_api_promote_rollback_full_cycle():
    """测试推全与回滚在分腿超时与思考强度上的完整还原能力。"""
    orig_cfg = db.get_engine_config()
    try:
        # 设定初始状态：常规组 A，灰测组 B
        setup_payload = {
            "rec_timeout_seconds": 15,
            "openai_rec_reasoning_effort": "low",
            "aud_timeout_seconds": 8,
            "openai_aud_reasoning_effort": "low",
            "parse_timeout_seconds": 12,
            "openai_parse_reasoning_effort": "low",

            "grey_enabled": True,
            "grey_percent": 50,
            "grey_openai_rec_base_url": "https://api.siliconflow.cn/v1",
            "grey_openai_rec_api_key": "sk-test-grey-key-12345678",
            "grey_openai_rec_model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "grey_rec_timeout_seconds": 45,
            "grey_openai_rec_reasoning_effort": "high",
            "grey_aud_timeout_seconds": 16,
            "grey_openai_aud_reasoning_effort": "medium",
            "grey_parse_timeout_seconds": 22,
            "grey_openai_parse_reasoning_effort": "xhigh",
        }
        client.put("/api/admin/engine-config", json=setup_payload)

        # 1. 推全
        promote_res = client.put("/api/admin/engine-config/promote")
        assert promote_res.status_code == 200

        cfg_p = db.get_engine_config()
        assert cfg_p.rec_timeout_seconds == 45
        assert cfg_p.openai_rec_reasoning_effort == "high"
        assert cfg_p.aud_timeout_seconds == 16
        assert cfg_p.openai_aud_reasoning_effort == "medium"
        assert cfg_p.parse_timeout_seconds == 22
        assert cfg_p.openai_parse_reasoning_effort == "xhigh"
        # 灰测应重置
        assert cfg_p.grey_rec_timeout_seconds is None
        assert cfg_p.grey_openai_rec_reasoning_effort == ""
        assert cfg_p.grey_aud_timeout_seconds is None
        assert cfg_p.grey_openai_aud_reasoning_effort == ""
        assert cfg_p.grey_parse_timeout_seconds is None
        assert cfg_p.grey_openai_parse_reasoning_effort == ""

        # 2. 回滚
        rollback_res = client.put("/api/admin/engine-config/rollback")
        assert rollback_res.status_code == 200

        cfg_r = db.get_engine_config()
        assert cfg_r.rec_timeout_seconds == 15
        assert cfg_r.openai_rec_reasoning_effort == "low"
        assert cfg_r.aud_timeout_seconds == 8
        assert cfg_r.openai_aud_reasoning_effort == "low"
        assert cfg_r.parse_timeout_seconds == 12
        assert cfg_r.openai_parse_reasoning_effort == "low"
    finally:
        db.set_engine_config(orig_cfg)


# -------------------------------------------------------------
# 5. 全局 Emoji 扫描与静态规范核验 (Directives Rule 1)
# -------------------------------------------------------------

def test_no_emojis_in_source_and_templates():
    """扫描本次涉及的核心代码、模版与脚本文件，断言绝无任何 Emoji 字符。"""
    files_to_check = [
        os.path.join(DEMO_DIR, "app", "models.py"),
        os.path.join(DEMO_DIR, "app", "llm.py"),
        os.path.join(DEMO_DIR, "app", "api_admin.py"),
        os.path.join(DEMO_DIR, "app", "db.py"),
        os.path.join(DEMO_DIR, "templates", "index.html"),
        os.path.join(DEMO_DIR, "static", "js", "main.js"),
    ]

    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002700-\U000027BF"  # dingbats
        "\U00002600-\U000026FF"  # misc symbols
        "\U0001F900-\U0001F9FF"  # supplemental symbols
        "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-a
        "]+",
        flags=re.UNICODE,
    )

    for file_path in files_to_check:
        assert os.path.exists(file_path), f"File not found: {file_path}"
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        matches = emoji_pattern.findall(content)
        assert len(matches) == 0, f"Found emoji(s) {matches} in {file_path}"

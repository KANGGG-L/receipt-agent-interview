# -*- coding: utf-8 -*-
import os
import sys
import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.main import app

client = TestClient(app, headers={"X-Role": "admin"})


def test_put_engine_config_pure_openai_auto_syncs_legacy_fields():
    orig_cfg = db.get_engine_config()
    try:
        payload = {
            "openai_rec_base_url": "https://api.siliconflow.cn/v1",
            "openai_rec_api_key": "sk-sync-test-key12345678",
            "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            "audit_enabled": True,
            "audit_mode": "text",
            "openai_aud_base_url": "https://api.siliconflow.cn/v1",
            "openai_aud_api_key": "sk-sync-aud-key87654321",
            "openai_aud_model": "zai-org/GLM-4.5V",
            "parse_llm_enabled": True,
            "openai_parse_base_url": "https://api.siliconflow.cn/v1",
            "openai_parse_api_key": "sk-sync-parse-key11223344",
            "openai_parse_model": "meituan-longcat/LongCat-2.0",
        }
        res = client.put("/api/admin/engine-config", json=payload)
        assert res.status_code == 200

        cfg = db.get_engine_config()
        # Verify automatic synchronization to legacy model and engine fields
        assert cfg.recognition_engine.value == "openai"
        assert cfg.recognition_model == "Qwen/Qwen3-VL-32B-Instruct"
        assert cfg.audit_engine.value == "openai"
        assert cfg.audit_model == "zai-org/GLM-4.5V"
        assert cfg.parse_llm_engine.value == "openai"
        assert cfg.parse_llm_model == "meituan-longcat/LongCat-2.0"
    finally:
        db.set_engine_config(orig_cfg)


def test_put_engine_config_grey_pure_openai_auto_syncs_legacy_fields():
    orig_cfg = db.get_engine_config()
    try:
        payload = {
            "grey_enabled": True,
            "grey_percent": 20,
            "grey_openai_rec_base_url": "https://api.siliconflow.cn/v1",
            "grey_openai_rec_api_key": "sk-sync-greyrec-12345678",
            "grey_openai_rec_model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "grey_audit_enabled": True,
            "grey_openai_aud_base_url": "https://api.siliconflow.cn/v1",
            "grey_openai_aud_api_key": "sk-sync-greyaud-87654321",
            "grey_openai_aud_model": "zai-org/GLM-4.5V",
            "grey_parse_llm_enabled": True,
            "grey_openai_parse_base_url": "https://api.siliconflow.cn/v1",
            "grey_openai_parse_api_key": "sk-sync-greyparse-11223344",
            "grey_openai_parse_model": "Qwen/Qwen3-VL-32B-Instruct",
        }
        res = client.put("/api/admin/engine-config", json=payload)
        assert res.status_code == 200

        cfg = db.get_engine_config()
        assert cfg.grey_recognition_engine.value == "openai"
        assert cfg.grey_recognition_model == "Qwen/Qwen2.5-VL-72B-Instruct"
        assert cfg.grey_audit_engine.value == "openai"
        assert cfg.grey_audit_model == "zai-org/GLM-4.5V"
        assert cfg.grey_parse_llm_engine.value == "openai"
        assert cfg.grey_parse_llm_model == "Qwen/Qwen3-VL-32B-Instruct"
    finally:
        db.set_engine_config(orig_cfg)


def test_engine_presets_endpoint():
    res = client.get("/api/admin/engine-presets")
    assert res.status_code == 200
    data = res.json()["data"]
    assert "agnes" in data
    assert "bailian" in data
    assert "siliconflow" in data
    assert data["siliconflow"]["base_url"] == "https://api.siliconflow.cn/v1"
    assert data["siliconflow"]["rec_model"] == "Qwen/Qwen3-VL-32B-Instruct"


def test_post_test_engine_config_rejects_ssrf_pure_openai():
    payload = {
        "openai_rec_base_url": "http://169.254.169.254/latest/meta-data",
        "openai_rec_api_key": "sk-sync-test-key12345678",
        "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
    }
    res = client.post("/api/admin/test-engine-config", json=payload)
    assert res.status_code == 400
    msg = res.json().get("msg", "")
    assert any(kw in msg for kw in ("安全阻断", "非法 Base URL", "安全校验未通过", "私网", "元数据"))


def test_put_engine_config_masks_secret_in_response():
    orig_cfg = db.get_engine_config()
    try:
        payload = {
            "openai_rec_base_url": "https://api.siliconflow.cn/v1",
            "openai_rec_api_key": "sk-mask-test-key12345678",
            "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
        }
        res = client.put("/api/admin/engine-config", json=payload)
        assert res.status_code == 200
        assert "sk-mask-test-key12345678" not in res.text
        data = res.json()["data"]
        assert data["openai_rec_api_key"] == "sk-mas****5678"
    finally:
        db.set_engine_config(orig_cfg)


def test_quick_test_engine_defensive_url_stripping(monkeypatch):
    from app.api_admin import _quick_test_engine

    requested_urls = []

    class DummyResponse:
        def __init__(self, status_code):
            self.status_code = status_code
            self.text = "ok"

    def mock_get(url, **kwargs):
        requested_urls.append(("GET", url))
        return DummyResponse(404)

    def mock_post(url, **kwargs):
        requested_urls.append(("POST", url))
        return DummyResponse(200)

    monkeypatch.setattr("requests.get", mock_get)
    monkeypatch.setattr("requests.post", mock_post)

    err = _quick_test_engine("openai", "test-model", "https://api.example.com/v1/models", "sk-validkey1234", "测试")
    assert err is None
    assert requested_urls[0] == ("GET", "https://api.example.com/v1/models")
    assert requested_urls[1] == ("POST", "https://api.example.com/v1/chat/completions")


def test_template_removes_legacy_dropdowns_and_presents_dual_columns():
    html_path = os.path.abspath(os.path.join(DEMO_DIR, "templates", "index.html"))
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 断言已彻底移除 12 个旧选择控件
    legacy_ids = [
        'id="adminRecognitionEngine"',
        'id="adminRecognitionModel"',
        'id="adminParseEngine"',
        'id="adminParseModel"',
        'id="adminAuditEngine"',
        'id="adminAuditModel"',
        'id="adminGreyRecEngine"',
        'id="adminGreyRecModel"',
        'id="adminGreyAudEngine"',
        'id="adminGreyAudModel"',
        'id="adminGreyParseEngine"',
        'id="adminGreyParseModel"',
    ]
    for lid in legacy_ids:
        assert lid not in html, f"Legacy control {lid} should be removed from template"

    # 断言存在核心双列卡片与高级抽屉结构
    assert 'id="adminRecognitionCard"' in html
    assert 'id="adminAuditCard"' in html
    assert 'id="adminParseDrawer"' in html
    assert 'id="adminGreyDrawer"' in html

    # 断言保留必须的 OpenAI 输入项与预设选择
    assert 'id="adminOpenaiRecBaseUrl"' in html
    assert 'id="adminOpenaiRecApiKey"' in html
    assert 'id="adminOpenaiRecModel"' in html
    assert 'id="adminOpenaiRecPreset"' in html

    assert 'id="adminOpenaiAudBaseUrl"' in html
    assert 'id="adminOpenaiAudApiKey"' in html
    assert 'id="adminOpenaiAudModel"' in html
    assert 'id="adminOpenaiAudPreset"' in html

    assert 'id="adminParseOpenaiBaseUrl"' in html
    assert 'id="adminParseOpenaiApiKey"' in html
    assert 'id="adminParseOpenaiModel"' in html
    assert 'id="adminParseOpenaiPreset"' in html

    assert 'id="adminGreyOpenaiRecBaseUrl"' in html
    assert 'id="adminGreyOpenaiAudBaseUrl"' in html
    assert 'id="adminGreyParseOpenaiBaseUrl"' in html

    # 断言动作按钮与状态容器
    assert 'id="adminSaveEngineBtn"' in html
    assert 'id="adminRollbackEngineBtn"' in html
    assert 'id="adminPromoteGreyBtn"' in html
    assert 'id="adminEngineStatus"' in html



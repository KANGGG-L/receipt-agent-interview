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
    assert html.count('class="engine-drawer-header" role="button" tabindex="0" aria-expanded="false"') == 3

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


def test_main_js_no_longer_queries_removed_engine_controls():
    js_path = os.path.abspath(os.path.join(DEMO_DIR, "static", "js", "main.js"))
    with open(js_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 验证 saveAdminEngineConfig 与 bindAdminEngineEventsOnce 不再依赖已删除的旧控件
    for removed_id in [
        "adminRecognitionEngine",
        "adminRecognitionModel",
        "adminParseEngine",
        "adminParseModel",
        "adminAuditEngine",
        "adminAuditModel",
        "adminGreyRecEngine",
        "adminGreyRecModel",
        "adminGreyAudEngine",
        "adminGreyAudModel",
        "adminGreyParseEngine",
        "adminGreyParseModel",
    ]:
        assert f"document.getElementById('{removed_id}')" not in content, f"Found lingering query for removed element {removed_id}"

    # 废弃函数已被移除
    assert "function fillModelOptions" not in content
    assert "function handleModelSelectChange" not in content
    assert "function syncModelSelectOpenaiState" not in content
    assert "function handleDeleteCurrentModel" not in content

    # 新抽屉交互函数存在
    assert "function toggleEngineDrawer" in content


def test_main_js_engine_drawer_and_pure_openai_interactions():
    js_path = os.path.abspath(os.path.join(DEMO_DIR, "static", "js", "main.js"))
    with open(js_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 抽屉折叠与无障碍逻辑
    assert "function toggleEngineDrawer" in content
    assert "aria-expanded" in content
    assert "engine-drawer-header" in content
    assert "adminParseDrawer" in content
    assert "adminGreyDrawer" in content

    # 回显时自动展开抽屉
    assert "parseDrawer.classList.toggle('collapsed'" in content
    assert "greyDrawer.classList.toggle('collapsed'" in content

    # 直接读取 pure OpenAI 字段
    for el_id in [
        "adminOpenaiRecBaseUrl",
        "adminOpenaiRecApiKey",
        "adminOpenaiRecModel",
        "adminOpenaiAudBaseUrl",
        "adminOpenaiAudApiKey",
        "adminOpenaiAudModel",
        "adminParseOpenaiBaseUrl",
        "adminParseOpenaiApiKey",
        "adminParseOpenaiModel",
        "adminGreyOpenaiRecBaseUrl",
        "adminGreyOpenaiRecApiKey",
        "adminGreyOpenaiRecModel",
        "adminGreyOpenaiAudBaseUrl",
        "adminGreyOpenaiAudApiKey",
        "adminGreyOpenaiAudModel",
        "adminGreyParseOpenaiBaseUrl",
        "adminGreyParseOpenaiApiKey",
        "adminGreyParseOpenaiModel",
    ]:
        assert el_id in content, f"Expected {el_id} in main.js"


def test_engine_config_full_lifecycle_and_security_adversarial(monkeypatch):
    orig_cfg = db.get_engine_config()
    try:
        # 1. Read engine config (verify initial masked keys)
        res_get = client.get("/api/admin/engine-config")
        assert res_get.status_code == 200
        get_data = res_get.json()["data"]
        for key_field in [
            "openai_rec_api_key", "openai_aud_api_key", "openai_parse_api_key",
            "grey_openai_rec_api_key", "grey_openai_aud_api_key", "grey_openai_parse_api_key"
        ]:
            val = get_data.get(key_field, "")
            if val:
                assert "****" in val, f"Expected masked key in {key_field}, got {val}"

        # 2. Trigger probe with SSRF payload (cloud metadata 169.254.169.254 / localhost) -> verify blocked (SSRF guard)
        ssrf_payloads = [
            {
                "openai_rec_base_url": "http://169.254.169.254/latest/meta-data",
                "openai_rec_api_key": "sk-ssrf-probe-key",
                "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            },
            {
                "openai_rec_base_url": "http://127.0.0.1:8000/v1",
                "openai_rec_api_key": "sk-ssrf-probe-key",
                "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            },
            {
                "openai_rec_base_url": "http://localhost:8080/v1",
                "openai_rec_api_key": "sk-ssrf-probe-key",
                "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            },
        ]
        for payload in ssrf_payloads:
            res_probe_ssrf = client.post("/api/admin/test-engine-config", json=payload)
            assert res_probe_ssrf.status_code == 400
            err_msg = res_probe_ssrf.json().get("msg", "")
            assert any(kw in err_msg for kw in ("安全阻断", "非法 Base URL", "安全校验未通过", "私网", "元数据", "回环")), f"Unexpected msg: {err_msg}"

        # 3. Trigger probe with public OpenAI url -> verify clean response or mocked model probe
        class MockResponse:
            def __init__(self, status_code=200, json_data=None):
                self.status_code = status_code
                self._json = json_data or {"data": []}
                self.text = "ok"
            def json(self):
                return self._json

        monkeypatch.setattr("requests.get", lambda url, **kw: MockResponse(200))
        monkeypatch.setattr("requests.post", lambda url, **kw: MockResponse(200))

        valid_probe_payload = {
            "openai_rec_base_url": "https://api.openai.com/v1",
            "openai_rec_api_key": "sk-valid-probe-key12345678",
            "openai_rec_model": "gpt-4o",
        }
        res_probe_valid = client.post("/api/admin/test-engine-config", json=valid_probe_payload)
        assert res_probe_valid.status_code == 200
        assert res_probe_valid.json()["status"] == "success"

        # 4. Save pure OpenAI payload with new models and keys
        main_rec_key = "sk-mainreckey-1234567890"
        main_aud_key = "sk-mainaudkey-0987654321"
        main_parse_key = "sk-mainparsekey-1122334455"
        grey_rec_key = "sk-greyreckey-9988776655"
        grey_aud_key = "sk-greyaudkey-5544332211"
        grey_parse_key = "sk-greyparsekey-6677889900"

        save_payload = {
            "openai_rec_base_url": "https://api.siliconflow.cn/v1",
            "openai_rec_api_key": main_rec_key,
            "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
            "audit_enabled": True,
            "openai_aud_base_url": "https://api.siliconflow.cn/v1",
            "openai_aud_api_key": main_aud_key,
            "openai_aud_model": "zai-org/GLM-4.5V",
            "parse_llm_enabled": True,
            "openai_parse_base_url": "https://api.siliconflow.cn/v1",
            "openai_parse_api_key": main_parse_key,
            "openai_parse_model": "meituan-longcat/LongCat-2.0",
            # Grey config
            "grey_enabled": True,
            "grey_percent": 35,
            "grey_openai_rec_base_url": "https://api.siliconflow.cn/v1",
            "grey_openai_rec_api_key": grey_rec_key,
            "grey_openai_rec_model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "grey_audit_enabled": True,
            "grey_openai_aud_base_url": "https://api.siliconflow.cn/v1",
            "grey_openai_aud_api_key": grey_aud_key,
            "grey_openai_aud_model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "grey_parse_llm_enabled": True,
            "grey_openai_parse_base_url": "https://api.siliconflow.cn/v1",
            "grey_openai_parse_api_key": grey_parse_key,
            "grey_openai_parse_model": "deepseek-ai/DeepSeek-V3",
        }
        res_save = client.put("/api/admin/engine-config", json=save_payload)
        assert res_save.status_code == 200
        for raw_k in [main_rec_key, main_aud_key, main_parse_key, grey_rec_key, grey_aud_key, grey_parse_key]:
            assert raw_k not in res_save.text
        save_data = res_save.json()["data"]
        assert save_data["openai_rec_api_key"] == "sk-mai****7890"

        # 5. Verify auto-sync to legacy fields
        cfg = db.get_engine_config()
        assert cfg.recognition_engine.value == "openai"
        assert cfg.recognition_model == "Qwen/Qwen3-VL-32B-Instruct"
        assert cfg.openai_rec_model == "Qwen/Qwen3-VL-32B-Instruct"

        assert cfg.audit_engine.value == "openai"
        assert cfg.audit_model == "zai-org/GLM-4.5V"
        assert cfg.openai_aud_model == "zai-org/GLM-4.5V"

        assert cfg.parse_llm_engine.value == "openai"
        assert cfg.parse_llm_model == "meituan-longcat/LongCat-2.0"
        assert cfg.openai_parse_model == "meituan-longcat/LongCat-2.0"

        assert cfg.grey_enabled is True
        assert cfg.grey_recognition_engine.value == "openai"
        assert cfg.grey_recognition_model == "Qwen/Qwen2.5-VL-72B-Instruct"
        assert cfg.grey_openai_rec_model == "Qwen/Qwen2.5-VL-72B-Instruct"

        assert cfg.grey_audit_engine.value == "openai"
        assert cfg.grey_audit_model == "Qwen/Qwen2.5-VL-72B-Instruct"
        assert cfg.grey_openai_aud_model == "Qwen/Qwen2.5-VL-72B-Instruct"

        assert cfg.grey_parse_llm_engine.value == "openai"
        assert cfg.grey_parse_llm_model == "deepseek-ai/DeepSeek-V3"
        assert cfg.grey_openai_parse_model == "deepseek-ai/DeepSeek-V3"

        # 6. Promote grey config (/api/admin/engine-config/promote) -> verify grey OpenAI parameters promote to main OpenAI parameters and sync to legacy fields
        res_promote = client.put("/api/admin/engine-config/promote")
        assert res_promote.status_code == 200
        for raw_k in [main_rec_key, main_aud_key, main_parse_key, grey_rec_key, grey_aud_key, grey_parse_key]:
            assert raw_k not in res_promote.text
        promote_data = res_promote.json()["data"]
        assert promote_data["openai_rec_api_key"] == "sk-gre****6655"

        cfg_promoted = db.get_engine_config()
        assert cfg_promoted.openai_rec_model == "Qwen/Qwen2.5-VL-72B-Instruct"
        assert cfg_promoted.openai_rec_api_key == grey_rec_key
        assert cfg_promoted.openai_aud_model == "Qwen/Qwen2.5-VL-72B-Instruct"
        assert cfg_promoted.openai_aud_api_key == grey_aud_key
        assert cfg_promoted.openai_parse_model == "deepseek-ai/DeepSeek-V3"
        assert cfg_promoted.openai_parse_api_key == grey_parse_key

        assert cfg_promoted.recognition_engine.value == "openai"
        assert cfg_promoted.recognition_model == "Qwen/Qwen2.5-VL-72B-Instruct"
        assert cfg_promoted.audit_engine.value == "openai"
        assert cfg_promoted.audit_model == "Qwen/Qwen2.5-VL-72B-Instruct"
        assert cfg_promoted.parse_llm_engine.value == "openai"
        assert cfg_promoted.parse_llm_model == "deepseek-ai/DeepSeek-V3"

        assert cfg_promoted.grey_enabled is False
        assert cfg_promoted.rollback_snapshot is not None

        # 7. Rollback engine config (/api/admin/engine-config/rollback) -> verify rollback works cleanly
        res_rollback = client.put("/api/admin/engine-config/rollback")
        assert res_rollback.status_code == 200
        for raw_k in [main_rec_key, main_aud_key, main_parse_key, grey_rec_key, grey_aud_key, grey_parse_key]:
            assert raw_k not in res_rollback.text

        cfg_rolled_back = db.get_engine_config()
        assert cfg_rolled_back.openai_rec_model == "Qwen/Qwen3-VL-32B-Instruct"
        assert cfg_rolled_back.openai_rec_api_key == main_rec_key
        assert cfg_rolled_back.recognition_model == "Qwen/Qwen3-VL-32B-Instruct"
        assert cfg_rolled_back.recognition_engine.value == "openai"

        assert cfg_rolled_back.openai_aud_model == "zai-org/GLM-4.5V"
        assert cfg_rolled_back.openai_aud_api_key == main_aud_key
        assert cfg_rolled_back.audit_model == "zai-org/GLM-4.5V"
        assert cfg_rolled_back.audit_engine.value == "openai"

        assert cfg_rolled_back.openai_parse_model == "meituan-longcat/LongCat-2.0"
        assert cfg_rolled_back.openai_parse_api_key == main_parse_key
        assert cfg_rolled_back.parse_llm_model == "meituan-longcat/LongCat-2.0"
        assert cfg_rolled_back.parse_llm_engine.value == "openai"

        assert cfg_rolled_back.rollback_snapshot is None
    finally:
        db.set_engine_config(orig_cfg)


def test_test_engine_config_unmasks_masked_api_key(monkeypatch):
    orig_cfg = db.get_engine_config()
    try:
        # Pre-seed database with a valid real key
        real_key = "sk-real-test-secret-key-1234567890"
        seeded_cfg = orig_cfg.model_copy()
        seeded_cfg.openai_rec_base_url = "https://api.openai.com/v1"
        seeded_cfg.openai_rec_api_key = real_key
        seeded_cfg.openai_rec_model = "gpt-4o"
        seeded_cfg.audit_enabled = False
        seeded_cfg.parse_llm_enabled = False
        seeded_cfg.grey_enabled = False
        db.set_engine_config(seeded_cfg)

        used_auth_headers = []

        class MockResponse:
            def __init__(self, status_code=200):
                self.status_code = status_code
                self.text = "ok"

            def json(self):
                return {"data": []}

        def mock_get(url, headers=None, **kwargs):
            if headers and "Authorization" in headers:
                used_auth_headers.append(headers["Authorization"])
            return MockResponse(200)

        monkeypatch.setattr("requests.get", mock_get)
        monkeypatch.setattr("requests.post", lambda *a, **kw: MockResponse(200))

        # Administrator tests configuration with a masked key
        payload = {
            "openai_rec_base_url": "https://api.openai.com/v1",
            "openai_rec_api_key": "sk-****1234",
            "openai_rec_model": "gpt-4o",
        }
        res = client.post("/api/admin/test-engine-config", json=payload)
        assert res.status_code == 200
        assert res.json().get("status") == "success"

        # Verify probe received the unmasked real key from db, not the masked key
        assert used_auth_headers
        assert f"Bearer {real_key}" in used_auth_headers
        assert "sk-****1234" not in used_auth_headers[0]
    finally:
        db.set_engine_config(orig_cfg)


def test_diff_regular_vs_grey_shows_unmasked_model_names():
    orig_cfg = db.get_engine_config()
    try:
        cfg = orig_cfg.model_copy()
        cfg.grey_enabled = True
        cfg.openai_rec_model = "Qwen/Qwen3-VL-32B-Instruct"
        cfg.grey_openai_rec_model = "Qwen/Qwen2.5-VL-72B-Instruct"
        db.set_engine_config(cfg)

        res = client.get("/api/admin/engine-config/diff")
        assert res.status_code == 200
        diffs = res.json()["data"]["diffs"]
        model_diff = next((d for d in diffs if d["label"] == "识别 · OpenAI 模型名"), None)
        assert model_diff is not None
        assert model_diff["sensitive"] is False
        assert model_diff["regular"] == "Qwen/Qwen3-VL-32B-Instruct"
        assert model_diff["grey"] == "Qwen/Qwen2.5-VL-72B-Instruct"
    finally:
        db.set_engine_config(orig_cfg)


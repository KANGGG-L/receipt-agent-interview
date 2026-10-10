# -*- coding: utf-8 -*-
"""用户自定义引擎配置 DB 持久化与模版管理全链路测试集。

覆盖：
1. GET /api/admin/engine-presets 纯 DB 驱动与密钥脱敏（无硬编码商业供应商）。
2. POST /api/admin/engine-presets 全参数存储（模型名、超时、重试、Token单价、思考强度）与参数边界校验。
3. POST /api/admin/engine-presets SSRF 安全网关阻断。
4. POST /api/admin/engine-presets 掩码密钥安全继承。
5. DELETE /api/admin/engine-presets/{key} 专属删除端点（成功删除与 404 判定）。
6. PUT /api/admin/engine-config 跨模版脱敏密钥安全还原（掩码智能比对与还原真实密钥）。
"""

import json
import os
import sys
import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.main import app
from app.services.security_guard import mask_secret_key

client = TestClient(app, headers={"X-Role": "admin"})

TEST_REAL_KEY = "sk-custom-vllm-secret-9876543210"
TEST_MASKED_KEY = mask_secret_key(TEST_REAL_KEY)


def test_presets_get_clean_and_masked():
    orig_custom = db.get_app_setting("custom_engine_presets", "{}")
    try:
        custom_data = {
            "internal_vllm": {
                "key": "internal_vllm",
                "label": "自建vLLM-Qwen",
                "base_url": "https://api.openai.com/v1",
                "api_key": TEST_REAL_KEY,
                "model": "qwen2.5-vl-72b",
                "timeout_seconds": 45,
                "max_retry_rounds": 3,
                "price_input": 0.8,
                "price_cached": 0.2,
                "price_output": 2.5,
                "reasoning_effort": "medium",
                "is_custom": True,
            }
        }
        db.set_app_setting("custom_engine_presets", json.dumps(custom_data))

        resp = client.get("/api/admin/engine-presets")
        assert resp.status_code == 200
        data = resp.json()["data"]

        # 无硬编码第三方商业预设
        assert "bailian" not in data
        assert "siliconflow" not in data
        assert "agnes" not in data

        # 正确返回自定义模版与脱敏 key
        assert "internal_vllm" in data
        entry = data["internal_vllm"]
        assert entry["label"] == "自建vLLM-Qwen"
        assert entry["api_key"] == TEST_MASKED_KEY
        assert "secret" not in entry["api_key"]
        assert entry["timeout_seconds"] == 45
        assert entry["max_retry_rounds"] == 3
        assert entry["price_input"] == 0.8
        assert entry["reasoning_effort"] == "medium"
    finally:
        db.set_app_setting("custom_engine_presets", orig_custom)


def test_presets_post_full_fields_and_persistence():
    orig_custom = db.get_app_setting("custom_engine_presets", "{}")
    try:
        payload = {
            "key": "my_new_preset",
            "label": "新自建模版",
            "base_url": "https://api.openai.com/v1",
            "api_key": TEST_REAL_KEY,
            "model": "qwen2.5-vl-32b",
            "timeout_seconds": 35,
            "max_retry_rounds": 2,
            "price_input": 0.6,
            "price_cached": 0.15,
            "price_output": 1.8,
            "reasoning_effort": "low",
        }
        resp = client.post("/api/admin/engine-presets", json=payload)
        assert resp.status_code == 200
        res_data = resp.json()
        assert res_data["status"] == "success"
        assert res_data["key"] == "my_new_preset"

        # 检查数据库持久化
        db_raw = db.get_app_setting("custom_engine_presets", "{}")
        db_data = json.loads(db_raw)
        assert "my_new_preset" in db_data
        saved = db_data["my_new_preset"]
        assert saved["label"] == "新自建模版"
        assert saved["api_key"] == TEST_REAL_KEY
        assert saved["model"] == "qwen2.5-vl-32b"
        assert saved["timeout_seconds"] == 35
        assert saved["max_retry_rounds"] == 2
        assert saved["price_input"] == 0.6
        assert saved["price_cached"] == 0.15
        assert saved["price_output"] == 1.8
        assert saved["reasoning_effort"] == "low"
        assert saved["is_custom"] is True
    finally:
        db.set_app_setting("custom_engine_presets", orig_custom)


def test_presets_post_ssrf_rejection():
    bad_urls = [
        "http://127.0.0.1:8000/v1",
        "http://localhost:8080",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/v1",
        "http://192.168.1.1/v1",
    ]
    for bad_url in bad_urls:
        resp = client.post("/api/admin/engine-presets", json={
            "key": "bad_preset",
            "label": "恶意地址模版",
            "base_url": bad_url,
            "api_key": "sk-test",
        })
        assert resp.status_code == 400
        msg = resp.json().get("msg", "")
        assert any(kw in msg for kw in ("安全校验未通过", "禁止", "回环", "私网", "元数据"))


def test_presets_post_validation_limits():
    # 超时超出限制
    resp = client.post("/api/admin/engine-presets", json={
        "key": "limit_test",
        "label": "超限模版",
        "base_url": "https://api.openai.com/v1",
        "timeout_seconds": 120,
    })
    assert resp.status_code == 400

    # 重跑上限超出限制
    resp = client.post("/api/admin/engine-presets", json={
        "key": "limit_test",
        "label": "超限模版",
        "base_url": "https://api.openai.com/v1",
        "max_retry_rounds": 15,
    })
    assert resp.status_code == 400

    # 价格为负数
    resp = client.post("/api/admin/engine-presets", json={
        "key": "limit_test",
        "label": "超限模版",
        "base_url": "https://api.openai.com/v1",
        "price_input": -0.5,
    })
    assert resp.status_code == 400


def test_presets_post_masked_key_preservation():
    orig_custom = db.get_app_setting("custom_engine_presets", "{}")
    try:
        # 先保存一个有明文密钥的模版
        db_data = {
            "vllm_existing": {
                "key": "vllm_existing",
                "label": "已存在模版",
                "base_url": "https://api.openai.com/v1",
                "api_key": TEST_REAL_KEY,
                "model": "qwen2.5-vl-72b",
            }
        }
        db.set_app_setting("custom_engine_presets", json.dumps(db_data))

        # 用户编辑该模版，带着掩码串提交更新
        resp = client.post("/api/admin/engine-presets", json={
            "key": "vllm_existing",
            "label": "已存在模版更新",
            "base_url": "https://api.openai.com/v1",
            "api_key": TEST_MASKED_KEY,
            "model": "qwen2.5-vl-72b-v2",
        })
        assert resp.status_code == 200

        # 真实密钥不得被掩码覆盖
        db_raw = db.get_app_setting("custom_engine_presets", "{}")
        updated = json.loads(db_raw)["vllm_existing"]
        assert updated["api_key"] == TEST_REAL_KEY
        assert updated["model"] == "qwen2.5-vl-72b-v2"
    finally:
        db.set_app_setting("custom_engine_presets", orig_custom)


def test_presets_delete_endpoint():
    orig_custom = db.get_app_setting("custom_engine_presets", "{}")
    try:
        custom_data = {
            "to_delete": {
                "key": "to_delete",
                "label": "待删除模版",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-delete-test",
            }
        }
        db.set_app_setting("custom_engine_presets", json.dumps(custom_data))

        # 删除存在的模版
        resp = client.delete("/api/admin/engine-presets/to_delete")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

        # 验证数据库中已被移除
        db_raw = db.get_app_setting("custom_engine_presets", "{}")
        assert "to_delete" not in json.loads(db_raw)

        # 再次删除同一 key 应返回 404
        resp404 = client.delete("/api/admin/engine-presets/to_delete")
        assert resp404.status_code == 404
        assert resp404.json()["status"] == "error"
    finally:
        db.set_app_setting("custom_engine_presets", orig_custom)


def test_put_engine_config_restores_key_from_preset():
    orig_cfg = db.get_engine_config()
    orig_custom = db.get_app_setting("custom_engine_presets", "{}")
    try:
        # 在 presets 中存储一个模版及其真实密钥
        preset_key = "vllm_preset_source"
        preset_real_key = "sk-preset-super-secret-key-abcdef987654"
        preset_masked = mask_secret_key(preset_real_key)

        custom_data = {
            preset_key: {
                "key": preset_key,
                "label": "模版源",
                "base_url": "https://api.openai.com/v1",
                "api_key": preset_real_key,
                "model": "qwen2.5-vl-72b",
            }
        }
        db.set_app_setting("custom_engine_presets", json.dumps(custom_data))

        # 当前引擎配置使用完全不同的 key
        seeded = db.get_engine_config()
        seeded.openai_rec_api_key = "sk-current-engine-real-key"
        db.set_engine_config(seeded)

        # 用户在前端卡片中从下拉菜单选择了该模版，提交时携带的是该模版的掩码串
        resp = client.put("/api/admin/engine-config", json={
            "openai_rec_base_url": "https://api.openai.com/v1",
            "openai_rec_api_key": preset_masked,
            "openai_rec_model": "qwen2.5-vl-72b",
        })
        assert resp.status_code == 200

        # 后端 _normalize_and_sync_engine_updates 必须智能匹配模版库，并将真实密钥恢复写入 DB
        new_cfg = db.get_engine_config()
        assert new_cfg.openai_rec_api_key == preset_real_key, \
            "提交模版回显的掩码串时，后端必须从 custom_engine_presets 中还原真实密钥入库"
        assert "****" not in new_cfg.openai_rec_api_key
    finally:
        db.set_engine_config(orig_cfg)
        db.set_app_setting("custom_engine_presets", orig_custom)

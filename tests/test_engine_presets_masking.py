# -*- coding: utf-8 -*-
"""engine-presets 密钥脱敏（P0-A / A2）测试集。

覆盖：
1. GET /api/admin/engine-presets 的 dashscope_key / siliconflow_key 经 mask_secret_key 脱敏，
   不再回传明文（审计确认的明文密钥外泄）。
2. 保存链路兼容掩码值：PUT /api/admin/engine-config 收到含 "****" 的密钥时，替换为库内既有
   真实密钥，绝不把掩码串写回库（前端会把预设回显的掩码原样回传）。
"""

import os
import sys

from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.main import app
from app.services.security_guard import mask_secret_key

client = TestClient(app, headers={"X-Role": "admin"})

_DASHSCOPE_KEY = "sk-dashscope-secret-1234567890"
_SILICONFLOW_KEY = "sk-siliconflow-secret-abcdef1234"


def test_engine_presets_masks_api_keys(monkeypatch):
    import json
    orig_custom = db.get_app_setting("custom_engine_presets", "{}")
    try:
        custom_data = {
            "my_custom": {
                "label": "My Custom Provider",
                "base_url": "https://api.example.com/v1",
                "api_key": _DASHSCOPE_KEY,
                "rec_model": "custom-vlm",
            }
        }
        db.set_app_setting("custom_engine_presets", json.dumps(custom_data))

        resp = client.get("/api/admin/engine-presets")
        assert resp.status_code == 200
        data = resp.json()["data"]

        # 验证彻底移除硬编码厂商预设
        assert "bailian" not in data
        assert "siliconflow" not in data
        assert "agnes" not in data

        # 自定义预设经 mask_secret_key 脱敏
        assert data["my_custom"]["api_key"] == mask_secret_key(_DASHSCOPE_KEY)
        # 明文不得出现在响应中
        assert "secret" not in data["my_custom"]["api_key"]
    finally:
        db.set_app_setting("custom_engine_presets", orig_custom)


def test_masked_api_key_is_not_written_back_to_db():
    orig_cfg = db.get_engine_config()
    try:
        seeded = db.get_engine_config()
        seeded.openai_rec_base_url = "https://93.184.216.34/v1"
        seeded.openai_rec_api_key = "sk-library-real-key-1234567890"
        seeded.openai_rec_model = "Qwen/Qwen3-VL-32B-Instruct"
        db.set_engine_config(seeded)

        # 前端把预设回显的掩码串原样回传（模拟用户未改密钥直接保存）
        resp = client.put("/api/admin/engine-config", json={
            "openai_rec_base_url": "https://93.184.216.34/v1",
            "openai_rec_api_key": "sk-lib****7890",
            "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
        })
        assert resp.status_code == 200

        cfg = db.get_engine_config()
        assert cfg.openai_rec_api_key == "sk-library-real-key-1234567890", \
            "掩码串必须被替换为库内真实密钥，不得写回库"
        assert "****" not in cfg.openai_rec_api_key
    finally:
        db.set_engine_config(orig_cfg)

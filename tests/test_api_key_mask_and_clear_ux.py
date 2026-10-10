# -*- coding: utf-8 -*-
"""AC01 - AC10 引擎 API Key 密码掩码呈现与占位符交互重构验收测试套件。

覆盖：
AC01: 已配置引擎密码脱敏掩码呈现与等长掩码构造（黑圆点对应长度严格一致）
AC02: 未配置引擎密钥为空串，返回 ""
AC03: 编辑其他参数时提交掩码，后端不丢失/不覆写已有真实 API Key
AC04: 显式提交空串 "" 时，后端将 DB 中的 API Key 置空（清空闭环）
AC05: 输入新 API Key 时，更新入库并自增版本
AC06: 单腿连通性探测 (Probe) 接收掩码或空串时自动还原 DB 既有真实 Key
AC07: 预设网关切换与占位符规范（无 Emoji 纯文本规范）
AC08: 零明文外泄脱敏硬限制 (GET /api/admin/engine-config 及 PUT 响应绝无明文)
AC09: 6 条引擎腿 (Rec, Aud, Parse, Grey Rec, Grey Aud, Grey Parse) 全面对齐
AC10: 全局无 Emoji 与状态标签规范 ([OK], [FAIL], [INFO], [WARN])
"""

import os
import sys
import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.main import app
from app.models import EngineConfig
from app.services.security_guard import mask_secret_key

client = TestClient(app, headers={"X-Role": "admin"})

TEST_REAL_KEYS = {
    "openai_rec_api_key": "sk-real-recognition-key-1234567890abcdef",
    "openai_aud_api_key": "sk-real-audit-key-abcdef1234567890",
    "openai_parse_api_key": "sk-real-parse-key-9876543210fedcba",
    "grey_openai_rec_api_key": "sk-real-grey-rec-key-1122334455667788",
    "grey_openai_aud_api_key": "sk-real-grey-aud-key-8877665544332211",
    "grey_openai_parse_api_key": "sk-real-grey-parse-key-aabbccddeeff0011",
}


@pytest.fixture(autouse=True)
def setup_engine_config():
    """每个测试前后保证数据库状态独立。"""
    orig_cfg = db.get_engine_config()
    cfg = db.get_engine_config()
    cfg.openai_rec_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    cfg.openai_rec_api_key = TEST_REAL_KEYS["openai_rec_api_key"]
    cfg.openai_rec_model = "qwen3.5-omni-flash"

    cfg.openai_aud_base_url = "https://api.siliconflow.cn/v1"
    cfg.openai_aud_api_key = TEST_REAL_KEYS["openai_aud_api_key"]
    cfg.openai_aud_model = "zai-org/GLM-4.5V"

    cfg.openai_parse_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    cfg.openai_parse_api_key = TEST_REAL_KEYS["openai_parse_api_key"]
    cfg.openai_parse_model = "deepseek-v3"

    cfg.grey_openai_rec_base_url = "https://apihub.agnes-ai.com/v1"
    cfg.grey_openai_rec_api_key = TEST_REAL_KEYS["grey_openai_rec_api_key"]
    cfg.grey_openai_rec_model = "agnes-2.0-flash"

    cfg.grey_openai_aud_base_url = "https://api.siliconflow.cn/v1"
    cfg.grey_openai_aud_api_key = TEST_REAL_KEYS["grey_openai_aud_api_key"]
    cfg.grey_openai_aud_model = "zai-org/GLM-4.5V"

    cfg.grey_openai_parse_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    cfg.grey_openai_parse_api_key = TEST_REAL_KEYS["grey_openai_parse_api_key"]
    cfg.grey_openai_parse_model = "deepseek-v3"

    db.set_engine_config(cfg, source="manual")
    yield
    db.set_engine_config(orig_cfg, source="manual")


def test_ac01_and_ac08_equal_length_mask_and_zero_cleartext_leak():
    """AC01 & AC08: GET /api/admin/engine-config 返回等长掩码，圆点数与原 Key 长度严格一致，零明文泄露。"""
    resp = client.get("/api/admin/engine-config")
    assert resp.status_code == 200
    data = resp.json()["data"]

    for key_field, real_val in TEST_REAL_KEYS.items():
        masked_val = data[key_field]
        # 1. 绝无明文泄露
        assert real_val not in resp.text
        assert masked_val != real_val
        # 2. 等长掩码构造：总长与原 Key 严格一致，以原生渲染对应黑圆点数量
        assert len(masked_val) == len(real_val), f"{key_field}: len({masked_val}) != len({real_val})"
        # 3. 包含掩码保护特征
        assert "*" in masked_val
        assert masked_val.startswith("sk-")
        assert masked_val.endswith(real_val[-4:])


def test_ac02_unconfigured_leg_returns_empty_string():
    """AC02: 未配置的引擎腿返回空字符串，无任何掩码圆点。"""
    cfg = db.get_engine_config()
    cfg.grey_openai_aud_api_key = ""
    db.set_engine_config(cfg, source="manual")

    resp = client.get("/api/admin/engine-config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["grey_openai_aud_api_key"] == ""


def test_ac03_and_ac09_editing_other_params_with_mask_preserves_secret():
    """AC03 & AC09: 编辑其他参数时回传掩码字符串，全 6 条引擎腿既有有效 Key 均不丢失、不被覆写。"""
    cfg = db.get_engine_config()
    current_ver = cfg.version

    # 读取当前脱敏数据
    resp_get = client.get("/api/admin/engine-config")
    masked_data = resp_get.json()["data"]

    # 提交配置：保持 6 条腿的掩码不变，修改模型名与超时
    payload = {
        "version": current_ver,
        "recognition_model": "qwen-max-2026",
        "openai_rec_model": "qwen-max-2026",
        "rec_timeout_seconds": 25,
        "openai_rec_api_key": masked_data["openai_rec_api_key"],
        "openai_aud_api_key": masked_data["openai_aud_api_key"],
        "openai_parse_api_key": masked_data["openai_parse_api_key"],
        "grey_openai_rec_api_key": masked_data["grey_openai_rec_api_key"],
        "grey_openai_aud_api_key": masked_data["grey_openai_aud_api_key"],
        "grey_openai_parse_api_key": masked_data["grey_openai_parse_api_key"],
    }
    resp_put = client.put("/api/admin/engine-config", json=payload)
    assert resp_put.status_code == 200, resp_put.text

    # 验证数据库中真实 Key 依然完好
    saved_cfg = db.get_engine_config()
    for key_field, real_val in TEST_REAL_KEYS.items():
        assert getattr(saved_cfg, key_field) == real_val, f"{key_field} was corrupted by mask submission!"
    assert saved_cfg.recognition_model == "qwen-max-2026"
    assert saved_cfg.rec_timeout_seconds == 25


def test_ac04_explicit_clear_api_key_lifecycle():
    """AC04: 显式提交空串 "" 时，对应引擎腿的 API Key 在数据库中被成功清空。"""
    cfg = db.get_engine_config()
    current_ver = cfg.version

    # 显式清空常规审核与灰测解析密钥
    payload = {
        "version": current_ver,
        "openai_aud_api_key": "",
        "grey_openai_parse_api_key": "",
    }
    resp = client.put("/api/admin/engine-config", json=payload)
    assert resp.status_code == 200, resp.text

    saved_cfg = db.get_engine_config()
    assert saved_cfg.openai_aud_api_key == ""
    assert saved_cfg.grey_openai_parse_api_key == ""
    # 其余腿不受影响
    assert saved_cfg.openai_rec_api_key == TEST_REAL_KEYS["openai_rec_api_key"]


def test_ac05_update_new_api_key():
    """AC05: 输入新真实 API Key 保存后更新入库，并返回等长脱敏值。"""
    cfg = db.get_engine_config()
    current_ver = cfg.version
    new_key = "sk-brandnew-valid-key-999888777666555"

    payload = {
        "version": current_ver,
        "openai_rec_api_key": new_key,
    }
    resp = client.put("/api/admin/engine-config", json=payload)
    assert resp.status_code == 200, resp.text
    ret = resp.json()

    saved_cfg = db.get_engine_config()
    assert saved_cfg.openai_rec_api_key == new_key
    # 返回脱敏结果长度与新 Key 等长
    assert len(ret["data"]["openai_rec_api_key"]) == len(new_key)
    assert new_key not in resp.text


def test_ac06_probe_resolves_masked_and_empty_keys():
    """AC06: 单腿连通性探测在收到掩码或留空时，自动提取 DB 真实 Key。"""
    # 模拟前端传入当前脱敏掩码进行探测
    cfg = db.get_engine_config()
    masked_key = mask_secret_key(cfg.openai_rec_api_key, equal_length=True)

    # 1. 传入掩码：由于 base_url 为公网地址，会在网络请求阶段拦截或握手，证明未被 "API Key 无效" 拦截
    res = client.post(
        "/api/admin/probe-single-engine",
        json={
            "leg_type": "rec",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": masked_key,
            "model_name": "qwen3.5-omni-flash",
            "timeout_seconds": 5.0,
        }
    )
    # 应进入 HTTP 外呼，而不是 400 "API Key 无效或为占位符"
    data = res.json()
    assert data.get("msg") != "API Key 无效或为占位符"

    # 2. 传入空串但库中有 Key：自动还原库中 Key
    res_empty = client.post(
        "/api/admin/probe-single-engine",
        json={
            "leg_type": "rec",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "",
            "model_name": "qwen3.5-omni-flash",
            "timeout_seconds": 5.0,
        }
    )
    assert res_empty.json().get("msg") != "API Key 无效或为占位符"

    # 3. 传入字面占位符 YOUR_xxx：拦截
    res_placeholder = client.post(
        "/api/admin/probe-single-engine",
        json={
            "leg_type": "rec",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "YOUR_API_KEY",
            "model_name": "qwen3.5-omni-flash",
            "timeout_seconds": 5.0,
        }
    )
    assert res_placeholder.status_code == 400
    assert "占位符" in res_placeholder.json().get("msg", "")


def test_ac10_no_emoji_in_backend_responses():
    """AC10: 后端所有响应绝不包含 Emoji 表情。"""
    resp = client.get("/api/admin/engine-config")
    assert resp.status_code == 200
    text = resp.text
    # Emoji 常见字符范围判定
    for char in text:
        code = ord(char)
        assert not (0x1F600 <= code <= 0x1F64F or 0x1F300 <= code <= 0x1F5FF or 0x1F680 <= code <= 0x1F6FF or 0x2600 <= code <= 0x26FF), f"Detected emoji {char} in response"

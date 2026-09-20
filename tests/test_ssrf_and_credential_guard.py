# -*- coding: utf-8 -*-
"""SSRF 阻断网关与敏感凭证脱敏测试集。

验证:
1. 私网 IP 与云厂商元数据 (169.254.169.254) 阻断
2. 合法公网 URL 放行与 allow_localhost 规则
3. DNS 解析检查与 DNS 重绑定阻断
4. 密钥脱敏 mask_secret_key
5. admin API (/api/admin/engine-config & /api/admin/test-engine-config) SSRF 阻断与密钥脱敏
6. llm._build 构造前的 SSRF 阻断
"""

import os
import sys
import socket
import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app.services.security_guard import validate_safe_external_url, mask_secret_key
from app import db
from app.models import EngineConfig, EngineKind
from app.main import app


def test_block_metadata_and_private_ips():
    dangerous_urls = [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/api",
        "http://127.0.0.2:8000/api",
        "http://localhost:15010/api",
        "http://10.0.1.5/v1",
        "http://192.168.1.1/v1",
        "http://172.16.0.10:8080/v1",
        "http://172.31.255.255/v1",
        "http://0.0.0.0:8000/v1",
        "http://[::1]:8000/v1",
        "http://[fc00::1]:8000/v1",
        "http://[fe80::1]:8000/v1",
        "http://[::ffff:127.0.0.1]:8000/v1",
        "http://[::ffff:169.254.169.254]:8000/v1",
        "http://100.64.0.1/v1",
    ]
    for url in dangerous_urls:
        valid, reason = validate_safe_external_url(url)
        assert not valid, f"Should block {url}"
        assert any(keyword in reason for keyword in ("私有", "元数据", "本地", "阻断", "私网", "受限")), f"Unexpected reason for {url}: {reason}"


def test_allow_safe_public_urls():
    safe_urls = [
        "https://api.siliconflow.cn/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://api.openai.com/v1",
    ]
    for url in safe_urls:
        valid, reason = validate_safe_external_url(url)
        assert valid, f"Should allow {url}, but got: {reason}"


def test_allow_localhost_flag():
    # 允许 localhost 场景（如单机自测开发）
    assert validate_safe_external_url("http://127.0.0.1:8000/v1", allow_localhost=True)[0] is True
    assert validate_safe_external_url("http://localhost:8000/v1", allow_localhost=True)[0] is True
    # 但云元数据和内部私网即使 allow_localhost=True 也必须严格阻断
    assert validate_safe_external_url("http://169.254.169.254/latest/meta-data/", allow_localhost=True)[0] is False
    assert validate_safe_external_url("http://10.0.0.1/v1", allow_localhost=True)[0] is False
    assert validate_safe_external_url("http://192.168.1.1/v1", allow_localhost=True)[0] is False


def test_dns_resolution_rebinding(monkeypatch):
    # 模拟域名解析到内网 IP
    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "rebind.evil.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
        if host == "metadata.evil.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    valid1, reason1 = validate_safe_external_url("http://rebind.evil.com/v1")
    assert not valid1
    assert "解析" in reason1 or "阻断" in reason1

    valid2, reason2 = validate_safe_external_url("http://metadata.evil.com/v1")
    assert not valid2

    valid3, reason3 = validate_safe_external_url("http://normal.safe.com/v1")
    assert valid3


def test_unresolved_domain_is_rejected_by_default(monkeypatch):
    """回归守卫：DNS 解析失败必须 fail-closed（拒绝），不得静默当成「安全」放行。

    历史缺陷：security_guard 曾用 `except socket.gaierror: pass` + `except Exception: pass`
    吞掉解析失败，随后落到 `return True, None`，使 SSRF 网关在无法确认目标 IP 时反而放行。
    """
    def raise_gaierror(host, port, *args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", raise_gaierror)

    valid, reason = validate_safe_external_url("http://offline.invalid/v1")
    assert valid is False, "解析失败时不得放行（fail-closed）"
    assert "无法解析" in reason


def test_unresolved_domain_allowed_only_when_explicitly_enabled():
    """显式开关：allow_unresolved=True 时，无法解析的域名才放行（离线/内网自测场景）。"""
    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        def raise_gaierror(host, port, *args, **kwargs):
            raise socket.gaierror(-2, "Name or service not known")

        mp.setattr(socket, "getaddrinfo", raise_gaierror)
        valid, reason = validate_safe_external_url(
            "http://offline.invalid/v1", allow_unresolved=True)
        assert valid is True
        assert reason is None


def test_mask_secret_key():
    assert mask_secret_key("") == ""
    assert mask_secret_key(None) == ""
    assert mask_secret_key("12345") == "******"
    assert mask_secret_key("12345678") == "******"
    assert mask_secret_key("123456789") == "123****6789"
    assert mask_secret_key("12345678901") == "123****8901"
    assert mask_secret_key("123456789012") == "123456****9012"
    assert mask_secret_key("sk-abcdef1234567890") == "sk-abc****7890"


def test_api_admin_set_engine_config_rejects_ssrf():
    client = TestClient(app, headers={"X-Role": "admin"})
    # 1. 识别引擎 base_url SSRF
    resp = client.put("/api/admin/engine-config", json={
        "recognition_engine": "openai",
        "openai_rec_base_url": "http://169.254.169.254/latest/meta-data",
        "openai_rec_api_key": "sk-realtestkey123456"
    })
    assert resp.status_code == 400
    data = resp.json()
    assert data["code"] == "ENGINE_CONFIG_INVALID"
    assert "安全校验未通过" in data["msg"] or "非法" in data["msg"] or "阻断" in data["msg"]

    # 2. 审核引擎 base_url SSRF
    resp_aud = client.put("/api/admin/engine-config", json={
        "openai_aud_base_url": "http://10.0.0.1/v1"
    })
    assert resp_aud.status_code == 400
    assert resp_aud.json()["code"] == "ENGINE_CONFIG_INVALID"

    # 3. 灰测识别引擎 base_url SSRF
    resp_grey = client.put("/api/admin/engine-config", json={
        "grey_openai_rec_base_url": "http://127.0.0.1:8000/v1"
    })
    assert resp_grey.status_code == 400
    assert resp_grey.json()["code"] == "ENGINE_CONFIG_INVALID"


def test_api_admin_quick_test_engine_rejects_ssrf():
    client = TestClient(app, headers={"X-Role": "admin"})
    resp = client.post("/api/admin/test-engine-config", json={
        "recognition_engine": "openai",
        "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct",
        "openai_rec_base_url": "http://169.254.169.254/latest/meta-data",
        "openai_rec_api_key": "sk-realtestkey123456"
    })
    assert resp.status_code == 400
    assert "安全阻断" in resp.json()["msg"] or "非法" in resp.json()["msg"] or "未通过" in resp.json()["msg"]


def test_api_admin_get_engine_config_masks_keys():
    client = TestClient(app, headers={"X-Role": "admin"})
    orig_cfg = db.get_engine_config()
    try:
        # 预设真实 key 到 DB
        cfg = db.get_engine_config()
        cfg.openai_rec_api_key = "sk-supersecretkey12345678"
        cfg.openai_aud_api_key = "sk-audsecretkey87654321"
        db.set_engine_config(cfg)

        # 通过 API 获取应脱敏
        resp = client.get("/api/admin/engine-config")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["openai_rec_api_key"] == "sk-sup****5678"
        assert data["openai_aud_api_key"] == "sk-aud****4321"

        # 内部直接读取 DB 仍然是明文，不破坏后端任务调用
        internal_cfg = db.get_engine_config()
        assert internal_cfg.openai_rec_api_key == "sk-supersecretkey12345678"
        assert internal_cfg.openai_aud_api_key == "sk-audsecretkey87654321"
    finally:
        db.set_engine_config(orig_cfg)


def test_llm_build_blocks_ssrf():
    from app import llm

    cfg = db.get_engine_config()
    cfg.openai_rec_base_url = "http://169.254.169.254/latest/meta-data"
    cfg.openai_rec_api_key = "sk-validkey123456"

    with pytest.raises(ValueError) as exc_info:
        llm._build("openai", "Qwen/Qwen3-VL-32B-Instruct", cfg, side="rec")
    assert "安全阻断: 非法外部 API Base URL" in str(exc_info.value)

# -*- coding: utf-8 -*-
"""P2-4 外呼点收口：_openai_gateway_healthy 必须过统一 SSRF 网关。

背景：`demo/app/db.py` 的 `_openai_gateway_healthy` 曾直接用
`urllib.request.urlopen(base + "/models")` 外呼，未过统一 SSRF 网关
`app.services.security_guard.validate_safe_external_url`。

覆盖：
  1. 内网 / 云元数据形态的 base_url 不触发任何实际外呼（urlopen 零调用），
     直接判为「不健康」并 [WARN] 留痕；
  2. 该函数确实调用了统一 SSRF 网关（显式命中一次校验）；
  3. 合法公网 base_url 仍照常外呼（网关不误伤正常健康检查）。
"""

import logging
import os
import sys

import pytest

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db


@pytest.mark.parametrize("base_url", [
    "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.1/v1",
    "http://192.168.1.10/v1",
    "http://127.0.0.1:8000/v1",
    "http://localhost:15010/v1",
])
def test_internal_base_url_never_calls_out(monkeypatch, caplog, base_url):
    called = []

    def _fake_urlopen(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("内网/元数据 base_url 不得触发实际外呼")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        healthy = db._openai_gateway_healthy("sk-testkey123456", base_url)
    assert healthy is False, "未通过 SSRF 校验必须判为不健康"
    assert called == [], "内网/元数据 base_url 触发了实际外呼（urlopen 被调用）"
    warns = [rec.getMessage() for rec in caplog.records if "[WARN]" in rec.getMessage()]
    assert warns, "必须留下 [WARN] 日志（不得静默）"


def test_guard_is_consulted(monkeypatch):
    """显式证明：外呼前调用统一 SSRF 网关（作用于传入的 base_url）。"""
    from app.services import security_guard

    seen = []

    def _fake_validate(url, *args, **kwargs):
        seen.append(url)
        return False, "blocked-test"

    monkeypatch.setattr(security_guard, "validate_safe_external_url", _fake_validate)
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("校验未通过不得外呼")))

    assert db._openai_gateway_healthy("sk-testkey123456", "http://example.invalid/v1") is False
    assert seen == ["http://example.invalid/v1"]


def test_public_base_url_still_probes(monkeypatch):
    called = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, timeout=None):
        called.append(req.full_url)
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    healthy = db._openai_gateway_healthy(
        "sk-testkey123456", "https://api.siliconflow.cn/v1")
    assert healthy is True
    assert called == ["https://api.siliconflow.cn/v1/models"]

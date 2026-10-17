# -*- coding: utf-8 -*-
"""UAT-010：生产会话缺少可见退出登录入口 — 开发专项。

验收映射（DEV 可自动化部分）：
1) 三真实 cookie 角色（admin/owner/staff）分别登录 → POST logout → 受保护 API 401；
   Cookie 被清除；令牌吊销。
2) 前端不再把 Bearer 写入 storage（静态：getAuthToken 恒 null；登录成功路径清键）。
3) 取消/网络失败不假退出（静态：requestAuthLogout 走确认；performAuthLogout
   仅在 HTTP 成功后清 AuthState）。
负向：demo 角色切换不是生产注销；logout 不扩大写权限。
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="uat010_logout_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ["DEMO_TOKEN_SECRET"] = "uat010-dev-secret-not-default"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

_REPO = os.path.abspath(os.path.join(_DEMO_DIR, ".."))
_COMMON_JS = os.path.join(_DEMO_DIR, "static", "js", "common.js")
_INDEX_HTML = os.path.join(_DEMO_DIR, "templates", "index.html")
_API_AUTH = os.path.join(_DEMO_DIR, "app", "api_auth.py")


_ROLES = (
    ("admin@demo.hk", "admin123", "admin"),
    ("boss@demo.hk", "boss123", "owner"),
    ("staff@demo.hk", "staff123", "staff"),
)


@pytest.fixture()
def auth_client(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("DEMO_TOKEN_SECRET", "uat010-dev-secret-not-default")
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from app import auth as auth_mod
    # reload auth_enabled cache via env already set
    assert auth_mod.auth_enabled() is True
    return TestClient(fastapi_app)


def test_three_cookie_roles_logout_then_protected_api_401(auth_client):
    from app import auth as auth_mod
    for email, password, role in _ROLES:
        c = auth_client
        # fresh client cookies per role to avoid cross-talk
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        c = TestClient(fastapi_app)
        login = c.post("/api/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, (email, login.text)
        body = login.json()
        assert body.get("status") == "success"
        assert body["account"]["role"] == role
        token = body["token"]
        assert c.cookies.get("receipt_session")
        me = c.get("/api/auth/me").json()
        assert me.get("auth_enabled") is True
        assert me.get("account", {}).get("role") == role
        assert me.get("identity_source") == "cookie"
        # protected before logout
        assert c.get("/api/receipts").status_code == 200
        out = c.post(
            "/api/auth/logout",
            headers={"X-Requested-With": "fetch"},
        )
        assert out.status_code == 200, out.text
        assert out.json().get("status") == "success"
        # cookie cleared on response
        sc = out.headers.get("set-cookie", "")
        assert "receipt_session" in sc
        # Token revoked
        assert auth_mod.parse_token(token) is None
        # New client pretending to reuse old cookie → 401
        c2 = TestClient(fastapi_app)
        c2.cookies.set("receipt_session", token)
        assert c2.get("/api/receipts").status_code == 401
        # Original client after logout (cookie deleted by TestClient on Set-Cookie)
        assert c.get("/api/receipts").status_code == 401


def test_logout_idempotent_and_does_not_expand_write_perms(auth_client):
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    c = TestClient(fastapi_app)
    anon = c.post("/api/auth/logout", headers={"X-Requested-With": "fetch"})
    assert anon.status_code == 200
    assert anon.json().get("status") == "success"
    assert "token" not in anon.json()
    assert c.get("/api/receipts").status_code == 401
    login = c.post(
        "/api/auth/login",
        json={"email": "staff@demo.hk", "password": "staff123"},
    )
    assert login.status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["account"]["role"] == "staff"
    # staff still staff after using logout API earlier; role not elevated
    assert me["account"]["email"] == "staff@demo.hk"
    out = c.post("/api/auth/logout", headers={"X-Requested-With": "fetch"})
    assert out.status_code == 200
    assert "token" not in out.json()

def test_ui_has_visible_logout_entry_and_safe_handlers():
    html = open(_INDEX_HTML, encoding="utf-8").read()
    assert 'id="authLogoutBtn"' in html
    assert "退出登录" in html
    assert 'id="authLogoutWrap"' in html
    # demo role switch remains a separate control (not the logout button)
    assert 'id="demoRoleSelect"' in html
    assert 'id="demoRoleSwitch"' in html

    js = open(_COMMON_JS, encoding="utf-8").read()
    assert "function requestAuthLogout" in js
    assert "function performAuthLogout" in js
    assert "function syncAuthChrome" in js
    assert "function clearClientAuthCredentials" in js
    # cancel must not fake-logout
    assert "onCancel: function () { /* 取消：保持会话，不假退出 */ }" in js
    # network / non-success must not clear AuthState before success branch
    assert "网络异常，未能退出" in js
    assert "AuthState.account = null;" in js
    # success path clears credentials then shows login
    assert "clearClientAuthCredentials();" in js
    assert "showLoginPanel('已退出登录')" in js
    # getAuthToken never returns stored bearer (S4-08)
    assert re.search(r"function getAuthToken\(\)\s*\{\s*return null;", js)
    # demo role switch is hidden when auth enabled — not treated as logout
    assert "demoRoleSwitch" in js
    assert "AuthState.enabled ? 'none'" in js

    api = open(_API_AUTH, encoding="utf-8").read()
    assert "clear_session_cookie" in api
    assert "revoke_jti" in api
    assert "不扩大任何角色写权限" in api


def test_logout_does_not_store_credentials_in_response_body_beyond_msg(auth_client):
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    c = TestClient(fastapi_app)
    login = c.post(
        "/api/auth/login",
        json={"email": "boss@demo.hk", "password": "boss123"},
    )
    assert login.status_code == 200
    out = c.post("/api/auth/logout", headers={"X-Requested-With": "fetch"})
    body = out.json()
    assert "token" not in body
    assert "password" not in body
    assert body.get("status") == "success"

# -*- coding: utf-8 -*-
"""生产态鉴权强制与真实开关回显（P0-A / A1）测试集。

覆盖：
1. 生产态（AUTH_ENABLED=1）下 X-Role 头不再获得身份 → 401。
2. 生产态伪造签名 token → 401；issue_token 合法签发 → 200 且角色来自 token。
3. 弱密钥 + 生产态 → enforce_production_secret() 抛 RuntimeError；强密钥不抛；
   开发态（AUTH_ENABLED=0）恒定不抛（演示「只告警不拒绝启动」）。
4. 开发态 X-Role 免密通道回归：确保演示角色切换通道没被改坏。
5. 生产态 X-Email 不再影响审计责任人（api_admin 里 who=account.get("email") 的来源）。
6. /api/auth/me 的 auth_enabled 为真实开关值（AUTH_ENABLED=0 时应为 false）。

写法约束（见 tests/conftest.py 头部）：该文件已把全局 AUTH_ENABLED 锁为 "0"，
验证「鉴权开启」行为一律在用例内 monkeypatch app.auth 的判定函数，不改全局开关，
从而不影响其余用例的离线全绿。
"""

import os
import sys

import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import auth, db
from app.main import app


def _enable_production(monkeypatch):
    """把 app.auth.auth_enabled 判定函数切到生产态（不触碰全局 AUTH_ENABLED 开关）。"""
    monkeypatch.setattr(auth, "auth_enabled", lambda: True)


def _request(headers):
    """构造一个最小 Starlette Request，用于直接单测 resolve_account。"""
    from starlette.requests import Request
    raw = [(str(k).lower().encode(), str(v).encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "method": "GET", "path": "/"})


# -------------------------------------------------------------
# 1. 生产态：X-Role 头不再获得身份
# -------------------------------------------------------------
def test_production_ignores_x_role_header(monkeypatch):
    _enable_production(monkeypatch)
    client = TestClient(app)
    resp = client.get("/api/admin/engine-config", headers={"X-Role": "admin"})
    assert resp.status_code == 401, "生产态不得凭 X-Role 声明获得身份"


def test_resolve_account_ignores_declared_headers_in_production(monkeypatch):
    _enable_production(monkeypatch)
    acct = auth.resolve_account(_request({"X-Role": "admin", "X-Email": "attacker@evil.com",
                                          "X-Tenant-Id": "tenantB"}))
    # 生产态：声明的角色/邮箱/租户一律不采信 → 匿名（tenant 也必须是 None，
    # 否则「匿名却带租户」会让下游把请求当成本租用户处理）
    assert acct == {"email": None, "role": None, "tenant": None, "source": None}


# -------------------------------------------------------------
# 2. 生产态：Bearer 验签是唯一身份来源
# -------------------------------------------------------------
def test_production_rejects_forged_token_and_accepts_issued_token(monkeypatch):
    _enable_production(monkeypatch)
    client = TestClient(app)

    forged = "admin@demo.hk.admin.9999999999.deadbeefdeadbeef"
    r_forged = client.get("/api/admin/engine-config",
                          headers={"Authorization": "Bearer " + forged})
    assert r_forged.status_code == 401, "伪造签名 token 必须 401"

    token = auth.issue_token("admin@demo.hk", "admin")
    r_ok = client.get("/api/admin/engine-config",
                      headers={"Authorization": "Bearer " + token})
    assert r_ok.status_code == 200, "合法签发 token 必须放行"

    # 角色来自 token（而非任意头）：staff token 访问 admin 端点 → 403（已认证/权限不足）
    staff_token = auth.issue_token("staff@demo.hk", "staff")
    r_staff = client.get("/api/admin/engine-config",
                         headers={"Authorization": "Bearer " + staff_token,
                                  "X-Role": "admin"})
    assert r_staff.status_code == 403, "角色必须取自 token，X-Role 不得提权"

    me = client.get("/api/auth/me", headers={"Authorization": "Bearer " + token}).json()
    assert me["auth_enabled"] is True
    assert me["identity_source"] == "token"
    assert me["account"] == {"email": "admin@demo.hk", "role": "admin"}


# -------------------------------------------------------------
# 3. 弱密钥强制（enforce_production_secret）
# -------------------------------------------------------------
def test_enforce_production_secret_raises_on_weak_secret(monkeypatch):
    _enable_production(monkeypatch)
    monkeypatch.delenv("DEMO_TOKEN_SECRET", raising=False)
    assert auth.token_secret_is_weak() is True
    with pytest.raises(RuntimeError) as exc_info:
        auth.enforce_production_secret()
    assert "DEMO_TOKEN_SECRET" in str(exc_info.value)


def test_enforce_production_secret_allows_strong_secret(monkeypatch):
    _enable_production(monkeypatch)
    monkeypatch.setenv("DEMO_TOKEN_SECRET", "a" * 64)
    assert auth.token_secret_is_weak() is False
    auth.enforce_production_secret()  # 不抛


def test_enforce_production_secret_is_noop_when_auth_disabled(monkeypatch):
    # 开发/演示态（全局 AUTH_ENABLED=0）：弱密钥不拒绝启动，维持既有「只告警」语义
    assert auth.auth_enabled() is False
    monkeypatch.delenv("DEMO_TOKEN_SECRET", raising=False)
    assert auth.token_secret_is_weak() is True
    auth.enforce_production_secret()  # 不抛


# -------------------------------------------------------------
# 4. 开发态：X-Role 免密通道回归（演示体验不受影响）
# -------------------------------------------------------------
def test_dev_mode_x_role_channel_regression():
    assert auth.auth_enabled() is False
    client = TestClient(app)

    me = client.get("/api/auth/me", headers={"X-Role": "owner"}).json()
    assert me["auth_enabled"] is False, "AUTH_ENABLED=0 时 auth_enabled 必须如实回显 false"
    assert me["identity_source"] == "header"
    assert me["account"] == {"email": "owner@demo.hk", "role": "owner"}

    # 角色头免密切换：admin 直通、staff 越权 403、无身份 401
    assert client.get("/api/admin/engine-config", headers={"X-Role": "admin"}).status_code == 200
    assert client.get("/api/admin/engine-config", headers={"X-Role": "staff"}).status_code == 403
    assert client.get("/api/admin/engine-config").status_code == 401

    # 开发态 X-Email 仍被采信（演示用，保持既有行为逐字节不变）；
    # tenant 在开发态取自 X-Tenant-Id（缺省 default），与既有 header 通道口径一致。
    acct = auth.resolve_account(_request({"X-Role": "admin", "X-Email": "boss@demo.hk"}))
    # B1：X-Email 不再被采信，操作人固定为「角色@demo.hk」的演示身份
    assert acct == {"email": "admin@demo.hk", "role": "admin",
                    "tenant": "default", "source": "header"}


# -------------------------------------------------------------
# 5. 生产态：X-Email 不再影响审计责任人
# -------------------------------------------------------------
def test_production_x_email_cannot_forge_audit_who(monkeypatch):
    _enable_production(monkeypatch)
    client = TestClient(app)
    token = auth.issue_token("admin@demo.hk", "admin")

    resp = client.put(
        "/api/admin/engine-config",
        headers={"Authorization": "Bearer " + token,
                 "X-Role": "boss", "X-Email": "boss@demo.hk"},
        json={"openai_rec_base_url": "https://93.184.216.34/v1",
              "openai_rec_api_key": "sk-xemail-test-key12345678",
              "openai_rec_model": "Qwen/Qwen3-VL-32B-Instruct"},
    )
    assert resp.status_code == 200

    entries = [e for e in db.read_system_audit_log() if e.get("action") == "set_engine_config"]
    assert entries, "保存配置应写入系统审计"
    assert entries[-1]["who"] == "admin@demo.hk", "审计责任人必须取自 token，而非 X-Email"


# -------------------------------------------------------------
# 6. D21：弱密钥下签名保护的「假象」必须对运维可见
# -------------------------------------------------------------
def test_health_exposes_signature_secret_strength(monkeypatch):
    """AUTH_ENABLED=0（本地演示）也必须在 /api/health 如实暴露密钥强度。

    why：该模式下签名密钥是仓库公开常量时，预览/凭证签名只防误撞、不防构造；
    仅靠一行启动 warning 无法让运维一眼看到「当前签名不构成保护」。health 标志
    在两种密钥形态下取值必须准确（弱 → true，强 → false），且端点仍照常出图
    （不因暴露标志而拒绝启动或降级）。
    """
    client = TestClient(app)

    monkeypatch.delenv("DEMO_TOKEN_SECRET", raising=False)
    weak = client.get("/api/health", headers={"X-Role": "admin"})
    assert weak.status_code == 200, weak.text
    assert weak.json()["signature_secret_weak"] is True

    monkeypatch.setenv("DEMO_TOKEN_SECRET", "a" * 64)
    strong = client.get("/api/health", headers={"X-Role": "admin"})
    assert strong.status_code == 200, strong.text
    assert strong.json()["signature_secret_weak"] is False


def test_health_flag_is_false_when_secret_configured(monkeypatch):
    """显式配置了非默认密钥 → 标志恒 False（与 token_secret_is_weak 同口径）。"""
    client = TestClient(app)
    monkeypatch.setenv("DEMO_TOKEN_SECRET", "s3cret-" + "x" * 40)
    body = client.get("/api/health", headers={"X-Role": "admin"}).json()
    assert body["signature_secret_weak"] is False
    assert auth.token_secret_is_weak() is False


# -------------------------------------------------------------
# 7. D23：/api/* 早鉴权中间件——消除 422 探测面
# -------------------------------------------------------------
# 受保护端点 + 非法请求体：匿名时必须在路由分发前被中间件拦成 401（而非因请求体
# 校验失败返回 422）。422 会暴露「该路径存在且需要哪些参数」，可被用于端点探测。
_PROTECTED_WITH_BODY = [
    ("post", "/api/dishes", {"json": {"garbage": 1}}),
    ("post", "/api/save_edited", {"json": {"bad": 1}}),
    ("put", "/api/admin/engine-config", {"json": {"bad": 1}}),
    ("post", "/api/receipt/1/approve", {"json": {"x": 1}}),
    ("post", "/api/suppliers", {"json": {"bad": 1}}),
]


def test_early_auth_dev_anonymous_invalid_body_is_401_not_422():
    """开发态（AUTH_ENABLED=0）：匿名 + 非法请求体 → 401，且绝不为 422。"""
    assert auth.auth_enabled() is False
    client = TestClient(app)
    for method, url, kwargs in _PROTECTED_WITH_BODY:
        resp = getattr(client, method)(url, **kwargs)
        assert resp.status_code == 401, (
            f"{method.upper()} {url} 匿名非法体应 401（消除 422 探测面），实际 {resp.status_code}")
        assert resp.status_code != 422


def test_early_auth_production_anonymous_invalid_body_is_401_not_422(monkeypatch):
    """生产态（AUTH_ENABLED=1）：匿名 + 非法请求体 → 401；仅 X-Role 头也拿不到身份。"""
    _enable_production(monkeypatch)
    client = TestClient(app)
    for method, url, kwargs in _PROTECTED_WITH_BODY:
        resp = getattr(client, method)(url, **kwargs)
        assert resp.status_code == 401, (
            f"{method.upper()} {url} 生产态匿名非法体应 401，实际 {resp.status_code}")
        # X-Role 声明在生产态不受信，仍是 401
        kw2 = dict(kwargs)
        kw2["headers"] = {"X-Role": "admin"}
        resp2 = getattr(client, method)(url, **kw2)
        assert resp2.status_code == 401, "生产态 X-Role 声明不得获得身份"


def test_early_auth_dev_identity_reaches_endpoint(monkeypatch):
    """开发态：带 X-Role 身份时中间件放行，请求体校验交回端点（不再被 401 短路）。"""
    client = TestClient(app)
    resp = client.post("/api/dishes", json={"garbage": 1}, headers={"X-Role": "owner"})
    assert resp.status_code == 422, "有身份时应到达端点、由其自身的参数校验给出 422"
    # 越权仍是 403（中间件只负责「先有身份」，角色阈值不变）
    assert client.get("/api/admin/engine-config", headers={"X-Role": "staff"}).status_code == 403


def test_early_auth_production_valid_token_reaches_endpoint(monkeypatch):
    """生产态：合法 Bearer 令牌放行中间件，请求体校验交回端点。"""
    _enable_production(monkeypatch)
    client = TestClient(app)
    token = auth.issue_token("admin@demo.hk", "admin")
    resp = client.put(
        "/api/admin/engine-config",
        headers={"Authorization": "Bearer " + token},
        json={"bad": 1},
    )
    assert resp.status_code != 401, "合法令牌不得被早鉴权中间件拒绝"
    assert resp.status_code in (200, 422)


def test_early_auth_whitelist_endpoints_stay_anonymous():
    """白名单端点匿名仍可用；非 /api/ 路径不受影响。"""
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["account"] is None
    # 登录端点匿名可达（凭据错误由其自身逻辑给出 401，而非中间件的未认证 401）
    login = client.post("/api/auth/login", json={"email": "x@y.z", "password": "bad"})
    assert login.status_code == 401
    assert "邮箱或密码错误" in login.text
    # 浏览器原生取图端点：匿名不被中间件拦成 401，而是走签名校验的 403
    img = client.get("/api/receipt/999999/image")
    assert img.status_code == 403
    assert img.json()["code"] == "IMAGE_SIGNATURE_INVALID"
    voucher = client.get("/api/payment/voucher/nope.jpg")
    assert voucher.status_code == 403
    assert voucher.json()["code"] == "VOUCHER_SIGNATURE_INVALID"
    # 非 /api/ 路径不经早鉴权
    assert client.get("/").status_code == 200


def test_early_auth_whitelist_predicate_matches_spec():
    """白名单判定的路径形态与本任务约定逐条对齐（含边界：仅 /image 子路径放行）。"""
    assert auth.is_early_auth_whitelisted("/api/auth/login")
    assert auth.is_early_auth_whitelisted("/api/auth/me")
    assert auth.is_early_auth_whitelisted("/api/health")
    assert auth.is_early_auth_whitelisted("/api/receipt/123/image")
    assert auth.is_early_auth_whitelisted("/api/payment/voucher/foo.jpg")
    # 受保护路径不得误入白名单
    assert not auth.is_early_auth_whitelisted("/api/receipt/123")
    assert not auth.is_early_auth_whitelisted("/api/receipts")
    assert not auth.is_early_auth_whitelisted("/api/receipt/123/image/extra")
    assert not auth.is_early_auth_whitelisted("/api/admin/engine-config")
    # 穿越/编码形态须放行到端点自身的收敛逻辑（而非被中间件拦成 401）
    assert auth.is_early_auth_whitelisted("/api/payment/voucher/../outside.jpg")


# -------------------------------------------------------------
# 8. 生产启动核验的「环境门控」（UAT BLOCKER-1 回归）
# -------------------------------------------------------------
# 契约：弱预置口令只在「显式生产环境」拒绝启动；开发/演示/面试展示路径（缺省 development）
# 放行。弱 DEMO_TOKEN_SECRET 的强制项不随环境放宽（任何环境开启鉴权都拒绝）。
def _strong_secret(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN_SECRET", "a" * 64)


def test_checklist_rejects_weak_preset_passwords_in_production(monkeypatch):
    """ENV=production + 弱预置口令 → 仍 fail-closed（不得被环境门控误放行）。"""
    _enable_production(monkeypatch)
    _strong_secret(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    assert auth.is_production_env() is True
    with pytest.raises(RuntimeError) as exc_info:
        auth.enforce_production_checklist()
    assert "弱口令" in str(exc_info.value)


def test_checklist_recognizes_prod_alias(monkeypatch):
    """ENV=prod 视同生产（与 main.py 的 DEV_MODE 判定同一口径）。"""
    _enable_production(monkeypatch)
    _strong_secret(monkeypatch)
    monkeypatch.setenv("ENV", "prod")
    assert auth.is_production_env() is True
    with pytest.raises(RuntimeError):
        auth.enforce_production_checklist()


def test_checklist_allows_weak_preset_passwords_in_development(monkeypatch):
    """ENV=development + 弱预置口令 → 放行（面试展示/boot 路径依赖预置账号登录）。"""
    _enable_production(monkeypatch)
    _strong_secret(monkeypatch)
    monkeypatch.setenv("ENV", "development")
    assert auth.is_production_env() is False
    auth.enforce_production_checklist()  # 不抛


def test_checklist_default_env_is_development(monkeypatch):
    """ENV/APP_ENV 均未设置时缺省为 development → 放行（interview-show.sh 的实际形态）。"""
    _enable_production(monkeypatch)
    _strong_secret(monkeypatch)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    assert auth.env_name() == "development"
    assert auth.is_production_env() is False
    auth.enforce_production_checklist()  # 不抛


def test_checklist_app_env_fallback(monkeypatch):
    """ENV 未设置时回落到 APP_ENV（与 main.py 的读取顺序一致）。"""
    _enable_production(monkeypatch)
    _strong_secret(monkeypatch)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert auth.is_production_env() is True
    with pytest.raises(RuntimeError):
        auth.enforce_production_checklist()


def test_checklist_still_requires_strong_secret_in_development(monkeypatch):
    """强密钥要求不随环境放宽：开发环境开启鉴权 + 弱密钥 → 仍拒绝启动。"""
    _enable_production(monkeypatch)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("DEMO_TOKEN_SECRET", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        auth.enforce_production_checklist()
    assert "DEMO_TOKEN_SECRET" in str(exc_info.value)

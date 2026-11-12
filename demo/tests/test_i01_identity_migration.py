# -*- coding: utf-8 -*-
"""I01 身份迁移门禁：v8 迁移四场景 + AC1/AC2/AC3/AC5（I01 规格 v1 第 3 节）。

红线约束（沿用 fix wave 套件口径）：
1. 全局严禁 Emoji；状态用 [OK] / [FAIL]。
2. 全部落在 tempfile 沙箱 DB；不触 live DB / live 向量库。
3. AUTH_ENABLED=1 真实 token 链路；IDENTITY_V2_ENABLED 逐测试切换。
"""

import os
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_i01_identity_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_i01.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "i01-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db, identity_store  # noqa: E402
from app.main import app  # noqa: E402

db._make_engine()


def _hdr(email, role, tenant="default"):
    return {"Authorization": "Bearer " + auth.issue_token(email, role, tenant)}


@pytest.fixture()
def v2_on(monkeypatch):
    monkeypatch.setenv("IDENTITY_V2_ENABLED", "1")
    yield
    monkeypatch.delenv("IDENTITY_V2_ENABLED", raising=False)


def _approve(client, token_hdr):
    return client.post("/api/receipt/999999/approve", headers=token_hdr, json={"version": 1})


# -------------------------------------------------------------
# v8 迁移门禁（P04 同款四场景，压缩为两测试）
# -------------------------------------------------------------
def test_v8_migration_empty_and_repeat():
    engine = db._engine
    with engine.connect() as conn:
        for t in ("accounts", "tenant_memberships", "platform_roles",
                  "billing_memberships", "session_auth_version", "invite_tokens"):
            assert conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='%s'" % t)).fetchone(), t
        n1 = conn.execute(text("SELECT COUNT(*) FROM schema_version")).scalar()
    db._run_ordered_migrations(engine)
    db._run_ordered_migrations(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM schema_version")).scalar() == n1


def test_v8_migration_failure_recovery(monkeypatch):
    original = list(db.SCHEMA_MIGRATIONS)
    def _boom(_engine):
        raise RuntimeError("I01 synthetic migration failure")
    monkeypatch.setattr(db, "SCHEMA_MIGRATIONS",
                        [(v, n, (_boom if v == 8 else fn)) for v, n, fn in original])
    fresh = os.path.join(_TMP, "i01_failrec.db")
    old_path = db.DB_PATH
    db.DB_PATH = fresh
    try:
        # _make_engine 内部会跑有序迁移：v8 注入失败必须中止且不留版本记录
        with pytest.raises(RuntimeError):
            db._make_engine()
        assert 8 not in {r[0] for r in db._engine.connect().execute(
            text("SELECT version FROM schema_version")).fetchall()}
        monkeypatch.setattr(db, "SCHEMA_MIGRATIONS", original)
        db._run_ordered_migrations(db._engine)
        assert 8 in {r[0] for r in db._engine.connect().execute(
            text("SELECT version FROM schema_version")).fetchall()}
    finally:
        db.DB_PATH = old_path
        monkeypatch.setattr(db, "SCHEMA_MIGRATIONS", original)
        db._make_engine()  # 恢复全局 engine 指针，避免污染后续测试


# -------------------------------------------------------------
# AC1 + AC5：admin 无商户业务授权、不自动建 membership；rank 反例翻转
# -------------------------------------------------------------
def test_ac1_admin_no_business_grant(v2_on):
    client = TestClient(app)
    r = _approve(client, _hdr("admin@demo.hk", "admin"))
    assert r.status_code == 403, r.status_code  # 旧 rank 行为是 404（RBAC 通过），v2 翻转为 403
    with db._engine.connect() as conn:
        n_memberships = conn.execute(text("SELECT COUNT(*) FROM tenant_memberships")).fetchone()[0]
    assert n_memberships == 0  # AC1：判定过程不隐式开户/授权，admin 未自动获得任何 membership


def test_ac5_legacy_flag_off_keeps_rank(monkeypatch):
    monkeypatch.delenv("IDENTITY_V2_ENABLED", raising=False)
    client = TestClient(app)
    r = _approve(client, _hdr("admin@demo.hk", "admin"))
    assert r.status_code == 404, r.status_code  # 开关关：rank 继承放行 RBAC，随后 404（不存在的单）
    # staff 仍被 rank 拒（403）
    r2 = _approve(client, _hdr("staff@demo.hk", "staff"))
    assert r2.status_code == 403, r2.status_code


# -------------------------------------------------------------
# membership 判定：staff 账号获得 owner membership 后通过 owner 门
# -------------------------------------------------------------
def test_membership_resolution_passes_owner_gate(v2_on):
    client = TestClient(app)
    # 未授权：staff token → 403
    assert _approve(client, _hdr("staff@demo.hk", "staff")).status_code == 403
    identity_store.grant_tenant_membership("staff@demo.hk", "default", "owner")
    # 授权后通过 owner 门（到达 404：单不存在）
    assert _approve(client, _hdr("staff@demo.hk", "staff")).status_code == 404
    # 错租户 membership 不跨域
    identity_store.grant_tenant_membership("staff@demo.hk", "tenant_other", "owner")
    assert _approve(client, _hdr("staff@demo.hk", "staff")).status_code == 404  # default 仍在


# -------------------------------------------------------------
# AC2：一次性邀请绑定
# -------------------------------------------------------------
def test_ac2_invite_one_time_binding(v2_on):
    raw = identity_store.issue_invite_token("tenant_a", "owner", ttl_seconds=60)
    out = identity_store.consume_invite_token(raw, "newowner@example.test")
    assert out == {"tenant_id": "tenant_a", "role": "owner"}
    memberships = identity_store.resolve_memberships("newowner@example.test")
    assert memberships["tenant_roles"] == {"tenant_a": "owner"}
    with pytest.raises(ValueError):
        identity_store.consume_invite_token(raw, "other@example.test")  # 一次性：复用拒绝
    with pytest.raises(ValueError):
        identity_store.consume_invite_token("no-such-token", "x@example.test")
    # 过期邀请拒绝
    stale = identity_store.issue_invite_token("tenant_b", "staff", ttl_seconds=-1)
    with pytest.raises(ValueError):
        identity_store.consume_invite_token(stale, "y@example.test")
    # 绑定租户与角色由签发侧固定（错绑无入口）
    raw2 = identity_store.issue_invite_token("tenant_b", "staff", ttl_seconds=60)
    out2 = identity_store.consume_invite_token(raw2, "z@example.test")
    assert out2["tenant_id"] == "tenant_b" and out2["role"] == "staff"


# -------------------------------------------------------------
# AC3：membership 撤销/会话版本递增 → 旧 Bearer 401、新登录正常
# -------------------------------------------------------------
def test_ac3_old_token_revoked_after_revoke(v2_on):
    auth.ACCOUNTS["revokeme@example.test"] = ("i01-task-only", "staff", "tenant_x")
    identity_store.grant_tenant_membership("revokeme@example.test", "tenant_x", "staff")
    time.sleep(1.1)  # 秒级版本粒度：确保旧令牌签发时间严格早于版本递增点
    old_token = auth.issue_token("revokeme@example.test", "staff", "tenant_x")
    client = TestClient(app)
    # 撤销前：staff token 打 owner 门 → 403（无 owner membership）
    assert client.post("/api/receipt/999999/approve",
                       headers={"Authorization": "Bearer " + old_token},
                       json={"version": 1}).status_code == 403
    identity_store.grant_tenant_membership("revokeme@example.test", "tenant_x", "owner")
    ok_token = auth.issue_token("revokeme@example.test", "staff", "tenant_x")
    assert client.post("/api/receipt/999999/approve",
                       headers={"Authorization": "Bearer " + ok_token},
                       json={"version": 1}).status_code == 404
    # 撤销：membership revoked + 会话版本递增；秒级版本粒度下，撤销时刻须晚于
    # 旧令牌签发秒（同秒签发+撤销按「有效」处理，见 identity_store 边界声明）
    time.sleep(1.1)
    assert identity_store.revoke_tenant_membership("revokeme@example.test", "tenant_x") is True
    new_token = auth.issue_token("revokeme@example.test", "staff", "tenant_x")
    assert auth.parse_token_full(old_token) is None  # 旧令牌拒（401 语义）
    assert auth.parse_token_full(new_token) is not None  # 新登录正常
    assert identity_store.has_business_role("revokeme@example.test", "tenant_x", "staff") is False

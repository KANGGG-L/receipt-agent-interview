# -*- coding: utf-8 -*-
"""I01 持久身份存储：accounts / tenant_memberships / platform_roles /
billing_memberships / session_auth_version / invite_tokens 的最小读写层。

口径（ADR06 / D-02 / I01 规格 v1）：
- 商户业务角色只来自 tenant_memberships 显式行；platform_admin 不默认具备任何
  商户业务角色（无 rank 继承、无 default tenant fallback）。
- membership 撤销 = 行标记 revoked + session_auth_version 递增（旧令牌 401 重登）。
- 邀请令牌一次性：consume 原子标记 consumed_at；错租户/过期/复用一律拒绝。
- 本层不做登录凭据切换：authenticate 仍用 auth.ACCOUNTS（兼容合同，见 i01-exit）。

开关：IDENTITY_V2_ENABLED 环境变量（auth.py 读取同一来源）。默认关闭时本层
不被 require_role/parse_token 调用，既有 rank 行为与 104 项测试不受影响。
"""

import hashlib
import secrets
import time
from datetime import datetime

from sqlalchemy import text

_TENANT_ROLES = ("owner", "staff")
_BILLING_ROLES = ("billing_owner", "billing_operator")
_INVITE_TTL_SECONDS_DEFAULT = 900


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _engine():
    from app import db
    if getattr(db, "_engine", None) is None:
        db._make_engine()
    return db._engine


def identity_v2_enabled():
    import os
    return os.environ.get("IDENTITY_V2_ENABLED", "0").strip().lower() in ("1", "true", "yes")


# -------------------------------------------------------------
# accounts
# -------------------------------------------------------------
def ensure_account(email, credential_hash=None, display_name=None):
    """受控开户（幂等）：已存在则原样返回 id，不覆盖凭据。"""
    email = str(email or "").strip().lower()
    if not email:
        raise ValueError("email required")
    eng = _engine()
    with eng.begin() as conn:
        row = conn.execute(text("SELECT id FROM accounts WHERE email=:e"), {"e": email}).fetchone()
        if row:
            return int(row[0])
        conn.execute(
            text("INSERT INTO accounts (email, credential_hash, display_name, disabled, created_at)"
                 " VALUES (:e, :c, :d, 0, :t)"),
            {"e": email, "c": credential_hash, "d": display_name, "t": _now_iso()})
        row = conn.execute(text("SELECT id FROM accounts WHERE email=:e"), {"e": email}).fetchone()
        return int(row[0])


def get_account_id(email):
    email = str(email or "").strip().lower()
    if not email:
        return None
    with _engine().connect() as conn:
        row = conn.execute(text("SELECT id FROM accounts WHERE email=:e"), {"e": email}).fetchone()
        return int(row[0]) if row else None


# -------------------------------------------------------------
# tenant_memberships
# -------------------------------------------------------------
def grant_tenant_membership(email, tenant_id, role):
    """授予/更新商户 membership（幂等 upsert；撤销后重新授予即复活）。"""
    if role not in _TENANT_ROLES:
        raise ValueError("tenant role must be one of %s" % (_TENANT_ROLES,))
    account_id = ensure_account(email)
    eng = _engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM tenant_memberships WHERE tenant_id=:t AND account_id=:a"),
            {"t": tenant_id, "a": account_id}).fetchone()
        if row:
            conn.execute(
                text("UPDATE tenant_memberships SET role=:r, status='active', revoked_at=NULL"
                     " WHERE id=:i"), {"r": role, "i": int(row[0])})
            return int(row[0])
        conn.execute(
            text("INSERT INTO tenant_memberships (tenant_id, account_id, role, status, created_at)"
                 " VALUES (:t, :a, :r, 'active', :c)"),
            {"t": tenant_id, "a": account_id, "r": role, "c": _now_iso()})
        row = conn.execute(
            text("SELECT id FROM tenant_memberships WHERE tenant_id=:t AND account_id=:a"),
            {"t": tenant_id, "a": account_id}).fetchone()
        return int(row[0])


def revoke_tenant_membership(email, tenant_id):
    """撤销 membership：行标记 revoked + 会话版本递增（旧 Bearer 401）。"""
    account_id = get_account_id(email)
    if account_id is None:
        return False
    eng = _engine()
    changed = False
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM tenant_memberships WHERE tenant_id=:t AND account_id=:a"
                 " AND status='active'"), {"t": tenant_id, "a": account_id}).fetchone()
        if row:
            conn.execute(
                text("UPDATE tenant_memberships SET status='revoked', revoked_at=:r WHERE id=:i"),
                {"r": _now_iso(), "i": int(row[0])})
            changed = True
    if changed:
        bump_session_auth_version(email)
    return changed


def has_business_role(email, tenant_id, need):
    """业务角色判定（I01 唯一权威实现；require_role 在 v2 开关下委托本函数）。

    - need='owner'：需显式 owner membership；
    - need='staff'：owner/staff membership 均可（同租户域内 owner 覆盖 staff 事务）；
    - need='admin'：平台域，不在 membership 判定范围（返回 False，require_admin 走
      平台身份链路）。
    - platform_admin 账号在此恒为无业务角色（无 rank 继承，D-02）。
    """
    if need == "admin":
        return False
    if need not in _TENANT_ROLES:
        raise ValueError("need must be owner/staff/admin")
    account_id = get_account_id(email)
    if account_id is None:
        return False
    with _engine().connect() as conn:
        rows = conn.execute(
            text("SELECT role FROM tenant_memberships WHERE account_id=:a AND tenant_id=:t"
                 " AND status='active'"), {"a": account_id, "t": tenant_id}).fetchall()
    roles = {r[0] for r in rows}
    if need == "owner":
        return "owner" in roles
    return bool(roles & {"owner", "staff"})


def resolve_memberships(email):
    """读取全部身份归属（调试点/后续 I02 capability 展开用）。"""
    account_id = get_account_id(email)
    out = {"account_id": account_id, "tenant_roles": {}, "platform_role": None,
           "billing_roles": {}}
    if account_id is None:
        return out
    with _engine().connect() as conn:
        for tid, role in conn.execute(
                text("SELECT tenant_id, role FROM tenant_memberships"
                     " WHERE account_id=:a AND status='active'"),
                {"a": account_id}).fetchall():
            out["tenant_roles"][tid] = role
        prow = conn.execute(
            text("SELECT role FROM platform_roles WHERE account_id=:a AND status='active'"),
            {"a": account_id}).fetchone()
        if prow:
            out["platform_role"] = prow[0]
        for tid, role in conn.execute(
                text("SELECT tenant_id, role FROM billing_memberships"
                     " WHERE account_id=:a AND status='active'"),
                {"a": account_id}).fetchall():
            out["billing_roles"][tid] = role
    return out


# -------------------------------------------------------------
# capability 词汇表与映射（I02 规格 v1 §1；词汇冻结，下游 S01/U03 引用）
# -------------------------------------------------------------
CAPABILITIES_TENANT_OWNER = frozenset({
    "own_receipt_read", "own_receipt_edit", "receipt_approve",
    "inventory_read", "inventory_manage",
    "supplier_manage", "supplier_credit_allocate", "supplier_refund_record",
    "finance_read", "finance_operate",
    "export_data", "dish_manage", "settings_manage", "memory_governance",
})
CAPABILITIES_TENANT_STAFF = frozenset({
    "own_receipt_read", "own_receipt_edit", "inventory_read",
})
CAPABILITIES_PLATFORM = frozenset({
    "engine_manage", "technical_aggregate_read",
})


def capabilities_for(email, tenant_id):
    """身份 → capability 集合（D-02：商户域与平台域分离，无跨域继承）。

    - owner membership（本租户）：CAPABILITIES_TENANT_OWNER 全集；
    - staff membership（本租户）：CAPABILITIES_TENANT_STAFF 子集；
    - platform_roles 行：CAPABILITIES_PLATFORM（平台域，与租户域互斥不叠加）；
    - billing_memberships 行：'billing:' + role 前缀能力（S01 消费）。
    返回 set[str]；无任何身份返回空集。
    """
    info = resolve_memberships(email)
    caps = set()
    role = (info["tenant_roles"] or {}).get(tenant_id)
    if role == "owner":
        caps |= CAPABILITIES_TENANT_OWNER
    elif role == "staff":
        caps |= CAPABILITIES_TENANT_STAFF
    if info["platform_role"]:
        caps |= CAPABILITIES_PLATFORM
    for brole in (info["billing_roles"] or {}).values():
        caps.add("billing:" + brole)
    return caps


# -------------------------------------------------------------
# platform / billing roles（schema 落地；消费方为 I02/S01）
# -------------------------------------------------------------
def set_platform_role(email, role="platform_admin"):
    account_id = ensure_account(email)
    with _engine().begin() as conn:
        conn.execute(
            text("INSERT INTO platform_roles (account_id, role, status, created_at)"
                 " VALUES (:a, :r, 'active', :c)"
                 " ON CONFLICT(account_id) DO UPDATE SET role=:r, status='active'"),
            {"a": account_id, "r": role, "c": _now_iso()})


def grant_billing_membership(email, tenant_id, role):
    if role not in _BILLING_ROLES:
        raise ValueError("billing role must be one of %s" % (_BILLING_ROLES,))
    account_id = ensure_account(email)
    with _engine().begin() as conn:
        row = conn.execute(
            text("SELECT id FROM billing_memberships WHERE tenant_id=:t AND account_id=:a"),
            {"t": tenant_id, "a": account_id}).fetchone()
        if row:
            conn.execute(
                text("UPDATE billing_memberships SET role=:r, status='active', revoked_at=NULL"
                     " WHERE id=:i"), {"r": role, "i": int(row[0])})
            return int(row[0])
        conn.execute(
            text("INSERT INTO billing_memberships (tenant_id, account_id, role, status, created_at)"
                 " VALUES (:t, :a, :r, 'active', :c)"),
            {"t": tenant_id, "a": account_id, "r": role, "c": _now_iso()})
        row = conn.execute(
            text("SELECT id FROM billing_memberships WHERE tenant_id=:t AND account_id=:a"),
            {"t": tenant_id, "a": account_id}).fetchone()
        return int(row[0])


# -------------------------------------------------------------
# session_auth_version（旧令牌迁移：版本不符即 401 重登）
# -------------------------------------------------------------
def bump_session_auth_version(email):
    account_id = ensure_account(email)
    now = int(time.time())
    with _engine().begin() as conn:
        conn.execute(
            text("INSERT INTO session_auth_version (account_id, version, updated_at)"
                 " VALUES (:a, 1, :t)"
                 " ON CONFLICT(account_id) DO UPDATE SET"
                 " version=version+1, updated_at=:t"),
            {"a": account_id, "t": now})


def token_version_ok(email, issued_at_epoch):
    """令牌签发时间不早于最近一次会话版本递增才有效；无版本记录的账号不受限。

    秒级粒度边界（显式声明）：签发与递增发生在同一秒时按「有效」处理——
    撤销的最坏生效延迟为 1 秒；新登录在递增同秒内签发的令牌不会被误拒。
    """
    account_id = get_account_id(email)
    if account_id is None:
        return True
    with _engine().connect() as conn:
        row = conn.execute(
            text("SELECT updated_at FROM session_auth_version WHERE account_id=:a"),
            {"a": account_id}).fetchone()
    if not row:
        return True
    return int(issued_at_epoch) >= int(row[0])


# -------------------------------------------------------------
# invite_tokens（受控开户：一次性邀请绑定商户 membership）
# -------------------------------------------------------------
def _token_hash(raw):
    return hashlib.sha256(str(raw).encode()).hexdigest()


def issue_invite_token(tenant_id, role, ttl_seconds=_INVITE_TTL_SECONDS_DEFAULT):
    """签发一次性邀请：明文 token 只在返回值出现一次，库里只存 sha256。"""
    if role not in _TENANT_ROLES:
        raise ValueError("invite role must be one of %s" % (_TENANT_ROLES,))
    raw = secrets.token_urlsafe(24)
    expires = int(time.time()) + int(ttl_seconds)
    with _engine().begin() as conn:
        conn.execute(
            text("INSERT INTO invite_tokens (token_hash, tenant_id, role, expires_at, created_at)"
                 " VALUES (:h, :t, :r, :e, :c)"),
            {"h": _token_hash(raw), "t": tenant_id, "r": role, "e": expires, "c": _now_iso()})
    return raw


def consume_invite_token(raw_token, email):
    """消费邀请：一次性、限时、租户/角色由签发时绑定（错绑拒绝）。

    成功 → 创建/复活对应 membership 并返回 {"tenant_id", "role"}；
    失败 → ValueError（invalid / expired / consumed）。
    """
    h = _token_hash(raw_token)
    eng = _engine()
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT id, tenant_id, role, expires_at, consumed_at FROM invite_tokens"
                 " WHERE token_hash=:h"), {"h": h}).fetchone()
        if not row:
            raise ValueError("invalid invite token")
        invite_id, tenant_id, role, expires_at, consumed_at = (
            int(row[0]), row[1], row[2], int(row[3]), row[4])
        if consumed_at is not None:
            raise ValueError("invite token already consumed")
        if expires_at < int(time.time()):
            raise ValueError("invite token expired")
        conn.execute(
            text("UPDATE invite_tokens SET consumed_at=:t, consumed_email=:e WHERE id=:i"),
            {"t": int(time.time()), "e": str(email).strip().lower(), "i": invite_id})
    grant_tenant_membership(email, tenant_id, role)
    return {"tenant_id": tenant_id, "role": role}

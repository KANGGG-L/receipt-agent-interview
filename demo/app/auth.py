# -*- coding: utf-8 -*-
"""轻量鉴权：三层 RBAC（admin / owner / staff）+ HMAC token。

对齐完整版 JWT 语义但极简化：
- AUTH_ENABLED 默认 0 → /api/auth/me 返回 auth_enabled=false、account=null，
  前端视为 owner 直通（面试演示顺畅）。
- 登录时校验账号 → 返回角色 + token（HMAC 签名，含角色与过期）。
- 请求带 Authorization: Bearer <token> → 解析角色注入 request.state.account。

预置账号：
  admin@demo.hk/admin123 → admin（引擎配置/灰测）
  boss@demo.hk/boss123   → owner（approve/flag/成本）
  staff@demo.hk/staff123 → staff（上传/复核）
"""

import hashlib
import hmac
import os
import time

from fastapi import Request, HTTPException

ACCOUNTS = {
    "admin@demo.hk": ("admin123", "admin"),
    "boss@demo.hk": ("boss123", "owner"),
    "staff@demo.hk": ("staff123", "staff"),
}

ROLE_RANK = {"admin": 3, "owner": 2, "staff": 1}

_TOKEN_SECRET = os.environ.get("DEMO_TOKEN_SECRET", "demo-token-secret-dev-only")
_TOKEN_TTL = 12 * 3600  # 12h


def auth_enabled():
    return os.environ.get("AUTH_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def _sign(payload: str) -> str:
    return hmac.new(_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(email: str, role: str) -> str:
    """签发 token：email.role.exp.signature。"""
    exp = int(time.time()) + _TOKEN_TTL
    payload = f"{email}.{role}.{exp}"
    return f"{payload}.{_sign(payload)}"


def parse_token(token: str):
    """验签解析 token → (email, role) | None。"""
    try:
        payload, sig = token.rsplit(".", 1)
        email, role, exp = payload.split(".")
        if hmac.compare_digest(_sign(payload), sig) and int(exp) > int(time.time()):
            if email in ACCOUNTS and ACCOUNTS[email][1] == role:
                return email, role
    except (ValueError, TypeError):
        pass
    return None


def authenticate(email: str, password: str):
    acct = ACCOUNTS.get((email or "").strip())
    if acct is None or acct[0] != password:
        return None
    return {"email": email.strip(), "role": acct[1]}


def resolve_account(request: Request):
    """从请求解析当前账号（优先 Bearer token，其次 X-Role 头，缺省 owner）。

    返回 dict：{"email", "role"}。
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        parsed = parse_token(auth[7:].strip())
        if parsed:
            return {"email": parsed[0], "role": parsed[1]}
    role = request.headers.get("X-Role", "owner")
    email = request.headers.get("X-Email", f"{role}@demo.hk")
    return {"email": email, "role": role}


def require_role(role: str):
    """FastAPI 依赖：请求者角色层级 >= 目标角色。"""
    def _dep(request: Request):
        account = resolve_account(request)
        if ROLE_RANK.get(account.get("role", ""), 0) < ROLE_RANK.get(role, 99):
            raise HTTPException(status_code=403, detail="权限不足")
        try:
            request.state.account = account
        except AttributeError:
            pass
        return account
    return _dep


def require_admin(request: Request):
    account = resolve_account(request)
    if account.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅 admin 可操作")
    request.state.account = account
    return account

# -*- coding: utf-8 -*-
"""认证端点：/api/auth/login、/api/auth/me。"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import authenticate, auth_enabled, issue_token, resolve_account

router = APIRouter()


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    acct = authenticate(body.email, body.password)
    if acct is None:
        # 与完整版一致：凭据错误 401
        return JSONResponse(content={"status": "error", "msg": "邮箱或密码错误"},
                            status_code=401)
    token = issue_token(acct["email"], acct["role"])
    # demo 无 refresh 流程；字段面与完整版对齐（AUTH_ENABLED=0 时亦为 None）
    return {"status": "success", "token": token, "refresh_token": None, "account": acct}


@router.get("/api/auth/me")
def me(request: Request):
    """角色探测：始终返回 auth_enabled=true + account（角色由 X-Role 头指定）。

    Demo 无密码：前端角色下拉 → localStorage → 请求带 X-Role 头。
    """
    acct = resolve_account(request)
    role = acct.get("role", "owner")
    if role not in ("admin", "owner", "staff"):
        role = "owner"
    return {
        "status": "success",
        "auth_enabled": True,
        "account": {"email": acct.get("email", ""), "role": role},
    }

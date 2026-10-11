# -*- coding: utf-8 -*-
"""认证端点：/api/auth/login、/api/auth/logout、/api/auth/me。"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.errors import error_response

# 以模块方式引用（而非 from ... import 值绑定）：auth_enabled 等判定函数需在调用时
# 动态解析，测试用例 monkeypatch `app.auth` 的判定函数才能影响本模块的回显。
from app import auth

router = APIRouter()


class LoginBody(BaseModel):
    email: str
    password: str


def _client_addr(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    client = _client_addr(request)
    wait = auth.login_wait_seconds(body.email, client)
    if wait > 0:
        resp = error_response(
            429, "LOGIN_THROTTLED",
            f"登录失败次数太多了，请等 {wait} 秒后再试。忘记密码请联系老板或管理员。",
            retry_after=wait)
        resp.headers["Retry-After"] = str(wait)
        return resp
    acct = auth.authenticate(body.email, body.password)
    if acct is None:
        wait = auth.record_login_failure(body.email, client)
        msg = "邮箱或密码错误，请检查后重新输入。"
        if wait > 0:
            msg += f"连续输错太多次，请等 {wait} 秒后再试。"
        return error_response(401, "INVALID_CREDENTIALS", msg)
    auth.clear_login_failures(body.email, client)
    # 租户随令牌一起签发（D19）：生产态租户归属只能来自身份，token 载荷是唯一来源。
    token = auth.issue_token(acct["email"], acct["role"], acct["tenant"])
    # token 同时放在响应体（旧前端用 Bearer）与 HttpOnly Cookie（新前端不必再把令牌放进 localStorage）。
    resp = JSONResponse(content={"status": "success", "token": token,
                                 "refresh_token": None, "account": acct})
    # 只在鉴权开启时下发 Cookie：免密演示模式靠 X-Role 头切换角色，残留的会话 Cookie
    # 会盖过角色头、让「切换角色」失灵。
    if auth.auth_enabled():
        auth.set_session_cookie(resp, token)
    return resp


@router.post("/api/auth/logout")
def logout(request: Request):
    """吊销当前令牌并清除会话 Cookie；令牌已失效时也返回成功（登出幂等）。"""
    tok, _origin = auth._request_token(request)
    full = auth.parse_token_full(tok) if tok else None
    if full:
        auth.revoke_jti(full["jti"], full["exp"])
    resp = JSONResponse(content={"status": "success", "msg": "已退出登录"})
    auth.clear_session_cookie(resp)
    return resp


@router.get("/api/auth/me")
def me(request: Request):
    """角色探测：回显当前请求携带的身份来源（角色由 X-Role 头指定）。

    Demo 无密码：前端角色下拉 → localStorage → 请求带 X-Role 头。
    匿名（无令牌且无 X-Role）时 account 返回 null——不再凭空报 owner，否则
    「角色头被中途丢弃」会被前端读成「当前就是老板」。
    """
    acct = auth.resolve_account(request)
    role = acct.get("role")
    # AUTH_ENABLED 的真实回显（不再硬编码 True）：前端启动探测据 auth_enabled=false 判定
    # 「免密直通」，若在 AUTH_ENABLED=0 时误报 true，会与账号为空（account=null）自相矛盾。
    enabled = auth.auth_enabled()
    if role not in ("admin", "owner", "staff"):
        return {"status": "success", "auth_enabled": enabled,
                "account": None, "identity_source": "missing"}
    return {
        "status": "success",
        "auth_enabled": enabled,
        "account": {"email": acct.get("email", ""), "role": role},
        "identity_source": acct.get("source"),
        "demo_identity": acct.get("source") == "header",
    }

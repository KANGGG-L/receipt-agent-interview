# -*- coding: utf-8 -*-
"""认证端点：/api/auth/login、/api/auth/me。"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# 以模块方式引用（而非 from ... import 值绑定）：auth_enabled 等判定函数需在调用时
# 动态解析，测试用例 monkeypatch `app.auth` 的判定函数才能影响本模块的回显。
from app import auth

router = APIRouter()


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    acct = auth.authenticate(body.email, body.password)
    if acct is None:
        # 与完整版一致：凭据错误 401
        return JSONResponse(content={"status": "error", "msg": "邮箱或密码错误"},
                            status_code=401)
    # 租户随令牌一起签发（D19）：生产态租户归属只能来自身份，token 载荷是唯一来源，
    # 故这里必须把账号在 ACCOUNTS 中的租户透传给 issue_token（缺省 default）。
    token = auth.issue_token(acct["email"], acct["role"], acct["tenant"])
    # demo 无 refresh 流程；字段面与完整版对齐（AUTH_ENABLED=0 时亦为 None）
    return {"status": "success", "token": token, "refresh_token": None, "account": acct}


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
    }

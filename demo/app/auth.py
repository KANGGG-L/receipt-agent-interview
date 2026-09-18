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

# 仓库内置的开发默认密钥。生产不设 DEMO_TOKEN_SECRET 就等同于用公开值签名，
# 防枚举强度退化为混淆；但故意不在此处改动默认值本身——改了会让既有已签发的
# 预览 URL / 令牌全部失效，只能靠启动告警提示运维补齐环境变量（见
# token_secret_is_weak 与 app/main.py 的 _warn_weak_token_secret）。
_DEFAULT_TOKEN_SECRET = "demo-token-secret-dev-only"
_TOKEN_SECRET = os.environ.get("DEMO_TOKEN_SECRET", _DEFAULT_TOKEN_SECRET)
_TOKEN_TTL = 12 * 3600  # 12h


def token_secret_is_weak():
    """是否仍在使用仓库内置默认密钥（未设 DEMO_TOKEN_SECRET，或设成了同一个值）。

    why: 生产部署若忘设该环境变量，预览端点签名等于公开常量，任何知道默认值的
    人都能自行伪造 sig 遍历单据 id。启动阶段据此告警；开发/演示环境不受影响。
    """
    raw = (os.environ.get("DEMO_TOKEN_SECRET") or "").strip()
    return raw == "" or raw == _DEFAULT_TOKEN_SECRET


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
    """验签解析 token → (email, role) | None。

    why 从右侧切分：payload 结构是 email.role.exp，而本项目预置账号全是
    `xx@demo.hk`（邮箱本身含点），旧的 payload.split(".") 会切出 4 段导致
    解包 ValueError、被 except 静默吞掉，parse_token 恒返回 None（Bearer 链路
    实际不可用）。role 是已知枚举（admin/owner/staff）、exp 是纯数字，都不含点，
    故 rsplit(".", 2) 从右侧切两段可把点安全地留在 email 段内。
    注意：token 生成格式（issue_token）刻意不改，改了会让既有已签发 token 失效。
    """
    try:
        payload, sig = token.rsplit(".", 1)
        email, role, exp = payload.rsplit(".", 2)
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
    """从请求解析当前账号身份，区分三种来源，不做静默降级。

    返回 dict：{"email", "role", "source"}；匿名时 role=None（由调用方决定拒绝方式）。
      - source="token"  ：Bearer 令牌验签通过（最强）
      - source="header" ：X-Role 头声明（demo 无密码 RBAC 的正常通道）
      - source=None     ：两者皆无 = 匿名。曾默认成 owner，使「角色头被中途丢弃」
                          表现为「当前角色为老板」的误导性 403，故显式置空。
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        parsed = parse_token(auth[7:].strip())
        if parsed:
            return {"email": parsed[0], "role": parsed[1], "source": "token"}
    role = request.headers.get("X-Role")
    if role in ROLE_RANK:
        return {"email": request.headers.get("X-Email", f"{role}@demo.hk"),
                "role": role, "source": "header"}
    return {"email": None, "role": None, "source": None}


_ROLE_LABEL = {"admin": "超管", "owner": "老板", "staff": "店员"}

_UNAUTHENTICATED_DETAIL = ("未认证：请求未携带身份信息（既无有效 Bearer 令牌，也无 X-Role 角色头）。"
                           "这通常意味着角色头在传输中被丢弃，并非权限不足——请确认前端已随请求附带 X-Role。")


def _reject_anonymous():
    """匿名请求走 401（未认证），与 403（已认证但权限不足）语义分开。"""
    raise HTTPException(status_code=401, detail=_UNAUTHENTICATED_DETAIL)


def require_role(role: str):
    """FastAPI 依赖：请求者角色层级 >= 目标角色。403 文案人话统一（含当前角色与所需角色指引）。"""
    def _dep(request: Request):
        account = resolve_account(request)
        cur = account.get("role")
        if cur is None:
            _reject_anonymous()
        if ROLE_RANK.get(cur, 0) < ROLE_RANK.get(role, 99):
            need_label = _ROLE_LABEL.get(role, role)
            cur_label = _ROLE_LABEL.get(cur, cur)
            raise HTTPException(status_code=403, detail=f"权限不足：当前角色为{cur_label}（{cur}），此操作需{need_label}（{role}）及以上权限。请切换角色或联系管理员。")
        try:
            request.state.account = account
        except AttributeError:
            pass
        return account
    return _dep


def require_admin(request: Request):
    account = resolve_account(request)
    cur = account.get("role")
    if cur is None:
        _reject_anonymous()
    if cur != "admin":
        cur_label = _ROLE_LABEL.get(cur, cur)
        raise HTTPException(status_code=403, detail=f"权限不足：此操作仅限超管（admin）执行，当前角色为{cur_label}（{cur}）。请切换为 admin 角色。")
    request.state.account = account
    return account

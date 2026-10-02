# -*- coding: utf-8 -*-
"""轻量鉴权：三层 RBAC（admin / owner / staff）+ HMAC token。

对齐完整版 JWT 语义但极简化：
- AUTH_ENABLED 默认 0 → /api/auth/me 返回 auth_enabled=false、account=null，
  前端视为 owner 直通（面试演示顺畅）。
- 登录时校验账号 → 返回角色 + token（HMAC 签名，含角色、租户与过期）。
- 请求带 Authorization: Bearer <token> → 解析角色注入 request.state.account。
- AUTH_ENABLED=1（生产）时身份只能来自 Bearer 验签，不回落 X-Role / X-Email；且启动阶段
  强制要求非默认 DEMO_TOKEN_SECRET（见 enforce_production_secret），否则拒绝启动。
- 租户同样由身份派生：生产态租户「只」取自 token 载荷（客户端 X-Tenant-Id 一律忽略），
  开发/演示态才读 X-Tenant-Id。唯一实现见 resolve_tenant，各 api_*.py 的 _tenant_id
  一律委托它，不得各写一份。

预置账号（第 3 个元素是账号所属租户；生产态租户即由此经 token 派生）：
  admin@demo.hk/admin123 → admin（引擎配置/灰测） tenant=default
  boss@demo.hk/boss123   → owner（approve/flag/成本） tenant=default
  staff@demo.hk/staff123 → staff（上传/复核） tenant=default
"""

import hashlib
import hmac
import os
import re
import time

from fastapi import Request, HTTPException

# email -> (password, role, tenant)
ACCOUNTS = {
    "admin@demo.hk": ("admin123", "admin", "default"),
    "boss@demo.hk": ("boss123", "owner", "default"),
    "staff@demo.hk": ("staff123", "staff", "default"),
}

ROLE_RANK = {"admin": 3, "owner": 2, "staff": 1}

# 缺省租户字面量：开发/演示态无 X-Tenant-Id 头、生产态无有效身份时统一落到它，
# 与 db 层（tenant_id 为空即不过滤）及前端 localStorage 缺省值语义一致。
_DEFAULT_TENANT = "default"

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

    如实陈述（不得美化）：弱密钥形态下的签名**只防误撞（偶然撞出合法 URL），
    不防构造**——密钥是仓库公开常量，任何读过源码的人都能自行签发任意
    receipt_id / 凭证名 / 租户的合法 sig。此形态下签名不构成访问控制，仅
    「URL 必须由后端签发过」这一形态校验仍然成立；真正的边界只有租户归属校验与
    文件路径收敛。/api/health 的 signature_secret_weak 标志把这个事实暴露给运维。
    """
    raw = (os.environ.get("DEMO_TOKEN_SECRET") or "").strip()
    return raw == "" or raw == _DEFAULT_TOKEN_SECRET


def client_declared_tenant(request: Request, fallback: str = "") -> str:
    """客户端声明的租户：X-Tenant-Id 头 → fallback → default。

    仅在 auth_enabled() 为假（开发/演示）时允许使用：该头由客户端任意声明，生产态
    采信即等于把租户隔离降级成「任何人自称任意租户」。resolve_tenant 的开发态分支与
    feedback 端点的「body 兜底」通道共用本实现，避免同一条头读取逻辑抄两份。
    """
    declared = (request.headers.get("X-Tenant-Id")
                or request.headers.get("x-tenant-id") or "").strip()
    if declared:
        return declared
    return str(fallback or "").strip() or _DEFAULT_TENANT


def auth_enabled():
    return os.environ.get("AUTH_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def enforce_production_checklist():
    """生产环境强启动安全核验（Production Startup Gate，W-ISO-1）。

    当 AUTH_ENABLED=1 时强制核验：
    1. DEMO_TOKEN_SECRET 不得为空且不得等于仓库内置默认值；
    2. 生产环境预置密码核验：预置账号不得使用内置默认弱口令（admin123, boss123, staff123）。
    任一项违规直接抛出 RuntimeError 拒绝服务拉起并打印修复指引。
    """
    if not auth_enabled():
        return
    if token_secret_is_weak():
        raise RuntimeError(
            "生产环境鉴权已开启（AUTH_ENABLED=1），但 DEMO_TOKEN_SECRET 未设置或仍是"
            "仓库内置默认值。该密钥用于签发与校验登录令牌，使用公开默认值等同于任何人都能"
            "伪造管理员令牌。请在部署环境显式设置一个随机长字符串（例如 openssl rand -hex 32）"
            "后重启服务。"
        )
    weak_passwords = {"admin123", "boss123", "staff123"}
    for email, creds in ACCOUNTS.items():
        if creds[0] in weak_passwords:
            raise RuntimeError(
                f"生产环境鉴权已开启（AUTH_ENABLED=1），但预置账号 {email} 仍在使用内置默认弱口令 ({creds[0]})。"
                "生产环境严禁使用默认弱口令，请重设密码或在生产配置中禁用预置账号后重启服务。"
            )


def enforce_production_secret():
    """生产态强制项：仅检查密钥强度（保持向后兼容）。"""
    if auth_enabled() and token_secret_is_weak():
        raise RuntimeError(
            "生产环境鉴权已开启（AUTH_ENABLED=1），但 DEMO_TOKEN_SECRET 未设置或仍是"
            "仓库内置默认值。该密钥用于签发与校验登录令牌，使用公开默认值等同于任何人都能"
            "伪造管理员令牌。请在部署环境显式设置一个随机长字符串（例如 openssl rand -hex 32）"
            "后重启服务。"
        )


def _sign(payload: str) -> str:
    return hmac.new(_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(email: str, role: str, tenant: str = _DEFAULT_TENANT) -> str:
    """签发 token：email.role.exp.tenant.signature。

    tenant（D19）随令牌一起签进载荷，成为生产态租户归属的唯一来源：客户端
    X-Tenant-Id 头在生产态被整体忽略（见 resolve_tenant），越租请求拿不到他租数据。
    tenant 由登录账号在 ACCOUNTS 中的第 3 个元素给出（api_auth.login 透传）。
    """
    tenant = str(tenant or "").strip() or _DEFAULT_TENANT
    if "." in tenant:
        # 载荷以点分段、tenant 是最后一段：含点的 tenant 会被 parse_token 切碎，
        # 导致「签发成功但令牌永远验不过」的静默失配，故在此显式拒绝。
        raise ValueError(f"租户标识不得包含 '.'：{tenant!r}")
    exp = int(time.time()) + _TOKEN_TTL
    payload = f"{email}.{role}.{exp}.{tenant}"
    return f"{payload}.{_sign(payload)}"


def parse_token(token: str):
    """验签解析 token → (email, role, tenant) | None。

    why 从右侧切分：payload 结构是 email.role.exp.tenant，而本项目预置账号全是
    `xx@demo.hk`（邮箱本身含点），旧的 payload.split(".") 会切出 4 段导致
    解包 ValueError、被 except 静默吞掉，parse_token 恒返回 None（Bearer 链路
    实际不可用）。role 是已知枚举（admin/owner/staff）、exp 是纯数字、tenant 是
    内部配置的标识（issue_token 已拒绝含点值），都不含点，故 rsplit(".", 3) 从右侧
    切三段可把点安全地留在 email 段内。
    账号特征三要素（role / tenant / 存在于 ACCOUNTS）必须与签名载荷完全一致，
    否则视为无效令牌：tenant 一并校验，可防「用合法账号签出他租令牌」。
    """
    try:
        payload, sig = token.rsplit(".", 1)
        email, role, exp, tenant = payload.rsplit(".", 3)
        if hmac.compare_digest(_sign(payload), sig) and int(exp) > int(time.time()):
            acct = ACCOUNTS.get(email)
            if acct and acct[1] == role and acct[2] == tenant:
                return email, role, tenant
    except (ValueError, TypeError, IndexError):
        pass
    return None


def authenticate(email: str, password: str):
    acct = ACCOUNTS.get((email or "").strip())
    if acct is None or acct[0] != password:
        return None
    return {"email": email.strip(), "role": acct[1], "tenant": acct[2]}


def resolve_account(request: Request):
    """从请求解析当前账号身份，区分三种来源，不做静默降级。

    返回 dict：{"email", "role", "tenant", "source"}；匿名时 role/tenant=None
    （由调用方决定拒绝方式）。
      - source="token"  ：Bearer 令牌验签通过（最强）；tenant 取自令牌载荷
                          （令牌由 ACCOUNTS 的账号租户签发，故不可跨租户伪造）
      - source="header" ：X-Role 头声明（仅在 AUTH_ENABLED=0 的 demo 免密模式）；
                          此时无真实身份，tenant 取自 X-Tenant-Id 头（缺省 default），
                          与 resolve_tenant 在开发态的口径保持一致
      - source=None     ：匿名。曾默认成 owner，使「角色头被中途丢弃」表现为
                          「当前角色为老板」的误导性 403，故显式置空。

    AUTH_ENABLED=1（生产）时不再回落 X-Role / X-Email：这两个头是客户端可任意声明的，
    回落等于把「鉴权开启」降级成「任何人都能自称 admin」。此时身份只能来自 Bearer 验签，
    无有效令牌即匿名（role=None, tenant=None），由 _reject_anonymous 统一给出 401。

    AUTH_ENABLED=0 时的行为保持不变：Bearer 优先，其次 X-Role（demo 免密角色切换通道）。
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        parsed = parse_token(auth[7:].strip())
        if parsed:
            return {"email": parsed[0], "role": parsed[1], "tenant": parsed[2],
                    "source": "token"}
    if auth_enabled():
        # 生产态：X-Role / X-Email 声明一律不采信，匿名交给调用方以 401 拒绝。
        return {"email": None, "role": None, "tenant": None, "source": None}
    role = request.headers.get("X-Role")
    if role in ROLE_RANK:
        return {"email": request.headers.get("X-Email", f"{role}@demo.hk"),
                "role": role, "tenant": client_declared_tenant(request),
                "source": "header"}
    return {"email": None, "role": None, "tenant": None, "source": None}


def resolve_tenant(request: Request) -> str:
    """租户归属的唯一解析实现（各 api_*.py 的 _tenant_id 一律委托本函数）。

    口径（D19：租户必须由身份派生，不得由客户端声明）：
      - auth_enabled() 为真（生产）：租户**只**取自身份 —— 优先 request.state.account
        （require_role / require_admin 已注入），否则回落到 resolve_account(request)
        （例如未过 RBAC 依赖的端点）；身份里的 tenant 来自令牌载荷。客户端
        X-Tenant-Id **整体忽略**，越租请求因此拿不到他租数据。
        无有效身份（匿名）时取 _DEFAULT_TENANT —— 受保护端点在到达本函数前已被 401
        拒绝，这里只覆盖「匿名可访问且需要租户键」的余量路径。
      - auth_enabled() 为假（开发/演示）：维持既有行为，读 X-Tenant-Id（缺省 default）。
        演示角色下拉与 localStorage 的租户切换依赖该通道，不能改坏。

    例外（必须保持请求头/查询参数口径）：GET /api/receipt/{id}/image 与
    GET /api/payment/voucher/{name} 由浏览器原生 `<img>` / `<a>` 发起，带不了请求头，
    其租户来自 query 参数并由 HMAC 签名绑定（见 receipt_utils 的
    preview_image_signature / voucher_image_signature）。这两条端点**不要求身份**，
    故不适用本函数的生产态口径；调用点仅把它们缺失 query 时的兜底退回本函数。
    """
    if auth_enabled():
        try:
            acct = getattr(request.state, "account", None)
        except AttributeError:
            acct = None
        if not acct:
            acct = resolve_account(request)
        tenant = (acct or {}).get("tenant")
        if acct and acct.get("role") and tenant:
            return str(tenant).strip() or _DEFAULT_TENANT
        return _DEFAULT_TENANT
    return client_declared_tenant(request)


def resolve_request_tenant(request: Request, client_claimed: str = "") -> str:
    """带「客户端兜底」的租户解析：生产态只信身份，开发/演示态 header → client_claimed → default。

    仅用于**允许老客户端把租户放进请求体**的兼容端点（当前只有
    POST /api/receipt/{id}/feedback）：生产态直接委托 resolve_tenant（忽略一切客户端声明），
    开发态沿用既有「X-Tenant-Id 头优先 → body.tenant_id 兜底」通道。

    why 单独一个函数而不是在端点里各写一遍 if：判定必须走 auth_enabled() 的动态解析，
    端点若 `from app.auth import auth_enabled` 会把函数对象绑定进自己的命名空间，测试
    monkeypatch app.auth 的判定函数就影响不到它（本项目的既有约束）。租户口径的统一实现
    只有 resolve_tenant 一份，本函数只是它的「开发态兜底策略」包装。
    """
    if auth_enabled():
        return resolve_tenant(request)
    return client_declared_tenant(request, fallback=client_claimed)


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


# -------------------------------------------------------------
# D23：/api/* 早鉴权中间件（消除 422 探测面）
# -------------------------------------------------------------
# 背景：各受保护端点用内联 require_role(...)(request) 校验身份，但那发生在 FastAPI
# 完成请求体/查询参数校验（Pydantic）之后——于是匿名请求带一个非法请求体时，会先因
# 参数校验失败拿到 422、而不是 401。422 与 401 的差异暴露了「该路径存在且需要这些
# 参数」的信息，可用于端点探测/枚举。
#
# 本中间件在路由分发（即任何参数校验）之前，对 /api/* 做一次「是否已有可解析身份」
# 的判定：只要不是白名单路径，且 resolve_account 解析出的 role 为 None（匿名），
# 直接 401。它只负责「必须先有身份」，端点内的角色阈值（staff/owner/admin）保持不变，
# 由端点自身的 require_role 继续把关（越权仍是 403，语义不混）。
#
# 白名单（必须保持匿名可用，见 auth.resolve_tenant 的例外说明）：
#   - /api/auth/login、/api/auth/me：登录与身份探测本身就是匿名入口；
#   - /api/health：运维健康检查（匿名可读，不强依赖身份）；
#   - /api/receipt/{id}/image、/api/payment/voucher/{name}：由浏览器原生 <img>/<a>
#     发起，带不了请求头，靠 HMAC 签名 + 租户归属收敛。
# 非 /api/ 路径（/、/static、/evalset 等）完全不受影响。
#
# 两种模式（AUTH_ENABLED=0 开发态 / =1 生产态）都生效：判定复用 resolve_account，
# 开发态身份来自 X-Role 头（与现状一致），生产态身份只能来自 Bearer 验签。
_WHITELIST_EXACT = frozenset({"/api/auth/login", "/api/auth/me", "/api/health"})
# {id} / {name} 是路径参数：穿越/编码形态（如 `..%2Fsecret` 解码后含 '/'）也必须放行到
# 端点，由端点自身的签名 + basename/realpath 收敛判定给出 404/403——这两条端点本就
# 匿名可用，中间件不得改变它们的既有可观测行为（tests/test_no_uploads_static_exposure.py
# 的穿越用例断言 404）。该前缀下除这两条匿名端点外无其它路由，放宽不暴露受保护端点。
_WHITELIST_PATTERNS = (
    re.compile(r"^/api/receipt/.+/image$"),
    re.compile(r"^/api/payment/voucher/.+$"),
)


def is_early_auth_whitelisted(path: str) -> bool:
    """早鉴权白名单判定：命中即跳过「必须先有身份」的强制（仅这两类端点匿名可用）。"""
    if path in _WHITELIST_EXACT:
        return True
    return any(p.match(path or "") for p in _WHITELIST_PATTERNS)


def unauthenticated_detail() -> str:
    """匿名 401 的统一人话文案（中间件与 _reject_anonymous 共用，避免两处文案漂移）。"""
    return _UNAUTHENTICATED_DETAIL


class EarlyAuthMiddleware:
    """纯 ASGI 中间件：/api/* 在路由分发前强制「必须先有可解析身份」。

    why 纯 ASGI 而非 BaseHTTPMiddleware：本中间件只需读取请求头、不消费请求体，
    也不包装响应，纯 ASGI 实现不引入额外的 receive 包装与 streaming 边界问题
    （图片端点的 FileResponse 流式响应不受影响）。

    非 http scope（如 websocket/lifespan）直接透传。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "") or ""
        if not path.startswith("/api/") or is_early_auth_whitelisted(path):
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request as _StarletteRequest
        from starlette.responses import JSONResponse as _JSONResponse

        account = resolve_account(_StarletteRequest(scope, receive=receive))
        if account.get("role") is None:
            response = _JSONResponse(
                status_code=401,
                content={"status": "error", "detail": _UNAUTHENTICATED_DETAIL},
            )
            await response(scope, receive, send)
            return

        from app import db as _db
        tenant_id = account.get("tenant")
        has_header_tid = False
        for k, v in scope.get("headers", []):
            if k.lower() == b"x-tenant-id":
                has_header_tid = True
                break

        if auth_enabled() or has_header_tid:
            _db.set_current_tenant_id(tenant_id or _DEFAULT_TENANT)
        try:
            await self.app(scope, receive, send)
        except _db.TenantBoundaryViolationError:
            response = _JSONResponse(
                status_code=404,
                content={"status": "error", "msg": "未找到指定资源", "code": "RESOURCE_NOT_FOUND"},
            )
            await response(scope, receive, send)
        finally:
            _db.set_current_tenant_id(None)

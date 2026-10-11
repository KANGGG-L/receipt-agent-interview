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
import secrets
import threading
import time

from fastapi import Request, HTTPException

# email -> (password_hash, role, tenant)。口令只存 PBKDF2 哈希，比较用 compare_digest；
# 为兼容测试与外部注入的明文账号，不带 "pbkdf2_sha256$" 前缀的值按明文常量时间比较。
ACCOUNTS = {
    "admin@demo.hk": (
        "pbkdf2_sha256$150000$a1c0de01$9c20aa8a26dcd8bdc416a64b2255eef437ad050f317d561f7d4350e972a56bee",
        "admin", "default"),
    "boss@demo.hk": (
        "pbkdf2_sha256$150000$b055c0de$c4efa44c3af2e850f62d00dcf29c62b1bb1eb3b978a29532becd5c645b4a73b6",
        "owner", "default"),
    "staff@demo.hk": (
        "pbkdf2_sha256$150000$57aff001$dea5b33e472e82b66c08e9eada23e2ca5c0a3524e853db333e28d37353569912",
        "staff", "default"),
}

_WEAK_DEFAULT_PASSWORDS = ("admin123", "boss123", "staff123")


def hash_password(password: str, salt_hex: str = "", iterations: int = 150000) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(8)
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode(), salt, iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验口令；stored 为哈希串或（兼容用的）明文。"""
    password = str(password or "")
    stored = str(stored or "")
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iters, salt_hex, digest = stored.split("$", 3)
            calc = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(calc, digest)
    return hmac.compare_digest(password.encode(), stored.encode())

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


def env_name() -> str:
    """当前运行环境名（小写）：ENV → APP_ENV → 缺省 development。

    why 与 app/main.py 的 `_DEV_ENV` 保持同一读取顺序与缺省值：环境判定有两处使用
    （本模块的生产启动核验、main 的静态缓存控制），口径一旦漂移就会出现「核验按生产、
    缓存按开发」之类的错位。此处集中实现，main.py 复用同一函数。

    注意缺省是 development（而非 production）：未显式声明即视为开发/演示环境，与既有
    `ENV`/`APP_ENV` 语义一致——面试展示脚本（interview-show.sh）正是跑在该缺省下。
    """
    return os.environ.get("ENV", os.environ.get("APP_ENV", "development")).strip().lower()


def is_production_env() -> bool:
    """是否显式声明为生产环境（production/prod）；未设置或其它值一律视为开发/演示。"""
    return env_name() in ("production", "prod")


def enforce_production_checklist():
    """生产启动强核验（Production Startup Gate，W-ISO-1）。

    当 AUTH_ENABLED=1 时强制核验：
    1. DEMO_TOKEN_SECRET 不得为空且不得等于仓库内置默认值（**任何环境**都拒绝，防弱签名上线）；
    2. 预置账号弱口令：**仅在生产环境**（ENV/APP_ENV == production/prod）拒绝启动。

    why 第 2 项要按环境门控：本仓库是面试/演示资产，interview-show.sh 以 AUTH_ENABLED=1
    公网拉起并依赖预置账号（admin@demo.hk/admin123 等）登录；预置口令是公开且刻意的，
    一律拒绝会让「公网面试演示」这条旗舰路径无法启动（UAT BLOCKER-1）。而生产部署必须
    显式声明 ENV=production，届时弱口令仍 fail-closed —— 这不是「放行生产」，而是把
    「预置口令不可用于生产」这条规则绑定到真正的生产环境判定上。第 1 项（强密钥）不随
    环境放宽，保持原样。
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
    if not is_production_env():
        # 开发/演示/面试展示：预置账号是刻意的公开口令，允许启动（见函数 docstring）。
        return
    for email, creds in ACCOUNTS.items():
        weak = next((w for w in _WEAK_DEFAULT_PASSWORDS if verify_password(w, creds[0])), None)
        if weak:
            raise RuntimeError(
                f"生产环境鉴权已开启（AUTH_ENABLED=1，ENV={env_name()}），但预置账号 {email} 仍在使用内置默认弱口令 ({weak})。"
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


# 已吊销令牌（登出）：jti -> exp。进程内缓存 + 落库（见 app/session_store.py），
# 过期条目随查询顺手清理，表不会无限增长。
_REVOKED = {}
_REVOKED_LOCK = threading.Lock()


def revoke_jti(jti: str, exp: int) -> None:
    with _REVOKED_LOCK:
        _REVOKED[jti] = int(exp)
    try:
        from app import session_store
        session_store.persist_revoked(jti, int(exp))
    except Exception:  # noqa: BLE001 落库失败不影响本进程内的吊销生效
        pass


def load_revoked_from_store() -> None:
    try:
        from app import session_store
        rows = session_store.load_revoked()
    except Exception:  # noqa: BLE001
        return
    with _REVOKED_LOCK:
        _REVOKED.update(rows)


def is_revoked(jti: str) -> bool:
    now = int(time.time())
    with _REVOKED_LOCK:
        if len(_REVOKED) > 512:
            for k in [k for k, v in _REVOKED.items() if v <= now]:
                _REVOKED.pop(k, None)
        return jti in _REVOKED


def issue_token(email: str, role: str, tenant: str = _DEFAULT_TENANT) -> str:
    """签发 token：email.role.exp.tenant.jti.signature。

    tenant（D19）随令牌一起签进载荷，成为生产态租户归属的唯一来源：客户端
    X-Tenant-Id 头在生产态被整体忽略（见 resolve_tenant），越租请求拿不到他租数据。
    jti 是每枚令牌唯一的编号，登出时按它吊销（见 revoke_jti）。
    """
    tenant = str(tenant or "").strip() or _DEFAULT_TENANT
    if "." in tenant:
        # 载荷以点分段：含点的 tenant 会被 parse_token 切碎，导致「签发成功但令牌永远
        # 验不过」的静默失配，故在此显式拒绝。
        raise ValueError(f"租户标识不得包含 '.'：{tenant!r}")
    exp = int(time.time()) + _TOKEN_TTL
    jti = secrets.token_hex(8)
    payload = f"{email}.{role}.{exp}.{tenant}.{jti}"
    return f"{payload}.{_sign(payload)}"


def parse_token_full(token: str):
    """验签解析 token → dict(email, role, tenant, exp, jti) | None（含吊销检查）。

    why 从右侧切分：email 本身含点（xx@demo.hk），role/exp/tenant/jti 都不含点，
    故 rsplit(".", 4) 从右侧切四段可把点安全地留在 email 段内。
    账号特征三要素（role / tenant / 存在于 ACCOUNTS）必须与签名载荷完全一致。
    """
    try:
        payload, sig = token.rsplit(".", 1)
        email, role, exp, tenant, jti = payload.rsplit(".", 4)
        if hmac.compare_digest(_sign(payload), sig) and int(exp) > int(time.time()):
            acct = ACCOUNTS.get(email)
            if acct and acct[1] == role and acct[2] == tenant and not is_revoked(jti):
                return {"email": email, "role": role, "tenant": tenant,
                        "exp": int(exp), "jti": jti}
    except (ValueError, TypeError, IndexError, AttributeError):
        pass
    return None


def parse_token(token: str):
    """验签解析 token → (email, role, tenant) | None。"""
    full = parse_token_full(token)
    if full is None:
        return None
    return full["email"], full["role"], full["tenant"]


# 登录失败计数与退避：同一「邮箱 + 来源地址」连续失败 5 次后开始等待，
# 每多失败一次等待翻倍（上限 15 分钟）；成功登录清零。进程内计数，重启清零。
_LOGIN_FAILS = {}
_LOGIN_LOCK = threading.Lock()
_LOGIN_FREE_ATTEMPTS = 5
_LOGIN_MAX_WAIT = 900


def _login_key(email: str, client: str) -> str:
    return f"{(email or '').strip().lower()}|{client or ''}"


def login_wait_seconds(email: str, client: str = "") -> int:
    """距离允许下一次尝试还需等待的秒数（0 表示可以尝试）。"""
    with _LOGIN_LOCK:
        rec = _LOGIN_FAILS.get(_login_key(email, client))
        if not rec:
            return 0
        return max(0, int(rec["until"] - time.time() + 0.999))


def record_login_failure(email: str, client: str = "") -> int:
    with _LOGIN_LOCK:
        key = _login_key(email, client)
        rec = _LOGIN_FAILS.setdefault(key, {"count": 0, "until": 0.0})
        rec["count"] += 1
        over = rec["count"] - _LOGIN_FREE_ATTEMPTS
        wait = min(_LOGIN_MAX_WAIT, 2 ** over) if over >= 0 else 0
        rec["until"] = time.time() + wait
        if len(_LOGIN_FAILS) > 2000:
            now = time.time()
            for k in [k for k, v in _LOGIN_FAILS.items() if v["until"] < now - 3600]:
                _LOGIN_FAILS.pop(k, None)
        return wait


def clear_login_failures(email: str, client: str = "") -> None:
    with _LOGIN_LOCK:
        _LOGIN_FAILS.pop(_login_key(email, client), None)


def reset_login_throttle() -> None:
    with _LOGIN_LOCK:
        _LOGIN_FAILS.clear()


def authenticate(email: str, password: str):
    key = (email or "").strip()
    acct = ACCOUNTS.get(key)
    # 账号不存在时也做一次同成本的哈希比较，避免从耗时差异探测账号是否存在
    stored = acct[0] if acct else ACCOUNTS["staff@demo.hk"][0]
    ok = verify_password(password, stored)
    if acct is None or not ok:
        return None
    return {"email": key, "role": acct[1], "tenant": acct[2]}


SESSION_COOKIE = "receipt_session"
# 带 Cookie 会话的写请求必须携带该头：跨站表单与图片请求无法自定义请求头，
# 浏览器对跨站带自定义头的 fetch 又会先发预检（本服务不开 CORS），从而挡住 CSRF；
# Cookie 本身另设 SameSite=Lax。
CSRF_HEADER = "x-requested-with"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _request_token(request: Request):
    """从请求取令牌：Authorization Bearer 优先，其次 HttpOnly 会话 Cookie。返回 (token, 来源)。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip(), "token"
    cookie = request.cookies.get(SESSION_COOKIE) if hasattr(request, "cookies") else None
    if cookie:
        return cookie, "cookie"
    return None, None


def csrf_ok(request: Request, account: dict) -> bool:
    """仅对 Cookie 会话的写请求要求自定义头；Bearer 与演示身份不涉及 CSRF。"""
    if (account or {}).get("source") != "cookie":
        return True
    if request.method.upper() not in _UNSAFE_METHODS:
        return True
    return bool(request.headers.get(CSRF_HEADER))


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=_TOKEN_TTL, httponly=True, samesite="lax",
        secure=is_production_env(), path="/")


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def resolve_account(request: Request):
    """从请求解析当前账号身份，区分来源，不做静默降级。

    返回 dict：{"email", "role", "tenant", "source"}；匿名时 role/tenant=None
    （由调用方决定拒绝方式）。
      - source="token"  ：Bearer 令牌验签通过；tenant 取自令牌载荷
      - source="cookie" ：HttpOnly 会话 Cookie 中的令牌验签通过（同一种令牌，
                          写请求另需 CSRF 自定义头，见 csrf_ok）
      - source="header" ：X-Role 头声明（仅在 AUTH_ENABLED=0 的 demo 免密模式）；
                          没有真实身份，email 固定为「角色@demo.hk」的演示身份，
                          不再采信客户端自报的 X-Email（审计里的操作人不可伪造）
      - source=None     ：匿名。

    AUTH_ENABLED=1（生产）时不回落 X-Role：身份只能来自令牌。
    """
    tok, origin = _request_token(request)
    if tok:
        parsed = parse_token(tok)
        if parsed:
            return {"email": parsed[0], "role": parsed[1], "tenant": parsed[2],
                    "source": origin}
    if auth_enabled():
        return {"email": None, "role": None, "tenant": None, "source": None}
    role = request.headers.get("X-Role")
    if role in ROLE_RANK:
        return {"email": f"{role}@demo.hk",
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

_UNAUTHENTICATED_DETAIL = "请先登录。你还没有登录，或登录已经过期，请重新登录后再操作。"


def _reject_anonymous():
    """匿名请求走 401（未认证），与 403（已认证但权限不足）语义分开。"""
    raise HTTPException(status_code=401, detail=_UNAUTHENTICATED_DETAIL)


def _forbidden_detail(cur: str, need: str) -> str:
    need_label = _ROLE_LABEL.get(need, need)
    cur_label = _ROLE_LABEL.get(cur, cur)
    if need == "admin":
        return f"权限不足：这个操作只有超管能做，你现在是{cur_label}。请用超管账号登录，或联系管理员。"
    return f"权限不足：这个操作需要{need_label}或更高权限，你现在是{cur_label}。请联系{need_label}来操作。"


def require_role(role: str):
    """FastAPI 依赖：请求者角色层级 >= 目标角色。403 文案人话统一（含当前角色与所需角色指引）。"""
    def _dep(request: Request):
        account = resolve_account(request)
        cur = account.get("role")
        if cur is None:
            _reject_anonymous()
        if ROLE_RANK.get(cur, 0) < ROLE_RANK.get(role, 99):
            raise HTTPException(status_code=403, detail=_forbidden_detail(cur, role))
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
        raise HTTPException(status_code=403, detail=_forbidden_detail(cur, "admin"))
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
_WHITELIST_EXACT = frozenset({"/api/auth/login", "/api/auth/me", "/api/auth/logout", "/api/health",
                              "/api/csp-report"})
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
        from app.errors import error_response as _error_response

        request = _StarletteRequest(scope, receive=receive)
        account = resolve_account(request)
        if account.get("role") is None:
            response = _error_response(401, "UNAUTHENTICATED", _UNAUTHENTICATED_DETAIL)
            await response(scope, receive, send)
            return
        if not csrf_ok(request, account):
            response = _error_response(
                403, "CSRF_REJECTED",
                "这次操作来源不明，已被拦下。请回到本系统页面重新操作；如果一直这样，请重新登录。")
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
        finally:
            _db.set_current_tenant_id(None)

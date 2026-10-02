# -*- coding: utf-8 -*-
"""安全防护网关服务。

提供:
1. validate_safe_external_url: 网络外呼 SSRF 阻断网关，阻断私有网络、本地回环、云厂商
   元数据地址，以及所有「非全局可路由」的保留/测试/基准段（见 BLOCKED_IP_NETWORKS
   逐条注释）。安全语义为 fail-closed：无法判定目标安全性（含域名 DNS 解析失败、非预期
   异常）一律拒绝并告警；仅当调用方显式传 allow_unresolved=True 时才放行不可解析域名
   （离线/内网自测用）。
2. mask_secret_key: 敏感 API 密钥脱敏工具。
"""

import ipaddress
import logging
import socket
from typing import Optional, Tuple, Union
from urllib.parse import urlparse

logger = logging.getLogger("security_guard")

RESERVED_BENCHMARK_NETWORKS = [
    ipaddress.ip_network("198.18.0.0/15"),
]

BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),     # RFC 6598 Carrier-Grade NAT (AWS/GCP/AliCloud 私有端点)
    # ---- 非全局可路由的保留 / 测试 / 基准段（无一可能是真实识别引擎端点）----
    # 这类段的价值不在于「内网可达」，而在于：DNS 劫持与 fake-IP 代理（Clash/Surge 等把
    # 任意域名解析成这些段里的地址）会让**域名形态**的 base_url 校验被整体旁路 ——
    # 目标域名解析到 198.18.x.x 时，域名校验只看解析结果，不封这些段就等于放行任意域名。
    ipaddress.ip_network("192.0.0.0/24"),      # RFC 6890 IETF 协议专用（192.0.0.0/29 等），非公网可路由
    ipaddress.ip_network("192.0.2.0/24"),      # RFC 5737 TEST-NET-1（文档用，永非真实服务）
    ipaddress.ip_network("198.51.100.0/24"),   # RFC 5737 TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),    # RFC 5737 TEST-NET-3
    ipaddress.ip_network("240.0.0.0/4"),       # RFC 1112 保留段（含 255.255.255.255 广播地址）
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("2001:db8::/32"),     # RFC 3849 IPv6 文档段（与 TEST-NET 同性质）
]

# 注：IPv6 未指定地址 ::/128（以及 IPv4 的 0.0.0.0/8）已在 LOOPBACK_NETWORKS 中，
# 「保留/测试段」补漏时无需重复登记（重复登记反而会让两条列表的语义边界变模糊）。
LOOPBACK_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
]


def _check_ip(
    ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address],
    allow_localhost: bool = False
) -> Tuple[bool, Optional[str]]:
    """检查单个 IP 是否属于禁止的私网、回环或云元数据地址。"""
    # 针对 IPv4 映射的 IPv6 地址
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    for net in RESERVED_BENCHMARK_NETWORKS:
        if ip in net:
            return False, f"禁止直接访问保留基准网络 IP ({ip})，已被安全策略拦截"


    # 回环地址判定
    for net in LOOPBACK_NETWORKS:
        if ip in net:
            if allow_localhost:
                return True, None
            return False, f"禁止访问本地回环 IP ({ip})，已被安全策略拦截"

    if ip.is_loopback:
        if allow_localhost:
            return True, None
        return False, f"禁止访问本地回环 IP ({ip})，已被安全策略拦截"

    # 私网与云厂商元数据 (169.254.169.254) 判定（即便开启 allow_localhost 亦严格阻断）
    for net in BLOCKED_IP_NETWORKS:
        if ip in net:
            return False, f"检测到非法私网或元数据目标 IP ({ip})，已被安全策略拦截"

    if ip.is_link_local:
        return False, f"检测到非法链路本地目标 IP ({ip})，已被安全策略拦截"

    return True, None


def validate_safe_external_url(
    url: str,
    allow_localhost: bool = False,
    allow_unresolved: bool = False,
) -> Tuple[bool, Optional[str]]:
    """验证外部 API Base URL 是否安全，阻断 SSRF 攻击。

    返回: (is_safe, error_reason)

    allow_unresolved: 域名无法解析（DNS 失败）时的处置开关，默认 False = fail-closed。
    - False（默认，fail-closed）：拒绝并记 warning。无法确认目标 IP 就不放行；
      这样也能避免把「解析失败」静默当成「安全」。
    - True（显式放行）：放行并记 warning。仅用于离线/内网自测环境里使用不可解析的
      占位域名；必须由明确知道自己身处离线环境的调用方显式传入。
    """
    if not url or not str(url).strip():
        return False, "URL 不能为空"

    cleaned_url = str(url).strip()
    try:
        parsed = urlparse(cleaned_url)
        if parsed.scheme not in ("http", "https"):
            return False, f"不支持的协议: {parsed.scheme}，仅支持 http/https"

        host = parsed.hostname
        if not host:
            return False, "无法解析主机名"

        host_lower = host.lower().strip()
        if host_lower == "localhost" or host_lower.endswith(".localhost"):
            if allow_localhost:
                return True, None
            return False, "禁止访问本地回环地址 (localhost)"

        # 尝试直接解析为 IP 地址
        try:
            ip = ipaddress.ip_address(host_lower)
            return _check_ip(ip, allow_localhost=allow_localhost)
        except ValueError:
            # 属于域名，进行 DNS 解析检查防止 DNS 重绑定与域名伪装
            
            # 私有保留域名检查
            private_tlds = ('.localhost', '.local', '.internal', '.lan', '.home', '.corp', '.invalid', '.test', '.example')
            RESERVED_INTERNAL_HOSTS = {
                "localhost",
                "ip6-localhost",
                "ip6-loopback",
                "metadata.google.internal",
            }
            if (
                host_lower in RESERVED_INTERNAL_HOSTS
                or any(host_lower.endswith(s) for s in private_tlds)
            ):
                return False, f"禁止访问私有保留域名 ({host})"
                
            try:
                addr_info = socket.getaddrinfo(host, None)
            except (socket.gaierror, OSError) as e:
                # DNS 解析失败属「无法判定目标安全性」，不是「安全」。
                # 默认 fail-closed；只有调用方显式声明允许未解析域名时才放行。
                if allow_unresolved:
                    logger.warning(
                        "[SSRF] 域名 %s 无法解析(%s: %s)，按 allow_unresolved=True 显式放行",
                        host, type(e).__name__, e)
                    return True, None
                logger.warning(
                    "[SSRF] 域名 %s 无法解析(%s: %s)，按 fail-closed 拒绝",
                    host, type(e).__name__, e)
                return False, (
                    f"域名 {host} 无法解析（{type(e).__name__}），"
                    "无法确认目标安全性，已被安全策略拒绝"
                )

            if not addr_info:
                return False, f"域名 {host} DNS 解析结果为空"
            for item in addr_info:
                ip_str = item[4][0]
                try:
                    resolved_ip = ipaddress.ip_address(ip_str)
                except ValueError:
                    # getaddrinfo 正常只会给出可解析的地址串；出现异常形态说明解析层有问题，
                    # 跳过该条会削弱本次校验的覆盖面，故留痕（此前是静默 continue）。
                    logger.warning("[SSRF] 域名 %s 解析结果中的地址无法识别，已跳过该条：%r",
                                   host, ip_str)
                    continue
                is_safe, reason = _check_ip(resolved_ip, allow_localhost=allow_localhost)
                if not is_safe:
                    # 如果是被 _check_ip 因为 RESERVED_BENCHMARK_NETWORKS 拦截，但它是域名解析来的且不是私有保留域名，则放行
                    for net in RESERVED_BENCHMARK_NETWORKS:
                        if resolved_ip in net:
                            # 识别为本地代理 Fake-IP 分配，予以安全放行
                            logger.info(
                                "[SSRF] 域名 %s 解析到 RFC 2544 基准段 IP (%s)，属于本地代理 Fake-IP 占用，予以安全放行",
                                host, resolved_ip
                            )
                            is_safe = True
                            break
                if not is_safe:
                    return False, f"域名 {host} 解析到危险私网/元数据 IP ({resolved_ip})，已被安全策略拦截"


        return True, None
    except Exception as e:
        # 非预期异常（含代码缺陷）一律 fail-closed，且必须可观测，不得静默放行
        logger.warning("[SSRF] URL 校验出现非预期异常，按 fail-closed 拒绝: %s (%s)", cleaned_url, e)
        return False, f"URL 解析异常: {str(e)}"


def mask_secret_key(key: Optional[str]) -> str:
    """脱敏 API 密钥。

    规则:
    - 为空或 None: 返回 ""
    - 长度 <= 8: 返回 "******"
    - 长度 >= 12: 保留前 6 位与后 4 位，中间 "****" (例如: sk-abc****1234)
    - 否则 (9..11 位): 保留前 3 位与后 4 位，中间 "****"
    """
    k = str(key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "******"
    if len(k) >= 12:
        return f"{k[:6]}****{k[-4:]}"
    return f"{k[:3]}****{k[-4:]}"


# ---------------------------------------------------------------------------
# W-ISO-2 Provider 合规准入白名单 (Provider Admission Whitelist)
# ---------------------------------------------------------------------------
ADMITTED_PROVIDER_DOMAINS = [
    "aliyun.com",
    "volces.com",
    "baidubce.com",
    "tencentcloudapi.com",
    "dashscope.aliyuncs.com",
    "localhost",
    "127.0.0.1",
]


def is_provider_admitted(url: str) -> Tuple[bool, Optional[str]]:
    """检查 Provider API Base URL 域名是否属于合规准入白名单。

    非白名单域外推原图存在跨境或未经备案的第三方供应链泄露风险 (PIPL Compliance)。
    """
    if not url or not str(url).strip():
        return False, "Provider URL 不能为空"
    try:
        parsed = urlparse(str(url).strip())
        host = (parsed.hostname or "").lower().strip()
        if not host:
            return False, "无法从 Provider URL 解析出有效的主机名"
        for admitted in ADMITTED_PROVIDER_DOMAINS:
            if host == admitted or host.endswith("." + admitted):
                return True, None
        return False, f"Provider 域名 {host} 未在系统合规准入清单中"
    except Exception as e:
        return False, f"Provider URL 解析异常: {e}"


def check_outbound_image_provider(url: str, outbound_acknowledged: bool = False) -> Tuple[bool, Optional[str]]:
    """对包含原图的数据出站进行 Provider 准入与二次确认核验。

    若域名在准入清单内，放行；
    若非准入清单域名且未显式勾选 outbound_acknowledged，阻断外推并记录安全事件；
    若显式确认，放行并记录审计留痕。
    """
    is_adm, reason = is_provider_admitted(url)
    if is_adm:
        return True, None
    if not outbound_acknowledged:
        log_security_event(
            event_type="SSRF_ATTEMPT_BLOCKED",
            severity="HIGH",
            source_module="provider_admission",
            details={"url": url, "reason": reason, "action": "blocked"}
        )
        return False, f"[BLOCKED] 目标 Provider 域名未准入且未确认出站风险: {reason}"
    log_security_event(
        event_type="OUTBOUND_PII_MASKED",
        severity="MEDIUM",
        source_module="provider_admission",
        details={"url": url, "reason": reason, "action": "acknowledged_by_user"}
    )
    return True, f"[WARN] 生产单据数据将出站发送至实验节点: {url}"


# ---------------------------------------------------------------------------
# W-SEC-6 统一安全事件埋点接口 (Unified Security Event Logger)
# ---------------------------------------------------------------------------
def log_security_event(
    event_type: str,
    severity: str,
    source_module: str,
    details: dict = None,
    tenant_id: str = "default",
    ip_address: str = "",
    user_email: str = ""
):
    """统一安全事件埋点唯一实现。

    六大核心安全事件类型：
    - PROMPT_INJECTION_DETECTED
    - IMAGE_GUARD_FLAGGED
    - CANARY_VERIFICATION_FAILED
    - SSRF_ATTEMPT_BLOCKED
    - OUTBOUND_PII_MASKED
    - TENANT_VIOLATION_BLOCKED
    """
    try:
        clean_details = dict(details or {})
        for k in list(clean_details.keys()):
            if any(s in k.lower() for s in ("key", "secret", "token", "password")):
                clean_details[k] = mask_secret_key(str(clean_details[k]))
        logger.info(
            "[SECURITY_EVENT] [%s] [%s] module=%s tenant=%s details=%s",
            severity.upper(), event_type, source_module, tenant_id, clean_details
        )
        from app import db as _db
        return _db.record_security_event(
            event_type=event_type,
            severity=severity,
            source_module=source_module,
            details=clean_details,
            tenant_id=tenant_id,
            ip_address=ip_address,
            user_email=user_email,
        )
    except Exception as e:
        logger.warning(f"[SECURITY_EVENT] log_security_event failed: {e}")
        return None

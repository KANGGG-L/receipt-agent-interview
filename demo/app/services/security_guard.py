# -*- coding: utf-8 -*-
"""安全防护网关服务。

提供:
1. validate_safe_external_url: 网络外呼 SSRF 阻断网关，阻断私有网络、本地回环与云厂商元数据地址。
   安全语义为 fail-closed：无法判定目标安全性（含域名 DNS 解析失败、非预期异常）一律拒绝并告警；
   仅当调用方显式传 allow_unresolved=True 时才放行不可解析域名（离线/内网自测用）。
2. mask_secret_key: 敏感 API 密钥脱敏工具。
"""

import ipaddress
import logging
import socket
from typing import Optional, Tuple, Union
from urllib.parse import urlparse

logger = logging.getLogger("security_guard")

BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),     # RFC 6598 Carrier-Grade NAT (AWS/GCP/AliCloud 私有端点)
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

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
    # 针对 IPv4 映射的 IPv6 地址 (如 ::ffff:127.0.0.1) 转为原生 IPv4 判定
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

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

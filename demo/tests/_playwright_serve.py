# -*- coding: utf-8 -*-
"""以「环境锁定」方式启动被测本地服务（含域名 DNS 解析固定）。

为什么需要在**服务进程内**再锁一次 DNS：`security_guard.validate_safe_external_url`
对域名先做 DNS 解析、再按 IP 段判安全性。本机若启用了 fake-IP DNS 代理（把任意域名解析
成 198.18.0.0/15 —— RFC 2544 基准段），一切域名形态的 base_url 都会被 fail-closed 拒绝。
那是「本机解析器行为泄漏进被测进程」，不是产品缺陷：根套件早已用同一把锁
（见 tests/conftest.py 的 `_pin_dns_resolution`，D20）把解析固定为公网地址来消除这道
隐藏变量。被测服务运行在独立子进程里，pytest 进程内的 monkeypatch 到不了它，故此处把
同一把锁装进服务进程：域名解析固定为公网地址 1.1.1.1，字面量 IP（含 127.0.0.1 绑定）
原样透传给原始解析器，断言口径与「真实公网解析」下的行为完全一致，不涉及任何断言弱化。

由 `_playwright_server.py` 以 `python _playwright_serve.py` 方式拉起，端口沿用 15010。
"""

import os
import socket
import sys

_ORIG_GETADDRINFO = socket.getaddrinfo
_PIN_IP = "1.1.1.1"
_PORT = int(os.environ.get("PW_SERVER_PORT", "15010"))


def _is_ip_literal(host):
    try:
        import ipaddress
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _pinned_getaddrinfo(host, port, *args, **kwargs):
    # 字面量 IP / 空 host / localhost 一律走原始解析器：服务自身 bind 127.0.0.1 不受影响，
    # 只把「域名」形态的解析固定到公网地址。
    if not host or host == "localhost" or _is_ip_literal(host):
        return _ORIG_GETADDRINFO(host, port, *args, **kwargs)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PIN_IP, int(port or 0)))]


def main():
    socket.getaddrinfo = _pinned_getaddrinfo
    if os.environ.get("PW_TEST_BLOCK_OUTBOUND", "1") == "1":
        _orig_connect = socket.socket.connect
        def _isolated_connect(self, address):
            if isinstance(address, tuple) and address[0] not in ('127.0.0.1', 'localhost', '::1'):
                raise OSError("Isolated test mode: outbound network blocked (%s)" % (address[0],))
            return _orig_connect(self, address)
        socket.socket.connect = _isolated_connect

    # 禁止付费推理与外网调用，默认哈希 embedding
    os.environ.setdefault("OPENAI_API_KEY", "")
    os.environ.setdefault("SILICONFLOW_API_KEY", "")
    os.environ.setdefault("QWEN_API_KEY", "")
    os.environ.setdefault("RAG_EMBEDDING_PROVIDER", "hash")
    os.environ.setdefault("MEMORY_EXTRACT_ENABLED", "0")

    demo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if demo_dir not in sys.path:
        sys.path.insert(0, demo_dir)
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=_PORT)


if __name__ == "__main__":
    main()

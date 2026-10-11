# -*- coding: utf-8 -*-
"""静态资源与响应头：内容 hash 版本参数、缓存策略、安全响应头、CSP 违例收集。"""

import hashlib
import logging
import re
import threading
import time
from collections import Counter
from pathlib import Path

from fastapi.responses import HTMLResponse
from starlette.staticfiles import StaticFiles

logger = logging.getLogger("app.web_assets")

_ASSET_RE = re.compile(
    r'(?P<attr>\b(?:src|href)=)(?P<q>["\'])(?P<path>/static/[^"\'?#]+\.(?:js|css))'
    r'(?:\?[^"\'#]*)?(?P=q)')

_hash_cache = {}
_hash_lock = threading.Lock()


def file_version(path: Path) -> str:
    """文件内容 hash 的前 10 位；按 (mtime, size) 缓存，文件变化即重算。"""
    try:
        st = path.stat()
    except OSError:
        return "0"
    key = str(path)
    stamp = (st.st_mtime_ns, st.st_size)
    with _hash_lock:
        hit = _hash_cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    with _hash_lock:
        _hash_cache[key] = (stamp, digest)
    return digest


def inject_asset_versions(html: str, static_dir: Path) -> str:
    """把页面里 /static/*.js|css 的版本参数替换成文件内容 hash（手写的 ?v= 一并覆盖）。"""
    def _sub(m):
        rel = m.group("path")[len("/static/"):]
        target = (static_dir / rel).resolve()
        try:
            target.relative_to(static_dir.resolve())
        except ValueError:
            return m.group(0)
        if not target.is_file():
            return m.group(0)
        return f'{m.group("attr")}{m.group("q")}{m.group("path")}?v={file_version(target)}{m.group("q")}'
    return _ASSET_RE.sub(_sub, html)


def html_response(template: Path, static_dir: Path, dev_mode: bool, transform=None) -> HTMLResponse:
    html = template.read_text(encoding="utf-8")
    if transform is not None:
        html = transform(html)
    html = inject_asset_versions(html, static_dir)
    resp = HTMLResponse(content=html)
    if dev_mode:
        resp.headers.update({"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})
    else:
        resp.headers["Cache-Control"] = "no-cache"
    return resp


class AssetStaticFiles(StaticFiles):
    """静态文件：开发态 no-store（ETag 仍随响应下发）；生产态带 ?v= 的请求长缓存 immutable。"""

    def __init__(self, *args, dev_mode: bool = True, **kwargs):
        self._dev_mode = dev_mode
        super().__init__(*args, **kwargs)

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code >= 400:
            return response
        if self._dev_mode:
            response.headers.update({"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})
        elif b"v=" in (scope.get("query_string") or b""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


CSP_REPORT_PATH = "/api/csp-report"
CSP_REPORT_ONLY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
    f"report-uri {CSP_REPORT_PATH}"
)

SECURITY_HEADERS = (
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(self), microphone=(), geolocation=(), payment=()"),
    (b"content-security-policy-report-only", CSP_REPORT_ONLY.encode()),
)


class SecurityHeadersMiddleware:
    """纯 ASGI：给所有 HTTP 响应补安全头（已显式设置的不覆盖）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {k.lower() for k, _ in headers}
                for k, v in SECURITY_HEADERS:
                    if k not in present:
                        headers.append((k, v))
                message = dict(message, headers=headers)
            await send(message)

        await self.app(scope, receive, _send)


_csp_counter = Counter()
_csp_lock = threading.Lock()
_csp_first_seen = {}


def record_csp_report(report: dict) -> None:
    """记录一条 CSP 违例（只统计 指令 + 来源 的计数，不存页面内容）。"""
    body = report.get("csp-report") if isinstance(report, dict) else None
    if not isinstance(body, dict):
        body = report if isinstance(report, dict) else {}
    directive = str(body.get("effective-directive") or body.get("violated-directive") or "unknown")[:80]
    blocked = str(body.get("blocked-uri") or body.get("blockedURL") or "")[:120]
    key = f"{directive} | {blocked or 'inline'}"
    with _csp_lock:
        _csp_counter[key] += 1
        _csp_first_seen.setdefault(key, time.strftime("%Y-%m-%d %H:%M:%S"))
        if len(_csp_counter) > 500:
            for k, _ in _csp_counter.most_common()[:-250]:
                _csp_counter.pop(k, None)
    logger.info("[csp-report] %s", key)


def csp_report_summary() -> list:
    with _csp_lock:
        return [{"violation": k, "count": c, "first_seen": _csp_first_seen.get(k, "")}
                for k, c in _csp_counter.most_common(100)]

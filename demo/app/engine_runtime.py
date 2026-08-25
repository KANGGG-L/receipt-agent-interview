# -*- coding: utf-8 -*-
from __future__ import annotations
"""Layer 3 — 常驻进程引擎管理器（消除每次模型调用的 CLI 冷启动）。

设计要点（对齐根治计划 §3.1 / §3.3）：
- 默认不启用：长驻进程需用户显式把 transport 切到 "persistent" 才启动；
  否则本模块保持休眠，模型仍走 subprocess 快路径（向后兼容）。
- 懒启动：首次请求时按配置拉起 opencode/codebuddy 的 ACP/serve 进程，
  暴露一个 OpenAI 兼容的 HTTP completions 端点。
- 健康探活 + 崩溃自动重启：探活失败或进程退出则重建进程。
- 失败即回退：任何异常（启动失败 / 探活失败 / 请求失败 / 超时）统一抛出
  `PersistentEngineUnavailable`，由上层模型工厂（§3.2）捕获后回退到 subprocess 模式，
  保证可用性绝不劣于现状。
- 并发安全：单进程内用 threading.Lock 串行化 HTTP 调用，多 receipt 并发时排队。

本模块只负责「进程生命周期 + HTTP 调用」，不依赖 LangChain；消息以
OpenAI chat 格式（[{"role","content"}]）传入，串行化由调用方完成。
"""

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

import requests

logger = logging.getLogger("engine_runtime")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18731
DEFAULT_COMPLETIONS_PATH = "/v1/chat/completions"
DEFAULT_STARTUP_TIMEOUT = 30.0  # 拉起进程后等待就绪的最长秒数
DEFAULT_REQUEST_TIMEOUT = 90.0  # 单次 complete 的 HTTP 超时（秒）
HEALTH_PATH = "/health"


class PersistentEngineUnavailable(Exception):
    """长驻进程不可用。

    语义：调用方必须回退到 subprocess 模式。本管理器自身不执行 subprocess 回退，
    以保持职责单一；回退逻辑集中在模型工厂（§3.2）。
    """


@dataclass
class _EngineInstance:
    """单个常驻进程的运行态。"""

    proc: subprocess.Popen
    port: int
    base_url: str
    bin_path: str
    cmd: List[str]
    kind: str
    started_at: float = field(default_factory=time.time)


class PersistentEngineManager:
    """模块级单例的常驻进程管理器。

    典型用法（由模型工厂在 transport=persistent 时调用）：

        mgr = get_persistent_manager()
        mgr.configure(kind="opencode", bin_path="/path/opencode", port=18731)
        try:
            text = mgr.complete(messages, model="opencode/mimo-v2.5-free")
        except PersistentEngineUnavailable:
            text = _run_subprocess(...)  # 回退
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: Optional[_EngineInstance] = None
        self._enabled = False
        self._kind = "opencode"
        self._bin_path: Optional[str] = None
        self._host = DEFAULT_HOST
        self._port = DEFAULT_PORT
        self._completions_path = DEFAULT_COMPLETIONS_PATH
        self._startup_timeout = DEFAULT_STARTUP_TIMEOUT
        self._request_timeout = DEFAULT_REQUEST_TIMEOUT
        self._probes = 0
        self._restarts = 0

    # ----- 配置 -----

    def configure(
        self,
        enabled: bool,
        kind: str = "opencode",
        bin_path: Optional[str] = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        completions_path: str = DEFAULT_COMPLETIONS_PATH,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        """设置是否启用长驻进程及其启动参数。

        enabled=False（默认）时任何 complete() 都直接抛 PersistentEngineUnavailable，
        由调用方走 subprocess，保证默认零影响。
        """
        with self._lock:
            self._enabled = bool(enabled)
            self._kind = kind
            self._bin_path = bin_path
            self._host = host
            self._port = port
            self._completions_path = completions_path
            self._startup_timeout = startup_timeout
            self._request_timeout = request_timeout
            # 配置变更视为需要重建进程（下次请求懒重启）
            if self._instance is not None:
                self._teardown_instance()
        logger.info(
            "[engine_runtime] configured enabled=%s kind=%s bin=%s port=%s",
            self._enabled, self._kind, self._bin_path, self._port,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ----- 生命周期 -----

    def ensure_started(self) -> _EngineInstance:
        """线程安全地确保常驻进程已就绪；惰性启动 + 崩溃重建。"""
        with self._lock:
            if not self._enabled:
                raise PersistentEngineUnavailable("长驻进程未启用（transport=subprocess）")
            if self._instance is not None and self._is_proc_alive(self._instance):
                if self._probe_health(self._instance):
                    return self._instance
                # 进程在但探活失败 → 重建
                logger.warning("[engine_runtime] 进程存活但探活失败，重建")
                self._teardown_instance()
            return self._spawn_locked()

    def _spawn_locked(self) -> _EngineInstance:
        """在持有 _lock 的情况下拉起进程并等待就绪。"""
        if not self._bin_path:
            raise PersistentEngineUnavailable("未配置长驻进程 bin 路径")
        cmd = self._build_cmd()
        logger.info("[engine_runtime] 启动常驻进程: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except (OSError, ValueError) as exc:
            raise PersistentEngineUnavailable(f"启动失败: {exc}")

        instance = _EngineInstance(
            proc=proc,
            port=self._port,
            base_url=f"http://{self._host}:{self._port}",
            bin_path=self._bin_path,
            cmd=cmd,
            kind=self._kind,
        )
        self._instance = instance
        self._restarts += 1

        # 等待就绪：轮询探活，超时则放弃并回退
        deadline = time.time() + self._startup_timeout
        while time.time() < deadline:
            if not self._is_proc_alive(instance):
                stderr = self._drain_stderr(instance)
                raise PersistentEngineUnavailable(f"进程启动即退出: {stderr[:300]}")
            if self._probe_health(instance):
                logger.info("[engine_runtime] 常驻进程就绪 port=%s", self._port)
                return instance
            time.sleep(0.3)
        self._teardown_instance()
        raise PersistentEngineUnavailable("启动后探活超时，回退 subprocess")

    def _build_cmd(self) -> List[str]:
        """根据 kind 构造启动命令。

        期望目标进程暴露 OpenAI 兼容的 HTTP completions 端点
        （opencode/codebuddy 的 acp/serve 模式通常支持）。
        """
        if self._kind == "codebuddy":
            return [self._bin_path, "--acp", "--port", str(self._port)]
        # 默认 opencode
        return [self._bin_path, "acp", "--port", str(self._port), "--host", self._host]

    def _is_proc_alive(self, inst: _EngineInstance) -> bool:
        return inst.proc.poll() is None

    def _probe_health(self, inst: _EngineInstance) -> bool:
        """HTTP 探活（GET /health，若无则试探 completions 端点返回 405/400 之类非连接错误即视为就绪）。"""
        self._probes += 1
        try:
            resp = requests.get(f"{inst.base_url}{HEALTH_PATH}", timeout=2.0)
            return resp.status_code < 500
        except requests.RequestException:
            # /health 不存在不代表未就绪；用一次轻量 OPTIONS/GET 试探根路径
            try:
                requests.get(inst.base_url, timeout=2.0)
                return True
            except requests.RequestException:
                return False

    def _drain_stderr(self, inst: _EngineInstance) -> str:
        try:
            return (inst.proc.stderr.read() or "") if inst.proc.stderr else ""
        except Exception:
            return ""

    def _teardown_instance(self) -> None:
        inst = self._instance
        self._instance = None
        if inst is None:
            return
        try:
            inst.proc.terminate()
            try:
                inst.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                inst.proc.kill()
        except Exception:
            pass

    def shutdown(self) -> None:
        """显式关闭常驻进程（进程退出 / 配置变更时调用）。"""
        with self._lock:
            self._teardown_instance()

    # ----- 调用 -----

    def complete(
        self,
        messages: List[dict],
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """向常驻进程发起一次补全，返回文本。

        任何失败（未启用 / 启动失败 / 探活失败 / HTTP 错误 / 超时 / 解析失败）
        统一抛 PersistentEngineUnavailable，调用方据此回退 subprocess。
        """
        if not self._enabled:
            raise PersistentEngineUnavailable("长驻进程未启用（transport=subprocess）")

        # 串行化多并发请求（单进程内排队）
        with self._lock:
            try:
                inst = self.ensure_started()
            except PersistentEngineUnavailable:
                raise

            url = f"{inst.base_url}{self._completions_path}"
            payload = {"messages": messages, "stream": True}
            if model:
                payload["model"] = model
            req_timeout = timeout if timeout is not None else self._request_timeout

            try:
                resp = requests.post(url, json=payload, timeout=req_timeout, stream=True)
            except requests.RequestException as exc:
                # 请求失败 → 标记进程需重建（下次请求自动重启），并回退
                logger.warning("[engine_runtime] 请求失败，触发重建: %s", exc)
                self._teardown_instance()
                raise PersistentEngineUnavailable(f"请求失败: {exc}")

            if resp.status_code >= 400:
                body = resp.text[:300]
                self._teardown_instance()
                raise PersistentEngineUnavailable(f"HTTP {resp.status_code}: {body}")

            return self._parse_stream(resp)

    def _parse_stream(self, resp: "requests.Response") -> str:
        """解析 SSE / NDJSON 流，拼接 choices[].delta.content。

        兼容两种常见形态：
        - SSE：`data: {json}`（含 `[DONE]` 终止）
        - 非流式 JSON：直接返回 choices[0].message.content
        """
        chunks: List[str] = []
        try:
            # 非流式（stream=false 被忽略时）直接 JSON
            ctype = resp.headers.get("Content-Type", "")
            text_preview = ""
            for line in resp.iter_lines(decode_unicode=True):
                if line is None:
                    continue
                s = line.strip()
                if not s:
                    continue
                if s.startswith("data:"):
                    s = s[len("data:"):].strip()
                if s == "[DONE]":
                    break
                try:
                    obj = json.loads(s)
                except ValueError:
                    # 可能是纯文本增量
                    chunks.append(s)
                    continue
                # OpenAI 流式格式
                choices = obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    c = delta.get("content")
                    if c:
                        chunks.append(c)
                # 非流式兜底
                if not chunks and obj.get("choices"):
                    msg = obj["choices"][0].get("message") or {}
                    c = msg.get("content")
                    if c:
                        chunks.append(c)
            return "".join(chunks).strip()
        except requests.RequestException as exc:
            raise PersistentEngineUnavailable(f"流读取失败: {exc}")
        except Exception as exc:  # 解析异常，回退
            raise PersistentEngineUnavailable(f"响应解析失败: {exc}")


# 模块级单例
_MANAGER: Optional[PersistentEngineManager] = None


def get_persistent_manager() -> PersistentEngineManager:
    """获取（惰性创建）模块级单例。"""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = PersistentEngineManager()
    return _MANAGER

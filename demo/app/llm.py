# -*- coding: utf-8 -*-
from __future__ import annotations
"""LangChain 模型封装：CodeBuddy / opencode CLI 包装为 BaseChatModel + OpenAI 兼容。

设计：
- 面试叙事「模型可插拔」：Supervisor 只依赖 LangChain ChatModel 接口，
  识别引擎换 CodeBuddy / opencode / 任何 OpenAI 兼容模型都不改管线代码。
- CodeBuddyChatModel：包装本机 `codebuddy -p` 子进程（免费多模态，读图）。
- OpencodeChatModel：包装本机 `opencode run` 子进程（支持任意 opencode 模型，
  视觉模型如 MiMo-V2.5 Free 可读图）。
- OpenAIChatModel：自定义 OpenAI 兼容接口（base_url + api_key + model），
  支持多模态图片（data URL）。由 admin 在引擎配置界面填写。
"""

import base64
import json
import logging
import os
import subprocess
import time

import requests
from dotenv import load_dotenv
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

load_dotenv()

CODEBUDDY_DEFAULT_BIN = "/Users/ethan/.nvm/versions/node/v24.16.0/bin/codebuddy"
OPENCODE_DEFAULT_BIN = "/Users/ethan/.opencode/bin/opencode"
CALL_TIMEOUT_SECONDS = 240  # 模型字段默认回落值（历史硬上限）
DEFAULT_CALL_TIMEOUT = 90   # 超时可控：缺省 90s 快速失败


def _resolve_timeout(cfg=None):
    """解析单引擎调用超时（秒）。

    优先级：EngineConfig.call_timeout_seconds > env ENGINE_CALL_TIMEOUT > 90s 缺省。
    即便某引擎变慢，也按此值快速失败而非阻塞 240s。
    """
    if cfg is not None:
        v = getattr(cfg, "call_timeout_seconds", None)
        if isinstance(v, int) and v > 0:
            return v
    env = os.environ.get("ENGINE_CALL_TIMEOUT")
    if env:
        try:
            iv = int(env)
            if iv > 0:
                return iv
        except (ValueError, TypeError):
            pass
    return DEFAULT_CALL_TIMEOUT


def _get_codebuddy_bin():
    return os.environ.get("CODEBUDDY_BIN", CODEBUDDY_DEFAULT_BIN)


def _get_opencode_bin():
    return os.environ.get("OPENCODE_BIN", OPENCODE_DEFAULT_BIN)


# -------------------------------------------------------------
# 长驻进程 transport（Layer 3, §3.2）：仅当模型 transport=persistent 时启用，
# 调用 engine_runtime.PersistentEngineManager；任何不可用都回退 subprocess。
# 默认 transport=subprocess，本函数不会被触发，保证零影响、向后兼容。
# -------------------------------------------------------------
def _invoke_persistent(model, messages):
    """transport=persistent 时走常驻进程；异常一律回退 subprocess 子进程模式。

    model 须提供：_runtime_kind(str)、_bin()、model、call_timeout、
    _messages_to_prompt(messages)、_run_cli(prompt)。
    """
    from app.engine_runtime import get_persistent_manager, PersistentEngineUnavailable

    logger = logging.getLogger("llm")
    mgr = get_persistent_manager()
    # 仅在未启用时才 configure，避免每次调用都重建常驻进程
    if not mgr.enabled:
        mgr.configure(
            enabled=True,
            kind=getattr(model, "_runtime_kind", "opencode"),
            bin_path=model._bin(),
            request_timeout=model.call_timeout,
        )
    try:
        openai_msgs = [_lc_to_openai(m) for m in messages]
        content = mgr.complete(openai_msgs, model=model.model, timeout=model.call_timeout)
    except PersistentEngineUnavailable as exc:
        logger.warning("[llm] 长驻进程不可用，回退 subprocess: %s", exc)
        return model._run_cli(model._messages_to_prompt(messages))
    # 风险与回退（§3.3）：常驻进程返回空/空白内容视为不可用，回退子进程模式，
    # 避免静默降级——保证可用性绝不劣于现状的 subprocess 路径。
    if not content or not content.strip():
        logger.warning("[llm] 长驻进程返回空内容，回退 subprocess")
        return model._run_cli(model._messages_to_prompt(messages))
    return content


class CodeBuddyChatModel(BaseChatModel):
    """把本机 CodeBuddy CLI 包装为 LangChain ChatModel。

    支持多模态：HumanMessage 可携带 image_url（data URL / 本地绝对路径）。
    图片传本地路径时由 CLI 读图（绕过 LangChain 的 base64 解码限制）。
    """

    model: str = "minimax-m3-pay"
    temperature: float = 0.01
    session_id: str = None  # 复用长会话，加速 + 上下文累计
    call_timeout: int = CALL_TIMEOUT_SECONDS  # 单引擎调用超时（秒），由 _build 注入
    transport: str = "subprocess"  # subprocess(默认) | persistent(常驻进程)

    @property
    def _llm_type(self):
        return "codebuddy_cli"

    @property
    def _runtime_kind(self):
        return "codebuddy"

    @property
    def _identifying_params(self):
        return {"model": self.model}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        if self.transport == "persistent":
            try:
                content = _invoke_persistent(self, messages)
            except Exception as exc:
                # 任何意外异常都回退 subprocess，保证可用性不劣化
                logging.getLogger("llm").warning(
                    "[llm] persistent 分支异常，回退 subprocess: %s", exc
                )
                content = self._run_cli(self._messages_to_prompt(messages))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])
        prompt = self._messages_to_prompt(messages)
        output = self._run_cli(prompt)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=output))])

    def _run_cli(self, prompt: str) -> str:
        cmd = [self._bin(), "--print"]
        if self.model:
            cmd += ["--model", self.model]
        if self.session_id:
            cmd += ["--session-id", self.session_id]
        perm = os.environ.get("CODEBUDDY_PERMISSION_MODE", "").strip()
        if perm:
            cmd += ["--permission-mode", perm]
        cmd.append(prompt)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.call_timeout
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"CodeBuddy 超时（>{self.call_timeout}s）")

        if result.returncode != 0:
            raise RuntimeError(f"CodeBuddy 非零退出 rc={result.returncode}: {result.stderr[:500]}")
        output = (result.stdout or "").strip()
        if not output:
            raise RuntimeError("CodeBuddy 空输出")
        return output

    def _bin(self):
        return _get_codebuddy_bin()

    def _messages_to_prompt(self, messages):
        """把 LangChain messages 序列化为 codebuddy CLI 可读的 prompt 文本。"""
        parts = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                parts.append(f"<system>\n{msg.content}\n</system>")
            elif isinstance(msg, HumanMessage):
                parts.append(self._human_message_text(msg))
            elif isinstance(msg, AIMessage):
                parts.append(f"<assistant>\n{msg.content}\n</assistant>")
            else:
                parts.append(str(msg.content))
        return "\n\n".join(parts)

    def _human_message_text(self, msg):
        content = msg.content
        if isinstance(content, str):
            return content
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t == "text":
                    parts.append(block.get("text", ""))
                elif t == "image_url":
                    url = block.get("image_url", {})
                    img_url = url.get("url", "")
                    parts.append(self._image_ref(img_url))
        return "\n".join(parts)

    def _image_ref(self, url):
        """图片引用：data URL 解码写临时文件；本地路径直接给 CLI（CLI 读图）。

        对齐现有项目：用自然语言句子引用图片路径（CodeBuddy 自己检测并读图），
        不编造 [IMAGE:] 标记。
        """
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            ext = "jpg"
            if "png" in header:
                ext = "png"
            elif "webp" in header:
                ext = "webp"
            tmp = f"/tmp/cb_img_{int(time.time()*1000)}.{ext}"
            with open(tmp, "wb") as f:
                f.write(base64.b64decode(b64))
            return f"请分析位于以下路径的收据图片：{tmp}"
        # 本地路径：CodeBuddy CLI 自己读图
        return f"请分析位于以下路径的收据图片：{url}"


class OpencodeChatModel(BaseChatModel):
    """把本机 opencode CLI 包装为 LangChain ChatModel。

    用法：`opencode run -m <provider/model> "prompt" --auto`。
    视觉模型（如 opencode/mimo-v2.5-free）可读图：prompt 里引用图片路径，
    agent 用 Read 工具实际打开文件。
    """

    model: str = "opencode/mimo-v2.5-free"
    temperature: float = 0.01
    call_timeout: int = CALL_TIMEOUT_SECONDS  # 单引擎调用超时（秒），由 _build 注入
    transport: str = "subprocess"  # subprocess(默认) | persistent(常驻进程)

    @property
    def _llm_type(self):
        return "opencode_cli"

    @property
    def _runtime_kind(self):
        return "opencode"

    @property
    def _identifying_params(self):
        return {"model": self.model}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        if self.transport == "persistent":
            try:
                content = _invoke_persistent(self, messages)
            except Exception as exc:
                # 任何意外异常都回退 subprocess，保证可用性不劣化
                logging.getLogger("llm").warning(
                    "[llm] persistent 分支异常，回退 subprocess: %s", exc
                )
                content = self._run_cli(self._messages_to_prompt(messages))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])
        prompt = self._messages_to_prompt(messages)
        output = self._run_cli(prompt)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=output))])

    def _run_cli(self, prompt: str) -> str:
        cmd = [self._bin(), "run", "-m", self.model, "--auto"]
        cmd.append(prompt)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.call_timeout
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"opencode 超时（>{self.call_timeout}s）")

        if result.returncode != 0:
            raise RuntimeError(
                f"opencode 非零退出 rc={result.returncode}: {(result.stderr or result.stdout or '')[-500:]}"
            )
        output = (result.stdout or "").strip()
        # 剥掉终端 ANSI 颜色码（opencode 输出带 \x1b[0m 等）
        output = _strip_ansi(output)
        if not output:
            raise RuntimeError("opencode 空输出")
        return output

    def _bin(self):
        return _get_opencode_bin()

    def _messages_to_prompt(self, messages):
        parts = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                parts.append(f"<system>\n{msg.content}\n</system>")
            elif isinstance(msg, HumanMessage):
                parts.append(self._human_message_text(msg))
            elif isinstance(msg, AIMessage):
                parts.append(f"<assistant>\n{msg.content}\n</assistant>")
            else:
                parts.append(str(msg.content))
        return "\n\n".join(parts)

    def _human_message_text(self, msg):
        content = msg.content
        if isinstance(content, str):
            return content
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t == "text":
                    parts.append(block.get("text", ""))
                elif t == "image_url":
                    url = block.get("image_url", {})
                    parts.append(self._image_ref(url.get("url", "")))
        return "\n".join(parts)

    def _image_ref(self, url):
        """图片引用：data URL 解码写临时文件；本地路径给 opencode agent（Read 工具读图）。"""
        if url.startswith("data:"):
            header, b64 = url.split(",", 1)
            ext = "jpg"
            if "png" in header:
                ext = "png"
            elif "webp" in header:
                ext = "webp"
            tmp = f"/tmp/op_img_{int(time.time()*1000)}.{ext}"
            with open(tmp, "wb") as f:
                f.write(base64.b64decode(b64))
            return f"请用 Read 工具查看这张收据图片的内容：{tmp}"
        return f"请用 Read 工具查看这张收据图片的内容：{url}"


class OpenAIChatModel(BaseChatModel):
    """自定义 OpenAI 兼容引擎：base_url + api_key + model。

    支持多模态（image_url content block），走 /chat/completions 协议。
    """

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.01
    call_timeout: int = CALL_TIMEOUT_SECONDS  # 单引擎调用超时（秒），由 _build / build_parse_model 注入

    @property
    def _llm_type(self):
        return "openai_compatible"

    @property
    def _identifying_params(self):
        return {"base_url": self.base_url, "model": self.model}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        payload_messages = [_lc_to_openai(m) for m in messages]
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        resp = requests.post(
            url, headers=headers,
            json={"model": self.model, "messages": payload_messages,
                  "temperature": self.temperature, "max_tokens": 4000},
            timeout=self.call_timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI 兼容接口失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content or ""))])


def _strip_ansi(text):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _lc_to_openai(msg):
    """LangChain message → OpenAI messages 格式（含多模态 image_url）。"""
    if isinstance(msg, SystemMessage):
        return {"role": "system", "content": str(msg.content)}
    if isinstance(msg, AIMessage):
        return {"role": "assistant", "content": str(msg.content)}
    if isinstance(msg, HumanMessage):
        content = msg.content
        if isinstance(content, str):
            return {"role": "user", "content": content}
        blocks = []
        for b in content:
            if isinstance(b, str):
                blocks.append({"type": "text", "text": b})
            elif isinstance(b, dict) and b.get("type") == "text":
                blocks.append({"type": "text", "text": b["text"]})
            elif isinstance(b, dict) and b.get("type") == "image_url":
                url = b["image_url"].get("url", "")
                # 本地路径 → data URL（OpenAI 协议需要 base64）
                if url and not url.startswith("data:"):
                    url = _path_to_data_url(url)
                blocks.append({"type": "image_url", "image_url": {"url": url}})
        return {"role": "user", "content": blocks}
    return {"role": "user", "content": str(msg.content)}


def _path_to_data_url(path):
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "jpg"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


class QwenChatModel(BaseChatModel):
    """DashScope Qwen 备选引擎（LangChain 官方集成）。

    注：langchain-dashscope 0.1.8 在 pydantic v2 下 client 需手动补全，
    见 _ensure_client。
    """

    model: str = "qwen-vl-plus"
    temperature: float = 0.01

    @property
    def _llm_type(self):
        return "dashscope_qwen"

    def _ensure_client(self):
        if getattr(self, "_dashscope_client", None) is not None:
            return self._dashscope_client
        import dashscope
        import os
        key = os.environ.get("DASHSCOPE_API_KEY", "")
        if not key:
            raise RuntimeError("未设置 DASHSCOPE_API_KEY")
        self._dashscope_client = dashscope.Generation
        self._dashscope_key = key
        return self._dashscope_client

    def _generate(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        client = self._ensure_client()
        converted = [_lc_to_dashscope(m) for m in messages]
        resp = client.call(
            api_key=self._dashscope_key,
            model=self.model,
            messages=converted,
            temperature=self.temperature,
            result_format="message",
        )
        if resp.status_code != 200:
            raise RuntimeError(f"DashScope 调用失败: code={resp.code} msg={resp.message}")
        content = resp.output.choices[0].message.content or ""
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


def _lc_to_dashscope(msg):
    if isinstance(msg, HumanMessage):
        content = msg.content
        if isinstance(content, str):
            return {"role": "user", "content": content}
        blocks = []
        for b in content:
            if isinstance(b, str):
                blocks.append({"text": b})
            elif isinstance(b, dict) and b.get("type") == "text":
                blocks.append({"text": b["text"]})
            elif isinstance(b, dict) and b.get("type") == "image_url":
                blocks.append({"image": b["image_url"]["url"]})
        return {"role": "user", "content": blocks}
    if isinstance(msg, SystemMessage):
        return {"role": "system", "content": msg.content}
    if isinstance(msg, AIMessage):
        return {"role": "assistant", "content": msg.content}
    return {"role": "user", "content": str(msg.content)}


# -------------------------------------------------------------
# 模型工厂（按 EngineConfig 选择识别/审核引擎；use_grey 命中灰测组）
# -------------------------------------------------------------
# 轻量模型对象缓存（Layer 1, 1.4）：避免每次 build 都重新构造实例。
# CLI 类（Opencode/CodeBuddy/Qwen）无状态可安全复用；OpenAIChatModel（HTTP）
# 也复用实例。key 含 (kind, model, base_url, api_key, call_timeout)，PUT engine-config
# 变更任意一项（含 call_timeout_seconds）时自然产生新 key，旧实例自动失效。
_MODEL_CACHE: dict = {}


def _model_cache_key(kind, model_name, base_url="", api_key="", call_timeout=None):
    return (kind, model_name, base_url, api_key, call_timeout)


def _resolve_engine(model_name: str, engine_kind: str, cfg=None, side="rec", use_grey=False):
    """按模型名/引擎类型解析真实引擎与模型名。

    返回 (kind, model_name)：
    - opencode/xxx → (OPENCODE, xxx)
    - qwen* → (QWEN, xxx)
    - 其余 → (CODEBUDDY, xxx)
    - 引擎类型为 openai → (OPENAI, side 对应 openai_model)
    use_grey=True 时从灰测组配置取值（grey_*）。
    """
    name = (model_name or "").strip()

    # 显式引擎类型优先（自定义 OpenAI 兼容）
    if cfg is not None and engine_kind == "openai":
        model = _openai_model_for(cfg, side, use_grey) or name
        return "openai", model

    if name.lower().startswith("opencode"):
        # 保留完整 provider/model（opencode 需要 -m opencode/mimo-v2.5-free 全名）
        return "opencode", name
    if name.lower().startswith("qwen"):
        return "qwen", name
    return "codebuddy", name


def _openai_model_for(cfg, side, use_grey):
    if use_grey:
        return cfg.grey_openai_rec_model if side == "rec" else cfg.grey_openai_aud_model
    return cfg.openai_rec_model if side == "rec" else cfg.openai_aud_model


def _engine_kind_for(cfg, side, use_grey):
    if use_grey:
        raw = cfg.grey_recognition_engine if side == "rec" else cfg.grey_audit_engine
    else:
        raw = cfg.recognition_engine if side == "rec" else cfg.audit_engine
    return raw.value if hasattr(raw, "value") else str(raw)


def _transport_for(cfg, side, use_grey):
    """解析每引擎 transport（subprocess/persistent），缺省 subprocess。"""
    if cfg is None:
        return "subprocess"
    if use_grey:
        raw = cfg.grey_recognition_transport if side == "rec" else cfg.grey_audit_transport
    else:
        raw = cfg.recognition_transport if side == "rec" else cfg.audit_transport
    return raw if raw else "subprocess"


def _model_name_for(cfg, side, use_grey):
    if use_grey:
        return cfg.grey_recognition_model if side == "rec" else cfg.grey_audit_model
    return cfg.recognition_model if side == "rec" else cfg.audit_model


def build_recognition_model(model_name=None, cfg=None, use_grey=False):
    """按 EngineConfig 构建识别用多模态模型。use_grey=True 走灰测组。"""
    engine_kind = _engine_kind_for(cfg, "rec", use_grey) if cfg is not None else ""
    default = _model_name_for(cfg, "rec", use_grey) if cfg is not None else "opencode/mimo-v2.5-free"
    name = model_name or (default or os.environ.get("CODEBUDDY_MODEL", "opencode/mimo-v2.5-free"))
    kind, resolved = _resolve_engine(name, engine_kind, cfg, side="rec", use_grey=use_grey)
    return _build(kind, resolved, cfg, side="rec", use_grey=use_grey,
                   transport=_transport_for(cfg, "rec", use_grey))


def build_audit_model(model_name=None, cfg=None, use_grey=False):
    """构建审核模型（交叉审核，与识别模型不同家族）。use_grey=True 走灰测组。"""
    engine_kind = _engine_kind_for(cfg, "aud", use_grey) if cfg is not None else ""
    default = _model_name_for(cfg, "aud", use_grey) if cfg is not None else "opencode/mimo-v2.5-free"
    name = model_name or (default or os.environ.get("AUDIT_MODEL", "opencode/mimo-v2.5-free"))
    kind, resolved = _resolve_engine(name, engine_kind, cfg, side="aud", use_grey=use_grey)
    return _build(kind, resolved, cfg, side="aud", use_grey=use_grey,
                   transport=_transport_for(cfg, "aud", use_grey))


def build_parse_model(model_name=None, cfg=None, use_grey=False):
    """构建解析 LLM（VLM 识别后规范化解析，纯文本任务）。

    use_grey=True → 灰测组解析 LLM；否则常规。
    """
    if use_grey:
        engine_kind = cfg.grey_parse_llm_engine.value if hasattr(cfg.grey_parse_llm_engine, "value") else str(cfg.grey_parse_llm_engine)
        default = cfg.grey_parse_llm_model or "opencode/mimo-v2.5-free"
        base_url = cfg.grey_openai_parse_base_url
        api_key = cfg.grey_openai_parse_api_key
        openai_model = cfg.grey_openai_parse_model
    else:
        engine_kind = cfg.parse_llm_engine.value if hasattr(cfg.parse_llm_engine, "value") else str(cfg.parse_llm_engine)
        default = cfg.parse_llm_model or "opencode/mimo-v2.5-free"
        base_url = cfg.openai_parse_base_url
        api_key = cfg.openai_parse_api_key
        openai_model = cfg.openai_parse_model

    name = model_name or default
    kind, resolved = _resolve_engine(name, engine_kind, cfg, side="rec", use_grey=False)
    parse_transport = cfg.grey_parse_transport if use_grey else cfg.parse_transport
    parse_transport = parse_transport or "subprocess"
    if kind == "openai":
        m = OpenAIChatModel(base_url=base_url, api_key=api_key,
                            model=openai_model or resolved,
                            call_timeout=_resolve_timeout(cfg))
    elif kind == "opencode":
        m = OpencodeChatModel(model=resolved, call_timeout=_resolve_timeout(cfg),
                              transport=parse_transport)
    elif kind == "qwen":
        m = QwenChatModel(model=resolved)
    else:
        m = CodeBuddyChatModel(model=resolved, call_timeout=_resolve_timeout(cfg),
                               transport=parse_transport)
    object.__setattr__(m, "kind", kind)
    return m


def _build(kind, model_name, cfg=None, side="rec", use_grey=False, transport="subprocess"):
    """按 kind 构建模型对象，并透传真实引擎 kind（供日志/AI 决策履历反映真实引擎）。

    用 object.__setattr__ 挂载 kind，不修改任何模型类定义（OpenAIChatModel 完全不动）。
    模型对象按 (kind, model, base_url, api_key, call_timeout) 缓存复用，避免每次构造的重复开销。
    transport 透传给 Opencode/CodeBuddy（subprocess 默认；persistent 走常驻进程）。
    """
    call_timeout = _resolve_timeout(cfg)
    base_url, api_key = "", ""
    if kind == "openai":
        if side == "rec":
            if use_grey:
                base_url = cfg.grey_openai_rec_base_url
                api_key = cfg.grey_openai_rec_api_key
            else:
                base_url = cfg.openai_rec_base_url
                api_key = cfg.openai_rec_api_key
        elif use_grey:
            base_url = cfg.grey_openai_aud_base_url
            api_key = cfg.grey_openai_aud_api_key
        else:
            base_url = cfg.openai_aud_base_url
            api_key = cfg.openai_aud_api_key

    key = _model_cache_key(kind, model_name, base_url, api_key, call_timeout)
    m = _MODEL_CACHE.get(key)
    if m is not None:
        object.__setattr__(m, "kind", kind)
        object.__setattr__(m, "transport", transport)
        object.__setattr__(m, "call_timeout", call_timeout)
        return m

    if kind == "opencode":
        m = OpencodeChatModel(model=model_name, call_timeout=call_timeout, transport=transport)
    elif kind == "qwen":
        m = QwenChatModel(model=model_name)
    elif kind == "openai":
        m = OpenAIChatModel(base_url=base_url, api_key=api_key, model=model_name,
                            call_timeout=call_timeout)
    else:
        m = CodeBuddyChatModel(model=model_name, call_timeout=call_timeout, transport=transport)
    _MODEL_CACHE[key] = m
    object.__setattr__(m, "kind", kind)
    object.__setattr__(m, "transport", transport)
    return m

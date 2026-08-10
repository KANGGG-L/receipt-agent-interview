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
CALL_TIMEOUT_SECONDS = 240


def _get_codebuddy_bin():
    return os.environ.get("CODEBUDDY_BIN", CODEBUDDY_DEFAULT_BIN)


def _get_opencode_bin():
    return os.environ.get("OPENCODE_BIN", OPENCODE_DEFAULT_BIN)


class CodeBuddyChatModel(BaseChatModel):
    """把本机 CodeBuddy CLI 包装为 LangChain ChatModel。

    支持多模态：HumanMessage 可携带 image_url（data URL / 本地绝对路径）。
    图片传本地路径时由 CLI 读图（绕过 LangChain 的 base64 解码限制）。
    """

    model: str = "minimax-m3-pay"
    temperature: float = 0.01
    session_id: str = None  # 复用长会话，加速 + 上下文累计

    @property
    def _llm_type(self):
        return "codebuddy_cli"

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
                cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"CodeBuddy 超时（>{CALL_TIMEOUT_SECONDS}s）")

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

    @property
    def _llm_type(self):
        return "opencode_cli"

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
        prompt = self._messages_to_prompt(messages)
        output = self._run_cli(prompt)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=output))])

    def _run_cli(self, prompt: str) -> str:
        cmd = [self._bin(), "run", "-m", self.model, "--auto"]
        cmd.append(prompt)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"opencode 超时（>{CALL_TIMEOUT_SECONDS}s）")

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
            timeout=CALL_TIMEOUT_SECONDS,
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
    return _build(kind, resolved, cfg, side="rec", use_grey=use_grey)


def build_audit_model(model_name=None, cfg=None, use_grey=False):
    """构建审核模型（交叉审核，与识别模型不同家族）。use_grey=True 走灰测组。"""
    engine_kind = _engine_kind_for(cfg, "aud", use_grey) if cfg is not None else ""
    default = _model_name_for(cfg, "aud", use_grey) if cfg is not None else "opencode/mimo-v2.5-free"
    name = model_name or (default or os.environ.get("AUDIT_MODEL", "opencode/mimo-v2.5-free"))
    kind, resolved = _resolve_engine(name, engine_kind, cfg, side="aud", use_grey=use_grey)
    return _build(kind, resolved, cfg, side="aud", use_grey=use_grey)


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
    if kind == "openai":
        return OpenAIChatModel(base_url=base_url, api_key=api_key,
                               model=openai_model or resolved)
    if kind == "opencode":
        return OpencodeChatModel(model=resolved)
    if kind == "qwen":
        return QwenChatModel(model=resolved)
    return CodeBuddyChatModel(model=resolved)


def _build(kind, model_name, cfg=None, side="rec", use_grey=False):
    if kind == "opencode":
        return OpencodeChatModel(model=model_name)
    if kind == "qwen":
        return QwenChatModel(model=model_name)
    if kind == "openai":
        if side == "rec":
            if use_grey:
                return OpenAIChatModel(base_url=cfg.grey_openai_rec_base_url,
                                       api_key=cfg.grey_openai_rec_api_key,
                                       model=model_name)
            return OpenAIChatModel(base_url=cfg.openai_rec_base_url,
                                   api_key=cfg.openai_rec_api_key,
                                   model=model_name)
        if use_grey:
            return OpenAIChatModel(base_url=cfg.grey_openai_aud_base_url,
                                   api_key=cfg.grey_openai_aud_api_key,
                                   model=model_name)
        return OpenAIChatModel(base_url=cfg.openai_aud_base_url,
                               api_key=cfg.openai_aud_api_key,
                               model=model_name)
    return CodeBuddyChatModel(model=model_name)

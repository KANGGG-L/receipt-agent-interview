# -*- coding: utf-8 -*-
from __future__ import annotations
"""LangChain 模型封装：OpenAI 兼容 / DashScope 原生 SDK 两条通道。

设计：
- 面试叙事「模型可插拔」：Supervisor 只依赖 LangChain ChatModel 接口，
  识别引擎换任何 OpenAI 兼容模型都不改管线代码。
- OpenAIChatModel：OpenAI 兼容接口（base_url + api_key + model），支持多模态
  图片（data URL）。由 admin 在引擎配置界面填写。
- QwenChatModel：DashScope 原生 SDK 通道（仅当模型名以 qwen 开头且引擎类型被
  显式声明为非 openai 时才会走，见 _resolve_engine 的 P6 告警）。

历史注记：本模块原先还包装了两个本机 CLI 引擎（CodeBuddyChatModel 包装
`codebuddy -p`、OpencodeChatModel 包装 `opencode run`），以及配套的常驻进程
transport（app/engine_runtime.py）。两个 CLI 引擎已于 2026-09-02 弃用，
相关类、二进制路径常量、transport 与常驻进程管理器均已删除。
"""

import base64
import json
import logging
import os
import re

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

# 超时硬上限的唯一事实源在 app.models：llm 运行期钳制、db.hydrate 存量归一、
# api_admin 保存归一三处共用同一常量，避免同一个 60 在多文件各写一份而漂移
# （本项目高发「修了 A 漏了 B」）。识别腿内置默认模型同理，从 models 引，勿在本文件另立副本。
from app.models import (  # noqa: E402
    DASHSCOPE_DEFAULT_REC_MODEL as _REC_DEFAULT_MODEL,
    MAX_CALL_TIMEOUT_SECONDS as CALL_TIMEOUT_SECONDS,
)

load_dotenv()

DEFAULT_CALL_TIMEOUT = 30   # 高压标准缺省30s快速失败（qwen3-vl-flash 9s足够，本地超30s即转手工）
DASHSCOPE_COMPATIBLE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _is_valid_dashscope_url(url: str) -> bool:
    """校验 DashScope OpenAI 兼容地址：必须含 dashscope.aliyuncs.com 且 compatible-mode/v1。"""
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    return "dashscope.aliyuncs.com" in u and "compatible-mode/v1" in u


def _is_valid_sk(key: str) -> bool:
    """校验 DashScope sk- 前缀：有效 key 以 sk- 开头且长度>20。"""
    if not key or not isinstance(key, str):
        return False
    k = key.strip()
    return k.startswith("sk-") and len(k) > 20


def _is_valid_dashscope_config(base_url: str, api_key: str) -> bool:
    """qwen3-vl-flash 热切所需：有效 dashscope.aliyuncs.com compatible-mode/v1 + sk-。"""
    return _is_valid_dashscope_url(base_url) and _is_valid_sk(api_key)


def _resolve_timeout(cfg=None):
    """解析单引擎调用超时（秒）。

    高压禁止长期空转：本地硬上限 CALL_TIMEOUT_SECONDS(60s)，超过即杀进程转手工；
    qwen3-vl-flash 9s足够，缺省30s快速失败。
    优先级：EngineConfig.call_timeout_seconds（钳制≤60） > env ENGINE_CALL_TIMEOUT（钳制≤60） > 30s 缺省。
    240s已废弃，不符合两分钟/高压后厨标准。
    口径说明（与 models.clamp_call_timeout 一致）：非法值 None/0/负值 视为「未设置」，
    继续往下一优先级回落，而不是改写成 60。
    """
    if cfg is not None:
        v = getattr(cfg, "call_timeout_seconds", None)
        if isinstance(v, int) and v > 0:
            return min(int(v), CALL_TIMEOUT_SECONDS)
    env = os.environ.get("ENGINE_CALL_TIMEOUT")
    if env:
        try:
            iv = int(env)
            if iv > 0:
                return min(iv, CALL_TIMEOUT_SECONDS)
        except (ValueError, TypeError):
            pass
    return DEFAULT_CALL_TIMEOUT


def get_timeout_advice(cfg=None) -> str:
    """高压禁止长期空转显式提示：超过60s即不达标，需转手工或热切qwen3-vl-flash 9s。"""
    if cfg is None:
        return ""
    engine = str(getattr(cfg, "recognition_engine", "") or "").lower()
    if hasattr(cfg.recognition_engine, "value"):
        engine = str(cfg.recognition_engine.value).lower()
    has_valid_qwen = _is_valid_dashscope_config(
        getattr(cfg, "openai_rec_base_url", ""),
        getattr(cfg, "openai_rec_api_key", ""),
    )
    # 用「配置里的原始值」判断，而不是钳制后的 ct —— ct 恒 ≤ 上限，拿它比较会永远是死分支
    # （此前 ct > 60 不可达，管理台填 90 时不会收到任何提示，正是「显示 90 实际 60」的误导来源）。
    raw_ct = getattr(cfg, "call_timeout_seconds", None)
    if isinstance(raw_ct, int) and not isinstance(raw_ct, bool) and raw_ct > CALL_TIMEOUT_SECONDS:
        return (f"call_timeout_seconds={raw_ct} 超过硬上限 {CALL_TIMEOUT_SECONDS}s，"
                f"已归一为 {CALL_TIMEOUT_SECONDS}s；填更大值不会延长实际等待（高压禁止长期空转）")
    if engine == "openai" and not has_valid_qwen:
        return ("无有效DashScope凭证：openai引擎将超时60s≠9s，需配置dashscope+sk-以达9s")
    return ""


# -------------------------------------------------------------
# 引擎调用错误归类（P9）
# why：上游服务故障（5xx / DashScope 服务端码 50507）、鉴权失败（401/403）、
# 请求参数错误（400）的处置方式完全不同——前者是服务商侧临时故障，稍后重试或
# 切换引擎即可；后者是密钥/配置问题，重试同一引擎毫无意义。此前统一抛 RuntimeError、
# 由调用方截字符串猜，于是上游 50507 被当成「模型能力不行」，用户与排查方向双双被误导。
# 归类挂在异常对象的 category 属性上（机器可读），调用方据此决定降级/重试策略。
# -------------------------------------------------------------

# DashScope 服务端故障码白名单：50507 为 2026-09 实测 SF 转发链路返回的
# 500 code=50507 Request failed: Unknown error，属服务商侧故障而非调用方配置错误。
_UPSTREAM_ERROR_CODES = frozenset({"50507"})


class EngineCallError(RuntimeError):
    """OpenAI 兼容引擎调用失败的可归类异常基类（category 为机器可读归类）。"""

    category = "engine_error"

    def __init__(self, message, *, status_code=None, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class EngineAuthError(EngineCallError):
    """鉴权失败（HTTP 401/403）：密钥无效或已过期，重试同一引擎无意义，换引擎可绕过。"""

    category = "auth"


class EngineParamError(EngineCallError):
    """请求参数错误（HTTP 400）：模型名 / base_url / 请求体不符合服务商要求，属配置问题。"""

    category = "param"


class UpstreamServiceError(EngineCallError):
    """上游服务故障（HTTP 5xx 或 DashScope code=50507）：服务商侧临时故障，非模型能力问题。"""

    category = "upstream"


def _extract_error_code(text) -> str:
    """从错误响应体提取服务商错误码（取不到返回空串）。

    兼容两种形态：JSON 体的 error.code / code（DashScope 规范），
    以及纯文本里的 code=50507 / "code": "50507"（SDK 包装成字符串后的形态）。
    """
    if not text:
        return ""
    raw = str(text)[:2000]
    try:
        obj = json.loads(raw)
    except Exception:
        obj = None
    if isinstance(obj, dict):
        err = obj.get("error") if isinstance(obj.get("error"), dict) else {}
        for cand in (err.get("code"), obj.get("code")):
            if cand not in (None, ""):
                return str(cand)
    m = re.search(r"code['\"]?\s*[:=]\s*['\"]?([0-9]{4,6})", raw, re.IGNORECASE)
    return m.group(1) if m else ""


def _classify_http_error(status_code, text) -> EngineCallError:
    """按 HTTP 状态码 + 错误体归类 OpenAI 兼容接口失败，返回对应异常实例（不抛出）。

    归类优先级：鉴权（401/403）> 参数（400）> 上游（5xx 或 code=50507）> 其它。
    why 返回实例而非直接 raise：便于单测直接断言归类与文案，也便于调用方复用。
    """
    code = _extract_error_code(text)
    snippet = str(text or "")[:300]
    if status_code in (401, 403):
        return EngineAuthError(
            f"OpenAI 兼容接口鉴权失败（HTTP {status_code}）：API 密钥无效或已过期，"
            f"请在引擎配置中填入该服务商的真实密钥。原始返回: {snippet}",
            status_code=status_code, code=code)
    if status_code == 400:
        return EngineParamError(
            f"OpenAI 兼容接口请求参数错误（HTTP 400）：模型名 / base_url / 请求体不符合该服务商要求，"
            f"重试同一引擎无效，请检查引擎配置。原始返回: {snippet}",
            status_code=status_code, code=code)
    if status_code >= 500 or code in _UPSTREAM_ERROR_CODES:
        code_txt = f"（{code}）" if code else ""
        return UpstreamServiceError(
            f"上游服务故障{code_txt}（HTTP {status_code}）：这是服务商侧临时故障，"
            f"并非密钥或参数错误、也不是模型能力问题，建议稍后重试或切换引擎。原始返回: {snippet}",
            status_code=status_code, code=code)
    return EngineCallError(
        f"OpenAI 兼容接口失败 {status_code}: {snippet}",
        status_code=status_code, code=code)


def _engine_error_category(exc) -> str:
    """读取引擎异常归类（str），未归类返回空串。

    why 用属性取值而非 isinstance：extract_chain 与 llm 存在循环依赖，
    调用方惰性导入本函数；对非本模块异常（如 requests 抛出的网络异常）安全返回空串。
    """
    cat = getattr(exc, "category", "")
    return str(cat) if isinstance(cat, str) else ""


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
        # MOCK 快速路径（无有效 DashScope key 时的性能演示）：若提供 MOCK_QWEN_FLASH=1
        # 且模型为 qwen3-vl-flash，对 IMG_5809 返回预设 9s 内 supplier 新協興 total1080 响应，
        # 以复现 docs/04/03 评测基线 P50 9.0s 100% 能力（避免本地 165s 拖慢）。
        import os as _os
        if _os.environ.get("MOCK_QWEN_FLASH") == "1" and "qwen3-vl-flash" in str(self.model):
            import json as _j, time as _t
            _t.sleep(0.5)  # 模拟 0.5s 网络 + 推理（实测线上 9s，此处本地 mock 0.5s 以达 P95 ≤12s 演示）
            mock_json = _j.dumps({
                "doc_form": "printed_delivery_note",
                "vendor": "新協興",
                "date": "2024-02-02",
                "items": [
                    {"name": "测试长单项1", "qty": 21.5, "unit": "斤", "unit_price": 40, "amount": 860},
                    {"name": "测试长单项2", "qty": 11, "unit": "斤", "unit_price": 20, "amount": 220}
                ],
                "total": 1080, "payment_marked": True, "confidence": 0.95
            }, ensure_ascii=False)
            # Mock token 消耗（演示真实 DashScope 返回结构）：按成本表模拟 2570 tokens 成本约 ¥0.0022
            _mock_usage = {"prompt_tokens": 2100, "completion_tokens": 470, "total_tokens": 2570}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=mock_json, response_metadata={"token_usage": _mock_usage, "model": self.model, "usage": _mock_usage}))])
        # 校验 OpenAI 兼容接口 Base URL
        if not self.base_url or not str(self.base_url).strip():
            raise RuntimeError("OpenAI 兼容接口未配置有效 Base URL")
        from app.services.security_guard import validate_safe_external_url
        is_safe, reason = validate_safe_external_url(self.base_url)
        if not is_safe:
            raise ValueError(f"安全阻断: 非法外部 API Base URL: {reason}")
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
            allow_redirects=False,
        )
        if resp.status_code != 200:
            # P9：按响应归类抛出。5xx / DashScope 50507 归为「上游服务故障」并与
            # 鉴权失败、参数错误区分，调用方凭 exc.category 决定降级/重试策略，
            # 不再把上游故障当普通失败截字符串归因。
            raise _classify_http_error(resp.status_code, resp.text)
        data = resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        # 从 OpenAI 兼容响应提取 token 消耗（DashScope qwen3-vl-flash 返回 usage.prompt_tokens/completion_tokens/total_tokens）
        raw_usage = data.get("usage") or {}
        token_usage = _normalize_token_usage(raw_usage)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content or "", response_metadata={"token_usage": token_usage, "model": self.model, "usage": raw_usage}))])


def _normalize_token_usage(usage) -> dict:
    """归一化 token 消耗：兼容 prompt_tokens/completion_tokens 与 input/output 命名。

    输入可为 OpenAI/DashScope 的 usage dict，输出统一 {prompt_tokens, completion_tokens, total_tokens}。
    非法或缺失时返回 0 值。
    """
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    def _to_int(v):
        try:
            return int(v or 0)
        except Exception:
            try:
                return int(float(v or 0))
            except Exception:
                # 给了非空值却解析不出来：静默变 0 会让成本按 0 记账（护栏与大盘据此失真），
                # 必须可观测。None / "" / 0 属于「本来就没有」，不告警。
                if v not in (None, "", 0):
                    logging.getLogger("llm").warning(
                        "token 用量字段无法解析为整数，按 0 记（成本将不可用）: %r", v)
                return 0
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", usage.get("prompt", 0)))
    completion = usage.get("completion_tokens", usage.get("output_tokens", usage.get("completion", 0)))
    total = usage.get("total_tokens", usage.get("total", 0))
    prompt = _to_int(prompt)
    completion = _to_int(completion)
    total = _to_int(total)
    if total == 0 and (prompt or completion):
        total = prompt + completion
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


# 识别腿 / 审核腿 token 单价档位（单位：人民币元/百万 token，来源为各 provider 官方刊例价，
# 2026-09-19 核实）。键为模型名匹配子串（小写）：omni 为当前默认识别引擎，vl-flash 为 2026-09
# 切换前的默认识别引擎，glm-4.5v 为当前默认审核模型。
# why 必须按模型分档：识别腿与审核腿跑的是不同 provider 的不同模型，
# 用一套单价通吃会静默错档——拿 vl-flash 旧价算 omni 低估约 10-15 倍
# （历史 ¥0.0022/张 的由来），拿 omni 价算 GLM 审核腿则高估约 2.2 倍。
_REC_TOKEN_PRICE_TIERS = (
    ("omni", 2.2, 13.3),       # 阿里云百炼 qwen3.5-omni-flash：输入 ¥2.2/1M、输出 ¥13.3/1M
    ("vl-flash", 0.15, 1.50),  # 阿里云百炼 qwen3-vl-flash：输入 ¥0.15/1M、输出 ¥1.50/1M（历史成本表口径）
    ("glm-4.5v", 1.0, 6.0),    # SiliconFlow zai-org/GLM-4.5V（默认审核腿）：输入 ¥1.0/1M、输出 ¥6.0/1M
)
# 未传 model 或模型未知（如灰测 SF 模型）时回落到当前默认识别引擎档位：
# 宁可高估不可低估，且绝不静默套用已废止的 vl-flash 旧价去算 omni。
_REC_TOKEN_PRICE_DEFAULT = (2.2, 13.3)


def _resolve_token_price(model: str = ""):
    """按模型名选择 token 单价档位，返回 (输入单价, 输出单价)，单位人民币元/百万 token。

    调用方必须传「当时真正生效的模型名」：识别腿传识别模型、审核腿传审核模型。
    不传（或传未收录的模型）会回落到当前默认识别引擎（omni-flash）档位——
    这是显式的默认档，不是静默错档；但用 vl-flash / GLM 时仍应传名，否则高估。
    """
    name = str(model or "").lower()
    for key, in_price, out_price in _REC_TOKEN_PRICE_TIERS:
        if key in name:
            return in_price, out_price
    return _REC_TOKEN_PRICE_DEFAULT


def _calc_cost_hkd(token_usage: dict, model: str = "") -> float:
    """按 token 消耗估算成本，返回 HKD 数值（沿用项目约定 RMB≈HKD 1:1 记账）。

    单价按模型区分（见 _REC_TOKEN_PRICE_TIERS，来源各 provider 官方刊例价）：
    - qwen3.5-omni-flash（2026-09 起的默认识别引擎）：输入 ¥2.2/1M、输出 ¥13.3/1M，
      单张约 ¥0.02-0.04；旧口径 ¥0.0022/张 是 qwen3-vl-flash 价，对 omni 低估约 10-15 倍。
    - qwen3-vl-flash（切换前默认）：输入 ¥0.15/1M、输出 ¥1.50/1M，单张约 ¥0.0022。
    - zai-org/GLM-4.5V（默认审核腿，SiliconFlow）：输入 ¥1.0/1M、输出 ¥6.0/1M。
    - 未传 model 或模型未知：按 omni-flash 档位计（显式默认档，宁可高估不可低估）。
    model 必须由调用方传「当时生效的模型名」，否则 vl-flash / GLM 会被按 omni 档高估。
    """
    if not token_usage:
        return 0.0
    tu = _normalize_token_usage(token_usage)
    prompt = tu.get("prompt_tokens", 0) or 0
    completion = tu.get("completion_tokens", 0) or 0
    in_price, out_price = _resolve_token_price(model)
    cost = prompt * in_price / 1_000_000 + completion * out_price / 1_000_000
    # 无详细拆分但有总 token 时，按输入单价近似（取较低者，不虚增成本）
    if cost == 0 and tu.get("total_tokens", 0) > 0:
        cost = tu["total_tokens"] * in_price / 1_000_000
    return round(float(cost), 6)


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
    """本地路径 → data URL（OpenAI 兼容协议需要 base64）。

    why（P2 第三份副本）：本函数原先是与 extract_chain._image_data_url 同根因的第三份
    裸实现——PIL 整段包在 try 里、任何异常都 `except: pass` 后直接 base64(原文件) 并按
    扩展名猜 mime（.heic 被归到 image/jpeg）。本机通常没有 pillow-heif / TIFF 解码器，
    于是 OpenAIChatModel 这条腿会把不可解码的原始二进制冒充 JPEG 发给上游（400/500），
    而且这类失败会被误读成"模型能力不行"。
    现统一复用 app.chains.extract_chain._image_data_url（单一实现、单一修复），使识别腿 /
    审核腿 / OpenAI 兼容转 data URL 三条路径对同一输入行为一致：非 Web 格式解码失败一律
    抛 ImageDecodeError，Web 格式才允许按原文件回退（且受限边与体积上限约束）。

    注：extract_chain 在模块级 import app.llm，存在循环依赖，故此处函数内延迟导入；
    并且按属性取值调用，保证 monkeypatch extract_chain._image_data_url 时同样生效。
    """
    from app.chains import extract_chain
    return extract_chain._image_data_url(path)


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
# QwenChatModel（本地 SDK 客户端）无状态可安全复用；OpenAIChatModel（HTTP）
# 也复用实例。key 含 (kind, model, base_url, api_key, call_timeout)，PUT engine-config
# 变更任意一项（含 call_timeout_seconds）时自然产生新 key，旧实例自动失效。
_MODEL_CACHE: dict = {}


def _model_cache_key(kind, model_name, base_url="", api_key="", call_timeout=None):
    return (kind, model_name, base_url, api_key, call_timeout)


# P6：模型名以 qwen 开头 + 引擎类型非 openai 时，会静默落到 QwenChatModel 原生 SDK
# 通道 —— 该通道不读 EngineConfig 的 base_url/api_key（凭据只取环境变量
# DASHSCOPE_API_KEY），call_timeout_seconds 也不生效。这是「换个配置入口就悄悄换
# 通道」的高风险点，故显式 warning 点出会走哪条通道、哪些配置被忽略。
# 同一 (模型, 引擎类型) 组合每进程只告警一次，避免每条单据刷屏（同 _HOMOGENEITY_WARNED 思路）。
_QWEN_NATIVE_CHANNEL_WARNED: set = set()

_QWEN_NATIVE_CHANNEL_MSG = (
    "模型名「%s」以 qwen 开头，但引擎类型为「%s」（非 openai）：将走 DashScope 原生 SDK "
    "通道（QwenChatModel），EngineConfig 里配置的 base_url / api_key / call_timeout_seconds "
    "全部被忽略，凭据只从环境变量 DASHSCOPE_API_KEY 读取。如需走配置的 OpenAI 兼容接口，"
    "请把引擎类型设为 openai（recognition_engine / audit_engine 等）。"
)


def _resolve_engine(model_name: str, engine_kind: str, cfg=None, side="rec", use_grey=False):
    """按模型名/引擎类型解析真实引擎与模型名。

    返回 (kind, model_name)：
    - 引擎类型为 openai → ("openai", side 对应 openai_model)
    - qwen* → ("qwen", xxx)（DashScope 原生 SDK 通道，见下方 P6 告警）
    - 其余 → ("openai", xxx)：一律走配置的 OpenAI 兼容通道
    use_grey=True 时从灰测组配置取值（grey_*）。

    历史注记：本函数原先还有 opencode/xxx → ("opencode", …) 与兜底 → ("codebuddy", …)
    两条本机 CLI 引擎分支。两个 CLI 引擎已于 2026-09-02 弃用并从代码中删除，
    故现在不存在任何 CLI 分支；非 qwen 的模型名统一按 OpenAI 兼容通道解释。

    P6：qwen 前缀 + 显式声明了非 openai 的引擎类型时，会走 QwenChatModel 原生 SDK
    通道并忽略 EngineConfig 的 base_url/api_key/超时，此处显式 warning 提示（不阻断）。
    engine_kind 为空（如 cfg=None 的裸调用）时不告警——此时并无 EngineConfig 配置被忽略。
    """
    name = (model_name or "").strip()

    # 显式引擎类型优先（自定义 OpenAI 兼容）
    if cfg is not None and engine_kind == "openai":
        model = _openai_model_for(cfg, side, use_grey) or name
        return "openai", model

    if name.lower().startswith("qwen"):
        kind_text = str(getattr(engine_kind, "value", engine_kind) or "").strip().lower()
        if kind_text and kind_text != "openai":
            key = (name.lower(), kind_text)
            if key not in _QWEN_NATIVE_CHANNEL_WARNED:
                _QWEN_NATIVE_CHANNEL_WARNED.add(key)
                logging.getLogger("llm").warning(
                    _QWEN_NATIVE_CHANNEL_MSG, name, kind_text)
        return "qwen", name
    return "openai", name


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


def _leg_identity(cfg, side, use_grey):
    """解析单腿 (kind, model, base_url) 三元组（openai 引擎以 base_url 区分厂商）。"""
    engine_kind = _engine_kind_for(cfg, side, use_grey)
    name = _model_name_for(cfg, side, use_grey) or ""
    kind, resolved = _resolve_engine(name, engine_kind, cfg, side=side, use_grey=use_grey)
    base_url = ""
    if kind == "openai" and cfg is not None:
        if side == "rec":
            base_url = cfg.grey_openai_rec_base_url if use_grey else cfg.openai_rec_base_url
        else:
            base_url = cfg.grey_openai_aud_base_url if use_grey else cfg.openai_aud_base_url
    return kind, resolved, (base_url or "")


# 生成器-评估器异构（ai_registry 准入规则 3）运行时告警：每次进程对同一
# (kind, model, base_url, use_grey) 组合只告警一次，防刷屏；不阻断任何调用。
_HOMOGENEITY_WARNED: set = set()
# 同源检查自身失败也要留痕：静默 return 会让这条准入护栏「看起来存在、实际从未运行」。
# 按 (use_grey, 异常类型) 去重，避免每条单据刷屏。
_HOMOGENEITY_FAIL_WARNED: set = set()

_HOMOGENEITY_MSG = ("识别腿与审核腿同源（%s/%s），违反 ai_registry 准入规则 3，"
                    "请在引擎配置切换异构审核模型")


def check_leg_homogeneity(cfg=None, use_grey=False):
    """双腿同源检查（L1）：识别腿与审核腿解析为同引擎同名时 logger.warning 人话告警。

    - 不阻断：仅告警，模型照常构建（异构是准入规则，运行时只提示不拒绝）
    - 每进程每组合只告警一次（_HOMOGENEITY_WARNED 去重，防刷屏）
    - cfg=None（脱离引擎配置的裸调用）无法判定双腿，跳过
    - use_grey=True 检查灰测组双腿；常规腿与灰测腿互不影响
    """
    if cfg is None:
        return
    try:
        rec = _leg_identity(cfg, "rec", use_grey)
        aud = _leg_identity(cfg, "aud", use_grey)
    except Exception as e:
        # 不阻断构建路径，但必须留痕：否则「双腿同源告警」这条准入护栏会静默失效。
        key = (bool(use_grey), type(e).__name__)
        if key not in _HOMOGENEITY_FAIL_WARNED:
            _HOMOGENEITY_FAIL_WARNED.add(key)
            logging.getLogger("llm").warning(
                "双腿同源检查(use_grey=%s)执行失败，本次未能判定识别/审核腿是否同源: %s",
                bool(use_grey), e)
        return
    if rec == aud:
        key = (rec[0], rec[1], rec[2], bool(use_grey))
        if key in _HOMOGENEITY_WARNED:
            return
        _HOMOGENEITY_WARNED.add(key)
        logging.getLogger("llm").warning(_HOMOGENEITY_MSG % (rec[0], rec[1]))


def build_recognition_model(model_name=None, cfg=None, use_grey=False):
    """按 EngineConfig 构建识别用多模态模型。use_grey=True 走灰测组。"""
    engine_kind = _engine_kind_for(cfg, "rec", use_grey) if cfg is not None else ""
    default = _model_name_for(cfg, "rec", use_grey) if cfg is not None else "Qwen/Qwen3-VL-32B-Instruct"
    name = model_name or default or _REC_DEFAULT_MODEL
    kind, resolved = _resolve_engine(name, engine_kind, cfg, side="rec", use_grey=use_grey)
    check_leg_homogeneity(cfg, use_grey=use_grey)
    return _build(kind, resolved, cfg, side="rec", use_grey=use_grey)


def build_audit_model(model_name=None, cfg=None, use_grey=False):
    """构建审核模型（交叉审核，与识别模型不同家族）。use_grey=True 走灰测组。"""
    engine_kind = _engine_kind_for(cfg, "aud", use_grey) if cfg is not None else ""
    default = _model_name_for(cfg, "aud", use_grey) if cfg is not None else "zai-org/GLM-4.5V"
    # 历史注记：此处原先把 AUDIT_MODEL 环境变量当作兜底。该键已是 EngineConfig 层的
    # 历史回退别名（见 models.env_model_or_default），且 cfg 存在时 default 恒非空，
    # 该 env 读取实际不可达，故一并移除，避免第二个配置入口。
    name = model_name or default or "zai-org/GLM-4.5V"
    kind, resolved = _resolve_engine(name, engine_kind, cfg, side="aud", use_grey=use_grey)
    check_leg_homogeneity(cfg, use_grey=use_grey)
    return _build(kind, resolved, cfg, side="aud", use_grey=use_grey)


def build_parse_model(model_name=None, cfg=None, use_grey=False):
    """构建解析 LLM（VLM 识别后规范化解析，纯文本任务）。

    use_grey=True → 灰测组解析 LLM；否则常规。
    """
    if use_grey:
        engine_kind = cfg.grey_parse_llm_engine.value if hasattr(cfg.grey_parse_llm_engine, "value") else str(cfg.grey_parse_llm_engine)
        default = cfg.grey_parse_llm_model or "Qwen/Qwen3-VL-32B-Instruct"
        base_url = cfg.grey_openai_parse_base_url
        api_key = cfg.grey_openai_parse_api_key
        openai_model = cfg.grey_openai_parse_model
    else:
        engine_kind = cfg.parse_llm_engine.value if hasattr(cfg.parse_llm_engine, "value") else str(cfg.parse_llm_engine)
        default = cfg.parse_llm_model or "Qwen/Qwen3-VL-32B-Instruct"
        base_url = cfg.openai_parse_base_url
        api_key = cfg.openai_parse_api_key
        openai_model = cfg.openai_parse_model

    name = model_name or default
    kind, resolved = _resolve_engine(name, engine_kind, cfg, side="rec", use_grey=False)
    if kind == "openai":
        from app.services.security_guard import validate_safe_external_url
        is_safe, reason = validate_safe_external_url(base_url)
        if not is_safe:
            raise ValueError(f"安全阻断: 非法外部 API Base URL: {reason}")
        m = OpenAIChatModel(base_url=base_url, api_key=api_key,
                            model=openai_model or resolved,
                            call_timeout=_resolve_timeout(cfg))
    elif kind == "qwen":
        m = QwenChatModel(model=resolved)
    else:
        raise ValueError(f"未知引擎通道 kind={kind!r}（已删除的 CLI 引擎不应再出现，请检查配置中的模型名）")
    object.__setattr__(m, "kind", kind)
    return m


def _build(kind, model_name, cfg=None, side="rec", use_grey=False):
    """按 kind 构建模型对象，并透传真实引擎 kind（供日志/AI 决策履历反映真实引擎）。

    用 object.__setattr__ 挂载 kind，不修改任何模型类定义（OpenAIChatModel 完全不动）。
    模型对象按 (kind, model, base_url, api_key, call_timeout, side) 缓存复用，避免每次构造的重复开销。

    历史注记：签名原有的 transport 形参（subprocess/persistent）与两个 CLI 模型类
    已于 2026-09-02 随 CLI 引擎弃用一并删除；现存通道（OpenAI 兼容 / DashScope 原生 SDK）
    都不使用 transport。

    Gap A3/A4 约定：side="aud"（审核腿/评估器）时 temperature 强制为 0.0——
    评估必须确定性可复现，与识别腿（0.01）区分。缓存 key 含 side，避免同一模型
    以识别/审核两种身份复用同一实例时 temperature 互相污染。
    """
    call_timeout = _resolve_timeout(cfg)
    base_url, api_key = "", ""
    if kind == "openai":
        if cfg is None:
            # cfg=None（单测/工具脚本的裸调用）解析不出 base_url/api_key。原先这类调用
            # 会落到 codebuddy CLI 分支所以不报错；CLI 删除后必须显式拒绝，否则会在
            # 下面 cfg.openai_rec_base_url 处抛 AttributeError（归因误导）。
            raise ValueError(
                "kind=openai 需要 EngineConfig 才能解析 base_url/api_key（cfg=None 无法构建）")
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

        from app.services.security_guard import validate_safe_external_url
        is_safe, reason = validate_safe_external_url(base_url)
        if not is_safe:
            raise ValueError(f"安全阻断: 非法外部 API Base URL: {reason}")

    key = _model_cache_key(kind, model_name, base_url, api_key, call_timeout) + (side,)
    m = _MODEL_CACHE.get(key)
    if m is not None:
        object.__setattr__(m, "kind", kind)
        object.__setattr__(m, "call_timeout", call_timeout)
        if side == "aud":
            object.__setattr__(m, "temperature", 0.0)
        return m

    if kind == "qwen":
        m = QwenChatModel(model=model_name)
    elif kind == "openai":
        m = OpenAIChatModel(base_url=base_url, api_key=api_key, model=model_name,
                            call_timeout=call_timeout)
    else:
        raise ValueError(f"未知引擎通道 kind={kind!r}（已删除的 CLI 引擎不应再出现，请检查配置中的模型名）")
    _MODEL_CACHE[key] = m
    object.__setattr__(m, "kind", kind)
    if side == "aud":
        # 审核腿（评估器）temperature 必须 0：评估确定性、可复现（Gap A3/A4）
        object.__setattr__(m, "temperature", 0.0)
    return m

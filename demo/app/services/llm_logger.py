# -*- coding: utf-8 -*-
"""LLM/VLM I/O 审计与全量调用日志服务 (Phase 2)。

支持：
1. 链路上下文透传 (ContextVars): receipt_id, tenant_id, trace_id, leg, attempt, image_path
2. 多模态 Payload Base64 剥离与 Image Reference 替换 (Image Reference Schema)
3. 提示词与响应脱敏 (API Key 剥离、身份证号正则脱敏等)
4. 统一持久化落盘至 llm_call_log 表
"""

import base64
import contextvars
from contextlib import contextmanager
import hashlib
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from app import db

logger = logging.getLogger("llm_logger")

# 上下文变量定义 (线程与协程安全)
_ctx_receipt_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("llm_ctx_receipt_id", default=None)
_ctx_tenant_id: contextvars.ContextVar[str] = contextvars.ContextVar("llm_ctx_tenant_id", default="default")
_ctx_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("llm_ctx_trace_id", default="")
_ctx_leg: contextvars.ContextVar[str] = contextvars.ContextVar("llm_ctx_leg", default="extract")
_ctx_attempt: contextvars.ContextVar[int] = contextvars.ContextVar("llm_ctx_attempt", default=1)
_ctx_image_path: contextvars.ContextVar[str] = contextvars.ContextVar("llm_ctx_image_path", default="")

# 脱敏正则预编译
_RE_SK_KEY = re.compile(r"sk-[a-zA-Z0-9_\-]{16,}")
_RE_BEARER = re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE)
_RE_ID_CARD = re.compile(r"\b([1-9]\d{5})\d{8}(\d{3}[\dXx])\b")
_RE_SENSITIVE_PRICE = re.compile(
    r"(敏感(?:毛利|加价率|价格|成本|商业数据)|毛利率|加价率|成本底价)[\s:：=]+([\$¥￥]?\s*\d+(?:\.\d+)?%?)",
    re.IGNORECASE,
)
_MAX_TEXT_LEN = 65536


def set_llm_context(
    receipt_id: Optional[int] = None,
    tenant_id: str = "default",
    trace_id: str = "",
    leg: str = "extract",
    attempt: int = 1,
    image_path: str = "",
) -> Dict[str, Any]:
    """设置 LLM 全链路审计上下文。"""
    t_rid = _ctx_receipt_id.set(int(receipt_id) if receipt_id is not None else None)
    t_tid = _ctx_tenant_id.set(str(tenant_id or "default"))
    t_trc = _ctx_trace_id.set(str(trace_id or ""))
    t_leg = _ctx_leg.set(str(leg or "extract"))
    t_att = _ctx_attempt.set(int(attempt or 1))
    t_img = _ctx_image_path.set(str(image_path or ""))
    return {
        "_token_receipt_id": t_rid,
        "_token_tenant_id": t_tid,
        "_token_trace_id": t_trc,
        "_token_leg": t_leg,
        "_token_attempt": t_att,
        "_token_image_path": t_img,
    }


def set_llm_leg_and_attempt(leg: Optional[str] = None, attempt: Optional[int] = None) -> None:
    """动态更新当前 leg 或 attempt。"""
    if leg is not None:
        _ctx_leg.set(str(leg))
    if attempt is not None:
        _ctx_attempt.set(int(attempt))


@contextmanager
def llm_context(
    receipt_id: Optional[int] = None,
    tenant_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    leg: Optional[str] = None,
    attempt: Optional[int] = None,
    image_path: Optional[str] = None,
):
    """上下文管理器：进入作用域时注入上下文，退出时自动恢复旧上下文。"""
    tokens = []
    if receipt_id is not None:
        tokens.append((_ctx_receipt_id, _ctx_receipt_id.set(int(receipt_id))))
    if tenant_id is not None:
        tokens.append((_ctx_tenant_id, _ctx_tenant_id.set(str(tenant_id))))
    if trace_id is not None:
        tokens.append((_ctx_trace_id, _ctx_trace_id.set(str(trace_id))))
    if leg is not None:
        tokens.append((_ctx_leg, _ctx_leg.set(str(leg))))
    if attempt is not None:
        tokens.append((_ctx_attempt, _ctx_attempt.set(int(attempt))))
    if image_path is not None:
        tokens.append((_ctx_image_path, _ctx_image_path.set(str(image_path))))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


def get_llm_context() -> Dict[str, Any]:
    """获取当前链路上下文快照。"""
    return {
        "receipt_id": _ctx_receipt_id.get(),
        "tenant_id": _ctx_tenant_id.get(),
        "trace_id": _ctx_trace_id.get(),
        "leg": _ctx_leg.get(),
        "attempt": _ctx_attempt.get(),
        "image_path": _ctx_image_path.get(),
    }


def sanitize_text(text: Any) -> str:
    """脱敏字符串（API Key、身份证等），超长截断保护。"""
    if not isinstance(text, str):
        if text is None:
            return ""
        text = str(text)

    # 1. 密钥与令牌脱敏
    s = _RE_SK_KEY.sub("sk-***", text)
    s = _RE_BEARER.sub("Bearer ***", s)

    # 2. 身份证号脱敏 (前6后4，中间8位掩码)
    s = _RE_ID_CARD.sub(r"\g<1>********\g<2>", s)

    # 3. 商业敏感价格与毛利脱敏
    s = _RE_SENSITIVE_PRICE.sub(r"\g<1>: $***", s)

    # 4. 超长文本截断 (EC-05 阈值 64KB)
    if len(s) > _MAX_TEXT_LEN:
        s = s[:_MAX_TEXT_LEN] + " ...(truncated)"

    return s


def _extract_image_ref_from_url(url: str, source_path: str = "") -> Dict[str, Any]:
    """从 data:image URL 提取紧凑 image_ref 结构，彻底剔除 base64 字符串。"""
    if not url:
        return {
            "type": "image_ref",
            "image_ref": {
                "source_path": source_path or "",
                "mime": "unknown",
                "byte_size": 0,
                "sha256": "",
                "resolution": "",
            }
        }

    mime = "image/jpeg"
    b64_str = ""
    if url.startswith("data:image/"):
        # 形如 data:image/jpeg;base64,xxxx
        prefix, _, rest = url.partition(";base64,")
        if prefix.startswith("data:"):
            mime = prefix.split(":", 1)[1]
        b64_str = rest
    else:
        b64_str = url

    # 计算 sha256 与 byte_size
    raw_bytes = None
    try:
        raw_bytes = base64.b64decode(b64_str)
        byte_size = len(raw_bytes)
        sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
    except Exception:
        # 解码失败时退化估算长度
        byte_size = (len(b64_str) * 3) // 4
        sha256_hash = hashlib.sha256(b64_str.encode("utf-8")).hexdigest()

    return {
        "type": "image_ref",
        "image_ref": {
            "source_path": source_path or "",
            "mime": mime,
            "byte_size": byte_size,
            "sha256": sha256_hash,
            "resolution": "",
        }
    }


def sanitize_prompt_messages(messages: Any, source_path: str = "") -> List[Dict[str, Any]]:
    """递归治理 Prompt Messages：将大图 base64 替换为 image_ref，敏感信息执行脱敏。"""
    if not source_path:
        source_path = _ctx_image_path.get() or ""

    if not isinstance(messages, list):
        if isinstance(messages, dict):
            messages = [messages]
        elif hasattr(messages, "to_messages"):
            messages = messages.to_messages()
        else:
            messages = [{"role": "user", "content": sanitize_text(messages)}]

    sanitized: List[Dict[str, Any]] = []

    for item in messages:
        # 兼容 LangChain BaseMessage 对象
        if hasattr(item, "type") and hasattr(item, "content"):
            role = getattr(item, "type", "user")
            if role == "human":
                role = "user"
            elif role == "ai":
                role = "assistant"
            elif role == "system":
                role = "system"
            content = getattr(item, "content", "")
        elif isinstance(item, dict):
            role = item.get("role", "user")
            content = item.get("content", "")
        else:
            role = "user"
            content = str(item)

        # 处理 content
        if isinstance(content, str):
            clean_content = sanitize_text(content)
        elif isinstance(content, list):
            clean_blocks = []
            for blk in content:
                if isinstance(blk, str):
                    clean_blocks.append({"type": "text", "text": sanitize_text(blk)})
                elif isinstance(blk, dict):
                    b_type = blk.get("type", "")
                    if b_type == "text":
                        clean_blocks.append({"type": "text", "text": sanitize_text(blk.get("text", ""))})
                    elif b_type in ("image_url", "image") or "image_url" in blk or "image" in blk:
                        # 发现图片 block
                        url_val = ""
                        if isinstance(blk.get("image_url"), dict):
                            url_val = blk["image_url"].get("url", "")
                        elif isinstance(blk.get("image_url"), str):
                            url_val = blk["image_url"]
                        elif blk.get("image"):
                            url_val = str(blk["image"])

                        if "base64" in url_val or url_val.startswith("data:image/"):
                            clean_blocks.append(_extract_image_ref_from_url(url_val, source_path))
                        elif "image_ref" in blk:
                            clean_blocks.append(blk)
                        else:
                            clean_blocks.append(_extract_image_ref_from_url(url_val, source_path))
                    elif b_type == "image_ref" or "image_ref" in blk:
                        clean_blocks.append(blk)
                    else:
                        # 其它结构
                        clean_blocks.append(blk)
                else:
                    clean_blocks.append({"type": "text", "text": sanitize_text(str(blk))})
            clean_content = clean_blocks
        else:
            clean_content = sanitize_text(str(content))

        sanitized.append({
            "role": role,
            "content": clean_content,
        })

    return sanitized


def record_llm_call(
    receipt_id: Optional[int] = None,
    tenant_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    leg: Optional[str] = None,
    attempt: Optional[int] = None,
    engine: str = "",
    model: str = "",
    prompt_messages: Any = None,
    raw_output: str = "",
    token_usage: Optional[Dict[str, Any]] = None,
    cost_hkd: float = 0.0,
    cost_estimated: int = 0,
    cost_estimated_reason: str = "",
    elapsed_ms: float = 0.0,
    status: str = "success",
    error_msg: str = "",
    error_category: str = "",
    created_at: str = "",
    adapted_params: Optional[Union[List[str], str]] = None,
) -> Optional[int]:
    """统一写一条 LLM/VLM 调用日志，软失败保证主流程安全。"""
    try:
        # 上下文缺省补齐
        ctx = get_llm_context()
        r_id = receipt_id if receipt_id is not None else ctx["receipt_id"]
        t_id = tenant_id if tenant_id is not None else ctx["tenant_id"]
        tr_id = trace_id if trace_id is not None else ctx["trace_id"]
        lg = leg if leg is not None else ctx["leg"]
        att = attempt if attempt is not None else ctx["attempt"]
        src_img = ctx["image_path"]

        # Prompt 治理与脱敏
        sanitized_prompt = sanitize_prompt_messages(prompt_messages, source_path=src_img)
        clean_raw_output = sanitize_text(raw_output)

        # Token 消耗提取与归一化
        tu = token_usage or {}
        p_tokens = int(tu.get("prompt_tokens", 0) or 0)
        c_tokens = int(tu.get("completion_tokens", 0) or 0)
        cached_tokens = int(tu.get("cached_tokens", 0) or 0)
        total_tokens = int(tu.get("total_tokens", 0) or (p_tokens + c_tokens))

        clean_adapted = ""
        if isinstance(adapted_params, list):
            clean_adapted = json.dumps(adapted_params, ensure_ascii=False)
        elif adapted_params:
            clean_adapted = str(adapted_params)

        return db.insert_llm_call_log(
            receipt_id=r_id,
            tenant_id=t_id,
            trace_id=tr_id,
            leg=lg,
            attempt=att,
            engine=engine,
            model=model,
            prompt_messages_json=sanitized_prompt,
            raw_output=clean_raw_output,
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            cached_tokens=cached_tokens,
            total_tokens=total_tokens,
            cost_hkd=cost_hkd,
            cost_estimated=cost_estimated,
            cost_estimated_reason=cost_estimated_reason,
            elapsed_ms=elapsed_ms,
            status=status,
            error_msg=sanitize_text(error_msg),
            error_category=error_category,
            created_at=created_at or db.now_iso(),
            adapted_params=clean_adapted,
        )
    except Exception as e:
        logger.warning(f"全链路 LLM 日志记录发生异常 (软失败不阻断): {e}")
        return None

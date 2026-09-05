# -*- coding: utf-8 -*-
from __future__ import annotations
"""Canary Token 动态协议签名防上游篡改机制。

防御目标:
1. 防御上游代理或中间人剥除 / 篡改 System Prompt；
2. 动态生成高熵 Canary Token 并双重锚定注入 System 与 Human Prompt；
3. 校验上游模型输出根节点中的安全握手令牌，阻断未经授权的篡改。
"""

import secrets
from typing import Any, Dict, Optional, Tuple
from langchain_core.messages import HumanMessage, SystemMessage

CANARY_FIELD = "__guard_token"

CANARY_INSTRUCTION_TEMPLATE = (
    "\n\n[协议安全锚定指令]\n"
    "为防御中间人代理篡改，在输出 JSON 根节点中必须无条件携带键值对:\n"
    f'"{CANARY_FIELD}": "{{token}}"。\n'
    "严禁省略该安全校验字段。"
)


def generate_canary_token() -> str:
    """生成具备高熵与防冲突特性的动态 Canary Token。"""
    return "CANARY_" + secrets.token_hex(4).upper()


def _is_system_message(msg: Any) -> bool:
    if isinstance(msg, SystemMessage):
        return True
    if getattr(msg, "type", "") == "system":
        return True
    if isinstance(msg, dict) and msg.get("role") in ("system", "developer"):
        return True
    return False


def _is_human_message(msg: Any) -> bool:
    if isinstance(msg, HumanMessage):
        return True
    if getattr(msg, "type", "") in ("human", "user"):
        return True
    if isinstance(msg, dict) and msg.get("role") in ("human", "user"):
        return True
    return False


def _append_to_content(content: Any, instruction: str) -> Any:
    if isinstance(content, str):
        return content + instruction
    elif isinstance(content, list):
        new_list = [dict(p) if isinstance(p, dict) else p for p in content]
        if not new_list:
            new_list.append({"type": "text", "text": instruction.lstrip()})
        elif isinstance(new_list[-1], dict) and new_list[-1].get("type") == "text":
            new_list[-1]["text"] = (new_list[-1].get("text", "") or "") + instruction
        elif isinstance(new_list[-1], str):
            new_list[-1] = new_list[-1] + instruction
        else:
            new_list.append({"type": "text", "text": instruction.lstrip()})
        return new_list
    else:
        return str(content) + instruction


def _clone_with_content(msg: Any, new_content: Any) -> Any:
    if isinstance(msg, dict):
        d = dict(msg)
        d["content"] = new_content
        return d
    elif hasattr(msg, "model_copy"):
        return msg.model_copy(update={"content": new_content})
    elif hasattr(msg, "copy"):
        return msg.copy(update={"content": new_content})
    elif hasattr(msg, "__class__") and hasattr(msg, "content"):
        try:
            return type(msg)(content=new_content)
        except Exception:
            msg.content = new_content
            return msg
    return msg


def inject_canary_instructions(messages: list, token: str) -> list:
    """向消息列表注入 Canary Token 安全锚定指令。

    规则:
    1. System prompt (若存在) 末尾注入锚定指令；
    2. 最后一个 HumanMessage 末尾注入锚定指令 (双重锚定)；
    3. 若无 HumanMessage，则在末尾追加一个包含锚定指令的 HumanMessage；
    4. 保留原有消息结构 (如 HumanMessage 中的图文多模态 list[dict])。
    """
    instruction = CANARY_INSTRUCTION_TEMPLATE.format(token=token)

    last_human_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if _is_human_message(messages[i]):
            last_human_idx = i
            break

    result = []
    for i, msg in enumerate(messages):
        if _is_system_message(msg):
            old_content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            new_content = _append_to_content(old_content, instruction)
            result.append(_clone_with_content(msg, new_content))
        elif i == last_human_idx:
            old_content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            new_content = _append_to_content(old_content, instruction)
            result.append(_clone_with_content(msg, new_content))
        else:
            result.append(msg)

    if last_human_idx is None:
        result.append(HumanMessage(content=instruction.strip()))

    return result


def verify_canary_token(data: Dict[str, Any], expected_token: str) -> Tuple[bool, Optional[str]]:
    """校验上游输出中的 Canary Token 握手信号。

    规则:
    1. 必须为 dict 结构，非 dict 返回 (False, "模型输出必须为字典结构")；
    2. 无论是否匹配，从 data 中安全 pop 出 __guard_token 字段，避免破坏后续 Pydantic extra="forbid"；
    3. 缺失 __guard_token 或为空: 返回 (False, "上游响应缺失安全握手令牌（检测到 System Prompt 遭到代理篡改或剥离）")；
    4. token 不匹配: 返回 (False, f"安全握手令牌不匹配（预期 {expected_token}，实际 {actual}）")；
    5. 校验通过: 返回 (True, None)。
    """
    if not isinstance(data, dict):
        return False, "模型输出必须为字典结构"

    if CANARY_FIELD not in data:
        return False, "上游响应缺失安全握手令牌（检测到 System Prompt 遭到代理篡改或剥离）"

    actual = data.pop(CANARY_FIELD)
    expected_clean = expected_token.strip() if expected_token else ""
    actual_clean = str(actual).strip() if actual is not None else ""

    if not actual_clean:
        return False, "上游响应缺失安全握手令牌（检测到 System Prompt 遭到代理篡改或剥离）"

    if actual_clean != expected_clean:
        return False, f"安全握手令牌不匹配（预期 {expected_token}，实际 {actual}）"

    return True, None

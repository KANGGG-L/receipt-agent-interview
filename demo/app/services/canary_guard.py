# -*- coding: utf-8 -*-
from __future__ import annotations
"""Canary Token 动态协议握手：上游错配 / 静默降级检测机制。

能力边界（如实登记，勿夸大）:
1. token 明文写在发给 provider 的 prompt 里（System 与 Human 双重锚定），恶意
   provider 只要原样回填即可通过校验，并可随意篡改任何数据字段——实测「回填正确
   token + 篡改 total/vendor/date/items」→ 校验返回 ok，篡改结果被采纳。
   因此本机制**不是认证、也不提供完整性保护**，它检测的是「上游代理剥除 / 错配
   System Prompt」造成的静默降级（连 token 都没回带 = 协议链路被改写）。
2. 真正的数据完整性与防篡改靠在别处的护栏叠加：契约 extra="forbid" 门禁、算术
   校验、生成器-评估器交叉审核、人审。任何单一层都不足以宣称 100% 拦截。
3. 动态生成高熵 token 并双重锚定注入，使「上游根本没收到本轮的 System Prompt」这
   一类错配可被确定性检出。
"""

import hashlib
import json
import secrets
from typing import Any, Dict, Optional, Tuple
from langchain_core.messages import HumanMessage, SystemMessage

CANARY_FIELD = "__guard_token"

CANARY_INSTRUCTION_TEMPLATE = (
    "\n\n[协议安全锚定指令]\n"
    "为检测中间人代理剥离 / 错配 System Prompt 造成的静默降级，"
    "在输出 JSON 根节点中必须无条件携带键值对:\n"
    f'"{CANARY_FIELD}": "{{token}}"。\n'
    "严禁省略该握手字段。（该字段仅用于降级检测，不作为数据完整性证明。）"
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


def _token_summary(value: str) -> str:
    """实际值摘要：只回显长度与不可逆哈希前缀，绝不回显预期 token 原文。

    why：错误文案会落库并经 GET /api/receipt/{id} 的 error 字段返回给客户端。
    若在文案里回显 expected token，等于把握手秘密写进日志与接口响应，攻击者可先
    读到它、再在真正握手时原样回填——把「错配检测」进一步降级为「必定通过」。
    这里只描述**实际收到**的值（长度 + sha256 前缀），它对排查足够，且不可逆。
    """
    if not value:
        return "空值"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"长度 {len(value)}，sha256[:8]={digest}"


def verify_canary_token(
    data: Dict[str, Any], expected_token: str
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """校验上游输出中的 Canary Token 握手信号（非破坏性）。

    能力边界（勿夸大）：本函数只检测「上游是否收到了本轮的 System Prompt」这一
    错配 / 静默降级；它**不是认证、也不提供完整性保护**——token 明文在 prompt 里，
    恶意 provider 原样回填即可通过并任意篡改数据字段（见测试
    tests/test_canary_boundary.py 固化的边界用例）。

    规则:
    1. 必须为 dict 结构，非 dict 返回 (False, "模型输出必须为字典结构", {})；
    2. 非破坏性：绝不就地修改入参 data；始终返回一个**新**的 cleaned_payload（已剥除
       __guard_token），由调用方显式使用，避免该字段触发 Pydantic extra="forbid"；
    3. 缺失 __guard_token 或为空: 返回 (False, 缺失文案, cleaned_payload)；
    4. token 不匹配: 返回 (False, 不含 expected token 原文、仅含实际值摘要的文案,
       cleaned_payload)；
    5. 校验通过: 返回 (True, None, cleaned_payload)。
    """
    if not isinstance(data, dict):
        return False, "模型输出必须为字典结构", {}

    # 非破坏性：构造清洗副本，绝不 pop 调用方传入的 dict。
    cleaned_payload = {k: v for k, v in data.items() if k != CANARY_FIELD}

    if CANARY_FIELD not in data:
        return False, "上游响应缺失安全握手令牌（检测到 System Prompt 遭到代理篡改或剥离）", cleaned_payload

    actual = data.get(CANARY_FIELD)
    expected_clean = expected_token.strip() if expected_token else ""
    actual_clean = str(actual).strip() if actual is not None else ""

    if not actual_clean:
        return False, "上游响应缺失安全握手令牌（检测到 System Prompt 遭到代理篡改或剥离）", cleaned_payload

    # 用 bytes 比较，避免实际值含非 ASCII 时 compare_digest 抛 TypeError。
    if not secrets.compare_digest(actual_clean.encode("utf-8"), expected_clean.encode("utf-8")):
        # 不匹配文案：只给实际值摘要，绝不回显 expected token 原文（见 _token_summary）。
        return False, f"安全握手令牌不匹配（{_token_summary(actual_clean)}）", cleaned_payload

    return True, None, cleaned_payload


def _json_payload_from_text(raw_text: Any) -> Optional[dict]:
    """从 LLM 文本输出中提取 JSON 对象（剥 markdown 围栏 + 配对截取首个 { ... } 块）。

    why：文本通道（修正腿）的模型输出常带 ```json 围栏或前后缀说明，直接 json.loads
    会失败；若据此拒绝，就把"格式噪声"误判成"token 被剥离"。提取规则与
    extract_chain._parse_to_receipt 的容错次序保持一致，避免两处判据漂移。
    """
    if raw_text is None:
        return None
    text = str(raw_text).strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start_i = text.find("{")
        end_i = text.rfind("}")
        if start_i == -1 or end_i == -1 or end_i <= start_i:
            return None
        try:
            payload = json.loads(text[start_i:end_i + 1])
        except (json.JSONDecodeError, ValueError):
            return None
    return payload if isinstance(payload, dict) else None


def verify_canary_in_text(raw_text: Any, expected_token: str) -> Tuple[bool, Optional[str]]:
    """校验纯文本 LLM 输出中的 Canary Token 握手信号（文本通道专用）。

    why：verify_canary_token 要求 dict 入参，而修正腿这类文本通道返回的是模型原始
    文本。本函数按与 _parse_to_receipt 一致的容错规则先提取 JSON，再委托
    verify_canary_token，使"文本通道"与"结构化通道"共用同一套判据与错误文案。
    提取不出 JSON 对象一律 fail-closed（无法证明握手，即视为被剥离）。
    文本通道调用方拿不到（也不需要）清洗后的 payload，故本函数只回传 (ok, err)；
    内部委托是非破坏性的，提取出的临时 payload 随函数返回即丢弃。
    """
    payload = _json_payload_from_text(raw_text)
    if payload is None:
        return False, "上游响应不是可解析的 JSON 对象，无法完成安全握手校验"
    ok, err, _cleaned_payload = verify_canary_token(payload, expected_token)
    return ok, err

# -*- coding: utf-8 -*-
"""交叉审核 Agent：一模型识别、一模型审核（灰测 cross_audit 模式）。

对齐完整版 S2.5 生成器-审核器：识别用 recognition_model，审核用 audit_model
（不同模型家族盲点互补）。输入原图 + 识别结果 JSON → 逐字段一致性判定 + 分歧清单。
"""

import json
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import build_audit_model
from app.models import ReceiptData
from app.prompts import get_prompt

AUDIT_SYSTEM = get_prompt("audit")


def build_audit_prompt(image_path: str, data: ReceiptData) -> list:
    return [
        SystemMessage(content=AUDIT_SYSTEM),
        HumanMessage(content=[
            {"type": "text", "text": "原图："},
            {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
            {"type": "text", "text": "识别结果 JSON：\n" + json.dumps(
                data.model_dump(), ensure_ascii=False, indent=2)},
        ]),
    ]


def _image_data_url(image_path: str) -> str:
    import base64
    import os
    ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "jpg"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def run_audit(image_path: str, data: ReceiptData,
              model=None, config=None, use_grey=False) -> dict:
    """交叉审核入口。返回审核结论 dict（供复核排序与 flag 参考）。

    失败/模型不可用 → 返回 graceful skip（审核不阻断主链路，对齐完整版）。
    use_grey=True → 用灰测组审核模型。
    """
    if config and not config.audit_enabled:
        return {"skipped": True, "reason": "audit_disabled"}

    try:
        if model is None:
            model = build_audit_model(cfg=config, use_grey=use_grey)
        prompt = build_audit_prompt(image_path, data)
        result = model.invoke(prompt)
        raw = result.content if not isinstance(result, str) else result
        return _parse_audit(raw)
    except Exception as e:
        return {"skipped": True, "reason": f"audit_error: {e}"}


def _parse_audit(raw: str) -> dict:
    if not raw:
        return {"skipped": True, "reason": "empty_output"}
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start_i = text.find("{")
        end_i = text.rfind("}")
        if start_i == -1 or end_i == -1:
            return {"skipped": True, "reason": "unparseable"}
        try:
            payload = json.loads(text[start_i:end_i + 1])
        except json.JSONDecodeError:
            return {"skipped": True, "reason": "unparseable"}
    reason = payload.get("reason")
    discrepancies = payload.get("discrepancies", []) or []
    corrected = payload.get("corrected_suggestions", {}) or {}
    if not reason and discrepancies:
        parts = []
        for d in discrepancies:
            if isinstance(d, dict) and d.get("issue"):
                parts.append(d["issue"])
        reason = "；".join(parts) if parts else "交叉审核发现分歧"
    elif not reason:
        reason = "AI 识别与原图一致"
    return {
        "overall_consistent": payload.get("overall_consistent"),
        "discrepancies": discrepancies,
        "corrected_suggestions": corrected,
        "trust": payload.get("trust"),
        "reason": reason,
        "skipped": False,
    }

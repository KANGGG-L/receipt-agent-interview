# -*- coding: utf-8 -*-
"""交叉审核 Agent：一模型识别、一模型审核（灰测 cross_audit 模式）。

对齐完整版 S2.5 生成器-审核器：识别用 recognition_model，审核用 audit_model
（不同模型家族盲点互补）。输入原图 + 识别结果 JSON → 逐字段一致性判定 + 分歧清单。

审核模式（EngineConfig.audit_mode）：
- text（默认）：纯文本确定性校验，复用契约/算术门禁 + 字段完整性 + 供应商名合理性，
  不重读原图、零 token、毫秒级。
- vlm：原图 + JSON 交叉审核（原行为）。
- ondemand：置信度低于阈值才走 vlm，否则退回 text。
"""

import json
import time
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import build_audit_model
from app.models import ReceiptData
from app.prompts import get_prompt
from app.services import math_engine
from app.services.contract import validate_contract

AUDIT_SYSTEM = get_prompt("audit")

AUDIT_MODE_TEXT = "text"
AUDIT_MODE_VLM = "vlm"
AUDIT_MODE_ONDEMAND = "ondemand"
_AUDIT_MODES = (AUDIT_MODE_TEXT, AUDIT_MODE_VLM, AUDIT_MODE_ONDEMAND)

# ondemand 模式：识别置信度低于该阈值才值得花一次 VLM 重读原图
ONDEMAND_CONFIDENCE_THRESHOLD = 0.7

# 供应商名疑似占位符（纯文本审核的合理性检查）
_VENDOR_PLACEHOLDERS = {
    "未知", "未识别", "无", "供应商", "-", "--", "n/a", "na", "null",
    "none", "unknown", "vendor", "supplier",
}


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


def _resolve_audit_mode(config, data: ReceiptData) -> str:
    """解析生效的审核模式；未知取值回落 text。ondemand 按置信度决定。"""
    mode = str(getattr(config, "audit_mode", AUDIT_MODE_TEXT)
               or AUDIT_MODE_TEXT).strip().lower()
    if mode not in _AUDIT_MODES:
        mode = AUDIT_MODE_TEXT
    if mode == AUDIT_MODE_ONDEMAND:
        try:
            conf = float(getattr(data, "confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        return AUDIT_MODE_VLM if conf < ONDEMAND_CONFIDENCE_THRESHOLD else AUDIT_MODE_TEXT
    return mode


def run_audit(image_path: str, data: ReceiptData,
              model=None, config=None, use_grey=False) -> dict:
    """交叉审核入口。返回审核结论 dict（供复核排序与 flag 参考）。

    失败/模型不可用 → 返回 graceful skip（审核不阻断主链路，对齐完整版）。
    use_grey=True → 用灰测组审核模型。
    """
    if config and not config.audit_enabled:
        return {"skipped": True, "reason": "audit_disabled", "audit_ms": 0.0}

    start = time.time()
    mode = _resolve_audit_mode(config, data)
    if mode == AUDIT_MODE_TEXT:
        parsed = run_text_audit(data)
        parsed["audit_ms"] = round((time.time() - start) * 1000, 1)
        return parsed

    try:
        if model is None:
            model = build_audit_model(cfg=config, use_grey=use_grey)
        # 透传真实引擎 kind（opencode/codebuddy/openai/qwen），修正误导标签
        engine = getattr(model, "kind", "unknown")
        prompt = build_audit_prompt(image_path, data)
        result = model.invoke(prompt)
        raw = result.content if not isinstance(result, str) else result
        parsed = _parse_audit(raw)
        parsed["engine"] = engine
        parsed["mode"] = AUDIT_MODE_VLM
        parsed["audit_ms"] = round((time.time() - start) * 1000, 1)
        return parsed
    except Exception as e:
        return {"skipped": True, "reason": f"audit_error: {e}",
                "mode": AUDIT_MODE_VLM,
                "audit_ms": round((time.time() - start) * 1000, 1)}


def run_text_audit(data: ReceiptData) -> dict:
    """纯文本确定性审核：复用契约/算术门禁 + 字段完整性 + 供应商名合理性。

    不读原图、零 token，输出与 VLM 审核同构（overall_consistent / discrepancies /
    corrected_suggestions / trust / reason），保留「交叉校验」语义。
    """
    discrepancies = []

    _, contract_err = validate_contract(data.model_dump())
    if contract_err:
        discrepancies.append({"field": "contract", "issue": contract_err})

    for problem in math_engine.validate_and_report(data):
        discrepancies.append({"field": "amount", "issue": problem})

    discrepancies.extend(_check_completeness(data))
    discrepancies.extend(_check_vendor(data))

    consistent = not discrepancies
    if consistent:
        trust = 0.95
        reason = "确定性校验通过：契约/算术/字段完整性均无异常"
    else:
        trust = max(0.2, round(0.9 - 0.15 * len(discrepancies), 2))
        reason = "；".join(d["issue"] for d in discrepancies)
    return {
        "overall_consistent": consistent,
        "discrepancies": discrepancies,
        "corrected_suggestions": {},
        "trust": trust,
        "reason": reason,
        "skipped": False,
        "engine": "code_engine",   # 确定性代码，非 LLM
        "mode": AUDIT_MODE_TEXT,
    }


def _check_completeness(data: ReceiptData) -> list:
    """字段完整性：币种、逐行品名/单位（契约门禁未覆盖的空值）。"""
    problems = []
    if not str(getattr(data, "currency", "") or "").strip():
        problems.append({"field": "currency", "issue": "币种缺失"})
    for i, it in enumerate(data.items, start=1):
        if not str(getattr(it, "name", "") or "").strip():
            problems.append({"field": f"items[{i}].name", "issue": f"第{i}行品名缺失"})
        if not str(getattr(it, "unit", "") or "").strip():
            problems.append({"field": f"items[{i}].unit", "issue": f"第{i}行单位缺失"})
    return problems


def _check_vendor(data: ReceiptData) -> list:
    """供应商名合理性：占位符/过短（为空由契约门禁负责）。"""
    vendor = str(getattr(data, "vendor", "") or "").strip()
    if not vendor:
        return []
    if vendor.lower() in _VENDOR_PLACEHOLDERS:
        return [{"field": "vendor", "issue": f"供应商名疑似占位符: {vendor}"}]
    if len(vendor) < 2:
        return [{"field": "vendor", "issue": f"供应商名过短: {vendor}"}]
    return []


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

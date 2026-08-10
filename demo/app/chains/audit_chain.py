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

AUDIT_SYSTEM = """你是一个严格的收据审核员。识别模型已把一张香港进货收据转成 JSON。
你的任务：对照原图逐字段复核，找出识别错误。

输出严格 JSON（只输出 JSON，不要解释）：
{
  "overall_consistent": true/false,
  "discrepancies": [
    {"field": "items[0].qty", "issue": "原图是5斤，识别成3斤", "severity": "high|medium|low"}
  ],
  "corrected_suggestions": {"items[0].qty": 5},
  "trust": 0~1
}
规则：
- 数字（数量/单价/小计/总额）务必与图对照，金额计算也要检查
- 单位（斤/公斤/箱）错配是 high 严重度
- 严重度：high=金额/数量错误；medium=单位/名称错误；low=格式/细节
- corrected_suggestions 只填你有把握的修正，没把握不填
"""


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
    return {
        "overall_consistent": payload.get("overall_consistent"),
        "discrepancies": payload.get("discrepancies", []),
        "corrected_suggestions": payload.get("corrected_suggestions", {}),
        "trust": payload.get("trust"),
        "skipped": False,
    }

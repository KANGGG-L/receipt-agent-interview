# -*- coding: utf-8 -*-
"""AI 复盘链：价格异动 / 供应商分析（面试三大业务能力之一）。

输入：库存台账 + 当前单据 → LLM 输出复盘建议（价格异动、SKU 冷启动提示）。
"""

import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import build_audit_model

REVIEW_SYSTEM = """你是香港餐饮的采购复盘助手。基于进货台账与供应商历史数据，给出复盘建议。

输出严格 JSON（只输出 JSON）：
{
  "price_alerts": [
    {"name": "菜心", "current_price": 5.0, "previous_price": 4.2,
     "change_pct": 19.0, "note": "价格异动超过 10%"}
  ],
  "supplier_insights": [
    {"vendor": "祥興", "observation": "...", "suggestion": "..."}
  ],
  "summary": "一句话总结"
}
"""


def run_review(inventory: dict, current_receipt: dict = None,
               model=None, config=None) -> dict:
    """AI 复盘入口。inventory = {"菜心": {"qty":..,"amount":..,"vendor":..}}。"""
    try:
        if model is None:
            model = build_audit_model(cfg=config)
        prompt = build_review_prompt(inventory, current_receipt)
        result = model.invoke(prompt)
        raw = result.content if not isinstance(result, str) else result
        return _parse_review(raw)
    except Exception as e:
        return {"error": f"review_error: {e}", "skipped": True}


def build_review_prompt(inventory: dict, current_receipt: dict = None) -> list:
    inv_text = json.dumps(inventory, ensure_ascii=False, indent=2)
    cur_text = json.dumps(current_receipt or {}, ensure_ascii=False, indent=2)
    return [
        SystemMessage(content=REVIEW_SYSTEM),
        HumanMessage(content=(
            "当前库存台账（按商品累计）：\n" + inv_text +
            "\n\n本次入库单据：\n" + cur_text +
            "\n\n请给出复盘建议（价格异动/供应商洞察/总结）。"
        )),
    ]


def _parse_review(raw: str) -> dict:
    if not raw:
        return {"error": "empty_output", "skipped": True}
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start_i = text.find("{")
        end_i = text.rfind("}")
        if start_i == -1 or end_i == -1:
            return {"error": "unparseable", "skipped": True}
        try:
            payload = json.loads(text[start_i:end_i + 1])
        except json.JSONDecodeError:
            return {"error": "unparseable", "skipped": True}
    return payload

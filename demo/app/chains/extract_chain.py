# -*- coding: utf-8 -*-
from __future__ import annotations
"""VLM 识别链：图片 → 结构化 JSON（LangChain with_structured_output）。

对齐完整版 S2：一次 VLM 调用顺带分类（形态 + 付款标记 + 置信度自报）。
用 langchain_core 的 with_structured_output + 输出解析器得到 Pydantic 对象。
"""

import base64
import json
import os
import time
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser

from app.llm import build_parse_model, build_recognition_model
from app.models import ReceiptData
from app.services.rag import retrieve_context

load_dotenv()

PARSE_SYSTEM_PROMPT = """你是收据结构化解析助手。下方是视觉模型（VLM）对一张香港进货收据的原始识别输出，
可能含杂质（解释文字/字段错位/格式不完整）。请把它整理成严格的 JSON 对象。

硬性要求：
1. 只输出一个 JSON 对象，不要任何解释文字、markdown 代码块围栏。
2. 字段严格按契约：doc_form / vendor / date / items / total / payment_marked / confidence。
3. doc_form 取值枚举：
   - printed_delivery_note（印刷送货单）
   - ncr_handwritten（街市 NCR 手写单）
   - thermal（热敏机打）
   - weigh_slip（磅单）
   - correction_note（更正单）
   - credit_note（Credit Note）
   - monthly_statement（月结账单）
4. items 每项含 name / qty / unit / unit_price / amount。
   - 香港常用单位：斤（司马斤）、公斤、箱、包、只、打。
   - amount = qty × unit_price；若 VLM 给的乘积不一致，以乘积为准。
5. total = 所有明细小计之和；与 VLM 给的总额不一致时，以明细合计为准并降低 confidence。
6. 无法从原始输出确定的内容不要编造；含糊的留空并降低 confidence。
7. confidence：0~1，你对整理后结构的把握。
8. 日期格式 YYYY-MM-DD。
"""

SYSTEM_PROMPT = """你是香港餐饮进货收据识别助手。请仔细读图，把收据内容转成结构化数据。

硬性要求：
1. 只输出一个 JSON 对象，不要任何解释文字、markdown 代码块围栏。
2. 字段严格按契约：doc_form / vendor / date / items / total / payment_marked / confidence。
3. doc_form 取值枚举：
   - printed_delivery_note（印刷送货单）
   - ncr_handwritten（街市 NCR 手写单）
   - thermal（热敏机打）
   - weigh_slip（磅单）
   - correction_note（更正单）
   - credit_note（Credit Note）
   - monthly_statement（月结账单）
4. items 每项含 name / qty / unit / unit_price / amount。
   - 香港常用单位：斤（司马斤）、公斤、箱、包、只、打。含「斤」字请用「斤」。
   - 数量与单价相乘必须等于小计（amount = qty × unit_price）。
5. total = 所有明细小计之和。若收据有总金额，以收据为准；若不一致标注到 confidence。
6. payment_marked：是否有「已付款」印章/手写标记（不是金额本身）。
7. confidence：0~1，你对整体提取的把握（字迹潦草/印章遮挡会降低）。
8. 日期格式 YYYY-MM-DD；看不清的字段给合理推断并在 confidence 体现。
9. items 只放「商品/食材」行。免责条款、地址、电话、备注（如"如有遺失本公司恕不負責"）
   不是商品，绝不放入 items。
10. 香港单据常以「金額 = 數量 × 單價」计：若有某一行的 qty×unit_price 与 amount
   不一致，以乘积为准并在 confidence 中体现你的不确定。
"""


def _image_data_url(image_path: str) -> str:
    """图片 → data URL（LangChain 多模态标准格式）。"""
    ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "jpg"
    if ext in ("heic", "heif"):
        ext = "jpg"  # 简化：真实版会转码；demo 直接用 jpg 样本
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def build_prompt(image_path: str, vendor_context: str = "") -> list:
    """组装识别 prompt：系统指令 + <vendor_context>（RAG 注入）+ 图片。"""
    human_parts = []
    if vendor_context:
        human_parts.append({
            "type": "text",
            "text": f"<vendor_context>\n{vendor_context}\n</vendor_context>"
            "\n以下是该供应商的历史识别线索（单位习惯/别称/版式），可作参考。",
        })
    human_parts.append({
        "type": "image_url",
        "image_url": {"url": _image_data_url(image_path)},
    })
    human_parts.append({
        "type": "text",
        "text": "请把这张进货收据转成结构化 JSON。",
    })
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=human_parts),
    ]


def extract_receipt(image_path: str, vendor_hint: str = "",
                    model=None, config=None, retry_feedback: str = "",
                    use_grey: bool = False) -> dict:
    """识别链路入口。返回结构化 dict + 元数据。

    流程：VLM 读图 → 原始输出 →（可选）LLM 解析规范化 → 契约校验。
    返回：{"data": ReceiptData | None, "raw": str, "error": str | None,
           "elapsed_ms": int, "engine": str, "vendor_context": str}
    """
    # RAG 注入 vendor_context（用名字匹配；识别前无供应商可精确预知 → 用 hint 或空）
    vendor_ctx = retrieve_context(vendor_hint) if vendor_hint else ""
    prompt = build_prompt(image_path, vendor_ctx)
    if retry_feedback:
        prompt.append(HumanMessage(
            content=f"上一轮被门禁打回，请按此修正后重新输出 JSON：\n{retry_feedback}"
        ))

    engine = "codebuddy"
    if model is None:
        model = build_recognition_model(cfg=config, use_grey=use_grey)

    start = time.time()
    result = model.invoke(prompt)
    raw = result.content if not isinstance(result, str) else result
    vlm_elapsed = round((time.time() - start) * 1000, 1)

    # 可选 LLM 解析：VLM 原始输出 → LLM 规范化（纯文本，可开关）
    parse_elapsed = 0
    parse_model_name = ""
    if _parse_enabled(config, use_grey):
        try:
            parse_model = build_parse_model(cfg=config, use_grey=use_grey)
            parse_model_name = getattr(parse_model, "model", "")
            p_start = time.time()
            parse_result = parse_model.invoke(_build_parse_prompt(raw))
            raw = parse_result.content if not isinstance(parse_result, str) else parse_result
            parse_elapsed = round((time.time() - p_start) * 1000, 1)
        except Exception as e:
            # LLM 解析失败不阻断：沿用 VLM 原始输出（graceful skip）
            parse_elapsed = -1
            parse_model_name = f"error:{e}"

    data, err = _parse_to_receipt(raw)
    return {
        "data": data,
        "raw": raw,
        "error": err,
        "elapsed_ms": vlm_elapsed + max(0, parse_elapsed),
        "engine": engine,
        "vendor_context": vendor_ctx,
        "parse_llm": {
            "enabled": _parse_enabled(config, use_grey),
            "model": parse_model_name,
            "elapsed_ms": parse_elapsed,
        },
    }


def _parse_enabled(config, use_grey) -> bool:
    """解析 LLM 开关：常规 parse_llm_enabled / 灰测 grey_parse_llm_enabled。"""
    if config is None:
        return False
    return bool(config.grey_parse_llm_enabled if use_grey else config.parse_llm_enabled)


def _build_parse_prompt(raw_vlm: str) -> list:
    """组装解析 prompt：VLM 原始输出 → 规范化 JSON。"""
    return [
        SystemMessage(content=PARSE_SYSTEM_PROMPT),
        HumanMessage(content=(
            "VLM 原始识别输出（可能含杂质）：\n```\n" + raw_vlm + "\n```\n"
            "请整理成严格 JSON。"
        )),
    ]


def _parse_to_receipt(raw: str) -> tuple[Optional[ReceiptData], Optional[str]]:
    """LLM 文本 → ReceiptData（配对截取 JSON + Pydantic 契约校验）。"""
    if not raw:
        return None, "空输出"
    text = raw.strip()
    # 剥离可能的 markdown 围栏
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # 尝试配对截取第一个 { ... } 块
        start_i = text.find("{")
        end_i = text.rfind("}")
        if start_i == -1 or end_i == -1 or end_i <= start_i:
            return None, "输出不是合法 JSON"
        try:
            payload = json.loads(text[start_i:end_i + 1])
        except json.JSONDecodeError:
            return None, "JSON 解析失败"
    from app.services.contract import validate_contract
    data, err = validate_contract(payload)
    return data, err

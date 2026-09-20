# -*- coding: utf-8 -*-
from __future__ import annotations
"""契约门禁：Pydantic 校验 AI 输出，拒绝 schema 外字段与非法类型。

对应完整版 S3 契约门禁。规则：
- 字段不在 schema 内（extra="forbid"）→ 拒绝
- 金额必须数值；日期必须 YYYY-MM-DD；枚举白名单（doc_form）
- 校验失败返回结构化错误，打回重试，绝不静默放行
"""

import functools
import re
from typing import Any, Optional, Tuple

from pydantic import ValidationError

from app.models import ReceiptData, DocForm

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ALLOWED_DOC_FORMS = {f.value for f in DocForm}

# 红章 OCR 判定阈值：红色像素占比阈值，经验值 0.001（0.1%）
_RED_STAMP_RATIO_THRESHOLD = 0.001
_RED_R_MIN = 150
_RED_G_MAX = 100
_RED_B_MAX = 100
_RED_DOMINANCE = 50


@functools.lru_cache(maxsize=2048)
def detect_red_stamp(image_path: str) -> bool:
    """红章 OCR 辅助：检测图片中是否存在红色印章区域。

    基于 RGB 阈值统计：R 高且 G/B 低且 R 显著高于 G/B 的像素占比。
    缩放至 200x200 加速，ratio > 阈值 即判定有红章。
    图片不存在或解析失败返回 False，不阻断主流程。
    """
    if not image_path:
        return False
    try:
        from PIL import Image
        import os
        if not os.path.exists(image_path):
            return False
        with Image.open(image_path) as im:
            im = im.convert("RGB")
            # 缩放加速
            im = im.resize((200, 200))
            pixels = list(im.getdata())
            if not pixels:
                return False
            red_cnt = 0
            for r, g, b in pixels:
                if r > _RED_R_MIN and g < _RED_G_MAX and b < _RED_B_MAX and (r - max(g, b)) > _RED_DOMINANCE:
                    red_cnt += 1
            ratio = red_cnt / len(pixels)
            return ratio > _RED_STAMP_RATIO_THRESHOLD
    except Exception:
        return False


def payment_mark_from_image(image_path: str, llm_marked: bool = False) -> str:
    """综合判定 payment_mark：LLM 已标记 或 红章检测命中 → 非空标记。

    返回：有付款痕迹时返回 "stamp"（红章）或 "已付款"，无则返回 ""。
    优先保留 LLM 判定，红章检测作为补充避免漏检。
    """
    if llm_marked:
        return "已付款"
    if detect_red_stamp(image_path):
        return "stamp"
    return ""


def validate_contract(payload: dict) -> Tuple[Optional[ReceiptData], Optional[str]]:
    """契约门禁入口。

    返回 (data, error)：合法 → (ReceiptData, None)；非法 → (None, error_msg)。
    """
    if not isinstance(payload, dict):
        return None, "AI 输出不是 JSON 对象"
    try:
        data = ReceiptData(**payload)
    except ValidationError as e:
        return None, _format_validation_error(e)

    # 业务级校验（Pydantic 之外再守一道）
    if not data.items:
        return None, "明细为空"
    if _is_nan_or_inf(data.total):
        return None, "总额非法（NaN/Infinity）"
    if data.total == 0:
        return None, "总额不能为0"
    if data.total < 0 and data.doc_form not in (DocForm.CREDIT, DocForm.CORRECTION):
        return None, f"非退款/更正单据总额不能为负数: {data.total}"
    for i, it in enumerate(data.items):
        if _is_nan_or_inf(it.qty) or _is_nan_or_inf(it.unit_price) or _is_nan_or_inf(it.amount):
            return None, f"第{i+1}行数值非法（NaN/Infinity）"
        if it.qty <= 0 or it.unit_price < 0:
            return None, f"第{i+1}行数量/单价非法"
    if not _DATE_RE.match(data.date or ""):
        return None, f"日期格式非法: {data.date}"
    if data.doc_form.value not in ALLOWED_DOC_FORMS:
        return None, f"非法单据形态: {data.doc_form}"
    if not data.vendor.strip():
        return None, "供应商为空"
    return data, None


def _is_nan_or_inf(value: float) -> bool:
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return True
    return fv != fv or fv in (float("inf"), float("-inf"))


def _format_validation_error(e: ValidationError) -> str:
    errors = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        errors.append(f"{loc}: {err['msg']}")
    return "契约校验失败: " + "; ".join(errors)


def sanitize_nan(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    """AI 可能输出 NaN/Infinity → 拒绝（完整版 A1：库存入参拒绝 Infinity）。

    default 允许传 None：调用方要「缺失即缺失」时用 default=None（例如 item 级
    confidence——把「模型未给出」落成 NULL，而不是伪造一个 0.5 的中等置信度）。
    """
    if value is None:
        return default
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return default
    if fv != fv or fv in (float("inf"), float("-inf")):
        return default
    return fv


def normalize_evidence(raw: Any) -> Optional[dict]:
    """字段级证据归一（Gap E1 / T7）：容错清洗，任何非法输入不阻断主链路。

    规则：
    - 非 dict（含 None/字符串/数字）→ 整体 None
    - bbox 非四元组 / 非数字 / NaN / 越界（不在 0-1）→ 仅 bbox 置 None
    - raw_text 空白 → None；page 非法回落 1
    - bbox 与 raw_text 双缺失 → 整体 None（不留空壳证据）
    返回 {"page": int, "bbox": list[float] | None, "raw_text": str | None} 或 None。
    """
    if not isinstance(raw, dict):
        return None
    try:
        page = int(raw.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    bbox = raw.get("bbox")
    norm_bbox = None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            vals = [float(v) for v in bbox]
            if all(v == v and v not in (float("inf"), float("-inf"))
                   and 0.0 <= v <= 1.0 for v in vals):
                norm_bbox = vals
        except (TypeError, ValueError):
            norm_bbox = None

    raw_text = raw.get("raw_text")
    if raw_text is not None:
        raw_text = str(raw_text)
        if not raw_text.strip():
            raw_text = None

    if norm_bbox is None and raw_text is None:
        return None
    return {"page": page, "bbox": norm_bbox, "raw_text": raw_text}

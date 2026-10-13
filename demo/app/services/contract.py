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


def payment_mark_tristate(image_path: str, llm_marked: bool = False) -> str:
    """AC-01-05：付款标记三态的**持久化取值**（识别落库用）。

    - 命中 LLM 判定或红章 → "已付款" / "stamp"（检测到）
    - 图片存在且可解码但未见印记 → "未付款"（明确未检测到）
    - 无原图 / 读图失败（无从判断）→ ""（不确定；绝不折算成「未付款」）
    """
    if llm_marked:
        return "已付款"
    if not image_path:
        return ""
    try:
        import os
        if not os.path.exists(image_path):
            return ""
    except Exception:
        return ""
    if detect_red_stamp(image_path):
        return "stamp"
    # 图片确实存在且解码未命中 → 明确「未见付款印章」
    return "未付款"


def payment_mark_state(payment_mark: str, image_available: bool = True) -> str:
    """把已落库的 payment_mark 映射为向外的三态标签（AC-01-05）。

    返回 "detected" / "not_detected" / "uncertain"。仅表示「原单印记的检测结果」，
    与系统付款状态（PaymentRow / paid_at）无关，绝不宣称系统已付。
    """
    mark = (payment_mark or "").strip()
    if not mark:
        return "uncertain"
    low = mark.lower()
    if low in ("未付款", "unpaid", "0", "false", "none", "无"):
        return "not_detected"
    return "detected"


# 金额/数量上限：超出基本都是小数点错位或幻觉，判为可疑（宽松校验保留明细并提示）。
MAX_LINE_AMOUNT = 1_000_000.0
MAX_LINE_QTY = 1_000_000.0
MAX_TOTAL_AMOUNT = 5_000_000.0

_NEGATIVE_FORMS = (DocForm.CREDIT, DocForm.CORRECTION)
_DEFAULT_DOC_FORM = DocForm.PRINTED.value

_DOC_FORM_SYNONYMS = {
    "thermal_receipt": "thermal", "receipt": "thermal", "pos_receipt": "thermal",
    "pos": "thermal", "cash_receipt": "thermal", "小票": "thermal", "收银小票": "thermal",
    "热敏": "thermal", "熱敏": "thermal", "收據": "thermal", "收据": "thermal",
    "delivery_note": "printed_delivery_note", "delivery": "printed_delivery_note",
    "invoice": "printed_delivery_note", "printed_invoice": "printed_delivery_note",
    "printed": "printed_delivery_note", "送货单": "printed_delivery_note", "送貨單": "printed_delivery_note",
    "handwritten": "ncr_handwritten", "handwritten_receipt": "ncr_handwritten",
    "handwritten_note": "ncr_handwritten", "ncr": "ncr_handwritten", "手写单": "ncr_handwritten",
    "手寫單": "ncr_handwritten",
    "weighing_slip": "weigh_slip", "weight_slip": "weigh_slip", "weigh": "weigh_slip",
    "磅单": "weigh_slip", "磅單": "weigh_slip",
    "credit": "credit_note", "credit_memo": "credit_note", "return_note": "credit_note",
    "refund_note": "credit_note", "退货单": "credit_note", "退貨單": "credit_note",
    "退款单": "credit_note",
    "correction": "correction_note", "amendment": "correction_note", "更正单": "correction_note",
    "更正單": "correction_note",
    "statement": "monthly_statement", "monthly": "monthly_statement", "月结单": "monthly_statement",
    "月結單": "monthly_statement", "月结账单": "monthly_statement",
}


def normalize_doc_form(raw: Any) -> Tuple[Optional[str], bool]:
    """单据形态同义词归一。返回 (标准取值或 None, 是否发生了改写)。"""
    if raw is None:
        return None, False
    text = str(getattr(raw, "value", raw)).strip()
    if text in ALLOWED_DOC_FORMS:
        return text, False
    key = re.sub(r"[\s\-]+", "_", text.lower())
    if key in ALLOWED_DOC_FORMS:
        return key, True
    if key in _DOC_FORM_SYNONYMS:
        return _DOC_FORM_SYNONYMS[key], True
    if text in _DOC_FORM_SYNONYMS:
        return _DOC_FORM_SYNONYMS[text], True
    return None, False


def normalize_date_loose(raw: Any) -> Optional[str]:
    """宽松日期解析：支持 YYYY/MM/DD、DD/MM/YYYY（港式）、中文年月日、英文月份、带时间后缀。

    解析不出（如缺年份的 "10/01"）返回 None，由调用方记警告，绝不猜年份。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if _DATE_RE.match(text):
        return text if _valid_ymd(text) else None
    text = re.sub(r"[T\s]+\d{1,2}:\d{2}(:\d{2})?.*$", "", text).strip()
    try:
        from ai_registry.tools.date_normalizer.v1_0_0 import DateNormalizerTool
        norm, _fmt = DateNormalizerTool().normalize_date(text)
    except Exception:
        norm = None
    if norm and _DATE_RE.match(norm) and _valid_ymd(norm):
        return norm
    return None


def _valid_ymd(text: str) -> bool:
    import datetime as _dt
    try:
        _dt.date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
        return True
    except ValueError:
        return False


def _line_value_problem(it, negative_form: bool) -> Optional[str]:
    if _is_nan_or_inf(it.qty) or _is_nan_or_inf(it.unit_price) or _is_nan_or_inf(it.amount):
        return "nan"
    if negative_form:
        if it.qty == 0:
            return "qty"
    elif it.qty <= 0 or it.unit_price < 0:
        return "qty"
    if abs(it.qty) > MAX_LINE_QTY or abs(it.amount) > MAX_LINE_AMOUNT or abs(it.unit_price) > MAX_LINE_AMOUNT:
        return "limit"
    return None


def validate_contract(payload: dict) -> Tuple[Optional[ReceiptData], Optional[str]]:
    """严格契约校验（对抗性输入一律失败，测试与安全场景使用）。

    返回 (data, error)：合法 → (ReceiptData, None)；非法 → (None, error_msg)。
    线上识别链路使用 validate_contract_tiered：结构致命才失败，字段可疑只带警告。
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
    negative_form = data.doc_form in _NEGATIVE_FORMS
    if data.total < 0 and not negative_form:
        return None, f"非退款/更正单据总额不能为负数: {data.total}"
    if abs(data.total) > MAX_TOTAL_AMOUNT:
        return None, f"总额超出合理范围: {data.total}"
    for i, it in enumerate(data.items):
        problem = _line_value_problem(it, negative_form)
        if problem == "nan":
            return None, f"第{i+1}行数值非法（NaN/Infinity）"
        if problem == "qty":
            return None, f"第{i+1}行数量/单价非法"
        if problem == "limit":
            return None, f"第{i+1}行金额或数量超出合理范围"
    if not _DATE_RE.match(data.date or ""):
        return None, f"日期格式非法: {data.date}"
    if data.doc_form.value not in ALLOWED_DOC_FORMS:
        return None, f"非法单据形态: {data.doc_form}"
    if not data.vendor.strip():
        return None, "供应商为空"
    return data, None


_ITEM_NUMERIC_FIELDS = {"qty": 0.0, "unit_price": 0.0, "amount": 0.0}
_FIELD_LABEL = {"qty": "数量", "unit_price": "单价", "amount": "小计", "name": "品名",
                "unit": "单位", "actual_qty": "实收数量"}
_TOP_FEE_FIELDS = ("discount_amount", "deposit_amount", "delivery_fee", "service_fee",
                   "tax_amount", "rounding_adjustment")


def _repair_validation_errors(work: dict, errors: list, warnings: list) -> bool:
    """把"字段可疑"类的 Pydantic 错误就地修成安全默认值并记警告。返回是否全部可修复。"""
    for err in errors:
        loc = list(err.get("loc") or [])
        if not loc:
            return False
        head = loc[0]
        if head == "doc_form":
            raw = work.get("doc_form")
            work["doc_form"] = _DEFAULT_DOC_FORM
            warnings.append(f"单据类型「{raw}」认不出，先按送货单处理，请核对原图")
        elif head in _TOP_FEE_FIELDS:
            work[head] = 0.0
            warnings.append("折扣、运费等费用里有一栏没读清，已先留空，请对照原图补上")
        elif head == "payment_marked":
            work["payment_marked"] = False
        elif head == "confidence":
            work["confidence"] = 0.0
        elif head == "date":
            work["date"] = ""
        elif head == "vendor":
            work["vendor"] = ""
        elif head in ("fees_detail", "adjustment_notes", "math_warnings", "field_warnings"):
            work.pop(head, None)
        elif head == "items" and len(loc) >= 3 and isinstance(loc[1], int):
            idx, field = loc[1], loc[2]
            items = work.get("items")
            if not isinstance(items, list) or idx >= len(items) or not isinstance(items[idx], dict):
                return False
            row = items[idx]
            label = _FIELD_LABEL.get(field, str(field))
            if field in _ITEM_NUMERIC_FIELDS:
                row[field] = 0.0
            elif field == "actual_qty":
                row[field] = None
            elif field in ("confidence", "evidence", "unit_conversion_warning"):
                row.pop(field, None)
            elif field in ("name", "unit", "raw_name"):
                row[field] = "" if field != "raw_name" else None
            elif field in ("is_void", "contains_huama"):
                row[field] = False
            else:
                return False
            warnings.append(f"第{idx + 1}行的{label}没读清，已先留空，请对照原图填写")
        else:
            return False
    return True


def validate_contract_tiered(payload: dict, extra_warnings: Optional[list] = None) -> Tuple[Optional[ReceiptData], Optional[str], list]:
    """分级契约校验：返回 (data, fatal_error, warnings)。

    结构致命（不是对象、明细缺失/为空、总额缺失或非法）才返回 fatal_error；
    日期、单据类型、单行数值、费用字段等"字段可疑"一律保留明细并写入警告，
    由 data.field_warnings 带到复核界面。
    """
    if not isinstance(payload, dict):
        return None, "AI 输出不是 JSON 对象", []
    import copy
    work = copy.deepcopy(payload)
    warnings: list = list(extra_warnings or [])

    form_norm, changed = normalize_doc_form(work.get("doc_form"))
    if form_norm is not None:
        work["doc_form"] = form_norm
    work.pop("field_warnings", None)

    data = None
    for _ in range(8):
        try:
            data = ReceiptData(**work)
            break
        except ValidationError as e:
            if not _repair_validation_errors(work, e.errors(), warnings):
                return None, _format_validation_error(e), warnings
    if data is None:
        return None, "契约校验失败: 字段错误过多", warnings

    if not data.items:
        return None, "明细为空", warnings
    if _is_nan_or_inf(data.total):
        return None, "总额非法（NaN/Infinity）", warnings
    if data.total == 0:
        return None, "总额不能为0", warnings

    negative_form = data.doc_form in _NEGATIVE_FORMS
    if data.total < 0 and not negative_form:
        warnings.append(f"总额是负数（{data.total}），如果这是退货单请把单据类型改成退货单")
    if abs(data.total) > MAX_TOTAL_AMOUNT:
        warnings.append(f"总额 {data.total} 大得不正常，请核对小数点")
    for i, it in enumerate(data.items):
        problem = _line_value_problem(it, negative_form)
        if problem == "nan":
            it.qty = 0.0 if _is_nan_or_inf(it.qty) else it.qty
            it.unit_price = 0.0 if _is_nan_or_inf(it.unit_price) else it.unit_price
            it.amount = 0.0 if _is_nan_or_inf(it.amount) else it.amount
            warnings.append(f"第{i+1}行有数字没读清，已先留空，请对照原图填写")
        elif problem == "qty":
            warnings.append(f"第{i+1}行的数量或单价不合常理（数量 {it.qty}，单价 {it.unit_price}），请对照原图核对")
        elif problem == "limit":
            warnings.append(f"第{i+1}行的数量或金额大得不正常，请核对小数点")

    if not _DATE_RE.match(data.date or "") or not _valid_ymd(data.date):
        norm = normalize_date_loose(data.date)
        if norm:
            data.date = norm
        else:
            if (data.date or "").strip():
                warnings.append(f"日期「{data.date}」没读清，请对照原图填写")
            else:
                warnings.append("日期没读到，请对照原图填写")
            data.date = ""
    if not (data.vendor or "").strip():
        warnings.append("供应商名称没读到，请对照原图填写")

    merged = list(dict.fromkeys(list(data.field_warnings or []) + warnings))
    data.field_warnings = merged
    return data, None, merged


_ERROR_HUMAN_RULES = [
    ("安全阻断", "识别结果没有通过安全检查，请直接手工录入，原图已经保留"),
    ("输出不是合法 JSON", "AI 没有给出能看懂的识别结果，请直接手工录入，原图已经保留"),
    ("JSON 解析失败", "AI 没有给出能看懂的识别结果，请直接手工录入，原图已经保留"),
    ("空输出", "AI 这次没有返回内容，请重新拍一张或直接手工录入，原图已经保留"),
    ("明细为空", "AI 没有在单据上读到任何一行货品，请重新拍一张或直接手工录入，原图已经保留"),
    ("总额不能为0", "AI 没有读到单据的总额，请直接手工录入，原图已经保留"),
    ("总额非法", "AI 读到的总额不是正常数字，请直接手工录入，原图已经保留"),
    ("契约校验失败", "AI 读出来的内容缺了关键的栏位，请直接手工录入，原图已经保留"),
]


def humanize_contract_error(err: Optional[str]) -> str:
    """把识别失败原因翻成店员看得懂的话：发生了什么 + 下一步怎么做。"""
    text = str(err or "").strip()
    if not text:
        return "这张单没有识别出来，请直接手工录入，原图已经保留"
    for key, msg in _ERROR_HUMAN_RULES:
        if key in text:
            return msg
    return "这张单没有识别出来，请直接手工录入，原图已经保留"


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

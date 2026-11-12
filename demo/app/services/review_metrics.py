# -*- coding: utf-8 -*-
"""M02 复核 FER 计算（首见 AI 快照 + 不可变事件口径）。

设计原则（对任务卡 M02 / G04 / A028–029 / MP02）：
- **首见快照**：单据被 AI 识别、进人工复核之前，冻结一份 AI 预填原版 + 字段字典版本 +
  逐行 `line_uuid`。此后再改都不影响该快照（评审人看的"AI 原始判定"）。
- **按 line_uuid 对齐，不按数组位置**：加行 / 删行 / 重排行都以 `line_uuid` 的并集
  作为 slot 集合，重排不会假改、加删各计一个 slot 事实。
- **三组 FER**：header / item / 费用各自独立算，避免"表头改动稀释明细改动"。
- **零改规则**：只有 owner 完成且无强制 warning override 时才可能计零改；手工单 /
  识别失败（无快照）一律 uncomparable，绝不默认成"零改"去美化分母。
- **墙钟**：parsed→submitted 秒数可注入时钟，便于确定性测试；<=30s 计"过快提交"信号。

本模块为纯函数、无 DB 依赖，便于单测与独立复核。
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

# 字段字典版本：快照与该版本绑定；版本不匹配视为不可比（不硬套新口径回算旧单）。
FIELD_DICT_VERSION = "m02.review_fields.v1"

# 表头字段（AI 预填 payload 键 -> 终值键由调用方对齐后再传入），用于 header FER。
HEADER_FIELDS = ("supplier_name", "receipt_date", "total_amount")
# 明细行字段（AI 项键名 qty，DB 行键名 quantity，build_snapshot/compute_fer 统一成 quantity）。
ITEM_FIELDS = ("name", "quantity", "unit", "unit_price", "amount")
# 费用字段（Gap 9）。
FEE_FIELDS = ("service_fee", "tax_amount", "discount_amount",
              "delivery_fee", "deposit_amount", "rounding_adjustment")

# parsed -> submitted 墙钟阈值（秒）：<= 该值视为"过快提交"信号。
WALLCLOCK_THRESHOLD_SECONDS = 30

_AI_ITEM_QTY_KEYS = ("quantity", "qty")


def _num(v):
    """宽松数值归一：坏值/空值按 0.0（与既有 diff 口径一致，不抛）。"""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _text(v):
    return str(v or "").strip()


def _norm(field, v):
    if field in ("quantity", "unit_price", "amount", "total_amount",
                 *FEE_FIELDS):
        return _num(v)
    return _text(v)


def _item_quantity(it):
    for k in _AI_ITEM_QTY_KEYS:
        if k in it:
            return _num(it.get(k))
    return 0.0


def build_snapshot(ai_payload, item_dicts):
    """从 AI 预填原版 + 刚写库的明细行（含 line_uuid）构造首见快照。

    返回 dict；AI payload 缺失 / 非 dict / items 非 list 时返回 None（=uncomparable）。
    `item_dicts` 为 `db.get_receipt_items()` 的返回（按写入顺序），其 line_uuid 即
    复核期的稳定行身份，快照沿用同一 uuid 以便后续按 uuid 对齐。
    """
    if not isinstance(ai_payload, dict) or not ai_payload:
        return None
    ai_items = ai_payload.get("items")
    if not isinstance(ai_items, list):
        return None
    item_dicts = item_dicts or []

    lines = []
    for idx, it in enumerate(ai_items):
        it = it if isinstance(it, dict) else {}
        db_row = item_dicts[idx] if idx < len(item_dicts) else {}
        lines.append({
            "line_uuid": _text(db_row.get("line_uuid")) or uuid.uuid4().hex,
            "ordinal": idx,
            "name": _text(it.get("name")),
            "quantity": _item_quantity(it),
            "unit": _text(it.get("unit")),
            "unit_price": _num(it.get("unit_price")),
            "amount": _num(it.get("amount")),
            "sku_id": it.get("sku_id"),
        })

    header = {
        "supplier_name": _text(ai_payload.get("vendor")),
        "receipt_date": _text(ai_payload.get("date")),
        "total_amount": _num(ai_payload.get("total")),
    }
    fees = {f: _num(ai_payload.get(f)) for f in FEE_FIELDS}

    snap = {
        "field_dict_version": FIELD_DICT_VERSION,
        "header": header,
        "items": lines,
        "fees": fees,
    }
    snap["snapshot_sha256"] = _snapshot_sha256(snap)
    return snap


def _snapshot_sha256(snap):
    body = {
        "field_dict_version": snap.get("field_dict_version"),
        "header": snap.get("header"),
        "items": snap.get("items"),
        "fees": snap.get("fees"),
    }
    blob = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _uncomparable(reason):
    return {
        "comparable": False,
        "reason": reason,
        "field_dict_version": FIELD_DICT_VERSION,
        "header_fer": None,
        "item_fer": None,
        "fee_fer": None,
        "overall_fer": None,
        "item_slots": 0,
        "item_edited_slots": 0,
        "zero_edit": False,
    }


def compute_fer(snapshot, final_header, final_items, final_fees):
    """按 line_uuid 并集计算 header/item/费用三组 FER。

    - 缺快照 / 版本不匹配 -> comparable=False（uncomparable，绝不折 0 冒充）。
    - item slots = snapshot 与 final 的 line_uuid 并集（保序去重）；仅存于一侧
      （加行/删行）计一次 edited；两侧都有则逐 `ITEM_FIELDS` 比。
    - `final_header` / `final_fees` 为归一后的 dict；`final_items` 为 DB dict 列表。
    """
    if not isinstance(snapshot, dict) or not snapshot:
        return _uncomparable("no_snapshot")
    if snapshot.get("field_dict_version") != FIELD_DICT_VERSION:
        return _uncomparable("version_mismatch")

    final_header = final_header or {}
    final_fees = final_fees or {}
    final_items = final_items or []

    snap_by_uuid = {}
    for it in snapshot.get("items") or []:
        if isinstance(it, dict) and _text(it.get("line_uuid")):
            snap_by_uuid[_text(it["line_uuid"])] = it
    fin_by_uuid = {}
    for it in final_items:
        if isinstance(it, dict) and _text(it.get("line_uuid")):
            fin_by_uuid[_text(it["line_uuid"])] = it

    uuids = list(dict.fromkeys(list(snap_by_uuid.keys()) + list(fin_by_uuid.keys())))

    edited_slots = 0
    for u in uuids:
        s, f = snap_by_uuid.get(u), fin_by_uuid.get(u)
        if s is None or f is None:      # 加行 / 删行：slot 变更事实
            edited_slots += 1
            continue
        if any(_norm(fld, s.get(fld)) != _norm(fld, f.get(fld))
               for fld in ITEM_FIELDS):
            edited_slots += 1

    header_edited = sum(
        1 for fld in HEADER_FIELDS
        if _norm(fld, (snapshot.get("header") or {}).get(fld))
        != _norm(fld, final_header.get(fld)))
    fee_edited = sum(
        1 for fld in FEE_FIELDS
        if _norm(fld, (snapshot.get("fees") or {}).get(fld))
        != _norm(fld, final_fees.get(fld)))

    item_slots = len(uuids)
    item_fer = (edited_slots / item_slots) if item_slots else 0.0
    header_fer = header_edited / len(HEADER_FIELDS)
    fee_fer = fee_edited / len(FEE_FIELDS)
    denom = len(HEADER_FIELDS) + item_slots + len(FEE_FIELDS)
    overall_fer = ((header_edited + edited_slots + fee_edited) / denom
                   if denom else 0.0)

    return {
        "comparable": True,
        "reason": "",
        "field_dict_version": FIELD_DICT_VERSION,
        "header_fer": round(header_fer, 4),
        "item_fer": round(item_fer, 4),
        "fee_fer": round(fee_fer, 4),
        "overall_fer": round(overall_fer, 4),
        "item_slots": item_slots,
        "item_edited_slots": edited_slots,
        "zero_edit": (header_edited == 0 and edited_slots == 0
                      and fee_edited == 0),
    }


def zero_edit_eligible(owner_completed, forced_warning_override):
    """零改分母准入：owner 完成且无强制 warning override。"""
    return bool(owner_completed) and not bool(forced_warning_override)


def is_zero_edit(fer, owner_completed, forced_warning_override):
    """是否计为零改：需可比、三组无变化，且满足零改分母准入。

    manual / 识别失败（无快照 -> comparable=False）恒 False，不会伪造零改。
    """
    if not isinstance(fer, dict) or not fer.get("comparable"):
        return False
    if not fer.get("zero_edit"):
        return False
    return zero_edit_eligible(owner_completed, forced_warning_override)


def _parse_ts(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def wallclock_parsed_to_submitted(parsed_at, submitted_at=None, *, clock=None):
    """parsed -> submitted 墙钟秒数。

    `submitted_at` 缺省且给了 `clock` 时用 `clock()`（默认 UTC now）作为 submitted
    时刻；任一时间不可解析则返回 None（未知，不折 0）。
    """
    p = _parse_ts(parsed_at)
    if p is None:
        return None
    if submitted_at is None:
        s = (clock or (lambda: datetime.now(timezone.utc)))()
        s = _parse_ts(s)
    else:
        s = _parse_ts(submitted_at)
    if s is None:
        return None
    return (s - p).total_seconds()


def is_fast_submission(seconds, threshold=WALLCLOCK_THRESHOLD_SECONDS):
    """是否过快提交（parsed 后 <= threshold 秒即提交）；未知（None）返回 False。"""
    return seconds is not None and 0 <= seconds <= threshold

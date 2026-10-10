"""
AI 效果观测与评测中心 - 解析日志与链路审计模块 (Backend Service)
提供单据级解析明细聚合、Token/耗时统计、AI 预填 vs 最终入库 Diff 提取、多维筛选与分页。
"""

import json
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.services.receipt_utils import canonical_sku_name

logger = logging.getLogger("parsing_logs")


def _safe_json_loads(val: Any, default: Any = None) -> Any:
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    if not isinstance(val, str):
        return default
    val_str = val.strip()
    if not val_str:
        return default
    try:
        return json.loads(val_str)
    except Exception:
        return default


def _format_num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def anonymize_vendor(name: Any) -> str:
    """供应商名称别名化脱敏 (如 Vendor_A, Vendor_B)。"""
    if not name or not str(name).strip():
        return "Vendor_Unknown"
    s = str(name).strip()
    if s.startswith("Vendor_"):
        return s
    hash_val = sum(ord(c) for c in s)
    letter = chr(ord('A') + (hash_val % 26))
    num = (hash_val % 90) + 10
    return f"Vendor_{letter}{num}"


def compute_field_diffs(
    ai_prefill: Dict[str, Any],
    receipt_row: Any,
    receipt_items: List[Any],
) -> Tuple[bool, int, List[str], List[Dict[str, Any]]]:
    """比对 receipts.ai_prefill_json 与当前 receipts / receipt_items 数据。
    返回 (is_modified, modified_count, modified_fields, diff_details)。
    """
    diffs: List[Dict[str, Any]] = []
    modified_fields: List[str] = []

    if not isinstance(ai_prefill, dict) or not ai_prefill:
        # 无 AI 原始预填，检查是否有店员明确保存修改记录
        has_edit_audit = False
        audit_raw = getattr(receipt_row, "audit_logs_json", "") or "[]"
        if "save_edited" in str(audit_raw) or str(getattr(receipt_row, "status", "")) == "edited":
            has_edit_audit = True
        return has_edit_audit, 1 if has_edit_audit else 0, ["receipt"] if has_edit_audit else [], []

    # 1. 供应商名称比对
    ai_vendor = str(ai_prefill.get("vendor") or ai_prefill.get("supplier_name") or "").strip()
    cur_vendor = str(getattr(receipt_row, "supplier_name", "") or "").strip()
    if ai_vendor and cur_vendor and ai_vendor != cur_vendor:
        modified_fields.append("supplier_name")
        diffs.append({
            "field": "supplier_name",
            "label": "供应商名称",
            "ai_value": anonymize_vendor(ai_vendor),
            "user_value": anonymize_vendor(cur_vendor),
            "type": "modified",
        })

    # 2. 开单日期比对
    ai_date = str(ai_prefill.get("date") or ai_prefill.get("receipt_date") or "").strip()
    cur_date = str(getattr(receipt_row, "receipt_date", "") or "").strip()
    if ai_date and cur_date and ai_date != cur_date:
        modified_fields.append("receipt_date")
        diffs.append({
            "field": "receipt_date",
            "label": "开单日期",
            "ai_value": ai_date,
            "user_value": cur_date,
            "type": "modified",
        })

    # 3. 核算总额比对
    ai_total = ai_prefill.get("total")
    if ai_total is None:
        ai_total = ai_prefill.get("total_amount")
    cur_total = getattr(receipt_row, "total_amount", 0.0)
    if ai_total is not None and abs(_format_num(ai_total) - _format_num(cur_total)) > 0.01:
        modified_fields.append("total_amount")
        diffs.append({
            "field": "total_amount",
            "label": "核算总额",
            "ai_value": round(_format_num(ai_total), 2),
            "user_value": round(_format_num(cur_total), 2),
            "type": "modified",
        })

    # 4. 明细行比对
    ai_items = ai_prefill.get("items")
    ai_items = ai_items if isinstance(ai_items, list) else []

    # 格式化当前 receipt_items
    cur_item_list = []
    for it in receipt_items:
        cur_item_list.append({
            "name": str(getattr(it, "name", "") or "").strip(),
            "quantity": _format_num(getattr(it, "quantity", 0.0)),
            "unit": str(getattr(it, "unit", "") or "").strip(),
            "unit_price": _format_num(getattr(it, "unit_price", 0.0)),
            "amount": _format_num(getattr(it, "amount", 0.0)),
        })

    max_len = max(len(ai_items), len(cur_item_list))
    for i in range(max_len):
        if i < len(ai_items) and i < len(cur_item_list):
            ai_it = ai_items[i] if isinstance(ai_items[i], dict) else {}
            cur_it = cur_item_list[i]
            
            # 比较品名
            ai_name = canonical_sku_name(str(ai_it.get("name") or "").strip())
            cur_name = canonical_sku_name(str(cur_it.get("name") or "").strip())
            if ai_name and cur_name and ai_name != cur_name:
                field_key = f"items[{i}].name"
                modified_fields.append(field_key)
                diffs.append({
                    "field": field_key,
                    "label": f"第{i+1}行品名",
                    "ai_value": str(ai_it.get("name") or "").strip(),
                    "user_value": str(cur_it.get("name") or "").strip(),
                    "type": "modified",
                })

            # 比较单价
            ai_up = _format_num(ai_it.get("unit_price"))
            cur_up = cur_it.get("unit_price", 0.0)
            if ai_up > 0 and abs(ai_up - cur_up) > 0.01:
                field_key = f"items[{i}].unit_price"
                modified_fields.append(field_key)
                diffs.append({
                    "field": field_key,
                    "label": f"第{i+1}行单价",
                    "ai_value": round(ai_up, 2),
                    "user_value": round(cur_up, 2),
                    "type": "modified",
                })

            # 比较数量
            ai_qty = _format_num(ai_it.get("quantity"))
            cur_qty = cur_it.get("quantity", 0.0)
            if ai_qty > 0 and abs(ai_qty - cur_qty) > 0.01:
                field_key = f"items[{i}].quantity"
                modified_fields.append(field_key)
                diffs.append({
                    "field": field_key,
                    "label": f"第{i+1}行数量",
                    "ai_value": round(ai_qty, 2),
                    "user_value": round(cur_qty, 2),
                    "type": "modified",
                })

            # 比较金额
            ai_amt = _format_num(ai_it.get("amount"))
            cur_amt = cur_it.get("amount", 0.0)
            if ai_amt > 0 and abs(ai_amt - cur_amt) > 0.01:
                field_key = f"items[{i}].amount"
                modified_fields.append(field_key)
                diffs.append({
                    "field": field_key,
                    "label": f"第{i+1}行金额",
                    "ai_value": round(ai_amt, 2),
                    "user_value": round(cur_amt, 2),
                    "type": "modified",
                })

        elif i < len(cur_item_list):
            # 用户新增行
            cur_it = cur_item_list[i]
            field_key = f"items[{i}]"
            modified_fields.append(field_key)
            diffs.append({
                "field": field_key,
                "label": f"第{i+1}行(新增)",
                "ai_value": None,
                "user_value": f"{cur_it.get('name')} x{cur_it.get('quantity')} = {cur_it.get('amount')}",
                "type": "added",
            })
        else:
            # 用户删除了行
            ai_it = ai_items[i] if isinstance(ai_items[i], dict) else {}
            field_key = f"items[{i}]"
            modified_fields.append(field_key)
            diffs.append({
                "field": field_key,
                "label": f"第{i+1}行(删除)",
                "ai_value": f"{ai_it.get('name')} x{ai_it.get('quantity')} = {ai_it.get('amount')}",
                "user_value": None,
                "type": "deleted",
            })

    # 兜底判定：如果状态为 edited，或审计日志中含有 save_edited，但字段差异为 0，视为有改动
    audit_raw = getattr(receipt_row, "audit_logs_json", "") or "[]"
    status_str = str(getattr(receipt_row, "status", ""))
    is_modified = bool(diffs) or (status_str == "edited") or ("save_edited" in str(audit_raw))
    modified_count = len(diffs) if diffs else (1 if is_modified else 0)

    return is_modified, modified_count, modified_fields, diffs


def query_parsing_logs(
    s: Any,
    tenant_id: Optional[str] = None,
    status: str = "all",
    gate_status: str = "all",
    feedback: str = "all",
    is_modified: str = "all",
    search: str = "",
    date_range: str = "all",
    start_date: str = "",
    end_date: str = "",
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """多维联查解析日志，包含 KPI 汇总统计与分页明细。"""
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))

    # 1. 构建主表过滤
    q = s.query(db._ReceiptRow).filter(db._ReceiptRow.deleted_at.is_(None))

    if tenant_id and tenant_id != "all":
        q = q.filter(db._ReceiptRow.tenant_id == tenant_id)

    # 时间过滤
    now_dt = datetime.now()
    if date_range == "24h":
        start_ts = (now_dt - timedelta(days=1)).isoformat()
        q = q.filter(db._ReceiptRow.created_at >= start_ts)
    elif date_range == "7d":
        start_ts = (now_dt - timedelta(days=7)).isoformat()
        q = q.filter(db._ReceiptRow.created_at >= start_ts)
    elif date_range == "14d":
        start_ts = (now_dt - timedelta(days=14)).isoformat()
        q = q.filter(db._ReceiptRow.created_at >= start_ts)
    elif date_range == "30d":
        start_ts = (now_dt - timedelta(days=30)).isoformat()
        q = q.filter(db._ReceiptRow.created_at >= start_ts)

    if start_date:
        q = q.filter(db._ReceiptRow.created_at >= start_date)
    if end_date:
        q = q.filter(db._ReceiptRow.created_at <= end_date)

    # 状态过滤
    if status == "success":
        q = q.filter(db._ReceiptRow.status.in_(["parsed", "edited", "approved"]))
    elif status == "warning":
        q = q.filter(db._ReceiptRow.status == "parsed_with_warnings")
    elif status == "error":
        q = q.filter(db._ReceiptRow.status == "error")

    # 门禁状态过滤 (gate_status: all, triggered, none, rejected, warning)
    reject_rids_q = s.query(db._DecisionLogRow.receipt_id).filter(
        db._DecisionLogRow.receipt_id.isnot(None),
        (db._DecisionLogRow.ai_value.like("%gate_reject%"))
        | (db._DecisionLogRow.ai_value.like("%gate_err%"))
        | (db._DecisionLogRow.extra.like("%gate_reject%"))
    )
    warn_condition = (
        (db._ReceiptRow.status == "parsed_with_warnings")
        | ((db._ReceiptRow.math_warnings_json.isnot(None)) & (db._ReceiptRow.math_warnings_json != "") & (db._ReceiptRow.math_warnings_json != "[]"))
        | ((db._ReceiptRow.quality_warnings_json.isnot(None)) & (db._ReceiptRow.quality_warnings_json != "") & (db._ReceiptRow.quality_warnings_json != "[]"))
        | (db._ReceiptRow.image_guard_json.like("%\"flagged\": true%") | db._ReceiptRow.image_guard_json.like("%\"flagged\":true%"))
    )
    if gate_status == "rejected":
        q = q.filter(db._ReceiptRow.id.in_(reject_rids_q))
    elif gate_status == "warning":
        q = q.filter(~db._ReceiptRow.id.in_(reject_rids_q), warn_condition)
    elif gate_status == "triggered":
        q = q.filter((db._ReceiptRow.id.in_(reject_rids_q)) | (warn_condition))
    elif gate_status == "none":
        q = q.filter(~db._ReceiptRow.id.in_(reject_rids_q), ~warn_condition)

    # 改动过滤 (SQL 层粗筛)
    if is_modified == "yes":
        q = q.filter(
            (db._ReceiptRow.status == "edited")
            | (db._ReceiptRow.audit_logs_json.like("%save_edited%"))
        )
    elif is_modified == "no":
        q = q.filter(
            db._ReceiptRow.status != "edited",
            (db._ReceiptRow.audit_logs_json.is_(None))
            | (~db._ReceiptRow.audit_logs_json.like("%save_edited%")),
        )

    # 综合搜索框
    search_str = (search or "").strip()
    if search_str:
        s_num = search_str.lstrip("#").strip()
        if s_num.isdigit():
            target_id = int(s_num)
            q = q.filter(
                (db._ReceiptRow.id == target_id)
                | (db._ReceiptRow.supplier_name.like(f"%{search_str}%"))
            )
        else:
            # 模糊匹配供应商、或关联事件中的 job_id
            matched_event_rids = s.query(db._UserEventRow.receipt_id).filter(
                db._UserEventRow.properties.like(f"%{search_str}%")
            )
            q = q.filter(
                (db._ReceiptRow.supplier_name.like(f"%{search_str}%"))
                | (db._ReceiptRow.id.in_(matched_event_rids))
            )

    # 反馈过滤
    if feedback == "liked":
        liked_sub = s.query(db._ReceiptFeedbackRow.receipt_id).filter(
            db._ReceiptFeedbackRow.item_index.is_(None),
            db._ReceiptFeedbackRow.like == 1,
        )
        q = q.filter(db._ReceiptRow.id.in_(liked_sub))
    elif feedback == "disliked":
        disliked_sub = s.query(db._ReceiptFeedbackRow.receipt_id).filter(
            db._ReceiptFeedbackRow.item_index.is_(None),
            db._ReceiptFeedbackRow.like == -1,
        )
        q = q.filter(db._ReceiptRow.id.in_(disliked_sub))
    elif feedback == "unrated":
        rated_sub = s.query(db._ReceiptFeedbackRow.receipt_id).filter(
            db._ReceiptFeedbackRow.item_index.is_(None),
            db._ReceiptFeedbackRow.like.in_([1, -1]),
        )
        q = q.filter(~db._ReceiptRow.id.in_(rated_sub))

    # 计算总数与分页
    total_items = q.count()
    total_pages = math.ceil(total_items / page_size) if total_items > 0 else 1

    # 获取当前页单据行
    receipt_rows = q.order_by(db._ReceiptRow.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    page_rids = [r.id for r in receipt_rows]

    # 批量关联抓取附属数据
    decision_map: Dict[int, Any] = {}
    all_decisions_map: Dict[int, List[Any]] = {}
    event_map: Dict[int, List[Any]] = {}
    feedback_map: Dict[int, Any] = {}
    items_map: Dict[int, List[Any]] = {}

    if page_rids:
        # 1. 抓取最新 extract 或相关 decision log
        d_rows = s.query(db._DecisionLogRow).filter(
            db._DecisionLogRow.receipt_id.in_(page_rids)
        ).order_by(db._DecisionLogRow.id.desc()).all()
        for d in d_rows:
            if d.receipt_id not in all_decisions_map:
                all_decisions_map[d.receipt_id] = []
            all_decisions_map[d.receipt_id].append(d)
            # 优先采用 extract 类型的决策记录
            if d.receipt_id not in decision_map:
                decision_map[d.receipt_id] = d
            elif d.decision_type == "extract" and decision_map[d.receipt_id].decision_type != "extract":
                decision_map[d.receipt_id] = d

        # 2. 抓取 user_events 还原链路图与执行事件
        e_rows = s.query(db._UserEventRow).filter(
            db._UserEventRow.receipt_id.in_(page_rids)
        ).order_by(db._UserEventRow.id.asc()).all()
        for e in e_rows:
            if e.receipt_id not in event_map:
                event_map[e.receipt_id] = []
            event_map[e.receipt_id].append(e)

        # 3. 抓取整单反馈
        fb_rows = s.query(db._ReceiptFeedbackRow).filter(
            db._ReceiptFeedbackRow.receipt_id.in_(page_rids),
            db._ReceiptFeedbackRow.item_index.is_(None),
        ).order_by(db._ReceiptFeedbackRow.id.desc()).all()
        for fb in fb_rows:
            if fb.receipt_id not in feedback_map:
                feedback_map[fb.receipt_id] = fb

        # 4. 抓取当页所有的明细行 receipt_items
        it_rows = s.query(db._ItemRow).filter(
            db._ItemRow.receipt_id.in_(page_rids)
        ).order_by(db._ItemRow.id.asc()).all()
        for it in it_rows:
            if it.receipt_id not in items_map:
                items_map[it.receipt_id] = []
            items_map[it.receipt_id].append(it)

    # 逐条组装明细 items
    items_data = []
    for r in receipt_rows:
        rid = r.id
        dec = decision_map.get(rid)
        dec_extra = _safe_json_loads(getattr(dec, "extra", None), default={}) or {}
        fb = feedback_map.get(rid)
        r_events = event_map.get(rid, [])
        r_items = items_map.get(rid, [])

        # 解析 graph_id / job_id
        graph_id = None
        for ev in r_events:
            p = _safe_json_loads(getattr(ev, "properties", None), default={}) or {}
            if "job_id" in p and p["job_id"]:
                graph_id = str(p["job_id"])
                break
        if not graph_id and "job_id" in dec_extra:
            graph_id = str(dec_extra["job_id"])
        if not graph_id:
            graph_id = f"graph-{rid}"

        # 解析模型引擎与模型标识
        engine = getattr(dec, "engine", "") or "vlm"
        model = getattr(dec, "model", "") or "-"

        # 解析 Token 消耗
        tokens_prompt = int(dec_extra.get("tokens_prompt") or 0)
        tokens_completion = int(dec_extra.get("tokens_completion") or 0)
        tokens_cached = int(dec_extra.get("tokens_cached") or 0)
        tokens_total = int(dec_extra.get("tokens_total") or (tokens_prompt + tokens_completion))
        cache_hit_rate = round(tokens_cached / tokens_prompt, 4) if tokens_prompt > 0 else 0.0
        cost_hkd = float(dec_extra.get("cost_hkd") or 0.0)
        cost_estimated = int(dec_extra.get("cost_estimated") or 0)

        # 解析用时与分段
        elapsed_raw = dec_extra.get("elapsed_ms")
        elapsed_dict = {"total": 0.0, "extract": 0.0, "rag": 0.0, "audit": 0.0}
        if isinstance(elapsed_raw, dict):
            extract_ms = _format_num(elapsed_raw.get("extract"))
            rag_ms = _format_num(elapsed_raw.get("rag"))
            audit_ms = _format_num(elapsed_raw.get("audit"))
            total_ms = _format_num(elapsed_raw.get("total"))
            if total_ms <= 0:
                total_ms = round(extract_ms + rag_ms + audit_ms, 1)
            elapsed_dict = {
                "total": total_ms,
                "extract": extract_ms,
                "rag": rag_ms,
                "audit": audit_ms,
            }
        elif isinstance(elapsed_raw, (int, float)):
            elapsed_dict["total"] = float(elapsed_raw)
            elapsed_dict["extract"] = float(elapsed_raw)
        else:
            # 兜底尝试从 user_event 中寻找耗时
            for ev in r_events:
                p = _safe_json_loads(getattr(ev, "properties", None), default={}) or {}
                if "elapsed_ms" in p and isinstance(p["elapsed_ms"], (int, float)):
                    elapsed_dict["total"] = float(p["elapsed_ms"])
                    elapsed_dict["extract"] = float(p["elapsed_ms"])
                    break

        # 状态判定
        status_raw = str(getattr(r, "status", "") or "unknown")
        math_warns_raw = _safe_json_loads(getattr(r, "math_warnings_json", None), default=[]) or []
        math_warns: List[str] = []
        if isinstance(math_warns_raw, list):
            math_warns = [str(w) for w in math_warns_raw if w]
        elif isinstance(math_warns_raw, str) and math_warns_raw.strip():
            math_warns = [math_warns_raw.strip()]

        quality_warns_raw = _safe_json_loads(getattr(r, "quality_warnings_json", None), default=[]) or []
        quality_warns: List[str] = []
        if isinstance(quality_warns_raw, list):
            quality_warns = [str(w) for w in quality_warns_raw if w]
        elif isinstance(quality_warns_raw, str) and quality_warns_raw.strip():
            quality_warns = [quality_warns_raw.strip()]

        img_guard = _safe_json_loads(getattr(r, "image_guard_json", None), default={}) or {}
        has_security_warning = bool(img_guard.get("flagged", False)) if isinstance(img_guard, dict) else False
        security_reasons: List[str] = []
        if has_security_warning and isinstance(img_guard, dict):
            sr_raw = img_guard.get("reasons", [])
            if isinstance(sr_raw, list):
                security_reasons = [str(sr) for sr in sr_raw if sr]
            elif isinstance(sr_raw, str) and sr_raw.strip():
                security_reasons = [sr_raw.strip()]
            if not security_reasons:
                security_reasons = ["图内存在隐藏文字或异常内容风险"]

        # 多源门禁归因与判定引擎
        candidate_gate_errs: List[str] = []
        has_reject = False
        r_decs = all_decisions_map.get(rid, [])
        for d in r_decs:
            av = _safe_json_loads(getattr(d, "ai_value", None), default={}) or {}
            ex = _safe_json_loads(getattr(d, "extra", None), default={}) or {}
            d_status = ""
            d_err = ""
            if isinstance(av, dict):
                d_status = str(av.get("status") or "")
                d_err = str(av.get("gate_err") or "")
            if not d_status and isinstance(ex, dict):
                d_status = str(ex.get("status") or "")
            if not d_err and isinstance(ex, dict):
                d_err = str(ex.get("gate_err") or "")

            if d_status in ("gate_reject", "gate_reject_fast") or bool(d_err):
                has_reject = True
                if d_err and d_err not in candidate_gate_errs:
                    candidate_gate_errs.append(d_err)

        # 用户链路事件检查
        contract_event_warns: List[str] = []
        math_event_warns: List[str] = []
        for ev in r_events:
            etype = getattr(ev, "event_type", "")
            p = _safe_json_loads(getattr(ev, "properties", None), default={}) or {}
            if not isinstance(p, dict):
                continue
            if etype == "contract_guard_checked":
                if p.get("is_valid") is False or p.get("valid") is False:
                    contract_event_warns.append(str(p.get("reason") or p.get("error") or "契约校验不通过"))
            elif etype == "math_guard_checked":
                if p.get("is_valid") is False or p.get("valid") is False:
                    math_event_warns.append(str(p.get("reason") or p.get("error") or "算术平算不一致"))

        # 综合计算 gate_status
        has_math_warning = len(math_warns) > 0
        has_quality_warning = len(quality_warns) > 0
        if has_reject:
            gate_status_val = "rejected"
        elif has_math_warning or has_quality_warning or has_security_warning or status_raw == "parsed_with_warnings":
            gate_status_val = "warning"
        else:
            gate_status_val = "none"

        gate_triggered = (gate_status_val != "none")

        # 组装明细 details
        gate_details: List[Dict[str, Any]] = []
        type_labels_map = {
            "math": "算术门禁",
            "contract": "契约门禁",
            "quality": "画质门禁",
            "security": "安全门禁",
            "extract": "抽取门禁",
        }

        if has_reject:
            err_text = candidate_gate_errs[0] if candidate_gate_errs else "门禁拦截触发"
            lower_err = err_text.lower()
            if any(k in lower_err or k in err_text for k in ["算术", "合计", "总额", "差额", "math", "金额"]):
                rej_type = "math"
            elif any(k in lower_err or k in err_text for k in ["花码", "画质", "清晰度", "quality", "模糊"]):
                rej_type = "quality"
            elif any(k in lower_err or k in err_text for k in ["隐藏文字", "隐写", "image_guard", "注入", "安全"]):
                rej_type = "security"
            elif any(k in lower_err or k in err_text for k in ["抽取", "识别失败", "extract"]):
                rej_type = "extract"
            else:
                rej_type = "contract"

            gate_details.append({
                "type": rej_type,
                "type_label": type_labels_map.get(rej_type, "契约门禁"),
                "level": "rejected",
                "reason": err_text,
                "source": "ai_decision_log",
            })

        for mw in math_warns:
            gate_details.append({
                "type": "math",
                "type_label": "算术门禁",
                "level": "rejected" if (gate_status_val == "rejected" and not candidate_gate_errs) else "warning",
                "reason": mw,
                "source": "math_warnings_json",
            })

        for qw in quality_warns:
            gate_details.append({
                "type": "quality",
                "type_label": "画质门禁",
                "level": "warning",
                "reason": qw,
                "source": "quality_warnings_json",
            })

        for sr in security_reasons:
            gate_details.append({
                "type": "security",
                "type_label": "安全门禁",
                "level": "warning",
                "reason": sr,
                "source": "image_guard_json",
            })

        for cw in contract_event_warns:
            gate_details.append({
                "type": "contract",
                "type_label": "契约门禁",
                "level": "warning",
                "reason": cw,
                "source": "user_events",
            })

        for mw in math_event_warns:
            if not any(d["reason"] == mw for d in gate_details):
                gate_details.append({
                    "type": "math",
                    "type_label": "算术门禁",
                    "level": "warning",
                    "reason": mw,
                    "source": "user_events",
                })

        if not gate_details and status_raw == "parsed_with_warnings":
            gate_details.append({
                "type": "contract",
                "type_label": "契约门禁",
                "level": "warning",
                "reason": "单据带告警通过，存在校验未达标项",
                "source": "receipt_status",
            })

        # 主门禁类型判定
        if not gate_triggered:
            primary_type = ""
            primary_type_label = ""
            badge_label = "[平算无门禁]"
            gate_summary = "算术契约检验全部通过"
        else:
            primary_detail = None
            for d in gate_details:
                if d.get("level") == "rejected":
                    primary_detail = d
                    break
            if not primary_detail and gate_details:
                primary_detail = gate_details[0]

            primary_type = primary_detail["type"] if primary_detail else "math"
            primary_type_label = type_labels_map.get(primary_type, "")

            if gate_status_val == "rejected":
                badge_label = f"[门禁拦截: {primary_type_label}]" if primary_type_label else "[门禁拦截]"
            else:
                badge_label = f"[门禁告警: {primary_type_label}]" if primary_type_label else "[门禁告警]"

            gate_summary = primary_detail["reason"] if primary_detail else f"{primary_type_label}异常"

        gate_info = {
            "triggered": gate_triggered,
            "status": gate_status_val,
            "badge_label": badge_label,
            "primary_type": primary_type,
            "primary_type_label": primary_type_label,
            "summary": gate_summary,
            "details": gate_details,
        }

        # 整体解析状态判定
        if status_raw == "error":
            parse_status = "error"
        elif status_raw == "parsed_with_warnings" or gate_status_val == "warning":
            parse_status = "warning"
        elif status_raw in ("uploaded", "parsing"):
            parse_status = "processing"
        elif status_raw in ("parsed", "edited", "approved"):
            if gate_triggered and gate_status_val != "none":
                parse_status = "warning"
            else:
                parse_status = "success"
        else:
            parse_status = "success"

        # 反馈判定
        fb_like = getattr(fb, "like", None)
        if fb_like == 1:
            fb_label = "liked"
        elif fb_like == -1:
            fb_label = "disliked"
        else:
            fb_label = "unrated"

        feedback_data = {
            "like": fb_like,
            "label": fb_label,
            "comment": getattr(fb, "comment", "") or "",
            "created_at": getattr(fb, "created_at", "") or "",
        }

        # Diff 比对计算
        ai_prefill = _safe_json_loads(getattr(r, "ai_prefill_json", None), default={}) or {}
        is_mod, mod_count, mod_fields, diffs = compute_field_diffs(ai_prefill, r, r_items)

        # 构建单据审计时间流
        timeline = []
        # 事件 1: upload
        for ev in r_events:
            p = _safe_json_loads(getattr(ev, "properties", None), default={}) or {}
            timeline.append({
                "ts": getattr(ev, "ts", "") or "",
                "type": getattr(ev, "event_type", ""),
                "who": getattr(ev, "account_id", "") or "system",
                "details": p,
            })
        # 审计日志合并
        audit_logs = _safe_json_loads(getattr(r, "audit_logs_json", None), default=[]) or []
        if isinstance(audit_logs, list):
            for alog in audit_logs:
                if isinstance(alog, dict):
                    timeline.append({
                        "ts": alog.get("ts", "") or "",
                        "type": alog.get("action", "audit"),
                        "who": alog.get("who", "staff"),
                        "details": alog,
                    })

        items_data.append({
            "record_id": rid,
            "tenant_id": getattr(r, "tenant_id", "default") or "default",
            "graph_id": graph_id,
            "created_at": getattr(r, "created_at", "") or "",
            "supplier_name": anonymize_vendor(getattr(r, "supplier_name", "") or ""),
            "doc_form": getattr(r, "doc_form", "") or "standard",
            "image_path": getattr(r, "image_path", "") or "",
            "total_amount": round(_format_num(getattr(r, "total_amount", 0.0)), 2),
            "engine": engine,
            "model": model,
            "use_grey": int(getattr(r, "use_grey", 0) or 0),
            "token_usage": {
                "tokens_prompt": tokens_prompt,
                "tokens_completion": tokens_completion,
                "tokens_cached": tokens_cached,
                "tokens_total": tokens_total,
                "cache_hit_rate": cache_hit_rate,
                "cost_hkd": cost_hkd,
                "cost_estimated": cost_estimated,
            },
            "elapsed_ms": elapsed_dict,
            "parse_status": parse_status,
            "status_raw": status_raw,
            "gate_info": gate_info,
            "feedback": feedback_data,
            "modification": {
                "is_modified": is_mod,
                "modified_count": mod_count,
                "modified_fields": mod_fields,
                "diffs": diffs,
            },
            "warnings": {
                "math_warnings": math_warns,
                "quality_warnings": quality_warns,
                "image_guard_flagged": has_security_warning,
            },
            "timeline": timeline,
        })

    # KPI 汇总计算 (在当前筛选条件下或当前租户与时间范围内计算)
    if total_items == 0:
        summary_data = {
            "total_parses": 0,
            "success_count": 0,
            "warning_count": 0,
            "error_count": 0,
            "success_rate": 0.0,
            "avg_elapsed_ms": 0.0,
            "avg_tokens": 0,
            "likes_count": 0,
            "dislikes_count": 0,
            "positive_rate": 0.0,
            "modified_count": 0,
            "edit_rate": 0.0,
            "gate_triggered_count": 0,
            "gate_trigger_rate": 0.0,
            "gate_breakdown": {
                "math": 0,
                "contract": 0,
                "quality": 0,
                "security": 0,
            },
            "disliked_receipts_count": 0,
            "feedback_receipts_count": 0,
            "dislike_rate": 0.0,
            "dislike_frequency": 1.0,
            "recovery_attempts": 0,
            "recovery_entry_rate": 0.0,
            "recovery_success_count": 0,
            "recovery_success_rate": 0.0,
        }
    else:
        summary_data = _compute_summary(s, tenant_id, date_range, start_date, end_date)

    return {
        "summary": summary_data,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
        },
        "items": items_data,
    }


TELEMETRY_EVENT_ALIASES = {
    "upload": "收据凭证上传与任务创建",
    "receipt_uploaded": "收据凭证上传与任务创建",
    "ocr_parse_started": "AI 结构化解析流水线启动",
    "ocr_parsed": "AI 结构化解析完成并产出字段",
    "parse_done": "AI 结构化解析完成",
    "ocr_error": "AI 解析执行异常或超时",
    "receipt_review_submitted": "人工复核确认并保存提交",
    "save_edited": "人工复核修改保存",
    "edit": "人工复核编辑保存",
    "field_edited": "明细行离散编辑（增删行/SKU映射）",
    "receipt_approved": "收据审核通过并确认入库",
    "approve": "收据审核通过并确认入库",
    "receipt_flagged": "标记单据异常并转人工处理",
    "inventory_in": "库存流水入库并更新现存量",
    "image_replaced": "收据原图替换并触发重验",
    "retake_clicked": "触发单据重拍（图像质量归因）",
    "reparse_clicked": "触发重新解析（模型推理归因）",
    "reupload_after_fail": "异常单据重试上传",
    "feedback_received": "用户满意度体验反馈（赞/踩）",
    "parse_abandoned_for_manual": "解析等待中断并转手工录入",
    "manual_entry_start": "新建手工录入凭证",
    "manual_entry_started": "新建手工录入凭证",
    "rag_hit": "供应商记忆库命中与上下文注入（RAG）",
    "math_guard_checked": "金额与数量计算一致性校验（数学护栏）",
    "contract_guard_checked": "字段完整性与 Schema 契约校验（契约护栏）",
    "price_anomaly_flagged": "采购单价波动超阈值告警",
    # WS5：价格异动处置闭环（owner 对异动作忽略/采纳后由内部埋点通道发出）
    "price_anomaly_adopted": "价格异动采纳并纳入跟进（处置闭环）",
    "price_anomaly_dismissed": "价格异动忽略（处置闭环）",
    "reconciliation_completed": "供应商账期对账完成",
}


def get_telemetry_event_alias(event_type: str) -> str:
    """获取埋点事件的业务规范中文别名。"""
    return TELEMETRY_EVENT_ALIASES.get(event_type, "自定义业务事件")


def calculate_recovery_and_feedback_metrics(
    s: Any,
    tenant_id: Optional[str] = None,
    date_range: str = "all",
    start_date: str = "",
    end_date: str = "",
) -> Dict[str, Any]:
    """统一计算挽回漏斗与体验反馈指标 (SSOT 唯一事实源).
    严格对齐产品规格说明书 SPEC-OBS-TELEMETRY-20260930 标准口径。
    """
    rq = s.query(
        db._ReceiptRow.id,
        db._ReceiptRow.status,
        db._ReceiptRow.math_warnings_json,
        db._ReceiptRow.quality_warnings_json,
        db._ReceiptRow.image_guard_json,
        db._ReceiptRow.audit_logs_json,
    ).filter(db._ReceiptRow.deleted_at.is_(None))
    if tenant_id and tenant_id != "all":
        if tenant_id == "default":
            rq = rq.filter((db._ReceiptRow.tenant_id == "default") | (db._ReceiptRow.tenant_id.is_(None)) | (db._ReceiptRow.tenant_id == ""))
        else:
            rq = rq.filter(db._ReceiptRow.tenant_id == tenant_id)

    now_dt = datetime.now()
    start_ts = ""
    if date_range == "24h":
        start_ts = (now_dt - timedelta(days=1)).isoformat()
        rq = rq.filter(db._ReceiptRow.created_at >= start_ts)
    elif date_range == "7d":
        start_ts = (now_dt - timedelta(days=7)).isoformat()
        rq = rq.filter(db._ReceiptRow.created_at >= start_ts)
    elif date_range == "14d":
        start_ts = (now_dt - timedelta(days=14)).isoformat()
        rq = rq.filter(db._ReceiptRow.created_at >= start_ts)
    elif date_range == "30d":
        start_ts = (now_dt - timedelta(days=30)).isoformat()
        rq = rq.filter(db._ReceiptRow.created_at >= start_ts)

    if start_date:
        rq = rq.filter(db._ReceiptRow.created_at >= start_date)
    if end_date:
        rq = rq.filter(db._ReceiptRow.created_at <= end_date)

    receipt_rows = rq.all()
    total_parses = len(receipt_rows)
    all_rids = [r[0] for r in receipt_rows]

    if total_parses == 0:
        return {
            "total_parses": 0,
            "success_count": 0,
            "warning_count": 0,
            "error_count": 0,
            "success_rate": 0.0,
            "gate_triggered_count": 0,
            "gate_trigger_rate": 0.0,
            "gate_breakdown": {
                "math": 0,
                "contract": 0,
                "quality": 0,
                "security": 0,
            },
            "abnormal_count": 0,
            "modified_count": 0,
            "edit_rate": 0.0,
            "likes_count": 0,
            "dislikes_count": 0,
            "liked_receipts_count": 0,
            "disliked_receipts_count": 0,
            "feedback_receipts_count": 0,
            "positive_rate": 0.0,
            "dislike_rate": 0.0,
            "dislike_frequency": 1.0,
            "retake_clicked": 0,
            "reparse_clicked": 0,
            "recovery_attempts": 0,
            "recovery_entry_rate": 0.0,
            "entry_rate_desc": "挽回点击 0 / 异常单据 0",
            "input_attribution_share": None,
            "model_attribution_share": None,
            "recovery_success_count": 0,
            "recovery_total_receipts": 0,
            "recovery_success_rate": 0.0,
            "_rids": [],
        }

    error_count = sum(1 for r in receipt_rows if r[1] == "error")
    warning_count = sum(1 for r in receipt_rows if r[1] == "parsed_with_warnings")
    success_count = max(0, total_parses - error_count - warning_count)
    success_rate = round(success_count / total_parses, 3) if total_parses > 0 else 0.0

    reject_rids = set()
    if all_rids:
        for i in range(0, len(all_rids), 900):
            chunk = all_rids[i:i + 900]
            d_rows = s.query(db._DecisionLogRow.receipt_id).filter(
                db._DecisionLogRow.receipt_id.in_(chunk),
                db._DecisionLogRow.receipt_id.isnot(None),
                (db._DecisionLogRow.ai_value.like("%gate_reject%"))
                | (db._DecisionLogRow.ai_value.like("%gate_err%"))
                | (db._DecisionLogRow.extra.like("%gate_reject%"))
            ).all()
            for (d_rid,) in d_rows:
                if d_rid is not None:
                    reject_rids.add(d_rid)

    def _has_math_warn(m_json):
        return bool(m_json and m_json != "[]")

    def _has_quality_warn(q_json):
        return bool(q_json and q_json != "[]")

    def _has_sec_warn(s_json):
        if not s_json:
            return False
        return ('"flagged": true' in s_json) or ('"flagged":true' in s_json)

    def _is_warn_cond(r):
        return (
            r[1] == "parsed_with_warnings"
            or _has_math_warn(r[2])
            or _has_quality_warn(r[3])
            or _has_sec_warn(r[4])
        )

    gate_triggered_count = sum(1 for r in receipt_rows if (r[0] in reject_rids) or _is_warn_cond(r))
    gate_trigger_rate = round(gate_triggered_count / total_parses, 3) if total_parses > 0 else 0.0

    math_cnt = sum(1 for r in receipt_rows if _has_math_warn(r[2]))
    quality_cnt = sum(1 for r in receipt_rows if _has_quality_warn(r[3]))
    security_cnt = sum(1 for r in receipt_rows if _has_sec_warn(r[4]))
    contract_cnt = max(0, gate_triggered_count - math_cnt - quality_cnt - security_cnt)
    gate_breakdown = {
        "math": math_cnt,
        "contract": contract_cnt,
        "quality": quality_cnt,
        "security": security_cnt,
    }

    abnormal_count = sum(1 for r in receipt_rows if r[1] == "error" or (r[0] in reject_rids) or _is_warn_cond(r))
    modified_count = sum(1 for r in receipt_rows if r[1] == "edited" or (r[5] and "save_edited" in r[5]))
    edit_rate = round(modified_count / total_parses, 3) if total_parses > 0 else 0.0

    fb_all_rows = []
    if all_rids:
        for i in range(0, len(all_rids), 900):
            chunk = all_rids[i:i + 900]
            fb_all_rows.extend(s.query(db._ReceiptFeedbackRow.receipt_id, db._ReceiptFeedbackRow.like).filter(
                db._ReceiptFeedbackRow.receipt_id.in_(chunk),
                db._ReceiptFeedbackRow.item_index.is_(None),
            ).all())

    likes_count = sum(1 for f in fb_all_rows if f[1] == 1)
    dislikes_count = sum(1 for f in fb_all_rows if f[1] == -1)

    fb_receipts = {f[0] for f in fb_all_rows if f[0] is not None and f[1] in (1, -1)}
    fb_receipts_count = len(fb_receipts)
    liked_receipts = {f[0] for f in fb_all_rows if f[1] == 1 and f[0] is not None}
    disliked_receipts = {f[0] for f in fb_all_rows if f[1] == -1 and f[0] is not None}
    liked_receipts_count = len(liked_receipts)
    disliked_receipts_count = len(disliked_receipts)

    positive_rate = round(liked_receipts_count / fb_receipts_count, 3) if fb_receipts_count > 0 else 0.0
    dislike_rate = round(disliked_receipts_count / fb_receipts_count, 3) if fb_receipts_count > 0 else 0.0
    dislike_frequency = round(dislikes_count / disliked_receipts_count, 2) if disliked_receipts_count > 0 else 1.0

    # 挽回漏斗计算 (基于 user_events 与异常单据量)
    rec_ev_q = s.query(db._UserEventRow).filter(
        db._UserEventRow.event_type.in_(["retake_clicked", "reparse_clicked"])
    )
    if tenant_id and tenant_id != "all":
        if tenant_id == "default":
            rec_ev_q = rec_ev_q.filter((db._UserEventRow.tenant_id == "default") | (db._UserEventRow.tenant_id.is_(None)) | (db._UserEventRow.tenant_id == ""))
        else:
            rec_ev_q = rec_ev_q.filter(db._UserEventRow.tenant_id == tenant_id)
    if start_ts:
        rec_ev_q = rec_ev_q.filter(db._UserEventRow.ts >= start_ts)
    if start_date:
        rec_ev_q = rec_ev_q.filter(db._UserEventRow.ts >= start_date)
    if end_date:
        rec_ev_q = rec_ev_q.filter(db._UserEventRow.ts <= end_date)

    rec_events = rec_ev_q.all()
    retake_clicked = sum(1 for e in rec_events if e.event_type == "retake_clicked")
    reparse_clicked = sum(1 for e in rec_events if e.event_type == "reparse_clicked")
    recovery_attempts = len(rec_events)
    recovery_entry_rate = round(recovery_attempts / abnormal_count, 3) if abnormal_count > 0 else 0.0
    entry_rate_desc = f"挽回点击 {recovery_attempts} / 异常单据 {abnormal_count}"

    input_attribution_share = round(retake_clicked / recovery_attempts, 4) if recovery_attempts > 0 else None
    model_attribution_share = round(reparse_clicked / recovery_attempts, 4) if recovery_attempts > 0 else None

    # 挽回成功率：去重单据级分母，严格要求入库/提交时间戳晚于挽回点击时间戳
    rec_rids = {e.receipt_id for e in rec_events if e.receipt_id is not None}
    succ_rids = set()
    if rec_rids:
        succ_ev_q = s.query(db._UserEventRow).filter(
            db._UserEventRow.receipt_id.in_(rec_rids),
            db._UserEventRow.event_type.in_(["receipt_review_submitted", "receipt_approved"])
        )
        succ_events = succ_ev_q.all()
        succ_points = {}
        for se in succ_events:
            if se.receipt_id is not None:
                succ_points.setdefault(se.receipt_id, []).append((se.ts or "", se.id))

        for rid in rec_rids:
            r_rec_points = [(e.ts or "", e.id) for e in rec_events if e.receipt_id == rid]
            r_succ_points = succ_points.get(rid, [])
            if r_succ_points and any(sp > rp for rp in r_rec_points for sp in r_succ_points):
                succ_rids.add(rid)

    recovery_success_count = len(succ_rids)
    recovery_total_receipts = len(rec_rids)
    recovery_success_rate = round(recovery_success_count / recovery_total_receipts, 3) if recovery_total_receipts > 0 else 0.0

    return {
        "total_parses": total_parses,
        "success_count": success_count,
        "warning_count": warning_count,
        "error_count": error_count,
        "success_rate": success_rate,
        "gate_triggered_count": gate_triggered_count,
        "gate_trigger_rate": gate_trigger_rate,
        "gate_breakdown": gate_breakdown,
        "abnormal_count": abnormal_count,
        "modified_count": modified_count,
        "edit_rate": edit_rate,
        "likes_count": likes_count,
        "dislikes_count": dislikes_count,
        "liked_receipts_count": liked_receipts_count,
        "disliked_receipts_count": disliked_receipts_count,
        "feedback_receipts_count": fb_receipts_count,
        "positive_rate": positive_rate,
        "dislike_rate": dislike_rate,
        "dislike_frequency": dislike_frequency,
        "retake_clicked": retake_clicked,
        "reparse_clicked": reparse_clicked,
        "recovery_attempts": recovery_attempts,
        "recovery_entry_rate": recovery_entry_rate,
        "entry_rate_desc": entry_rate_desc,
        "input_attribution_share": input_attribution_share,
        "model_attribution_share": model_attribution_share,
        "recovery_success_count": recovery_success_count,
        "recovery_total_receipts": recovery_total_receipts,
        "recovery_success_rate": recovery_success_rate,
        "_rids": all_rids,
    }


_SUMMARY_CACHE: Dict[Tuple[Any, ...], Tuple[float, Dict[str, Any]]] = {}
_SUMMARY_CACHE_TTL = 3.0


def clear_summary_cache():
    """清理指标汇总内存缓存。"""
    _SUMMARY_CACHE.clear()


def _compute_summary(
    s: Any,
    tenant_id: Optional[str] = None,
    date_range: str = "all",
    start_date: str = "",
    end_date: str = "",
) -> Dict[str, Any]:
    """计算大盘聚合 KPI 指标。"""
    cache_key = (tenant_id or "all", date_range or "all", start_date or "", end_date or "")
    now = time.time()
    if cache_key in _SUMMARY_CACHE:
        cached_time, cached_val = _SUMMARY_CACHE[cache_key]
        if now - cached_time < _SUMMARY_CACHE_TTL:
            return dict(cached_val)

    metrics = calculate_recovery_and_feedback_metrics(
        s=s,
        tenant_id=tenant_id,
        date_range=date_range,
        start_date=start_date,
        end_date=end_date,
    )

    if metrics["total_parses"] == 0:
        res = {
            "total_parses": 0,
            "success_count": 0,
            "warning_count": 0,
            "error_count": 0,
            "success_rate": 0.0,
            "avg_elapsed_ms": 0.0,
            "avg_tokens": 0,
            "likes_count": 0,
            "dislikes_count": 0,
            "positive_rate": 0.0,
            "modified_count": 0,
            "edit_rate": 0.0,
            "gate_triggered_count": 0,
            "gate_trigger_rate": 0.0,
            "gate_breakdown": {
                "math": 0,
                "contract": 0,
                "quality": 0,
                "security": 0,
            },
            "disliked_receipts_count": 0,
            "feedback_receipts_count": 0,
            "dislike_rate": 0.0,
            "dislike_frequency": 1.0,
            "recovery_attempts": 0,
            "recovery_entry_rate": 0.0,
            "recovery_success_count": 0,
            "recovery_success_rate": 0.0,
        }
        _SUMMARY_CACHE[cache_key] = (now, res)
        return dict(res)

    target_rids = metrics.get("_rids") or []
    elapsed_list = []
    tokens_list = []
    if target_rids:
        rids_sample = target_rids[:500]
        d_rows = s.query(db._DecisionLogRow.extra).filter(
            db._DecisionLogRow.receipt_id.in_(rids_sample),
            db._DecisionLogRow.extra != "",
            db._DecisionLogRow.extra.isnot(None),
        ).limit(500).all()

        for (extra_str,) in d_rows:
            extra_json = _safe_json_loads(extra_str, default={})
            if not isinstance(extra_json, dict):
                continue
            # tokens
            t_total = extra_json.get("tokens_total")
            if isinstance(t_total, (int, float)) and t_total > 0:
                tokens_list.append(t_total)
            # elapsed
            el = extra_json.get("elapsed_ms")
            if isinstance(el, dict):
                t_ms = el.get("total") or el.get("extract")
                if isinstance(t_ms, (int, float)) and t_ms > 0:
                    elapsed_list.append(t_ms)
            elif isinstance(el, (int, float)) and el > 0:
                elapsed_list.append(el)

    avg_elapsed_ms = round(sum(elapsed_list) / len(elapsed_list), 1) if elapsed_list else 0.0
    avg_tokens = round(sum(tokens_list) / len(tokens_list)) if tokens_list else 0

    res = {
        "total_parses": metrics["total_parses"],
        "success_count": metrics["success_count"],
        "warning_count": metrics["warning_count"],
        "error_count": metrics["error_count"],
        "success_rate": metrics["success_rate"],
        "avg_elapsed_ms": avg_elapsed_ms,
        "avg_tokens": avg_tokens,
        "likes_count": metrics["likes_count"],
        "dislikes_count": metrics["dislikes_count"],
        "positive_rate": metrics["positive_rate"],
        "modified_count": metrics["modified_count"],
        "edit_rate": metrics["edit_rate"],
        "gate_triggered_count": metrics["gate_triggered_count"],
        "gate_trigger_rate": metrics["gate_trigger_rate"],
        "gate_breakdown": metrics["gate_breakdown"],
        "disliked_receipts_count": metrics["disliked_receipts_count"],
        "feedback_receipts_count": metrics["feedback_receipts_count"],
        "dislike_rate": metrics["dislike_rate"],
        "dislike_frequency": metrics["dislike_frequency"],
        "recovery_attempts": metrics["recovery_attempts"],
        "recovery_entry_rate": metrics["recovery_entry_rate"],
        "recovery_success_count": metrics["recovery_success_count"],
        "recovery_success_rate": metrics["recovery_success_rate"],
    }
    _SUMMARY_CACHE[cache_key] = (now, res)
    return dict(res)


def get_parsing_log_detail(
    s: Any,
    record_id: Optional[int] = None,
    job_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """获取指定单据的全息生命周期详情及所有关联的时序打点事件。"""
    target_rid: Optional[int] = record_id
    if target_rid is None and job_id:
        matched_ev = s.query(db._UserEventRow.receipt_id).filter(
            db._UserEventRow.properties.like(f"%{job_id}%"),
            db._UserEventRow.receipt_id.isnot(None),
        ).first()
        if matched_ev and matched_ev[0]:
            target_rid = int(matched_ev[0])

    if target_rid is None:
        return None

    # 查询该单据基础数据与 diff
    res = query_parsing_logs(
        s=s,
        tenant_id="all",
        search=str(target_rid),
        page=1,
        page_size=10,
    )
    items = res.get("items") or []
    target_item = None
    for it in items:
        if it.get("record_id") == target_rid:
            target_item = it
            break

    if not target_item:
        row = s.query(db._ReceiptRow).filter(db._ReceiptRow.id == target_rid).first()
        if not row:
            return None
        res2 = query_parsing_logs(s=s, tenant_id="all", page=1, page_size=100)
        for it in res2.get("items") or []:
            if it.get("record_id") == target_rid:
                target_item = it
                break

    if not target_item:
        return None

    # 提取关联的所有时序打点事件
    r_events = s.query(db._UserEventRow).filter(
        (db._UserEventRow.receipt_id == target_rid)
        | (db._UserEventRow.properties.like(f"%{target_rid}%"))
    ).order_by(db._UserEventRow.ts.asc(), db._UserEventRow.id.asc()).all()

    audit_events: List[Dict[str, Any]] = []
    for ev in r_events:
        props = _safe_json_loads(getattr(ev, "properties", None), default={}) or {}
        audit_events.append({
            "id": getattr(ev, "id", 0),
            "event_type": getattr(ev, "event_type", ""),
            "ts": getattr(ev, "ts", "") or "",
            "account_id": getattr(ev, "account_id", "") or "system",
            "receipt_id": getattr(ev, "receipt_id", None),
            "properties": props,
        })

    target_item["audit_events"] = audit_events

    # 提取关联的全部 LLM/VLM 调用日志 (全链路全腿与各轮次 I/O 审计)
    raw_calls = db.list_llm_call_logs_by_receipt(target_rid, s=s)
    llm_calls: List[Dict[str, Any]] = []

    def _leg_label(leg_name: str) -> str:
        mapping = {
            "extract": "识别腿 (VLM)",
            "parse": "解析腿 (LLM)",
            "audit": "审核腿 (Cross-Audit)",
            "precheck": "预判腿 (Precheck)",
        }
        return mapping.get(str(leg_name or "").lower(), f"{leg_name} 腿")

    tot_cached_from_calls = 0
    for rc in (raw_calls or []):
        p_tokens = int(rc.get("prompt_tokens", 0) or 0)
        c_tokens = int(rc.get("completion_tokens", 0) or 0)
        k_tokens = int(rc.get("cached_tokens", 0) or 0)
        t_tokens = int(rc.get("total_tokens", 0) or (p_tokens + c_tokens))
        tot_cached_from_calls += k_tokens
        cache_hit_rate = round(k_tokens / p_tokens, 4) if p_tokens > 0 else 0.0

        p_msgs = _safe_json_loads(rc.get("prompt_messages_json"), default=[])
        if not isinstance(p_msgs, list):
            p_msgs = [p_msgs] if p_msgs else []

        llm_calls.append({
            "id": rc.get("id"),
            "leg": rc.get("leg"),
            "leg_label": _leg_label(rc.get("leg")),
            "attempt": rc.get("attempt"),
            "engine": rc.get("engine"),
            "model": rc.get("model"),
            "status": rc.get("status"),
            "elapsed_ms": rc.get("elapsed_ms"),
            "token_usage": {
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "cached_tokens": k_tokens,
                "total_tokens": t_tokens,
                "cache_hit_rate": cache_hit_rate,
            },
            "cost_hkd": rc.get("cost_hkd"),
            "cost_estimated": rc.get("cost_estimated"),
            "cost_estimated_reason": rc.get("cost_estimated_reason"),
            "prompt_messages": p_msgs,
            "raw_output": rc.get("raw_output") or "",
            "error_msg": rc.get("error_msg") or "",
            "error_category": rc.get("error_category") or "",
            "created_at": rc.get("created_at") or "",
        })

    target_item["llm_calls"] = llm_calls

    # 更新或增强 target_item 的 token_usage 中的 cached_tokens 和 cache_hit_rate
    tu = target_item.get("token_usage") or {}
    p_tok = int(tu.get("tokens_prompt", 0) or 0)
    cached_tok = int(tu.get("tokens_cached", 0) or tot_cached_from_calls)
    tu["tokens_cached"] = cached_tok
    tu["cache_hit_rate"] = round(cached_tok / p_tok, 4) if p_tok > 0 else 0.0
    target_item["token_usage"] = tu

    return target_item


# -------------------------------------------------------------
# 8 大核心 KPI 时序切片与多维趋势下钻计算逻辑 (PRD-TELEMETRY-KPI-TREND-001)
# -------------------------------------------------------------
KPI_METRIC_META = {
    "total_parses": {
        "name": "解析总单量",
        "unit": "单",
        "section": "Quality & Latency",
        "section_name": "模型吞吐与系统质量",
        "is_rate": False,
        "is_integer": True,
    },
    "success_rate": {
        "name": "解析成功率",
        "unit": "%",
        "section": "Quality & Latency",
        "section_name": "模型吞吐与系统质量",
        "is_rate": True,
        "is_integer": False,
    },
    "avg_elapsed_ms": {
        "name": "平均解析耗时",
        "unit": "ms",
        "section": "Quality & Latency",
        "section_name": "模型吞吐与系统质量",
        "is_rate": False,
        "is_integer": False,
        # 越低越好：下降是积极信号，趋势展示应标绿
        "lower_is_better": True,
    },
    "avg_tokens": {
        "name": "平均 Token 消耗",
        "unit": "Tokens",
        "section": "Quality & Latency",
        "section_name": "模型吞吐与系统质量",
        "is_rate": False,
        "is_integer": True,
        "lower_is_better": True,
    },
    "positive_rate": {
        "name": "综合好评率",
        "unit": "%",
        "section": "UX & Recovery",
        "section_name": "用户体验感知与挽回漏斗",
        "is_rate": True,
        "is_integer": False,
    },
    "edit_rate": {
        "name": "人工修正率",
        "unit": "%",
        "section": "UX & Recovery",
        "section_name": "用户体验感知与挽回漏斗",
        "is_rate": True,
        "is_integer": False,
        # 人工改动越多说明 AI 预填越差：越低越好
        "lower_is_better": True,
    },
    "recovery_entry_rate": {
        "name": "异常挽回触发率",
        "unit": "%",
        "section": "UX & Recovery",
        "section_name": "用户体验感知与挽回漏斗",
        "is_rate": True,
        "is_integer": False,
        # 触发挽回说明解析出了问题：越低越好
        "lower_is_better": True,
    },
    "recovery_success_rate": {
        "name": "挽回成功率",
        "unit": "%",
        "section": "UX & Recovery",
        "section_name": "用户体验感知与挽回漏斗",
        "is_rate": True,
        "is_integer": False,
    },
}


def compute_kpi_trends(
    s: Any,
    metric_key: str,
    days: int = 7,
    effective_tenant: Optional[str] = "all",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """计算 8 大核心 KPI 的每日时间切片时序数据、统计摘要及异常波动标记。
    严格对齐 PRD-OBS-KPI-DRILLDOWN-V2-20260930 标准口径。
    支持 N+1 天基线数据预取，实现全周期环比连续回溯，支持自定义起止日期。
    """
    if metric_key not in KPI_METRIC_META:
        raise ValueError(f"不支持的指标类型: {metric_key}")

    meta = KPI_METRIC_META[metric_key]
    is_int = meta.get("is_integer", False)

    # 1. 确定日期序列与 N+1 基线对比日
    if start_date and end_date:
        s_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        e_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        span = (e_dt - s_dt).days + 1
        dates = [(s_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(span)]
        baseline_d = (s_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        period_days = span
    else:
        today = datetime.now().date()
        days = max(1, min(90, int(days or 7)))
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
        baseline_d = (datetime.strptime(dates[0], "%Y-%m-%d").date() - timedelta(days=1)).strftime("%Y-%m-%d")
        period_days = days

    all_query_dates = [baseline_d] + dates
    start_ts = f"{baseline_d}T00:00:00"
    end_ts = f"{dates[-1]}T23:59:59.999999"

    # 2. 数据库检索与预加载
    rq = s.query(
        db._ReceiptRow.id,
        db._ReceiptRow.created_at,
        db._ReceiptRow.status,
        db._ReceiptRow.math_warnings_json,
        db._ReceiptRow.quality_warnings_json,
        db._ReceiptRow.image_guard_json,
        db._ReceiptRow.audit_logs_json,
        db._ReceiptRow.tenant_id,
    ).filter(
        db._ReceiptRow.deleted_at.is_(None),
        db._ReceiptRow.created_at >= start_ts,
        db._ReceiptRow.created_at <= end_ts,
    )

    if effective_tenant and effective_tenant != "all":
        if effective_tenant == "default":
            rq = rq.filter(
                (db._ReceiptRow.tenant_id == "default")
                | (db._ReceiptRow.tenant_id.is_(None))
                | (db._ReceiptRow.tenant_id == "")
            )
        else:
            rq = rq.filter(db._ReceiptRow.tenant_id == effective_tenant)

    receipt_rows = rq.all()
    rids = [r.id for r in receipt_rows]

    # 按自然日聚合单据
    receipts_by_date: Dict[str, List[Any]] = {d: [] for d in all_query_dates}
    for r in receipt_rows:
        d = str(r.created_at or "")[:10]
        if d in receipts_by_date:
            receipts_by_date[d].append(r)

    # 预加载门禁拦截单据集合
    gate_reject_rids = set()
    if rids:
        reject_rows = s.query(db._DecisionLogRow.receipt_id).filter(
            db._DecisionLogRow.receipt_id.in_(rids),
            db._DecisionLogRow.receipt_id.isnot(None),
            (db._DecisionLogRow.ai_value.like("%gate_reject%"))
            | (db._DecisionLogRow.ai_value.like("%gate_err%"))
            | (db._DecisionLogRow.extra.like("%gate_reject%"))
        ).all()
        gate_reject_rids = {r[0] for r in reject_rows if r[0] is not None}

    # 预加载耗时与 Token 消耗数据
    receipt_extras: Dict[int, List[Dict[str, Any]]] = {}
    if metric_key in ("avg_elapsed_ms", "avg_tokens") and rids:
        d_rows = s.query(db._DecisionLogRow.receipt_id, db._DecisionLogRow.extra).filter(
            db._DecisionLogRow.receipt_id.in_(rids),
            db._DecisionLogRow.extra != "",
            db._DecisionLogRow.extra.isnot(None),
        ).all()
        for rid, extra_str in d_rows:
            if not extra_str:
                continue
            ex = _safe_json_loads(extra_str, default={})
            if isinstance(ex, dict):
                receipt_extras.setdefault(rid, []).append(ex)

    # 预加载好评与点赞反馈数据
    receipt_feedbacks: Dict[int, List[int]] = {}
    if metric_key == "positive_rate" and rids:
        f_rows = s.query(db._ReceiptFeedbackRow.receipt_id, db._ReceiptFeedbackRow.like).filter(
            db._ReceiptFeedbackRow.receipt_id.in_(rids),
            db._ReceiptFeedbackRow.item_index.is_(None),
        ).all()
        for rid, lk in f_rows:
            if rid is not None:
                receipt_feedbacks.setdefault(rid, []).append(lk)

    # 预加载异常挽回埋点事件数据
    rec_events_by_date: Dict[str, List[Any]] = {d: [] for d in all_query_dates}
    succ_events_by_rid: Dict[int, List[Tuple[str, int]]] = {}
    if metric_key in ("recovery_entry_rate", "recovery_success_rate"):
        ev_q = s.query(
            db._UserEventRow.id,
            db._UserEventRow.ts,
            db._UserEventRow.event_type,
            db._UserEventRow.receipt_id,
            db._UserEventRow.tenant_id,
        ).filter(
            db._UserEventRow.event_type.in_(["retake_clicked", "reparse_clicked", "receipt_review_submitted", "receipt_approved"]),
            db._UserEventRow.ts >= start_ts,
            db._UserEventRow.ts <= end_ts,
        )
        if effective_tenant and effective_tenant != "all":
            if effective_tenant == "default":
                ev_q = ev_q.filter(
                    (db._UserEventRow.tenant_id == "default")
                    | (db._UserEventRow.tenant_id.is_(None))
                    | (db._UserEventRow.tenant_id == "")
                )
            else:
                ev_q = ev_q.filter(db._UserEventRow.tenant_id == effective_tenant)
        all_evs = ev_q.all()
        for ev in all_evs:
            d = str(ev.ts or "")[:10]
            if ev.event_type in ("retake_clicked", "reparse_clicked"):
                if d in rec_events_by_date:
                    rec_events_by_date[d].append(ev)
            elif ev.event_type in ("receipt_review_submitted", "receipt_approved"):
                if ev.receipt_id is not None:
                    succ_events_by_rid.setdefault(ev.receipt_id, []).append((ev.ts or "", ev.id))

    def _eval_day_metric(d_str: str, day_receipts: List[Any]) -> Tuple[float, int, int]:
        total_parses = len(day_receipts)
        val = 0.0
        num = 0
        den = 0

        if metric_key == "total_parses":
            num = total_parses
            den = total_parses
            val = float(total_parses)

        elif metric_key == "success_rate":
            den = total_parses
            succ = 0
            for r in day_receipts:
                is_err = (r.status == "error")
                is_rej = (r.id in gate_reject_rids)
                is_warn = (
                    (r.status == "parsed_with_warnings")
                    or bool(r.math_warnings_json and r.math_warnings_json not in ("", "[]"))
                    or bool(r.quality_warnings_json and r.quality_warnings_json not in ("", "[]"))
                    or ("\"flagged\": true" in str(r.image_guard_json or ""))
                    or ("\"flagged\":true" in str(r.image_guard_json or ""))
                )
                if not is_err and not is_rej and not is_warn:
                    succ += 1
            num = succ
            val = round(succ / den * 100.0, 1) if den > 0 else 0.0

        elif metric_key == "avg_elapsed_ms":
            elapsed_list = []
            for r in day_receipts:
                extras = receipt_extras.get(r.id, [])
                for ex in extras:
                    el = ex.get("elapsed_ms")
                    if isinstance(el, dict):
                        t_ms = el.get("total") or el.get("extract")
                        if isinstance(t_ms, (int, float)) and t_ms > 0:
                            elapsed_list.append(t_ms)
                    elif isinstance(el, (int, float)) and el > 0:
                        elapsed_list.append(el)
            den = len(elapsed_list)
            num = round(sum(elapsed_list), 1) if den > 0 else 0.0
            val = round(num / den, 1) if den > 0 else 0.0

        elif metric_key == "avg_tokens":
            tokens_list = []
            for r in day_receipts:
                extras = receipt_extras.get(r.id, [])
                for ex in extras:
                    t_total = ex.get("tokens_total")
                    if isinstance(t_total, (int, float)) and t_total > 0:
                        tokens_list.append(t_total)
            den = len(tokens_list)
            num = int(sum(tokens_list)) if den > 0 else 0
            val = round(num / den) if den > 0 else 0

        elif metric_key == "positive_rate":
            liked_rids = set()
            rated_rids = set()
            for r in day_receipts:
                fbs = receipt_feedbacks.get(r.id, [])
                for lk in fbs:
                    if lk in (1, -1):
                        rated_rids.add(r.id)
                    if lk == 1:
                        liked_rids.add(r.id)
            num = len(liked_rids)
            den = len(rated_rids)
            val = round(num / den * 100.0, 1) if den > 0 else 0.0

        elif metric_key == "edit_rate":
            den = total_parses
            edited_count = sum(
                1 for r in day_receipts
                if (r.status == "edited") or ("save_edited" in str(r.audit_logs_json or ""))
            )
            num = edited_count
            val = round(num / den * 100.0, 1) if den > 0 else 0.0

        elif metric_key == "recovery_entry_rate":
            abnormal_count = 0
            for r in day_receipts:
                is_err = (r.status == "error")
                is_rej = (r.id in gate_reject_rids)
                is_warn = (
                    (r.status == "parsed_with_warnings")
                    or bool(r.math_warnings_json and r.math_warnings_json not in ("", "[]"))
                    or bool(r.quality_warnings_json and r.quality_warnings_json not in ("", "[]"))
                    or ("\"flagged\": true" in str(r.image_guard_json or ""))
                    or ("\"flagged\":true" in str(r.image_guard_json or ""))
                )
                if is_err or is_rej or is_warn:
                    abnormal_count += 1
            den = abnormal_count
            rec_evs = rec_events_by_date.get(d_str, [])
            num = len(rec_evs)
            val = round(num / den * 100.0, 1) if den > 0 else 0.0

        elif metric_key == "recovery_success_rate":
            rec_evs = rec_events_by_date.get(d_str, [])
            rec_rids = {e.receipt_id for e in rec_evs if e.receipt_id is not None}
            den = len(rec_rids)
            succ_rids = set()
            for rid in rec_rids:
                r_rec_points = [(e.ts or "", e.id) for e in rec_evs if e.receipt_id == rid]
                r_succ_points = succ_events_by_rid.get(rid, [])
                if r_succ_points and any(sp > rp for rp in r_rec_points for sp in r_succ_points):
                    succ_rids.add(rid)
            num = len(succ_rids)
            val = round(num / den * 100.0, 1) if den > 0 else 0.0

        return (val, num, den)

    # 3. 计算基线日指标
    base_val, base_num, base_den = _eval_day_metric(baseline_d, receipts_by_date[baseline_d])
    base_item = {
        "date": baseline_d,
        "value": base_val,
        "numerator": base_num,
        "denominator": base_den,
    }

    # 4. 计算展示周期内各项切片及 PoP 环比
    series = []
    daily_records = []

    for idx, d in enumerate(dates):
        val, num, den = _eval_day_metric(d, receipts_by_date[d])

        # 确定对比基准：首日使用 N+1 基线日，后续使用前一天
        prev_item = base_item if idx == 0 else series[idx - 1]

        prev_diff = None
        if meta["is_rate"]:
            if prev_item["denominator"] > 0 and den > 0:
                prev_diff = round(val - prev_item["value"], 1)
        else:
            if prev_item["value"] > 0:
                prev_diff = round((val - prev_item["value"]) / prev_item["value"] * 100.0, 1)
            elif prev_item["value"] == 0:
                if val == 0:
                    prev_diff = 0.0
                else:
                    prev_diff = 100.0

        # 异常波动判定阈值
        is_anomaly = False
        if metric_key == "total_parses":
            if prev_item["value"] >= 3 and prev_diff is not None and (prev_diff > 100.0 or prev_diff < -50.0):
                is_anomaly = True
        elif metric_key == "success_rate":
            if prev_diff is not None and prev_diff < -10.0:
                is_anomaly = True
        elif metric_key == "avg_elapsed_ms":
            if (prev_diff is not None and prev_diff > 50.0) or val > 5000.0:
                is_anomaly = True
        elif metric_key == "avg_tokens":
            if prev_diff is not None and prev_diff > 40.0:
                is_anomaly = True
        elif metric_key == "positive_rate":
            if prev_diff is not None and prev_diff < -15.0:
                is_anomaly = True
        elif metric_key == "edit_rate":
            if prev_diff is not None and prev_diff > 15.0:
                is_anomaly = True
        elif metric_key == "recovery_entry_rate":
            if prev_diff is not None and prev_diff < -20.0:
                is_anomaly = True
        elif metric_key == "recovery_success_rate":
            if prev_diff is not None and prev_diff < -20.0:
                is_anomaly = True

        # 元素收敛：计数型指标（解析总单量）的样本量即数值本身，0 单是合法
        # 业务状态而非缺数，健康档只保留 正常/异动预警；比率与均值型指标的
        # 分母衡量样本充分性，保留四档判定。
        if metric_key == "total_parses":
            health_status = "[异动预警]" if is_anomaly else "[正常]"
        else:
            health_status = "[正常]"
            if den == 0:
                health_status = "[无数据]"
            elif den < 3:
                health_status = "[样本不足]"
            elif is_anomaly:
                health_status = "[异动预警]"

        # 智能数值格式化：整数指标纯整数，不带单位“单”
        if is_int:
            val_out = int(round(val))
            formatted_val = str(val_out)
        elif meta["is_rate"]:
            val_out = val
            formatted_val = f"{val:.1f}%"
        else:
            val_out = val
            formatted_val = f"{int(round(val))}" if float(val).is_integer() else f"{val:.1f}"

        # 元素收敛：比率型展示 分子/分母（口径核心）；均值型只展示样本量
        # n（分子为总和，可由均值×样本量推出，无独立信息）；计数型数值即
        # 样本量，置空由前端显示占位符，避免与「当日数值」列重复。
        if meta["is_rate"]:
            formatted_ratio = f"{num}/{den}"
        elif metric_key in ("avg_elapsed_ms", "avg_tokens"):
            formatted_ratio = f"n={den}"
        else:
            formatted_ratio = ""

        series.append({
            "date": d,
            "value": val_out,
            "numerator": num,
            "denominator": den,
            "prev_diff_pct": prev_diff,
            "is_anomaly": is_anomaly,
        })

        daily_records.append({
            "date": d,
            "value": val_out,
            "formatted_value": formatted_val,
            "numerator": num,
            "denominator": den,
            "formatted_ratio": formatted_ratio,
            "prev_diff_pct": prev_diff,
            "is_anomaly": is_anomaly,
            "health_status": health_status,
        })

    # Summary 聚合（计数型指标 0 单日同样是有效观测，均值/极值应包含）
    if metric_key == "total_parses":
        valid_points = list(series)
    else:
        valid_points = [s_item for s_item in series if s_item["denominator"] > 0]
    current_val = series[-1]["value"] if series else 0
    if valid_points:
        min_val = min(p["value"] for p in valid_points)
        max_val = max(p["value"] for p in valid_points)
        if meta["is_rate"]:
            tot_num = sum(p["numerator"] for p in valid_points)
            tot_den = sum(p["denominator"] for p in valid_points)
            avg_val = round(tot_num / tot_den * 100.0, 1) if tot_den > 0 else 0.0
        else:
            avg_val = round(sum(p["value"] for p in valid_points) / len(valid_points), 1)

        first_val = valid_points[0]["value"]
        if meta["is_rate"]:
            change_rate_pct = round(current_val - first_val, 1)
        else:
            change_rate_pct = round((current_val - first_val) / first_val * 100.0, 1) if first_val > 0 else (0.0 if current_val == 0 else 100.0)
    else:
        min_val = 0
        max_val = 0
        avg_val = 0
        change_rate_pct = 0.0

    if is_int:
        current_val = int(round(current_val))
        min_val = int(round(min_val))
        max_val = int(round(max_val))
        avg_val = int(round(avg_val))

    trend_direction = "flat"
    if change_rate_pct > 0.5:
        trend_direction = "up"
    elif change_rate_pct < -0.5:
        trend_direction = "down"

    summary = {
        "current_val": current_val,
        "avg_val": avg_val,
        "min_val": min_val,
        "max_val": max_val,
        "change_rate_pct": change_rate_pct,
        "trend_direction": trend_direction,
    }

    return {
        "metric_key": metric_key,
        "metric_name": meta["name"],
        "metric_unit": meta["unit"],
        "section_name": meta["section_name"],
        "is_rate": meta["is_rate"],
        "is_integer": is_int,
        "lower_is_better": meta.get("lower_is_better", False),
        "period_days": period_days,
        "date_range": {
            "start_date": dates[0],
            "end_date": dates[-1],
        },
        "tenant_id": effective_tenant or "all",
        "summary": summary,
        "series": series,
        "daily_records": list(reversed(daily_records)),
    }

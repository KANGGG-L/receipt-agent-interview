# -*- coding: utf-8 -*-
"""收据数据桥接：AI 识别管线结果 ↔ 前端契约数据模型。

- run_ai_pipeline(image_path, vendor_hint): 调用 supervisor，返回前端 upload 响应 data
- build_detail(receipt_row): 收据行 → /api/receipt/{id} 的 ReceiptDetail
- build_row(receipt_row): 收据行 → /api/receipts 列表行
"""

import json
import re
import threading

from app import db
from app.models import EngineConfig
from ai_registry.tools.smart_splitter.v1_2_0_multi_pack import SmartSplitterTool as _S

# B-P0-1 专用：流水号后缀正则 (FR-7 品名归一) —— 有机菜心_1787140420→有机菜心, 本地新鲜菜心_1787140411→本地新鲜菜心
_SERIAL_SUFFIX_RE = re.compile(r"_\d{10}$")

# 模块顶层单例：避免 save_parsed_data/canonical_sku_name 每次实例化
try:
    _sanitizer_singleton = _S()
except Exception:
    _sanitizer_singleton = None

# 在内存 job 表（单进程部署约束，对齐完整版）
JOBS = {}
JOBS_LOCK = threading.Lock()


# -------------------------------------------------------------
# AI 管线：异步 Job
# -------------------------------------------------------------
def start_recognition_job(image_path, vendor_hint="", receipt_id=None):
    """启动后台识别 Job。返回 job_id。

    线程执行识别管线；完成后写回收据行并更新 JOBS。
    """
    job_id = db.new_id()
    if receipt_id is None:
        receipt_id = db.create_receipt(status="uploaded")
    db.update_receipt(receipt_id, status="parsing", image_path=image_path)

    with JOBS_LOCK:
        JOBS[job_id] = {"job_id": job_id, "job_status": "queued", "receipt_id": receipt_id}

    def _run():
        with JOBS_LOCK:
            JOBS[job_id]["job_status"] = "running"

        def _on_event(ev):
            # 首选引擎降级回退时实时写入 Job，供前端轮询第一时间弹提示
            try:
                if isinstance(ev, dict) and ev.get("type") == "engine_fallback":
                    with JOBS_LOCK:
                        JOBS[job_id]["progress_fallback"] = {
                            "reason": str(ev.get("reason") or "")[:300],
                            "from": str(ev.get("from") or "")[:60],
                            "to": str(ev.get("to") or "")[:60],
                        }
            except Exception:
                pass

        try:
            cfg = db.get_engine_config()
            from app.chains import supervisor
            result = supervisor.run_pipeline(
                image_path, vendor_hint=vendor_hint, config=cfg,
                supplier_name=vendor_hint or "",
                receipt_id=receipt_id,  # U-2: 透传 receipt_id，AI 决策履历落库关联单据
                on_event=_on_event,
            )
            data = result.get("data")
            if data is None:
                db.update_receipt(receipt_id, status="error")
                with JOBS_LOCK:
                    JOBS[job_id].update({
                        "job_status": "error",
                        "error_msg": result.get("contract_error") or result.get("last_error") or "识别失败",
                        "fallback_triggered": result.get("fallback_triggered", False),
                        "fallback_failed": result.get("fallback_failed", False),
                        "fallback_from": result.get("fallback_from", ""),
                        "fallback_engine": result.get("fallback_engine", ""),
                    })
                return

            detail = save_parsed_data(receipt_id, data, result)
            with JOBS_LOCK:
                JOBS[job_id].update({
                    "job_status": "done",
                    "result": {
                        "status": "success",
                        "receipt_id": receipt_id,
                        "data": detail,
                        "image_url": "/uploads/" + (db.get_receipt_row(receipt_id).image_path.split("/")[-1] if db.get_receipt_row(receipt_id) else ""),
                        "version": db.get_receipt_row(receipt_id).version,
                        "quality_warnings": json.loads(db.get_receipt_row(receipt_id).quality_warnings_json or "[]"),
                        "fallback_triggered": result.get("fallback_triggered", False),
                        "fallback_reason": result.get("fallback_reason", ""),
                        "fallback_from": result.get("fallback_from", ""),
                        "fallback_engine": result.get("fallback_engine", ""),
                    },
                })
        except Exception as e:
            db.update_receipt(receipt_id, status="error")
            with JOBS_LOCK:
                JOBS[job_id].update({"job_status": "error", "error_msg": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return job_id, receipt_id


def sweep_parsing_timeout(timeout_seconds=300):
    """Gate-2：扫描超时的 parsing 单据，强制置为 error(parsing_timeout)。

    可由定时任务或请求入口调用，幂等、无副作用于已完成单据。
    返回被置为 error 的 receipt_id 列表。
    """
    import datetime as _dt
    cutoff = (_dt.datetime.now() - _dt.timedelta(seconds=timeout_seconds)).isoformat(timespec="seconds")
    s = db.get_session()
    try:
        rows = s.query(db._ReceiptRow).filter(
            db._ReceiptRow.status == "parsing",
            db._ReceiptRow.created_at < cutoff,
            db._ReceiptRow.deleted_at.is_(None),
        ).all()
        ids = []
        for r in rows:
            r.status = "error"
            # reason 写入 audit_logs_json 供前端标红“识别超时”
            logs = __import__("json").loads(r.audit_logs_json or "[]")
            logs.append({"who": "system", "action": "timeout", "field": "status", "old": "parsing", "new": "error", "reason": "parsing_timeout", "ts": db.now_iso()})
            r.audit_logs_json = __import__("json").dumps(logs, ensure_ascii=False)
            r.updated_at = db.now_iso()
            ids.append(r.id)
        if ids:
            s.commit()
        return ids
    finally:
        s.close()


def get_job(job_id):
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id, {}))
    if not job:
        return None
    if job.get("job_status") == "done":
        row = db.get_receipt_row(job["receipt_id"])
        if row:
            job["image_url"] = "/uploads/" + (row.image_path.split("/")[-1] if row.image_path else "")
            if not job.get("result"):
                job["result"] = {"status": "success", "receipt_id": job["receipt_id"],
                                 "data": build_detail(row), "version": row.version}
    return job


# -------------------------------------------------------------
# AI 结果 → 前端数据模型
# -------------------------------------------------------------
def strip_serial_suffix(name: str) -> str:
    """B-P0-1 核心词归一：显式正则剥离 _\\d{10} 后缀，返回纯净品名。"""
    t = str(name or "").strip()
    return _SERIAL_SUFFIX_RE.sub("", t).strip()


def canonical_sku_name(name: str) -> str:
    """FR-7/B-P0-1 统一归一入口：优先走 SmartSplitter v1_2_0 单例，fallback 正则剥离。"""
    if _sanitizer_singleton is not None:
        try:
            return _sanitizer_singleton.sanitize_name(name)
        except Exception:
            pass
    return strip_serial_suffix(name)


def save_parsed_data(receipt_id, data, result):
    """把 AI 结构化结果写入收据行 + 明细 + SKU 匹配。返回 detail。"""
    from app.services.contract import sanitize_nan

    # 使用模块顶层单例，避免每次实例化
    _sanitizer = _sanitizer_singleton

    items_raw = []
    for it in data.items:
        raw = it.name or ""
        if _sanitizer is not None:
            try:
                clean_name = _sanitizer.sanitize_name(raw)
            except Exception:
                clean_name = strip_serial_suffix(raw)
        else:
            clean_name = strip_serial_suffix(raw)
        items_raw.append({
            "name": clean_name, "raw_name": it.name,
            "quantity": sanitize_nan(it.qty), "unit": it.unit or "", "raw_unit": it.unit or "",
            "unit_price": sanitize_nan(it.unit_price), "amount": sanitize_nan(it.amount),
            "sku_id": None, "cost_center_id": None, "confidence": it.confidence if hasattr(it, 'confidence') else 0.5,
            "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
            "price_diff_percent": 0.0, "unit_conversion_warning": "",
            "fuzzy_candidates": [], "entity_candidates": [],
            "is_void": int(bool(getattr(it, "is_void", False))),
            "actual_qty": getattr(it, "actual_qty", None),
        })
        _match_sku(items_raw[-1])

    use_grey = int(bool(result.get("use_grey"))) if result.get("use_grey") is not None else 0
    # Gate-3：交叉审核分歧 → review_priority_score（Top10%标重点复核）
    audit_res = result.get("audit_result") or {}
    discrepancies = audit_res.get("discrepancies") if isinstance(audit_res, dict) else None
    if isinstance(discrepancies, list) and discrepancies:
        review_priority_score = min(1.0, 0.5 + 0.15 * len(discrepancies))
    elif audit_res.get("reason") == "AI 识别与原图一致":
        review_priority_score = 0.0
    else:
        review_priority_score = 0.1 if discrepancies == [] else 0.0

    # 红章补充：LLM 未标记但图片含红章 → 补充为 stamp（单一来源：contract.payment_mark_from_image）
    try:
        from app.services.contract import payment_mark_from_image
        row_tmp = db.get_receipt_row(receipt_id)
        img_path = row_tmp.image_path if row_tmp else ""
        payment_mark_val = payment_mark_from_image(img_path, llm_marked=data.payment_marked)
    except Exception:
        payment_mark_val = "已付款" if data.payment_marked else ""

    # RAG 上下文持久化（供 data_only 调试开关按需展示，默认不暴露）
    rag_ctx = result.get("vendor_context") or result.get("rag_context") or ""
    # 归一为结构化文本：保留供应商先验与检索片段
    if isinstance(rag_ctx, dict):
        rag_ctx_str = json.dumps(rag_ctx, ensure_ascii=False)
    else:
        rag_ctx_str = str(rag_ctx or "")
    db.update_receipt(
        receipt_id,
        status="parsed",
        supplier_name=data.vendor,
        receipt_date=data.date,
        sheet_name=(data.date or "")[:7],
        total_amount=sanitize_nan(data.total),
        doc_form=data.doc_form.value if hasattr(data.doc_form, "value") else str(data.doc_form),
        payment_mark=payment_mark_val,
        confidence=sanitize_nan(data.confidence),
        raw_llm=result.get("raw", ""),
        audit_json=json.dumps(audit_res, ensure_ascii=False),
        ai_prefill_json=json.dumps(data.model_dump(), ensure_ascii=False),
        math_warnings_json=json.dumps(result.get("math_problems", []), ensure_ascii=False),
        use_grey=use_grey,
        review_priority_score=review_priority_score,
        rag_context_json=rag_ctx_str,
        currency=getattr(data, "currency", None) or "HKD",
    )
    db.set_receipt_items(receipt_id, items_raw)
    return build_detail(db.get_receipt_row(receipt_id))


def _normalize_sku_name(name):
    """SKU 匹配用规范化：去空格/去常见规格后缀，保留核心词 + B-P0-1 流水号剥离。

    例："6包裝 維他朱古奶" → "維他朱古奶"
        "500毫升維他蘋果茉莉綠茶飲品" → "維他蘋果茉莉綠茶飲品"
        "有机菜心_1787140420" → "有机菜心" (FR-7)
        "本地新鲜菜心_1787140411" → "本地新鲜菜心"
    """
    text = str(name or "").strip()
    # B-P0-1：优先显式剥离 _\d{10} 后缀（核心词归一），再走通用清洗
    text = _SERIAL_SUFFIX_RE.sub("", text).strip()
    # 兼容：再尝试走 SmartSplitter 单例兜底（若本地正则未命中泛化流水号）
    if _sanitizer_singleton is not None and "_" in text and re.search(r"_[A-Za-z0-9_\-]{4,}$", text):
        try:
            text = _sanitizer_singleton.sanitize_name(text)
        except Exception:
            pass
    # 去掉开头的数量/包装前缀（如 6包裝、500毫升、480毫升、500mL、10x100個）
    text = re.sub(r"^\d+(\.\d+)?(x\d+)*\s*(包裝|包|毫升|mL|ml|升|L|瓶|罐|條|盒|個|合|箱|隻)?\s*", "", text)
    # 去掉结尾规格（括号内容 / 数字x数字 / 体积容量）
    text = re.sub(r"[（(][^）)]*[)）]\s*$", "", text)
    text = re.sub(r"[\s·•：:]*$", "", text)
    return text


def _match_sku(item):
    """按商品名匹配 SKU（精确 → 核心词匹配）。

    匹配到则回填 sku_id / last_price。匹配规则收紧：
    - 优先精确匹配
    - 模糊匹配要求：核心词长度 ≥2，且 SKU 名包含商品核心词（双向包含）
    - 避免"茶"这类单字误配（核心词最短 2 字符）
    """
    name = item["name"] or ""
    sku = db.find_sku_by_name(name)
    if sku is None:
        core = _normalize_sku_name(name)
        if len(core) >= 2:
            for s in db.list_skus():
                s_core = _normalize_sku_name(s.name)
                # 双向匹配都要求核心词 ≥2（避免"茶"这类单字 SKU 误配到任何含该字的长商品）
                if s_core and len(s_core) >= 2 and (s_core in core or core in s_core):
                    sku = s
                    break
    if sku:
        item["sku_id"] = sku.id
        item["matched"] = 1
        if sku.last_unit_price and item["unit_price"]:
            diff_pct = round((item["unit_price"] - sku.last_unit_price) / sku.last_unit_price * 100, 1)
            item["price_diff_percent"] = diff_pct
            if abs(diff_pct) > 10:
                item["price_anomaly"] = 1
                item["price_anomaly_direction"] = "up" if diff_pct > 0 else "down"


# -------------------------------------------------------------
# 序列化
# -------------------------------------------------------------
def build_detail(row):
    """收据行 → ReceiptDetail（/api/receipt/{id} 与 upload data 结构）。"""
    if row is None:
        return None
    items = db.get_receipt_items(row.id)
    dept_name = ""
    if row.department_id:
        for d in db.list_departments():
            if d.id == row.department_id:
                dept_name = d.name
                break

    payment_mark_val = row.payment_mark or ""
    if not payment_mark_val:
        try:
            from app.services.contract import payment_mark_from_image
            payment_mark_val = payment_mark_from_image(row.image_path or "", llm_marked=False)
        except Exception:
            pass

    return {
        "receipt_id": row.id,
        "supplier_name": row.supplier_name or "",
        "date": row.receipt_date or "",
        "sheet_name": row.sheet_name or "",
        "total_amount": row.total_amount or 0.0,
        "status": row.status,
        "settlement_type": row.settlement_type or "credit",
        "payment_mark": payment_mark_val,
        "department_id": row.department_id,
        "department_name": dept_name,
        "doc_form": row.doc_form or "",
        "layout_type": row.layout_type or "",
        "version": row.version or 1,
        "math_warnings": json.loads(row.math_warnings_json or "[]"),
        "quality_warnings": json.loads(row.quality_warnings_json or "[]"),
        "review_priority_score": row.review_priority_score or 0.0,
        "items": items,
        "audit_logs": json.loads(row.audit_logs_json or "[]"),
        "ai_prefill": json.loads(row.ai_prefill_json or "{}"),
        "audit_result": _patch_audit_reason(json.loads(row.audit_json or "{}")),
        "use_grey": row.use_grey or 0,
        "confidence": row.confidence or 0.0,
        "currency": getattr(row, "currency", None) or "HKD",
        "rag_context": getattr(row, "rag_context_json", None) or "",
        # U-2: AI 决策履历（extract 各轮 + audit），单表索引查询，detail 频次可接受
        "ai_decisions": db.list_ai_decisions(row.id),
    }


def _patch_audit_reason(audit_result):
    """阶段 1：如果 audit_result 有 discrepancies 但 reason 为空/缺失，
    根据 discrepancies 中的 issue 聚合生成人类可读 reason。
    无 discrepancies → 补简短说明。
    """
    if not isinstance(audit_result, dict):
        return audit_result
    if audit_result.get("skipped"):
        return audit_result
    reason = audit_result.get("reason")
    discrepancies = audit_result.get("discrepancies") or []
    if not discrepancies:
        if not reason:
            return {**audit_result, "reason": "AI 识别与原图一致"}
        return audit_result
    # 已有 reason 则返回原样
    if reason:
        return audit_result
    # 无 reason 时按 severity 聚合 discrepancies 中的 issue
    parts = []
    for d in discrepancies:
        if not isinstance(d, dict):
            continue
        issue = d.get("issue") or ""
        if not issue:
            continue
        parts.append(issue)
    audit_result = {**audit_result}
    audit_result["reason"] = "；".join(parts) if parts else "交叉审核发现分歧"
    return audit_result


def build_row(row):
    """收据行 → /api/receipts 列表行（含付款派生字段）。"""
    payment_status = "unknown"
    if row.payment_id and row.paid_at:
        payment_status = "paid"
    elif row.settlement_type == "credit":
        payment_status = "unpaid"
        if row.expected_pay_date:
            from datetime import date
            today = date.today().isoformat()
            if row.expected_pay_date < today:
                payment_status = "overdue"
    elif row.settlement_type == "cash":
        payment_status = "paid_at_delivery"

    # 红章补充：列表视图也补充 payment_mark 避免漏检（单一来源：contract.payment_mark_from_image）
    payment_mark_val = row.payment_mark or ""
    if not payment_mark_val:
        try:
            from app.services.contract import payment_mark_from_image
            payment_mark_val = payment_mark_from_image(row.image_path or "", llm_marked=False)
        except Exception:
            pass

    import json as _json
    quality_warnings = _json.loads(row.quality_warnings_json or "[]") if getattr(row, "quality_warnings_json", None) else []
    review_priority = getattr(row, "review_priority_score", 0.0) or 0.0

    return {
        "id": row.id,
        "supplier_name": row.supplier_name or "",
        "supplier_code": row.supplier_code or "",
        "image_url": "/uploads/" + (row.image_path.split("/")[-1] if row.image_path else ""),
        "status": row.status,
        "total_amount": row.total_amount or 0.0,
        "department_name": "",
        "payment_status": payment_status,
        "receipt_date": row.receipt_date or "",
        "upload_date": row.created_at or "",
        "updated_date": row.updated_at or "",
        "settlement_type": row.settlement_type or "",
        "doc_form": row.doc_form or "",
        "paid_method": row.paid_method or "",
        "expected_pay_date": row.expected_pay_date or "",
        "due_soon": False,
        "paid_at": row.paid_at or "",
        "payment_id": row.payment_id,
        "use_grey": row.use_grey or 0,
        "payment_mark": payment_mark_val,
        "quality_warnings": quality_warnings,
        "review_priority_score": review_priority,
        "currency": getattr(row, "currency", None) or "HKD",
    }

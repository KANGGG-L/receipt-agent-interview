# -*- coding: utf-8 -*-
"""收据数据桥接：AI 识别管线结果 ↔ 前端契约数据模型。

- run_ai_pipeline(image_path, vendor_hint): 调用 supervisor，返回前端 upload 响应 data
- build_detail(receipt_row): 收据行 → /api/receipt/{id} 的 ReceiptDetail
- build_row(receipt_row): 收据行 → /api/receipts 列表行
"""

import json
import threading

from app import db
from app.models import EngineConfig

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
        try:
            cfg = db.get_engine_config()
            from app.chains import supervisor
            result = supervisor.run_pipeline(
                image_path, vendor_hint=vendor_hint, config=cfg,
                supplier_name=vendor_hint or "",
            )
            data = result.get("data")
            if data is None:
                db.update_receipt(receipt_id, status="error")
                with JOBS_LOCK:
                    JOBS[job_id].update({
                        "job_status": "error",
                        "error_msg": result.get("contract_error") or result.get("last_error") or "识别失败",
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
                    },
                })
        except Exception as e:
            db.update_receipt(receipt_id, status="error")
            with JOBS_LOCK:
                JOBS[job_id].update({"job_status": "error", "error_msg": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return job_id, receipt_id


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
def save_parsed_data(receipt_id, data, result):
    """把 AI 结构化结果写入收据行 + 明细 + SKU 匹配。返回 detail。"""
    from app.services.contract import sanitize_nan

    items_raw = []
    for it in data.items:
        items_raw.append({
            "name": it.name, "raw_name": it.name,
            "quantity": sanitize_nan(it.qty), "unit": it.unit or "", "raw_unit": it.unit or "",
            "unit_price": sanitize_nan(it.unit_price), "amount": sanitize_nan(it.amount),
            "sku_id": None, "cost_center_id": None, "confidence": it.confidence if hasattr(it, 'confidence') else 0.5,
            "matched": 0, "price_anomaly": 0, "price_anomaly_direction": "",
            "price_diff_percent": 0.0, "unit_conversion_warning": "",
            "fuzzy_candidates": [], "entity_candidates": [],
        })
        _match_sku(items_raw[-1])

    db.update_receipt(
        receipt_id,
        status="parsed",
        supplier_name=data.vendor,
        receipt_date=data.date,
        sheet_name=(data.date or "")[:7],
        total_amount=sanitize_nan(data.total),
        doc_form=data.doc_form.value if hasattr(data.doc_form, "value") else str(data.doc_form),
        payment_mark="已付款" if data.payment_marked else "",
        confidence=sanitize_nan(data.confidence),
        raw_llm=result.get("raw", ""),
        audit_json=json.dumps(result.get("audit_result", {}), ensure_ascii=False),
        ai_prefill_json=json.dumps(data.model_dump(), ensure_ascii=False),
        math_warnings_json=json.dumps(result.get("math_problems", []), ensure_ascii=False),
    )
    db.set_receipt_items(receipt_id, items_raw)
    return build_detail(db.get_receipt_row(receipt_id))


def _normalize_sku_name(name):
    """SKU 匹配用规范化：去空格/去常见规格后缀，保留核心词。

    例："6包裝 維他朱古奶" → "維他朱古奶"
        "500毫升維他蘋果茉莉綠茶飲品" → "維他蘋果茉莉綠茶飲品"
    """
    import re
    text = str(name or "").strip()
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

    return {
        "receipt_id": row.id,
        "supplier_name": row.supplier_name or "",
        "date": row.receipt_date or "",
        "sheet_name": row.sheet_name or "",
        "total_amount": row.total_amount or 0.0,
        "status": row.status,
        "settlement_type": row.settlement_type or "credit",
        "payment_mark": row.payment_mark or "",
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
        "confidence": row.confidence or 0.0,
    }


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
    }

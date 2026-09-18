# -*- coding: utf-8 -*-
"""收据数据桥接：AI 识别管线结果 ↔ 前端契约数据模型。

- run_ai_pipeline(image_path, vendor_hint): 调用 supervisor，返回前端 upload 响应 data
- build_detail(receipt_row): 收据行 → /api/receipt/{id} 的 ReceiptDetail
- build_row(receipt_row): 收据行 → /api/receipts 列表行
"""

import hmac
import json
import os
import re
import threading
import time
from urllib.parse import quote

from app import db
from app.models import EngineConfig
from app.services import image_web
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


def _track_internal(event_type, receipt_id=None, properties=None):
    """埋点内部封装：Job 线程内无 request context，直写 user_event，失败静默。"""
    try:
        db.log_user_event(
            account_id="system",
            session_id="",
            event_type=str(event_type),
            receipt_id=int(receipt_id) if receipt_id else None,
            properties=properties or {},
        )
    except Exception:
        pass


def _job_elapsed_ms(job_id):
    with JOBS_LOCK:
        started = JOBS.get(job_id, {}).get("started_ts")
    if not started:
        return None
    return int((time.time() - started) * 1000)


def _log_max_attempt(result):
    log = result.get("log") or []
    attempts = [int(e.get("attempt", 0)) for e in log if isinstance(e, dict)]
    return max(attempts) if attempts else 1


def _log_gate_rejects(result):
    log = result.get("log") or []
    return sum(1 for e in log if isinstance(e, dict)
               and e.get("action") in ("gate_reject", "gate_reject_fast"))


# -------------------------------------------------------------
# AI 管线：异步 Job
# -------------------------------------------------------------
def start_recognition_job(image_path, vendor_hint="", receipt_id=None,
                          tenant_id=None):
    """启动后台识别 Job。返回 job_id。

    线程执行识别管线；完成后写回收据行并更新 JOBS。
    P0-1 写入链路租户透传：tenant_id 为上传入口的租户键，缺省 "default"
    向后兼容；必须在派发后台线程前捕获为局部变量，Job 线程内不得读 request。
    """
    job_id = db.new_id()
    _tenant = str(tenant_id or "default").strip() or "default"
    if receipt_id is None:
        receipt_id = db.create_receipt(status="uploaded", tenant_id=_tenant)
    db.update_receipt(receipt_id, status="parsing", image_path=image_path)

    with JOBS_LOCK:
        JOBS[job_id] = {"job_id": job_id, "job_status": "queued",
                        "receipt_id": receipt_id, "started_ts": time.time()}
    _track_internal("ocr_parse_started", receipt_id, {"job_id": job_id})

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
            experiment_id = None
            try:
                running_exp = db.get_running_experiment()
                if running_exp:
                    experiment_id = running_exp["id"]
                    target_pct = int(running_exp.get("target_percent") or 50)
                    bucket = (int(receipt_id) % 100) if receipt_id else 0
                    grp = "treatment" if bucket < target_pct else "control"
                    db.add_assignment(experiment_id, receipt_id, grp)
                    snap = running_exp.get("grey_snapshot") or {}
                    if isinstance(snap, str):
                        try:
                            snap = json.loads(snap)
                        except Exception:
                            snap = {}
                    if grp == "treatment":
                        if snap.get("treatment_model"):
                            cfg.openai_rec_model = snap["treatment_model"]
                        if snap.get("treatment_base_url"):
                            cfg.openai_rec_base_url = snap["treatment_base_url"]
                        if snap.get("treatment_api_key"):
                            cfg.openai_rec_api_key = snap["treatment_api_key"]
                    else:
                        if snap.get("control_model"):
                            cfg.openai_rec_model = snap["control_model"]
                        if snap.get("control_base_url"):
                            cfg.openai_rec_base_url = snap["control_base_url"]
                        if snap.get("control_api_key"):
                            cfg.openai_rec_api_key = snap["control_api_key"]
            except Exception as exp_err:
                logging.getLogger("receipt_utils").warning(f"解析运行中 A/B 实验路由失败: {exp_err}")

            from app.chains import supervisor
            result = supervisor.run_pipeline(
                image_path, vendor_hint=vendor_hint, config=cfg,
                supplier_name=vendor_hint or "",
                receipt_id=receipt_id,  # U-2: 透传 receipt_id，AI 决策履历落库关联单据
                experiment_id=experiment_id,
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
                _track_internal("ocr_error", receipt_id, {
                    "job_id": job_id, "status": "error",
                    "elapsed_ms": _job_elapsed_ms(job_id),
                    "reason": str(result.get("contract_error") or result.get("last_error") or "识别失败")[:300],
                    "attempts": _log_max_attempt(result),
                    "gate_rejects": _log_gate_rejects(result),
                })
                return

            detail = save_parsed_data(receipt_id, data, result)
            # 提为局部变量：原实现的 image_url/version/quality_warnings 三处各查一次，
            # 既冗余又可能在同一结果里读到不同快照
            done_row = db.get_receipt_row(receipt_id)
            # why：save_parsed_data 与 get_receipt_row 之间单据可能被删除/软删，
            # 此时 done_row 为 None。所有取自 done_row 的字段都必须纳入同一守卫，
            # 否则 version / quality_warnings_json 会抛 AttributeError，
            # 且原先只有 image_url 行有 else 分支，会让人误以为 None 已被处理。
            if done_row:
                image_url = public_image_url(receipt_id, done_row.image_path,
                                             getattr(done_row, "tenant_id", None))
                version = done_row.version
                quality_warnings = json.loads(done_row.quality_warnings_json or "[]")
            else:
                image_url = ""
                version = ""
                quality_warnings = []
            with JOBS_LOCK:
                JOBS[job_id].update({
                    "job_status": "done",
                    "result": {
                        "status": "success",
                        "receipt_id": receipt_id,
                        "data": detail,
                        "image_url": image_url,
                        "version": version,
                        "quality_warnings": quality_warnings,
                        "fallback_triggered": result.get("fallback_triggered", False),
                        "fallback_reason": result.get("fallback_reason", ""),
                        "fallback_from": result.get("fallback_from", ""),
                        "fallback_engine": result.get("fallback_engine", ""),
                    },
                })
            _track_internal("ocr_parsed", receipt_id, {
                "job_id": job_id, "status": "done",
                "elapsed_ms": _job_elapsed_ms(job_id),
                "attempts": _log_max_attempt(result),
                "gate_rejects": _log_gate_rejects(result),
                "use_grey": int(bool(result.get("use_grey"))),
            })
        except Exception as e:
            db.update_receipt(receipt_id, status="error")
            with JOBS_LOCK:
                JOBS[job_id].update({"job_status": "error", "error_msg": str(e)})
            _track_internal("ocr_error", receipt_id, {
                "job_id": job_id, "status": "error",
                "elapsed_ms": _job_elapsed_ms(job_id),
                "reason": str(e)[:300],
            })

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
        for rid in ids:
            _track_internal("ocr_error", rid, {
                "status": "timeout", "reason": "parsing_timeout",
            })
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
            job["image_url"] = public_image_url(job["receipt_id"], row.image_path,
                                                getattr(row, "tenant_id", None))
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
    from app.services.contract import sanitize_nan, normalize_evidence

    # P2-2：租户上下文从单据行派生（Job 线程内无 request 可读），
    # SKU 匹配限定本租户，避免跨租户 SKU 误匹配；行缺失时不过滤（向后兼容）
    _ctx_row = db.get_receipt_row(receipt_id)
    _tenant_ctx = None
    if _ctx_row is not None:
        _tenant_ctx = (getattr(_ctx_row, "tenant_id", "") or "").strip() or None

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
        # Gap E1 / T7：字段级证据（可选）——归一后随行落库，缺失/非法为 None 不阻断
        _ev = getattr(it, "evidence", None)
        if _ev is not None and hasattr(_ev, "model_dump"):
            _ev = _ev.model_dump()
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
            "evidence": normalize_evidence(_ev),
        })
        _match_sku(items_raw[-1], tenant_id=_tenant_ctx)

    anomaly_items = [it for it in items_raw if it.get("price_anomaly")]
    if anomaly_items:
        _track_internal("price_anomaly_flagged", receipt_id, {
            "anomaly_count": len(anomaly_items),
            "max_surge_pct": max(abs(float(it.get("price_diff_percent") or 0))
                                 for it in anomaly_items),
        })

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
        # Gap 9 / Gap 6：识别直出费用与注记落列（店员可在 save_edited 修正覆写）
        service_fee=sanitize_nan(getattr(data, "service_fee", 0.0)),
        tax_amount=sanitize_nan(getattr(data, "tax_amount", 0.0)),
        adjustment_notes_json=json.dumps(
            [str(n) for n in (getattr(data, "adjustment_notes", None) or [])],
            ensure_ascii=False),
        payment_evidence=str(getattr(data, "payment_evidence", "") or ""),
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


def _match_sku(item, tenant_id=None):
    """按商品名匹配 SKU（精确 → 核心词匹配）。

    匹配到则回填 sku_id / last_price。匹配规则收紧：
    - 优先精确匹配
    - 模糊匹配要求：核心词长度 ≥2，且 SKU 名包含商品核心词（双向包含）
    - 避免"茶"这类单字误配（核心词最短 2 字符）
    P2-2：tenant_id 从所属单据行派生传入，SKU 检索限定本租户；
    缺省 None 时不过滤（既有调用向后兼容）。
    """
    name = item["name"] or ""
    sku = db.find_sku_by_name(name, tenant_id=tenant_id)
    if sku is None:
        core = _normalize_sku_name(name)
        if len(core) >= 2:
            for s in db.list_skus(tenant_id=tenant_id):
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
def _load_json_list(raw):
    """JSON list 列安全解析（非 list/空/坏 JSON → []）。"""
    try:
        val = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return val if isinstance(val, list) else []


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
        # Gap 9 / Gap 6：店员可修正字段回读（复核界面与识别 prefill 同名直灌）
        "service_fee": float(getattr(row, "service_fee", 0.0) or 0.0),
        "tax_amount": float(getattr(row, "tax_amount", 0.0) or 0.0),
        "adjustment_notes": _load_json_list(getattr(row, "adjustment_notes_json", None)),
        "payment_evidence": str(getattr(row, "payment_evidence", "") or ""),
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


# -------------------------------------------------------------
# 原图 URL：按格式分流 + HMAC 签名防枚举
# -------------------------------------------------------------
def _sign_tenant(tenant_id):
    """签名与 URL 共用的租户归一：空 / None → default。

    why: build_row 传 row.tenant_id（可能为 None），端点传解析后的 query/header 值
    （非空，默认 default）；两侧必须归一到同一字面量，否则签名必然对不上。
    """
    return str(tenant_id or "").strip() or "default"


def preview_image_signature(receipt_id, stem, tenant_id="default"):
    """原图预览 URL 的 HMAC 签名：绑定 单据 id + 文件名主体 + 租户。

    why: 该端点匿名可取图（`<img src>` 无法携带请求头），而 receipt_id 自增可枚举；
    签名让「URL 由后端签发」成为取图前提，堵住遍历 id 的横向枚举。复用 app.auth
    的 _sign / _TOKEN_SECRET，不新造密钥：轮换 DEMO_TOKEN_SECRET 即让旧签名全部失效。
    """
    from app.auth import _sign
    payload = "%d|%s|%s" % (int(receipt_id), stem, _sign_tenant(tenant_id))
    return _sign(payload)


def verify_preview_image_signature(receipt_id, stem, tenant_id, sig):
    """常量时间比对预览签名；缺签名 / 篡改 / 参数非法一律 False。"""
    if not sig:
        return False
    try:
        expected = preview_image_signature(receipt_id, stem, tenant_id)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, str(sig))


def _quote_query_value(value):
    """查询参数值编码：只编码值本身，不编码整个 URL。

    why: stem/tenant 当前恒为十六进制与 default，实测编码前后逐字节相同（live 库
    432 条非空 URL 全量比对一致）；但 legacy 数据或手工导入可能带 空格/&/# 等字符，
    不编码会把 URL 从中间截断或让端点解不出原值。safe="" 让 "/" 也参与编码（它是
    值而非路径分隔符）。签名仍基于未编码的原值计算——端点的 query 解析会自动解回
    原值，故签名值不因编码而变，编码仅作用于 URL 字面量。
    """
    return quote(str(value or ""), safe="")


def public_image_url(receipt_id, image_path, tenant_id="default"):
    """收据原图对外 URL：Web 格式沿用 /uploads 静态地址，非 Web 格式走转码端点。

    why: `.heic/.tiff` 经 StaticFiles 返回 text/plain，浏览器不渲染；非 Web 格式
    改指 GET /api/receipt/{id}/image 按需转码。Web 格式保持 `/uploads/<basename>`
    逐字节不变（1490 张 jpg/png 零回归）。
    `?v=<stem>` 兼作版本参数：replace-image 换图后 receipt_id 不变，不带版本参数
    会命中旧图的浏览器/HTTP 缓存。
    `&sig=<HMAC(id|stem|租户)>` 是该端点的取图凭证（端点为浏览器原生发起、带不了
    请求头，故不能靠 RBAC）；签名随 URL 由后端签发，前端零改动，见
    verify_preview_image_signature。

    URL 只对查询参数值做 percent 编码（见 _quote_query_value）：签名基于未编码的
    原值，端点由 query 解析自动解回原值，故签名与取图行为不受编码影响。

    行为变更（相对旧实现，显式记录）：旧实现是
    `"/uploads/" + (image_path.split("/")[-1] if image_path else "")`，即
    image_path 为空时返回 `"/uploads/"` —— 一个指向目录、浏览器打不开的无效 URL
    （live 库有 238 条空 image_path 单据走这条分支）。本函数对空 image_path 返回
    `""`，语义为「无图」，由消费方按空值降级处理（前端须有 `if (url)` /
    `|| fallback` 守卫，见 main.js 归档弹窗）。
    """
    if not image_path:
        # 空路径 = 无图：返回空串（不是 "/uploads/"），避免调用方拿到无效 URL
        return ""
    name = str(image_path).split("/")[-1]
    if not image_web.is_non_web_image_path(name):
        return "/uploads/" + name
    # 非 Web 格式：receipt_id 缺失/非法时退回静态地址，保持旧行为不退化为坏 URL
    try:
        rid = int(receipt_id)
    except (TypeError, ValueError):
        return "/uploads/" + name
    stem = image_web.preview_stem(name)
    tenant = _sign_tenant(tenant_id)
    url = "/api/receipt/%d/image?v=%s&sig=%s" % (
        rid, _quote_query_value(stem), preview_image_signature(rid, stem, tenant))
    if tenant != "default":
        url += "&tenant_id=" + _quote_query_value(tenant)
    return url


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

    # W2 列表性能修复：列表行不再逐条读图补红章，直接返回已落库的 payment_mark。
    # why: 旧实现对本行 payment_mark 为空时调 contract.payment_mark_from_image →
    # detect_red_stamp 解码图片，配合 list_receipts 的同类补充形成每请求上千次读图
    # （详见 api_receipts.list_receipts）。红章补充改由详情视图承担
    # （build_detail 与本文件的 detail 路径）。
    payment_mark_val = row.payment_mark or ""

    import json as _json
    quality_warnings = _json.loads(row.quality_warnings_json or "[]") if getattr(row, "quality_warnings_json", None) else []
    review_priority = getattr(row, "review_priority_score", 0.0) or 0.0

    return {
        "id": row.id,
        "supplier_name": row.supplier_name or "",
        "supplier_code": row.supplier_code or "",
        "image_url": public_image_url(row.id, row.image_path,
                                      getattr(row, "tenant_id", None)),
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


# -------------------------------------------------------------
# 埋点：人工复核 diff（AI 原版 vs 用户最终提交）
# -------------------------------------------------------------
_DIFF_FIELDS = ("name", "quantity", "unit", "unit_price", "amount")


def compute_review_diff(ai_items, final_items):
    """行级三类 diff + SKU 更改计数（纯函数，无 DB 依赖）。

    对齐算法：按位置 zip 到 min 长度逐行比关键字段；final 多出计 added，
    ai 多出计 deleted。ai_items 为空（手工单无 AI 原版）→ 全部计 added。
    """
    ai_items = ai_items or []
    final_items = final_items or []
    rows_added = rows_modified = rows_deleted = sku_changed = 0
    field_mod_counts = {f: 0 for f in _DIFF_FIELDS}

    for ai_it, fin_it in zip(ai_items, final_items):
        ai_it = ai_it if isinstance(ai_it, dict) else {}
        fin_it = fin_it if isinstance(fin_it, dict) else {}
        changed = False
        for f in _DIFF_FIELDS:
            av, fv = ai_it.get(f), fin_it.get(f)
            if f in ("quantity", "unit_price", "amount"):
                av = float(av or 0)
                fv = float(fv or 0)
            elif f == "name":
                av = canonical_sku_name(av or "")
                fv = canonical_sku_name(fv or "")
            if av != fv:
                field_mod_counts[f] += 1
                changed = True
        ai_sku = ai_it.get("sku_id")
        if ai_sku != fin_it.get("sku_id"):
            changed = True
        if ai_sku and (ai_sku != fin_it.get("sku_id")
                       or canonical_sku_name(ai_it.get("name") or "")
                       != canonical_sku_name(fin_it.get("name") or "")):
            sku_changed += 1
        if changed:
            rows_modified += 1

    rows_added = len(final_items) - min(len(ai_items), len(final_items))
    rows_deleted = len(ai_items) - min(len(ai_items), len(final_items))
    return {
        "rows_added": rows_added,
        "rows_modified": rows_modified,
        "rows_deleted": rows_deleted,
        "sku_changed": sku_changed,
        "field_mod_counts": field_mod_counts,
        "total_ai": len(ai_items),
        "total_final": len(final_items),
    }

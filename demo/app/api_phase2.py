# -*- coding: utf-8 -*-
"""阶段 2 端点：AI 指标 / 灰测对比 / 使用漏斗 / A/B 实验。

数据层（表 + SQLAlchemy 行类）由 db.py 提供：
ai_decision_log / ai_metric_snapshot / user_event / experiment / experiment_assignment。
本模块只写查询 + 写入逻辑，owner-protected。
"""

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import json
import math
from datetime import datetime, timedelta

from app import db
from app.api_admin import resolve_tenant_filter
from app.auth import require_role, require_admin, resolve_tenant

router = APIRouter()


def _who(request: Request) -> str:
    return getattr(request.state, "account", {}).get("email", "unknown")


# -------------------------------------------------------------
# 采集点 helper（供其他模块 import 调用）
# -------------------------------------------------------------
def record_ai_decision(*, receipt_id: Optional[int] = None, supplier_id: Optional[int] = None,
                       experiment_id: Optional[int] = None, grp: str = "control",
                       engine: str = "", model: str = "", use_grey: int = 0,
                       decision_type: str = "extract", field_path: str = "",
                       ai_value: str = "", user_value: Optional[str] = None,
                       adopted: Optional[int] = None, confidence: Optional[float] = None,
                       is_hallucination: Optional[int] = None, extra: str = "") -> None:
    """记一条 AI 决策。采纳判定在 approve 时回填。"""
    _s = db.get_session()
    try:
        row = db._DecisionLogRow(
            ts=db.now_iso(), receipt_id=receipt_id, supplier_id=supplier_id,
            experiment_id=experiment_id, grp=grp, engine=engine, model=model,
            use_grey=use_grey, decision_type=decision_type, field_path=field_path,
            ai_value=str(ai_value), user_value=user_value, adopted=adopted,
            confidence=confidence, is_hallucination=is_hallucination, extra=extra,
        )
        _s.add(row)
        _s.commit()
    finally:
        _s.close()


def record_user_event(*, event_type: str, receipt_id: Optional[int] = None,
                      account_id: str = "", session_id: str = "",
                      properties: Optional[dict] = None, grp: Optional[str] = None) -> None:
    """前端/后端埋点。"""
    _s = db.get_session()
    try:
        row = db._UserEventRow(
            ts=db.now_iso(), account_id=account_id, session_id=session_id,
            event_type=event_type, receipt_id=receipt_id,
            properties=json.dumps(properties or {}), grp=grp,
        )
        _s.add(row)
        _s.commit()
    finally:
        _s.close()


def _compute_metrics(rows) -> dict:
    """从 ai_decision_log 行集合实时聚合核心指标。"""
    total = len(rows)
    if total == 0:
        return {"accuracy": None, "hallucination_rate": None, "edit_rate": None,
                "audit_adoption_rate": None, "trust_score": None, "sample_size": 0,
                "low_confidence": True}
    # 准确率：ai_value == user_value 且已决策（user_value 非空）
    decided = [r for r in rows if r.user_value is not None]
    if decided:
        accuracy = round(sum(1 for r in decided if str(r.ai_value) == str(r.user_value)) / len(decided), 4)
    else:
        accuracy = None
    # 修正率：user_value != ai_value
    edit_rate = round(sum(1 for r in decided if str(r.ai_value) != str(r.user_value)) / len(decided), 4) if decided else None
    # 幻觉率
    h = [r for r in rows if r.is_hallucination is not None]
    hallucination_rate = round(sum(1 for r in h if r.is_hallucination == 1) / len(h), 4) if h else None
    # 审核采纳率
    adopted_rows = [r for r in rows if r.adopted is not None]
    audit_adoption_rate = round(sum(1 for r in adopted_rows if r.adopted == 1) / len(adopted_rows), 4) if adopted_rows else None
    # 信任度（采纳率滑窗近似）
    trust_score = audit_adoption_rate
    return {"accuracy": accuracy, "hallucination_rate": hallucination_rate,
            "edit_rate": edit_rate, "audit_adoption_rate": audit_adoption_rate,
            "trust_score": trust_score, "sample_size": total,
            "low_confidence": total < 30}


# -------------------------------------------------------------
# 2.3 使用漏斗
# -------------------------------------------------------------
FUNNEL_DEFAULT = [
    ("upload", ("upload", "receipt_uploaded")),
    ("ocr_parse_started", ("ocr_parse_started",)),
    ("ocr_parsed", ("parse_done", "ocr_parsed")),
    ("edit", ("save_edited", "receipt_review_submitted")),
    ("approve", ("approve", "receipt_approved")),
    ("inventory_in", ("inventory_in",)),
]

@router.get("/api/funnel")
@router.get("/api/admin/funnel")  # 旧路径别名，下个版本删除：老板级接口不应挂在 admin 路径下
def funnel(request: Request):
    require_role("owner")(request)
    period = request.query_params.get("period", "7d")
    steps = FUNNEL_DEFAULT
    s = db.get_session()
    try:
        rows = db.scoped(s.query(db._UserEventRow), db._UserEventRow,
                         resolve_tenant(request)).all()
    finally:
        s.close()
    # 按 event_type 计数（先做全量；period 过滤在 event_type 维度暂不强制，数据少时全量即可）
    counts = {}
    for r in rows:
        counts[r.event_type] = counts.get(r.event_type, 0) + 1
    first = sum(counts.get(k, 0) for k in steps[0][1]) if steps else 0
    out = []
    for step_name, event_keys in steps:
        c = sum(counts.get(k, 0) for k in event_keys)
        step_rate = round(c / first, 4) if first else None
        cum_rate = round(c / first, 4) if first else None
        out.append({"step": step_name, "count": c, "step_rate": step_rate, "cumulative_rate": cum_rate})
    return {"status": "success", "data": {"steps": out, "period": period,
            "low_confidence": first < 30}}


# -------------------------------------------------------------
# 2.5 埋点观测台：挽回与全量埋点聚合
# 口径见《11-组件Spec-全链路埋点与体验反馈体系》§7.1：
# 全量事件分布 / 挽回点击归因（输入:模型）/ 点踩率 / 挽回成功率 /
# 最近事件流。仿 /api/admin/funnel：全量查 user_event + Python 循环，
# 不用 SQL group by；分母 < 30 附 low_confidence 标记。
# -------------------------------------------------------------
RECOVERY_CLICK_EVENTS = ("retake_clicked", "reparse_clicked")
RECOVERY_SUCCESS_EVENTS = ("receipt_review_submitted", "receipt_approved")


def _parse_props(raw):
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return {}


def _is_telemetry_probe_event(event_type: str) -> bool:
    """过滤自动化测试探针、会话探测等非业务行为事件，确保业务观测大盘口径纯净。"""
    if not event_type:
        return True
    et = str(event_type).lower()
    return et.startswith("qa_") or "probe" in et or et.startswith("test_") or et == "totally_unknown_evt_zzz"


@router.get("/api/analytics/recovery-summary")
def recovery_summary(
    request: Request,
    tenant_id: Optional[str] = None,
    date_range: str = "all",
    start_date: str = "",
    end_date: str = "",
):
    require_admin(request)
    from app.services.parsing_logs import calculate_recovery_and_feedback_metrics
    
    # 租户入参：Query 参数优先，其次请求 Header（与 api_admin 共用解析器）
    effective_tenant = resolve_tenant_filter(request, tenant_id)

    s = db.get_session()
    try:
        all_rows = s.query(db._UserEventRow).all()
        # 收集系统中存在的所有租户 ID（去重排序）
        found_tenants = set()
        for r in all_rows:
            if getattr(r, "tenant_id", None):
                found_tenants.add(r.tenant_id)
        try:
            for r in s.query(db._ReceiptRow.tenant_id).distinct():
                if r[0]:
                    found_tenants.add(r[0])
        except Exception:
            pass
        if not found_tenants:
            found_tenants.add("default")
        available_tenants = sorted(list(found_tenants))

        # 按租户过滤
        if effective_tenant and effective_tenant != "all":
            rows = [r for r in all_rows if (getattr(r, "tenant_id", None) or "default") == effective_tenant]
        else:
            rows = all_rows
        # 排除非业务探测/测试探针事件（如 qa_fe_probe, verify_session_probe 等）
        rows = [r for r in rows if not _is_telemetry_probe_event(r.event_type)]

        # 严格时间窗口过滤 (Truthfulness 保障：事件分布对齐时间跨度)
        now_dt = datetime.now()
        start_ts = ""
        if date_range == "24h":
            start_ts = (now_dt - timedelta(days=1)).isoformat()
        elif date_range == "7d":
            start_ts = (now_dt - timedelta(days=7)).isoformat()
        elif date_range == "14d":
            start_ts = (now_dt - timedelta(days=14)).isoformat()
        elif date_range == "30d":
            start_ts = (now_dt - timedelta(days=30)).isoformat()

        if start_ts:
            rows = [r for r in rows if (r.ts or "") >= start_ts]
        if start_date:
            rows = [r for r in rows if (r.ts or "") >= start_date]
        if end_date:
            rows = [r for r in rows if (r.ts or "") <= end_date]

        # 统一指标计算器 (SSOT 唯一事实源)
        metrics = calculate_recovery_and_feedback_metrics(
            s=s,
            tenant_id=effective_tenant,
            date_range=date_range,
            start_date=start_date,
            end_date=end_date,
        )
    finally:
        s.close()

    total = len(rows)
    # 1) 全量事件分布：所有 event_type 的计数 / 占比 / 最近触发时间（计数降序）
    counts = {}
    last_ts = {}
    for r in rows:
        counts[r.event_type] = counts.get(r.event_type, 0) + 1
        cur = last_ts.get(r.event_type)
        if cur is None or (r.ts or "") > cur:
            last_ts[r.event_type] = r.ts or ""
    distribution = [
        {"event_type": k, "count": c,
         "share": round(c / total, 4) if total else 0.0,
         "last_ts": last_ts.get(k, "")}
        for k, c in sorted(counts.items(), key=lambda kv: (kv[1], str(last_ts.get(kv[0]) or "")), reverse=True)
    ]

    # 2) 挽回点击：重拍 / 重新解析计数及占比 + 挽回点击入口率（/ocr_parsed）
    retake = counts.get("retake_clicked", 0)
    reparse = counts.get("reparse_clicked", 0)
    recovery_total = retake + reparse
    parsed_total = counts.get("ocr_parsed", 0)
    recovery = {
        "retake_clicked": retake,
        "reparse_clicked": reparse,
        "total": recovery_total,
        "input_attribution_share": round(retake / recovery_total, 4) if recovery_total else None,
        "model_attribution_share": round(reparse / recovery_total, 4) if recovery_total else None,
        "entry_rate": round(recovery_total / parsed_total, 4) if parsed_total else None,
        "entry_rate_desc": f"挽回点击 {recovery_total} / 解析单据 {parsed_total}" if parsed_total else "",
    }

    # 3) 点踩率：receipt_feedback down 数 / 有反馈的去重单据数
    # （口径与 /api/analytics/recognition-summary 的 feedback 块同源）
    if effective_tenant:
        fbs = db.list_receipt_feedbacks(tenant_id=effective_tenant)
    else:
        fbs = db.list_receipt_feedbacks()
    fb_down = sum(1 for f in fbs if f.get("like") == -1)
    fb_receipts = len({f.get("receipt_id") for f in fbs
                       if f.get("receipt_id") is not None})
    fb_down_receipts = len({f.get("receipt_id") for f in fbs
                            if f.get("receipt_id") is not None and f.get("like") == -1})
    fb_liked_receipts = len({f.get("receipt_id") for f in fbs
                             if f.get("receipt_id") is not None and f.get("like") == 1})
    fb_liked = sum(1 for f in fbs if f.get("like") == 1)
    feedback = {
        "down": fb_down,
        "feedbacked_receipts": fb_receipts,
        "disliked_receipts_count": fb_down_receipts,
        "down_rate": round(fb_down_receipts / fb_receipts, 4) if fb_receipts else None,
        "dislike_frequency": round(fb_down / fb_down_receipts, 2) if fb_down_receipts else 1.0,
        "positive_rate": round(fb_liked_receipts / fb_receipts, 4) if fb_receipts else None,
        "liked_receipts_count": fb_liked_receipts,
    }

    # 4) 挽回成功率：每条挽回点击事件，在同 receipt_id 找 ts 更晚的
    #    receipt_review_submitted / receipt_approved（ts 秒级同值时以自增 id 定序）
    success_points = {}
    for r in rows:
        if r.event_type in RECOVERY_SUCCESS_EVENTS and r.receipt_id is not None:
            success_points.setdefault(r.receipt_id, []).append((r.ts or "", r.id))
    recovery_rows = [r for r in rows if r.event_type in RECOVERY_CLICK_EVENTS]
    success_count = 0
    for r in recovery_rows:
        if r.receipt_id is None:
            continue
        point = (r.ts or "", r.id)
        if any(sp > point for sp in success_points.get(r.receipt_id, [])):
            success_count += 1
    recovery_success = {
        "success": success_count,
        "total": len(recovery_rows),
        "rate": round(success_count / len(recovery_rows), 4) if recovery_rows else None,
    }

    # 5) 最近事件流（最近 50 条，保留供只读与兼容）
    recent = sorted(rows, key=lambda r: ((r.ts or ""), r.id), reverse=True)[:50]
    recent_events = [
        {"ts": r.ts or "", "event_type": r.event_type,
         "receipt_id": r.receipt_id, "account_id": r.account_id or "",
         "properties": _parse_props(r.properties)}
        for r in recent
    ]

    return {"status": "success", "data": {
        "tenant_id": effective_tenant or "all",
        "available_tenants": available_tenants,
        "event_distribution": distribution,
        "recovery": recovery,
        "feedback": feedback,
        "recovery_success": recovery_success,
        "recent_events": recent_events,
        "total_events": total,
        "low_confidence": total < 30,
    }}


# -------------------------------------------------------------
# 2.6 埋点事件明细审计与分页下钻接口 (Drill-down Event Logs API)
# -------------------------------------------------------------
@router.get("/api/analytics/events/logs")
def analytics_event_logs(
    request: Request,
    event_type: str = "",
    tenant_id: Optional[str] = "all",
    search: str = "",
    page: int = 1,
    page_size: int = 20,
):
    """供埋点事件下钻审计模态框专用，根据特定 event_type 检索与分页明细时序日志。"""
    require_admin(request)
    from app.services.parsing_logs import get_telemetry_event_alias

    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    effective_tenant = resolve_tenant_filter(request, tenant_id)

    s = db.get_session()
    try:
        q = s.query(db._UserEventRow)
        if event_type:
            q = q.filter(db._UserEventRow.event_type == event_type)

        if effective_tenant and effective_tenant != "all":
            if effective_tenant == "default":
                q = q.filter(
                    (db._UserEventRow.tenant_id == "default")
                    | (db._UserEventRow.tenant_id.is_(None))
                    | (db._UserEventRow.tenant_id == "")
                )
            else:
                q = q.filter(db._UserEventRow.tenant_id == effective_tenant)

        if search:
            search_str = search.strip()
            like_str = f"%{search_str}%"
            s_num = search_str.lstrip("#").strip()
            rid_filter = False
            if s_num.isdigit():
                try:
                    rid_val = int(s_num)
                    rid_filter = (db._UserEventRow.receipt_id == rid_val)
                except ValueError:
                    rid_filter = False

            if rid_filter is not False:
                q = q.filter(
                    (rid_filter)
                    | (db._UserEventRow.account_id.like(like_str))
                    | (db._UserEventRow.properties.like(like_str))
                    | (db._UserEventRow.event_type.like(like_str))
                )
            else:
                q = q.filter(
                    (db._UserEventRow.account_id.like(like_str))
                    | (db._UserEventRow.properties.like(like_str))
                    | (db._UserEventRow.event_type.like(like_str))
                )

        total_items = q.count()
        total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 1

        rows = (
            q.order_by(db._UserEventRow.ts.desc(), db._UserEventRow.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        items = []
        for r in rows:
            raw_ts = str(r.ts or "").strip()
            fmt_ts = raw_ts.replace("T", " ").split(".")[0][:19] if raw_ts else "-"
            props = _parse_props(r.properties)
            items.append({
                "id": r.id,
                "ts": fmt_ts,
                "event_type": r.event_type,
                "receipt_id": r.receipt_id,
                "account_id": r.account_id or "system",
                "tenant_id": r.tenant_id or "default",
                "properties": props,
            })

        alias = get_telemetry_event_alias(event_type) if event_type else "全量业务事件"

        return {
            "status": "success",
            "data": {
                "event_type": event_type,
                "event_alias": alias,
                "tenant_id": effective_tenant or "all",
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_items": total_items,
                    "total_pages": total_pages,
                },
                "items": items,
            },
        }
    finally:
        s.close()




# -------------------------------------------------------------
# 2.7 8 大核心 KPI 时序趋势与多维下钻分析接口 (KPI Trends API)
# -------------------------------------------------------------
@router.get("/api/analytics/kpi-trends")
def analytics_kpi_trends(
    request: Request,
    metric_key: str = "",
    days: int = 7,
    tenant_id: Optional[str] = "all",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """观测中心链路审计看板：8 大核心 KPI 时序趋势与多维下钻分析。
    遵循 PRD-OBS-KPI-DRILLDOWN-V2-20260930 规格规范。
    支持自定义起止日期筛选 (start_date, end_date) 与 N+1 天基线预取。
    仅提供 end_date 时，start_date 默认解析为最早一条收据记录的日期。
    """
    require_admin(request)
    from app.services.parsing_logs import compute_kpi_trends, KPI_METRIC_META

    m_key = (metric_key or "").strip()
    if not m_key or m_key not in KPI_METRIC_META:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"不支持或未指定的指标类型: '{metric_key}'，必须为 8 大核心 KPI 之一",
                "supported_metrics": list(KPI_METRIC_META.keys()),
            },
        )

    s_date = (start_date or "").strip()
    e_date = (end_date or "").strip()
    if s_date and not e_date:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "提供 start_date 时必须同时提供 end_date（或仅提供 end_date，开始日期默认为最早一条记录）",
            },
        )
    if e_date:
        try:
            p_end = datetime.strptime(e_date, "%Y-%m-%d").date()
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "日期格式非法，必须符合 YYYY-MM-DD 规范",
                },
            )
        if s_date:
            try:
                p_start = datetime.strptime(s_date, "%Y-%m-%d").date()
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "message": "日期格式非法，必须符合 YYYY-MM-DD 规范",
                    },
                )
            if p_start > p_end:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "message": "起始日期 start_date 不能晚于截止日期 end_date",
                    },
                )
            if (p_end - p_start).days > 90:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "message": "自定义时间段跨度不能超过 90 天",
                    },
                )

    try:
        days_int = int(days)
    except (ValueError, TypeError):
        days_int = 7
    days_int = max(1, min(90, days_int))

    effective_tenant = resolve_tenant_filter(request, tenant_id)

    s = db.get_session()
    try:
        if e_date and not s_date:
            # 开始日期留空：默认从最早一条收据记录开始（口径与主查询一致：
            # 排除已删除单据、尊重租户过滤，且不晚于结束日期）
            from sqlalchemy import func as sa_func
            min_q = s.query(sa_func.min(db._ReceiptRow.created_at)).filter(
                db._ReceiptRow.deleted_at.is_(None),
                db._ReceiptRow.created_at <= f"{e_date}T23:59:59.999999",
            )
            if effective_tenant and effective_tenant != "all":
                if effective_tenant == "default":
                    min_q = min_q.filter(
                        (db._ReceiptRow.tenant_id == "default")
                        | (db._ReceiptRow.tenant_id.is_(None))
                        | (db._ReceiptRow.tenant_id == "")
                    )
                else:
                    min_q = min_q.filter(db._ReceiptRow.tenant_id == effective_tenant)
            min_created = min_q.scalar()
            s_date = str(min_created)[:10] if min_created else e_date

        data = compute_kpi_trends(
            s=s,
            metric_key=m_key,
            days=days_int,
            effective_tenant=effective_tenant,
            start_date=s_date if s_date else None,
            end_date=e_date if e_date else None,
        )
        return {"status": "success", "data": data}
    finally:
        s.close()


# -------------------------------------------------------------
# 2.4 A/B 实验
# -------------------------------------------------------------
class ExpConcludeBody(BaseModel):
    conclusion: str  # promote|rollback|inconclusive
    conclusion_reason: str
    # 兼容旧客户端的字段：结题人只取自登录身份，请求体里的值一律忽略
    concluded_by: Optional[str] = None


@router.post("/api/admin/experiments/{exp_id}/conclude")
def conclude_experiment(exp_id: int, body: ExpConcludeBody, request: Request):
    # 实验是平台级对象（没有租户归属），创建/启动/停止都是超管操作，结题同级
    require_admin(request)
    who = (getattr(request.state, "account", None) or {}).get("email", "unknown")
    _s = db.get_session()
    try:
        exp = _s.query(db._ExperimentRow).get(exp_id)
        if not exp:
            return JSONResponse(status_code=404, content={"status": "error", "code": "EXPERIMENT_NOT_FOUND", "msg": "找不到这个实验，请刷新页面后再试。"})
        if exp.status == "concluded":
            return JSONResponse(status_code=409, content={"status": "error", "code": "EXPERIMENT_CONCLUDED", "msg": "这个实验已经结题了，不能重复结题。"})
        # 校验 min_sample
        logs = _s.query(db._DecisionLogRow).filter(
            db._DecisionLogRow.experiment_id == exp_id
        ).all()
        n = len([l for l in logs if l.grp == "treatment"])
        # min_sample 为 NULL（raw SQL 直插等）时不能直接比较：TypeError → 500。
        # 口径与守护门/落库侧一致（guardian.py 与 create_experiment 均用
        # db.coerce_int(min_sample, 100)）：NULL 回落设计默认 100，显式 0 保留为
        # 「不做样本量门槛」，不在此处自创语义。
        min_sample = db.coerce_int(exp.min_sample, 100)
        if n < min_sample:
            return JSONResponse(status_code=400, content={"status": "error", "code": "SAMPLE_TOO_SMALL", "msg": f"样本还不够：目前只有 {n} 条，至少需要 {min_sample} 条才能结题。"})
        exp.conclusion = body.conclusion
        exp.conclusion_reason = body.conclusion_reason
        exp.concluded_by = who
        exp.concluded_at = db.now_iso()
        exp.status = "concluded"
        exp.end_ts = db.now_iso()
        _s.commit()
        return {"status": "success", "id": exp.id, "conclusion": exp.conclusion,
                "sample_size_treatment": n}
    finally:
        _s.close()

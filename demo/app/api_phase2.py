# -*- coding: utf-8 -*-
"""阶段 2 端点：AI 指标 / 灰测对比 / 使用漏斗 / A/B 实验。

数据层（表 + SQLAlchemy 行类）由 db.py 提供：
ai_decision_log / ai_metric_snapshot / user_event / experiment / experiment_assignment。
本模块只写查询 + 写入逻辑，owner-protected。
"""

from datetime import datetime
from math import sqrt
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import json

from app import db
from app.api_admin import resolve_tenant_filter
from app.auth import require_role, require_admin

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
# 2.1 AI 指标
# -------------------------------------------------------------
@router.get("/api/admin/ai-metrics")
def ai_metrics(request: Request):
    require_role("owner")(request)
    grp = request.query_params.get("group")
    metric = request.query_params.get("metric") or "accuracy"
    granularity = request.query_params.get("granularity") or "global"
    s = db.get_session()
    try:
        q = s.query(db._DecisionLogRow)
        if grp in ("control", "treatment"):
            q = q.filter(db._DecisionLogRow.grp == grp)
        rows = q.all()
    finally:
        s.close()
    data = _compute_metrics(rows)
    return {"status": "success", "data": data, "metric": metric, "granularity": granularity}


# -------------------------------------------------------------
# 2.2 灰测效果对比
# -------------------------------------------------------------
@router.get("/api/admin/grey-compare")
def grey_compare(request: Request):
    require_role("owner")(request)
    s = db.get_session()
    try:
        ctrl = s.query(db._DecisionLogRow).filter(db._DecisionLogRow.grp == "control").all()
        treat = s.query(db._DecisionLogRow).filter(db._DecisionLogRow.grp == "treatment").all()
    finally:
        s.close()
    c = _compute_metrics(ctrl)
    t = _compute_metrics(treat)

    def diff(a, b):
        if a is None or b is None:
            return None
        return round(b - a, 4)  # treatment - control

    return {"status": "success", "data": {
        "control": c, "treatment": t,
        "diff": {k: diff(c.get(k), t.get(k)) for k in ("accuracy", "hallucination_rate", "edit_rate", "audit_adoption_rate", "trust_score")}
    }}


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

@router.get("/api/admin/funnel")
def funnel(request: Request):
    require_role("owner")(request)
    period = request.query_params.get("period", "7d")
    steps = FUNNEL_DEFAULT
    s = db.get_session()
    try:
        rows = s.query(db._UserEventRow).all()
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


@router.get("/api/analytics/recovery-summary")
def recovery_summary(request: Request, tenant_id: Optional[str] = None):
    require_admin(request)
    
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
        if effective_tenant:
            rows = [r for r in all_rows if (getattr(r, "tenant_id", None) or "default") == effective_tenant]
        else:
            rows = all_rows
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
        for k, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # 2) 挽回点击：输入归因（重拍）: 模型归因（重新解析）
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
    feedback = {
        "down": fb_down,
        "feedbacked_receipts": fb_receipts,
        "down_rate": round(fb_down / fb_receipts, 4) if fb_receipts else None,
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

    # 5) 最近事件流（最近 50 条，供人工逐条核对）
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
# 2.4 A/B 实验
# -------------------------------------------------------------
class ExpCreateBody(BaseModel):
    name: str
    hypothesis: str = ""
    success_metric: str = "accuracy"
    guardrail_metrics: list = []
    target_percent: int = 0
    target_supplier_ids: list = []
    min_sample: int = 100


class ExpConcludeBody(BaseModel):
    conclusion: str  # promote|rollback|inconclusive
    conclusion_reason: str
    concluded_by: str


def _z_test(n1: int, x1: int, n2: int, x2: int) -> Optional[dict]:
    """双比例 z 检验（treatment vs control）。n1=control, n2=treatment。"""
    if n1 < 2 or n2 < 2:
        return {"p": None, "z": None, "insufficient": True}
    p1, p2 = x1 / n1, x2 / n2
    pp = (x1 + x2) / (n1 + n2)
    denom = pp * (1 - pp) * (1 / n1 + 1 / n2)
    if denom <= 0:
        return {"p": None, "z": None, "insufficient": True}
    z = (p2 - p1) / sqrt(denom)
    # 标准正态双尾 p 值（近似）
    p = _norm_cdf_two_tail(abs(z))
    return {"p": round(p, 5), "z": round(z, 4), "insufficient": False}


def _norm_cdf_two_tail(x: float) -> float:
    """|Z| 双尾 p 值近似（Abramowitz & Stegun）。"""
    t = 1.0 / (1.0 + 0.2316419 * x)
    d = 0.3989422804014327  # 1/sqrt(2pi)
    p_one = d * exp(-x * x / 2) * (t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))))
    return 2 * p_one


from math import exp  # 供 _norm_cdf_two_tail


@router.post("/api/admin/experiments")
def create_experiment(body: ExpCreateBody, request: Request):
    require_role("owner")(request)
    _s = db.get_session()
    try:
        row = db._ExperimentRow(
            name=body.name, hypothesis=body.hypothesis,
            success_metric=body.success_metric,
            guardrail_metrics=json.dumps(body.guardrail_metrics),
            status="draft", target_percent=body.target_percent,
            target_supplier_ids=json.dumps(body.target_supplier_ids),
            min_sample=body.min_sample,
        )
        _s.add(row)
        _s.commit()
        return {"status": "success", "id": row.id}
    finally:
        _s.close()


@router.post("/api/admin/experiments/{exp_id}/start")
def start_experiment(exp_id: int, request: Request):
    require_role("owner")(request)
    _s = db.get_session()
    try:
        row = _s.query(db._ExperimentRow).get(exp_id)
        if not row:
            return JSONResponse(status_code=404, content={"status": "error", "msg": "实验不存在"})
        row.status = "running"
        row.start_ts = db.now_iso()
        _s.commit()
        return {"status": "success", "id": row.id, "start_ts": row.start_ts}
    finally:
        _s.close()


@router.get("/api/admin/experiments")
def list_experiments(request: Request):
    require_role("owner")(request)
    _s = db.get_session()
    try:
        rows = _s.query(db._ExperimentRow).order_by(db._ExperimentRow.id.desc()).all()
    finally:
        _s.close()
    return {"status": "success", "data": [_exp_to_dict(r) for r in rows]}


@router.get("/api/admin/experiments/{exp_id}")
def get_experiment(exp_id: int, request: Request):
    require_role("owner")(request)
    _s = db.get_session()
    try:
        exp = _s.query(db._ExperimentRow).get(exp_id)
        if not exp:
            return JSONResponse(status_code=404, content={"status": "error", "msg": "实验不存在"})
        # 分组指标对比 + z 检验
        logs = _s.query(db._DecisionLogRow).filter(
            db._DecisionLogRow.experiment_id == exp_id
        ).all()
        assigns = _s.query(db._ExperimentAssignRow).filter(
            db._ExperimentAssignRow.experiment_id == exp_id
        ).all()
    finally:
        _s.close()
    ctrl = [l for l in logs if l.grp == "control"]
    treat = [l for l in logs if l.grp == "treatment"]
    cd = _compute_metrics(ctrl)
    td = _compute_metrics(treat)
    # 用 accuracy 做主指标 z 检验：x = 正确数, n = decided 数
    z = None
    if cd["sample_size"] > 0 and td["sample_size"] > 0:
        # accuracy 已是比例，反推：x = accuracy * n（近似）
        x1 = round((cd.get("accuracy") or 0) * cd["sample_size"])
        x2 = round((td.get("accuracy") or 0) * td["sample_size"])
        z = _z_test(cd["sample_size"], x1, td["sample_size"], x2)
    return {"status": "success", "data": {
        **_exp_to_dict(exp),
        "control": cd, "treatment": td,
        "assignments": len(assigns), "z_test": z,
    }}


@router.post("/api/admin/experiments/{exp_id}/conclude")
def conclude_experiment(exp_id: int, body: ExpConcludeBody, request: Request):
    require_role("owner")(request)
    _s = db.get_session()
    try:
        exp = _s.query(db._ExperimentRow).get(exp_id)
        if not exp:
            return JSONResponse(status_code=404, content={"status": "error", "msg": "实验不存在"})
        if exp.status == "concluded":
            return JSONResponse(status_code=400, content={"status": "error", "msg": "实验已结题"})
        # 校验 min_sample
        logs = _s.query(db._DecisionLogRow).filter(
            db._DecisionLogRow.experiment_id == exp_id
        ).all()
        n = len([l for l in logs if l.grp == "treatment"])
        if n < exp.min_sample:
            return JSONResponse(status_code=400, content={"status": "error", "msg": f"样本量不足：treatment={n} < min_sample={exp.min_sample}"})
        exp.conclusion = body.conclusion
        exp.conclusion_reason = body.conclusion_reason
        exp.concluded_by = body.concluded_by
        exp.concluded_at = db.now_iso()
        exp.status = "concluded"
        exp.end_ts = db.now_iso()
        _s.commit()
        return {"status": "success", "id": exp.id, "conclusion": exp.conclusion,
                "sample_size_treatment": n}
    finally:
        _s.close()


def _exp_to_dict(r) -> dict:
    import json
    return {
        "id": r.id, "name": r.name, "hypothesis": r.hypothesis,
        "success_metric": r.success_metric,
        "guardrail_metrics": json.loads(r.guardrail_metrics or "[]"),
        "status": r.status, "target_percent": r.target_percent,
        "target_supplier_ids": json.loads(r.target_supplier_ids or "[]"),
        "min_sample": r.min_sample, "start_ts": r.start_ts, "end_ts": r.end_ts,
        "conclusion": r.conclusion, "conclusion_reason": r.conclusion_reason,
        "concluded_by": r.concluded_by, "concluded_at": r.concluded_at,
    }

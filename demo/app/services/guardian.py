# -*- coding: utf-8 -*-
"""实验守护（T9 Gap C3 + D4）：自动回滚 + 方向性约束指标。

职责（只守护、不改实验设计）：
- 巡检 status='running' 的实验，按 guardrail 阈值判定：
    GUARD_SUCCESS_DROP_PP   成功率下降 pp（缺省 5）
    GUARD_COST_RISE_PCT     单张成本涨幅 %（缺省 50）
    GUARD_P95_LATENCY_MS    P95 延迟上限（缺省 20000）
  阈值全部走 settings（T10 机制），改配置无需重启。
- 触发 → 调既有引擎回滚逻辑（rollback_snapshot 还原）+ 冻结实验
  （status='stopped', conclusion='rollback'）+ guardrail_event / 系统审计留痕。
- 样本量不足 experiment.min_sample 不判定（避免小样本误杀）。
- 方向性指标（avg_output_tokens / avg_tool_calls / avg_retry_rounds）
  连续两期同向漂移超阈值 → 仅 alert，不回滚。
"""

import logging
import math
import os
import threading

logger = logging.getLogger(__name__)

_THREAD = None
_THREAD_LOCK = threading.Lock()


# -------------------------------------------------------------
# 阈值（settings 实时读取，缺省 = 计划口径）
# -------------------------------------------------------------
def _thresholds():
    try:
        from app.services import settings_service as ss
        return {
            "success_drop_pp": ss.get_float("guard_success_drop_pp", 5),
            "cost_rise_pct": ss.get_float("guard_cost_rise_pct", 50),
            "p95_latency_ms": ss.get_float("guard_p95_latency_ms", 20000),
            "drift_pct": ss.get_float("guard_directional_drift_pct", 20),
        }
    except Exception:
        return {"success_drop_pp": 5, "cost_rise_pct": 50,
                "p95_latency_ms": 20000, "drift_pct": 20}


def _interval_minutes():
    env = os.environ.get("GUARDIAN_INTERVAL_MINUTES")
    if env:
        try:
            v = int(env)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    try:
        from app.services import settings_service as ss
        return ss.get_int("guard_interval_minutes", 15)
    except Exception:
        return 15


# -------------------------------------------------------------
# 指标计算（口径对齐 supervisor._log_extract_decision 的 extra）
# -------------------------------------------------------------
def _elapsed_total(extra):
    e = extra.get("elapsed_ms")
    if isinstance(e, dict):
        for key in ("total", "elapsed_ms", "ms"):
            v = e.get(key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return None
    try:
        return float(e) if e is not None else None
    except (TypeError, ValueError):
        return None


def _group_metrics(rows):
    """一组决策行 → 守护指标。口径：仅 extract 轮计入成功率/成本/延迟。"""
    extracts = [r for r in rows if r.get("decision_type") == "extract"]
    n = len(extracts)
    out = {
        "sample_size": n,
        "success_rate_pct": None, "avg_cost": None,
        "p95_latency_ms": None, "avg_output_tokens": None,
        "avg_tool_calls": None, "avg_retry_rounds": None,
        "cost_estimated_count": 0, "cost_sample_size": 0,
    }
    if n == 0:
        return out
    extras = [r.get("extra") or {} for r in extracts]

    success = sum(1 for x in extras if bool(x.get("success")))
    out["success_rate_pct"] = round(100.0 * success / n, 4)

    # 成本可信度：extra.cost_estimated=1 的行其 cost_hkd 不是「真实单价 x 真实 token」的测量值
    # （拿不到 token / 成本链路异常 / 走了兜底单价），计成 0 或低估会直接把成本护栏做瞎。
    # 因此平均成本只用非估算行计算；估算行单独计数上报，让「不可判定」显式可见。
    # 全为实测行时（正常路径）该口径与旧实现逐值一致。
    reliable_costs = [float(x.get("cost_hkd") or 0) for x in extras
                      if int(x.get("cost_estimated") or 0) != 1]
    out["cost_estimated_count"] = n - len(reliable_costs)
    out["cost_sample_size"] = len(reliable_costs)
    out["avg_cost"] = round(sum(reliable_costs) / len(reliable_costs), 6) if reliable_costs else None

    lat = sorted(v for v in (_elapsed_total(x) for x in extras) if v is not None)
    if lat:
        idx = max(0, math.ceil(0.95 * len(lat)) - 1)
        out["p95_latency_ms"] = round(lat[idx], 2)

    toks = [float(x.get("tokens_completion") or 0) for x in extras]
    out["avg_output_tokens"] = round(sum(toks) / n, 4)

    tools = [float(x.get("tool_calls") or 0) for x in extras]
    out["avg_tool_calls"] = round(sum(tools) / n, 4)

    per_receipt = {}
    for r in extracts:
        rid = r.get("receipt_id")
        per_receipt[rid] = per_receipt.get(rid, 0) + 1
    rounds = list(per_receipt.values())
    out["avg_retry_rounds"] = round(sum(rounds) / len(rounds), 4) if rounds else None
    return out


# -------------------------------------------------------------
# 既有引擎回滚逻辑（api_admin.rollback_engine_config 同源）
# -------------------------------------------------------------
def perform_engine_rollback(who="guardian"):
    """用最近一次推全快照（rollback_snapshot.prev）覆盖回常规组并清空快照。

    返回 (ok, new_cfg_or_None)。无可回滚快照时返回 (False, None)。
    """
    from app import db
    from app.models import EngineConfig
    cfg = db.get_engine_config()
    snap = cfg.rollback_snapshot
    if not snap or "prev" not in snap:
        return False, None
    prev = snap.get("prev", {})
    old_engine = cfg.recognition_engine
    new_dict = {**cfg.model_dump(), **prev, "rollback_snapshot": None}
    try:
        new_cfg = EngineConfig(**new_dict)
    except Exception as e:
        logger.warning("[guardian] rollback config rebuild failed: %s", e)
        return False, None
    # N4：回滚是把「人工推全」还原，属人工改配的延续 —— 必须显式置 manual，
    # 否则该配置仍是 auto，下次重启会被 hydrate 按 .env 打回（推全/回滚白做）。
    db.set_engine_config(new_cfg, source="manual")
    try:
        db.append_system_audit_log(
            who, "rollback_engine_config", "recognition_engine",
            str(old_engine), str(new_cfg.recognition_engine))
    except Exception as e:
        logger.warning("[guardian] 审计写入失败(action=rollback_engine_config who=%s): %s", who, e)
    return True, new_cfg


# -------------------------------------------------------------
# 方向性漂移：连续两期同向且幅度超阈值 → alert（不回滚）
# -------------------------------------------------------------
def _drift_alerts(exp_id, th_drift_pct):
    from app import db
    snaps = db.list_experiment_snapshots(exp_id, limit=3)
    if len(snaps) < 3:
        return []
    alerts = []
    # 阈值 0 是合法配置（任何漂移都告警），不能用 `or 20` 吞成 20%；
    # 缺省值由 _thresholds() 的 settings 缺省负责，这里只兜 None。
    pct = (20.0 if th_drift_pct is None else float(th_drift_pct)) / 100.0
    for metric in ("avg_output_tokens", "avg_tool_calls", "avg_retry_rounds"):
        vals = [s.get(metric) for s in snaps]
        if any(v is None for v in vals):
            continue
        v0, v1, v2 = (float(v) for v in vals)
        if v0 <= 0 or v1 <= 0:
            continue
        d1 = (v1 - v0) / v0
        d2 = (v2 - v1) / v1
        same_dir = (d1 > 0 and d2 > 0) or (d1 < 0 and d2 < 0)
        if same_dir and abs(d1) > pct and abs(d2) > pct:
            direction = "上升" if d1 > 0 else "下降"
            alerts.append(
                f"{metric} 连续两期同向{direction}"
                f"（{d1*100:.1f}% / {d2*100:.1f}%，阈值 {pct*100:.0f}%）")
    return alerts


# -------------------------------------------------------------
# 主入口
# -------------------------------------------------------------
def check_once():
    """巡检一次全部 running 实验，返回动作列表。

    GuardianAction: {experiment_id, kind: 'rollback'|'alert', reason,
                     metrics_snapshot}
    任何单个实验判定异常只告警不中断其余实验。
    """
    from app import db
    actions = []
    th = _thresholds()
    try:
        exps = [e for e in db.list_experiments() if e.get("status") == "running"]
    except Exception as e:
        logger.warning("[guardian] list experiments failed: %s", e)
        return actions

    for exp in exps:
        exp_id = exp.get("id")
        try:
            rows = db.list_experiment_decision_rows(exp_id)
            ctrl = [r for r in rows if r.get("grp") == "control"]
            treat = [r for r in rows if r.get("grp") == "treatment"]
            c = _group_metrics(ctrl)
            t = _group_metrics(treat)

            # 方向性快照（本期）先落库，再基于最近三期判漂移
            try:
                db.write_experiment_directional_snapshot(
                    exp_id,
                    avg_output_tokens=t["avg_output_tokens"],
                    avg_tool_calls=t["avg_tool_calls"],
                    avg_retry_rounds=t["avg_retry_rounds"],
                    sample_size=t["sample_size"], grp="treatment")
            except Exception as e:
                logger.warning("[guardian] snapshot write failed exp=%s: %s", exp_id, e)

            drift_reasons = []
            try:
                drift_reasons = _drift_alerts(exp_id, th["drift_pct"])
            except Exception as e:
                logger.warning("[guardian] drift check failed exp=%s: %s", exp_id, e)
            for reason in drift_reasons:
                db.write_guardrail_event(exp_id, "alert", reason,
                                         metrics={"treatment": t, "control": c})
                try:
                    db.append_system_audit_log(
                        "guardian", "guardian_alert",
                        f"experiment:{exp_id}", "", reason)
                except Exception as e:
                    logger.warning("[guardian] 审计写入失败(action=guardian_alert exp=%s): %s",
                                   exp_id, e)
                actions.append({"experiment_id": exp_id, "kind": "alert",
                                "reason": reason,
                                "metrics_snapshot": {"treatment": t, "control": c}})

            # 样本量门槛：不足不判（避免小样本误杀）
            # min_sample=0 是合法值（不做门槛），不能用 `or 100` 吞成 100；
            # 归一逻辑与 create_experiment 落库口径共用 db.coerce_int。
            min_sample = db.coerce_int(exp.get("min_sample"), 100)
            if t["sample_size"] < min_sample:
                continue

            # 成本护栏可判定性：估算行的 cost_hkd 不是真实测量值，据此判定涨跌会得出错误结论。
            # 必须显式告警，否则「没有回滚」会被误读成「成本没有上涨」。
            cost_decidable = bool(c["avg_cost"] and c["avg_cost"] > 0
                                  and t["avg_cost"] is not None)
            if not cost_decidable and (c["cost_estimated_count"] or t["cost_estimated_count"]):
                logger.warning(
                    "[guardian] 实验 %s 成本护栏不可判定：估算行 control=%s/%s treatment=%s/%s"
                    "（估算行的 cost_hkd 非真实测量值，不能据此判定涨跌）",
                    exp_id, c["cost_estimated_count"], c["sample_size"],
                    t["cost_estimated_count"], t["sample_size"])

            # 守护判定：任一触发即回滚
            rollback_reasons = []
            if (c["success_rate_pct"] is not None
                    and t["success_rate_pct"] is not None):
                drop = c["success_rate_pct"] - t["success_rate_pct"]
                if drop > th["success_drop_pp"]:
                    rollback_reasons.append(
                        f"success_rate 下降 {drop:.1f}pp"
                        f"（control {c['success_rate_pct']:.1f}% → "
                        f"treatment {t['success_rate_pct']:.1f}%，"
                        f"阈值 {th['success_drop_pp']}pp）")
            if cost_decidable:
                rise = (t["avg_cost"] - c["avg_cost"]) / c["avg_cost"] * 100.0
                if rise > th["cost_rise_pct"]:
                    rollback_reasons.append(
                        f"单张成本上涨 {rise:.1f}%"
                        f"（阈值 {th['cost_rise_pct']}%）")
            if (t["p95_latency_ms"] is not None
                    and t["p95_latency_ms"] > th["p95_latency_ms"]):
                rollback_reasons.append(
                    f"P95 延迟 {t['p95_latency_ms']:.0f}ms 超上限"
                    f" {th['p95_latency_ms']:.0f}ms")

            if not rollback_reasons:
                continue

            reason = "；".join(rollback_reasons)
            metrics = {"treatment": t, "control": c}
            # 1) 引擎配置回滚（既有逻辑：快照还原）
            rolled, _ = perform_engine_rollback(who="guardian")
            # 2) 冻结实验
            db.freeze_experiment_rollback(exp_id, reason=reason, by="guardian")
            # 3) 留痕
            db.write_guardrail_event(
                exp_id, "rollback", reason,
                metrics={**metrics, "engine_config_rolled_back": bool(rolled)})
            try:
                db.append_system_audit_log(
                    "guardian", "guardian_rollback",
                    f"experiment:{exp_id}", "running", "stopped")
            except Exception as e:
                logger.warning("[guardian] 审计写入失败(action=guardian_rollback exp=%s): %s",
                               exp_id, e)
            logger.warning("[guardian] experiment %s rolled back: %s", exp_id, reason)
            actions.append({"experiment_id": exp_id, "kind": "rollback",
                            "reason": reason, "metrics_snapshot": metrics})
        except Exception as e:
            logger.warning("[guardian] check failed exp=%s: %s", exp_id, e)
    return actions


# -------------------------------------------------------------
# 后台线程（main.py startup 拉起；间隔 settings 可配，缺省 15 分钟）
# -------------------------------------------------------------
def _loop():
    import time
    while True:
        try:
            time.sleep(_interval_minutes() * 60)
            check_once()
        except Exception as e:
            logger.warning("[guardian] loop error: %s", e)


def start_background_thread():
    """幂等启动守护线程（daemon）。服务重启前不重复起。"""
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return _THREAD
        _THREAD = threading.Thread(target=_loop, name="experiment-guardian",
                                   daemon=True)
        _THREAD.start()
        logger.info("[guardian] background thread started (interval=%s min)",
                    _interval_minutes())
        return _THREAD

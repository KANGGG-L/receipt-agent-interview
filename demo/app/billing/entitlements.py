# -*- coding: utf-8 -*-
"""S03 权益判定：AI-only 门、权益快照、request_key 预留/消费/释放。

边界（S03 卡）：
- 只有「新 AI 识别」受限；历史读取、导出、对账、手工录入/转手工一律不经过本门。
- 预留一旦建立，inflight 可跨试用/周期到期继续完成（consume/release 不看当前权益）。
- 同一 request_key 幂等：同 body 回放、异 body 409；并发领取试用由 lifecycle 的
  coupon 原子核销 + subscriptions.tenant_id 唯一约束兜底。
"""

import hashlib
import os

from sqlalchemy import text

from app import db
from app.billing import lifecycle
from app.billing.errors import BillingConflict

AI_DENIED_MSG = (
    "试用已结束或套餐还没生效，暂时不能发起新的 AI 识别。"
    "历史单据、导出、对账和手工录入都还能正常使用；请登记续费后再试。")
NO_SUBSCRIPTION_MSG = (
    "这个商户还没有试用或套餐，暂时不能发起新的 AI 识别。"
    "可以先兑换试用券，或使用手工录入；历史数据不受影响。")


def gate_enabled():
    """新 AI 权益门开关。默认关闭以保持既有演示/离线测试链路不变；生产显式开启。"""
    return os.environ.get("BILLING_ENFORCEMENT_ENABLED", "0").strip().lower() in (
        "1", "true", "yes")


def _session():
    return db.get_session()


def _rowdict(row):
    return {k: row._mapping[k] for k in row._mapping.keys()} if row is not None else None


def evaluate(tenant_id, now=None):
    """纯读取判定（不改状态）：返回 allow_new_ai / basis / 到期时间 / 人话原因。"""
    now = now or lifecycle.utcnow()
    sub = lifecycle.get_subscription(tenant_id)
    if not sub:
        return {"allow_new_ai": False, "basis": "no_subscription", "state": None,
                "trial_ends_at": None, "period_ends_at": None, "policy_ref": None,
                "code": "AI_ENTITLEMENT_REQUIRED", "msg": NO_SUBSCRIPTION_MSG}
    period = sub.get("current_period") or {}
    trial_ends = sub.get("trial_ends_at")
    period_ends = period.get("ends_at")
    state = sub.get("state")
    allow = False
    basis = state or "unknown"
    if state == "trialing" and trial_ends and lifecycle.parse_iso(trial_ends) > now:
        allow, basis = True, "active_trial"
    elif state in ("active", "cancel_scheduled") and period_ends \
            and lifecycle.parse_iso(period_ends) > now:
        allow, basis = True, "verified_period"
    elif state == "past_due":
        basis = "past_due"
    elif state == "trial_expired" or (state == "trialing" and trial_ends):
        basis = "trial_expired"
    elif state == "terminated":
        basis = "terminated"
    return {
        "allow_new_ai": allow, "basis": basis, "state": state,
        "trial_ends_at": trial_ends, "period_ends_at": period_ends,
        "policy_ref": sub.get("policy_ref"),
        "code": None if allow else "AI_ENTITLEMENT_REQUIRED",
        "msg": None if allow else (AI_DENIED_MSG if state else NO_SUBSCRIPTION_MSG),
    }


def allow_new_ai(tenant_id, now=None):
    return bool(evaluate(tenant_id, now)["allow_new_ai"])


def snapshot(tenant_id, allow, basis, policy_ref=""):
    s = _session()
    try:
        s.execute(text(
            "INSERT INTO entitlement_snapshots (tenant_id, allow_new_ai, basis, policy_ref,"
            " created_at) VALUES (:t,:a,:b,:p,:now)"),
            {"t": tenant_id, "a": 1 if allow else 0, "b": basis, "p": policy_ref or "",
             "now": lifecycle.to_iso(lifecycle.utcnow())})
        s.commit()
    finally:
        s.close()


def _request_hash(request_key, request_hash=""):
    if request_hash:
        return str(request_hash)
    return hashlib.sha256(str(request_key).encode()).hexdigest()


def authorize_new_ai(tenant_id, request_key="", request_hash=""):
    """AI 新识别准入 + 预留。

    - request_key 非空：已存在同 key 预留 → 回放（即使订阅已到期也允许完成 inflight）；
      异 body → 409。
    - 当前无权益 → allow=False（不建预留）。
    """
    decision = evaluate(tenant_id)
    key = str(request_key or "").strip()
    if not key:
        return decision
    rh = _request_hash(key, request_hash)
    s = _session()
    try:
        prior = s.execute(text(
            "SELECT * FROM entitlement_reservations WHERE tenant_id=:t AND request_key=:k"),
            {"t": tenant_id, "k": key}).fetchone()
        if prior:
            prior = _rowdict(prior)
            if prior.get("request_hash") and prior["request_hash"] != rh:
                raise BillingConflict(
                    "IDEMPOTENCY_REUSED",
                    "同一个操作编号已经用于另一张图片。请刷新页面后重新上传。")
            if prior.get("state") == "released":
                # 已释放（未发起 AI，如转手工）：重试按当前权益重新判定，不享受 inflight。
                if not decision["allow_new_ai"]:
                    return decision
                s.execute(text(
                    "UPDATE entitlement_reservations SET state='reserved', basis=:b,"
                    " updated_at=:now WHERE id=:i"),
                    {"b": decision["basis"],
                     "now": lifecycle.to_iso(lifecycle.utcnow()), "i": prior["id"]})
                s.commit()
                return {"allow_new_ai": True, "basis": decision["basis"],
                        "reservation_id": prior["id"], "state": "reserved",
                        "inflight": False, "code": None, "msg": None}
            return {"allow_new_ai": True, "basis": "reservation_replay",
                    "reservation_id": prior["id"], "state": prior.get("state"),
                    "inflight": True, "code": None, "msg": None}
        if not decision["allow_new_ai"]:
            return decision
        now = lifecycle.to_iso(lifecycle.utcnow())
        s.execute(text(
            "INSERT INTO entitlement_reservations (tenant_id, request_key, request_hash,"
            " state, basis, subscription_state, policy_ref, created_at, updated_at)"
            " VALUES (:t,:k,:h,'reserved',:b,:st,:p,:now,:now)"),
            {"t": tenant_id, "k": key, "h": rh, "b": decision["basis"],
             "st": decision.get("state") or "", "p": decision.get("policy_ref") or "",
             "now": now})
        rid = int(s.execute(text(
            "SELECT id FROM entitlement_reservations WHERE tenant_id=:t AND request_key=:k"),
            {"t": tenant_id, "k": key}).scalar())
        # 必须复用同一个 session/事务写快照：另开 session 会与未提交的 reservation
        # 争 SQLite 写锁（database is locked）。
        s.execute(text(
            "INSERT INTO entitlement_snapshots (tenant_id, allow_new_ai, basis, policy_ref,"
            " created_at) VALUES (:t,1,:b,:p,:now)"),
            {"t": tenant_id, "b": decision["basis"],
             "p": decision.get("policy_ref") or "", "now": now})
        s.commit()
        return {"allow_new_ai": True, "basis": decision["basis"], "reservation_id": rid,
                "state": "reserved", "inflight": True, "code": None, "msg": None}
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def consume(tenant_id, request_key):
    """预留 → 已消费（新 AI 任务已受理）。不看当前权益：inflight 跨到期可完成。"""
    return _transition(tenant_id, request_key, from_state="reserved", to_state="consumed")


def release(tenant_id, request_key):
    """预留 → 释放（任务未受理/转手工）。幂等；已消费不回收（AI 已发生）。"""
    return _transition(tenant_id, request_key, from_state="reserved", to_state="released")


def _transition(tenant_id, request_key, from_state, to_state):
    key = str(request_key or "").strip()
    if not key:
        return False
    s = _session()
    try:
        res = s.execute(text(
            "UPDATE entitlement_reservations SET state=:to, updated_at=:now"
            " WHERE tenant_id=:t AND request_key=:k AND state=:frm"),
            {"to": to_state, "now": lifecycle.to_iso(lifecycle.utcnow()),
             "t": tenant_id, "k": key, "frm": from_state})
        s.commit()
        return res.rowcount == 1
    finally:
        s.close()


def get_reservation(tenant_id, request_key):
    s = _session()
    try:
        return _rowdict(s.execute(text(
            "SELECT * FROM entitlement_reservations WHERE tenant_id=:t AND request_key=:k"),
            {"t": tenant_id, "k": str(request_key)}).fetchone())
    finally:
        s.close()

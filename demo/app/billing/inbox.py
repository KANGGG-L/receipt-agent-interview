# -*- coding: utf-8 -*-
"""S05 可靠回调收验：原 body HMAC 验签、时间窗、merchant/env/金额绑定、持久 Inbox。

顺序不可交换：验签 → 业务绑定校验 → 写 provider_inbox（唯一 event_id）→ 提交 → 才 ack。
写库失败必须抛错让渠道重投（绝不 ack 成功）；重复 event_id 回放同 body 视为已收，
不同 body 视为冲突。normalize 由 worker/端点随后执行，billing_events 语义去重。
"""

import hashlib
import hmac
import json
import os
import time

from sqlalchemy import text

from app import db
from app.billing import lifecycle, reconcile
from app.billing.errors import (
    BillingConflict, BillingNotFound, SecurityReject,
)

TOLERANCE_SECONDS = 300


def _now_epoch():
    return time.time()


def secret():
    raw = os.environ.get("BILLING_WEBHOOK_SECRET", "").strip()
    if not raw:
        # fail-closed：没有共享密钥就无法验证来源，不能把回调当成事实。
        raise SecurityReject("WEBHOOK_NOT_CONFIGURED",
                             "支付回调验签密钥未配置，已拒绝接收，请联系平台管理员。")
    return raw


def sign(raw_body, timestamp, secret_value=None):
    """HMAC-SHA256 over "<timestamp>.<raw body>"（十六进制小写）。"""
    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")
    key = (secret_value if secret_value is not None else secret()).encode("utf-8")
    msg = str(timestamp).encode("utf-8") + b"." + bytes(raw_body)
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_signature(raw_body, headers, now_epoch=None):
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    timestamp = headers.get("x-billing-timestamp") or headers.get("x-webhook-timestamp") or ""
    signature = headers.get("x-billing-signature") or headers.get("x-webhook-signature") or ""
    if not timestamp or not signature:
        raise SecurityReject("WEBHOOK_SIGNATURE_MISSING", "支付回调缺少签名信息，已拒绝。")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise SecurityReject("WEBHOOK_SIGNATURE_INVALID", "支付回调签名时间格式不对，已拒绝。")
    now = int(now_epoch if now_epoch is not None else _now_epoch())
    if abs(now - ts) > TOLERANCE_SECONDS:
        raise SecurityReject("WEBHOOK_REPLAY_WINDOW",
                             "支付回调已超过允许的时间窗，可能是重放，已拒绝。")
    expected = sign(raw_body, timestamp)
    provided = str(signature).strip()
    if provided.startswith("sha256="):
        provided = provided[7:]
    if not hmac.compare_digest(expected, provided):
        raise SecurityReject("WEBHOOK_SIGNATURE_INVALID", "支付回调签名校验不通过，已拒绝。")
    return ts


def _session():
    return db.get_session()


def _rowdict(row):
    return {k: row._mapping[k] for k in row._mapping.keys()} if row is not None else None


def _require(payload, key, code="WEBHOOK_PAYLOAD_INVALID"):
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise SecurityReject(code, "支付回调缺少必要字段，已拒绝。")
    return value


def _insert_inbox(s, row):
    """独立出来便于测试模拟数据库故障（DB 失败不得 ack 成功）。"""
    return s.execute(text(
        "INSERT OR IGNORE INTO provider_inbox (provider, environment, event_id,"
        " payload_digest, payload_json, fact_kind, order_no, provider_payment_id,"
        " received_at, processed_status)"
        " VALUES (:pv,:env,:e,:d,:p,:fk,:o,:pp,:now,'pending')"), row).rowcount == 1


def receive(provider, raw_body, headers, expected_environment=None):
    """接收并持久化回调（不在此做事实归约）。返回 accepted/duplicate。"""
    provider = str(provider or "").strip().lower()
    if not provider:
        raise SecurityReject("WEBHOOK_PROVIDER_INVALID", "支付回调来源不明确，已拒绝。")
    # 渠道白名单：真实渠道未接入（vendor_a 未授权），只接受 fake（测试/演示）。
    if provider != "fake":
        raise SecurityReject(
            "PROVIDER_NOT_AVAILABLE",
            "这个支付渠道还没接入，不能接收它的回调。请使用人工登记收款。")
    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")

    verify_signature(raw_body, headers)
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SecurityReject("WEBHOOK_PAYLOAD_INVALID", "支付回调内容不是合法 JSON，已拒绝。")
    if not isinstance(payload, dict):
        raise SecurityReject("WEBHOOK_PAYLOAD_INVALID", "支付回调内容格式不对，已拒绝。")

    event_id = str(_require(payload, "event_id"))
    merchant_id = str(_require(payload, "merchant_id"))
    environment = str(_require(payload, "environment")).lower()
    fact_kind = str(_require(payload, "fact_kind"))
    order_no = str(_require(payload, "order_no"))
    object_version = int(payload.get("object_version") or 1)

    expected_env = str(expected_environment
                       or os.environ.get("BILLING_ENVIRONMENT", "")).strip().lower()
    if not expected_env:
        raise SecurityReject("WEBHOOK_ENV_NOT_CONFIGURED",
                             "支付回调环境未配置，无法核对来源，已拒绝。")
    if environment != expected_env:
        raise SecurityReject("WEBHOOK_ENV_MISMATCH",
                             "支付回调环境与当前部署不一致，已拒绝。")

    s = _session()
    try:
        order = s.execute(text("SELECT * FROM billing_orders WHERE order_no=:o"),
                          {"o": order_no}).fetchone()
        if not order:
            raise BillingNotFound("ORDER_NOT_FOUND", "支付回调对应的订单不存在，已拒绝。")
        order = _rowdict(order)
        # merchant 绑定：回调声明的商户必须等于订单租户，防跨商户伪造。
        if merchant_id != str(order["tenant_id"]):
            raise SecurityReject("WEBHOOK_MERCHANT_MISMATCH",
                                 "支付回调商户与订单归属不一致，已拒绝。")

        if fact_kind == reconcile.FACT_SUCCESS:
            provider_payment_id = str(_require(payload, "provider_payment_id"))
            amount = int(_require(payload, "amount_minor"))
            currency = str(_require(payload, "currency")).upper()
            if amount != int(order["amount_minor"]) or \
                    currency != str(order["currency"]).upper():
                raise SecurityReject(
                    "WEBHOOK_AMOUNT_MISMATCH",
                    "支付回调金额或币种与订单不一致，已拒绝入账，请联系平台核对。")
        elif fact_kind == reconcile.FACT_REFUND:
            provider_payment_id = str(_require(payload, "provider_refund_id"))
            amount = int(payload.get("amount_minor") or 0)
            currency = str(payload.get("currency") or order["currency"]).upper()
            if amount <= 0 or amount > int(order["amount_minor"]):
                raise SecurityReject(
                    "WEBHOOK_AMOUNT_MISMATCH",
                    "退款回调金额不合法或超过已付金额，已拒绝。")
        elif fact_kind == reconcile.FACT_FAILED:
            provider_payment_id = str(payload.get("provider_payment_id")
                                      or "failed-%s" % event_id)
            amount = payload.get("amount_minor")
            currency = str(payload.get("currency") or order["currency"]).upper()
        else:
            raise SecurityReject("WEBHOOK_FACT_UNKNOWN",
                                 "支付回调类型无法识别，已拒绝。")

        digest = hashlib.sha256(raw_body).hexdigest()
        row = {"pv": provider, "env": environment, "e": event_id, "d": digest,
               "p": raw_body.decode("utf-8", "replace"), "fk": fact_kind,
               "o": order_no, "pp": provider_payment_id,
               "now": lifecycle.to_iso(lifecycle.utcnow())}
        inserted = _insert_inbox(s, row)
        if not inserted:
            existing = s.execute(text(
                "SELECT * FROM provider_inbox WHERE provider=:pv AND environment=:env"
                " AND event_id=:e"), {"pv": provider, "env": environment,
                                      "e": event_id}).fetchone()
            existing = _rowdict(existing) or {}
            if existing.get("payload_digest") and existing["payload_digest"] != digest:
                raise BillingConflict(
                    "WEBHOOK_EVENT_ID_CONFLICT",
                    "同一个回调编号携带了不同内容，已拒绝，请联系平台核对。")
            s.commit()
            return {"status": "duplicate", "event_id": event_id,
                    "inbox_id": existing.get("id"), "processed": True}
        inbox_id = int(s.execute(text(
            "SELECT id FROM provider_inbox WHERE provider=:pv AND environment=:env"
            " AND event_id=:e"), {"pv": provider, "env": environment,
                                  "e": event_id}).scalar())
        # 先提交再 ack：这里抛错就不会返回成功（渠道会重投）。
        s.commit()
        return {"status": "accepted", "event_id": event_id, "inbox_id": inbox_id,
                "object_version": object_version, "processed": False}
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def normalize(inbox_ids=None, limit=100):
    """把已验签 Inbox 归一为 billing_events；失败的保留 pending 待重试。"""
    s = _session()
    try:
        params = {}
        sql = "SELECT * FROM provider_inbox WHERE processed_status='pending'"
        if inbox_ids:
            ids = [int(i) for i in inbox_ids]
            sql += " AND id IN (%s)" % ",".join(":i%d" % n for n in range(len(ids)))
            params.update({"i%d" % n: v for n, v in enumerate(ids)})
        sql += " ORDER BY id LIMIT :lim"
        params["lim"] = int(limit)
        rows = [_rowdict(r) for r in s.execute(text(sql), params).fetchall()]
    finally:
        s.close()

    out = []
    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
            fact = {
                "tenant_id": payload.get("merchant_id"),
                "order_no": payload.get("order_no") or row.get("order_no"),
                "provider_payment_id": (
                    payload.get("provider_refund_id") or payload.get("provider_payment_id")
                    or row.get("provider_payment_id")),
                "fact_kind": payload.get("fact_kind") or row.get("fact_kind"),
                "object_version": int(payload.get("object_version") or 1),
                "amount_minor": payload.get("amount_minor"),
                "currency": payload.get("currency"),
                "event_id": row.get("event_id"),
                "payload_digest": row.get("payload_digest"),
            }
            res = reconcile.apply_fact(fact)
            _mark_processed(row["id"], "processed")
            out.append({"inbox_id": row["id"], "status": "processed",
                        "duplicate": res.get("duplicate")})
        except Exception as exc:  # noqa: BLE001 单条失败不拖垮其余；保持 pending 可重试
            # 绝不标 error/终态：事件仍可能被重投或 worker 重放，标死等于永久丢事实。
            out.append({"inbox_id": row["id"], "status": "pending",
                        "error": str(exc)[:200]})
    return out


def _mark_processed(inbox_id, status):
    s = _session()
    try:
        s.execute(text("UPDATE provider_inbox SET processed_status=:st WHERE id=:i"),
                  {"st": status, "i": int(inbox_id)})
        s.commit()
    finally:
        s.close()

# -*- coding: utf-8 -*-
"""L06 owner 显式信用抵扣（D-03：不自动抵最早采购、不按符号猜）。

Interface（P03 §1 码表 + ADR03）：
- allocate_credit：owner 选目标未付采购与金额（部分/多目标）；ambient/独立事务均可；
  幂等键裁决（同 key 同 hash 返原结果 / 异 hash 409 IDEMPOTENCY_REUSED）；版本 CAS；
  余额校验在事务内（与付款共享 serialized 写——SQLite 单主写天然串行）。
- reverse_allocation：追加 reversal（不删历史），恢复可用余额与目标应付。
错误：CREDIT_INSUFFICIENT(409) / CREDIT_SUPPLIER_MISMATCH(422) / CREDIT_CURRENCY_MISMATCH(422) /
IDEMPOTENCY_REUSED(409) / VERSION_CONFLICT(409)。
"""

import hashlib
import json

from sqlalchemy import text


class CreditError(Exception):
    def __init__(self, code, status, msg, data=None):
        super().__init__(msg)
        self.code = code
        self.status = status
        self.msg = msg
        self.data = data or {}


def _engine():
    from app import db
    if getattr(db, "_engine", None) is None:
        db._make_engine()
    return db._engine


def _request_hash(credit_id, targets, idempotency_key):
    payload = json.dumps({"credit_id": int(credit_id),
                          "targets": sorted((int(t["receipt_id"]), int(t["amount_minor"]))
                                            for t in targets),
                          "key": str(idempotency_key or "")},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _target_outstanding(conn, tenant_id, receipt_id, currency):
    """目标采购单的当前未付额（元 float，付款+信用分配共用口径）。

    全部用同一事务连接读取（付款分配+信用分配），消除并发读窗口。
    v1 币种口径：HKD 单币种（跨币种抵扣在 approve/发行层已拒）。
    """
    row = conn.execute(text(
        "SELECT total_amount, currency FROM receipts"
        " WHERE id=:r AND tenant_id=:t"), {"r": int(receipt_id),
                                           "t": str(tenant_id)}).fetchone()
    if row is None:
        return None, None
    total = float(row[0] or 0.0)
    try:
        paid_rows = conn.execute(text(
            "SELECT COALESCE(SUM(amount),0) FROM payment_allocations"
            " WHERE tenant_id=:t AND receipt_id=:r"), {"t": str(tenant_id),
                                                       "r": int(receipt_id)}).fetchone()
    except Exception:
        paid_rows = [0]  # recon 表未初始化（从未记过付款）：按 0 已付
    credit_rows = conn.execute(text(
        "SELECT COALESCE(SUM(CASE WHEN reversal_of IS NULL THEN amount_minor"
        " ELSE -amount_minor END),0) FROM credit_allocations"
        " WHERE tenant_id=:t AND target_receipt_id=:r"),
        {"t": str(tenant_id), "r": int(receipt_id)}).fetchone()
    # 早期整单付款（legacy_paid 无分配记录）按整单已付（与 recon_store 同口径）
    legacy = conn.execute(text(
        "SELECT payment_id, paid_at FROM receipts WHERE id=:r"),
        {"r": int(receipt_id)}).fetchone()
    paid = float(paid_rows[0] or 0.0) + (int(credit_rows[0] or 0)) / 100.0
    if legacy[0] and legacy[1] and paid == 0.0:
        paid = total  # legacy 整单已付（无分配记录）
    return total - paid, row[1]


def allocate_credit(tenant_id, credit_id, targets, actor="", actor_role="",
                    idempotency_key=None, expected_credit_version=None):
    """owner 显式抵扣。返回 dict(allocation_ids, credit_version, applied)；幂等回放 applied=False。"""
    eng = _engine()
    if not targets or not isinstance(targets, list):
        raise CreditError("VALIDATION_ERROR", 422, "抵扣目标不能为空")
    if any(int(t.get("amount_minor") or 0) <= 0 for t in targets):
        raise CreditError("VALIDATION_ERROR", 422, "每笔抵扣金额必须为正数（分）")
    rh = _request_hash(credit_id, targets, idempotency_key)
    # BEGIN IMMEDIATE：整个抵扣事务（余额读+写）持写锁串行化——deferred 事务的
    # 余额读发生在写锁外，并发抢用会双双通过余额校验成真超额（ADR04 红线）。
    conn = eng.connect()
    conn.execute(text("BEGIN IMMEDIATE"))
    try:
        # 幂等裁决：同 key 同 hash 返原结果；同 key 异 hash 409
        if idempotency_key:
            prior = conn.execute(text(
                "SELECT id, request_hash FROM credit_allocations"
                " WHERE tenant_id=:t AND credit_id=:c AND idempotency_key=:k ORDER BY id LIMIT 1"),
                {"t": str(tenant_id), "c": int(credit_id), "k": str(idempotency_key)}).fetchone()
            if prior:
                if prior[1] == rh:
                    return {"applied": False, "allocation_ids": [int(prior[0])],
                            "message": "幂等回放：该请求已生效，返回原结果"}
                raise CreditError("IDEMPOTENCY_REUSED", 409,
                                  "同一幂等键已被不同内容的请求使用。")
        doc = conn.execute(text(
            "SELECT id, supplier_id, face_minor, currency, state, version FROM credit_documents"
            " WHERE id=:c AND tenant_id=:t"), {"c": int(credit_id),
                                               "t": str(tenant_id)}).fetchone()
        if doc is None or doc[4] != "active":
            raise CreditError("CREDIT_NOT_FOUND", 404, "信用票据不存在或非生效状态。")
        doc_id, supplier_id, face_minor, currency, _state, version = (
            int(doc[0]), int(doc[1]), int(doc[2]), doc[3], doc[4], int(doc[5]))
        if expected_credit_version is not None and int(expected_credit_version) != version:
            raise CreditError("VERSION_CONFLICT", 409,
                              "信用余额已被其他操作更新（当前版本 %d），请刷新后重试。" % version)
        # 可用余额：同一事务连接实时计算（事务外连接看不到未提交的并发分配——
        # 那会把并发抢用全部放行成真超额；SQLite 单主写+本事务读=串行化正确性）。
        alloc_sum = conn.execute(text(
            "SELECT COALESCE(SUM(CASE WHEN reversal_of IS NULL THEN amount_minor"
            " ELSE -amount_minor END),0) FROM credit_allocations"
            " WHERE tenant_id=:t AND credit_id=:c"),
            {"t": str(tenant_id), "c": doc_id}).fetchone()[0]
        refund_sum = conn.execute(text(
            "SELECT COALESCE(SUM(CASE WHEN event_kind IN ('refund_pending','refund_received')"
            " THEN amount_minor ELSE 0 END) - COALESCE(SUM(CASE WHEN event_kind='reversal'"
            " THEN amount_minor ELSE 0 END),0),0) FROM credit_entries"
            " WHERE tenant_id=:t AND credit_id=:c"),
            {"t": str(tenant_id), "c": doc_id}).fetchone()[0]
        available = face_minor - int(alloc_sum or 0) - int(refund_sum or 0)
        total_req = sum(int(t["amount_minor"]) for t in targets)
        if total_req > available:
            raise CreditError("CREDIT_INSUFFICIENT", 409,
                              "可用信用不足：可用 HK$ %.2f，本次要抵 HK$ %.2f。"
                              "请减少抵扣金额。" % (available / 100.0, total_req / 100.0),
                              data={"credit_version": version})
        now_iso = None
        from app import db as _db
        now_iso = _db.now_iso()
        alloc_ids = []
        for t in targets:
            rid = int(t["receipt_id"])
            amt = int(t["amount_minor"])
            outstanding, pay_currency = _target_outstanding(conn, tenant_id, rid, currency)
            if outstanding is None:
                raise CreditError("NOT_FOUND", 404,
                                  "抵扣目标采购单 #%d 不存在。" % rid)
            trow = conn.execute(text(
                "SELECT supplier_id, status FROM receipts WHERE id=:r"),
                {"r": rid}).fetchone()
            if trow[1] != "approved":
                raise CreditError("VALIDATION_ERROR", 422,
                                  "抵扣目标采购单 #%d 尚未入账。" % rid)
            if int(trow[0] or -1) != supplier_id:
                raise CreditError("CREDIT_SUPPLIER_MISMATCH", 422,
                                  "抵扣目标采购单 #%d 不属于该供应商（跨供应商抵扣被拒绝）。" % rid)
            if outstanding * 100 + 1e-6 < amt:
                raise CreditError("CREDIT_INSUFFICIENT", 409,
                                  "采购单 #%d 未付金额不足：未付 HK$ %.2f，本次要抵 HK$ %.2f。"
                                  % (rid, outstanding, amt / 100.0),
                                  data={"credit_version": version})
            res = conn.execute(text(
                "INSERT INTO credit_allocations (tenant_id, credit_id, target_receipt_id,"
                " amount_minor, currency, reversal_of, idempotency_key, request_hash, created_at)"
                " VALUES (:t,:c,:r,:a,:cur,NULL,:k,:h,:ts)"),
                {"t": str(tenant_id), "c": doc_id, "r": rid, "a": amt,
                 "cur": currency, "k": str(idempotency_key or ""), "h": rh, "ts": now_iso})
            alloc_ids.append(int(res.lastrowid))
            conn.execute(text(
                "INSERT INTO credit_entries (tenant_id, credit_id, event_kind, amount_minor,"
                " idempotency_key, created_at) VALUES (:t,:c,'allocated',:a,:k,:ts)"),
                {"t": str(tenant_id), "c": doc_id, "a": amt,
                 "k": str(idempotency_key or ""), "ts": now_iso})
        conn.execute(text(
            "UPDATE credit_documents SET version=version+1 WHERE id=:c AND version=:v"),
            {"c": doc_id, "v": version})
        return {"applied": True, "allocation_ids": alloc_ids, "credit_version": version + 1,
                "allocated_minor": total_req, "available_minor": available - total_req}
    except Exception:
        conn.execute(text("ROLLBACK"))
        raise
    finally:
        try:
            conn.execute(text("COMMIT"))
        except Exception:
            pass
        conn.close()


def reverse_allocation(tenant_id, credit_id, allocation_id, actor="", reason=""):
    """撤销抵扣：追加 reversal（不删历史），恢复可用余额与目标应付；重复撤销不二次反冲。"""
    eng = _engine()
    with eng.begin() as conn:
        alloc = conn.execute(text(
            "SELECT id, amount_minor, reversal_of, target_receipt_id FROM credit_allocations"
            " WHERE id=:a AND tenant_id=:t AND credit_id=:c"),
            {"a": int(allocation_id), "t": str(tenant_id), "c": int(credit_id)}).fetchone()
        if alloc is None:
            raise CreditError("NOT_FOUND", 404, "抵扣记录不存在。")
        dup = conn.execute(text(
            "SELECT id FROM credit_allocations WHERE reversal_of=:a AND tenant_id=:t"),
            {"a": int(allocation_id), "t": str(tenant_id)}).fetchone()
        if dup:
            return {"applied": False, "message": "该抵扣已被撤销（幂等回放）",
                    "reversal_id": int(dup[0])}
        now_iso = None
        from app import db as _db
        now_iso = _db.now_iso()
        res = conn.execute(text(
            "INSERT INTO credit_allocations (tenant_id, credit_id, target_receipt_id,"
            " amount_minor, currency, reversal_of, idempotency_key, created_at)"
            " SELECT tenant_id, credit_id, target_receipt_id, amount_minor, currency,"
            " :a, :k, :ts FROM credit_allocations WHERE id=:aid"),
            {"a": int(allocation_id), "k": "reversal:%d" % int(allocation_id),
             "ts": now_iso, "aid": int(allocation_id)})
        rev_id = int(res.lastrowid)
        conn.execute(text(
            "INSERT INTO credit_entries (tenant_id, credit_id, event_kind, amount_minor,"
            " reverses_event_id, created_at) VALUES (:t,:c,'reversal',:amt,NULL,:ts)"),
            {"t": str(tenant_id), "c": int(credit_id), "amt": int(alloc[1]), "ts": now_iso})
        conn.execute(text(
            "UPDATE credit_documents SET version=version+1 WHERE id=:c"),
            {"c": int(credit_id)})
        return {"applied": True, "reversal_id": rev_id,
                "restored_minor": int(alloc[1])}


# -------------------------------------------------------------
# L07 供应商退款：pending 占用 → 收到/取消/失败（占用语义，ADR03）
# -------------------------------------------------------------
def request_refund(tenant_id, credit_id, amount_minor, actor="", idempotency_key=None):
    """登记退款请求：pending 先占用信用（防重复花费）；同 key 幂等回放。"""
    eng = _engine()
    if int(amount_minor or 0) <= 0:
        raise CreditError("VALIDATION_ERROR", 422, "退款金额必须为正数（分）")
    rh = hashlib.sha256(json.dumps(
        {"op": "refund_request", "credit_id": int(credit_id),
         "amount_minor": int(amount_minor), "key": str(idempotency_key or "")},
        sort_keys=True).encode()).hexdigest()
    conn = eng.connect()
    conn.execute(text("BEGIN IMMEDIATE"))
    try:
        if idempotency_key:
            prior = conn.execute(text(
                "SELECT id, state FROM supplier_refunds WHERE tenant_id=:t AND credit_id=:c"
                " AND idempotency_key=:k ORDER BY id LIMIT 1"),
                {"t": str(tenant_id), "c": int(credit_id),
                 "k": str(idempotency_key)}).fetchone()
            if prior:
                return {"applied": False, "refund_id": int(prior[0]),
                        "state": prior[1], "message": "幂等回放：该退款请求已存在"}
        doc = conn.execute(text(
            "SELECT supplier_id, face_minor, state, version FROM credit_documents"
            " WHERE id=:c AND tenant_id=:t"), {"c": int(credit_id),
                                               "t": str(tenant_id)}).fetchone()
        if doc is None or doc[2] != "active":
            raise CreditError("CREDIT_NOT_FOUND", 404, "信用票据不存在或非生效状态。")
        alloc_sum = conn.execute(text(
            "SELECT COALESCE(SUM(CASE WHEN reversal_of IS NULL THEN amount_minor"
            " ELSE -amount_minor END),0) FROM credit_allocations"
            " WHERE tenant_id=:t AND credit_id=:c"),
            {"t": str(tenant_id), "c": int(credit_id)}).fetchone()[0]
        refund_sum = conn.execute(text(
            "SELECT COALESCE(SUM(CASE WHEN event_kind IN ('refund_pending','refund_received')"
            " THEN amount_minor ELSE 0 END) - COALESCE(SUM(CASE WHEN event_kind='reversal'"
            " THEN amount_minor ELSE 0 END),0),0) FROM credit_entries"
            " WHERE tenant_id=:t AND credit_id=:c"),
            {"t": str(tenant_id), "c": int(credit_id)}).fetchone()[0]
        available = int(doc[1]) - int(alloc_sum or 0) - int(refund_sum or 0)
        if int(amount_minor) > available:
            raise CreditError("CREDIT_INSUFFICIENT", 409,
                              "可用信用不足：可用 HK$ %.2f，本次要登记退款 HK$ %.2f。"
                              % (available / 100.0, amount_minor / 100.0),
                              data={"credit_version": int(doc[3])})
        from app import db as _db
        now_iso = _db.now_iso()
        res = conn.execute(text(
            "INSERT INTO supplier_refunds (tenant_id, credit_id, state, version,"
            " idempotency_key, request_hash, created_at)"
            " VALUES (:t,:c,'awaiting_receipt',1,:k,:h,:ts)"),
            {"t": str(tenant_id), "c": int(credit_id), "k": str(idempotency_key or ""),
             "h": rh, "ts": now_iso})
        refund_id = int(res.lastrowid)
        conn.execute(text(
            "INSERT INTO credit_entries (tenant_id, credit_id, event_kind, amount_minor,"
            " idempotency_key, created_at) VALUES (:t,:c,'refund_pending',:a,:k,:ts)"),
            {"t": str(tenant_id), "c": int(credit_id), "a": int(amount_minor),
             "k": str(idempotency_key or ""), "ts": now_iso})
        conn.execute(text("UPDATE credit_documents SET version=version+1 WHERE id=:c"),
                     {"c": int(credit_id)})
        return {"applied": True, "refund_id": refund_id, "state": "awaiting_receipt",
                "occupied_minor": int(amount_minor), "available_minor": available - int(amount_minor)}
    except Exception:
        conn.execute(text("ROLLBACK"))
        raise
    finally:
        try:
            conn.execute(text("COMMIT"))
        except Exception:
            pass
        conn.close()


def settle_refund(tenant_id, credit_id, refund_id, action, actor="", evidence=""):
    """退款终结：received（登记收到证据，占用转已收）/ cancelled|failed（释放占用）。"""
    eng = _engine()
    if action not in ("receive", "cancel", "fail"):
        raise CreditError("VALIDATION_ERROR", 422, "退款动作只能是 receive/cancel/fail")
    conn = eng.connect()
    conn.execute(text("BEGIN IMMEDIATE"))
    try:
        row = conn.execute(text(
            "SELECT id, state FROM supplier_refunds WHERE id=:r AND tenant_id=:t"
            " AND credit_id=:c"), {"r": int(refund_id), "t": str(tenant_id),
                                   "c": int(credit_id)}).fetchone()
        if row is None:
            raise CreditError("NOT_FOUND", 404, "退款请求不存在。")
        state = row[1]
        if state not in ("awaiting_receipt",):
            return {"applied": False, "message": "该退款已终结（%s），幂等回放" % state,
                    "state": state}
        pend = conn.execute(text(
            "SELECT COALESCE(SUM(amount_minor),0) FROM credit_entries"
            " WHERE tenant_id=:t AND credit_id=:c AND event_kind='refund_pending'"),
            {"t": str(tenant_id), "c": int(credit_id)}).fetchone()[0]
        from app import db as _db
        now_iso = _db.now_iso()
        if action == "receive":
            new_state = "received"
            if not str(evidence or "").strip():
                raise CreditError("REFUND_NOT_RECEIVED", 422,
                                  "登记收到退款必须附收到证据（凭证号/到账记录）。未收退款不得记已收。")
            # pending 先 reversal（占用清除），再入 refund_received（已收持续占用面额）——
            # 否则 pending+received 双重占用（测试抓出）。
            conn.execute(text(
                "INSERT INTO credit_entries (tenant_id, credit_id, event_kind, amount_minor,"
                " created_at) VALUES (:t,:c,'reversal',:a,:ts)"),
                {"t": str(tenant_id), "c": int(credit_id), "a": int(pend), "ts": now_iso})
            conn.execute(text(
                "INSERT INTO credit_entries (tenant_id, credit_id, event_kind, amount_minor,"
                " created_at) VALUES (:t,:c,'refund_received',:a,:ts)"),
                {"t": str(tenant_id), "c": int(credit_id), "a": int(pend), "ts": now_iso})
        else:
            new_state = "cancelled" if action == "cancel" else "failed"
            # 释放占用：追加 reversal（不删历史 pending 事实）
            conn.execute(text(
                "INSERT INTO credit_entries (tenant_id, credit_id, event_kind, amount_minor,"
                " created_at) VALUES (:t,:c,'reversal',:a,:ts)"),
                {"t": str(tenant_id), "c": int(credit_id), "a": int(pend), "ts": now_iso})
        conn.execute(text(
            "UPDATE supplier_refunds SET state=:s, version=version+1, received_evidence=:e"
            " WHERE id=:r"), {"s": new_state, "e": str(evidence or ""), "r": int(refund_id)})
        conn.execute(text("UPDATE credit_documents SET version=version+1 WHERE id=:c"),
                     {"c": int(credit_id)})
        return {"applied": True, "refund_id": int(refund_id), "state": new_state,
                "released_minor": (int(pend) if action != "receive" else 0)}
    except Exception:
        conn.execute(text("ROLLBACK"))
        raise
    finally:
        try:
            conn.execute(text("COMMIT"))
        except Exception:
            pass
        conn.close()

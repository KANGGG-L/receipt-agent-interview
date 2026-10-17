# -*- coding: utf-8 -*-
"""餐品消耗的补充存储：售出时的售价快照、提交请求的去重记录。

表在首次使用时建（CREATE TABLE IF NOT EXISTS），租户列必备。

UAT018 open risk (not closed):
- Extreme *finite* BOM consumption_qty upper-limit is NOT adjudicated yet.
- Do NOT claim a B2-6-mandated BOM qty cap here; do NOT silent-clamp large finite qty.
- NaN / Infinity / negative / 0 writes are rejected in api_dishes._validate_recipe_payload.
- Unpurchased theory cost uses disclosed provisional estimate markers (theory_cost_source),
  not a silent definite 0-cost / 100% margin.
"""

import json
import threading

from sqlalchemy import text

from app import db
from app.store_util import Gate

# UAT018 — extreme finite BOM qty ceiling adjudication status (product risk ledger).
# Source: none claimed (explicitly not B2-6). Status remains open until PM decides.
BOM_QTY_UPPER_LIMIT_STATUS = "open_risk_not_adjudicated"
BOM_QTY_UPPER_LIMIT_SOURCE = None  # do not invent / claim historical B2-6 cap
BOM_QTY_UPPER_LIMIT_NOTE = (
    "Finite extreme BOM qty (e.g. 999999999999) may still persist until an upper "
    "limit is adjudicated; rejects apply only to non-finite / <=0 values."
)


_DDL = [
    """CREATE TABLE IF NOT EXISTS consumption_snapshots (
        consumption_id INTEGER NOT NULL,
        tenant_id VARCHAR(64) NOT NULL,
        dish_price FLOAT NOT NULL,
        bom_version INTEGER DEFAULT 1,
        created_at VARCHAR(32) DEFAULT '',
        PRIMARY KEY (consumption_id, tenant_id))""",
    """CREATE TABLE IF NOT EXISTS consumption_requests (
        tenant_id VARCHAR(64) NOT NULL,
        request_id VARCHAR(80) NOT NULL,
        response_json TEXT NOT NULL,
        created_at VARCHAR(32) DEFAULT '',
        PRIMARY KEY (tenant_id, request_id))""",
]

_gate = Gate(_DDL, ("consumption_snapshots", "consumption_requests"))


def _engine():
    return _gate


def _tid(t):
    return str(t or "default").strip() or "default"


def save_snapshot(tenant_id, consumption_id, dish_price, bom_version):
    with _engine().begin() as conn:
        conn.execute(text("DELETE FROM consumption_snapshots WHERE consumption_id=:c AND tenant_id=:t"),
                     {"c": int(consumption_id), "t": _tid(tenant_id)})
        conn.execute(text(
            "INSERT INTO consumption_snapshots (consumption_id, tenant_id, dish_price, bom_version, created_at) "
            "VALUES (:c,:t,:p,:v,:d)"),
            {"c": int(consumption_id), "t": _tid(tenant_id), "p": float(dish_price),
             "v": int(bom_version or 1), "d": db.now_iso()})


def snapshot_prices(tenant_id):
    """{消耗记录 id: 售出时的售价}。没有快照的历史记录不在里面，调用方回退当前售价。"""
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT consumption_id, dish_price FROM consumption_snapshots WHERE tenant_id=:t"),
            {"t": _tid(tenant_id)}).fetchall()
    return {int(r[0]): float(r[1]) for r in rows}


def get_request(tenant_id, request_id):
    with _engine().connect() as conn:
        r = conn.execute(text(
            "SELECT response_json FROM consumption_requests WHERE tenant_id=:t AND request_id=:r"),
            {"t": _tid(tenant_id), "r": request_id}).fetchone()
    return json.loads(r[0]) if r else None


def save_request(tenant_id, request_id, response):
    with _engine().begin() as conn:
        conn.execute(text("DELETE FROM consumption_requests WHERE tenant_id=:t AND request_id=:r"),
                     {"t": _tid(tenant_id), "r": request_id})
        conn.execute(text(
            "INSERT INTO consumption_requests (tenant_id, request_id, response_json, created_at) "
            "VALUES (:t,:r,:j,:d)"),
            {"t": _tid(tenant_id), "r": request_id, "j": json.dumps(response, ensure_ascii=False),
             "d": db.now_iso()})

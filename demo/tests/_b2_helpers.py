# -*- coding: utf-8 -*-
"""B2 测试共用：按租户隔离造数据（每个用例一个新租户，互不污染）。"""

import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db

_counter = itertools.count(1)


def new_tenant():
    return "b2t%d" % next(_counter)


def hdr(tenant, role="owner"):
    return {"X-Role": role, "X-Tenant-Id": tenant}


def item(name, qty, unit, price, **kw):
    d = {"name": name, "quantity": qty, "unit": unit, "unit_price": price,
         "amount": round(qty * price, 2)}
    d.update(kw)
    return d


def make_receipt(client, tenant, items, total, supplier="测试供应商", date="2026-10-05",
                 settlement="credit", approve=True, department_id=None, currency="HKD",
                 supplier_id=None, **fees):
    body = {"supplier_name": supplier, "date": date, "total_amount": total,
            "settlement_type": settlement, "items": items, "version": None,
            "source": "manual", "currency": currency, "department_id": department_id, **fees}
    r = client.post("/api/save_edited", headers=hdr(tenant, "staff"), json=body)
    assert r.status_code == 200 and r.json().get("status") == "success", r.text
    rid = r.json().get("receipt_id") or r.json().get("id")
    if supplier_id is not None:
        db.update_receipt(rid, supplier_id=supplier_id)
    if not approve:
        return rid, None
    row = db.get_receipt_row(rid)
    ap = client.post("/api/receipt/%d/approve" % rid, headers=hdr(tenant), json={"version": row.version})
    return rid, ap


def make_supplier(tenant, name, terms=None):
    sid, err = db.create_supplier(name, tenant_id=tenant)
    assert sid, err
    if terms is not None:
        db.update_supplier(sid, payment_terms_days=terms)
    return sid

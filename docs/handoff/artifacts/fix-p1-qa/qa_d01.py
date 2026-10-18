# -*- coding: utf-8 -*-
"""FIX-P1 独立 QA 独立复现脚本：对隔离实例 (127.0.0.1:15151, AUTH_ENABLED=1)
用真实 API + 真实 Chrome 验证三条 P1 不再复现。只读验证，不改产品代码。"""
import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:15151"
OUT = "/tmp/fix-p1-qa/evidence"
os.makedirs(OUT, exist_ok=True)
results = {}


def req(method, path, payload=None, token=None, raw=False):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "x-requested-with": "fetch"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# ---- login ----
st, body = req("POST", "/api/auth/login", {"email": "boss@demo.hk", "password": "boss123"})
tok = json.loads(body).get("token")
assert tok, body
print("LOGIN", st)

# ---- build a SKU with stock ----
st, body = req("POST", "/api/inventory/skus", {"name": "QAD01-食材", "base_unit": "斤"}, tok)
d = json.loads(body)
sid = d.get("id") or (d.get("data") or {}).get("id")
print("SKU", st, sid)
st, body = req("POST", "/api/inventory/%s/stocktake" % sid, {"actual_qty": 10.0}, tok)
print("STOCKTAKE-NORMAL", st)

# ---- D-01: 1e308 rejected ----
st, body = req("POST", "/api/inventory/%s/stocktake" % sid, {"actual_qty": 1e308}, tok)
results["d01_stocktake_1e308"] = {"status": st, "body": body[:300]}
st2, body2 = req("POST", "/api/inventory/%s/consume" % sid, {"quantity": 1e308}, tok)
results["d01_consume_1e308"] = {"status": st2, "body": body2[:300]}
st3, body3 = req("POST", "/api/inventory/%s/waste" % sid, {"quantity": 1e308}, tok)
results["d01_waste_1e308"] = {"status": st3, "body": body3[:300]}
st4, body4 = req("GET", "/api/inventory/logs?sku_id=%s" % sid, token=tok)
ok_json = ("Infinity" not in body4 and "NaN" not in body4)
results["d01_logs_after_attempts"] = {"status": st4, "json_compliant": ok_json}

# ---- D-01b: poison legacy row directly then logs ----
sys.path.insert(0, "/home/agentuser/receipt-agent-interview/demo")
os.environ["DB_PATH"] = "/tmp/fix-p1-qa/live.db"
from app import db  # noqa
from app.maintenance import sanitize_stock_logs  # noqa
s = db.get_session()
s.add(db._StockLogRow(sku_id=sid, name="poison", qty=float("inf"), unit="斤", amount=float("nan"),
                      vendor="", date=db.now_iso()[:10], receipt_id=None, kind="stocktake",
                      note="qa poison legacy", created_at=db.now_iso(), tenant_id="default"))
s.commit(); s.close()
st5, body5 = req("GET", "/api/inventory/logs?sku_id=%s" % sid, token=tok)
results["d01_logs_with_legacy_poison"] = {"status": st5, "json_compliant": ("Infinity" not in body5 and "NaN" not in body5)}
rep_dry = sanitize_stock_logs(dry_run=True)
rep_run = sanitize_stock_logs(dry_run=False)
rep_idem = sanitize_stock_logs(dry_run=False)
st6, body6 = req("GET", "/api/inventory/logs?sku_id=%s" % sid, token=tok)
results["d01_sanitize"] = {"dry_affected": rep_dry["affected"], "run_cleaned": rep_run["cleaned"],
                           "backup_file": rep_run.get("backup_file"), "idem_cleaned": rep_idem["cleaned"],
                           "logs_after": st6, "json_compliant": ("Infinity" not in body6 and "NaN" not in body6)}

# ---- save_edited 1e308 path (original defect variant) ----
st7, body7 = req("POST", "/api/save_edited", {"supplier_name": "QA", "items": [{"name": "x", "quantity": 1e308, "unit_price": 1.0, "amount": 1e308}]}, tok)
results["d01_save_edited_1e308"] = {"status": st7, "body": body7[:300]}

with open(os.path.join(OUT, "d01_api.json"), "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(json.dumps(results, ensure_ascii=False, indent=2))

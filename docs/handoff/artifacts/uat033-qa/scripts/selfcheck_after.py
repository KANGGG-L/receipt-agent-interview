# -*- coding: utf-8 -*-
"""UAT-033 QA: after FX (AC2 API) + new-approve 422 (AC4) + cleanup (AC3)."""
import json, os, sys, urllib.request
from sqlalchemy import text

BASE = os.environ.get("QA_BASE", "http://127.0.0.1:15165")
TN = "uat033qa"
TN_B = "uat033qab"
OUT = os.environ["QA_LOG"]
sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
from app import db, units_store

def req(method, path, headers=None, body=None, role="owner", tenant=TN):
    data = None
    hdrs = {"X-Role": role, "X-Tenant-Id": tenant, "Content-Type": "application/json"}
    if headers: hdrs.update(headers)
    if body is not None: data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try: return e.code, json.loads(raw) if raw else {}
        except Exception: return e.code, {"raw": raw}

# AC2 API: add FX
st, _ = req("PUT", "/api/settings/fx_rates",
            body={"rates": [{"currency": "NFX", "rate": 2.0, "confirmed": True}]})
assert st == 200, _
st, rep = req("GET", "/api/cost_report?month=2026-10")
d = rep["data"]
assert d["month_total"] == 220.0, d
assert d["unconverted_count"] == 0
assert d["missing_fx_count"] == 0
assert not d.get("unconverted")
assert (d.get("unconverted_warning") or "") == ""
with open(os.path.join(OUT, "api-after-fx.json"), "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print("API_AFTER_FX_OK", d["month_total"])

# Write ready flag so UI after can run before we clean
open(os.path.join(OUT, "ready-after-fx-flag.txt"), "w").write("READY\n")
print("READY_AFTER_FX")

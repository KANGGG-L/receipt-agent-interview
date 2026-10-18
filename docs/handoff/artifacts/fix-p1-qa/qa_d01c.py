# -*- coding: utf-8 -*-
import json
import urllib.request

BASE = "http://127.0.0.1:15151"


def req(method, path, payload=None, token=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "x-requested-with": "fetch"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


st, body = req("POST", "/api/auth/login", {"email": "boss@demo.hk", "password": "boss123"})
tok = json.loads(body)["token"]

# item qty 1e308 only, normal total
p = {"supplier_name": "QA-D01C", "total_amount": 10.0, "settlement_type": "cash",
     "items": [{"name": "x", "quantity": 1e308, "unit_price": 1.0, "amount": 10.0}]}
st, body = req("POST", "/api/save_edited", p, tok)
print("item_qty_1e308:", st, body[:300])

# actual_qty 1e308 (the UAT-008 field)
p2 = {"supplier_name": "QA-D01D", "total_amount": 10.0, "settlement_type": "cash",
      "items": [{"name": "x", "quantity": 10.0, "actual_qty": 1e308, "unit_price": 1.0, "amount": 10.0}]}
st2, body2 = req("POST", "/api/save_edited", p2, tok)
print("actual_qty_1e308:", st2, body2[:300])

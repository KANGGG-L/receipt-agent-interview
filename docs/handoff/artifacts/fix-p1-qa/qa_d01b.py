# -*- coding: utf-8 -*-
"""D-01b: save_edited 1e308 variant — with settlement_type to reach item validation."""
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

# with settlement_type so we get past SETTLEMENT_REQUIRED gate
payload = {
    "supplier_name": "QA-D01B",
    "total_amount": 1e308,
    "settlement_type": "cash",
    "items": [{"name": "x", "quantity": 1e308, "unit_price": 1.0, "amount": 1e308}],
}
st, body = req("POST", "/api/save_edited", payload, tok)
print("save_edited_1e308_with_settlement:", st)
print(body[:400])

# normal value control
payload2 = {
    "supplier_name": "QA-D01B-CTRL",
    "total_amount": 10.0,
    "settlement_type": "cash",
    "items": [{"name": "x", "quantity": 10.0, "unit_price": 1.0, "amount": 10.0}],
}
st2, body2 = req("POST", "/api/save_edited", payload2, tok)
print("save_edited_normal:", st2)
print(body2[:300])

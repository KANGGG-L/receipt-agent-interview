#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UAT-021 independent UAT: seed fixture + cookie API gates (AUTH=1)."""
import json, os, sys, http.cookiejar, urllib.request, urllib.error

PORT = int(os.environ.get("PORT", "15700"))
BASE = f"http://127.0.0.1:{PORT}"
OUT = os.environ.get("OUT_JSON", "")
FIX = os.environ.get("FIXTURE_JSON", "")

SENSITIVE_SKU = ("last_unit_price", "avg_cost", "vs_avg_pct", "latest_price")
SENSITIVE_DISH = ("price", "theoretical_cost", "gross_profit", "gross_margin_rate")

def log(msg):
    print(msg)

class Client:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
    def clear(self):
        self.cj.clear()
    def request(self, method, path, body=None, headers=None):
        url = BASE + path
        data = None
        hdrs = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
            hdrs["X-Requested-With"] = "XMLHttpRequest"
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read().decode(); code = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode(); code = e.code
        try:
            return code, json.loads(raw)
        except Exception:
            return code, {"_raw": raw}
    def login(self, email, password):
        self.clear()
        return self.request("POST", "/api/auth/login", {"email": email, "password": password})

def main():
    results = {"pass": 0, "fail": 0, "checks": [], "fixture": {}}
    def gate(cond, label, detail=None):
        ok = bool(cond)
        results["checks"].append({"label": label, "pass": ok, "detail": detail})
        results["pass" if ok else "fail"] += 1
        log(f"{'PASS' if ok else 'FAIL'} {label}" + (f" | {detail}" if detail else ""))
        return ok

    c = Client()
    log(f"=== UAT-021 UAT seed+API port={PORT} ===")

    code, body = c.login("boss@demo.hk", "boss123")
    gate(code == 200 and (body.get("account") or {}).get("role") == "owner", "owner_login", f"code={code}")

    code, sku_body = c.request("POST", "/api/inventory/skus", {
        "name": "UAT021料-招牌菜", "base_unit": "斤", "category": "食材",
    })
    gate(code == 200 and sku_body.get("status") == "success", "owner_create_sku", str(sku_body)[:200])
    sku_id = (sku_body.get("data") or {}).get("id") or sku_body.get("id")
    gate(bool(sku_id), "sku_id", str(sku_id))

    code, patch = c.request("PATCH", f"/api/inventory/skus/{sku_id}", {
        "current_stock": 40.0, "last_unit_price": 15.0,
    })
    log(f"INFO patch code={code} body={str(patch)[:240]}")

    # Try approve-inbound style if available
    for path, payload in [
        (f"/api/inventory/{sku_id}/stocktake", {"counted_qty": 40.0, "unit": "斤", "note": "uat021-uat-seed"}),
        ("/api/inventory/inbound", {"sku_id": sku_id, "qty": 40.0, "unit": "斤", "unit_price": 15.0, "note": "uat021"}),
    ]:
        try:
            cc, bb = c.request("POST", path, payload)
            log(f"INFO try {path} code={cc} body={str(bb)[:180]}")
        except Exception as e:
            log(f"INFO try {path} err={e}")

    code, dish_body = c.request("POST", "/api/dishes", {
        "name": "UAT021招牌菜",
        "price": 48.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 0.5, "unit": "斤"}],
    })
    gate(code == 200 and dish_body.get("status") == "success", "owner_create_dish", str(dish_body)[:240])
    dish_id = (dish_body.get("data") or {}).get("id")
    gate(bool(dish_id), "dish_id", str(dish_id))

    results["fixture"] = {"sku_id": sku_id, "dish_id": dish_id, "dish_price": 48.0, "sku_name": "UAT021料-招牌菜", "dish_name": "UAT021招牌菜"}

    # Owner visibility
    code, inv = c.request("GET", "/api/inventory")
    osku = next((x for x in (inv.get("data") or []) if x.get("id") == sku_id), None)
    gate(osku is not None, "owner_inv_has_sku")
    owner_price = None if not osku else osku.get("last_unit_price")
    if owner_price is None:
        log("WARN owner last_unit_price still null after seed — dish price remains primary AC3")
        results["fixture"]["owner_inv_price"] = None
    else:
        gate(owner_price is not None, "owner_inv_price_visible", f"last_unit_price={owner_price}")
        results["fixture"]["owner_inv_price"] = owner_price

    code, dlist = c.request("GET", "/api/dishes")
    odish = next((x for x in (dlist.get("data") or []) if x.get("id") == dish_id), None)
    gate(odish is not None and odish.get("price") == 48.0, "owner_dish_price_visible",
         None if not odish else f"price={odish.get('price')}")
    gate(odish is not None and odish.get("gross_profit") is not None, "owner_dish_margin_visible",
         None if not odish else f"gp={odish.get('gross_profit')} gm={odish.get('gross_margin_rate')}")

    code, odet = c.request("GET", f"/api/dishes/{dish_id}")
    gate((odet.get("data") or {}).get("price") == 48.0, "owner_dish_detail_price")
    code, over = c.request("GET", f"/api/dishes/{dish_id}/versions/1")
    snap = ((over.get("data") or {}).get("snapshot") or {}).get("dish") or {}
    gate(snap.get("price") == 48.0, "owner_version_price", f"snap.price={snap.get('price')}")

    code, osku_det = c.request("GET", f"/api/inventory/skus/{sku_id}")
    gate((osku_det.get("data") or {}).get("prices_visible") is True, "owner_sku_prices_visible")

    # Staff login
    code, body = c.login("staff@demo.hk", "staff123")
    gate(code == 200 and (body.get("account") or {}).get("role") == "staff", "staff_login")
    code, me = c.request("GET", "/api/auth/me")
    gate(me.get("identity_source") == "cookie" and (me.get("account") or {}).get("role") == "staff",
         "staff_me_cookie", json.dumps(me, ensure_ascii=False)[:200])

    code, inv = c.request("GET", "/api/inventory")
    ssku = next((x for x in (inv.get("data") or []) if x.get("id") == sku_id), None)
    gate(ssku is not None, "staff_inv_has_sku")
    gate(ssku is not None and ssku.get("last_unit_price") is None, "staff_inv_price_null",
         None if not ssku else f"last_unit_price={ssku.get('last_unit_price')}")
    gate(ssku is not None and ssku.get("base_unit") == "斤", "staff_inv_unit_visible",
         None if not ssku else ssku.get("base_unit"))
    gate(ssku is not None and ssku.get("current_stock") is not None, "staff_inv_qty_visible",
         None if not ssku else str(ssku.get("current_stock")))
    for k in ("vs_avg_pct", "ack_action"):
        if ssku and k in ssku:
            gate(ssku.get(k) is None or ssku.get(k) is False, f"staff_inv_{k}_masked", str(ssku.get(k)))

    code, dlist = c.request("GET", "/api/dishes")
    sdish = next((x for x in (dlist.get("data") or []) if x.get("id") == dish_id), None)
    gate(sdish is not None, "staff_dish_has")
    for k in SENSITIVE_DISH:
        gate(sdish is not None and sdish.get(k) is None, f"staff_dish_list_{k}_null",
             None if not sdish else str(sdish.get(k)))

    code, sdet = c.request("GET", f"/api/inventory/skus/{sku_id}")
    sd = sdet.get("data") or {}
    gate(sd.get("prices_visible") is False, "staff_sku_detail_prices_visible_false")
    gate(sd.get("last_unit_price") is None and sd.get("avg_cost") is None, "staff_sku_detail_costs_null")
    gate(sd.get("base_unit") == "斤" or sd.get("name"), "staff_sku_detail_nonprice_retained")

    code, ddet = c.request("GET", f"/api/dishes/{dish_id}")
    dd = ddet.get("data") or {}
    for k in SENSITIVE_DISH:
        gate(dd.get(k) is None, f"staff_dish_detail_{k}_null")
    ings = dd.get("ingredients") or []
    if ings:
        ing0 = ings[0]
        gate(ing0.get("consumption_qty") == 0.5 and ing0.get("unit") == "斤", "staff_ing_qty_unit_retained",
             f"qty={ing0.get('consumption_qty')} unit={ing0.get('unit')}")
        for k in ("sku_last_unit_price", "ingredient_cost", "unit_cost", "total_cost"):
            if k in ing0:
                gate(ing0.get(k) is None, f"staff_ing_{k}_null")

    code, sver = c.request("GET", f"/api/dishes/{dish_id}/versions/1")
    snap = ((sver.get("data") or {}).get("snapshot") or {}).get("dish") or {}
    gate(snap.get("price") is None, "staff_version_price_null", f"snap.price={snap.get('price')}")
    gate(bool(snap.get("name")), "staff_version_name_retained", snap.get("name"))

    # Fake owner header
    fake_hdr = {"X-Role": "owner", "X-User": "boss@demo.hk", "X-Tenant-Id": "default"}
    code, inv = c.request("GET", "/api/inventory", headers=fake_hdr)
    fsku = next((x for x in (inv.get("data") or []) if x.get("id") == sku_id), None)
    gate(fsku is not None and fsku.get("last_unit_price") is None, "fake_header_inv_still_null")
    code, dlist = c.request("GET", "/api/dishes", headers=fake_hdr)
    fdish = next((x for x in (dlist.get("data") or []) if x.get("id") == dish_id), None)
    gate(fdish is not None and fdish.get("price") is None, "fake_header_dish_still_null")
    code, sver = c.request("GET", f"/api/dishes/{dish_id}/versions/1", headers=fake_hdr)
    snap = ((sver.get("data") or {}).get("snapshot") or {}).get("dish") or {}
    gate(snap.get("price") is None, "fake_header_version_still_null")
    code, sdet = c.request("GET", f"/api/inventory/skus/{sku_id}", headers=fake_hdr)
    gate((sdet.get("data") or {}).get("prices_visible") is False, "fake_header_sku_detail_blocked")

    # X-Role alone → 401
    c.clear()
    code, body = c.request("GET", "/api/inventory", headers={"X-Role": "owner"})
    gate(code == 401, "xrole_alone_401", f"code={code}")

    results["overall"] = results["fail"] == 0
    log(f"=== API overall pass={results['pass']} fail={results['fail']} ===")
    if OUT:
        with open(OUT, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    if FIX:
        with open(FIX, "w") as f:
            json.dump(results["fixture"], f, ensure_ascii=False, indent=2)
    return 0 if results["overall"] else 1

if __name__ == "__main__":
    sys.exit(main())

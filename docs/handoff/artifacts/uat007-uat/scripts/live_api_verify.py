import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, ".")
import app.db as db

BASE = "http://127.0.0.1:15200"
SKU = 1
HDR = {"X-Role": "owner", "X-Tenant-Id": "default", "Content-Type": "application/json"}
HDR_STAFF = {"X-Role": "staff", "X-Tenant-Id": "default"}
HDR_B_STAFF = {"X-Role": "staff", "X-Tenant-Id": "t2"}
pass_flags = []
OLD = "const qtyText = (qty > 0 ? '+' : '') + qty;"

def req(method, path, headers=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            payload = {"raw": str(e)}
        return e.code, payload

def stock_of(obj):
    if not isinstance(obj, dict):
        return None
    if "current_stock" in obj:
        return float(obj["current_stock"])
    d = obj.get("data")
    if isinstance(d, dict) and "current_stock" in d:
        return float(d["current_stock"])
    return None

st, sku = req("GET", f"/api/inventory/skus/{SKU}", HDR_STAFF)
stock0 = stock_of(sku)
print(f"SKU_GET status={st} STOCK_AFTER_SEED={stock0}")
assert stock0 == 10.0

st, cons = req("POST", f"/api/inventory/{SKU}/consume", HDR, {"quantity": 1.0, "notes": "UAT007 uat consume"})
print(f"CONSUME_STATUS={st} STOCK={stock_of(cons)}")
assert st == 200 and stock_of(cons) == 9.0
pass_flags.append("consume_stock")

st, logs = req("GET", f"/api/inventory/logs?sku_id={SKU}", HDR_STAFF)
consume_rows = [x for x in logs["data"] if x["kind"] == "consume"]
assert len(consume_rows) == 1
c = consume_rows[0]
print(f"CONSUME_ROW id={c['id']} qty={c['qty']} qty_signed={c['qty_signed']} kind={c['kind']}")
assert float(c["qty"]) == 1.0 and float(c["qty_signed"]) == -1.0
pass_flags.append("consume_signed")

session = db.get_session()
try:
    row = session.get(db._StockLogRow, int(c["id"]))
    print(f"ID_ALIGN db_id={row.id} api_id={c['id']} db_qty={float(row.qty)} kind={row.kind} PASS")
    assert row is not None and row.id == c["id"] and float(row.qty) == 1.0 and row.kind == "consume"
    pass_flags.append("id_align")
finally:
    session.close()

db.apply_stock_log(
    sku_id=SKU, name="Synthetic_Rice_UAT", qty=1.0, unit="kg", amount=10.0,
    vendor="Vendor_B", date="2026-10-17", receipt_id=None, kind="in", tenant_id="default")
sku2 = db.get_sku(SKU, tenant_id="default")
print(f"STOCK_AFTER_IN1={float(sku2.current_stock)}")
assert float(sku2.current_stock) == 10.0
st, logs2 = req("GET", f"/api/inventory/logs?sku_id={SKU}", HDR_STAFF)
in1 = [x for x in logs2["data"] if x["kind"] == "in" and float(x["qty"]) == 1.0]
assert in1
print(f"IN1_ROW id={in1[0]['id']} qty={in1[0]['qty']} qty_signed={in1[0]['qty_signed']}")
assert float(in1[0]["qty_signed"]) == 1.0
pass_flags.append("inbound_signed")

session = db.get_session()
try:
    session.add(db._StockLogRow(
        sku_id=SKU, name="Synthetic_Rice_UAT", qty=-1.0, unit="kg", amount=-10.0,
        vendor="Vendor_A", date="2026-10-17", receipt_id=501, kind="reversal",
        note="冲销单据 #501", created_at=db.now_iso(), tenant_id="default"))
    session.add(db._StockLogRow(
        sku_id=SKU, name="Synthetic_Rice_UAT", qty=1.0, unit="kg", amount=8.0,
        vendor="", date="2026-10-17", receipt_id=None, kind="adjust",
        note="冲销作废餐品消耗恢复库存", created_at=db.now_iso(), tenant_id="default"))
    session.commit()
finally:
    session.close()

st, logs3 = req("GET", f"/api/inventory/logs?sku_id={SKU}", HDR_STAFF)
by = {}
for x in logs3["data"]:
    by.setdefault(x["kind"], []).append(x)
rev = by["reversal"][0]
adj = by["adjust"][0]
print(f"REVERSAL id={rev['id']} qty={rev['qty']} qty_signed={rev['qty_signed']}")
print(f"ADJUST id={adj['id']} qty={adj['qty']} qty_signed={adj['qty_signed']}")
assert float(rev["qty_signed"]) == -1.0 and float(adj["qty_signed"]) == 1.0
pass_flags.append("reversal_adjust")

st, cons2 = req("POST", f"/api/inventory/{SKU}/consume", HDR, {"quantity": 1.0, "notes": "UAT007 second consume"})
print(f"SECOND_CONSUME_STATUS={st} STOCK={stock_of(cons2)}")
assert st == 200 and stock_of(cons2) == 9.0
st, logs4 = req("GET", f"/api/inventory/logs?sku_id={SKU}&kind=consume", HDR_STAFF)
crows = logs4["data"]
print(f"CONSUME_COUNT={len(crows)}")
for x in crows:
    print(f"  consume id={x['id']} qty={x['qty']} signed={x['qty_signed']}")
    assert float(x["qty"]) == 1.0 and float(x["qty_signed"]) == -1.0
assert len(crows) == 2
pass_flags.append("no_double_deduct")

before_b = float(db.get_sku(2, tenant_id="t2").current_stock)
st, deny = req("POST", "/api/inventory/2/consume", HDR, {"quantity": 1.0})
print(f"CROSS_TENANT_CONSUME_STATUS={st}")
assert st == 404
after_b = float(db.get_sku(2, tenant_id="t2").current_stock)
print(f"CROSS_TENANT_B_STOCK_UNCHANGED={after_b}")
assert before_b == after_b == 7.0
st, logs_b = req("GET", "/api/inventory/logs", HDR_B_STAFF)
assert all(x.get("sku_id") != 1 for x in logs_b["data"])
print(f"CROSS_TENANT_LOGS_NO_A=true count_b={len(logs_b['data'])}")
pass_flags.append("cross_tenant")

st, all_logs = req("GET", f"/api/inventory/logs?sku_id={SKU}", HDR_STAFF)
path = "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat007-uat/logs/api-logs-default.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(all_logs, f, ensure_ascii=False, indent=2)
print(f"API_LOGS_WRITTEN={path} count={len(all_logs['data'])}")

js = urllib.request.urlopen(BASE + "/static/js/inventory_board.js", timeout=10).read().decode()
print(f"UI_USES_QTY_SIGNED={('invLedgerQtySigned' in js and 'invLedgerQtyText' in js and 'qty_signed' in js)}")
print(f"OLD_PATTERN_ABSENT={OLD not in js}")
print(f"USES_HELPER_CALL={'invLedgerQtyText(r)' in js}")
assert OLD not in js and "invLedgerQtyText(r)" in js
pass_flags.append("ui_source")

print("PASS_FLAGS=" + ",".join(pass_flags))
print("LIVE_VERIFY_PASS=true")

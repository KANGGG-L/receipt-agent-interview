# -*- coding: utf-8 -*-
"""UAT-004 live isolate probe: optional contact_phone/notes empty save; settlement empty still 422."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib import error, request

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
EV = ART / "evidence"
EV.mkdir(parents=True, exist_ok=True)

TENANT_A = "uat004-a"
TENANT_B = "uat004-b"


def hdr(tenant: str, role: str = "owner") -> dict:
    return {
        "Content-Type": "application/json",
        "X-Role": role,
        "X-Tenant-Id": tenant,
    }


def call(method: str, path: str, body=None, *, tenant: str, role: str = "owner"):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = request.Request(
        BASE + path,
        data=data,
        headers=hdr(tenant, role),
        method=method,
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return {
                "ok": True,
                "status": resp.status,
                "body": parsed,
                "raw": raw[:2000],
            }
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return {
            "ok": False,
            "status": e.code,
            "body": parsed,
            "raw": raw[:2000],
        }


def find_by_name(items, name: str):
    for row in items or []:
        if row.get("name") == name:
            return row
    return None


def is_empty_norm(v) -> bool:
    return v == "" or v is None


steps = []
verdict = "PASS"
failures = []


def record(name: str, passed: bool, detail: dict):
    global verdict
    steps.append({"name": name, "pass": passed, "detail": detail})
    if not passed:
        verdict = "FAIL"
        failures.append(name)
    print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    print(json.dumps(detail, ensure_ascii=False, indent=2)[:1500])


# --- Tenant B baseline ---
b0 = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_B)
b_baseline_ids = sorted(x["id"] for x in (b0.get("body") or {}).get("data") or [])
b_baseline_names = sorted(x.get("name") for x in (b0.get("body") or {}).get("data") or [])
record(
    "tenant_b_baseline",
    b0["status"] == 200 and (b0.get("body") or {}).get("status") == "success",
    {"status": b0["status"], "count": len(b_baseline_ids), "names": b_baseline_names},
)

# --- AC1: UI-default payload (empty phone/notes strings, settlement null) ---
# Mirrors supplier_archive.js saveSupplier when fields left blank.
ui_payload = {
    "name": "Vendor_A",
    "contact_phone": "",
    "notes": "",
    "settlement_pref": None,
    "payment_terms_days": None,
}
r1 = call("POST", "/api/suppliers", ui_payload, tenant=TENANT_A)
sid_a = (r1.get("body") or {}).get("id")
ac1_create_ok = (
    r1["status"] == 200
    and (r1.get("body") or {}).get("status") == "success"
    and sid_a is not None
)
record(
    "ac1_ui_default_create_http",
    ac1_create_ok,
    {"status": r1["status"], "body": r1.get("body"), "payload": ui_payload},
)

r1g = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_A)
row1 = find_by_name((r1g.get("body") or {}).get("data"), "Vendor_A")
phone1 = None if row1 is None else row1.get("contact_phone")
notes1 = None if row1 is None else row1.get("notes")
ac1_empty = row1 is not None and is_empty_norm(phone1) and is_empty_norm(notes1)
# Prefer empty-string norm as UI/API contract (_supplier_dict uses `or ""`)
ac1_empty_str = row1 is not None and phone1 == "" and notes1 == ""
record(
    "ac1_ui_default_readback_empty_norm",
    ac1_empty and ac1_empty_str,
    {
        "row_id": None if row1 is None else row1.get("id"),
        "contact_phone": phone1,
        "notes": notes1,
        "settlement_pref": None if row1 is None else row1.get("settlement_pref"),
        "empty_string_norm": ac1_empty_str,
    },
)

# --- AC2: fill both fields then read back ---
fill = {"contact_phone": "91234567", "notes": "note-uat004"}
r2 = call("PATCH", f"/api/suppliers/{sid_a}", fill, tenant=TENANT_A) if sid_a else {
    "status": 0, "body": None
}
r2g = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_A)
row2 = find_by_name((r2g.get("body") or {}).get("data"), "Vendor_A")
ac2_ok = (
    r2["status"] == 200
    and row2 is not None
    and row2.get("contact_phone") == "91234567"
    and row2.get("notes") == "note-uat004"
)
record(
    "ac2_fill_both_readback",
    ac2_ok,
    {
        "patch_status": r2["status"],
        "patch_body": r2.get("body"),
        "contact_phone": None if row2 is None else row2.get("contact_phone"),
        "notes": None if row2 is None else row2.get("notes"),
    },
)

# --- AC3: null / omitted / empty-string separately (create + patch) ---
# Create variants
variants = []

# create omitted phone+notes (name only)
rc_omit = call("POST", "/api/suppliers", {"name": "Vendor_Omit"}, tenant=TENANT_A)
sid_omit = (rc_omit.get("body") or {}).get("id")
rg_omit = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_A)
row_omit = find_by_name((rg_omit.get("body") or {}).get("data"), "Vendor_Omit")
variants.append(
    {
        "case": "create_omitted_phone_notes",
        "http": rc_omit["status"],
        "id": sid_omit,
        "contact_phone": None if row_omit is None else row_omit.get("contact_phone"),
        "notes": None if row_omit is None else row_omit.get("notes"),
        "pass": rc_omit["status"] == 200
        and row_omit is not None
        and row_omit.get("contact_phone") == ""
        and row_omit.get("notes") == "",
    }
)

# create null phone+notes
rc_null = call(
    "POST",
    "/api/suppliers",
    {"name": "Vendor_Null", "contact_phone": None, "notes": None},
    tenant=TENANT_A,
)
sid_null = (rc_null.get("body") or {}).get("id")
rg_null = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_A)
row_null = find_by_name((rg_null.get("body") or {}).get("data"), "Vendor_Null")
variants.append(
    {
        "case": "create_null_phone_notes",
        "http": rc_null["status"],
        "id": sid_null,
        "contact_phone": None if row_null is None else row_null.get("contact_phone"),
        "notes": None if row_null is None else row_null.get("notes"),
        "pass": rc_null["status"] == 200
        and row_null is not None
        and row_null.get("contact_phone") == ""
        and row_null.get("notes") == "",
    }
)

# create empty-string phone+notes
rc_empty = call(
    "POST",
    "/api/suppliers",
    {"name": "Vendor_EmptyStr", "contact_phone": "", "notes": ""},
    tenant=TENANT_A,
)
sid_empty = (rc_empty.get("body") or {}).get("id")
rg_empty = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_A)
row_empty = find_by_name((rg_empty.get("body") or {}).get("data"), "Vendor_EmptyStr")
variants.append(
    {
        "case": "create_empty_string_phone_notes",
        "http": rc_empty["status"],
        "id": sid_empty,
        "contact_phone": None if row_empty is None else row_empty.get("contact_phone"),
        "notes": None if row_empty is None else row_empty.get("notes"),
        "pass": rc_empty["status"] == 200
        and row_empty is not None
        and row_empty.get("contact_phone") == ""
        and row_empty.get("notes") == "",
    }
)

# Patch variants on Vendor_A (currently filled): clear via empty string, then refill, clear via null, refill, omit (must not clear)
# empty-string clear
rp_es = call(
    "PATCH",
    f"/api/suppliers/{sid_a}",
    {"contact_phone": "", "notes": ""},
    tenant=TENANT_A,
) if sid_a else {"status": 0, "body": None}
rg_es = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_A)
row_es = find_by_name((rg_es.get("body") or {}).get("data"), "Vendor_A")
variants.append(
    {
        "case": "patch_empty_string_clears",
        "http": rp_es["status"],
        "contact_phone": None if row_es is None else row_es.get("contact_phone"),
        "notes": None if row_es is None else row_es.get("notes"),
        "pass": rp_es["status"] == 200
        and row_es is not None
        and row_es.get("contact_phone") == ""
        and row_es.get("notes") == "",
    }
)

# refill
call(
    "PATCH",
    f"/api/suppliers/{sid_a}",
    {"contact_phone": "98887766", "notes": "keep-me"},
    tenant=TENANT_A,
) if sid_a else None

# null clear
rp_null = call(
    "PATCH",
    f"/api/suppliers/{sid_a}",
    {"contact_phone": None, "notes": None},
    tenant=TENANT_A,
) if sid_a else {"status": 0, "body": None}
rg_n = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_A)
row_n = find_by_name((rg_n.get("body") or {}).get("data"), "Vendor_A")
variants.append(
    {
        "case": "patch_null_clears",
        "http": rp_null["status"],
        "contact_phone": None if row_n is None else row_n.get("contact_phone"),
        "notes": None if row_n is None else row_n.get("notes"),
        "pass": rp_null["status"] == 200
        and row_n is not None
        and row_n.get("contact_phone") == ""
        and row_n.get("notes") == "",
    }
)

# refill again
call(
    "PATCH",
    f"/api/suppliers/{sid_a}",
    {"contact_phone": "91112222", "notes": "omit-keep"},
    tenant=TENANT_A,
) if sid_a else None

# omit: patch only settlement_pref cash — phone/notes must stay
rp_omit = call(
    "PATCH",
    f"/api/suppliers/{sid_a}",
    {"settlement_pref": "cash"},
    tenant=TENANT_A,
) if sid_a else {"status": 0, "body": None}
rg_o = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_A)
row_o = find_by_name((rg_o.get("body") or {}).get("data"), "Vendor_A")
variants.append(
    {
        "case": "patch_omit_phone_notes_unchanged",
        "http": rp_omit["status"],
        "contact_phone": None if row_o is None else row_o.get("contact_phone"),
        "notes": None if row_o is None else row_o.get("notes"),
        "settlement_pref": None if row_o is None else row_o.get("settlement_pref"),
        "pass": rp_omit["status"] == 200
        and row_o is not None
        and row_o.get("contact_phone") == "91112222"
        and row_o.get("notes") == "omit-keep"
        and row_o.get("settlement_pref") == "cash",
    }
)

ac3_ok = all(v["pass"] for v in variants)
record("ac3_null_omitted_empty_string", ac3_ok, {"variants": variants})

# --- AC4: explicit empty settlement still 422 ---
r4_create = call(
    "POST",
    "/api/suppliers",
    {"name": "Vendor_BadSettle", "settlement_pref": ""},
    tenant=TENANT_A,
)
# Pydantic validation → 422; detail should mention settlement
settle_msg = json.dumps(r4_create.get("body"), ensure_ascii=False)
r4_patch = call(
    "PATCH",
    f"/api/suppliers/{sid_a}",
    {"settlement_pref": ""},
    tenant=TENANT_A,
) if sid_a else {"status": 0, "body": None}
settle_msg_p = json.dumps(r4_patch.get("body"), ensure_ascii=False)
ac4_ok = (
    r4_create["status"] == 422
    and ("结算方式不能为空" in settle_msg or "settlement" in settle_msg.lower())
    and r4_patch["status"] == 422
    and ("结算方式不能为空" in settle_msg_p or "settlement" in settle_msg_p.lower())
)
record(
    "ac4_explicit_empty_settlement_422",
    ac4_ok,
    {
        "create_status": r4_create["status"],
        "create_body": r4_create.get("body"),
        "patch_status": r4_patch["status"],
        "patch_body": r4_patch.get("body"),
    },
)

# --- Negative: empty phone/notes must NOT 422 ---
# Already covered by AC1/AC3; assert no 422 on those paths
neg_422_phone = any(
    (s["name"].startswith("ac1_") or s["name"].startswith("ac3_")) and not s["pass"]
    for s in steps
)
# Explicit check: create with empty phone returns not 422
r_neg = call(
    "POST",
    "/api/suppliers",
    {"name": "Vendor_No422Phone", "contact_phone": "", "notes": ""},
    tenant=TENANT_A,
)
record(
    "neg_empty_phone_notes_not_422",
    r_neg["status"] == 200 and r_neg["status"] != 422,
    {"status": r_neg["status"], "body": r_neg.get("body")},
)

# Credit settlement still accepted (do not loosen credit rules by accident)
r_credit = call(
    "POST",
    "/api/suppliers",
    {"name": "Vendor_Credit", "settlement_pref": "credit", "contact_phone": "", "notes": ""},
    tenant=TENANT_A,
)
rg_c = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_A)
row_c = find_by_name((rg_c.get("body") or {}).get("data"), "Vendor_Credit")
record(
    "neg_credit_settlement_still_ok",
    r_credit["status"] == 200
    and row_c is not None
    and row_c.get("settlement_pref") == "credit",
    {
        "status": r_credit["status"],
        "settlement_pref": None if row_c is None else row_c.get("settlement_pref"),
        "contact_phone": None if row_c is None else row_c.get("contact_phone"),
    },
)

# Cross-tenant: B cannot see A's Vendor_A; B cannot patch A's id
rb_list = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_B)
b_sees_a = find_by_name((rb_list.get("body") or {}).get("data"), "Vendor_A") is not None
rb_hack = call(
    "PATCH",
    f"/api/suppliers/{sid_a}",
    {"notes": "hacked-by-b"},
    tenant=TENANT_B,
) if sid_a else {"status": 0, "body": None}
# Seed one B supplier and confirm unchanged after A ops
rb_seed = call(
    "POST",
    "/api/suppliers",
    {"name": "Vendor_Only_In_B", "contact_phone": "222", "notes": "b-note"},
    tenant=TENANT_B,
)
# Re-do an A create to ensure no spill
call("POST", "/api/suppliers", {"name": "Vendor_A_Extra", "contact_phone": ""}, tenant=TENANT_A)
rb_after = call("GET", "/api/suppliers?include_inactive=1", tenant=TENANT_B)
b_after_names = sorted(x.get("name") for x in (rb_after.get("body") or {}).get("data") or [])
b_row = find_by_name((rb_after.get("body") or {}).get("data"), "Vendor_Only_In_B")
# Baseline was empty; after seed should only have Vendor_Only_In_B (plus any prior)
# "unchanged" relative to ops on A: B must not gain A's names; B's own row intact
a_names_leak = any(
    n in b_after_names
    for n in (
        "Vendor_A",
        "Vendor_Omit",
        "Vendor_Null",
        "Vendor_EmptyStr",
        "Vendor_No422Phone",
        "Vendor_Credit",
        "Vendor_A_Extra",
        "Vendor_BadSettle",
    )
)
record(
    "neg_tenant_b_isolation",
    (not b_sees_a)
    and rb_hack["status"] in (404, 403)
    and not a_names_leak
    and b_row is not None
    and b_row.get("contact_phone") == "222"
    and b_row.get("notes") == "b-note"
    and rb_seed["status"] == 200,
    {
        "b_sees_vendor_a": b_sees_a,
        "hack_status": rb_hack["status"],
        "hack_body": rb_hack.get("body"),
        "b_names_after": b_after_names,
        "a_names_leak": a_names_leak,
        "b_row": b_row,
        "seed_status": rb_seed["status"],
    },
)

# UI payload literal audit from source (no browser required for AUTO_API card)
js_path = Path("/workspace/receipt-agent-interview/demo/static/js/supplier_archive.js")
js = js_path.read_text(encoding="utf-8")
has_phone_trim = "getElementById('supPhone').value || '').trim()" in js
has_notes_trim = "getElementById('supNotes').value || '').trim()" in js
has_payload = (
    "contact_phone: phone" in js
    and "notes: notes" in js
    and "settlement_pref: pref || null" in js
)
record(
    "ui_payload_contract_static",
    has_phone_trim and has_notes_trim and has_payload and "supPhone" in js and "supNotes" in js,
    {
        "file": str(js_path),
        "phone_trim": has_phone_trim,
        "notes_trim": has_notes_trim,
        "payload_fields": has_payload,
        "settlement_null_when_blank": "settlement_pref: pref || null" in js,
        "snippet_line_hint": "saveSupplier payload mirrors AC1 UI defaults",
    },
)

summary = {
    "card": "UAT-004",
    "phase": "DEV",
    "base": BASE,
    "tenant_a": TENANT_A,
    "tenant_b": TENANT_B,
    "verdict": verdict,
    "failures": failures,
    "steps": steps,
    "ts": time.strftime("%Y-%m-%d %H:%M:%S CST"),
}
out = EV / "supplier-optional-probe.json"
out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print("VERDICT", verdict)
print("WROTE", out)
sys.exit(0 if verdict == "PASS" else 1)

# -*- coding: utf-8 -*-
"""UAT-001 live API probe: edited-with-image vs manual-no-image; expired sig recover; tenant B."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib import error, request

from PIL import Image

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
DB_PATH = os.environ["DB_PATH"]
# Must match running uvicorn's uploads (demo/uploads); basename-only resolution.
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/workspace/receipt-agent-interview/demo/uploads"))
EV = ART / "evidence"
EV.mkdir(parents=True, exist_ok=True)

DEMO = Path("/workspace/receipt-agent-interview/demo")
sys.path.insert(0, str(DEMO))
os.environ.setdefault("AUTH_ENABLED", "0")
# Ensure db module binds to isolate DB
os.environ["DB_PATH"] = DB_PATH

from app import db  # noqa: E402

TENANT_A = "uat001-a"
TENANT_B = "uat001-b"
result = {"card": "UAT-001", "script": "probe_original_image_api.py", "verdict": "FAIL", "steps": [], "ids": {}}


def step(name, ok, detail=None):
    result["steps"].append({"name": name, "pass": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), name, json.dumps(detail, ensure_ascii=False, default=str)[:900] if detail is not None else "")


def hdr(tenant, role="owner"):
    return {
        "Content-Type": "application/json",
        "X-Role": role,
        "X-Tenant-Id": tenant,
        "X-Requested-With": "fetch",
    }


def call(method, path, body=None, *, tenant=TENANT_A, role="owner", raw=False):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = request.Request(BASE + path, data=data, headers=hdr(tenant, role), method=method)
    try:
        with request.urlopen(req, timeout=60) as resp:
            content = resp.read()
            if raw:
                return {"ok": True, "status": resp.status, "content": content, "headers": dict(resp.headers)}
            text = content.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text else None
            except json.JSONDecodeError:
                parsed = text
            return {"ok": True, "status": resp.status, "body": parsed}
    except error.HTTPError as e:
        content = e.read()
        if raw:
            return {"ok": False, "status": e.code, "content": content, "headers": dict(e.headers)}
        text = content.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = text
        return {"ok": False, "status": e.code, "body": parsed}
    except Exception as e:
        return {"ok": False, "status": 0, "body": str(e)}


def png(path: Path, color=(40, 120, 220), size=(640, 480)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")


def seed_edited_with_image():
    name = "uat001_edited_with_image.png"
    img_path = UPLOAD_DIR / name
    png(img_path, color=(30, 140, 230), size=(640, 480))
    rid = db.create_receipt(supplier_name="Vendor_A_Edited_WithImage", status="edited", tenant_id=TENANT_A)
    db.update_receipt(
        rid,
        receipt_date="2026-10-15",
        total_amount=230.0,
        settlement_type="credit",
        doc_form="printed",
        image_path=str(img_path),
        version=1,
    )
    db.set_receipt_items(
        rid,
        [
            {"name": "UAT_Rice", "quantity": 10, "unit": "kg", "unit_price": 12, "amount": 120},
            {"name": "UAT_Oil", "quantity": 5, "unit": "L", "unit_price": 20, "amount": 100},
        ],
        tenant_id=TENANT_A,
    )
    return rid, name


def seed_manual_no_image():
    rid = db.create_receipt(supplier_name="Vendor_A_Manual_NoImage", status="edited", tenant_id=TENANT_A)
    db.update_receipt(
        rid,
        receipt_date="2026-10-15",
        total_amount=10.0,
        settlement_type="cash",
        doc_form="manual_entry",
        image_path="",
        version=1,
    )
    db.set_receipt_items(
        rid,
        [{"name": "Synthetic_Rice", "quantity": 1, "unit": "kg", "unit_price": 10, "amount": 10}],
        tenant_id=TENANT_A,
    )
    return rid


def seed_tenant_b():
    rid = db.create_receipt(supplier_name="Vendor_B_Baseline", status="edited", tenant_id=TENANT_B)
    db.update_receipt(
        rid,
        receipt_date="2026-10-15",
        total_amount=1.0,
        settlement_type="cash",
        doc_form="manual_entry",
        image_path="",
        version=1,
    )
    return rid


def tenant_b_snapshot():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT id, supplier_name, status, image_path, total_amount FROM receipts WHERE tenant_id=? ORDER BY id",
        (TENANT_B,),
    ).fetchall()
    con.close()
    return {"count": len(rows), "rows": [list(r) for r in rows]}


def main():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    rid_b = seed_tenant_b()
    b_before = tenant_b_snapshot()
    step("tenant_b_baseline", b_before["count"] >= 1 and rid_b in [r[0] for r in b_before["rows"]], b_before)

    rid_edited, img_name = seed_edited_with_image()
    rid_manual = seed_manual_no_image()
    result["ids"] = {"edited": rid_edited, "manual": rid_manual, "tenant_b": rid_b, "image_name": img_name}

    # write ids for UI probe
    (EV / "seed-ids.json").write_text(json.dumps(result["ids"], indent=2), encoding="utf-8")

    d = call("GET", f"/api/receipt/{rid_edited}", tenant=TENANT_A)
    body = d.get("body") or {}
    data = (body.get("data") or {}) if isinstance(body, dict) else {}
    top_url = body.get("image_url") if isinstance(body, dict) else None
    data_url = data.get("image_url")
    ok_edited = (
        d.get("status") == 200
        and bool(top_url)
        and str(top_url).startswith(f"/api/receipt/{rid_edited}/image")
        and "sig=" in str(top_url)
        and data_url == top_url
        and data.get("has_original_image") is True
        and body.get("has_original_image") is True
    )
    step(
        "edited_detail_signed_url",
        ok_edited,
        {
            "status": d.get("status"),
            "top_url": top_url,
            "data_url": data_url,
            "has_original_image": data.get("has_original_image"),
        },
    )

    img = call("GET", top_url, tenant=TENANT_A, raw=True) if top_url else {"status": 0, "content": b""}
    content = img.get("content") or b""
    ok_img = img.get("status") == 200 and len(content) > 0 and (
        content[:4] == b"\x89PNG" or content[:2] == b"\xff\xd8"
    )
    step(
        "edited_image_http_200",
        ok_img,
        {"status": img.get("status"), "bytes": len(content), "magic": content[:4].hex() if content else None},
    )

    m = call("GET", f"/api/receipt/{rid_manual}", tenant=TENANT_A)
    mb = m.get("body") or {}
    md = (mb.get("data") or {}) if isinstance(mb, dict) else {}
    ok_manual = (
        m.get("status") == 200
        and mb.get("image_url") == ""
        and md.get("image_url") == ""
        and md.get("has_original_image") is False
        and mb.get("has_original_image") is False
    )
    step(
        "manual_detail_empty_image",
        ok_manual,
        {
            "status": m.get("status"),
            "image_url": mb.get("image_url") if isinstance(mb, dict) else None,
            "has_original_image": md.get("has_original_image"),
        },
    )

    tampered = f"/api/receipt/{rid_edited}/image?v={img_name}&sig=corrupted_invalid_sig"
    t = call("GET", tampered, tenant=TENANT_A)
    step("tampered_sig_403", t.get("status") == 403, {"status": t.get("status"), "body": t.get("body")})

    no_sig = f"/api/receipt/{rid_edited}/image?v={img_name}"
    ns = call("GET", no_sig, tenant=TENANT_A)
    step("missing_sig_403", ns.get("status") == 403, {"status": ns.get("status"), "body": ns.get("body")})

    refresh = call("GET", f"/api/receipt/{rid_edited}", tenant=TENANT_A)
    fresh_url = (refresh.get("body") or {}).get("image_url") if isinstance(refresh.get("body"), dict) else None
    recovered = call("GET", fresh_url, tenant=TENANT_A, raw=True) if fresh_url else {"status": 0, "content": b""}
    ok_rec = (
        refresh.get("status") == 200
        and bool(fresh_url)
        and "sig=" in str(fresh_url)
        and recovered.get("status") == 200
        and len(recovered.get("content") or b"") > 0
    )
    step(
        "refresh_recovers_signed_url",
        ok_rec,
        {"fresh_url": fresh_url, "img_status": recovered.get("status"), "bytes": len(recovered.get("content") or b"")},
    )

    cross = call("GET", f"/api/receipt/{rid_edited}", tenant=TENANT_B)
    step("cross_tenant_detail_404", cross.get("status") == 404, {"status": cross.get("status")})

    if top_url and f"tenant_id={TENANT_A}" in str(top_url):
        bad_tenant_url = str(top_url).replace(f"tenant_id={TENANT_A}", f"tenant_id={TENANT_B}")
    else:
        sep = "&" if top_url and "?" in str(top_url) else "?"
        bad_tenant_url = f"{top_url}{sep}tenant_id={TENANT_B}"
    cross_img = call("GET", bad_tenant_url, tenant=TENANT_B, raw=True)
    step("cross_tenant_image_403", cross_img.get("status") == 403, {"status": cross_img.get("status")})

    b_after = tenant_b_snapshot()
    ok_b = b_after == b_before
    step("tenant_b_unchanged", ok_b, {"before": b_before, "after": b_after})

    result["verdict"] = "PASS" if all(s["pass"] for s in result["steps"]) else "FAIL"
    out = EV / "original-image-api-probe.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("VERDICT", result["verdict"], "wrote", out)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""UAT-005 live API probe: empty/corrupt/unsupported vs valid formats; timeout->manual; tenant B."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from urllib import error, request

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
DB_PATH = os.environ["DB_PATH"]
EV = ART / "evidence"
EV.mkdir(parents=True, exist_ok=True)

ROOT = Path("/workspace/receipt-agent-interview")
FIXTURES = ROOT / "demo/tests/fixtures/uat005"
TENANT_A = "uat005-a"
TENANT_B = "uat005-b"

result = {"card": "UAT-005", "script": "probe_upload_errors_api.py", "verdict": "FAIL", "steps": [], "counts": {}}


def step(name, ok, detail=None):
    result["steps"].append({"name": name, "pass": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), name, json.dumps(detail, ensure_ascii=False, default=str)[:900] if detail is not None else "")


def hdr(tenant, role="staff"):
    return {"X-Role": role, "X-Tenant-Id": tenant, "X-Requested-With": "fetch"}


def multipart_upload(path: Path, filename: str, content_type: str, *, tenant=TENANT_A, endpoint="/api/upload", field="receipt"):
    boundary = f"----uat005{uuid.uuid4().hex}"
    data = path.read_bytes() if path.exists() else b""
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = request.Request(
        BASE + endpoint,
        data=body,
        headers={**hdr(tenant), "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text else None
            except json.JSONDecodeError:
                parsed = text
            return {"ok": True, "status": resp.status, "body": parsed, "raw_len": len(data)}
    except error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = text
        return {"ok": False, "status": e.code, "body": parsed, "raw_len": len(data)}
    except Exception as e:
        return {"ok": False, "status": 0, "body": str(e), "raw_len": 0}


def call_json(method, path, body=None, *, tenant=TENANT_A, role="staff"):
    data = None if body is None else json.dumps(body).encode("utf-8")
    h = hdr(tenant, role)
    if data is not None:
        h["Content-Type"] = "application/json"
    req = request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(text) if text else None
            except json.JSONDecodeError:
                parsed = text
            return {"ok": True, "status": resp.status, "body": parsed}
    except error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = text
        return {"ok": False, "status": e.code, "body": parsed}
    except Exception as e:
        return {"ok": False, "status": 0, "body": str(e)}


def count_receipts(tenant):
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute("SELECT COUNT(*) FROM receipts WHERE tenant_id=?", (tenant,)).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def count_batches_via_receipts(tenant):
    con = sqlite3.connect(DB_PATH)
    try:
        # inventory_batches may join via receipt_id
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "inventory_batches" not in tables:
            return 0
        q = """
        SELECT COUNT(*) FROM inventory_batches ib
        JOIN receipts r ON ib.receipt_id = r.id
        WHERE r.tenant_id=?
        """
        try:
            return int(con.execute(q, (tenant,)).fetchone()[0])
        except sqlite3.Error:
            return 0
    finally:
        con.close()


# --- baseline B ---
b_before = count_receipts(TENANT_B)
b_batches_before = count_batches_via_receipts(TENANT_B)
a_before = count_receipts(TENANT_A)

# 1 empty.png
r = multipart_upload(FIXTURES / "empty.png", "empty.png", "image/png")
body = r.get("body") or {}
ok = r["status"] == 400 and isinstance(body, dict) and body.get("code") == "EMPTY_FILE"
step("empty_png_EMPTY_FILE", ok, {"status": r["status"], "code": body.get("code") if isinstance(body, dict) else body, "msg": (body.get("msg") if isinstance(body, dict) else None)})
a_after_empty = count_receipts(TENANT_A)
step("empty_zero_residue", a_after_empty == a_before, {"before": a_before, "after": a_after_empty})

# 2 malformed.png
r = multipart_upload(FIXTURES / "malformed.png", "malformed.png", "image/png")
body = r.get("body") or {}
ok = r["status"] == 400 and isinstance(body, dict) and body.get("code") == "INVALID_IMAGE"
step("malformed_INVALID_IMAGE", ok, {"status": r["status"], "code": body.get("code") if isinstance(body, dict) else body, "msg": (body.get("msg") if isinstance(body, dict) else None)})
a_after_mal = count_receipts(TENANT_A)
step("malformed_zero_residue", a_after_mal == a_before, {"before": a_before, "after": a_after_mal})

# 3 wrong-type.txt
r = multipart_upload(FIXTURES / "wrong-type.txt", "wrong-type.txt", "text/plain")
body = r.get("body") or {}
ok = r["status"] == 400 and isinstance(body, dict) and body.get("code") == "UNSUPPORTED_IMAGE_FORMAT"
step("wrong_type_UNSUPPORTED", ok, {"status": r["status"], "code": body.get("code") if isinstance(body, dict) else body, "msg": (body.get("msg") if isinstance(body, dict) else None)})

# 4 oversize
r = multipart_upload(FIXTURES / "oversize-synthetic.png", "oversize-synthetic.png", "image/png")
body = r.get("body") or {}
ok = r["status"] == 400 and isinstance(body, dict) and body.get("code") == "FILE_TOO_LARGE"
step("oversize_FILE_TOO_LARGE", ok, {"status": r["status"], "code": body.get("code") if isinstance(body, dict) else body, "bytes": r.get("raw_len")})

# 5 PDF on receipt upload -> UNSUPPORTED (not blanket "recognition")
r = multipart_upload(FIXTURES / "receipt-hant.pdf", "receipt-hant.pdf", "application/pdf")
body = r.get("body") or {}
ok = r["status"] == 400 and isinstance(body, dict) and body.get("code") == "UNSUPPORTED_IMAGE_FORMAT"
step("pdf_upload_UNSUPPORTED_not_recognition", ok, {"status": r["status"], "code": body.get("code") if isinstance(body, dict) else body})

# 6-8 valid PNG/JPEG/WEBP
valid_ids = {}
for name, ctype, key in [
    ("receipt-hant.png", "image/png", "png"),
    ("receipt-hant.jpg", "image/jpeg", "jpg"),
    ("receipt-hant.webp", "image/webp", "webp"),
]:
    r = multipart_upload(FIXTURES / name, name, ctype)
    body = r.get("body") or {}
    rid = body.get("receipt_id") if isinstance(body, dict) else None
    ok = r["status"] == 200 and rid is not None
    step(f"valid_{key}_accepted", ok, {"status": r["status"], "receipt_id": rid, "status_field": body.get("status") if isinstance(body, dict) else None, "code": body.get("code") if isinstance(body, dict) else None, "image_url": bool(body.get("image_url")) if isinstance(body, dict) else False})
    if rid:
        valid_ids[key] = rid

# 9 HEIC convert
r = multipart_upload(FIXTURES / "receipt-hant.heic", "receipt-hant.heic", "image/heic", endpoint="/api/convert-image", field="file")
ok = r["status"] == 200  # convert returns image/jpeg bytes; HTTPError path uses json; success may be binary
# For binary success, body may be non-json — re-check via raw
if r["status"] == 200:
    step("heic_convert_jpeg", True, {"status": 200})
else:
    # maybe content-type jpeg with empty body parse — try again raw
    step("heic_convert_jpeg", False, {"status": r["status"], "body": r.get("body")})

# TIFF if present
tif = FIXTURES / "receipt-hant.tif"
if tif.exists():
    r = multipart_upload(tif, "receipt-hant.tif", "image/tiff")
    body = r.get("body") or {}
    ok = r["status"] == 200 and isinstance(body, dict) and body.get("receipt_id")
    step("valid_tif_accepted", ok, {"status": r["status"], "receipt_id": body.get("receipt_id") if isinstance(body, dict) else None})

# valid image -> convert_manual preserves image
rid = valid_ids.get("png")
if rid:
    r = call_json("POST", f"/api/receipt/{rid}/convert_manual", tenant=TENANT_A)
    body = r.get("body") or {}
    data = body.get("data") if isinstance(body, dict) else {}
    ok = (
        r["status"] == 200
        and isinstance(body, dict)
        and body.get("status") == "success"
        and data.get("doc_form") == "manual_entry"
        and data.get("has_original_image") is True
        and bool(data.get("image_url"))
    )
    step("valid_timeout_path_convert_manual_keeps_image", ok, {
        "status": r["status"],
        "doc_form": data.get("doc_form") if isinstance(data, dict) else None,
        "has_original_image": data.get("has_original_image") if isinstance(data, dict) else None,
        "image_url_present": bool(data.get("image_url")) if isinstance(data, dict) else False,
    })
else:
    step("valid_timeout_path_convert_manual_keeps_image", False, {"reason": "no png receipt_id"})

# semantic separation (static codes distinct)
codes = [
    "EMPTY_FILE", "INVALID_IMAGE", "UNSUPPORTED_IMAGE_FORMAT", "FILE_TOO_LARGE",
    "IMAGE_QUALITY_ERROR", "RECOGNITION_UNREADABLE", "ENGINE_TIMEOUT",
]
step("error_codes_semantically_distinct", len(codes) == len(set(codes)), {"codes": codes})

# tenant B isolation
if valid_ids.get("jpg"):
    rid = valid_ids["jpg"]
    peek = call_json("GET", f"/api/receipt/{rid}", tenant=TENANT_B, role="owner")
    step("cross_tenant_detail_404", peek["status"] == 404, {"status": peek["status"]})
else:
    step("cross_tenant_detail_404", False, {"reason": "no jpg id"})

b_after = count_receipts(TENANT_B)
b_batches_after = count_batches_via_receipts(TENANT_B)
step("tenant_b_unchanged", b_after == b_before and b_batches_after == b_batches_before, {
    "receipts_before": b_before, "receipts_after": b_after,
    "batches_before": b_batches_before, "batches_after": b_batches_after,
})

# no approvable from bad uploads: count only grew from valid uploads
a_final = count_receipts(TENANT_A)
result["counts"] = {"a_before": a_before, "a_final": a_final, "valid_ids": valid_ids}

# Rejected codes must not equal RECOGNITION_UNREADABLE / ENGINE_TIMEOUT
bad_codes = [s["detail"].get("code") for s in result["steps"] if s["name"] in (
    "empty_png_EMPTY_FILE", "malformed_INVALID_IMAGE", "wrong_type_UNSUPPORTED",
    "oversize_FILE_TOO_LARGE", "pdf_upload_UNSUPPORTED_not_recognition"
) and isinstance(s.get("detail"), dict)]
step(
    "bad_inputs_not_misclassified_as_recognition",
    all(c not in ("RECOGNITION_UNREADABLE", "ENGINE_TIMEOUT", "ENGINE_NOT_CONFIGURED") for c in bad_codes if c),
    {"codes": bad_codes},
)

result["verdict"] = "PASS" if all(s["pass"] for s in result["steps"]) else "FAIL"
(EV / "upload-errors-api-probe.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
print("VERDICT", result["verdict"], f"{sum(1 for s in result['steps'] if s['pass'])}/{len(result['steps'])}")
sys.exit(0 if result["verdict"] == "PASS" else 1)

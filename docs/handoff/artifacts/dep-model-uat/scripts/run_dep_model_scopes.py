#!/usr/bin/env python3
"""DEP-MODEL: real model path for 9 operational scopes.

Catalog discovery note: the original UAG scope_id list lives on the local
harness (receipt-repair-plan-20261015) and is NOT present in this cloud clone.
These 9 scope_ids are inferred from ENG-02/ENG-03 PARTIAL deferred model-path
AC plus recognition contract points that mocked E2E currently covers.
Independent QA must re-verify against any recovered original catalog.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("DEP_MODEL_BASE", "http://127.0.0.1:15411")
ART = Path(os.environ.get(
    "DEP_MODEL_ART",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/dep-model-uat",
))
DB = ART / "environment" / "dep_model.db"
IMG_711 = ART / "evidence" / "sample_711.jpg"
IMG_HEIC = ART / "evidence" / "sample_heic_5878.jpg"
ORIG_711 = json.loads((ART / "original" / "assertions_711.json").read_text())

# Original triple combos (viewport x role) from S3 review methodology
TRIPLES = [
    {"id": "desk-staff", "viewport": {"width": 1440, "height": 900}, "role": "staff"},
    {"id": "mob-staff", "viewport": {"width": 390, "height": 844}, "role": "staff"},
    {"id": "desk-owner", "viewport": {"width": 1440, "height": 900}, "role": "owner"},
]

SCOPES = [
    {
        "scope_id": "DM-01-normal-auto-recognize",
        "source": "ENG-02 AC1",
        "need_job": True,
        "image": "711",
        "assert_keys": ["job_status"],
    },
    {
        "scope_id": "DM-02-job-terminal-done",
        "source": "ENG-02 AC2 / DEP-MODEL job done",
        "need_job": True,
        "image": "711",
        "assert_keys": ["job_status", "supplier_name", "date", "total_amount"],
    },
    {
        "scope_id": "DM-03-prefill-header-fields",
        "source": "ENG-02 AC2 prefill header",
        "need_job": True,
        "image": "711",
        "assert_keys": ["supplier_name", "date", "total_amount"],
    },
    {
        "scope_id": "DM-04-prefill-line-items",
        "source": "ENG-02 AC2 line items",
        "need_job": True,
        "image": "711",
        "assert_keys": ["items"],
    },
    {
        "scope_id": "DM-05-second-image-heic-done",
        "source": "real model on HEIC-derived JPEG (assets)",
        "need_job": True,
        "image": "heic",
        "assert_keys": ["job_status"],
    },
    {
        "scope_id": "DM-06-ai-prefill-badge-contract",
        "source": "ENG-03 AC3 real AI prefill must show badge (UI later)",
        "need_job": True,
        "image": "711",
        "assert_keys": ["job_status", "doc_form_not_manual"],
    },
    {
        "scope_id": "DM-07-manual-entry-save",
        "source": "ENG-03 AC2 manual fill+save",
        "need_job": False,
        "image": None,
        "assert_keys": ["manual_save_http", "db_receipt"],
    },
    {
        "scope_id": "DM-08-ai-then-human-save",
        "source": "ENG-02/03 AI prefill then human edit save + DB",
        "need_job": True,
        "image": "711",
        "assert_keys": ["save_http", "db_receipt"],
    },
    {
        "scope_id": "DM-09-original-assertions-711",
        "source": "read original/assertions vs live job for sample_711",
        "need_job": True,
        "image": "711",
        "assert_keys": ["original_match_supplier", "original_match_date", "original_match_total"],
    },
]


def redact(obj):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    s = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***REDACTED***", s)
    return json.loads(s)


def call(path, method="GET", body=None, headers=None, timeout=120):
    h = {"X-Role": "owner", "X-Tenant-Id": "default"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"raw": raw[:500]}
        return e.code, payload


def upload_async(img_path: Path, role="staff"):
    boundary = "----DepModelBoundary7MA4YWxk"
    content = img_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="receipt"; filename="{img_path.name}"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/api/upload?async=true",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Role": role,
            "X-Tenant-Id": "default",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, json.loads(resp.read().decode())


def poll_job(job_id, timeout_s=180):
    t0 = time.time()
    last = None
    polls = []
    while time.time() - t0 < timeout_s:
        code, job = call(f"/api/job/{job_id}", timeout=30)
        last = job
        st = (job or {}).get("job_status") or (job or {}).get("status")
        polls.append({"t": round(time.time() - t0, 1), "http": code, "job_status": st})
        if st in ("done", "error"):
            return last, polls
        time.sleep(2)
    return last, polls


def extract_result(job):
    if not job:
        return {}
    # shapes: job.result.data / job.result / job.data
    result = job.get("result") or {}
    if isinstance(result, dict) and "data" in result:
        data = result["data"]
    elif isinstance(result, dict):
        data = result
    else:
        data = {}
    return data if isinstance(data, dict) else {}


def db_receipt(rid):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # Schema uses receipt_date (not date) — same harness fix Dev recorded for DM-07/08
    row = con.execute(
        "SELECT id, supplier_name, receipt_date, total_amount, status, doc_form, version FROM receipts WHERE id=?",
        (rid,),
    ).fetchone()
    items = con.execute(
        "SELECT name, quantity, unit, unit_price, amount FROM receipt_items WHERE receipt_id=? ORDER BY id",
        (rid,),
    ).fetchall()
    con.close()
    if not row:
        return None
    rec = dict(row)
    # expose both keys for assertions/readability
    rec["date"] = rec.get("receipt_date")
    return {
        "receipt": rec,
        "items": [dict(i) for i in items],
    }


def norm_supplier(s):
    return re.sub(r"[\s\-]", "", (s or "")).upper()


def run_job(image_key, role="staff"):
    img = IMG_711 if image_key == "711" else IMG_HEIC
    attempts = []
    for attempt in range(1, 4):
        t0 = time.time()
        try:
            code, up = upload_async(img, role=role)
        except Exception as e:
            attempts.append({
                "attempt": attempt,
                "phase": "upload",
                "error": f"{type(e).__name__}: {str(e)[:200]}",
                "elapsed_s": round(time.time() - t0, 2),
            })
            time.sleep(2)
            continue
        job_id = up.get("job_id")
        rid = up.get("receipt_id")
        job, polls = poll_job(job_id)
        st = (job or {}).get("job_status")
        data = extract_result(job)
        attempts.append({
            "attempt": attempt,
            "upload_http": code,
            "upload": {k: up.get(k) for k in ("status", "job_id", "receipt_id", "image_url")},
            "job_status": st,
            "polls": polls,
            "error_msg": (job or {}).get("error_msg") or (job or {}).get("msg"),
            "result_summary": {
                "supplier_name": data.get("supplier_name"),
                "date": data.get("date"),
                "total_amount": data.get("total_amount"),
                "doc_form": data.get("doc_form"),
                "items_n": len(data.get("items") or []),
            },
            "elapsed_s": round(time.time() - t0, 2),
        })
        if st == "done":
            return {
                "ok": True,
                "attempts": attempts,
                "job_id": job_id,
                "receipt_id": rid,
                "job": redact(job),
                "data": redact(data),
                "attempt_count": attempt,
            }
        time.sleep(2)
    return {"ok": False, "attempts": attempts, "attempt_count": len(attempts)}


def manual_save():
    # create blank manual via API if available; else save_edited with new receipt
    # Prefer POST that creates manual entry
    code, body = call("/api/receipts/manual", "POST", {
        "supplier_name": "Vendor_A",
        "date": "2026-10-17",
        "total_amount": 80.0,
        "settlement_type": "cash",
        "items": [
            {"name": "测试菜心", "quantity": 4, "unit": "斤", "unit_price": 20, "amount": 80}
        ],
    })
    if code >= 400:
        # fallback: upload is not wanted; try save_edited without id (create)
        code, body = call("/api/save_edited", "POST", {
            "supplier_name": "Vendor_A",
            "date": "2026-10-17",
            "sheet_name": "2026-10",
            "total_amount": 80.0,
            "settlement_type": "cash",
            "doc_form": "manual_entry",
            "items": [
                {"name": "测试菜心", "quantity": 4, "unit": "斤", "unit_price": 20, "amount": 80}
            ],
        })
    return code, body


def ai_then_save(job_bundle):
    data = job_bundle["data"]
    rid = job_bundle["receipt_id"]
    items = []
    for it in (data.get("items") or []):
        items.append({k: it.get(k) for k in ("name", "quantity", "unit", "unit_price", "amount", "sku_id") if k in it or True})
        items[-1] = {k: v for k, v in {
            "name": it.get("name"),
            "quantity": it.get("quantity"),
            "unit": it.get("unit"),
            "unit_price": it.get("unit_price"),
            "amount": it.get("amount"),
        }.items()}
    # human edit: bump total note via settlement
    payload = {
        "receipt_id": rid,
        "supplier_name": data.get("supplier_name") or "Vendor_A",
        "date": data.get("date") or "2016-10-01",
        "sheet_name": data.get("sheet_name") or "2016-10",
        "total_amount": data.get("total_amount"),
        "settlement_type": data.get("settlement_type") or "cash",
        "doc_form": data.get("doc_form") or "printed_delivery_note",
        "items": items,
        "version": data.get("version") or 1,
    }
    code, body = call("/api/save_edited", "POST", payload)
    return code, body, payload


def evaluate(scope, job_bundle=None, extra=None):
    checks = {}
    data = (job_bundle or {}).get("data") or {}
    keys = scope["assert_keys"]
    if "job_status" in keys:
        checks["job_status"] = (job_bundle or {}).get("ok") is True
    if "supplier_name" in keys:
        checks["supplier_name"] = bool(data.get("supplier_name"))
    if "date" in keys:
        checks["date"] = bool(data.get("date"))
    if "total_amount" in keys:
        ta = data.get("total_amount")
        checks["total_amount"] = ta is not None and float(ta) >= 0
    if "items" in keys:
        checks["items"] = len(data.get("items") or []) >= 1
    if "doc_form_not_manual" in keys:
        df = (data.get("doc_form") or "")
        checks["doc_form_not_manual"] = df != "manual_entry" and bool(df or data.get("supplier_name"))
    if "manual_save_http" in keys and extra:
        checks["manual_save_http"] = 200 <= int(extra.get("http", 0)) < 300
    if "save_http" in keys and extra:
        checks["save_http"] = 200 <= int(extra.get("http", 0)) < 300
    if "db_receipt" in keys and extra:
        checks["db_receipt"] = extra.get("db") is not None
    if "original_match_supplier" in keys:
        checks["original_match_supplier"] = (
            norm_supplier(data.get("supplier_name")) == norm_supplier(ORIG_711["supplier_name"])
            or norm_supplier(ORIG_711["supplier_name"]) in norm_supplier(data.get("supplier_name"))
            or norm_supplier(data.get("supplier_name")) in norm_supplier(ORIG_711["supplier_name"])
        )
    if "original_match_date" in keys:
        checks["original_match_date"] = str(data.get("date") or "") == str(ORIG_711["date"])
    if "original_match_total" in keys:
        try:
            checks["original_match_total"] = abs(float(data.get("total_amount")) - float(ORIG_711["total_amount"])) < 0.01
        except Exception:
            checks["original_match_total"] = False
    ok = all(checks.values()) if checks else False
    return ok, checks


def main():
    out_dir = ART / "evidence" / "scopes"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    # Cache jobs per image to avoid redundant model calls when possible,
    # but AC wants per-scope real job — still do dedicated jobs for need_job scopes.
    for scope in SCOPES:
        sid = scope["scope_id"]
        print(f"=== SCOPE {sid} ===", flush=True)
        rec = {
            "scope_id": sid,
            "source": scope["source"],
            "triples_planned": [t["id"] for t in TRIPLES],
            "attempts": 0,
            "status": "BLOCKED",
            "checks": {},
            "evidence_paths": [],
            "notes": [],
        }
        job_bundle = None
        extra = {}
        try:
            if scope["need_job"]:
                job_bundle = run_job(scope["image"])
                rec["attempts"] = job_bundle.get("attempt_count", 0)
                rec["job_bundle_summary"] = {
                    "ok": job_bundle.get("ok"),
                    "attempt_count": job_bundle.get("attempt_count"),
                    "receipt_id": job_bundle.get("receipt_id"),
                    "job_id": job_bundle.get("job_id"),
                    "result_summary": (job_bundle.get("attempts") or [{}])[-1].get("result_summary"),
                    "attempts": job_bundle.get("attempts"),
                }
                jp = out_dir / f"{sid}.job.json"
                jp.write_text(json.dumps(redact(job_bundle), ensure_ascii=False, indent=2))
                rec["evidence_paths"].append(str(jp.relative_to(ART)))
                if not job_bundle.get("ok"):
                    rec["status"] = "BLOCKED"
                    rec["notes"].append("real job did not reach done after attempts")
                    results.append(rec)
                    (out_dir / f"{sid}.result.json").write_text(
                        json.dumps(rec, ensure_ascii=False, indent=2)
                    )
                    continue
            else:
                rec["attempts"] = 1

            if sid == "DM-07-manual-entry-save":
                code, body = manual_save()
                rid = None
                if isinstance(body, dict):
                    rid = body.get("receipt_id") or (body.get("data") or {}).get("id") or (body.get("data") or {}).get("receipt_id")
                db = db_receipt(rid) if rid else None
                extra = {"http": code, "body": redact(body), "db": db}
                mp = out_dir / f"{sid}.manual.json"
                mp.write_text(json.dumps(extra, ensure_ascii=False, indent=2))
                rec["evidence_paths"].append(str(mp.relative_to(ART)))
                rec["attempts"] = 1 if code < 500 else 2

            if sid == "DM-08-ai-then-human-save":
                code, body, payload = ai_then_save(job_bundle)
                rid = job_bundle.get("receipt_id")
                db = db_receipt(rid)
                extra = {"http": code, "body": redact(body), "payload_keys": list(payload.keys()), "db": db}
                sp = out_dir / f"{sid}.save.json"
                sp.write_text(json.dumps(extra, ensure_ascii=False, indent=2))
                rec["evidence_paths"].append(str(sp.relative_to(ART)))

            if sid == "DM-09-original-assertions-711":
                # stash comparison
                cmp = {
                    "original": ORIG_711,
                    "live": {
                        "supplier_name": (job_bundle.get("data") or {}).get("supplier_name"),
                        "date": (job_bundle.get("data") or {}).get("date"),
                        "total_amount": (job_bundle.get("data") or {}).get("total_amount"),
                        "items_n": len((job_bundle.get("data") or {}).get("items") or []),
                    },
                }
                cp = out_dir / f"{sid}.assertions-compare.json"
                cp.write_text(json.dumps(cmp, ensure_ascii=False, indent=2))
                rec["evidence_paths"].append(str(cp.relative_to(ART)))
                rec["notes"].append("original/assertions from demo/samples/20161001_711_thermal_receipt.json")

            ok, checks = evaluate(scope, job_bundle, extra)
            rec["checks"] = checks
            rec["status"] = "PASS" if ok else "FAIL"
            # For dependency gate: if job path blocked, stay BLOCKED
            if scope["need_job"] and job_bundle and not job_bundle.get("ok"):
                rec["status"] = "BLOCKED"
        except Exception as e:
            rec["status"] = "BLOCKED"
            rec["attempts"] = max(rec.get("attempts") or 0, 1)
            rec["notes"].append(f"{type(e).__name__}: {str(e)[:300]}")
        results.append(rec)
        (out_dir / f"{sid}.result.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2))
        print(f"  -> {rec['status']} checks={rec['checks']} attempts={rec['attempts']}", flush=True)

    summary = {
        "card": "DEP-MODEL",
        "catalog_source": "INFERRED_FROM_ENG02_ENG03_PARTIAL (original UAG catalog not in cloud clone)",
        "triples": TRIPLES,
        "scopes": results,
        "counts": {
            "PASS": sum(1 for r in results if r["status"] == "PASS"),
            "FAIL": sum(1 for r in results if r["status"] == "FAIL"),
            "BLOCKED": sum(1 for r in results if r["status"] == "BLOCKED"),
        },
    }
    (ART / "evidence" / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print("SUMMARY", summary["counts"], flush=True)
    return 0 if summary["counts"]["FAIL"] == 0 and summary["counts"]["BLOCKED"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

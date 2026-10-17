# -*- coding: utf-8 -*-
"""UAT-014 live probe: write HKID-bearing LLM log via production record_llm_call,
read back from isolate DB, assert masking. Never prints plaintext HKID/phone/key.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ART = Path(os.environ["ART"])
DB_PATH = os.environ["DB_PATH"]
BASE = os.environ.get("BASE", "")
PORT = os.environ.get("PORT", "")

# Synthetic PII tokens used only in-process; evidence stores redacted forms only.
HKID_A = "A" + "123456" + "(7)"
HKID_B = "K" + "1234567"
PHONE = "+852 " + "9123" + " " + "4567"
MAINLAND = "110101" + "19900307" + "2345"
FAKE_KEY = "sk-" + ("abcdefghijklmnopqrstuv")

RAW_MARKER = "UAT014_LIVE_PROBE"
PROMPT_PLAIN = (
    f"{RAW_MARKER} customer {HKID_A} phone {PHONE} "
    f"alt {HKID_B} id {MAINLAND} key {FAKE_KEY}"
)
OUTPUT_PLAIN = f"{RAW_MARKER} vendor note {HKID_A} contact {PHONE}"

# Expected redacted substrings (safe to put in evidence)
SAFE_TOKENS = ["[HKID_MASKED]", "110101********2345", "sk-***"]
FORBIDDEN_PLAIN = [HKID_A, HKID_B, "9123 4567", "91234567", MAINLAND, FAKE_KEY]


def _redact_for_evidence(s: str) -> str:
    out = s
    for p in FORBIDDEN_PLAIN:
        if p in out:
            out = out.replace(p, "<REDACTED_PLAINTEXT>")
    return out


def main() -> int:
    # Ensure demo app import path
    root = Path("/workspace/receipt-agent-interview")
    sys.path.insert(0, str(root / "demo"))
    sys.path.insert(0, str(root))

    os.environ["DB_PATH"] = DB_PATH

    from app.services.llm_logger import record_llm_call, sanitize_text
    from app import db as app_db

    # Unit-level sanitize (same function production logging uses)
    sanitized_preview = sanitize_text(PROMPT_PLAIN)
    sanitize_ok = all(t in sanitized_preview for t in ("[HKID_MASKED]", "sk-***")) and all(
        p not in sanitized_preview for p in (HKID_A, HKID_B, "9123 4567")
    )

    # Live write through production path into isolate DB
    log_id = record_llm_call(
        receipt_id=None,
        tenant_id="uat014-dev",
        trace_id="uat014-live-probe",
        leg="extract",
        attempt=1,
        engine="probe",
        model="uat014-mask-check",
        prompt_messages=[{"role": "user", "content": PROMPT_PLAIN}],
        raw_output=OUTPUT_PLAIN,
        token_usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        status="success",
        error_msg="",
    )

    # Read back via sqlite (authoritative on-disk evidence)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, prompt_messages_json, raw_output, error_msg, model, tenant_id, trace_id "
        "FROM llm_call_log WHERE id = ?",
        (log_id,),
    ).fetchone()
    conn.close()

    checks = {
        "health_base": BASE,
        "port": PORT,
        "db_path": DB_PATH,
        "log_id": log_id,
        "row_found": row is not None,
        "sanitize_unit_ok": sanitize_ok,
        "sanitize_preview_redacted": _redact_for_evidence(sanitized_preview),
    }

    if row is None:
        checks["verdict"] = "FAIL"
        checks["reason"] = "llm_call_log row missing after record_llm_call"
        (ART / "evidence" / "hkid-live-probe.json").write_text(
            json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"verdict": "FAIL", "reason": checks["reason"]}))
        return 1

    prompt_json = row["prompt_messages_json"] or ""
    raw_out = row["raw_output"] or ""
    blob = f"{prompt_json}\n{raw_out}"

    plaintext_leaks = [p for p in FORBIDDEN_PLAIN if p in blob]
    has_mask_tokens = ("[HKID_MASKED]" in blob) and ("sk-***" in blob or "sk-***" in prompt_json)

    checks.update(
        {
            "model": row["model"],
            "tenant_id": row["tenant_id"],
            "trace_id": row["trace_id"],
            "prompt_redacted_sample": _redact_for_evidence(prompt_json[:500]),
            "raw_output_redacted_sample": _redact_for_evidence(raw_out[:300]),
            "contains_HKID_MASKED": "[HKID_MASKED]" in blob,
            "contains_sk_mask": "sk-***" in blob,
            "contains_mainland_mask": "110101********2345" in blob or "********" in blob,
            "plaintext_leak_count": len(plaintext_leaks),
            # Never list the actual leaked values
            "plaintext_leaks_kinds": [
                "hkid_paren" if p == HKID_A else
                "hkid_noparen" if p == HKID_B else
                "phone" if "9123" in p else
                "mainland_id" if p == MAINLAND else
                "api_key" if p.startswith("sk-") else "other"
                for p in plaintext_leaks
            ],
            "has_mask_tokens": has_mask_tokens,
        }
    )

    ok = (
        log_id is not None
        and sanitize_ok
        and len(plaintext_leaks) == 0
        and checks["contains_HKID_MASKED"]
        and checks["contains_sk_mask"]
    )
    checks["verdict"] = "PASS" if ok else "FAIL"
    if not ok:
        checks["reason"] = "masking incomplete or plaintext leaked into llm_call_log"

    out_path = ART / "evidence" / "hkid-live-probe.json"
    out_path.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": checks["verdict"], "log_id": log_id, "path": str(out_path)}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Build synthetic evalset with sample U001 (draft GT) under EVALSET_DIR."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

from PIL import Image

EVALSET_DIR = Path(os.environ["EVALSET_DIR"])
COLS = [
    "sample_id", "image", "split", "doc_form", "layout_type", "supplier_id",
    "gt_status", "gt_source_model", "src_sha1", "short_side",
]

CANDIDATE = {
    "supplier_name": "U001 Synth Vendor",
    "date": "2026-10-15",
    "total_amount": 40.0,
    "payment_marked": False,
    "payment_evidence": "",
    "currency": "HKD",
    "discount_amount": 0.0,
    "deposit_amount": 0.0,
    "delivery_fee": 0.0,
    "service_fee": 0.0,
    "tax_amount": 0.0,
    "rounding_adjustment": 0.0,
    "adjustment_notes": [],
    "items": [
        {"name": "测试白菜", "qty": 2.0, "unit": "斤", "unit_price": 10.0, "amount": 20.0},
        {"name": "测试萝卜", "qty": 1.0, "unit": "个", "unit_price": 20.0, "amount": 20.0},
    ],
    "gt_status": "draft",
    "gt_source_model": "uat011/synth",
    "gt_reviewed_by": None,
    "gt_reviewed_at": None,
    "doc_form": "printed_delivery_note",
}


def write_sample(sid: str):
    receipts = EVALSET_DIR / "receipts"
    expected = EVALSET_DIR / "expected"
    receipts.mkdir(parents=True, exist_ok=True)
    expected.mkdir(parents=True, exist_ok=True)
    img_rel = f"receipts/{sid}.png"
    img_path = EVALSET_DIR / img_rel
    Image.new("RGB", (900, 640), "white").save(str(img_path))
    sha = hashlib.sha1(img_path.read_bytes()).hexdigest()
    body = dict(CANDIDATE)
    body["gt_status"] = "draft"
    (expected / f"{sid}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "sample_id": sid,
        "image": img_rel,
        "split": "test",
        "doc_form": "printed_delivery_note",
        "layout_type": "table",
        "supplier_id": "U001V",
        "gt_status": "draft",
        "gt_source_model": "uat011/synth",
        "src_sha1": sha,
        "short_side": "640",
    }


def main():
    EVALSET_DIR.mkdir(parents=True, exist_ok=True)
    rows = [write_sample("U001"), write_sample("U002")]
    with open(EVALSET_DIR / "manifest.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    meta = {"samples": [r["sample_id"] for r in rows], "evalset_dir": str(EVALSET_DIR)}
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()

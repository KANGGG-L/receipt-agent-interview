# -*- coding: utf-8 -*-
"""Unit tests for Golden Board GT Filter feature:
Backend data linkage and filtering between GT confirmation (evalset / eval_candidate)
and golden benchmark set (/api/admin/golden-samples).
"""

import csv
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.main import app


_VALID_GT = {
    "supplier_name": "Test Supplier Ltd",
    "date": "2026-09-01",
    "total_amount": 100.0,
    "items": [
        {"name": "Item A", "qty": 2.0, "unit": "kg", "unit_price": 50.0, "amount": 100.0}
    ],
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
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_golden_gt.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()

    evalset_dir = str(tmp_path / "evalsets")
    os.makedirs(os.path.join(evalset_dir, "expected"), exist_ok=True)
    os.makedirs(os.path.join(evalset_dir, "receipts"), exist_ok=True)
    manifest_file = os.path.join(evalset_dir, "manifest.csv")
    with open(manifest_file, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "sample_id", "image", "split", "doc_form", "layout_type", "supplier_id",
            "gt_status", "gt_source_model", "src_sha1", "short_side"
        ])
    monkeypatch.setenv("EVALSET_DIR", evalset_dir)

    yield {
        "db_path": test_db_path,
        "evalset_dir": evalset_dir,
    }

    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


@pytest.fixture
def client():
    return TestClient(app)


def _admin_headers(tenant_id="default"):
    return {
        "X-Role": "admin",
        "X-Email": "admin@demo.hk",
        "X-Tenant-Id": tenant_id,
    }


def _make_receipt(supplier_name="", total_amount=0.0, doc_form="printed_delivery_note", tenant_id="default"):
    rid = db.create_receipt(supplier_name=supplier_name, tenant_id=tenant_id)
    db.update_receipt(rid, total_amount=total_amount, doc_form=doc_form)
    return rid


def _add_manifest_entry(evalset_dir, sample_id, split="test", gt_status="draft"):
    manifest_file = os.path.join(evalset_dir, "manifest.csv")
    with open(manifest_file, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            sample_id, f"receipts/{sample_id}.png", split, "printed_delivery_note",
            "table", "V001", gt_status, "human", "dummy_sha1", "1000"
        ])


def _write_expected_json(evalset_dir, sample_id, data):
    p = os.path.join(evalset_dir, "expected", f"{sample_id}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def test_golden_samples_gt_linkage_and_scopes(client, isolated_env):
    """Test /api/admin/golden-samples linkage with eval_candidates and evalset,
    and verify scope filtering: all, golden, gt_confirmed, gt_pending.
    """
    evalset_dir = isolated_env["evalset_dir"]

    # 1. Create test receipts:
    # r1: regular unconfirmed receipt
    r1_id = _make_receipt(supplier_name="Supplier 1", total_amount=10.0)
    
    # r2: curated golden receipt, not in GT
    r2_id = _make_receipt(supplier_name="Supplier 2", total_amount=20.0)
    db.set_golden_sample(r2_id, 1, tenant_id="default")

    # r3: receipt with candidate status='pending'
    r3_id = _make_receipt(supplier_name="Supplier 3", total_amount=30.0)
    cand3_id, _ = db.create_eval_candidate(r3_id, reason="low_confidence", tenant_id="default")

    # r4: receipt with candidate status='promoted_to_val'
    r4_id = _make_receipt(supplier_name="Supplier 4", total_amount=40.0)
    cand4_id, _ = db.create_eval_candidate(r4_id, reason="user_edit", tenant_id="default")
    db.set_eval_candidate_status(cand4_id, "promoted_to_val")

    # r5: receipt with candidate status='rejected'
    r5_id = _make_receipt(supplier_name="Supplier 5", total_amount=50.0)
    cand5_id, _ = db.create_eval_candidate(r5_id, reason="gate_reject", tenant_id="default")
    db.set_eval_candidate_status(cand5_id, "rejected")

    # r6: receipt confirmed in evalset
    r6_id = _make_receipt(supplier_name="Supplier 6", total_amount=60.0)
    _add_manifest_entry(evalset_dir, "S001", split="test", gt_status="confirmed")
    gt6 = dict(_VALID_GT)
    gt6["source_receipt_id"] = r6_id
    gt6["gt_status"] = "confirmed"
    _write_expected_json(evalset_dir, "S001", gt6)

    # --- Test scope=all ---
    resp = client.get("/api/admin/golden-samples?scope=all", headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["total"] == 6
    assert body["golden_total"] == 1
    assert body["gt_confirmed_total"] == 1
    assert body["gt_pending_total"] == 2
    assert body["scope"] == "all"

    items_by_id = {it["id"]: it for it in body["items"]}
    
    # Assert r1
    assert items_by_id[r1_id]["gt_status"] == "none"
    assert items_by_id[r1_id]["gt_label"] == "未确权"
    assert items_by_id[r1_id]["sample_id"] is None
    assert items_by_id[r1_id]["eval_candidate_id"] is None
    assert items_by_id[r1_id]["is_golden_sample"] is False

    # Assert r2
    assert items_by_id[r2_id]["gt_status"] == "none"
    assert items_by_id[r2_id]["gt_label"] == "未确权"
    assert items_by_id[r2_id]["is_golden_sample"] is True

    # Assert r3 (pending)
    assert items_by_id[r3_id]["gt_status"] == "pending"
    assert items_by_id[r3_id]["gt_label"] == "待抽检"
    assert items_by_id[r3_id]["eval_candidate_id"] == cand3_id

    # Assert r4 (promoted)
    assert items_by_id[r4_id]["gt_status"] == "promoted"
    assert items_by_id[r4_id]["gt_label"] == "已晋升评测集"
    assert items_by_id[r4_id]["eval_candidate_id"] == cand4_id

    # Assert r5 (rejected)
    assert items_by_id[r5_id]["gt_status"] == "rejected"
    assert items_by_id[r5_id]["gt_label"] == "已驳回"
    assert items_by_id[r5_id]["eval_candidate_id"] == cand5_id

    # Assert r6 (confirmed)
    assert items_by_id[r6_id]["gt_status"] == "confirmed"
    assert items_by_id[r6_id]["gt_label"] == "已确权"
    assert items_by_id[r6_id]["sample_id"] == "S001"

    # --- Test scope=golden ---
    resp_golden = client.get("/api/admin/golden-samples?scope=golden", headers=_admin_headers())
    assert resp_golden.status_code == 200
    body_golden = resp_golden.json()
    assert body_golden["scope"] == "golden"
    assert body_golden["total"] == 1
    assert len(body_golden["items"]) == 1
    assert body_golden["items"][0]["id"] == r2_id

    # --- Test scope=gt_confirmed ---
    resp_conf = client.get("/api/admin/golden-samples?scope=gt_confirmed", headers=_admin_headers())
    assert resp_conf.status_code == 200
    body_conf = resp_conf.json()
    assert body_conf["scope"] == "gt_confirmed"
    assert body_conf["total"] == 1
    assert len(body_conf["items"]) == 1
    assert body_conf["items"][0]["id"] == r6_id
    assert body_conf["items"][0]["gt_status"] == "confirmed"

    # --- Test scope=gt_pending ---
    resp_pend = client.get("/api/admin/golden-samples?scope=gt_pending", headers=_admin_headers())
    assert resp_pend.status_code == 200
    body_pend = resp_pend.json()
    assert body_pend["scope"] == "gt_pending"
    assert body_pend["total"] == 2
    pend_ids = {it["id"] for it in body_pend["items"]}
    assert pend_ids == {r3_id, r4_id}


def test_confirm_sample_promotes_source_receipt_to_golden(client, isolated_env):
    """Test confirming a sample in evalset automatically sets is_golden_sample = 1
    for the source receipt.
    """
    evalset_dir = isolated_env["evalset_dir"]

    # Create receipt not in golden set
    rid = _make_receipt(supplier_name="Auto Golden Supplier", total_amount=88.8)
    receipt = db.get_receipt_row(rid)
    assert (getattr(receipt, "is_golden_sample", 0) or 0) == 0

    # Put a draft sample in evalset pointing to this receipt
    sample_id = "S050"
    _add_manifest_entry(evalset_dir, sample_id, split="val", gt_status="draft")
    gt_draft = dict(_VALID_GT)
    gt_draft["gt_status"] = "draft"
    gt_draft["source_receipt_id"] = rid
    _write_expected_json(evalset_dir, sample_id, gt_draft)

    # Confirm the sample via API
    confirm_payload = {
        "gt": _VALID_GT,
        "confirm_blank_total": False,
    }
    resp = client.post(f"/api/evalset/sample/{sample_id}/confirm", json=confirm_payload, headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["data"]["gt_status"] == "confirmed"

    # Verify that the source receipt is now promoted to golden set!
    updated_receipt = db.get_receipt_row(rid)
    assert getattr(updated_receipt, "is_golden_sample", 0) == 1

    # Verify golden board reflects this under scope=golden and scope=gt_confirmed
    resp_board = client.get("/api/admin/golden-samples?scope=golden", headers=_admin_headers())
    assert resp_board.status_code == 200
    items = resp_board.json()["items"]
    matching = [it for it in items if it["id"] == rid]
    assert len(matching) == 1
    assert matching[0]["is_golden_sample"] is True
    assert matching[0]["gt_status"] == "confirmed"
    assert matching[0]["gt_label"] == "已确权"
    assert matching[0]["sample_id"] == sample_id


def test_linkage_via_source_candidate_id_and_scope_fallback(client, isolated_env):
    """Test candidate matching via source_candidate_id and invalid scope fallback to 'all'."""
    evalset_dir = isolated_env["evalset_dir"]

    rid = _make_receipt(supplier_name="Candidate Source Supplier", total_amount=123.0)
    cid, _ = db.create_eval_candidate(rid, reason="user_edit", tenant_id="default")
    db.set_eval_candidate_status(cid, "promoted_to_test")

    # Sample json has source_candidate_id, but NO source_receipt_id
    sample_id = "S088"
    _add_manifest_entry(evalset_dir, sample_id, split="test", gt_status="confirmed")
    gt_data = dict(_VALID_GT)
    gt_data["gt_status"] = "confirmed"
    gt_data["source_candidate_id"] = cid
    _write_expected_json(evalset_dir, sample_id, gt_data)

    # Call with unknown scope -> fallback to all
    resp = client.get("/api/admin/golden-samples?scope=unknown_scope", headers=_admin_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "all"
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == rid
    assert item["gt_status"] == "confirmed"
    assert item["gt_label"] == "已确权"
    assert item["sample_id"] == sample_id
    assert item["eval_candidate_id"] == cid


# -*- coding: utf-8 -*-
"""Unit tests for evalset GT data linkage:
Verifies that GT 抽检确权评测集与单据库（receipts 表）和黄金基准集看板（/api/admin/golden-samples）
完整联通，包括已确权样本在 scope=gt_confirmed 下的展示与 gt_confirmed_total 统计。
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
from app.services.evalset_linkage import (
    sync_evalset_receipts_linkage,
    link_or_create_receipt_for_sample,
)


@pytest.fixture
def client():
    return TestClient(app)


def _admin_headers(tenant_id="default"):
    return {
        "X-Role": "admin",
        "X-Email": "admin@demo.hk",
        "X-Tenant-Id": tenant_id,
    }


def test_sync_evalset_receipts_linkage_lifecycle(tmp_path, monkeypatch):
    """测试评测集同步模块生命周期：
    1. 自动创建 receipts 记录并回填 expected JSON 的 source_receipt_id；
    2. 已确权样本置位 is_golden_sample=1，草稿置位 is_golden_sample=0；
    3. 幂等性：二次运行无新单据创建。
    """
    old_db_path = db.DB_PATH
    test_db = str(tmp_path / "lifecycle.db")
    monkeypatch.setenv("DB_PATH", test_db)
    db.DB_PATH = test_db
    db._make_engine()

    try:
        eval_dir = str(tmp_path / "evalsets")
        os.makedirs(os.path.join(eval_dir, "expected"), exist_ok=True)
        os.makedirs(os.path.join(eval_dir, "receipts"), exist_ok=True)

        # 准备 manifest
        manifest_path = os.path.join(eval_dir, "manifest.csv")
        with open(manifest_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "sample_id", "image", "split", "doc_form", "layout_type", "supplier_id",
                "gt_status", "gt_source_model", "src_sha1", "short_side"
            ])
            w.writerow(["S101", "receipts/S101.png", "test", "printed_delivery_note", "table", "V001", "confirmed", "human", "h1", "1000"])
            w.writerow(["S102", "receipts/S102.png", "test", "printed_delivery_note", "table", "V002", "draft", "human", "h2", "1000"])

        # 准备 expected JSON（故意不含 source_receipt_id）
        with open(os.path.join(eval_dir, "expected", "S101.json"), "w", encoding="utf-8") as f:
            json.dump({
                "supplier_name": "Supplier Confirmed",
                "date": "2026-09-01",
                "total_amount": 188.0,
                "items": [{"name": "Item 1", "qty": 2, "unit_price": 94, "amount": 188}],
                "gt_status": "confirmed",
            }, f)

        with open(os.path.join(eval_dir, "expected", "S102.json"), "w", encoding="utf-8") as f:
            json.dump({
                "supplier_name": "Supplier Draft",
                "date": "2026-09-02",
                "total_amount": 99.0,
                "items": [{"name": "Item 2", "qty": 1, "unit_price": 99, "amount": 99}],
                "gt_status": "draft",
            }, f)

        # 第一次同步
        stats1 = sync_evalset_receipts_linkage(evalset_dir=eval_dir, tenant_id="default")
        assert stats1["total"] == 2
        assert stats1["linked"] == 2
        assert stats1["created"] == 2
        assert stats1["confirmed"] == 1

        # 检查 expected JSON 是否被回填 source_receipt_id
        with open(os.path.join(eval_dir, "expected", "S101.json"), encoding="utf-8") as f:
            gt101 = json.load(f)
            s101_rid = gt101.get("source_receipt_id")
            assert s101_rid is not None

        with open(os.path.join(eval_dir, "expected", "S102.json"), encoding="utf-8") as f:
            gt102 = json.load(f)
            s102_rid = gt102.get("source_receipt_id")
            assert s102_rid is not None

        # 检查数据库中的 receipts 记录及其黄金基准集标记
        r101 = db.get_receipt_row(s101_rid)
        assert r101 is not None
        assert r101.supplier_name == "Supplier Confirmed"
        assert r101.total_amount == 188.0
        assert r101.is_golden_sample == 1

        r102 = db.get_receipt_row(s102_rid)
        assert r102 is not None
        assert r102.supplier_name == "Supplier Draft"
        assert r102.is_golden_sample == 0

        # 第二次同步（验证幂等性）
        stats2 = sync_evalset_receipts_linkage(evalset_dir=eval_dir, tenant_id="default")
        assert stats2["total"] == 2
        assert stats2["linked"] == 2
        assert stats2["created"] == 0
        assert stats2["confirmed"] == 1
    finally:
        db.DB_PATH = old_db_path
        db._make_engine()


def test_real_evalset_confirmed_samples_appear_in_golden_board(client):
    resp = client.get("/api/admin/golden-samples?scope=gt_confirmed", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "success"
    assert data["scope"] == "gt_confirmed"
    assert data["gt_confirmed_total"] >= 5
    assert data["total"] == data["gt_confirmed_total"]

    returned_sids = {it["sample_id"] for it in data["items"] if it.get("sample_id")}
    required_sids = {"S003", "S005", "S026", "S028", "S030"}
    assert required_sids.issubset(returned_sids), f"Missing required sids in gt_confirmed: {required_sids - returned_sids}"

    for it in data["items"]:
        assert it["gt_status"] == "confirmed"
        assert it["gt_label"] == "已确权"
        assert it["sample_id"] in returned_sids


def test_scope_gt_pending_includes_draft_evalset_samples(client):
    """测试 scope=gt_pending（待确权候选池）包含评测集中的草稿样本。"""
    resp = client.get("/api/admin/golden-samples?scope=gt_pending", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["scope"] == "gt_pending"
    assert data["gt_pending_total"] >= 28

    pending_sids = {it["sample_id"] for it in data["items"] if it.get("sample_id")}
    # 验证若干已知草稿样本出现在待确权候选池
    sample_drafts = {"S033", "S039", "S043", "S154"}
    assert sample_drafts.issubset(pending_sids), f"Missing drafts in gt_pending: {sample_drafts - pending_sids}"

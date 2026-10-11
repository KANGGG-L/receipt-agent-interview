# -*- coding: utf-8 -*-
"""B2：权限矩阵不回退（S1-P1-06）。"""

import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

_SCAN_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "scripts", "permission_scan.py")
_spec = importlib.util.spec_from_file_location("permission_scan", _SCAN_PATH)
permission_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(permission_scan)

STAFF = {"X-Role": "staff"}
OWNER = {"X-Role": "owner"}


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_matrix_file_matches_code():
    """代码里任何端点的角色门槛变了，矩阵文件必须同步（python scripts/permission_scan.py --write）。"""
    assert os.path.exists(permission_scan.MATRIX_PATH), "缺少 docs/permission-matrix.md"
    with open(permission_scan.MATRIX_PATH, encoding="utf-8") as f:
        on_disk = f.read()
    assert on_disk == permission_scan.render(), \
        "docs/permission-matrix.md 与代码里的权限不一致，请运行 python demo/scripts/permission_scan.py --write"


def test_every_api_route_has_known_guard():
    rows = permission_scan.scan()
    assert len(rows) >= 150
    unknown = [r for r in rows if r[2] == "未知"]
    assert not unknown, unknown
    anon = {(r[0], r[1]) for r in rows if r[2] == permission_scan.ANON}
    # 只有登录、健康检查、浏览器直接取图/取凭证（签名链接）允许匿名
    assert anon <= {
        ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"), ("GET", "/api/auth/me"),
        ("GET", "/api/health"), ("GET", "/api/payment/voucher/{name}"),
        ("GET", "/api/receipt/{receipt_id}/image"), ("POST", "/api/csp-report"),
    }, anon


def test_money_endpoints_are_owner_only():
    rows = {(m, p): role for m, p, role, _n in permission_scan.scan()}
    must_owner = [
        ("GET", "/api/cost_report"), ("GET", "/api/cost_report/items"), ("GET", "/api/price_history/{sku_id}"),
        ("GET", "/api/dishes/cost_analysis"), ("GET", "/api/payments"), ("POST", "/api/payments"),
        ("GET", "/api/payables"), ("GET", "/api/reconciliation"),
        ("GET", "/api/settings/units"), ("PUT", "/api/settings/fx_rates"),
        ("GET", "/api/inventory/negative_stock"),
    ]
    for key in must_owner:
        assert rows.get(key) in ("owner", "admin"), (key, rows.get(key))


def test_export_cost_reports_are_owner_min_role():
    reports = {rid: role for rid, _t, role in permission_scan.report_rows()}
    for rid in ("spend_summary", "dish_cost_margin", "inventory_valuation",
                "inventory_price_anomaly", "receipts_itemized_ledger",
                "dish_ingredient_consumption", "inventory_stock_logs"):
        assert reports[rid] == "owner", rid
    assert reports["inventory_stocktake"] == "staff"


@pytest.mark.parametrize("report_id", [
    "spend_summary", "dish_cost_margin", "inventory_valuation", "inventory_price_anomaly",
    "receipts_itemized_ledger", "dish_ingredient_consumption", "inventory_stock_logs"])
def test_staff_cannot_preview_or_download_cost_reports(client, report_id):
    for path in ("/api/export/preview", "/api/export/download"):
        r = client.post(path, headers=STAFF, json={"report_id": report_id, "params": {}})
        assert r.status_code == 403, (path, report_id, r.status_code)
        r = client.post(path, headers=OWNER, json={"report_id": report_id, "params": {}})
        assert r.status_code == 200, (path, report_id, r.status_code, r.text[:200])


def test_staff_cannot_read_cost_analysis_or_price_trend(client):
    assert client.get("/api/dishes/cost_analysis", headers=STAFF).status_code == 403
    assert client.get("/api/price_history/1", headers=STAFF).status_code == 403
    assert client.get("/api/dishes/cost_analysis", headers=OWNER).status_code == 200

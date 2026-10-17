# -*- coding: utf-8 -*-
"""UAT-033: legacy 缺汇率单据被排除时，部门成本报表须露出缺汇率说明。

Positive:
1. legacy NFX（无 snapshot / 设置无汇率）→ API 排除 1 + count/warning；合计不含该单
2. 补汇率后提示清空、合计恢复
3. finally 清 fixture（租户级隔离即可）
4. 新单缺 FX 审批仍 422 FX_RATE_MISSING

Negative: legacy 缺汇率不进合计；不声称新单可绕过 FX。
Tenant B baseline 不变。
"""

import os
import re
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import db, units_store
from _b2_helpers import hdr, item, make_receipt, make_supplier, new_tenant


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _report(client, tn, **q):
    qs = "&".join("%s=%s" % kv for kv in q.items())
    r = client.get("/api/cost_report?" + qs, headers=hdr(tn))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "success"
    return body["data"]


def _make_legacy_nfx(client, tn, *, currency="NFX", total=100.0, date="2026-10-12",
                     supplier="Vendor_NFX_UAT033"):
    """Approve under a known FX, then strip snapshot + rewrite currency to unknown.

    Mimics historically approved foreign receipts that lost FX snapshot / rate.
    """
    sid = make_supplier(tn, supplier)
    put = client.put(
        "/api/settings/fx_rates",
        headers=hdr(tn),
        json={"rates": [{"currency": "CNY", "rate": 1.1, "confirmed": True}]},
    )
    assert put.status_code == 200, put.text
    rid, ap = make_receipt(
        client, tn, [item("NFX货", 1, "斤", total)], total,
        supplier=supplier, supplier_id=sid, currency="CNY",
        settlement="credit", approve=True, date=date,
    )
    assert ap is not None and ap.status_code == 200, getattr(ap, "text", ap)
    with units_store._engine().begin() as conn:
        conn.execute(
            text("DELETE FROM receipt_fx_snapshots WHERE receipt_id=:r AND tenant_id=:t"),
            {"r": rid, "t": tn},
        )
        conn.execute(
            text("DELETE FROM tenant_fx_rates WHERE tenant_id=:t AND currency=:c"),
            {"t": tn, "c": currency},
        )
        conn.execute(
            text("DELETE FROM tenant_fx_rates WHERE tenant_id=:t AND currency='CNY'"),
            {"t": tn},
        )
    db.update_receipt(rid, currency=currency)
    return rid, sid


def test_legacy_nfx_excluded_with_count_and_warning_then_restored(client):
    tn = new_tenant()
    tn_b = new_tenant()
    baseline_b = _report(client, tn_b, month="2026-10")
    assert baseline_b["month_total"] == 0.0
    assert baseline_b.get("unconverted_count", 0) == 0

    rid, _sid = _make_legacy_nfx(client, tn)
    # Also one HKD approved receipt so totals are not only-empty
    rid_hkd, ap_hkd = make_receipt(
        client, tn, [item("本地菜", 2, "斤", 10)], 20.0,
        supplier="Vendor_HKD_UAT033", currency="HKD", date="2026-10-12",
    )
    assert ap_hkd.status_code == 200, ap_hkd.text

    rep = _report(client, tn, month="2026-10")
    assert rep["month_total"] == 20.0
    assert abs(sum(d["total"] for d in rep["departments"]) + rep["unallocated"]["total"] - 20.0) < 0.005
    assert rep["unconverted_count"] == 1
    assert rep["missing_fx_count"] == 1
    assert len(rep["unconverted"]) == 1
    assert rep["unconverted"][0]["receipt_id"] == rid
    assert rep["unconverted"][0]["currency"] == "NFX"
    assert "汇率" in (rep.get("unconverted_warning") or "")
    assert "1" in (rep.get("unconverted_warning") or "")

    # Tenant B untouched
    after_b = _report(client, tn_b, month="2026-10")
    assert after_b["month_total"] == baseline_b["month_total"]
    assert after_b.get("unconverted_count", 0) == 0
    assert after_b.get("unconverted") in ([], None) or len(after_b.get("unconverted") or []) == 0

    # Add FX for NFX → notice clears, totals include legacy amount * rate
    put = client.put(
        "/api/settings/fx_rates",
        headers=hdr(tn),
        json={"rates": [{"currency": "NFX", "rate": 2.0, "confirmed": True}]},
    )
    assert put.status_code == 200, put.text
    rep2 = _report(client, tn, month="2026-10")
    assert rep2["month_total"] == 220.0  # 20 HKD + 100*2
    assert rep2["unconverted_count"] == 0
    assert rep2["missing_fx_count"] == 0
    assert not rep2.get("unconverted")
    assert (rep2.get("unconverted_warning") or "") == ""

    # New receipt missing FX still 422 on approve
    # save_edited 白名单会把未知币种压成 HKD，故先合法保存再改币种后审批（同 B2）
    rid_new, _ = make_receipt(
        client, tn, [item("新外币", 1, "斤", 50)], 50.0,
        supplier="Vendor_NEW_NOFX", currency="CNY", approve=False, date="2026-10-13",
    )
    assert units_store.get_fx_rate(tn, "XXX") is None
    db.update_receipt(rid_new, currency="XXX")
    row_new = db.get_receipt_row(rid_new)
    ap_new = client.post(
        "/api/receipt/%d/approve" % rid_new,
        headers=hdr(tn),
        json={"version": row_new.version},
    )
    assert ap_new.status_code == 422, ap_new.text
    body = ap_new.json()
    assert body.get("code") == "FX_RATE_MISSING"
    assert db.get_receipt_row(rid_new).status != "approved"
    # Not in report totals
    rep3 = _report(client, tn, month="2026-10")
    assert rep3["month_total"] == 220.0

    # Cross-tenant: B still baseline
    final_b = _report(client, tn_b, month="2026-10")
    assert final_b["month_total"] == 0.0
    assert final_b.get("unconverted_count", 0) == 0


def test_only_legacy_nfx_empty_month_still_surfaces_count(client):
    """Empty month_total must still expose unconverted_* so UI can show notice."""
    tn = new_tenant()
    rid, _ = _make_legacy_nfx(client, tn, date="2026-09-01")
    rep = _report(client, tn, month="2026-09")
    assert rep["month_total"] == 0.0
    assert rep["unconverted_count"] == 1
    assert rep["missing_fx_count"] == 1
    assert rep["unconverted"][0]["receipt_id"] == rid
    assert "汇率" in rep["unconverted_warning"]


def test_ui_source_uses_unconverted_fields_not_blind_count_only(client):
    """Static contract: UI reads unconverted / warning helpers; no early-return swallow."""
    js_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "static", "js", "cost_report.js",
    )
    src = open(js_path, encoding="utf-8").read()
    assert "costReportMissingFxCount" in src
    assert "costReportMissingFxNotice" in src
    assert "d.unconverted" in src
    assert "unconverted_warning" in src
    # Early empty return must require missFx === 0
    assert re.search(r"missFx\s*===\s*0", src)
    # Summary must call helper (not inline blind Number(...) alone)
    assert "costReportMissingFxNotice(d, missFx)" in src
    # Fallback Number(...) only allowed inside costReportMissingFxCount
    idx = src.find("function costReportMissingFxCount")
    assert idx >= 0
    rest = src[idx:]
    # Outside the helper, summary must not reintroduce the old one-liner alone
    assert src.count("Number(d.unconverted_count || d.missing_fx_count || 0)") == 1

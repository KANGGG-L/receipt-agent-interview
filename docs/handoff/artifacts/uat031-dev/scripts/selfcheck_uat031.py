# -*- coding: utf-8 -*-
"""UAT-031 isolated-port self-check: merge migrates batches; FIFO consume no estimated."""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DEV_BASE", "http://127.0.0.1:15131")
TN = "uat031dev"
TN_B = "uat031tenantb"
OUT = os.environ.get(
    "UAT031_OUT",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat031-dev/logs",
)


def req(method, path, headers=None, body=None, tenant=TN, role="owner"):
    data = None
    hdrs = {
        "X-Role": role,
        "X-Tenant-Id": tenant,
        "Content-Type": "application/json",
    }
    if headers:
        hdrs.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw}


def create_sku(name, unit="kg", tenant=TN):
    st, j = req(
        "POST",
        "/api/inventory/skus",
        role="staff",
        tenant=tenant,
        body={"name": name, "base_unit": unit, "category": "肉类", "min_stock_alert": 0},
    )
    assert st == 200 and j.get("status") == "success", (st, j)
    return j["id"]


def staff_save(name, qty, unit_price, supplier, date="2026-10-01", tenant=TN):
    total = qty * unit_price
    body = {
        "supplier_name": supplier,
        "date": date,
        "total_amount": total,
        "settlement_type": "credit",
        "currency": "HKD",
        "items": [
            {
                "name": name,
                "quantity": qty,
                "unit": "kg",
                "unit_price": unit_price,
                "amount": total,
            }
        ],
        "version": None,
        "source": "manual",
    }
    st, j = req("POST", "/api/save_edited", role="staff", tenant=tenant, body=body)
    assert st == 200 and j.get("status") == "success", (st, j)
    return j.get("receipt_id") or j.get("id")


def approve(rid, tenant=TN):
    st, detail = req("GET", "/api/receipt/%d" % rid, tenant=tenant)
    assert st == 200, (st, detail)
    data = detail.get("data") or detail
    ver = data.get("version")
    st2, ap = req(
        "POST",
        "/api/receipt/%d/approve" % rid,
        tenant=tenant,
        body={"version": ver},
    )
    assert st2 == 200 and ap.get("status") == "success", (st2, ap)
    return ap


def main():
    os.makedirs(OUT, exist_ok=True)
    evidence = {"base": BASE, "tenant": TN, "steps": [], "verdict": "FAIL"}
    try:
        # Tenant B baseline: create SKU + batch, snapshot
        b_sku = create_sku("B租户牛肉_UAT031", tenant=TN_B)
        rid_b = staff_save(
            "B租户牛肉_UAT031", 3.0, 30.0, "Vendor_B_UAT031", tenant=TN_B
        )
        approve(rid_b, tenant=TN_B)
        st, inv_b0 = req("GET", "/api/inventory/skus/%d" % b_sku, tenant=TN_B)
        assert st == 200, (st, inv_b0)
        b0 = inv_b0.get("data") or inv_b0
        evidence["steps"].append(
            {
                "name": "tenant_b_baseline",
                "sku_id": b_sku,
                "stock": b0.get("current_stock"),
                "batches": b0.get("batches"),
            }
        )

        # Tenant A: primary + secondary with 1kg each
        p_id = create_sku("本地新鲜牛肉_UAT031")
        s_id = create_sku("手打牛肉丸_UAT031")
        rid_p = staff_save(
            "本地新鲜牛肉_UAT031", 1.0, 40.0, "Vendor_A_UAT031", date="2026-10-01"
        )
        approve(rid_p)
        rid_s = staff_save(
            "手打牛肉丸_UAT031", 1.0, 50.0, "Vendor_A_UAT031", date="2026-10-02"
        )
        approve(rid_s)

        st, inv_p = req("GET", "/api/inventory/skus/%d" % p_id)
        st2, inv_s = req("GET", "/api/inventory/skus/%d" % s_id)
        assert st == 200 and st2 == 200
        dp = inv_p.get("data") or inv_p
        ds = inv_s.get("data") or inv_s
        assert float(dp.get("current_stock") or 0) == 1.0, dp
        assert float(ds.get("current_stock") or 0) == 1.0, ds
        assert len(dp.get("batches") or []) >= 1, dp
        assert len(ds.get("batches") or []) >= 1, ds
        evidence["steps"].append(
            {
                "name": "pre_merge",
                "primary": {"id": p_id, "stock": dp.get("current_stock"), "batches": dp.get("batches")},
                "secondary": {"id": s_id, "stock": ds.get("current_stock"), "batches": ds.get("batches")},
            }
        )

        # Merge secondary into primary
        st, merge = req(
            "POST",
            "/api/inventory/skus/merge",
            body={"primary_sku_id": p_id, "secondary_sku_ids": [s_id]},
        )
        assert st == 200 and merge.get("status") == "success", (st, merge)
        md = merge.get("data") or {}
        assert md.get("primary_sku_id") == p_id
        assert float(md.get("new_stock") or 0) == 2.0, md
        assert int(md.get("batches_migrated") or 0) >= 1, md
        evidence["steps"].append({"name": "merge", "data": md})

        # After merge: primary has 2 batches, secondary inactive/0
        st, inv_p2 = req("GET", "/api/inventory/skus/%d" % p_id)
        dp2 = inv_p2.get("data") or inv_p2
        batches = dp2.get("batches") or []
        assert float(dp2.get("current_stock") or 0) == 2.0, dp2
        assert len(batches) == 2, batches
        rem = sorted(float(b.get("remaining_qty") or b.get("remaining") or 0) for b in batches)
        assert rem == [1.0, 1.0], batches
        evidence["steps"].append(
            {"name": "post_merge_primary", "stock": dp2.get("current_stock"), "batches": batches}
        )

        # Cross-tenant merge must fail
        st_x, cross = req(
            "POST",
            "/api/inventory/skus/merge",
            body={"primary_sku_id": p_id, "secondary_sku_ids": [b_sku]},
        )
        assert cross.get("status") == "error", (st_x, cross)
        evidence["steps"].append({"name": "cross_tenant_rejected", "status": st_x, "body": cross})

        # Consume 1.25kg — must succeed without estimated_partial
        st, cons = req(
            "POST",
            "/api/inventory/%d/consume" % p_id,
            body={"quantity": 1.25, "notes": "uat031 fifo"},
        )
        assert st == 200 and cons.get("status") == "success", (st, cons)
        # Expected FIFO cost: 1.0*40 + 0.25*50 = 52.5
        cost = float(cons.get("cost") or (cons.get("data") or {}).get("cost") or 0)
        assert abs(cost - 52.5) < 0.011, (cost, cons)
        evidence["steps"].append({"name": "consume_1_25", "cost": cost, "body": cons})

        # Inspect DB for estimated batches on primary
        sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
        from app import db

        session = db.get_session()
        try:
            est = (
                session.query(db._InventoryBatchRow)
                .filter(
                    db._InventoryBatchRow.sku_id == p_id,
                    db._InventoryBatchRow.is_estimated == 1,
                )
                .all()
            )
            assert len(est) == 0, est
            open_batches = (
                session.query(db._InventoryBatchRow)
                .filter(
                    db._InventoryBatchRow.sku_id == p_id,
                    db._InventoryBatchRow.is_closed == 0,
                )
                .all()
            )
            # one batch remaining 0.75
            rem_open = [float(b.remaining_qty) for b in open_batches]
            assert any(abs(x - 0.75) < 1e-6 for x in rem_open), rem_open
            # secondary inactive; its batches migrated (sku_id == primary)
            sec = session.get(db._SkuRow, s_id)
            assert sec.active == 0 and float(sec.current_stock or 0) == 0.0
            sec_batches = (
                session.query(db._InventoryBatchRow)
                .filter(db._InventoryBatchRow.sku_id == s_id)
                .all()
            )
            assert len(sec_batches) == 0, sec_batches
            migrated = (
                session.query(db._InventoryBatchRow)
                .filter(db._InventoryBatchRow.source_sku_id == s_id)
                .all()
            )
            assert len(migrated) >= 1, migrated
            evidence["steps"].append(
                {
                    "name": "db_no_estimated",
                    "estimated_count": 0,
                    "open_remaining": rem_open,
                    "migrated_from_secondary": len(migrated),
                }
            )
        finally:
            session.close()

        # Orphan dry-run default
        st, orphan = req("GET", "/api/inventory/batches/orphan")
        assert st == 200, (st, orphan)
        st, mig = req(
            "POST",
            "/api/inventory/batches/orphan/migrate",
            body={},
        )
        assert st == 200, (st, mig)
        mdry = mig.get("data") or {}
        assert mdry.get("dry_run") is True
        assert mdry.get("status") == "PENDING_APPROVAL"
        evidence["steps"].append({"name": "orphan_dry_run", "data": mdry})

        # Tenant B unchanged
        st, inv_b1 = req("GET", "/api/inventory/skus/%d" % b_sku, tenant=TN_B)
        b1 = inv_b1.get("data") or inv_b1
        assert float(b1.get("current_stock") or 0) == float(b0.get("current_stock") or 0)
        evidence["steps"].append(
            {
                "name": "tenant_b_after",
                "stock": b1.get("current_stock"),
                "batches": b1.get("batches"),
            }
        )

        evidence["verdict"] = "PASS"
    except Exception as e:
        evidence["error"] = repr(e)
        evidence["verdict"] = "FAIL"
        raise
    finally:
        path = os.path.join(OUT, "selfcheck-evidence.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, ensure_ascii=False, indent=2)
        print("WROTE", path, "verdict=", evidence["verdict"])


if __name__ == "__main__":
    main()

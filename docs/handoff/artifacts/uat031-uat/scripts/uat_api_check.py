# -*- coding: utf-8 -*-
"""UAT-031 independent UAT API + DB check on isolated port (not QA/DEV)."""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("UAT_BASE", "http://127.0.0.1:15251")
TN = "uat031uat"
TN_B = "uat031uatb"
OUT = os.environ.get(
    "UAT031_UAT_OUT",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat031-uat/logs",
)
DB_PATH = os.environ.get(
    "DB_PATH",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat031-uat/environment/uat_isolated.db",
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
    checks = []
    evidence = {"base": BASE, "tenant": TN, "checks": checks, "verdict": "FAIL"}

    def ok(name, detail=None):
        checks.append({"name": name, "pass": True, "detail": detail})

    def fail(name, detail=None):
        checks.append({"name": name, "pass": False, "detail": detail})
        raise AssertionError("%s: %s" % (name, detail))

    try:
        b_sku = create_sku("B租户牛肉_UAT031UAT", tenant=TN_B)
        rid_b = staff_save(
            "B租户牛肉_UAT031UAT", 3.0, 30.0, "Vendor_B_UAT031UAT", tenant=TN_B
        )
        approve(rid_b, tenant=TN_B)
        st, inv_b0 = req("GET", "/api/inventory/skus/%d" % b_sku, tenant=TN_B)
        assert st == 200, (st, inv_b0)
        b0 = inv_b0.get("data") or inv_b0
        b0_stock = float(b0.get("current_stock") or 0)
        assert b0_stock == 3.0, b0
        ok("tenant_b_baseline", {"sku_id": b_sku, "stock": b0_stock, "batches": b0.get("batches")})

        p_id = create_sku("本地新鲜牛肉_UAT031UAT")
        s_id = create_sku("手打牛肉丸_UAT031UAT")
        rid_p = staff_save(
            "本地新鲜牛肉_UAT031UAT", 1.0, 40.0, "Vendor_A_UAT031UAT", date="2026-10-01"
        )
        approve(rid_p)
        rid_s = staff_save(
            "手打牛肉丸_UAT031UAT", 1.0, 50.0, "Vendor_A_UAT031UAT", date="2026-10-02"
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
        ok(
            "pre_merge",
            {
                "primary": {"id": p_id, "stock": dp.get("current_stock"), "batches": dp.get("batches")},
                "secondary": {"id": s_id, "stock": ds.get("current_stock"), "batches": ds.get("batches")},
            },
        )

        st, merge = req(
            "POST",
            "/api/inventory/skus/merge",
            body={"primary_sku_id": p_id, "secondary_sku_ids": [s_id]},
        )
        assert st == 200 and merge.get("status") == "success", (st, merge)
        md = merge.get("data") or {}
        assert md.get("primary_sku_id") == p_id
        assert float(md.get("new_stock") or 0) == 2.0, md
        batches_migrated = int(md.get("batches_migrated") or 0)
        assert batches_migrated >= 1, md
        ok("merge_api", md)

        st, inv_p2 = req("GET", "/api/inventory/skus/%d" % p_id)
        dp2 = inv_p2.get("data") or inv_p2
        batches = dp2.get("batches") or []
        assert float(dp2.get("current_stock") or 0) == 2.0, dp2
        assert len(batches) == 2, batches
        rem = sorted(float(b.get("remaining_qty") or b.get("remaining") or 0) for b in batches)
        assert rem == [1.0, 1.0], batches
        ok("ac1_post_merge_batches", {"stock": dp2.get("current_stock"), "batches": batches})

        sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
        os.environ["DB_PATH"] = DB_PATH
        from app import db

        db.DB_PATH = DB_PATH
        if hasattr(db, "_make_engine"):
            try:
                db._make_engine()
            except Exception:
                pass
        session = db.get_session()
        try:
            sec = session.get(db._SkuRow, s_id)
            assert sec.active == 0 and float(sec.current_stock or 0) == 0.0

            sec_batches = (
                session.query(db._InventoryBatchRow)
                .filter(db._InventoryBatchRow.sku_id == s_id)
                .all()
            )
            assert len(sec_batches) == 0, "batches must not stay on secondary (not stock-total-only)"
            migrated = (
                session.query(db._InventoryBatchRow)
                .filter(db._InventoryBatchRow.source_sku_id == s_id)
                .all()
            )
            assert len(migrated) >= 1, migrated
            for b in migrated:
                assert b.sku_id == p_id
                assert float(b.initial_qty or 0) > 0

            logs = (
                session.query(db._StockLogRow)
                .filter(db._StockLogRow.source_sku_id == s_id)
                .all()
            )
            assert len(logs) >= 1, "stock logs must migrate with source_sku_id"
            for lg in logs:
                assert lg.sku_id == p_id
                assert ("from_sku_id=%s" % s_id) in (lg.note or "") or (
                    "sku_merged" in (lg.note or "")
                )

            items = (
                session.query(db._ItemRow)
                .filter(db._ItemRow.source_sku_id == s_id)
                .all()
            )
            assert len(items) >= 1, "receipt items must migrate with source_sku_id"
            for it in items:
                assert it.sku_id == p_id

            ok(
                "ac1_db_unify_provenance",
                {
                    "migrated_batches": len(migrated),
                    "migrated_logs": len(logs),
                    "migrated_items": len(items),
                    "secondary_batches_left": 0,
                },
            )
        finally:
            session.close()

        st_x, cross = req(
            "POST",
            "/api/inventory/skus/merge",
            body={"primary_sku_id": p_id, "secondary_sku_ids": [b_sku]},
        )
        assert cross.get("status") == "error", (st_x, cross)
        ok("cross_tenant_rejected", {"status": st_x, "body": cross})

        st, cons = req(
            "POST",
            "/api/inventory/%d/consume" % p_id,
            body={"quantity": 1.25, "notes": "uat031uat fifo"},
        )
        assert st == 200 and cons.get("status") == "success", (st, cons)
        cost = float(cons.get("cost") or (cons.get("data") or {}).get("cost") or 0)
        assert abs(cost - 52.5) < 0.011, (cost, cons)
        ok("ac2_consume_cost", {"cost": cost, "body": cons})

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
            rem_open = [float(b.remaining_qty) for b in open_batches]
            assert any(abs(x - 0.75) < 1e-6 for x in rem_open), rem_open
            all_primary = (
                session.query(db._InventoryBatchRow)
                .filter(db._InventoryBatchRow.sku_id == p_id)
                .all()
            )
            assert len(all_primary) == 2, "must keep both batch rows (not delete history)"
            initials = sorted(float(b.initial_qty) for b in all_primary)
            assert initials == [1.0, 1.0], initials
            ok(
                "ac2_no_estimated_and_neg_keep_batches",
                {
                    "estimated_count": 0,
                    "open_remaining": rem_open,
                    "batch_count": len(all_primary),
                    "initials": initials,
                },
            )
        finally:
            session.close()

        st, orphan = req("GET", "/api/inventory/batches/orphan")
        assert st == 200, (st, orphan)
        st, mig = req("POST", "/api/inventory/batches/orphan/migrate", body={})
        assert st == 200, (st, mig)
        mdry = mig.get("data") or {}
        assert mdry.get("dry_run") is True
        assert mdry.get("status") == "PENDING_APPROVAL"
        ok("orphan_dry_run", mdry)

        st, inv_b1 = req("GET", "/api/inventory/skus/%d" % b_sku, tenant=TN_B)
        b1 = inv_b1.get("data") or inv_b1
        assert float(b1.get("current_stock") or 0) == b0_stock
        ok("ac4_tenant_b_unchanged", {"stock": b1.get("current_stock"), "batches": b1.get("batches")})

        evidence["verdict"] = "PASS"
        evidence["primary_sku_id"] = p_id
        evidence["secondary_sku_id"] = s_id
        evidence["tenant_b_sku_id"] = b_sku
    except Exception as e:
        evidence["error"] = repr(e)
        evidence["verdict"] = "FAIL"
        raise
    finally:
        path = os.path.join(OUT, "uat_api_check.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, ensure_ascii=False, indent=2, default=str)
        print("WROTE", path, "verdict=", evidence["verdict"])
        print("checks_passed=", sum(1 for c in checks if c.get("pass")), "/", len(checks))


if __name__ == "__main__":
    main()

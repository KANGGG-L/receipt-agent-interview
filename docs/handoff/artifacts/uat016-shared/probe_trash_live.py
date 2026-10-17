# -*- coding: utf-8 -*-
"""UAT-016 live probe: owner recycle / restore / purge on isolate (no model)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ["BASE"].rstrip("/")
ART = os.environ["ART"]
TENANT_A = "uat016-dev"
TENANT_B = "uat016uatb"


def req(method: str, path: str, *, role: str, tenant: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Role": role,
            "X-Tenant-Id": tenant,
        },
    )
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"_raw": raw}
        return e.code, payload


def draft(tenant: str, name: str) -> int:
    st, j = req(
        "POST",
        "/api/save_edited",
        role="staff",
        tenant=tenant,
        body={
            "supplier_name": name,
            "date": "2026-10-17",
            "total_amount": 12.5,
            "settlement_type": "cash",
            "doc_form": "printed",
            "items": [
                {
                    "name": "UAT016菜心",
                    "quantity": 1,
                    "unit": "斤",
                    "unit_price": 12.5,
                    "amount": 12.5,
                }
            ],
        },
    )
    assert st == 200 and "receipt_id" in j, (st, j)
    return int(j["receipt_id"])


def main() -> int:
    out: dict = {"base": BASE, "steps": [], "verdict": "FAIL"}
    try:
        mine = draft(TENANT_A, "UAT016-A供应商")
        theirs = draft(TENANT_B, "UAT016-B供应商")
        out["steps"].append({"mk_drafts": {"mine": mine, "theirs": theirs}})

        st, j = req("POST", f"/api/receipt/{mine}/discard", role="staff", tenant=TENANT_A)
        assert st == 200, (st, j)
        out["steps"].append({"discard_mine_staff": {"status": st, "msg": j.get("msg")}})

        st, j = req("POST", f"/api/receipt/{theirs}/discard", role="staff", tenant=TENANT_B)
        assert st == 200, (st, j)
        out["steps"].append({"discard_theirs_staff": {"status": st}})

        st, j = req("GET", "/api/trash/receipts", role="staff", tenant=TENANT_A)
        assert st == 403, (st, j)
        out["steps"].append({"staff_list_trash": {"status": st, "expect": 403}})

        st, j = req("GET", "/api/trash/receipts", role="owner", tenant=TENANT_A)
        assert st == 200, (st, j)
        ids = [r["id"] for r in j.get("data") or []]
        assert mine in ids and theirs not in ids, ids
        row = next(r for r in j["data"] if r["id"] == mine)
        assert row.get("deleted_at"), row
        out["steps"].append(
            {
                "owner_list_trash": {
                    "status": st,
                    "ids": ids,
                    "deleted_by": row.get("deleted_by"),
                    "tenant_scoped": True,
                }
            }
        )

        st, j = req(
            "POST", f"/api/trash/receipts/{theirs}/restore", role="owner", tenant=TENANT_A
        )
        assert st == 404, (st, j)
        st2, j2 = req(
            "DELETE", f"/api/trash/receipts/{theirs}", role="owner", tenant=TENANT_A
        )
        assert st2 == 404, (st2, j2)
        out["steps"].append(
            {"cross_tenant_restore_purge": {"restore": st, "purge": st2, "expect": 404}}
        )

        st, j = req(
            "POST", f"/api/trash/receipts/{mine}/restore", role="owner", tenant=TENANT_A
        )
        assert st == 200, (st, j)
        out["steps"].append({"owner_restore": {"status": st, "msg": j.get("msg")}})

        st, j = req("GET", "/api/trash/receipts", role="owner", tenant=TENANT_A)
        ids = [r["id"] for r in (j.get("data") or [])]
        assert mine not in ids, ids
        out["steps"].append({"trash_after_restore": {"ids": ids, "mine_gone": True}})

        st, j = req("POST", f"/api/receipt/{mine}/discard", role="staff", tenant=TENANT_A)
        assert st == 200, (st, j)

        st, j = req("DELETE", f"/api/trash/receipts/{mine}", role="owner", tenant=TENANT_A)
        assert st == 200, (st, j)
        out["steps"].append({"owner_purge": {"status": st, "msg": j.get("msg")}})

        st, j = req("GET", "/api/trash/receipts", role="owner", tenant=TENANT_A)
        ids = [r["id"] for r in (j.get("data") or [])]
        assert mine not in ids, ids
        out["steps"].append({"trash_after_purge": {"ids": ids}})

        # Tenant B still soft-deleted (not polluted by A's purge)
        st, j = req("GET", "/api/trash/receipts", role="owner", tenant=TENANT_B)
        assert st == 200, (st, j)
        bids = [r["id"] for r in (j.get("data") or [])]
        assert theirs in bids, bids
        out["steps"].append({"tenant_b_unchanged": {"ids": bids, "theirs_still_trashed": True}})

        # Old admin trash endpoints must stay removed (not re-invented)
        st, j = req("GET", "/api/admin/trash", role="admin", tenant=TENANT_A)
        assert st == 404, (st, j)
        out["steps"].append({"old_admin_trash_gone": {"status": st, "expect": 404}})

        # Hard-delete boundary: purge refuses approved (soft-deleted) receipts
        st, j = req(
            "POST",
            "/api/save_edited",
            role="staff",
            tenant=TENANT_A,
            body={
                "supplier_name": "UAT016-approved",
                "date": "2026-10-17",
                "total_amount": 20,
                "settlement_type": "credit",
                "doc_form": "printed",
                "items": [
                    {
                        "name": "UAT016白菜",
                        "quantity": 2,
                        "unit": "斤",
                        "unit_price": 10,
                        "amount": 20,
                    }
                ],
            },
        )
        rid = int(j["receipt_id"])
        ver = int(j["version"])
        st, j = req(
            "POST",
            f"/api/receipt/{rid}/approve",
            role="owner",
            tenant=TENANT_A,
            body={"version": ver},
        )
        assert st == 200, (st, j)
        # soft-delete via DB is not available over HTTP for approved; discard blocks approved.
        # Use discard only for non-approved; for approved hard-delete boundary we soft-delete
        # by calling an internal path is not available — instead verify discard of approved fails
        # and document purge CANNOT_PURGE via pytest. Here: discard approved should 409/403.
        st_disc, j_disc = req("POST", f"/api/receipt/{rid}/discard", role="owner", tenant=TENANT_A)
        assert st_disc == 409 and j_disc.get("code") == "CANNOT_DISCARD_APPROVED", (st_disc, j_disc)
        out["steps"].append(
            {
                "discard_approved_blocked": {
                    "status": st_disc,
                    "code": j_disc.get("code"),
                },
                "note": "hard-delete boundary: approved cannot discard; purge-approved 409 covered in pytest",
            }
        )

        out["verdict"] = "PASS"
        code = 0
    except Exception as e:  # noqa: BLE001
        out["error"] = repr(e)
        out["verdict"] = "FAIL"
        code = 1

    path = os.path.join(ART, "evidence", "trash-live-probe.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({"verdict": out["verdict"], "path": path, "steps": len(out.get("steps", []))}))
    return code


if __name__ == "__main__":
    sys.exit(main())

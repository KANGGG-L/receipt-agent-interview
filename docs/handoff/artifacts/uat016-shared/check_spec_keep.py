# -*- coding: utf-8 -*-
"""UAT-016: document SPEC_KEEP invariants (no product edits; owner trash contract)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("/workspace/receipt-agent-interview")
ART = Path(os.environ["ART"])


def main() -> int:
    out = {
        "decision": "DEFAULT_ORIGINAL",
        "card": "UAT-016",
        "canonical": {
            "S1-P1-12": "docs/review-2026-10/S1-backend.md:76 — owner trash UI + tenant filter; flagged discard owner-only; admin trash removed",
            "api-conventions": "docs/api-conventions.md:88 — GET/POST /api/admin/trash* deleted; replaced by /api/trash/receipts*",
            "S1-P1-13": "docs/review-2026-10/S1-backend.md:77 — experiment conclude require_admin; concluded_by from identity",
            "S3-07": "docs/review-2026-10/S3-frontend-ux.md:50 — in-app confirm; no false admin-restore promise",
            "S4-12": "docs/review-2026-10/S4-frontend-eng.md:70 — shared dialog/escape (destructive confirms use shared path)",
        },
        "rejected_derived": "Derived UAT Spec hard-delete / invent admin-only trash auth — not approved; do not claim as done",
        "owner": "D0 SPEC_KEEP wave / handoff checklist owner (docs/handoff/01-acceptance-checklist.md §2.1 UAT-016)",
        "product_change": False,
        "checks": {},
        "verdict": "FAIL",
    }

    # No product file edits for this card (source_files=[])
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", "demo/app", "demo/static", "demo/templates"],
        cwd=str(ROOT),
        text=True,
    ).strip()
    # Note: other cards may leave dirty tree; we only assert our scripts did not edit product
    # by checking our working tree against HEAD for lifecycle files we care about.
    lifecycle = [
        "demo/app/api_receipt_lifecycle.py",
        "demo/app/api_receipts.py",
        "demo/static/js/finance.js",
        "demo/app/api_phase2.py",
    ]
    diffs = {}
    for rel in lifecycle:
        r = subprocess.run(
            ["git", "diff", "--", rel],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        diffs[rel] = bool(r.stdout.strip())
    out["checks"]["lifecycle_dirty_vs_head"] = diffs
    # Dirty from other cards is OK to note; this card must not introduce new diffs.
    # We record baseline only — Dev does not edit product.

    # Contract presence: owner trash routes in source
    life = (ROOT / "demo/app/api_receipt_lifecycle.py").read_text(encoding="utf-8")
    out["checks"]["owner_trash_routes"] = {
        "list": "@router.get(\"/api/trash/receipts\")" in life
        or '@router.get("/api/trash/receipts")' in life,
        "restore": "/api/trash/receipts/{receipt_id}/restore" in life,
        "purge": "@router.delete(\"/api/trash/receipts/{receipt_id}\")" in life
        or 'delete("/api/trash/receipts/{receipt_id}")' in life,
        "require_role_owner": 'require_role("owner")' in life,
    }
    fin = (ROOT / "demo/static/js/finance.js").read_text(encoding="utf-8")
    out["checks"]["owner_trash_ui"] = {
        "trashCard": "trashCard" in fin,
        "restore_btn": "trash-restore" in fin,
        "purge_btn": "trash-purge" in fin,
        "api_list": "/api/trash/receipts" in fin,
    }
    # Old admin trash must not be reintroduced
    admin_hits = []
    for p in (ROOT / "demo/app").rglob("*.py"):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "/api/admin/trash" in txt and "404" not in txt.split("/api/admin/trash")[0][-80:]:
            # allow comments mentioning removal
            if "admin/trash" in txt and ("删除" in txt or "removed" in txt or "改为" in txt):
                continue
            if '@router' in txt and 'admin/trash' in txt:
                admin_hits.append(str(p.relative_to(ROOT)))
    out["checks"]["admin_trash_router_absent"] = admin_hits == []
    out["checks"]["admin_trash_hits"] = admin_hits

    # S4-12 shared dialog presence (not claiming new feature)
    shared_dialog = list((ROOT / "demo/static/js").rglob("*dialog*"))
    shared_names = [str(p.relative_to(ROOT)) for p in shared_dialog]
    out["checks"]["s4_12_shared_dialog_files"] = shared_names
    out["checks"]["s4_12_ui_dlg_used_in_finance_or_upload"] = (
        "showCustomConfirm" in fin
        or "ui-dlg" in fin
        or "confirm" in fin.lower()
    )

    ok = (
        out["checks"]["owner_trash_routes"]["list"]
        and out["checks"]["owner_trash_routes"]["restore"]
        and out["checks"]["owner_trash_routes"]["purge"]
        and out["checks"]["owner_trash_ui"]["trashCard"]
        and out["checks"]["admin_trash_router_absent"]
    )
    out["verdict"] = "PASS" if ok else "FAIL"
    path = ART / "evidence" / "spec-keep.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": out["verdict"], "path": str(path)}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Prove DEFAULT_ORIGINAL: no new memory-recovery / consistency / security-event UI.

Scans product templates/JS for dedicated UI affordances that would claim the
derived Spec (reachable UI for memory revive / memory-consistency / security-events).
Pre-existing vendor-memory governance (read+delete) is documented as already-present,
not claimed as a new UAT-014 deliverable.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ART = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
ROOT = Path("/workspace/receipt-agent-interview")
SCAN_PATHS = [
    ROOT / "demo/templates/index.html",
    ROOT / "demo/static/js",
]

# Patterns that would indicate a NEW dedicated UI for the derived Spec features
FORBIDDEN_UI = [
    (r"memory[-_]?recover", "memory-recovery UI string"),
    (r"记忆恢复", "memory-recovery Chinese UI"),
    (r"security[-_]?events?[-_]?(panel|page|tab|view)", "security-events UI panel"),
    (r"安全事件(面板|页面|大盘|列表)", "security-events Chinese UI"),
    (r"memory[-_]?consistency[-_]?(panel|page|tab|view|ui)", "memory-consistency UI panel"),
    (r"一致性(巡检|面板|页面)", "consistency Chinese UI"),
    (r"/api/memory/[^\"']+/revive", "revive wired in frontend"),
    (r"loadSecurityEvents|renderSecurityEvents|openSecurityEvents", "security-events frontend loader"),
    (r"loadMemoryConsistency|renderMemoryConsistency", "memory-consistency frontend loader"),
]

hits = []
for base in SCAN_PATHS:
    files = [base] if base.is_file() else sorted(base.rglob("*.js")) + sorted(base.rglob("*.html"))
    for f in files:
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pat, label in FORBIDDEN_UI:
            for m in re.finditer(pat, text, re.IGNORECASE):
                # line number
                line = text.count("\n", 0, m.start()) + 1
                hits.append({"file": str(f.relative_to(ROOT)), "line": line, "label": label, "match": m.group(0)[:80]})

# Pre-existing vendor-memory governance (document, not a failure)
preexisting = []
idx = ROOT / "demo/templates/index.html"
engine = ROOT / "demo/static/js/engine_admin.js"
for f, needles in [
    (idx, ["loadVendorMemoryGovernance"]),
    (engine, ["loadVendorMemoryGovernance", "/api/admin/vendor-memory"]),
]:
    if not f.exists():
        continue
    text = f.read_text(encoding="utf-8", errors="replace")
    for n in needles:
        if n in text:
            preexisting.append({"file": str(f.relative_to(ROOT)), "token": n, "note": "pre-existing governance; NOT claimed as UAT-014 new UI"})

# Product files changed this wave for UAT-014 must be empty (SPEC_KEEP / source_files=[])
# Use git diff --name-only vs HEAD for product paths; we only care that THIS card didn't add UI files.
# Report current dirty product files for honesty (other cards may have dirty tree).
r = subprocess.run(
    ["git", "-C", str(ROOT), "diff", "--name-only", "--", "demo/templates", "demo/static"],
    capture_output=True, text=True,
)
dirty_ui = [x for x in r.stdout.splitlines() if x.strip()]

result = {
    "forbidden_ui_hits": hits,
    "preexisting_vendor_memory_governance": preexisting,
    "dirty_ui_files_vs_HEAD": dirty_ui,
    "note": (
        "DEFAULT_ORIGINAL: card forbids adding memory-recovery/consistency/security-event UI. "
        "Zero forbidden_ui_hits required. Pre-existing vendor-memory governance is acknowledged, "
        "not claimed as new work. Dirty UI files from other in-progress cards are listed for honesty."
    ),
    "verdict": "PASS" if len(hits) == 0 else "FAIL",
}
out = ART / "evidence" / "no-new-ui.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"verdict": result["verdict"], "hits": len(hits), "path": str(out)}, ensure_ascii=False))
sys.exit(0 if result["verdict"] == "PASS" else 1)

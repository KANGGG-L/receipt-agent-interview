# -*- coding: utf-8 -*-
"""H06：审批包门禁齐全才 ready；任何缺失/漂移阻 apply；包内无执行脚本。"""

import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_repair_execution_packet as h06  # noqa: E402

PLAN = {
    "snapshot": {"sha256": "abc123"},
    "decisions": [
        {"object_kind": "credit", "object_id": 1, "action": "adjustment",
         "reason": "credit_balance_mismatch", "requires_owner": True, "status": "pending"},
        {"object_kind": "receipt", "object_id": 2, "action": "pending_evidence",
         "reason": "unknown_quantity", "requires_owner": True, "status": "blocked"},
    ],
}
DRILL_OK = {"ok": True, "unexplained": 0, "snapshot_sha256": "abc123"}
META = {
    "authorization_ref": "OWNER-2026-10-26-01",
    "approved_by": "owner@user",
    "operator": "data-ops-1",
    "restore_evidence": "evidence/h02-restore-abc.json",
    "write_window_start": "2026-10-27T01:00:00Z",
    "write_window_end": "2026-10-27T03:00:00Z",
    "whitelist": ["credit:1", "receipt:2"],
}


def test_complete_packet_ready_and_has_no_apply_script():
    p = h06.build_packet(PLAN, DRILL_OK, META)
    assert p["ready"] is True and p["blocking_reasons"] == []
    assert p["apply_script"] is None
    assert p["snapshot_sha256"] == "abc123"
    assert len(p["whitelist"]) == 2
    assert "apply" not in json.dumps(p).lower().replace("apply_script", "")


def test_missing_any_gate_blocks():
    for key in ("authorization_ref", "approved_by", "operator", "restore_evidence"):
        m = dict(META)
        m.pop(key)
        p = h06.build_packet(PLAN, DRILL_OK, m)
        assert p["ready"] is False and key in p["blocking_reasons"]
    p = h06.build_packet(PLAN, dict(DRILL_OK, ok=False), META)
    assert p["ready"] is False and "drill_reconcile_not_ok" in p["blocking_reasons"]
    p = h06.build_packet(PLAN, dict(DRILL_OK, snapshot_sha256="zzz"), META)
    assert p["ready"] is False and "snapshot_mismatch" in p["blocking_reasons"]
    p = h06.build_packet(PLAN, DRILL_OK, dict(META, whitelist=["credit:1"]))
    assert p["ready"] is False and "object_whitelist_mismatch" in p["blocking_reasons"]
    p = h06.build_packet(PLAN, DRILL_OK, dict(META, write_window_start="2026-10-28",
                                             write_window_end="2026-10-27"))
    assert p["ready"] is False and "write_window_invalid" in p["blocking_reasons"]


def test_cli_writes_packet_and_exit_code(tmp_path):
    plan = tmp_path / "plan.json"
    drill = tmp_path / "drill.json"
    meta = tmp_path / "meta.json"
    out = tmp_path / "packet.json"
    plan.write_text(json.dumps(PLAN), encoding="utf-8")
    drill.write_text(json.dumps(DRILL_OK), encoding="utf-8")
    meta.write_text(json.dumps(META), encoding="utf-8")
    assert h06.main(["--plan", str(plan), "--drill", str(drill),
                     "--meta", str(meta), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["ready"] is True
    bad = dict(META)
    bad["operator"] = ""
    meta.write_text(json.dumps(bad), encoding="utf-8")
    assert h06.main(["--plan", str(plan), "--drill", str(drill),
                     "--meta", str(meta), "--out", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["ready"] is False

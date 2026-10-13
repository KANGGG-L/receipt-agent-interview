# -*- coding: utf-8 -*-
"""批 1 / C3：对账页导入对账单。前端入口 + 同一任务 409 + staff 403 + 跨租户 task_id 404。"""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TESTS = os.path.dirname(os.path.abspath(__file__))
_HARNESS = os.path.join(_TESTS, "frontend_batch01_harness.js")


def test_import_statement_ui_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node")
    proc = subprocess.run([node, _HARNESS], capture_output=True, text=True, timeout=60)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, "脚手架无 JSON：stdout=%s stderr=%s" % (proc.stdout, proc.stderr)
    result = json.loads(lines[-1])
    assert proc.returncode == 0, result.get("failures")
    assert result["fail"] == 0, result["failures"]


def test_import_statement_second_time_staff_and_other_tenant(client):
    from _b2_helpers import hdr, make_supplier, new_tenant

    tn = new_tenant()
    other = new_tenant()
    sid = make_supplier(tn, "供应商甲")
    sid_other = make_supplier(other, "供应商乙")
    body = {
        "supplier_id": sid,
        "statement_period": "2026-10",
        "external_lines": [{"date": "2026-10-05", "invoice_no": "INV-1", "amount": 12.5}],
    }
    staff = client.post("/api/reconciliation/import-statement", headers=hdr(tn, "staff"), json=body)
    assert staff.status_code == 403, staff.text
    first = client.post("/api/reconciliation/import-statement", headers=hdr(tn), json=body)
    assert first.status_code == 200, first.text
    task_id = first.json()["task_id"]
    again = client.post("/api/reconciliation/import-statement", headers=hdr(tn), json=dict(body, task_id=task_id))
    assert again.status_code == 409, again.text
    again_body = again.json()
    assert again_body["code"] == "STATEMENT_ALREADY_IMPORTED"
    assert "导入" in again_body["msg"]
    foreign = client.post(
        "/api/reconciliation/import-statement",
        headers=hdr(other),
        json={
            "supplier_id": sid_other,
            "statement_period": "2026-10",
            "external_lines": body["external_lines"],
            "task_id": task_id,
        },
    )
    assert foreign.status_code == 404, foreign.text


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)

# -*- coding: utf-8 -*-
"""S1-P2-05：停留过久的识别中单据会被清扫，店员可以把失败单转成手工。"""

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, maintenance
from app.main import app

STAFF = {"X-Role": "staff"}


def test_stale_parsing_becomes_error_and_staff_can_convert_manual():
    client = TestClient(app)
    rid = db.create_receipt(status="parsing", tenant_id="default", supplier_name="供应商甲")
    session = db.get_session()
    try:
        row = session.get(db._ReceiptRow, rid)
        row.created_at = "2000-01-01T00:00:00"
        row.updated_at = "2000-01-01T00:00:00"
        session.commit()
    finally:
        session.close()
    assert rid in maintenance.sweep_parsing()
    assert db.get_receipt_row(rid, tenant_id="default").status == "error"
    converted = client.post("/api/receipt/%d/convert_manual" % rid, headers=STAFF)
    assert converted.status_code == 200, converted.text
    done = db.get_receipt_row(rid, tenant_id="default")
    assert done.status == "edited" and done.doc_form == "manual_entry"

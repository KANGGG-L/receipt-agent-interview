# -*- coding: utf-8 -*-
"""I18N-04：导出 lang 只改表头和文件名，不改单元格。不启动浏览器，不读现场库。"""

import os
import sys
from urllib.parse import unquote

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.i18n_text import to_hant_hk
from app.main import app

ADMIN = {"X-Role": "admin"}


def _filename(resp):
    disposition = resp.headers["content-disposition"]
    return unquote(disposition.split("''", 1)[1])


def test_export_download_lang_changes_header_and_filename_only():
    client = TestClient(app)
    listed = client.get("/api/export/reports", headers=ADMIN)
    assert listed.status_code == 200, listed.text
    reports = listed.json()["data"]
    report_id = reports[0]["id"] if isinstance(reports, list) else None
    assert report_id
    body = {"report_id": report_id, "params": {}}
    plain = client.post("/api/export/download", headers=ADMIN, json=body)
    hant = client.post("/api/export/download", headers=ADMIN, json={**body, "lang": "zh-Hant-HK"})
    hans = client.post("/api/export/download", headers=ADMIN, json={**body, "lang": "zh-Hans"})
    assert plain.status_code == hant.status_code == hans.status_code == 200
    plain_lines = plain.content.decode("utf-8-sig").splitlines()
    hant_lines = hant.content.decode("utf-8-sig").splitlines()
    hans_lines = hans.content.decode("utf-8-sig").splitlines()
    assert hans_lines[0] == plain_lines[0]
    assert hant_lines[0] == to_hant_hk(plain_lines[0])
    assert hant_lines[0] != plain_lines[0]
    assert _filename(hant) == to_hant_hk(_filename(plain).rsplit(".", 1)[0]) + ".csv"
    assert _filename(hant) != _filename(plain)
    assert hant_lines[1:] == plain_lines[1:]

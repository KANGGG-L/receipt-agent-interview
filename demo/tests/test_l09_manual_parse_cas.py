# -*- coding: utf-8 -*-
"""L09 C-STATE：转手工版本/审计一次、parse 迟到写回 CAS、retry/replace/convert 状态矩阵。

零真实外呼：识别 Job 用桩替代；零真实 DB：模块级临时库；不读真实 .env。
"""

import io
import json
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="test_l09_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_l09.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "0"
os.environ.pop("IDENTITY_V2_ENABLED", None)
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
           "OPENAI_REC_API_KEY", "OPENAI_REC_BASE_URL", "OPENAI_REC_MODEL",
           "OPENAI_AUD_API_KEY", "OPENAI_AUD_BASE_URL", "OPENAI_AUD_MODEL",
           "OPENAI_PARSE_API_KEY", "OPENAI_PARSE_BASE_URL", "OPENAI_PARSE_MODEL",
           "DASHSCOPE_API_KEY"):
    os.environ[_k] = ""

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import receipt_utils  # noqa: E402

db._make_engine()
client = TestClient(app)
STAFF = {"X-Role": "staff"}


@pytest.fixture()
def job_stub(monkeypatch):
    """替换后台识别派发，记录调用；不跑真实管线。"""
    calls = []

    def _stub(image_path, vendor_hint="", receipt_id=None, tenant_id=None,
              image_guard=None, **kwargs):
        calls.append({"image_path": image_path, "receipt_id": receipt_id})
        return ("job-l09", receipt_id)

    from app import api_receipts
    monkeypatch.setattr(api_receipts, "start_recognition_job", _stub)
    return calls


@pytest.fixture()
def upload_dir(monkeypatch, tmp_path):
    from app import api_receipts
    d = tmp_path / "uploads"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(api_receipts, "UPLOAD_DIR", d)
    return d


def _mk_receipt(tmp_path, status="error", doc_form=None):
    img = tmp_path / "l09_original.jpg"
    if not img.exists():
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 60)
    rid = db.create_receipt(supplier_name="原人工供应商", status=status)
    fields = {"image_path": str(img)}
    if doc_form:
        fields["doc_form"] = doc_form
    db.update_receipt(rid, **fields)
    return rid, img


def _audit_actions(rid, action):
    row = db.get_receipt_row(rid)
    return [e for e in json.loads(row.audit_logs_json or "[]")
            if e.get("action") == action]


def _receipt_data(vendor="迟到供应商"):
    from app.models import DocForm, ReceiptData, ReceiptItem
    return ReceiptData(
        doc_form=DocForm.PRINTED, vendor=vendor, date="2026-10-02",
        items=[ReceiptItem(name="迟到品", qty=1, unit="个", unit_price=1, amount=1)],
        total=1, payment_marked=False, confidence=0.9)


def _sharp_png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (96, 96), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. convert_manual：版本/审计恰一增量 + 幂等 + 409 + 无权不改状态
# ---------------------------------------------------------------------------
def test_convert_manual_version_audit_once(tmp_path):
    rid, img = _mk_receipt(tmp_path, status="error")
    assert db.get_receipt_row(rid).version == 1

    body = {"expected_version": 1, "request_key": "cm-once-1"}
    r = client.post(f"/api/receipt/{rid}/convert_manual", headers=STAFF, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert r.json()["version"] == 2
    assert r.json()["has_original_image"] is True

    row = db.get_receipt_row(rid)
    assert row.id == rid, "原单据 ID 必须保留"
    assert row.image_path == str(img), "原图路径必须保留"
    assert row.status == "edited" and row.doc_form == "manual_entry"
    assert row.version == 2, "version 恰一增量"
    logs = _audit_actions(rid, "convert_manual")
    assert len(logs) == 1, "审计恰一条"
    assert logs[0]["details"].get("request_key") == "cm-once-1"

    # 同 key 重放（网络重试）：成功回放，不得再推进 version/审计
    r2 = client.post(f"/api/receipt/{rid}/convert_manual", headers=STAFF, json=body)
    assert r2.status_code == 200 and r2.json()["version"] == 2, r2.text
    row2 = db.get_receipt_row(rid)
    assert row2.version == 2 and len(_audit_actions(rid, "convert_manual")) == 1

    # 旧客户端拿过期 version：409 VERSION_CONFLICT，本地草稿由此保留
    r3 = client.post(f"/api/receipt/{rid}/convert_manual", headers=STAFF,
                     json={"expected_version": 1, "request_key": "cm-stale"})
    assert r3.status_code == 409 and r3.json()["code"] == "VERSION_CONFLICT"
    assert r3.json()["current_version"] == 2
    assert db.get_receipt_row(rid).version == 2

    # 无权角色不得改变状态
    rid2, _ = _mk_receipt(tmp_path, status="error")
    r4 = client.post(f"/api/receipt/{rid2}/convert_manual")
    assert r4.status_code == 401
    assert db.get_receipt_row(rid2).status == "error"

    # 旧客户端不带 body 仍兼容（version 同样恰一增量）
    r5 = client.post(f"/api/receipt/{rid2}/convert_manual", headers=STAFF)
    assert r5.status_code == 200 and r5.json()["version"] == 2
    assert len(_audit_actions(rid2, "convert_manual")) == 1


# ---------------------------------------------------------------------------
# 2. parse 回写 CAS：转手工后旧 Job 不得覆盖；冻结/软删门禁
# ---------------------------------------------------------------------------
def test_parse_finishes_after_manual_no_overwrite(tmp_path):
    rid, img = _mk_receipt(tmp_path, status="parsing")
    r = client.post(f"/api/receipt/{rid}/convert_manual", headers=STAFF,
                    json={"expected_version": 1, "request_key": "cm-race"})
    assert r.status_code == 200 and r.json()["version"] == 2

    # 旧 Job 在转手工后才完成：基线 v1 已失效，写回不得覆盖新版本
    stale = {"_expected_parse_version": 1, "status": "parsed"}
    receipt_utils.save_parsed_data(rid, _receipt_data(), stale)
    row = db.get_receipt_row(rid)
    assert row.status == "edited" and row.doc_form == "manual_entry"
    assert row.supplier_name == "原人工供应商", "旧识别结果不得覆写人工数据"
    assert row.version == 2 and row.image_path == str(img)

    # CAS 单测：状态仍是 parsing，但版本已被推进（如换图/重跑）→ 基线不符不写
    rid2, _ = _mk_receipt(tmp_path, status="parsing")
    db.update_receipt(rid2, version=2)
    receipt_utils.save_parsed_data(rid2, _receipt_data("旧结果供应商"),
                                   {"_expected_parse_version": 1, "status": "parsed"})
    row2 = db.get_receipt_row(rid2)
    assert row2.supplier_name == "原人工供应商" and row2.version == 2
    # 基线正确 → 正常写回并推进版本，供后续旧 Job 失效
    receipt_utils.save_parsed_data(rid2, _receipt_data("新结果供应商"),
                                   {"_expected_parse_version": 2, "status": "parsed"})
    row2b = db.get_receipt_row(rid2)
    assert row2b.supplier_name == "新结果供应商" and row2b.version == 3

    # 软删门禁
    rid3, _ = _mk_receipt(tmp_path, status="parsed")
    db.update_receipt(rid3, deleted_at="2026-10-20T10:00:00")
    receipt_utils.save_parsed_data(rid3, _receipt_data("软删后"), {"_expected_parse_version": 1})
    assert db.get_receipt_row(rid3).supplier_name == "原人工供应商"

    # 冻结（已冲销 reversed_at）门禁
    rid4, _ = _mk_receipt(tmp_path, status="parsed")
    db.update_receipt(rid4, reversed_at="2026-10-20T10:00:00")
    receipt_utils.save_parsed_data(rid4, _receipt_data("冲销后"), {"_expected_parse_version": 1})
    assert db.get_receipt_row(rid4).supplier_name == "原人工供应商"


# ---------------------------------------------------------------------------
# 3. retry / replace-image / convert-manual 状态矩阵
# ---------------------------------------------------------------------------
def test_retry_replace_status_matrix(tmp_path, job_stub, upload_dir):
    allowed = ("uploaded", "parsed", "parsed_with_warnings", "edited", "error")
    for status in allowed:
        rid, _ = _mk_receipt(tmp_path, status=status)
        r = client.post(f"/api/receipt/{rid}/retry", headers=STAFF)
        assert r.status_code == 200 and r.json()["status"] == "queued", f"[{status}] {r.text}"
    assert len(job_stub) == len(allowed)

    rejected = ("parsing", "approved", "flagged", "reversed")
    for status in rejected:
        rid, _ = _mk_receipt(tmp_path, status=status)
        r = client.post(f"/api/receipt/{rid}/retry", headers=STAFF)
        assert r.status_code == 409, f"[{status}] 应拒绝: {r.text}"
        if status == "reversed":
            c = client.post(f"/api/receipt/{rid}/convert_manual", headers=STAFF)
            assert c.status_code == 409 and c.json()["code"] == "INVALID_STATUS"
    assert len(job_stub) == len(allowed), "被拒状态不得派发 Job"

    # 手工单不可 retry / replace
    rid_m, _ = _mk_receipt(tmp_path, status="edited", doc_form="manual_entry")
    assert client.post(f"/api/receipt/{rid_m}/retry", headers=STAFF).status_code == 400
    r_img = client.post(f"/api/receipt/{rid_m}/replace-image",
                        files={"receipt": ("r.png", _sharp_png_bytes(), "image/png")},
                        headers=STAFF)
    assert r_img.status_code == 400, r_img.text

    # 乐观锁：旧页面版本 → 409 VERSION_CONFLICT（retry 与 replace 同口径）
    rid_v, _ = _mk_receipt(tmp_path, status="parsed")
    r_bad = client.post(f"/api/receipt/{rid_v}/retry?expected_version=99", headers=STAFF)
    assert r_bad.status_code == 409 and r_bad.json()["code"] == "VERSION_CONFLICT"
    r_rep = client.post(f"/api/receipt/{rid_v}/replace-image",
                        files={"receipt": ("r.png", _sharp_png_bytes(), "image/png")},
                        data={"expected_version": "99"}, headers=STAFF)
    assert r_rep.status_code == 409 and r_rep.json()["code"] == "VERSION_CONFLICT"
    assert db.get_receipt_row(rid_v).status == "parsed", "被拒请求不得改变状态"

    # convert 状态矩阵：approved/flagged 拒绝；parsing 允许（识别中断逃生）
    for status in ("approved", "flagged"):
        rid_c, _ = _mk_receipt(tmp_path, status=status)
        c = client.post(f"/api/receipt/{rid_c}/convert_manual", headers=STAFF)
        assert c.status_code == 409 and db.get_receipt_row(rid_c).status == status
    rid_p, _ = _mk_receipt(tmp_path, status="parsing")
    c2 = client.post(f"/api/receipt/{rid_p}/convert_manual", headers=STAFF)
    assert c2.status_code == 200 and db.get_receipt_row(rid_p).status == "edited"


# ---------------------------------------------------------------------------
# 4. replace-image：换图推进 version + 审计；旧基线 parse 写回被 CAS 拒绝
# ---------------------------------------------------------------------------
def test_replace_image_bumps_version_and_stale_parse_rejected(
        tmp_path, job_stub, upload_dir):
    rid, orig = _mk_receipt(tmp_path, status="parsed")
    assert db.get_receipt_row(rid).version == 1

    r = client.post(
        f"/api/receipt/{rid}/replace-image",
        files={"receipt": ("retake.png", _sharp_png_bytes(), "image/png")},
        data={"force": "true", "expected_version": "1"},
        headers=STAFF)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert r.json()["version"] == 2, "换图成功必须推进 version"

    row = db.get_receipt_row(rid)
    assert row.id == rid, "原单据 ID 必须保留"
    assert row.version == 2
    assert row.image_path != str(orig), "image_path 必须指向新图"
    assert os.path.exists(row.image_path)
    assert os.path.exists(str(orig)), "旧图保留可审计"
    assert len(_audit_actions(rid, "replace_image")) == 1, "换图审计恰一条"
    assert job_stub and job_stub[0]["receipt_id"] == rid

    # 旧 Job 以换图前 v1 为 parse 基线：迟到写回不得覆盖新图结果
    receipt_utils.save_parsed_data(
        rid, _receipt_data("旧基线供应商"),
        {"_expected_parse_version": 1, "status": "parsed"})
    row2 = db.get_receipt_row(rid)
    assert row2.image_path == row.image_path, "旧结果不得改写新图路径"
    assert row2.supplier_name == "原人工供应商", "旧结果不得覆盖新单数据"
    assert row2.version == 2


# ---------------------------------------------------------------------------
# 5. replace-image 锁内复核 expected_version（TOCTOU：预检后版本被推进）
# ---------------------------------------------------------------------------
def test_replace_image_lock_rechecks_expected_version(
        tmp_path, job_stub, upload_dir, monkeypatch):
    from app import api_receipts

    rid, orig = _mk_receipt(tmp_path, status="parsed")
    real_save = api_receipts._save_upload

    def _save_then_conflict(file):
        path = real_save(file)
        # 锁外预检已通过、尚未进锁：模拟他人推进版本（TOCTOU 窗口）
        db.update_receipt(rid, version=2)
        return path

    monkeypatch.setattr(api_receipts, "_save_upload", _save_then_conflict)

    r = client.post(
        f"/api/receipt/{rid}/replace-image",
        files={"receipt": ("retake.png", _sharp_png_bytes(), "image/png")},
        data={"force": "true", "expected_version": "1"},
        headers=STAFF)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "VERSION_CONFLICT"
    assert r.json()["current_version"] == 2

    row = db.get_receipt_row(rid)
    assert row.status == "parsed", "被拒请求不得改变状态"
    assert row.image_path == str(orig), "被拒请求不得换图"
    assert os.path.exists(str(orig))
    assert job_stub == [], "被拒请求不得派发 Job"
    assert len(_audit_actions(rid, "replace_image")) == 0


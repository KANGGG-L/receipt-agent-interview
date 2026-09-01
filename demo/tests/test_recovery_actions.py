# -*- coding: utf-8 -*-
"""挽回动作（重新解析 / 重拍）与挽回埋点聚合测试（不依赖 LLM，秒级）。

覆盖计划 §5：
- /api/receipt/{id}/retry 状态门放宽（edited 放行；parsing/approved/flagged 拒绝；
  既有失败态 uploaded/parsed/error 回归不受影响；手工录入拒绝）
- /api/receipt/{id}/replace-image（换图重跑：成功返回 image_url、404、
  过小文件 400、极模糊硬拦截 + force 绕过、image_replaced 埋点落库）
- /api/analytics/recovery-summary（RBAC + 三指标口径 + 事件分布）
- /api/track 回归（前端挽回点击事件可正常入库）
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_recovery_test.db")
if os.path.exists("/tmp/receipt_demo_recovery_test.db"):
    os.remove("/tmp/receipt_demo_recovery_test.db")

import pytest
from app import db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_recovery.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def job_stub(monkeypatch):
    """替换后台识别 Job 派发：记录调用参数，不真正跑识别管线。"""
    calls = []

    def _stub(image_path, vendor_hint="", receipt_id=None, tenant_id=None):
        calls.append({"image_path": image_path, "vendor_hint": vendor_hint,
                      "receipt_id": receipt_id, "tenant_id": tenant_id})
        return ("job-test-1", receipt_id)

    from app import api_receipts
    monkeypatch.setattr(api_receipts, "start_recognition_job", _stub)
    return calls


@pytest.fixture()
def upload_dir(monkeypatch, tmp_path):
    """把上传落盘目录指到临时目录，避免污染仓库 uploads/。"""
    from app import api_receipts
    d = tmp_path / "uploads"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(api_receipts, "UPLOAD_DIR", d)
    return d


def _mk_receipt(tmp_path, status="edited", doc_form=None):
    """建一张带原图文件的单据（假 JPEG，>30 字节）。"""
    img = tmp_path / "orig_receipt.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 60)
    rid = db.create_receipt(supplier_name="祥興", status=status)
    fields = {"image_path": str(img)}
    if doc_form:
        fields["doc_form"] = doc_form
    db.update_receipt(rid, **fields)
    return rid, img


def _sharp_png_bytes():
    """随机噪声图：Laplacian 方差远高于阈值 30，可通过画质门禁。"""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _blur_png_bytes():
    """纯色图：Laplacian 方差为 0，必被极模糊硬拦截。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("L", (64, 64), color=128).save(buf, format="PNG")
    return buf.getvalue()


# -------------------------------------------------------------
# 1. retry 状态门
# -------------------------------------------------------------
def test_retry_allows_edited_and_legacy_states(client, job_stub, tmp_path):
    """edited（自动保存产物）+ 既有失败态 uploaded/parsed/error 均可重跑。"""
    for status in ("uploaded", "parsed", "edited", "error"):
        rid, img = _mk_receipt(tmp_path, status=status)
        r = client.post(f"/api/receipt/{rid}/retry", headers={"X-Role": "staff"})
        assert r.status_code == 200, f"[{status}] 应放行: {r.text}"
        body = r.json()
        assert body["status"] == "queued"
        assert body["receipt_id"] == rid
        assert body["job_id"] == "job-test-1"
        assert "version" in body
    # 每次都应复用原单据 + 原图派发 Job（不新建单据）
    assert len(job_stub) == 4
    assert all(c["receipt_id"] is not None for c in job_stub)
    assert all(os.path.basename(c["image_path"]) == "orig_receipt.jpg"
               for c in job_stub)


def test_retry_rejects_inflight_and_terminal_states(client, job_stub, tmp_path):
    """parsing 在途 / approved 已入账 / flagged 待处置 → 一律 409。"""
    for status in ("parsing", "approved", "flagged"):
        rid, _ = _mk_receipt(tmp_path, status=status)
        r = client.post(f"/api/receipt/{rid}/retry", headers={"X-Role": "staff"})
        assert r.status_code == 409, f"[{status}] 应拒绝: {r.text}"
        assert r.json()["status"] == "error"
    assert job_stub == [], "被拒状态不得派发识别 Job"


def test_retry_rejects_manual_entry(client, job_stub, tmp_path):
    """手工录入单据无原图 → 400。"""
    rid, _ = _mk_receipt(tmp_path, status="edited", doc_form="manual_entry")
    r = client.post(f"/api/receipt/{rid}/retry", headers={"X-Role": "staff"})
    assert r.status_code == 400
    assert "手工录入" in r.json()["msg"]
    assert job_stub == []


def test_retry_missing_image_400(client, job_stub):
    """image_path 缺失 → 400（无法重跑）。"""
    rid = db.create_receipt(status="edited")
    r = client.post(f"/api/receipt/{rid}/retry", headers={"X-Role": "staff"})
    assert r.status_code == 400
    assert "原图缺失" in r.json()["msg"]


def test_retry_not_found_404(client, job_stub):
    r = client.post("/api/receipt/999999/retry", headers={"X-Role": "staff"})
    assert r.status_code == 404


# -------------------------------------------------------------
# 2. replace-image（重拍）
# -------------------------------------------------------------
def test_replace_image_success(client, job_stub, upload_dir, tmp_path):
    """换图成功：返回 image_url、落库换图、审计、image_replaced 埋点、复用原单据重跑。"""
    rid, orig = _mk_receipt(tmp_path, status="edited")
    r = client.post(
        f"/api/receipt/{rid}/replace-image",
        files={"receipt": ("retake.png", _sharp_png_bytes(), "image/png")},
        data={"force": "false"},
        headers={"X-Role": "staff"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["receipt_id"] == rid
    assert body["job_id"] == "job-test-1"
    assert body["image_url"].startswith("/uploads/")

    # 换图落库：新图存在且指向新文件（旧图保留不删）
    row = db.get_receipt_row(rid)
    assert row.image_path != str(orig)
    assert os.path.exists(row.image_path)
    assert os.path.exists(str(orig)), "旧图应保留可审计"

    # 审计日志含 replace_image 动作（old/new 为文件名）
    logs = json.loads(row.audit_logs_json or "[]")
    rep = [l for l in logs if l.get("action") == "replace_image"]
    assert len(rep) == 1
    assert rep[0]["field"] == "image_path"
    assert rep[0]["old"] == "orig_receipt.jpg"
    assert rep[0]["new"] == os.path.basename(row.image_path)

    # image_replaced 埋点：含 old_image + trigger=retake，关联原单据
    s = db.get_session()
    try:
        evs = s.query(db._UserEventRow).filter_by(
            event_type="image_replaced").all()
        assert len(evs) == 1
        assert evs[0].receipt_id == rid
        props = json.loads(evs[0].properties or "{}")
        assert props["old_image"] == "orig_receipt.jpg"
        assert props["trigger"] == "retake"
    finally:
        s.close()

    # 复用原单据 + 新图派发识别
    assert len(job_stub) == 1
    assert job_stub[0]["receipt_id"] == rid
    assert job_stub[0]["image_path"] == row.image_path


def test_replace_image_status_gate_and_manual(client, job_stub, upload_dir, tmp_path):
    """换图与重试同门：parsing → 409；manual_entry → 400。"""
    rid1, _ = _mk_receipt(tmp_path, status="parsing")
    r = client.post(
        f"/api/receipt/{rid1}/replace-image",
        files={"receipt": ("r.png", _sharp_png_bytes(), "image/png")},
        headers={"X-Role": "staff"})
    assert r.status_code == 409

    rid2, _ = _mk_receipt(tmp_path, status="edited", doc_form="manual_entry")
    r2 = client.post(
        f"/api/receipt/{rid2}/replace-image",
        files={"receipt": ("r.png", _sharp_png_bytes(), "image/png")},
        headers={"X-Role": "staff"})
    assert r2.status_code == 400
    assert job_stub == []


def test_replace_image_too_small_400(client, job_stub, upload_dir, tmp_path):
    """<30 字节 → 400 IMAGE_QUALITY_ERROR。"""
    rid, _ = _mk_receipt(tmp_path, status="edited")
    r = client.post(
        f"/api/receipt/{rid}/replace-image",
        files={"receipt": ("tiny.jpg", b"xx", "image/jpeg")},
        headers={"X-Role": "staff"})
    assert r.status_code == 400
    assert r.json()["code"] == "IMAGE_QUALITY_ERROR"
    assert job_stub == []


def test_replace_image_blur_blocked_and_force_bypass(client, job_stub,
                                                     upload_dir, tmp_path):
    """极模糊硬拦截 400；用户确认后（force=true）放行换图。"""
    rid, _ = _mk_receipt(tmp_path, status="edited")
    r = client.post(
        f"/api/receipt/{rid}/replace-image",
        files={"receipt": ("blur.png", _blur_png_bytes(), "image/png")},
        data={"force": "false"},
        headers={"X-Role": "staff"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "IMAGE_QUALITY_ERROR"
    assert "image_blur" in body["quality_warnings"]
    assert job_stub == []

    # force=true → 绕过模糊拦截，正常换图重跑
    r2 = client.post(
        f"/api/receipt/{rid}/replace-image",
        files={"receipt": ("blur.png", _blur_png_bytes(), "image/png")},
        data={"force": "true"},
        headers={"X-Role": "staff"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "queued"
    assert len(job_stub) == 1


def test_replace_image_not_found_404(client, job_stub, upload_dir):
    r = client.post(
        "/api/receipt/999999/replace-image",
        files={"receipt": ("r.png", _sharp_png_bytes(), "image/png")},
        headers={"X-Role": "staff"})
    assert r.status_code == 404
    assert job_stub == []


# -------------------------------------------------------------
# 3. recovery-summary（RBAC + 多租户 + 口径）
# -------------------------------------------------------------
def test_recovery_summary_rbac(client):
    assert client.get("/api/analytics/recovery-summary",
                      headers={"X-Role": "staff"}).status_code == 403
    assert client.get("/api/analytics/recovery-summary",
                      headers={"X-Role": "owner"}).status_code == 403
    assert client.get("/api/analytics/recovery-summary",
                      headers={"X-Role": "admin"}).status_code == 200


def test_recovery_summary_multi_tenant(client):
    """多租户隔离统计与聚合测试。"""
    # 租户 A 事件：2 次解析 + 1 次重拍
    db.log_user_event(event_type="ocr_parsed", tenant_id="tenant_a")
    db.log_user_event(event_type="ocr_parsed", tenant_id="tenant_a")
    db.log_user_event(event_type="retake_clicked", tenant_id="tenant_a")
    # 租户 B 事件：1 次解析 + 1 次重解析
    db.log_user_event(event_type="ocr_parsed", tenant_id="tenant_b")
    db.log_user_event(event_type="reparse_clicked", tenant_id="tenant_b")

    # 查租户 A
    res_a = client.get("/api/analytics/recovery-summary?tenant_id=tenant_a",
                       headers={"X-Role": "admin"}).json()["data"]
    assert res_a["total_events"] == 3
    assert res_a["recovery"]["retake_clicked"] == 1
    assert res_a["recovery"]["reparse_clicked"] == 0
    assert res_a["tenant_id"] == "tenant_a"

    # 查租户 B
    res_b = client.get("/api/analytics/recovery-summary?tenant_id=tenant_b",
                       headers={"X-Role": "admin"}).json()["data"]
    assert res_b["total_events"] == 2
    assert res_b["recovery"]["retake_clicked"] == 0
    assert res_b["recovery"]["reparse_clicked"] == 1
    assert res_b["tenant_id"] == "tenant_b"

    # 全量聚合
    res_all = client.get("/api/analytics/recovery-summary?tenant_id=all",
                         headers={"X-Role": "admin"}).json()["data"]
    assert res_all["total_events"] == 5
    assert res_all["recovery"]["total"] == 2
    assert res_all["tenant_id"] == "all"
    assert "tenant_a" in res_all["available_tenants"]
    assert "tenant_b" in res_all["available_tenants"]


def test_recovery_summary_metrics(client, tmp_path):
    """预置事件 → 挽回占比/归因拆分、点踩率、挽回成功率、事件分布全部正确。"""
    rid_a = db.create_receipt(supplier_name="祥興", status="edited")
    rid_b = db.create_receipt(supplier_name="新記", status="edited")

    # 分母事件：4 次解析成功
    for _ in range(4):
        db.log_user_event(event_type="ocr_parsed", properties={"elapsed_ms": 900})
    # 挽回点击：2 次重拍（1 次带单据、1 次无单据）+ 1 次重新解析（带单据）
    db.log_user_event(event_type="retake_clicked", receipt_id=rid_a,
                      properties={"prior_feedback": -1})
    db.log_user_event(event_type="retake_clicked",
                      properties={"prior_feedback": None})
    db.log_user_event(event_type="reparse_clicked", receipt_id=rid_a,
                      properties={"prior_feedback": None})
    # 挽回成功：rid_a 在点击之后提交复核（同秒时以自增 id 定序）
    db.log_user_event(event_type="receipt_review_submitted", receipt_id=rid_a,
                      properties={"rows_modified": 1})
    # 干扰事件：与挽回无关的埋点
    db.log_user_event(event_type="upload")
    # 点踩/点赞：1 踩(rid_a) + 1 赞(rid_b)
    db.upsert_receipt_feedback(rid_a, like=-1, comment="识别错行")
    db.upsert_receipt_feedback(rid_b, like=1)

    body = client.get("/api/analytics/recovery-summary",
                      headers={"X-Role": "admin"}).json()
    assert body["status"] == "success"
    d = body["data"]

    # 全量事件分布：计数降序、占比正确
    dist = {e["event_type"]: e for e in d["event_distribution"]}
    assert dist["ocr_parsed"]["count"] == 4
    assert dist["retake_clicked"]["count"] == 2
    assert dist["reparse_clicked"]["count"] == 1
    assert d["total_events"] == 9
    assert abs(dist["ocr_parsed"]["share"] - 4 / 9) < 1e-3
    counts_seq = [e["count"] for e in d["event_distribution"]]
    assert counts_seq == sorted(counts_seq, reverse=True), "应按计数降序"

    # 挽回点击归因拆分：重拍(输入归因)2 / 重解析(模型归因)1，入口率 3/4
    rec = d["recovery"]
    assert rec["retake_clicked"] == 2 and rec["reparse_clicked"] == 1
    assert rec["total"] == 3
    assert abs(rec["input_attribution_share"] - 2 / 3) < 1e-3
    assert abs(rec["model_attribution_share"] - 1 / 3) < 1e-3
    assert abs(rec["entry_rate"] - 3 / 4) < 1e-3

    # 点踩率：1 踩 / 2 张有反馈单据
    fb = d["feedback"]
    assert fb["down"] == 1 and fb["feedbacked_receipts"] == 2
    assert abs(fb["down_rate"] - 0.5) < 1e-3

    # 挽回成功率：带单据的 2 次点击均有更晚的成功事件；无单据那次不计成功
    rs = d["recovery_success"]
    assert rs["success"] == 2 and rs["total"] == 3
    assert abs(rs["rate"] - 2 / 3) < 1e-3

    # 最近事件流 + 小样本标记
    assert len(d["recent_events"]) <= 50 and len(d["recent_events"]) == 9
    assert d["low_confidence"] is True


def test_recovery_summary_empty(client):
    """空库不崩：三指标为 None/0，low_confidence=True。"""
    d = client.get("/api/analytics/recovery-summary",
                   headers={"X-Role": "admin"}).json()["data"]
    assert d["total_events"] == 0
    assert d["event_distribution"] == []
    assert d["recovery"]["total"] == 0
    assert d["recovery"]["entry_rate"] is None
    assert d["feedback"]["down_rate"] is None
    assert d["recovery_success"]["rate"] is None
    assert d["low_confidence"] is True


# -------------------------------------------------------------
# 4. 回归：/api/track 前端挽回事件入库不受影响
# -------------------------------------------------------------
def test_track_frontend_recovery_events(client):
    for ev in ("retake_clicked", "reparse_clicked"):
        r = client.post("/api/track",
                        json={"event_type": ev, "receipt_id": None,
                              "properties": {"prior_feedback": -1}},
                        headers={"X-Role": "staff"})
        assert r.status_code == 200 and r.json()["status"] == "ok"
    s = db.get_session()
    try:
        rows = s.query(db._UserEventRow).filter(
            db._UserEventRow.event_type.in_(
                ["retake_clicked", "reparse_clicked"])).all()
    finally:
        s.close()
    assert len(rows) == 2
    for row in rows:
        assert json.loads(row.properties)["prior_feedback"] == -1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

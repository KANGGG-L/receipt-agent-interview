# -*- coding: utf-8 -*-
"""T6 线上低置信样本自动回流为评测候选（Gap A5）测试。

覆盖：
1. 四类触发各产生候选（低置信 / 门禁拒绝 / 用户修改 / 审核分歧），
   前三类经 run_pipeline 真实编排路径（mock extract/audit，零外部调用），
   user_edit 经 /api/save_edited 真实 API 路径
2. 高置信 + 无分歧不产生候选；钩子失败不阻断识别主链路
3. 每单每 reason 幂等（同单同 reason 不重复建，不同 reason 各建一条）
4. promote：pending -> promoted_to_val / promoted_to_test，原图与 AI 候选 GT
   复制进 demo/evalsets/（临时目录验证），manifest 同步追加行（sample_id 顺延）
5. promote 需 admin；对已 promote/rejected 的候选再 promote 返回 409；reject 流转
6. run_eval 可消费 promote 产物（stub engine 出分且新 sample_id 入报告）

全部离线：图片用 PIL 合成，数据为假数据，零外部调用。
"""

import csv
import json
import os
import sys

os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_eval_reflow.db")
os.environ.setdefault("AUTH_ENABLED", "0")

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(DEMO_DIR, "scripts")
for _p in (SCRIPTS_DIR, DEMO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

import run_eval


# ------------------------------------------------------------------
# 夹具与假数据
# ------------------------------------------------------------------
AI_GT = {
    "supplier_name": "祥興食品",
    "date": "2026-08-01",
    "total_amount": 50.0,
    "items": [
        {"name": "菜心", "quantity": 5.0, "unit": "斤", "unit_price": 10.0, "amount": 50.0},
    ],
}
ENGINE_NAME = "opencode"


def _make_png(path, size=(1100, 800)):
    from PIL import Image
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    Image.new("RGB", size, "white").save(str(path))
    return str(path)


def _read_manifest(evalset_dir):
    with open(os.path.join(evalset_dir, "manifest.csv"), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_manifest(evalset_dir, rows):
    from app.api_evalset import MANIFEST_COLUMNS
    with open(os.path.join(evalset_dir, "manifest.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


_SEEDED_GT = {
    "supplier_name": "Seed Supplier", "date": "2026-07-01", "total_amount": 10.0,
    "items": [{"name": "豆", "quantity": 1.0, "unit": "斤", "unit_price": 10.0, "amount": 10.0}],
}


@pytest.fixture
def evalset_env(tmp_path, monkeypatch):
    """临时评测集：manifest 预置 S001-S003（val split）+ 图片 + expected。"""
    evalset_dir = str(tmp_path / "evalsets")
    os.makedirs(os.path.join(evalset_dir, "receipts"), exist_ok=True)
    os.makedirs(os.path.join(evalset_dir, "expected"), exist_ok=True)
    rows = []
    for i in (1, 2, 3):
        sid = "S%03d" % i
        _make_png(os.path.join(evalset_dir, "receipts", sid + ".png"))
        gt = dict(_SEEDED_GT)
        gt.update({"gt_status": "confirmed", "gt_source_model": "seed"})
        with open(os.path.join(evalset_dir, "expected", sid + ".json"), "w", encoding="utf-8") as f:
            json.dump(gt, f, ensure_ascii=False)
        rows.append({
            "sample_id": sid, "image": "receipts/%s.png" % sid, "split": "val",
            "doc_form": "printed_delivery_note", "layout_type": "", "supplier_id": "",
            "gt_status": "confirmed", "gt_source_model": "seed", "src_sha1": "",
            "short_side": "800",
        })
    _write_manifest(evalset_dir, rows)
    monkeypatch.setenv("EVALSET_DIR", evalset_dir)
    return evalset_dir


@pytest.fixture
def client(evalset_env):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _admin_headers(role="admin"):
    return {"X-Role": role}


def _mk_receipt(**fields):
    from app import db
    rid = db.create_receipt(status="uploaded")
    db.update_receipt(rid, image_path=_make_png(fields.pop("image_path", "/tmp/eval_reflow_%s.png" % db.new_id())),
                      **fields)
    return rid


def _candidate_for(rid, reason):
    from app import db
    rows = [c for c in db.list_eval_candidates(reason=reason) if c.receipt_id == rid]
    return rows[0] if rows else None


def _fake_extract(confidence=0.9, gate_fail=False):
    """构造 mock 单轮 extract 返回值（不调外部 LLM）。"""
    from app.models import ReceiptData
    data = ReceiptData(doc_form="printed_delivery_note", vendor="祥興食品",
                       date="2026-08-01",
                       items=[{"name": "菜心", "qty": 5, "unit": "斤",
                               "unit_price": 10, "amount": 50}],
                       total=60.0 if gate_fail else 50.0,
                       payment_marked=False, confidence=confidence)
    return {"data": None if confidence is None else data, "raw": "raw-mock",
            "error": "", "elapsed_ms": 1, "engine": ENGINE_NAME,
            "vendor_context": ""}


def _patch_pipeline(monkeypatch, extract_result, audit_result=None):
    from app.chains import supervisor
    monkeypatch.setattr(supervisor, "_run_extract",
                        lambda *a, **k: extract_result)
    monkeypatch.setattr(supervisor.audit_chain, "run_audit",
                        lambda *a, **k: audit_result or {"skipped": True, "reason": "test_skip"})


# ------------------------------------------------------------------
# 1. 四类触发产生候选
# ------------------------------------------------------------------
def test_low_confidence_creates_candidate(monkeypatch):
    from app.chains import supervisor
    _patch_pipeline(monkeypatch, _fake_extract(confidence=0.3))
    rid = _mk_receipt()
    state = supervisor.run_pipeline("/tmp/nonexistent.jpg", receipt_id=rid)
    assert state["status"] == "parsed"
    cand = _candidate_for(rid, "low_confidence")
    assert cand is not None, "低置信单据必须回流为评测候选"
    assert cand.status == "pending"
    assert cand.confidence is not None and cand.confidence < 0.6
    assert cand.ai_candidate_json, "候选必须带 AI 候选 GT"
    gt = json.loads(cand.ai_candidate_json)
    assert gt["supplier_name"] == "祥興食品"
    assert gt["total_amount"] == 50.0
    assert gt["items"], "AI 候选 GT 必须含明细行"


def test_high_confidence_no_low_confidence_candidate(monkeypatch):
    from app.chains import supervisor
    _patch_pipeline(monkeypatch, _fake_extract(confidence=0.95))
    rid = _mk_receipt()
    supervisor.run_pipeline("/tmp/nonexistent.jpg", receipt_id=rid)
    assert _candidate_for(rid, "low_confidence") is None, \
        "高置信单据不应产生 low_confidence 候选"


def test_gate_reject_creates_candidate(monkeypatch):
    from app.chains import supervisor
    _patch_pipeline(monkeypatch, _fake_extract(confidence=0.9, gate_fail=True))
    rid = _mk_receipt()
    state = supervisor.run_pipeline("/tmp/nonexistent.jpg", receipt_id=rid)
    cand = _candidate_for(rid, "gate_reject")
    assert cand is not None, "门禁拒绝单据必须回流为评测候选"
    assert state["contract_error"], "场景前提：门禁错误信息非空"
    assert cand.note, "候选 note 必须携带门禁摘要"


def test_audit_discrepancy_creates_candidate(monkeypatch):
    from app.chains import supervisor
    audit = {"skipped": False, "overall_consistent": False, "trust": 0.4,
             "discrepancies": [{"field": "total", "issue": "总额与明细合计不符"}]}
    _patch_pipeline(monkeypatch, _fake_extract(confidence=0.9), audit_result=audit)
    rid = _mk_receipt()
    supervisor.run_pipeline("/tmp/nonexistent.jpg", receipt_id=rid)
    cand = _candidate_for(rid, "audit_discrepancy")
    assert cand is not None, "审核分歧单据必须回流为评测候选"


def test_audit_consistent_no_candidate(monkeypatch):
    from app.chains import supervisor
    audit = {"skipped": False, "overall_consistent": True, "trust": 0.95,
             "discrepancies": []}
    _patch_pipeline(monkeypatch, _fake_extract(confidence=0.9), audit_result=audit)
    rid = _mk_receipt()
    supervisor.run_pipeline("/tmp/nonexistent.jpg", receipt_id=rid)
    assert _candidate_for(rid, "audit_discrepancy") is None


def test_user_edit_creates_candidate(client):
    """人工保存与 AI 预填存在差异 -> user_edit 候选。"""
    from app import db
    rid = _mk_receipt()
    prefill = {"vendor": "祥興食品", "date": "2026-08-01", "total": 50.0,
               "items": [{"name": "菜心", "qty": 5, "unit": "斤",
                          "unit_price": 10, "amount": 50}]}
    db.update_receipt(rid, ai_prefill_json=json.dumps(prefill, ensure_ascii=False))
    edited = json.loads(json.dumps(AI_GT))
    edited["total_amount"] = 65.0  # 与 AI 预填不同
    resp = client.post("/api/save_edited", headers={"X-Role": "staff"}, json={
        "receipt_id": rid, "supplier_name": "祥興食品", "date": "2026-08-01",
        "total_amount": 65.0, "settlement_type": "cash",
        "items": [{"name": "菜心", "quantity": 5, "unit": "斤",
                   "unit_price": 13, "amount": 65}],
        "version": 1, "source": "manual",
    })
    assert resp.status_code == 200, resp.text
    cand = _candidate_for(rid, "user_edit")
    assert cand is not None, "人工修改与 AI 预填有差异必须回流为评测候选"


def test_auto_save_no_user_edit_candidate(client):
    """AI 自动落库（source=auto）不产生 user_edit 候选。"""
    from app import db
    rid = _mk_receipt()
    prefill = {"vendor": "祥興食品", "date": "2026-08-01", "total": 50.0,
               "items": [{"name": "菜心", "qty": 5, "unit": "斤",
                          "unit_price": 10, "amount": 50}]}
    db.update_receipt(rid, ai_prefill_json=json.dumps(prefill, ensure_ascii=False))
    resp = client.post("/api/save_edited", headers={"X-Role": "staff"}, json={
        "receipt_id": rid, "supplier_name": "祥興食品", "date": "2026-08-01",
        "total_amount": 50.0, "settlement_type": "cash",
        "items": [{"name": "菜心", "quantity": 5, "unit": "斤",
                   "unit_price": 10, "amount": 50}],
        "version": 1, "source": "auto",
    })
    assert resp.status_code == 200, resp.text
    assert _candidate_for(rid, "user_edit") is None, \
        "无差异的自动落库不应产生 user_edit 候选"


# ------------------------------------------------------------------
# 2. 不阻断主链路 + 幂等
# ------------------------------------------------------------------
def test_reflow_failure_does_not_block_pipeline(monkeypatch, caplog):
    import logging as _logging
    from app import db
    from app.chains import supervisor
    _patch_pipeline(monkeypatch, _fake_extract(confidence=0.3))

    def _boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "create_eval_candidate", _boom)
    rid = _mk_receipt()
    with caplog.at_level(_logging.WARNING, logger="supervisor"):
        state = supervisor.run_pipeline("/tmp/nonexistent.jpg", receipt_id=rid)
    assert state["status"] == "parsed", "候选回流失败不得阻断识别主链路"
    assert any("回流失败" in r.message or "评测候选" in r.message
               for r in caplog.records), "失败必须留 warning 痕迹"


def test_candidate_idempotent_per_receipt_per_reason():
    from app import db
    rid = _mk_receipt()
    id1, created1 = db.create_eval_candidate(rid, "low_confidence", confidence=0.3)
    id2, created2 = db.create_eval_candidate(rid, "low_confidence", confidence=0.3)
    assert created1 is True and created2 is False, "同单同 reason 不得重复建"
    assert id1 == id2
    id3, created3 = db.create_eval_candidate(rid, "gate_reject", note="x")
    assert created3 is True and id3 != id1, "同单不同 reason 各建一条"
    rows = [c for c in db.list_eval_candidates() if c.receipt_id == rid]
    assert len(rows) == 2


# ------------------------------------------------------------------
# 3. db 函数：列表过滤 + 状态流转
# ------------------------------------------------------------------
def test_list_eval_candidates_filters():
    from app import db
    rid = _mk_receipt()
    db.create_eval_candidate(rid, "low_confidence", confidence=0.3)
    db.create_eval_candidate(rid, "gate_reject", note="x")
    only_low = [c for c in db.list_eval_candidates(reason="low_confidence")
                if c.receipt_id == rid]
    assert len(only_low) == 1
    only_pending = [c for c in db.list_eval_candidates(status="pending")
                    if c.receipt_id == rid]
    assert len(only_pending) == 2
    assert not [c for c in db.list_eval_candidates(status="promoted_to_val")
                if c.receipt_id == rid]


def test_set_status_transitions_and_conflict():
    from app import db
    rid = _mk_receipt()
    cid, _ = db.create_eval_candidate(rid, "low_confidence")
    row, err = db.set_eval_candidate_status(cid, "promoted_to_test")
    assert err is None and row.status == "promoted_to_test"
    assert row.promoted_at, "promote 必须写 promoted_at"
    row2, err2 = db.set_eval_candidate_status(cid, "rejected")
    assert err2 == "CONFLICT", "非 pending 状态再流转必须冲突"
    assert row2.status == "promoted_to_test"

    cid2, _ = db.create_eval_candidate(rid, "gate_reject")
    row3, err3 = db.set_eval_candidate_status(cid2, "rejected")
    assert err3 is None and row3.status == "rejected"
    assert not row3.promoted_at, "reject 不写 promoted_at"
    _, err4 = db.set_eval_candidate_status(99999, "rejected")
    assert err4 == "NOT_FOUND"
    _, err5 = db.set_eval_candidate_status(cid2, "bogus_status")
    assert err5 == "INVALID_STATUS"


# ------------------------------------------------------------------
# 4. promote 流程
# ------------------------------------------------------------------
def _mk_candidate_with_image(evalset_dir=None):
    """建一张带原图与 AI 候选 GT 的 pending 候选，返回 (candidate_id, receipt_id, 图片路径)。"""
    from app import db
    from app.chains import supervisor
    img = "/tmp/eval_reflow_src_%s.png" % db.new_id()
    rid = _mk_receipt(image_path=img)
    gt = dict(AI_GT)
    gt["gt_source_model"] = ENGINE_NAME
    cid, _ = db.create_eval_candidate(rid, "low_confidence", confidence=0.3,
                                      doc_form="printed_delivery_note",
                                      ai_candidate=gt)
    return cid, rid, img


def test_promote_requires_admin(client):
    cid, _, _ = _mk_candidate_with_image()
    for role in ("owner", "staff"):
        resp = client.post("/api/evalset/candidates/%d/promote?split=val" % cid,
                           headers={"X-Role": role})
        assert resp.status_code == 403, "%s 不应允许 promote" % role
    assert client.get("/api/evalset/candidates", headers={"X-Role": "owner"}).status_code == 403
    assert client.post("/api/evalset/candidates/%d/reject" % cid,
                       headers={"X-Role": "owner"}).status_code == 403


def test_promote_to_val_copies_image_and_gt(client, evalset_env):
    cid, rid, img = _mk_candidate_with_image()
    resp = client.post("/api/evalset/candidates/%d/promote?split=val" % cid,
                       headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    new_sid = data["sample_id"]
    assert new_sid == "S004", "sample_id 必须顺延现有最大编号"
    assert data["split"] == "val"

    evalset_dir = evalset_env
    # 原图复制进 evalsets/receipts/
    copied = os.path.join(evalset_dir, "receipts", new_sid + ".png")
    assert os.path.exists(copied), "原图必须复制进 evalsets/receipts/"
    with open(img, "rb") as f1, open(copied, "rb") as f2:
        assert f1.read() == f2.read(), "复制件必须与原图字节一致"
    # AI 候选 GT 复制进 evalsets/expected/（draft 状态）
    with open(os.path.join(evalset_dir, "expected", new_sid + ".json"),
              encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["gt_status"] == "draft"
    assert saved["gt_source_model"] == ENGINE_NAME, "必须记录 AI 引擎名"
    assert saved["supplier_name"] == AI_GT["supplier_name"]
    assert saved["total_amount"] == AI_GT["total_amount"]

    # manifest 追加行
    rows = _read_manifest(evalset_dir)
    row = [r for r in rows if r["sample_id"] == new_sid]
    assert len(row) == 1, "manifest 必须恰好追加一行"
    row = row[0]
    assert row["split"] == "val"
    assert row["image"] == "receipts/%s.png" % new_sid
    assert row["gt_status"] == "draft"
    assert row["doc_form"] == "printed_delivery_note"
    # 既有行不被破坏
    assert len(rows) == 4 and rows[0]["sample_id"] == "S001"

    # 候选状态流转
    from app import db
    cand = db.get_eval_candidate(cid)
    assert cand.status == "promoted_to_val"
    assert cand.promoted_at


def test_promote_to_test_split(client, evalset_env):
    cid, _, _ = _mk_candidate_with_image()
    resp = client.post("/api/evalset/candidates/%d/promote?split=test" % cid,
                       headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    from app import db
    assert db.get_eval_candidate(cid).status == "promoted_to_test"
    rows = _read_manifest(evalset_env)
    assert [r for r in rows if r["sample_id"] == "S004"][0]["split"] == "test"


def test_promote_invalid_split_400(client, evalset_env):
    cid, _, _ = _mk_candidate_with_image()
    resp = client.post("/api/evalset/candidates/%d/promote?split=train" % cid,
                       headers=_admin_headers())
    assert resp.status_code == 400, "train 不允许作为 promote 目标 split"


def test_promote_twice_conflict_409(client, evalset_env):
    cid, _, _ = _mk_candidate_with_image()
    first = client.post("/api/evalset/candidates/%d/promote?split=val" % cid,
                        headers=_admin_headers())
    assert first.status_code == 200
    second = client.post("/api/evalset/candidates/%d/promote?split=test" % cid,
                         headers=_admin_headers())
    assert second.status_code == 409, "已 promote 的候选再 promote 必须冲突"
    # 不得重复写 manifest
    rows = _read_manifest(evalset_env)
    assert len([r for r in rows if r["sample_id"] == "S004"]) == 1


def test_promote_after_reject_conflict_409(client, evalset_env):
    cid, _, _ = _mk_candidate_with_image()
    assert client.post("/api/evalset/candidates/%d/reject" % cid,
                       headers=_admin_headers()).status_code == 200
    resp = client.post("/api/evalset/candidates/%d/promote?split=val" % cid,
                       headers=_admin_headers())
    assert resp.status_code == 409, "已 rejected 的候选不得 promote"


def test_reject_flow_and_list_filters(client, evalset_env):
    cid_ok, _, _ = _mk_candidate_with_image()
    cid_rj, _, _ = _mk_candidate_with_image()
    assert client.post("/api/evalset/candidates/%d/reject" % cid_rj,
                       headers=_admin_headers()).status_code == 200

    resp = client.get("/api/evalset/candidates?status=pending", headers=_admin_headers())
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()["data"]["candidates"]]
    assert cid_ok in ids and cid_rj not in ids

    resp2 = client.get("/api/evalset/candidates?status=rejected", headers=_admin_headers())
    rej_ids = [c["id"] for c in resp2.json()["data"]["candidates"]]
    assert cid_rj in rej_ids and cid_ok not in rej_ids

    resp3 = client.get("/api/evalset/candidates?reason=low_confidence&status=pending",
                       headers=_admin_headers())
    ids3 = [c["id"] for c in resp3.json()["data"]["candidates"]]
    assert cid_ok in ids3 and cid_rj not in ids3
    # 人话字段：列表必须带 reason 与 note，便于人工判断
    item = [c for c in resp3.json()["data"]["candidates"] if c["id"] == cid_ok][0]
    assert item["reason"] == "low_confidence"
    assert "confidence" in item and "receipt_id" in item


def test_reject_unknown_candidate_404(client, evalset_env):
    resp = client.post("/api/evalset/candidates/999999/reject", headers=_admin_headers())
    assert resp.status_code == 404


def test_promote_without_ai_candidate_409(client, evalset_env):
    """识别未产出结构化结果（无 AI 候选 GT）的候选不可直接 promote。"""
    from app import db
    rid = _mk_receipt()
    cid, _ = db.create_eval_candidate(rid, "gate_reject", ai_candidate={})
    resp = client.post("/api/evalset/candidates/%d/promote?split=val" % cid,
                       headers=_admin_headers())
    assert resp.status_code == 409
    assert "AI 候选" in resp.json()["msg"], "409 提示必须说明缺少 AI 候选 GT"


def test_promote_missing_source_image_409(client, evalset_env):
    from app import db
    rid = _mk_receipt()
    db.update_receipt(rid, image_path="/tmp/eval_reflow_nonexistent_%s.png" % db.new_id())
    gt = dict(AI_GT)
    gt["gt_source_model"] = ENGINE_NAME
    cid, _ = db.create_eval_candidate(rid, "low_confidence", ai_candidate=gt)
    resp = client.post("/api/evalset/candidates/%d/promote?split=val" % cid,
                       headers=_admin_headers())
    assert resp.status_code == 409
    assert "原图" in resp.json()["msg"], "409 提示必须说明原图缺失"


# ------------------------------------------------------------------
# 5. run_eval 消费 promote 产物
# ------------------------------------------------------------------
def test_run_eval_consumes_promoted_sample(client, evalset_env):
    cid, _, _ = _mk_candidate_with_image()
    assert client.post("/api/evalset/candidates/%d/promote?split=val" % cid,
                       headers=_admin_headers()).status_code == 200
    # manifest 行合法性：sample_id/image/split/doc_form 齐备且 image 文件真实存在
    rows = _read_manifest(evalset_env)
    new_rows = [r for r in rows if r["sample_id"] == "S004"]
    assert new_rows and new_rows[0]["split"] == "val" and new_rows[0]["doc_form"]
    assert os.path.exists(os.path.join(evalset_env, new_rows[0]["image"]))

    report = run_eval.run_eval(split="val", engine="stub", evalset_dir=evalset_env,
                               report_dir=os.path.join(evalset_env, "reports"))
    assert "S004" in report["sample_ids"], "promote 产物必须可被 run_eval 消费"
    assert report["n_samples"] == 4


# ------------------------------------------------------------------
# 6. 前端：抽检台有回流候选入口（静态断言，允许简化实现）
# ------------------------------------------------------------------
def test_evalset_page_has_reflow_section():
    with open(os.path.join(DEMO_DIR, "templates", "evalset.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(DEMO_DIR, "static", "js", "evalset.js"), encoding="utf-8") as f:
        js = f.read()
    assert "回流候选" in html, "抽检台页面必须有回流候选区块"
    assert "/api/evalset/candidates" in js, "前端必须消费候选 API"
    assert "promote" in js and "reject" in js, "前端必须提供 promote/reject 操作"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

# -*- coding: utf-8 -*-
"""M2（P14 残留）：gate_reject_fast 决策项不得再写 success=True。

why: T2 已把「带警告通过」的单据状态改为 parsed_with_warnings、（管线层）success=False，
但 fast_gate 分支在写 ai_decision_log 时仍显式传了 success=True，于是
GET /api/admin/metrics 的 success_rate 依旧把这类门禁未过的单据计为成功——
用户在管理台看到的成功率与单据真实状态不一致，虚假成功信号仍在。

修法：删除该处显式 success=True，改由 _log_extract_decision 内部默认推断
（supervisor.py 的 `success = status in ("extract_ok", "parse_ok")`），
即 gate_reject_fast → success=False。

本用例覆盖两个方向（后者是反证，防止把 metric 改成一味不动）：
- 门禁失败走 fast_gate → 决策日志 extra.success/ai_value.success 均为 False，
  /api/admin/metrics 的 success_rate 不把它计为成功；
- 门禁通过走 extract_ok → 决策日志 success=True，metrics 正常计入。

全程用隔离临时库，不调 VLM、不写 live 库、不落 artifacts 记忆文件。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import db
from app.chains import supervisor
from app.models import DocForm, ReceiptData, ReceiptItem


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """沿用本项目既有的隔离模式：DB_PATH 指向临时库，跑完还原。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_gate_reject_fast_decision.db")
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


def _receipt_data(items, total):
    return ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="测试供应商",
        date="2026-09-19",
        items=items,
        total=total,
        payment_marked=True,
        confidence=0.9,
    )


def _patch_pipeline(monkeypatch, data):
    """只走门禁分支：不调 VLM、不做解析级修正、审核跳过、不落 artifacts 记忆文件。"""
    monkeypatch.setattr(supervisor, "_run_extract",
                        lambda *a, **kw: {
                            "raw": '{"vendor": "测试供应商"}',
                            "elapsed_ms": 1,
                            "error": "",
                            "extract_ms": 1.0,
                            "rag_ms": 0.0,
                            "parse_llm": {},
                            "token_usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                            "total_tokens": 2},
                            "cost_hkd": 0.0,
                            "engine": "fake-engine",
                            "data": data,
                            "vendor_context": "",
                        })
    monkeypatch.setattr(supervisor.extract_chain, "correct_receipt_with_feedback",
                        lambda *a, **kw: None)
    monkeypatch.setattr(supervisor, "_run_audit",
                        lambda *a, **kw: {"skipped": True, "reason": "test"})
    # 避免写仓库内 artifacts/memory/parse_log.jsonl 与起 audit 守护线程
    monkeypatch.setattr(supervisor, "_finalize", lambda state, t: state)
    monkeypatch.setattr(supervisor, "_log_audit_decision", lambda *a, **kw: None)


def _extract_rows(receipt_id):
    """取该单据的 extract 决策行（SQLAlchemy 行对象）。"""
    s = db.get_session()
    try:
        return (s.query(db._DecisionLogRow)
                .filter(db._DecisionLogRow.decision_type == "extract")
                .filter(db._DecisionLogRow.receipt_id == receipt_id)
                .all())
    finally:
        s.close()


def _ai_value_status(row):
    try:
        av = json.loads(row.ai_value) if row.ai_value and row.ai_value.strip().startswith("{") else {}
    except Exception:
        av = {}
    return av.get("status") if isinstance(av, dict) else None


def _metrics(client):
    resp = client.get("/api/admin/metrics", headers={"X-Role": "admin"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_gate_reject_fast_decision_is_not_success(monkeypatch, client):
    """门禁未过（算术不符）→ 决策日志 success 必须为 False，metrics 不计成功。"""
    data = _receipt_data(
        [ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=100, amount=100)],
        total=999,
    )
    _patch_pipeline(monkeypatch, data)
    rid = db.create_receipt(supplier_name="测试供应商", status="uploading")

    state = supervisor.run_pipeline("uploads/m2_gate_fast.jpg", receipt_id=rid)

    assert state["status"] == "parsed_with_warnings"
    assert state["success"] is False

    rows = _extract_rows(rid)
    fast = [r for r in rows if _ai_value_status(r) == "gate_reject_fast"]
    assert fast, "fast_gate 分支必须落一条 extract 决策日志"
    for r in fast:
        extra = json.loads(r.extra) if r.extra else {}
        assert extra.get("success") is False, "决策日志 extra.success 不得再写 True"
        av = json.loads(r.ai_value) if r.ai_value and r.ai_value.strip().startswith("{") else {}
        assert av.get("success") is False, "ai_value.success 不得再写 True"

    body = _metrics(client)
    assert body["db_count"] >= 1, body
    assert body["success_count"] == 0, body
    assert body["success_rate"] == 0, "success_rate 不得把门禁未过的单据计为成功"


def test_clean_extract_decision_still_counts_as_success(monkeypatch, client):
    """反证方向：门禁通过仍写 success=True 且 metrics 正常计入（防改坏正常路径）。"""
    data = _receipt_data(
        [ReceiptItem(name="菜心", qty=2, unit="斤", unit_price=50, amount=100)],
        total=100,
    )
    _patch_pipeline(monkeypatch, data)
    rid = db.create_receipt(supplier_name="测试供应商", status="uploading")

    state = supervisor.run_pipeline("uploads/m2_clean.jpg", receipt_id=rid)

    assert state["status"] == "parsed"
    assert state["success"] is True

    rows = _extract_rows(rid)
    ok = [r for r in rows if _ai_value_status(r) == "extract_ok"]
    assert ok, "门禁通过必须落 extract_ok 决策日志"
    for r in ok:
        extra = json.loads(r.extra) if r.extra else {}
        assert extra.get("success") is True

    body = _metrics(client)
    assert body["success_count"] >= 1, body
    assert body["success_rate"] > 0, body

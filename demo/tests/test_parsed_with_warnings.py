# -*- coding: utf-8 -*-
"""T2（P14+P15）回归：门禁失败的"带警告通过"不得被当成解析成功。

why: 修复前 supervisor 的 fast_gate 分支（算术/契约/总额/明细为空）只要有
fallback_data 就静默 `state["success"] = True` 并 break，收尾处又把 status 写成
"parsed"，save_parsed_data 也无条件落 status="parsed"。于是「算术对不上 / 明细为空」
的单据在前端显示「待核对」如同正常解析，只在 math_warnings_json 里留痕——这是用户
「感觉识别不准但系统说成功」的直接来源。

本用例覆盖两个方向：
- 门禁失败（算术不符 / 明细为空）→ status=parsed_with_warnings、success=False、
  math_warnings_json 非空；
- 门禁通过 → 仍为 parsed（防止把正常路径改坏）。

全程用隔离临时库，不碰 live 库；不调用任何真实 VLM。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

import pytest

from app import db
from app.chains import supervisor
from app.models import DocForm, ReceiptData, ReceiptItem
from app.services import receipt_utils


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """沿用 test_job_writeback_none_guard 的隔离模式：DB_PATH 指向临时库，跑完还原。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_parsed_with_warnings.db")
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


def _fake_extract_payload(data):
    """构造 _run_extract 的最小返回结构（keys 与 run_pipeline 消费点一致）。"""
    return {
        "raw": '{"vendor": "测试供应商"}',
        "elapsed_ms": 1,
        "error": "",
        "extract_ms": 1.0,
        "rag_ms": 0.0,
        "parse_llm": {},
        "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "cost_hkd": 0.0,
        "engine": "fake-engine",
        "data": data,
        "vendor_context": "",
    }


def _patch_pipeline(monkeypatch, data):
    """让 run_pipeline 只走门禁分支：不调 VLM、不做解析级修正、审核跳过。"""
    monkeypatch.setattr(supervisor, "_run_extract",
                        lambda *a, **kw: _fake_extract_payload(data))
    monkeypatch.setattr(supervisor.extract_chain, "correct_receipt_with_feedback",
                        lambda *a, **kw: None)
    monkeypatch.setattr(supervisor, "_run_audit",
                        lambda *a, **kw: {"skipped": True, "reason": "test"})


def test_arithmetic_mismatch_marks_parsed_with_warnings(monkeypatch):
    """算术不符：明细合计 100 但总额 999 → 带警告通过，不再冒充干净 parsed。"""
    data = _receipt_data(
        [ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=100, amount=100)],
        total=999,
    )
    _patch_pipeline(monkeypatch, data)

    state = supervisor.run_pipeline("uploads/t2_arith.jpg")

    assert state["status"] == "parsed_with_warnings"
    assert state["success"] is False
    assert state["gate_warnings"], state
    assert "算术门禁" in state["gate_warnings"][0]
    assert state["math_problems"], state
    assert state["data"] is not None, "带警告仍须保留识别结构供人工复核"


def test_empty_items_marks_parsed_with_warnings(monkeypatch):
    """明细为空：契约门禁拒绝 → 同样带警告通过。"""
    data = _receipt_data([], total=100)
    _patch_pipeline(monkeypatch, data)

    state = supervisor.run_pipeline("uploads/t2_empty.jpg")

    assert state["status"] == "parsed_with_warnings"
    assert state["success"] is False
    assert any("明细为空" in w for w in state["gate_warnings"]), state


def test_clean_gates_still_parsed(monkeypatch):
    """反证方向：门禁通过仍为干净的 parsed + success=True（防改坏正常路径）。"""
    data = _receipt_data(
        [ReceiptItem(name="菜心", qty=2, unit="斤", unit_price=50, amount=100)],
        total=100,
    )
    _patch_pipeline(monkeypatch, data)

    state = supervisor.run_pipeline("uploads/t2_clean.jpg")

    assert state["status"] == "parsed"
    assert state["success"] is True
    assert not state.get("gate_warnings"), state


def test_save_parsed_data_persists_warning_status():
    """落库：result 带 parsed_with_warnings 时，不再无条件写 parsed。"""
    rid = db.create_receipt(supplier_name="测试供应商", status="uploading")
    data = _receipt_data(
        [ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=100, amount=100)],
        total=999,
    )
    result = {
        "status": "parsed_with_warnings",
        "math_problems": ["算术门禁: 明细合计=100 预期总额=100，但总额=999（差-899）"],
        "math_warnings": ["算术门禁: 明细合计=100 预期总额=100，但总额=999（差-899）"],
        "raw": "",
        "use_grey": 0,
        "audit_result": {},
    }

    receipt_utils.save_parsed_data(rid, data, result)

    row = db.get_receipt_row(rid)
    assert row is not None
    assert row.status == "parsed_with_warnings"
    warnings = json.loads(row.math_warnings_json or "[]")
    assert warnings, "门禁警告必须落库，否则前端看不到问题"


def test_save_parsed_data_defaults_to_parsed_without_status():
    """兜底：result 未带 status（旧调用方）仍落 parsed，行为不回归。"""
    rid = db.create_receipt(supplier_name="测试供应商", status="uploading")
    data = _receipt_data(
        [ReceiptItem(name="菜心", qty=1, unit="斤", unit_price=100, amount=100)],
        total=100,
    )

    receipt_utils.save_parsed_data(rid, data, {"raw": "", "use_grey": 0, "audit_result": {}})

    row = db.get_receipt_row(rid)
    assert row is not None
    assert row.status == "parsed"


def test_build_row_exposes_math_warnings():
    """列表行透出门禁警告，供前端对 parsed_with_warnings 醒目标出。"""
    rid = db.create_receipt(supplier_name="测试供应商", status="parsed_with_warnings")
    db.update_receipt(rid, math_warnings_json=json.dumps(["算术门禁: 差-899"], ensure_ascii=False))

    row = db.get_receipt_row(rid)
    payload = receipt_utils.build_row(row)

    assert payload["status"] == "parsed_with_warnings"
    assert payload["math_warnings"] == ["算术门禁: 差-899"]


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_auto_save_keeps_warning_status_and_trace(client):
    """识别后自动保存（source=auto）不得把 parsed_with_warnings 降级为 parsed，
    也不能抹掉识别期门禁留痕。"""
    rid = db.create_receipt(supplier_name="测试供应商", status="parsed_with_warnings")
    db.update_receipt(
        rid,
        receipt_date="2026-09-19",
        math_warnings_json=json.dumps(["契约校验失败: payment_marked 缺少必填内容"], ensure_ascii=False),
    )
    row = db.get_receipt_row(rid)

    resp = client.post("/api/save_edited", headers={"X-Role": "staff"}, json={
        "receipt_id": rid,
        "supplier_name": "测试供应商",
        "date": "2026-09-19",
        "total_amount": 100.0,
        "settlement_type": "cash",
        "items": [{"name": "菜心", "quantity": 1.0, "unit": "斤",
                   "unit_price": 100.0, "amount": 100.0}],
        "version": row.version,
        "source": "auto",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json().get("status") == "success", resp.text

    after = db.get_receipt_row(rid)
    assert after.status == "parsed_with_warnings"
    assert json.loads(after.math_warnings_json or "[]") == ["契约校验失败: payment_marked 缺少必填内容"]


def test_approve_accepts_parsed_with_warnings(client):
    """老板仍可对 parsed_with_warnings 单据入账（状态白名单需接纳新状态，不得 409）。"""
    rid = db.create_receipt(supplier_name="测试供应商", status="parsed_with_warnings")
    row = db.get_receipt_row(rid)

    resp = client.post("/api/receipt/%d/approve" % rid,
                       headers={"X-Role": "owner"}, json={"version": row.version})

    assert resp.status_code == 200, resp.text
    assert db.get_receipt_row(rid).status == "approved"


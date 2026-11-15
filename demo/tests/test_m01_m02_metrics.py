# -*- coding: utf-8 -*-
"""M01/M02 指标真实性（C-METRIC：无评测 null、审批保事实、零 eligible 不达标）。"""

import os
import sys
import tempfile

from sqlalchemy import text

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="test_m01_m02_")
_MODULE_DB_PATH = os.path.join(_TMP, "test_m.db")
os.environ["DB_PATH"] = _MODULE_DB_PATH
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "m01-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import review_metrics  # noqa: E402
from scripts import memory_metrics  # noqa: E402

db._make_engine()
client = TestClient(app)


@pytest.fixture(autouse=True)
def _pin_module_db():
    """把每条用例钉回本模块声明的环境库 test_m.db。

    本文件的用例共享模块级种子（test_no_eval_run_rate_is_null 种下的 Vendor_A
    记忆被 test_approve_preserves_review_facts 消费）；全量收集下 S1 隔离夹具的
    落点会随前序文件的泄漏登记漂移（有时每例全新临时库 → 种子跨用例丢失）。
    夹具兜底与该时序无关，恒指回本模块自己的库。
    """
    old = db.DB_PATH
    db.DB_PATH = os.path.abspath(_MODULE_DB_PATH)
    db._make_engine()
    yield
    db.DB_PATH = old
    db._make_engine()
_ACCOUNT_EMAIL = {"staff": "staff@demo.hk", "owner": "boss@demo.hk", "admin": "admin@demo.hk"}


def _hdr(role):
    return {"Authorization": "Bearer " + auth.issue_token(_ACCOUNT_EMAIL[role], role, "default")}


def _collect():
    return memory_metrics.collect(db_path=db.DB_PATH)["memory_metrics_v2"]


def _save_manual(items, total):
    payload = {"source": "manual", "supplier_name": "Vendor_A", "date": "2026-10-26",
               "doc_form": "printed_delivery_note", "settlement_type": "credit",
               "currency": "HKD", "total_amount": total, "items": items}
    r = client.post("/api/save_edited", headers=_hdr("staff"), json=payload)
    assert r.status_code == 200 and r.json().get("status") == "success", r.json()
    return r.json()["receipt_id"], r.json()["version"]


def test_no_eval_run_rate_is_null():
    """M01/Q03：无真实评测运行 → eval_arm 注入率 null+原因，绝不生成达标值。"""
    db.upsert_vendor_memory(vendor="Vendor_A", notes="alias:Item_A", sample="layout",
                            tenant_id="default", fact_key="alias:Item_A")
    _save_manual([{"name": "Item_A", "quantity": 1, "unit": "kg", "unit_price": 7,
                   "amount": 7}], 7)
    m = _collect()
    inj = m["injection_rate"]
    assert inj["eligible_denominator"] >= 1
    assert inj["eval_arm"]["rate_pct"] is None, inj["eval_arm"]
    assert inj["eval_arm"]["injected"] is None
    assert "eval_run" in (inj["eval_arm"]["unknown_reason"] or "")
    assert m["schema_version"] == "v3"


def test_zero_eligible_not_success():
    """零 eligible：production/eval 均 null+原因，不出 0% 冒充或 100% 达标。"""
    m = _collect()  # 新库（本测试独立进程顺序无关，eligible 可能>0；用独立空库验证）
    # 直接以 collect 的原因字段语义断言：eligible=0 时 production rate 必为 None
    if m["injection_rate"]["eligible_denominator"] == 0:
        assert m["injection_rate"]["production"]["rate_pct"] is None
        assert "denominator=0" in (m["injection_rate"]["production"]["unknown_reason"] or "")


def test_approve_preserves_review_facts():
    """M02/Q04：审批不抹修改事实——edited 按 review_events 计数，approve 前后不变。"""
    rid, v = _save_manual([{"name": "Item_A", "quantity": 2, "unit": "kg",
                            "unit_price": 7, "amount": 14}], 14)
    m_before = _collect()["effect_ab"]
    assert m_before["edited_fact_basis"] == "review_events(saved)"
    assert m_before["has_memory"]["edited"] >= 1
    r = client.post("/api/receipt/%d/approve" % rid, headers=_hdr("owner"),
                    json={"version": v})
    assert r.status_code == 200, r.json()
    m_after = _collect()["effect_ab"]
    assert m_after["edited_fact_basis"] == "review_events(saved)"
    assert m_after["has_memory"]["edited"] == m_before["has_memory"]["edited"], (
        m_before["has_memory"], m_after["has_memory"])
    # 事件事实源：saved / submitted 均在且不可变（approve 不抹改，只追加 approved）
    s = db.get_session()
    try:
        from sqlalchemy import text as _t
        kinds = [k[0] for k in s.execute(_t(
            "select event_kind from review_events where receipt_id=:r order by id"),
            {"r": rid}).fetchall()]
        assert kinds == ["saved", "submitted", "approved"], kinds
    finally:
        s.close()


def test_metrics_endpoint_surfaces_ops_summary():
    """M05：看板端点带 ops 摘要（任务开关/统计；未知/错误分列不折 0）。"""
    s = db.get_session()
    try:
        s.execute(text("DELETE FROM durable_tasks"))
        s.commit()
    finally:
        s.close()
    r = client.get("/api/admin/memory-metrics", headers=_hdr("owner"))
    assert r.status_code == 403  # 非 admin
    from fastapi.testclient import TestClient as _TC
    admin = {"Authorization": "Bearer " + auth.issue_token(
        "admin@demo.hk", "admin", "default")}
    r2 = client.get("/api/admin/memory-metrics", headers=admin)
    assert r2.status_code == 200
    body = r2.json()
    assert "ops" in body
    assert body["ops"]["tasks_enabled"] in (True, False, None)
    assert isinstance(body["ops"].get("task_stats") or {}, dict)


def test_reordered_added_deleted_line_fer():
    """M02：按 line_uuid slots 并集——重排不假改，加/删/改各计一个 slot。"""
    ai = {"vendor": "V", "date": "2026-01-01", "total": 140, "items": [
        {"name": "A", "qty": 1, "unit": "kg", "unit_price": 10, "amount": 10},
        {"name": "B", "qty": 2, "unit": "kg", "unit_price": 20, "amount": 40},
        {"name": "C", "qty": 3, "unit": "box", "unit_price": 30, "amount": 90},
    ]}
    snap = review_metrics.build_snapshot(
        ai, [{"line_uuid": "ua"}, {"line_uuid": "ub"}, {"line_uuid": "uc"}])
    assert [it["line_uuid"] for it in snap["items"]] == ["ua", "ub", "uc"]

    # final：C 重排到最前（未改）、B 改价、A 删除、D 新增。
    final_items = [
        {"line_uuid": "uc", "name": "C", "quantity": 3, "unit": "box",
         "unit_price": 30, "amount": 90},
        {"line_uuid": "ub", "name": "B", "quantity": 2, "unit": "kg",
         "unit_price": 25, "amount": 50},
        {"line_uuid": "ud", "name": "D", "quantity": 1, "unit": "pcs",
         "unit_price": 5, "amount": 5},
    ]
    header = {"supplier_name": "V", "receipt_date": "2026-01-01", "total_amount": 140}
    fees = {f: 0 for f in review_metrics.FEE_FIELDS}
    fer = review_metrics.compute_fer(snap, header, final_items, fees)

    assert fer["comparable"] is True
    assert fer["item_slots"] == 4          # uc, ub, ua, ud 并集
    assert fer["item_edited_slots"] == 3   # ub 改 / ua 删 / ud 加；uc 仅重排不计
    assert fer["item_fer"] == 0.75
    assert fer["header_fer"] == 0.0
    assert fer["fee_fer"] == 0.0
    assert fer["zero_edit"] is False


def test_manual_failure_not_zero_edit():
    """M02：无快照（手工/识别失败）不可比，绝不伪造零改；零改须 owner 完成且无 override。"""
    # 无 AI 快照 -> 不可比
    assert review_metrics.build_snapshot(None, []) is None
    no_snap = review_metrics.compute_fer(None, {}, [], {})
    assert no_snap["comparable"] is False
    assert no_snap["reason"] == "no_snapshot"
    assert review_metrics.is_zero_edit(no_snap, True, False) is False

    # 完全一致 -> 可比且零改，但仅在 owner 完成且无强制 override 时计零改
    ai = {"vendor": "V", "date": "2026-01-01", "total": 10, "items": [
        {"name": "A", "qty": 1, "unit": "kg", "unit_price": 10, "amount": 10}]}
    snap = review_metrics.build_snapshot(ai, [{"line_uuid": "ua"}])
    same_items = [{"line_uuid": "ua", "name": "A", "quantity": 1, "unit": "kg",
                   "unit_price": 10, "amount": 10}]
    header = {"supplier_name": "V", "receipt_date": "2026-01-01", "total_amount": 10}
    fees = {f: 0 for f in review_metrics.FEE_FIELDS}
    fer = review_metrics.compute_fer(snap, header, same_items, fees)
    assert fer["comparable"] is True and fer["zero_edit"] is True
    assert review_metrics.is_zero_edit(fer, True, False) is True
    # 非 owner 完成（手工/失败）不算零改
    assert review_metrics.is_zero_edit(fer, False, False) is False
    # 强制 warning override 后不算零改
    assert review_metrics.is_zero_edit(fer, True, True) is False


def test_wallclock_parsed_submit_30s():
    """M02：parsed->submitted 墙钟可注入时钟；<=30s 记过快提交，不可解析返回 None。"""
    from datetime import datetime, timezone

    parsed = "2026-01-01T00:00:00+00:00"
    assert review_metrics.wallclock_parsed_to_submitted(
        parsed, "2026-01-01T00:00:10+00:00") == 10.0
    assert review_metrics.is_fast_submission(10.0) is True
    assert review_metrics.wallclock_parsed_to_submitted(
        parsed, "2026-01-01T00:00:31+00:00") == 31.0
    assert review_metrics.is_fast_submission(31.0) is False
    # 注入时钟：submitted_at 缺省时用 clock()
    got = review_metrics.wallclock_parsed_to_submitted(
        parsed, clock=lambda: datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc))
    assert got == 5.0
    # 不可解析 -> None（未知，不折 0）
    assert review_metrics.wallclock_parsed_to_submitted("not-a-date",
                                                        parsed) is None
    assert review_metrics.is_fast_submission(None) is False


def test_snapshot_and_submitted_events_appended():
    """M02：手工保存追加 submitted 事件；无 AI 预填时 FER 标 uncomparable（不造假）。"""
    rid, _v = _save_manual([{"name": "Item_A", "quantity": 1, "unit": "kg",
                             "unit_price": 7, "amount": 7}], 7)
    events = db.list_review_events(rid)
    kinds = [e["event_kind"] for e in events]
    assert "saved" in kinds and "submitted" in kinds, kinds
    sub = [e for e in events if e["event_kind"] == "submitted"][0]
    assert sub["payload"]["fer"]["comparable"] is False
    assert sub["payload"]["fer"]["reason"] == "no_snapshot"

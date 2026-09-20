# -*- coding: utf-8 -*-
"""R4（P14 看板维度）回归：带警告通过不得被看板计成干净成功。

why: T2 已让 supervisor 把 parsed_with_warnings 的 state["success"] 置 false 并把
该状态落库，但 Job 在 save_parsed_data 之后仍无条件发 ocr_parsed 埋点，而
api_admin.recognition_summary 直接按该事件条数计 parse_success —— 于是
「算术对不上 / 明细为空」的单据在识别看板上依旧表现为一次漂亮成功，P14 的虚假
成功信号只被堵住了落库与前端状态两个维度，看板维度仍存在。

本用例覆盖：
- Job 埋点带上管线状态（parse_status）与门禁警告（gate_warnings）；
- 看板 parse_success 与 parse_with_warnings 两个计数互斥，带警告单不进 parse_success；
- 灰测分组的 grey_split 同样按干净/带警告拆分；
- 历史事件（无 parse_status）按干净成功兼容，不追溯改写既有统计。

全程用隔离临时库，不调用任何真实 VLM，不碰 live 库。
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import db
from app.services import receipt_utils


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """沿用 test_job_writeback_none_guard 的隔离模式：DB_PATH 指向临时库，跑完还原。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_dashboard_warning_split.db")
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
def client(isolated_db):
    """依赖 isolated_db 以保证请求发到临时库；不使用 with，避免触发 startup 自愈。"""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _wait_job_settled(job_id, timeout=10.0):
    """轮询内存 job 表，等待后台线程把 job_status 落到 done/error。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = receipt_utils.get_job(job_id) or {}
        if job.get("job_status") in ("done", "error"):
            return job
        time.sleep(0.02)
    return receipt_utils.get_job(job_id) or {}


def _summary(client):
    resp = client.get("/api/analytics/recognition-summary",
                      headers={"X-Role": "admin"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _last_ocr_parsed_props(receipt_id):
    s = db.get_session()
    try:
        ev = (s.query(db._UserEventRow)
              .filter_by(event_type="ocr_parsed", receipt_id=receipt_id)
              .order_by(db._UserEventRow.id.desc()).first())
    finally:
        s.close()
    assert ev is not None, "未写到 ocr_parsed 埋点"
    return json.loads(ev.properties or "{}")


def _stub_job(monkeypatch, pipeline_result):
    """让 Job 只走回写与埋点分支：不真实落库、不真实识别。"""
    from app.chains import supervisor
    monkeypatch.setattr(receipt_utils, "save_parsed_data",
                        lambda rid, data, result: {"id": rid})
    monkeypatch.setattr(supervisor, "run_pipeline",
                        lambda *a, **kw: pipeline_result)


def test_warning_job_event_carries_parse_status_and_gate_warnings(client, monkeypatch):
    """门禁失败通过时：埋点带上 parse_status=parsed_with_warnings 与门禁警告原文。"""
    _stub_job(monkeypatch, {
        "data": {"supplier_name": "测试供应商"},
        "status": "parsed_with_warnings",
        "gate_warnings": ["算术门禁: 明细合计=100 预期总额=100，但总额=999（差-899）"],
    })

    job_id, receipt_id = receipt_utils.start_recognition_job("uploads/r4_warn.jpg")
    job = _wait_job_settled(job_id)
    assert job.get("job_status") == "done", job

    props = _last_ocr_parsed_props(receipt_id)
    assert props.get("parse_status") == "parsed_with_warnings"
    assert props.get("gate_warnings") == [
        "算术门禁: 明细合计=100 预期总额=100，但总额=999（差-899）"]


def test_warning_receipt_excluded_from_parse_success(client, monkeypatch):
    """带警告单只进 parse_with_warnings，绝不进 parse_success（本任务核心验收）。"""
    _stub_job(monkeypatch, {
        "data": {"supplier_name": "测试供应商"},
        "status": "parsed_with_warnings",
        "gate_warnings": ["契约门禁: 明细为空"],
    })

    job_id, _ = receipt_utils.start_recognition_job("uploads/r4_warn_only.jpg")
    assert _wait_job_settled(job_id).get("job_status") == "done"

    d = _summary(client)
    assert d["parse_total"] == 1
    assert d["parse_success"] == 0, "带警告单据不得计入干净成功: %s" % d
    assert d["parse_with_warnings"] == 1, d
    assert d["parse_fail"] == 0, d


def test_clean_job_still_counts_as_parse_success(client, monkeypatch):
    """反证方向：干净解析仍计 parse_success，带警告计数为 0（防把正常路径改坏）。"""
    _stub_job(monkeypatch, {
        "data": {"supplier_name": "测试供应商"},
        "status": "parsed",
        "gate_warnings": [],
    })

    job_id, _ = receipt_utils.start_recognition_job("uploads/r4_clean.jpg")
    assert _wait_job_settled(job_id).get("job_status") == "done"

    d = _summary(client)
    assert d["parse_total"] == 1
    assert d["parse_success"] == 1, d
    assert d["parse_with_warnings"] == 0, d


def test_mixed_events_split_two_buckets(client):
    """混合场景：1 干净 + 1 带警告 → 两个计数各 1，且互斥不重复计。"""
    db.log_user_event(event_type="ocr_parse_started")
    db.log_user_event(event_type="ocr_parse_started")
    db.log_user_event(event_type="ocr_parsed",
                      properties={"parse_status": "parsed", "attempts": 1})
    db.log_user_event(event_type="ocr_parsed",
                      properties={"parse_status": "parsed_with_warnings",
                                  "gate_warnings": ["明细为空"], "attempts": 2})

    d = _summary(client)
    assert d["parse_total"] == 2
    assert d["parse_success"] == 1, d
    assert d["parse_with_warnings"] == 1, d
    assert d["parse_success"] + d["parse_with_warnings"] == 2, d


def test_legacy_event_without_parse_status_counts_as_success(client):
    """兼容：历史事件无 parse_status 时按干净成功计，不追溯改写既有看板数字。"""
    db.log_user_event(event_type="ocr_parse_started")
    db.log_user_event(event_type="ocr_parsed",
                      properties={"elapsed_ms": 1000, "attempts": 1})

    d = _summary(client)
    assert d["parse_success"] == 1, d
    assert d["parse_with_warnings"] == 0, d


def test_grey_split_splits_warning_bucket(client):
    """灰测分组同样拆分：实验组里的带警告单不得混进该组 parse_success。"""
    db.log_user_event(event_type="ocr_parsed", grp="treatment",
                      properties={"parse_status": "parsed"})
    db.log_user_event(event_type="ocr_parsed", grp="treatment",
                      properties={"parse_status": "parsed_with_warnings"})
    db.log_user_event(event_type="ocr_parsed", grp="control",
                      properties={"parse_status": "parsed_with_warnings"})

    d = _summary(client)
    treat = d["grey_split"]["treatment"]
    ctrl = d["grey_split"]["control"]
    assert treat["parse_success"] == 1, treat
    assert treat["parse_with_warnings"] == 1, treat
    assert ctrl["parse_success"] == 0, ctrl
    assert ctrl["parse_with_warnings"] == 1, ctrl

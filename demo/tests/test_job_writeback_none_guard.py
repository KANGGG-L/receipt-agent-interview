# -*- coding: utf-8 -*-
"""W3 回归：job 完成回写时 done_row 为 None 的守卫（隔离库，不依赖 LLM）。

why: 识别 Job 在 save_parsed_data 与 get_receipt_row 之间，单据可能被删除/软删，
此时 done_row 为 None。回写 result 时若只守 image_url 而继续裸取
done_row.version / done_row.quality_warnings_json，会抛 AttributeError，
把本应正常的「识别完成」变成一个 Job 线程异常；而 image_url 行的 else 分支
又会误导读者以为 None 已被处理。本用例显式覆盖该分支。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import db
from app.services import receipt_utils


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """沿用 test_recovery_actions 的隔离模式：DB_PATH 指向临时库，跑完还原。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_job_writeback.db")
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


def _wait_job_settled(job_id, timeout=10.0):
    """轮询内存 job 表，等待后台线程把 job_status 落到 done/error。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = receipt_utils.get_job(job_id) or {}
        if job.get("job_status") in ("done", "error"):
            return job
        time.sleep(0.02)
    return receipt_utils.get_job(job_id) or {}


def test_result_writeback_survives_missing_row(isolated_db, monkeypatch):
    """done_row 为 None：回写不抛异常，且 result 结构完整、字段为安全默认值。"""
    from app.chains import supervisor

    detail = {"id": 1, "supplier_name": "测试供应商", "items": []}
    # 跳过真实落库与真实识别，只走回写分支；再让 get_receipt_row 永远返回 None，
    # 模拟「单据在回写前消失」。
    monkeypatch.setattr(receipt_utils, "save_parsed_data",
                        lambda rid, data, result: detail)
    monkeypatch.setattr(db, "get_receipt_row", lambda rid: None)
    monkeypatch.setattr(supervisor, "run_pipeline",
                        lambda *a, **kw: {"data": {"supplier_name": "测试供应商"}})

    job_id, receipt_id = receipt_utils.start_recognition_job("uploads/w3_missing.jpg")
    job = _wait_job_settled(job_id)

    assert job.get("job_status") == "done", job
    assert not job.get("error_msg"), job
    result = job.get("result") or {}
    assert result.get("status") == "success"
    assert result.get("receipt_id") == receipt_id
    assert result.get("data") == detail
    assert result.get("image_url") == ""
    assert result.get("version") == ""
    assert result.get("quality_warnings") == []
    # 返回结构不变：所有既有字段都在
    for key in ("status", "receipt_id", "data", "image_url", "version",
                "quality_warnings", "fallback_triggered", "fallback_reason",
                "fallback_from", "fallback_engine"):
        assert key in result, "缺少字段: %s" % key


def test_result_writeback_uses_row_when_present(isolated_db, monkeypatch):
    """对照用例：done_row 存在时仍取真实版本号与质检告警（防守卫改坏正常路径）。"""
    from app.chains import supervisor

    monkeypatch.setattr(receipt_utils, "save_parsed_data",
                        lambda rid, data, result: {"id": 1})
    monkeypatch.setattr(supervisor, "run_pipeline",
                        lambda *a, **kw: {"data": {"supplier_name": "正常供应商"}})

    detail_row = {"image_path": "uploads/w3_present.jpg", "version": 7,
                  "quality_warnings_json": '["字段缺失"]', "tenant_id": "default"}
    monkeypatch.setattr(db, "get_receipt_row", lambda rid: _Row(detail_row))
    monkeypatch.setattr(receipt_utils, "public_image_url",
                        lambda rid, path, tenant: "/api/receipt/%s/image" % rid)

    job_id, receipt_id = receipt_utils.start_recognition_job("uploads/w3_present.jpg")
    job = _wait_job_settled(job_id)

    assert job.get("job_status") == "done", job
    result = job.get("result") or {}
    assert result.get("image_url") == "/api/receipt/%s/image" % receipt_id
    assert result.get("version") == 7
    assert result.get("quality_warnings") == ["字段缺失"]


class _Row:
    """最小行替身：只暴露回写分支用到的字段。"""

    def __init__(self, values):
        self.__dict__.update(values)

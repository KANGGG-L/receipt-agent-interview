# -*- coding: utf-8 -*-
"""R1 回归：后台识别 Job 在 DB_PATH 被切换后不得污染新库（隔离库，不依赖 LLM）。

why（治本类缺陷，探针实锤）：识别 Job 跑在后台线程，写库时读的是「当时的」
db.DB_PATH。测试用例自己隔离了库，但收尾时夹具会把 db.DB_PATH 还原/切走；此时
晚到的 Job 线程继续执行，写库就落到新库上 —— 这正是历史「测试污染 live 库」的
机制（探针包装 db.get_session 抓到 46 次 get_session@LIVE，栈全部来自
receipt_utils.py 的后台线程）。

三层验证：
1. 正常路径：DB_PATH 全程不变 → 完整 Job 仍正常写库并产出结果（零影响）
2. 切换路径：Job 执行中把 db.DB_PATH 切到库 B → 库 B 零写入 + Job 可辨失败态 +
   放弃写入 warning
3. 反证：把闸门判据 monkeypatch 成「永不视为不一致」（等价于去掉校验）→ 同一
   场景下库 B 立刻被写脏（证明用例不是恒真）

全部离线：假 run_pipeline（内部照样调 db 写库，覆盖 supervisor 侧写库路径），
真实 save_parsed_data，零外部调用、零真实引擎。
"""

import logging
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.services import receipt_utils
from app.chains import supervisor


@pytest.fixture(autouse=True)
def restore_db_path():
    """用例会把 DB_PATH 在 A/B 两个临时库间切换，收尾必须还原（防止影响后续文件）。"""
    old_path = db.DB_PATH
    yield
    db.DB_PATH = old_path
    db._make_engine()


def _use_db(path):
    """切到指定库并重建引擎（materialize 出带完整 schema 的新库）。"""
    db.DB_PATH = path
    db._make_engine()


class _FakeItem:
    def __init__(self, name="测试菜心", qty=2.0, unit="斤",
                 unit_price=10.0, amount=20.0):
        self.name = name
        self.qty = qty
        self.unit = unit
        self.unit_price = unit_price
        self.amount = amount
        self.confidence = 0.9
        self.evidence = None
        self.unit_conversion_warning = ""
        self.is_void = False
        self.actual_qty = None


class _FakeData:
    """save_parsed_data 的鸭子类型替身（只提供它读取的属性）。"""

    def __init__(self):
        self.items = [_FakeItem()]
        self.vendor = "切换库测试供应商"
        self.date = "2026-09-19"
        self.total = 20.0
        self.doc_form = "invoice"
        self.confidence = 0.9
        self.payment_marked = False
        self.math_warnings = []
        self.currency = "HKD"
        self.service_fee = 0.0
        self.tax_amount = 0.0
        self.adjustment_notes = []
        self.payment_evidence = ""

    def model_dump(self):
        return {"vendor": self.vendor, "total": self.total}


def _wait_job(job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = receipt_utils.get_job(job_id)
        if job and job.get("job_status") in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"Job 未在限时内收口: {receipt_utils.get_job(job_id)}")


def _make_blocking_pipeline(started, release):
    """假管线：进入后阻塞，等测试切好库再返回；返回前先做 supervisor 侧同类写库。

    这三条写库调用与 supervisor 里真实存在的位置一一对应：
    _log_extract_decision/_log_audit_decision → db.log_ai_decision；
    _track_guard / 主链路埋点 → db.log_user_event；
    maybe_create_eval_candidate → db.create_eval_candidate。
    """

    def _fake_pipeline(image_path, vendor_hint="", config=None, supplier_name="",
                       receipt_id=None, experiment_id=None, on_event=None):
        started.set()
        assert release.wait(timeout=15), "测试未能及时切换库路径"
        db.log_ai_decision(receipt_id=receipt_id, decision_type="extract")
        db.log_user_event(account_id="system", event_type="contract_guard_checked",
                          receipt_id=receipt_id)
        db.create_eval_candidate(receipt_id=receipt_id, reason="low_confidence")
        return {"data": _FakeData(), "raw": "{}", "error": None,
                "status": "parsed", "gate_warnings": []}

    return _fake_pipeline


def _insert_written_counts(receipt_id):
    """当前库中「本 Job 若写入就会新增」的各表行数（全是 INSERT 型污染信号）。"""
    s = db.get_session()
    try:
        return {
            "user_event": s.query(db._UserEventRow)
                            .filter(db._UserEventRow.receipt_id == receipt_id).count(),
            "ai_decision_log": s.query(db._DecisionLogRow)
                                 .filter(db._DecisionLogRow.receipt_id == receipt_id).count(),
            "receipt_items": s.query(db._ItemRow)
                               .filter(db._ItemRow.receipt_id == receipt_id).count(),
            "eval_candidate": s.query(db._EvalCandidateRow)
                                .filter(db._EvalCandidateRow.receipt_id == receipt_id).count(),
        }
    finally:
        s.close()


def _run_switch_scenario(tmp_path, monkeypatch, started, release, sentinel_name):
    """启动 Job → 切库 B（放一张同 id 哨兵单据）→ 放行管线 → 等 Job 收口。

    返回 (job, receipt_id)。同 id 是关键：两库首张单据 id 都是 1，模拟 live 库中
    已存在同 id 单据（历史事故正是 Job 回写覆盖了 live 的 receipts.id=1 黄金样本）。
    """
    path_a = str(tmp_path / "job_db_a.db")
    path_b = str(tmp_path / "job_db_b.db")
    _use_db(path_a)

    monkeypatch.setattr(supervisor, "run_pipeline",
                        _make_blocking_pipeline(started, release))

    job_id, receipt_id = receipt_utils.start_recognition_job(
        str(tmp_path / "receipt.jpg"), vendor_hint="切换库测试")
    assert started.wait(timeout=15), "Job 未进入管线"

    _use_db(path_b)
    sentinel_id = db.create_receipt(supplier_name=sentinel_name)
    assert sentinel_id == receipt_id, "两库首张单据 id 应一致，才能模拟同 id 覆盖"

    release.set()
    return _wait_job(job_id), receipt_id


# ---------------------------------------------------------------
# 1. 切换路径：Job 放弃写入新库
# ---------------------------------------------------------------
def test_job_abandons_writes_after_db_path_switch(tmp_path, monkeypatch, caplog):
    started, release = threading.Event(), threading.Event()
    with caplog.at_level(logging.WARNING):
        job, receipt_id = _run_switch_scenario(
            tmp_path, monkeypatch, started, release, sentinel_name="库B哨兵单据")

    # Job 是可辨别的失败态，不是 done（前端不会误以为成功）
    assert job["job_status"] == "error", job
    assert job["error_code"] == "db_path_changed", job
    assert "库路径在识别任务执行期间被切换" in (job.get("error_msg") or "")
    # 日志里出现「放弃写入」的 warning
    assert "放弃写入以避免污染新库" in caplog.text, caplog.text

    # 库 B：零写入 —— 哨兵单据原样，且四张 INSERT 型表都没有该 Job 的行
    assert _insert_written_counts(receipt_id) == {
        "user_event": 0, "ai_decision_log": 0,
        "receipt_items": 0, "eval_candidate": 0,
    }
    sentinel = db.get_receipt_row(receipt_id)
    assert sentinel is not None
    assert sentinel.supplier_name == "库B哨兵单据"
    assert sentinel.status == "uploaded", "新库同 id 单据不得被 Job 回写覆盖"

    # 库 A：切换前已完成的部分仍在（单据已建 + 已置 parsing + 已埋点）
    _use_db(str(tmp_path / "job_db_a.db"))
    row_a = db.get_receipt_row(receipt_id)
    assert row_a is not None and row_a.status == "parsing"
    counts_a = _insert_written_counts(receipt_id)
    assert counts_a["user_event"] >= 1, "Job 启动时的 ocr_parse_started 埋点应留在库 A"


# ---------------------------------------------------------------
# 2. 正常路径：DB_PATH 不变 → 行为完全一致
# ---------------------------------------------------------------
def test_job_writes_normally_when_db_path_unchanged(tmp_path, monkeypatch, caplog):
    _use_db(str(tmp_path / "job_db_ok.db"))

    def _fake_pipeline(image_path, vendor_hint="", config=None, supplier_name="",
                       receipt_id=None, experiment_id=None, on_event=None):
        db.log_ai_decision(receipt_id=receipt_id, decision_type="extract")
        db.log_user_event(account_id="system", event_type="contract_guard_checked",
                          receipt_id=receipt_id)
        return {"data": _FakeData(), "raw": "{}", "error": None,
                "status": "parsed", "gate_warnings": []}

    monkeypatch.setattr(supervisor, "run_pipeline", _fake_pipeline)

    with caplog.at_level(logging.WARNING):
        job_id, receipt_id = receipt_utils.start_recognition_job(
            str(tmp_path / "receipt.jpg"), vendor_hint="正常路径")
        job = _wait_job(job_id)

    assert job["job_status"] == "done", job.get("error_msg")
    assert job["result"]["status"] == "success"
    row = db.get_receipt_row(receipt_id)
    assert row.status == "parsed"
    assert row.supplier_name == "切换库测试供应商"
    assert db.get_receipt_items(receipt_id), "明细应正常落库"
    assert _insert_written_counts(receipt_id)["user_event"] >= 1
    assert _insert_written_counts(receipt_id)["ai_decision_log"] >= 1
    # 未发生库切换 → 不得出现放弃写入的 warning
    assert "放弃写入以避免污染新库" not in caplog.text


# ---------------------------------------------------------------
# 3. 反证：去掉闸门 → 同一场景必然写脏库 B
# ---------------------------------------------------------------
def test_reverse_proof_without_guard_pollutes_new_db(tmp_path, monkeypatch):
    """monkeypatch 判据为「永不视为不一致」= 等同移除校验；库 B 必须被写脏。"""
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(db, "job_db_path_mismatch", lambda: None)

    job, receipt_id = _run_switch_scenario(
        tmp_path, monkeypatch, started, release, sentinel_name="库B哨兵单据")

    counts = _insert_written_counts(receipt_id)
    assert counts["user_event"] >= 1, "无闸门时 Job 埋点应写进库 B"
    assert counts["ai_decision_log"] >= 1, "无闸门时主管线决策日志应写进库 B"
    assert counts["receipt_items"] >= 1, "无闸门时明细应写进库 B"
    # 同 id 覆盖：新库哨兵单据被 Job 回写覆盖 status（这正是 live 黄金样本被覆盖的形态）
    sentinel = db.get_receipt_row(receipt_id)
    assert sentinel.status != "uploaded", "无闸门时库 B 同 id 单据应被覆盖"
    assert sentinel.supplier_name == "切换库测试供应商"

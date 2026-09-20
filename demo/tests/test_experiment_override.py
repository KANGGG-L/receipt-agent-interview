# -*- coding: utf-8 -*-
"""T4/P5 回归：A/B 实验快照覆盖管理台引擎配置，覆盖事实必须透传进 Job 结果。

为什么要有这组用例：识别链路在 running 实验下会用 grey_snapshot 覆盖
cfg.openai_rec_model / cfg.openai_rec_base_url（覆盖语义本身是对的、本次不改），
但覆盖事实此前对管理员不可见——只看到「改了引擎没生效」，无从得知是实验在起作用。
修复后 receipt_utils 把事实写进 JOBS[job_id]["experiment_override"] 与
Job result.experiment_override（{experiment_id, grp, model, base_url}），
前端（单张 + 批量路径）据此弹提示。

覆盖：
1. treatment 组：快照改写 model/base_url → Job 结果字段完整，且实际下发给管线的
   引擎配置确实被覆盖
2. control 组：同理（分组由 receipt_id % 100 与 target_percent 决定）
3. 反例：running 实验但快照没有改写 model/base_url → 不得误报覆盖

全部离线：独立临时 DB + 假 run_pipeline，零外部调用、零真实引擎。
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.services import receipt_utils
from app.chains import supervisor


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每例独立临时库，绝不落 live demo 库。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_experiment_override.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


def _start_running_experiment(snapshot, target_percent=100):
    """建一条 running 实验（target_percent=100 → 必然实验组；=0 → 必然对照组）。"""
    exp = db.create_experiment("覆盖可见性实验", hypothesis="测试假设",
                               target_percent=target_percent)
    started = db.start_experiment(exp["id"], grey_snapshot=snapshot)
    assert started and started["status"] == "running"
    return started


def _receipt_in_group(target_percent, want_control):
    """造一张必然落入指定分组的单据（bucket = receipt_id % 100）。

    分组判据（receipt_utils）：bucket < target_percent → treatment，否则 control。
    直接由用例控制 receipt_id，避免依赖自增 id 的偶然取值。
    """
    for _ in range(120):
        rid = db.create_receipt(status="uploaded")
        is_control = (rid % 100) >= target_percent
        if is_control == want_control:
            return rid
    raise AssertionError("未能造出目标分组的单据")


def _run_job_with_stub(monkeypatch, captured, receipt_id=None):
    """跑一次识别 Job：假 run_pipeline + 假 save_parsed_data，等 Job 收口后返回。"""
    def _fake_pipeline(image_path, vendor_hint="", config=None, supplier_name="",
                       receipt_id=None, experiment_id=None, on_event=None):
        captured["config"] = config
        captured["experiment_id"] = experiment_id
        captured["supplier_name"] = supplier_name
        return {"data": object(), "raw": "{}", "error": None,
                "status": "parsed", "gate_warnings": []}

    monkeypatch.setattr(supervisor, "run_pipeline", _fake_pipeline)
    monkeypatch.setattr(receipt_utils, "save_parsed_data",
                        lambda rid, data, result: {"id": rid})

    job_id, receipt_id = receipt_utils.start_recognition_job(
        "/tmp/fake_batch_receipt.jpg", vendor_hint="覆盖测试供应商",
        receipt_id=receipt_id)

    deadline = time.time() + 10
    while time.time() < deadline:
        job = receipt_utils.get_job(job_id)
        if job and job.get("job_status") in ("done", "error"):
            return job, receipt_id
        time.sleep(0.05)
    raise AssertionError(f"Job 未在限时内收口: {receipt_utils.get_job(job_id)}")


def test_treatment_override_is_recorded_in_job_result(monkeypatch):
    """实验组：覆盖事实进 Job 顶层与 result，字段完整，且配置真被改写。"""
    exp = _start_running_experiment({
        "treatment_model": "exp-treatment-model",
        "treatment_base_url": "https://exp-treatment.example.com/v1",
    }, target_percent=100)

    captured = {}
    job, receipt_id = _run_job_with_stub(monkeypatch, captured)

    assert job["job_status"] == "done", job.get("error_msg")
    expected = {
        "experiment_id": exp["id"],
        "grp": "treatment",
        "model": "exp-treatment-model",
        "base_url": "https://exp-treatment.example.com/v1",
    }
    # Job 级透传（轮询/错误分支也能看到）+ result 级（前端完成提示读这里）
    assert job["experiment_override"] == expected
    assert job["result"]["experiment_override"] == expected
    # 覆盖真的作用到了下发给管线的配置上（不是只记了个日志）
    assert captured["config"].openai_rec_model == "exp-treatment-model"
    assert captured["config"].openai_rec_base_url == "https://exp-treatment.example.com/v1"
    assert captured["experiment_id"] == exp["id"]


def test_control_override_is_recorded_in_job_result(monkeypatch):
    """对照组：分组由 receipt_id % 100 >= target_percent 决定，字段同样完整。"""
    target_percent = 50
    exp = _start_running_experiment({
        "control_model": "exp-control-model",
        "control_base_url": "https://exp-control.example.com/v1",
    }, target_percent=target_percent)

    captured = {}
    rid = _receipt_in_group(target_percent, want_control=True)
    job, _ = _run_job_with_stub(monkeypatch, captured, receipt_id=rid)

    assert job["job_status"] == "done", job.get("error_msg")
    expected = {
        "experiment_id": exp["id"],
        "grp": "control",
        "model": "exp-control-model",
        "base_url": "https://exp-control.example.com/v1",
    }
    assert job["experiment_override"] == expected
    assert job["result"]["experiment_override"] == expected
    assert captured["config"].openai_rec_model == "exp-control-model"


def test_running_experiment_without_snapshot_override_is_not_reported(monkeypatch):
    """反例：running 实验只分组、没改写 model/base_url → 不得误报「配置被覆盖」。"""
    _start_running_experiment({"note": "仅分组，无引擎覆盖"}, target_percent=100)

    captured = {}
    job, _ = _run_job_with_stub(monkeypatch, captured)

    assert job["job_status"] == "done", job.get("error_msg")
    assert job["experiment_override"] is None
    assert job["result"]["experiment_override"] is None
    # 未覆盖时下发的仍是管理台常规配置（与库中现值一致，未被快照改写）
    assert captured["config"].openai_rec_model == db.get_engine_config().openai_rec_model

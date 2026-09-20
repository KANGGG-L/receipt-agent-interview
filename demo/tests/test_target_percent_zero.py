# -*- coding: utf-8 -*-
"""P2 回归：target_percent=0 必须被尊重为 0，不能被静默当成 50。

why: 原实现用 `int(target_percent or 50)`（db.create_experiment 两处）与
`int(running_exp.get("target_percent") or 50)`（receipt_utils 分流读取处），
因 0 是 falsy，把合法配置 `target_percent=0`（「全部走对照组」）静默改成 50，
实验分流与预期不符；管理台创建接口的 target_percent 也缺范围校验。

本文件锁住修复后行为：
  1. db.coerce_int 的归一语义（0/100 保留；None/空串回落 50；非法值不炸）；
     why 泛化：原 coerce_target_percent 与本轮 S2 的 min_sample 完全同型，故抽成
     通用 helper（db.coerce_int(value, default)），不再各抄一份。
  2. db.create_experiment 落库后读回的就是 0 / 100（不是 50）；
  3. 真实 Job 分流：target_pct=0 时任意 receipt_id 都落 control，=100 时都落 treatment；
  4. 管理台 POST /api/admin/experiments 接受 0、落库 0，并拒绝越界值（422）。

全程离线：隔离临时库 + 假 run_pipeline，零网络、零真实引擎、零 live 库写入。
"""

import os
import sys
import time

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db
from app.chains import supervisor
from app.services import receipt_utils

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每例独立临时库，绝不落 live demo 库。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_target_percent_zero.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


def _admin_headers():
    return {"X-Role": "admin", "X-Email": "admin@demo.hk"}


# ---------------------------------------------------------------
# 1. 归一函数语义
# ---------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (0, 0), (0.0, 0), ("0", 0),          # 0 必须保留（本次核心）
    (100, 100), ("100", 100),            # 上边界同样保留
    (30, 30), ("30", 30),                # 常规值不受影响
    (None, 50), ("", 50), ("   ", 50),   # 仅"空"才回落默认
    ("abc", 50), ("", 50), (object(), 50),  # 非法值不炸，回落默认
])
def test_coerce_int_semantics(value, expected):
    assert db.coerce_int(value, 50) == expected


# ---------------------------------------------------------------
# 2. 落库：0 / 100 读回一致
# ---------------------------------------------------------------
def test_create_experiment_persists_zero():
    exp = db.create_experiment("全对照组实验", hypothesis="验证 0 不被改成 50",
                               target_percent=0)
    got = db.get_experiment(exp["id"])
    assert got["target_percent"] == 0, f"0 被改写成了 {got['target_percent']}"


def test_create_experiment_persists_hundred():
    exp = db.create_experiment("全实验组实验", hypothesis="验证 100 保留",
                               target_percent=100)
    assert db.get_experiment(exp["id"])["target_percent"] == 100


def test_create_experiment_none_falls_back_to_50():
    exp = db.create_experiment("默认比例实验", hypothesis="验证 None 回落 50",
                               target_percent=None)
    assert db.get_experiment(exp["id"])["target_percent"] == 50


# ---------------------------------------------------------------
# 3. 分流：0 → 全 control；100 → 全 treatment（走真实 Job 路径）
# ---------------------------------------------------------------
def _receipt_with_bucket(bucket):
    """造一张 bucket = receipt_id % 100 恰为指定值的单据。"""
    for _ in range(500):
        rid = db.create_receipt(status="uploaded")
        if rid % 100 == bucket:
            return rid
    raise AssertionError(f"未能造出 bucket={bucket} 的单据")


def _run_job(monkeypatch, receipt_id):
    """跑一次识别 Job：假 run_pipeline + 假 save_parsed_data，等 Job 收口。"""
    def _fake_pipeline(image_path, vendor_hint="", config=None, supplier_name="",
                       receipt_id=None, experiment_id=None, on_event=None):
        return {"data": object(), "raw": "{}", "error": None,
                "status": "parsed", "gate_warnings": []}

    monkeypatch.setattr(supervisor, "run_pipeline", _fake_pipeline)
    monkeypatch.setattr(receipt_utils, "save_parsed_data",
                        lambda r, data, result: {"id": r})

    job_id, _ = receipt_utils.start_recognition_job(
        "/tmp/fake_target_pct.jpg", receipt_id=receipt_id)
    deadline = time.time() + 10
    while time.time() < deadline:
        job = receipt_utils.get_job(job_id)
        if job and job.get("job_status") in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"Job 未在限时内收口: {receipt_utils.get_job(job_id)}")


def _start_running_experiment(target_percent):
    exp = db.create_experiment("分流实验", hypothesis="验证 0/100 边界",
                               target_percent=target_percent)
    started = db.start_experiment(exp["id"])
    assert started and started["status"] == "running"
    return exp["id"]


def test_target_zero_routes_every_receipt_to_control(monkeypatch):
    exp_id = _start_running_experiment(0)

    for bucket in (0, 1, 50, 99):
        rid = _receipt_with_bucket(bucket)
        job = _run_job(monkeypatch, rid)
        assert job["job_status"] == "done", job.get("error_msg")

    rows = db.get_experiment_assignments(exp_id)
    assert len(rows) == 4, rows
    assert {r["grp"] for r in rows} == {"control"}, \
        f"target_percent=0 应全部落 control，实际: {rows}"


def test_target_hundred_routes_every_receipt_to_treatment(monkeypatch):
    exp_id = _start_running_experiment(100)

    for bucket in (0, 1, 50, 99):
        rid = _receipt_with_bucket(bucket)
        job = _run_job(monkeypatch, rid)
        assert job["job_status"] == "done", job.get("error_msg")

    rows = db.get_experiment_assignments(exp_id)
    assert len(rows) == 4, rows
    assert {r["grp"] for r in rows} == {"treatment"}, \
        f"target_percent=100 应全部落 treatment，实际: {rows}"


# ---------------------------------------------------------------
# 4. 管理台接口：接受 0 并落库 0；越界值被拒（422）
# ---------------------------------------------------------------
def test_api_accepts_zero_and_persists():
    resp = client.post("/api/admin/experiments", json={
        "name": "接口零比例实验", "hypothesis": "0 必须被接受",
        "target_percent": 0,
    }, headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    exp_id = body["id"]

    got = client.get(f"/api/admin/experiments/{exp_id}", headers=_admin_headers())
    assert got.status_code == 200
    assert got.json()["experiment"]["target_percent"] == 0, \
        f"接口落库被改成 {got.json()['experiment']['target_percent']}"


@pytest.mark.parametrize("bad", [101, 150, -1, -50])
def test_api_rejects_out_of_range_target_percent(bad):
    resp = client.post("/api/admin/experiments", json={
        "name": f"越界实验 {bad}", "target_percent": bad,
    }, headers=_admin_headers())
    assert resp.status_code == 422, f"{bad} 应被拒，实际 {resp.status_code}: {resp.text}"


def test_api_accepts_boundaries_zero_and_hundred():
    for pct in (0, 100):
        resp = client.post("/api/admin/experiments", json={
            "name": f"边界实验 {pct}", "target_percent": pct,
        }, headers=_admin_headers())
        assert resp.status_code == 200, resp.text
        exp_id = resp.json()["id"]
        got = client.get(f"/api/admin/experiments/{exp_id}", headers=_admin_headers())
        assert got.json()["experiment"]["target_percent"] == pct

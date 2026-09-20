# -*- coding: utf-8 -*-
"""R2 回归：experiment.min_sample 为 NULL 时 conclude 不得 500（隔离库，零外部调用）。

why：api_phase2.conclude_experiment 里 `if n < exp.min_sample` 在 min_sample 为 NULL
（raw SQL 直插 / 历史脏数据）时抛 TypeError → 500。同仓其它读取点（guardian 守护门
`db.coerce_int(exp.get("min_sample"), 100)`、db.create_experiment 落库
`coerce_int(min_sample, 100)`）都统一为：NULL 回落设计默认 100，显式 0 保留为
「不做样本量门槛」。修复即改用同一 helper，不自创语义。

覆盖：
1. NULL + treatment 样本不足 → 400（而非 500），文案口径与默认 100 一致
2. NULL + 样本达标 → 200 成功（回落 100 的边界正确）
3. 显式 0 → 不做门槛，样本为 0 也能结题（守住「0 不被吞成 100」）
"""

import os
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db

# 不进入 lifespan：避免触发 startup 自愈副作用（对齐 test_dashboard_warning_split）
client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_conclude_min_sample.db")
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


def _admin_headers():
    return {"X-Role": "admin", "X-Email": "admin@demo.hk"}


def _make_experiment(treatment_n=1, min_sample=100):
    exp = db.create_experiment("min_sample NULL 回归", hypothesis="测试假设",
                              min_sample=min_sample)
    for _ in range(treatment_n):
        rid = db.create_receipt(status="parsed")
        db.log_ai_decision(receipt_id=rid, experiment_id=exp["id"],
                           grp="treatment", decision_type="extract")
    return exp["id"]


def _force_null_min_sample(exp_id):
    """把 min_sample 置为 NULL（模拟 raw SQL 直插造成的脏数据）。"""
    s = db.get_session()
    try:
        row = s.get(db._ExperimentRow, exp_id)
        row.min_sample = None
        s.commit()
    finally:
        s.close()


def _conclude(exp_id):
    return client.post(
        f"/api/admin/experiments/{exp_id}/conclude",
        json={"conclusion": "promote", "conclusion_reason": "回归验证",
              "concluded_by": "admin@demo.hk"},
        headers=_admin_headers())


def test_null_min_sample_does_not_500_and_falls_back_to_100():
    """NULL → 按设计默认 100 判门槛：样本不足返回 400，绝不 500。"""
    exp_id = _make_experiment(treatment_n=1)
    _force_null_min_sample(exp_id)

    resp = _conclude(exp_id)

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["status"] == "error"
    # 口径与 guardian / create_experiment 一致：NULL 视为默认 100
    assert "min_sample=100" in body["msg"], body["msg"]


def test_null_min_sample_passes_when_sample_size_reaches_default():
    """NULL → 回落 100：样本达 100 时正常结题（边界与守护门一致）。"""
    exp_id = _make_experiment(treatment_n=100)
    _force_null_min_sample(exp_id)

    resp = _conclude(exp_id)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["sample_size_treatment"] == 100


def test_explicit_zero_min_sample_means_no_threshold():
    """显式 0 是合法边界（不做门槛）：样本为 0 也能结题，不得被吞成 100。"""
    exp_id = _make_experiment(treatment_n=0, min_sample=0)

    resp = _conclude(exp_id)

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"

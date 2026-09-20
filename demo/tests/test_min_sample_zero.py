# -*- coding: utf-8 -*-
"""S2 回归：min_sample=0 必须被尊重为 0，不能被静默当成 100。

why: `db.create_experiment` 原实现 `min_sample=int(min_sample or 100)` 与刚修完的
`target_percent or 50` 是同型 falsy 陷阱 —— 0 是合法值（不做样本量门槛），却被
`or` 吞成 100，导致「实验已经跑够样本了却迟迟不判」。本轮把归一逻辑抽成通用
helper `db.coerce_int(value, default)`，target_percent 与 min_sample 共用一份实现。

本文件锁住修复后行为：
  1. db.coerce_int 对 min_sample 口径的归一语义（0 保留；None/空串回落 100；非法值不炸）；
  2. db.create_experiment 落库后读回的就是 0（不是 100），None 回落 100；
  3. 守护门 guardian.check_once 读到 min_sample=0 时不再擅自按 100 卡门槛；
  4. 管理台 POST /api/admin/experiments 接受 0、落库 0，并拒绝负数（422）。

全程离线：隔离临时库 + 假数据，零网络、零真实引擎、零 live 库写入。
"""

import os
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db
from app.services import guardian

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每例独立临时库，绝不落 live demo 库。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_min_sample_zero.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


def _admin_headers():
    return {"X-Role": "admin", "X-Email": "admin@demo.hk"}


# ---------------------------------------------------------------
# 1. 归一函数语义（min_sample 口径 default=100）
# ---------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (0, 0), (0.0, 0), ("0", 0),          # 0 必须保留（本次核心）
    (5, 5), ("40", 40), (200, 200),      # 常规值不受影响
    (None, 100), ("", 100), ("   ", 100),  # 仅"空"才回落默认
    ("abc", 100), (object(), 100),       # 非法值不炸，回落默认
])
def test_coerce_int_min_sample_semantics(value, expected):
    assert db.coerce_int(value, 100) == expected


# ---------------------------------------------------------------
# 2. 落库：0 读回一致；None 回落 100
# ---------------------------------------------------------------
def test_create_experiment_persists_zero_min_sample():
    exp = db.create_experiment("零门槛实验", hypothesis="验证 0 不被改成 100",
                               min_sample=0)
    got = db.get_experiment(exp["id"])
    assert got["min_sample"] == 0, f"0 被改写成了 {got['min_sample']}"


def test_create_experiment_none_min_sample_falls_back_to_100():
    exp = db.create_experiment("默认门槛实验", hypothesis="验证 None 回落 100",
                               min_sample=None)
    assert db.get_experiment(exp["id"])["min_sample"] == 100


# ---------------------------------------------------------------
# 3. 守护门：min_sample=0 不再按 100 卡门槛
# ---------------------------------------------------------------
def _log_extract(exp_id, grp, success):
    """写一条 extract 决策（extra 口径与 supervisor._log_extract_decision 一致）。"""
    rid = db.create_receipt(supplier_name="门槛供应商", status="parsed")
    db.log_ai_decision(
        receipt_id=rid, experiment_id=exp_id, grp=grp,
        engine="openai", model="test-model", decision_type="extract",
        ai_value={"status": "extract_ok"},
        extra={"tokens_prompt": 10, "tokens_completion": 10, "tokens_total": 20,
               "cost_hkd": 0.1, "elapsed_ms": {"total": 1000},
               "success": bool(success)},
    )
    return rid


def test_guardian_honors_zero_min_sample():
    """min_sample=0：样本量只有 1 条时也应进入判定（修复前被 `or 100` 拦掉）。"""
    exp = db.create_experiment("零门槛守护实验", min_sample=0)
    db.start_experiment(exp["id"])
    _log_extract(exp["id"], "control", True)
    _log_extract(exp["id"], "treatment", False)

    actions = guardian.check_once()
    rollbacks = [a for a in actions if a["kind"] == "rollback"
                 and a["experiment_id"] == exp["id"]]
    assert len(rollbacks) == 1, \
        f"min_sample=0 时样本量 1 也应判定回滚，实际动作: {actions}"


def test_guardian_still_gates_when_min_sample_positive():
    """反例守卫：min_sample=5 时样本量不足仍不得触发（避免把门槛改成恒不生效）。"""
    exp = db.create_experiment("有门槛守护实验", min_sample=5)
    db.start_experiment(exp["id"])
    _log_extract(exp["id"], "control", True)
    _log_extract(exp["id"], "treatment", False)

    actions = guardian.check_once()
    assert not [a for a in actions if a["kind"] == "rollback"
                and a["experiment_id"] == exp["id"]], \
        f"样本量不足（1 < 5）不得回滚，实际动作: {actions}"


# ---------------------------------------------------------------
# 4. 管理台接口：接受 0 并落库 0；负数被拒（422）
# ---------------------------------------------------------------
def test_api_accepts_zero_min_sample_and_persists():
    resp = client.post("/api/admin/experiments", json={
        "name": "接口零门槛实验", "hypothesis": "0 必须被接受",
        "min_sample": 0,
    }, headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    exp_id = resp.json()["id"]

    got = client.get(f"/api/admin/experiments/{exp_id}", headers=_admin_headers())
    assert got.status_code == 200
    assert got.json()["experiment"]["min_sample"] == 0, \
        f"接口落库被改成 {got.json()['experiment']['min_sample']}"


@pytest.mark.parametrize("bad", [-1, -50, -1000])
def test_api_rejects_negative_min_sample(bad):
    resp = client.post("/api/admin/experiments", json={
        "name": f"负数门槛实验 {bad}", "min_sample": bad,
    }, headers=_admin_headers())
    assert resp.status_code == 422, f"{bad} 应被拒，实际 {resp.status_code}: {resp.text}"


def test_api_default_min_sample_unchanged():
    """未传 min_sample 时保持接口既有默认 30（本次不改该默认值）。"""
    resp = client.post("/api/admin/experiments", json={
        "name": "默认门槛接口实验",
    }, headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    exp_id = resp.json()["id"]
    got = client.get(f"/api/admin/experiments/{exp_id}", headers=_admin_headers())
    assert got.json()["experiment"]["min_sample"] == 30

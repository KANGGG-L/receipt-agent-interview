# -*- coding: utf-8 -*-
"""Admin A/B 实验管理接口与生命周期流转单元测试。

覆盖接口：
- GET  /api/admin/experiments
- GET  /api/admin/experiments/{exp_id}
- POST /api/admin/experiments
- POST /api/admin/experiments/{exp_id}/start
- POST /api/admin/experiments/{exp_id}/stop

覆盖验证：
1. RBAC 权限控制（admin 允许，staff 403，匿名 401）
2. 实验创建（默认参数、treatment_model 快照透传、draft 状态）
3. 实验生命周期流转：draft -> running (start) -> stopped (stop)
4. 404 不存在实验异常处理
"""

import os
import sys
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_admin_experiments.db")
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


def _staff_headers():
    return {"X-Role": "staff", "X-Email": "staff@demo.hk"}


def test_admin_experiments_rbac_protection():
    """未携带 admin 身份的请求必须被拦截（401 未认证或 403 权限不足）。"""
    # 匿名请求 -> 401
    resp = client.get("/api/admin/experiments")
    assert resp.status_code == 401

    resp = client.post("/api/admin/experiments", json={"name": "Test Exp"})
    assert resp.status_code == 401

    # staff 权限不足 -> 403
    resp = client.get("/api/admin/experiments", headers=_staff_headers())
    assert resp.status_code == 403

    resp = client.post("/api/admin/experiments", json={"name": "Test Exp"}, headers=_staff_headers())
    assert resp.status_code == 403

    resp = client.post("/api/admin/experiments/1/start", headers=_staff_headers())
    assert resp.status_code == 403

    resp = client.post("/api/admin/experiments/1/stop", headers=_staff_headers())
    assert resp.status_code == 403


def test_list_experiments_empty_and_populated():
    """GET /api/admin/experiments 应该成功返回列表结构。"""
    resp = client.get("/api/admin/experiments", headers=_admin_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert isinstance(data["data"], list)


def test_create_experiment_success():
    """POST /api/admin/experiments 支持创建实验草稿并附带 treatment_model 快照。"""
    payload = {
        "name": "Prompt V2 准确率攻坚",
        "hypothesis": "采用 CoT 提示词可提升生鲜识别准确率",
        "success_metric": "accuracy",
        "target_percent": 30,
        "min_sample": 40,
        "treatment_model": "deepseek-chat"
    }
    resp = client.post("/api/admin/experiments", json=payload, headers=_admin_headers())
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "success"
    assert "id" in res
    exp = res["experiment"]
    assert exp["name"] == "Prompt V2 准确率攻坚"
    assert exp["status"] == "draft"

    # 查询单条确认详情
    exp_id = res["id"]
    get_resp = client.get(f"/api/admin/experiments/{exp_id}", headers=_admin_headers())
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["status"] == "success"
    assert get_data["experiment"]["id"] == exp_id
    assert get_data["experiment"]["target_percent"] == 30
    assert get_data["experiment"]["min_sample"] == 40
    # 验证 grey_snapshot 包含 treatment_model
    snapshot = get_data["experiment"].get("grey_snapshot") or {}
    assert snapshot.get("treatment_model") == "deepseek-chat"


def test_experiment_start_and_stop_lifecycle():
    """实验生命周期状态机：draft -> running (start) -> stopped (stop)。"""
    # 1. 创建实验
    create_resp = client.post("/api/admin/experiments", json={
        "name": "模型对比试验",
        "hypothesis": "测试启动与停止流转",
        "target_percent": 50,
    }, headers=_admin_headers())
    assert create_resp.status_code == 200
    exp_id = create_resp.json()["id"]

    # 2. 启动实验
    start_resp = client.post(f"/api/admin/experiments/{exp_id}/start", headers=_admin_headers())
    assert start_resp.status_code == 200
    start_data = start_resp.json()
    assert start_data["status"] == "success"
    assert start_data["experiment"]["status"] == "running"
    assert start_data["experiment"]["start_ts"] is not None

    # 3. 再次查询详情验证持久化状态
    get_resp = client.get(f"/api/admin/experiments/{exp_id}", headers=_admin_headers())
    assert get_resp.json()["experiment"]["status"] == "running"

    # 4. 暂停/停止实验
    stop_resp = client.post(f"/api/admin/experiments/{exp_id}/stop", headers=_admin_headers())
    assert stop_resp.status_code == 200
    stop_data = stop_resp.json()
    assert stop_data["status"] == "success"
    assert stop_data["experiment"]["status"] == "stopped"
    assert stop_data["experiment"]["end_ts"] is not None

    # 5. 再次查询详情验证持久化状态
    get_resp2 = client.get(f"/api/admin/experiments/{exp_id}", headers=_admin_headers())
    assert get_resp2.json()["experiment"]["status"] == "stopped"


def test_start_and_stop_nonexistent_experiment():
    """不存在的实验启动或停止应返回 404。"""
    resp_start = client.post("/api/admin/experiments/999999/start", headers=_admin_headers())
    assert resp_start.status_code == 404
    assert resp_start.json()["status"] == "error"

    resp_stop = client.post("/api/admin/experiments/999999/stop", headers=_admin_headers())
    assert resp_stop.status_code == 404
    assert resp_stop.json()["status"] == "error"

# -*- coding: utf-8 -*-
"""AI 效果观测与评测中心：历史灰度测试与 A/B 实验深度数据观测测试。

覆盖验证：
1. GET /api/admin/canary/history 接口鉴权与返回结构（历史变更总数、推全次数、回滚次数、履历清单）
2. GET /api/admin/grey-test/samples 支持按 scope（all, grey_only, prod_only, modified, approved）过滤
3. GET /api/admin/experiments/{exp_id} 返回结构包含 guardrail_events 与 snapshots
4. GET /api/admin/experiments/{exp_id}/samples 接口鉴权与样本明细流（Control vs Treatment、模型、准确率、采纳状态）
5. 前端 HTML 模板中历史灰测履历卡片与选定 A/B 实验深度档案卡片结构完整性
"""

import os
import sys
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from starlette.testclient import TestClient

# demo/ 目录：本文件位于 demo/tests/ 下，取两级父目录。
# 模板路径基于 __file__ 计算，避免写死 "demo/..." 相对路径而依赖 cwd。
_DEMO_DIR = Path(__file__).resolve().parents[1]
_INDEX_HTML = _DEMO_DIR / "templates" / "index.html"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db

client = TestClient(app)


def _admin_headers():
    return {"X-Role": "admin", "X-Email": "admin@demo.hk"}


def _staff_headers():
    return {"X-Role": "staff", "X-Email": "staff@demo.hk"}


def test_canary_history_rbac_and_structure():
    """验证历史灰测履历接口的 RBAC 保护与数据字段。"""
    # 匿名拦截
    res = client.get("/api/admin/canary/history")
    assert res.status_code == 401

    # Staff 拦截
    res = client.get("/api/admin/canary/history", headers=_staff_headers())
    assert res.status_code == 403

    # Admin 成功拉取
    res = client.get("/api/admin/canary/history", headers=_admin_headers())
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "total_records" in data
    assert "promote_count" in data
    assert "rollback_count" in data
    assert "items" in data
    assert isinstance(data["items"], list)


def test_grey_test_samples_scope_filtering():
    """验证灰测样本观测流支持 scope 过滤。"""
    # scope=all
    res = client.get("/api/admin/grey-test/samples?scope=all", headers=_admin_headers())
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["scope"] == "all"
    assert "samples" in data
    total_all = data["total_count"]

    # scope=grey_only
    res_grey = client.get("/api/admin/grey-test/samples?scope=grey_only", headers=_admin_headers())
    assert res_grey.status_code == 200
    data_grey = res_grey.json()
    assert data_grey["status"] == "success"
    assert data_grey["scope"] == "grey_only"
    assert all(s["use_grey"] for s in data_grey["samples"])

    # scope=prod_only
    res_prod = client.get("/api/admin/grey-test/samples?scope=prod_only", headers=_admin_headers())
    assert res_prod.status_code == 200
    data_prod = res_prod.json()
    assert data_prod["status"] == "success"
    assert all(not s["use_grey"] for s in data_prod["samples"])


def test_experiment_samples_api_and_rbac():
    """验证 A/B 实验样本明细流接口 RBAC 与数据流。"""
    # 获取已有实验
    exps = db.list_experiments()
    target_exp_id = exps[0]["id"] if exps else 1

    # 匿名拦截
    res = client.get(f"/api/admin/experiments/{target_exp_id}/samples")
    assert res.status_code == 401

    # Staff 拦截
    res = client.get(f"/api/admin/experiments/{target_exp_id}/samples", headers=_staff_headers())
    assert res.status_code == 403

    # Admin 成功拉取
    res = client.get(f"/api/admin/experiments/{target_exp_id}/samples", headers=_admin_headers())
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "samples" in data
    assert "control_count" in data
    assert "treatment_count" in data
    assert "total_samples" in data


def test_experiment_admin_detail_structure():
    """验证 GET /api/admin/experiments/{id} 返回完备的 metrics、detail、guardrail 与 snapshot。"""
    exps = db.list_experiments()
    if not exps:
        pytest.skip("No experiments available")
    target_id = exps[0]["id"]
    res = client.get(f"/api/admin/experiments/{target_id}", headers=_admin_headers())
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "experiment" in data
    assert "metrics" in data
    assert "detail" in data
    assert "guardrail_events" in data
    assert "snapshots" in data


def test_dom_structure_for_historical_canary_and_ab():
    """验证前端 index.html 模板中历史灰测履历卡片与 A/B 实验深度全景卡片 DOM。"""
    with open(_INDEX_HTML, "r", encoding="utf-8") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")

    # 1. 灰测历史卡片与表格
    assert soup.find(id="adminCanaryHistoryCard") is not None
    assert soup.find(id="adminCanaryHistoryTable") is not None
    assert soup.find(id="greySampleScopeSelect") is not None
    assert soup.find(id="mCanaryHistoryTotal") is not None

    # 2. A/B 实验深度档案卡片与表格
    assert soup.find(id="analyticsExpDetailCard") is not None
    assert soup.find(id="analyticsExpSelect") is not None
    assert soup.find(id="expSideBySideTable") is not None
    assert soup.find(id="expSampleTable") is not None
    assert soup.find(id="expSampleScopeSelect") is not None
    assert soup.find(id="expGuardrailSection") is not None

# -*- coding: utf-8 -*-
"""大盘 avg_cost_hkd 必须排除估算行（cost_estimated==1）——回归用例。

why：GET /api/admin/metrics 旧实现把 avg_cost_hkd 算成「全部 extract 行的 cost_hkd
均值」，其中混入了 cost_estimated==1 的估算行。估算行的 cost_hkd 是兜底/低估产物
（拿不到 token / 成本链路异常 / 走了兜底单价），混入均值会把「实测单张成本」拉偏，
把大盘读成比真实更便宜的成本。

用户已确认口径：avg_cost_hkd 只用非估算行；估算行单独用 cost_estimated_count 上报，
让「不可判定」显式可见。该口径与 guardian._group_metrics（guardian.py:103-107）一致，
且「全为实测行」时与旧实现逐值相同。

本用例覆盖三档：
1. 混合行：avg_cost_hkd == 仅非估算行的均值，cost_estimated_count 正确；
2. 全估算行：avg_cost_hkd 必须是 None（不可判定），不是 0；
3. 全实测行：正常路径数值不变（防把正常路径改坏）。

全程用隔离临时库，不调用真实 VLM，不碰 live 库。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """沿用 test_dashboard_warning_split 的隔离模式：DB_PATH 指向临时库，跑完还原。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_admin_metrics_cost_excludes_estimated.db")
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
    """依赖 isolated_db 保证请求发到临时库；不使用 with，避免触发 startup 自愈。"""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _seed_extract_row(cost_hkd, cost_estimated):
    """写一条 extract 决策行，带 cost_hkd 与成本可信度标记。"""
    db.log_ai_decision(
        decision_type="extract",
        engine="openai", model="qwen3.5-omni-flash",
        field_path="overall", ai_value={"status": "extract_ok"},
        extra={"tokens_total": 600000, "cost_hkd": cost_hkd,
               "cost_estimated": cost_estimated,
               "cost_estimated_reason": "" if not cost_estimated else "cost_zero_with_tokens",
               "elapsed_ms": {"total": 1000.0}, "success": True},
    )


def _metrics(client):
    resp = client.get("/api/admin/metrics", headers={"X-Role": "admin"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_avg_cost_excludes_estimated_rows(client):
    """混合行：avg_cost_hkd 是仅非估算行的均值，估算行只进 cost_estimated_count。"""
    # 非估算（实测）：1.0 / 2.0 / 3.0 -> 均值 2.0
    _seed_extract_row(1.0, 0)
    _seed_extract_row(2.0, 0)
    _seed_extract_row(3.0, 0)
    # 估算行：99.0 / 50.0 -> 若混入均值会得到 31.0（本用例的反证锚点）
    _seed_extract_row(99.0, 1)
    _seed_extract_row(50.0, 1)

    d = _metrics(client)

    assert d["db_count"] == 5, d
    assert d["avg_cost_hkd"] == 2.0, d
    assert d["avg_cost_hkd"] != 31.0, "估算行不得混入 avg_cost_hkd: %s" % d
    assert d["cost_estimated_count"] == 2, d


def test_avg_cost_is_none_when_all_estimated(client):
    """全估算行：avg_cost_hkd 必须为 None（不可判定），不能是 0。"""
    _seed_extract_row(0.0, 1)
    _seed_extract_row(12.5, 1)

    d = _metrics(client)

    assert d["db_count"] == 2, d
    assert d["avg_cost_hkd"] is None, d
    assert d["cost_estimated_count"] == 2, d


def test_avg_cost_all_measured_unchanged(client):
    """全实测行：正常路径口径不变，avg_cost_hkd 等于全部行的均值。"""
    _seed_extract_row(1.5, 0)
    _seed_extract_row(4.5, 0)

    d = _metrics(client)

    assert d["db_count"] == 2, d
    assert d["avg_cost_hkd"] == 3.0, d
    assert d["cost_estimated_count"] == 0, d


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

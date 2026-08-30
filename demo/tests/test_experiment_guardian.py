# -*- coding: utf-8 -*-
"""T9（Gap C3 + D4）：实验守护——自动回滚 + 方向性约束指标。

覆盖：
1. 成功率下降（假数据）→ 产生 rollback 动作且实验被冻结（stopped + conclusion=rollback）
2. 样本量不足 min_sample 时不触发任何动作
3. 方向性指标（avg_output_tokens 等）连续两期同向漂移 → alert 动作但不回滚
4. 回滚后 rollback_snapshot 可完整还原引擎配置（与既有推全回滚同一逻辑）
5. 手动触发端点 POST /api/admin/guardian/check（admin 专属）
6. guardrail_event 表留痕（动作 + 指标快照）

全部离线假数据：独立临时 DB，零外部调用、零真实引擎。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="experiment_guardian_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_guardian.db")
os.environ["AUTH_ENABLED"] = "0"
# 守护线程不在测试里起（main 未加载则无副作用；加载时以长间隔避免干扰）
os.environ.setdefault("GUARDIAN_INTERVAL_MINUTES", "1440")

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import pytest  # noqa: E402

import app.db as db  # noqa: E402
from app.services import guardian  # noqa: E402


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"gd_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    importlib.reload(guardian)
    return db


def _mk_running_experiment(_db, name="守护实验", min_sample=5):
    exp = _db.create_experiment(name, hypothesis="测试假设", min_sample=min_sample)
    started = _db.start_experiment(exp["id"], grey_snapshot={"note": "test"})
    return started


def _log_extract(_db, exp_id, grp, success, cost=0.10, elapsed_total=9000,
                 tokens_completion=500, receipt_id=None):
    """写一条 extract 决策（extra 口径与 supervisor._log_extract_decision 一致）。"""
    rid = receipt_id or _db.create_receipt(supplier_name="守护供应商", status="parsed")
    _db.log_ai_decision(
        receipt_id=rid, experiment_id=exp_id, grp=grp,
        engine="openai", model="test-model", decision_type="extract",
        ai_value={"status": "extract_ok"},
        extra={
            "tokens_prompt": 1000,
            "tokens_completion": tokens_completion,
            "tokens_total": 1000 + tokens_completion,
            "cost_hkd": cost,
            "elapsed_ms": {"total": elapsed_total},
            "success": bool(success),
        },
    )
    return rid


def _metric_rows(_db, exp_id):
    return [e for e in _db.list_guardrail_events(exp_id)]


# -------------------------------------------------------------
# 1. 成功率下降 → rollback 且实验冻结
# -------------------------------------------------------------
def test_success_drop_triggers_rollback_and_freeze():
    _db = _fresh_db()
    exp = _mk_running_experiment(_db, min_sample=5)

    # control 全成功（10 条），treatment 成功率 40%（4/10）→ 下降 60pp > 5pp
    for _ in range(10):
        _log_extract(_db, exp["id"], "control", success=True)
    for i in range(10):
        _log_extract(_db, exp["id"], "treatment", success=(i < 4))

    actions = guardian.check_once()

    rollbacks = [a for a in actions if a["kind"] == "rollback"
                 and a["experiment_id"] == exp["id"]]
    assert len(rollbacks) == 1, f"应产生且仅产生一个回滚动作: {actions}"
    assert "success" in rollbacks[0]["reason"]

    after = _db.get_experiment(exp["id"])
    assert after["status"] == "stopped", "回滚后实验必须被冻结"
    assert after["conclusion"] == "rollback"
    assert after["conclusion_reason"]

    events = _metric_rows(_db, exp["id"])
    assert any(e["action"] == "rollback" for e in events), "guardrail_event 必须留痕"


# -------------------------------------------------------------
# 2. 样本量不足 → 不触发
# -------------------------------------------------------------
def test_min_sample_not_enough_no_trigger():
    _db = _fresh_db()
    exp = _mk_running_experiment(_db, min_sample=5)

    for _ in range(5):
        _log_extract(_db, exp["id"], "control", success=True)
    # treatment 只有 2 条（< min_sample=5），即使全失败也不触发
    _log_extract(_db, exp["id"], "treatment", success=False)
    _log_extract(_db, exp["id"], "treatment", success=False)

    actions = guardian.check_once()
    assert all(a["experiment_id"] != exp["id"] or a["kind"] not in ("rollback",)
               for a in actions), "样本量不足不得触发回滚"
    after = _db.get_experiment(exp["id"])
    assert after["status"] == "running", "样本量不足实验保持运行"


# -------------------------------------------------------------
# 3. 方向性漂移 → alert 不回滚
# -------------------------------------------------------------
def test_directional_drift_alert_no_rollback():
    _db = _fresh_db()
    exp = _mk_running_experiment(_db, min_sample=3)

    # 两组成功率持平（不触发守护回滚）
    for _ in range(6):
        _log_extract(_db, exp["id"], "control", success=True, tokens_completion=100)
        _log_extract(_db, exp["id"], "treatment", success=True, tokens_completion=200)

    # 预置前两期快照：avg_output_tokens 100 → 130（同向上升 >20%）
    _db.write_experiment_directional_snapshot(exp["id"], avg_output_tokens=100.0)
    _db.write_experiment_directional_snapshot(exp["id"], avg_output_tokens=130.0)

    actions = guardian.check_once()
    # check_once 会写第 3 期快照（本批 treatment 均值=200，较 130 再升 >20%）→ 连续两期同向漂移
    alerts = [a for a in actions if a["kind"] == "alert"
              and a["experiment_id"] == exp["id"]]
    assert len(alerts) >= 1, f"方向性漂移应产生 alert: {actions}"

    after = _db.get_experiment(exp["id"])
    assert after["status"] == "running", "方向性漂移只告警，不得冻结/回滚实验"
    rollbacks = [a for a in actions if a["kind"] == "rollback"]
    assert rollbacks == [], "alert 场景不得产生回滚"

    events = _metric_rows(_db, exp["id"])
    assert any(e["action"] == "alert" for e in events)


# -------------------------------------------------------------
# 4. 回滚后 rollback_snapshot 完整还原配置
# -------------------------------------------------------------
def test_rollback_restores_config_from_snapshot():
    _db = _fresh_db()
    exp = _mk_running_experiment(_db, min_sample=5)

    # 模拟「推全」：快照保存推全前配置（模型 A），当前配置切成模型 B
    from app.models import EngineConfig
    base = EngineConfig(recognition_model="provider/model-A")
    prev = {k: v for k, v in base.model_dump().items()}
    promoted = base.model_copy(update={
        "recognition_model": "provider/model-B",
        "rollback_snapshot": {"prev": prev, "meta": {"by": "test"}},
    })
    _db.set_engine_config(promoted)
    assert _db.get_engine_config().recognition_model == "provider/model-B"

    # 制造成功率下降触发回滚
    for _ in range(10):
        _log_extract(_db, exp["id"], "control", success=True)
    for i in range(10):
        _log_extract(_db, exp["id"], "treatment", success=(i < 2))

    actions = guardian.check_once()
    assert any(a["kind"] == "rollback" for a in actions)

    cfg = _db.get_engine_config()
    assert cfg.recognition_model == "provider/model-A", "回滚必须完整还原快照配置"
    assert cfg.rollback_snapshot is None, "回滚后快照应清空"


# -------------------------------------------------------------
# 5. 手动触发端点 + 权限
# -------------------------------------------------------------
def test_manual_check_endpoint_admin_only():
    _db = _fresh_db()
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)

    resp = client.post("/api/admin/guardian/check", headers={"X-Role": "staff"})
    assert resp.status_code == 403

    resp = client.post("/api/admin/guardian/check", headers={"X-Role": "admin"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert isinstance(body["data"]["actions"], list)


# -------------------------------------------------------------
# 6. 无运行中实验 → 空动作（守护不报错）
# -------------------------------------------------------------
def test_no_running_experiments_returns_empty():
    _db = _fresh_db()
    actions = guardian.check_once()
    assert actions == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

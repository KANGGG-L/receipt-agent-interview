# -*- coding: utf-8 -*-
"""S3 回归：全仓 falsy 陷阱扫描中「确实会把合法 0/False 吞掉」的几处修复守卫。

本文件锁住三类同型缺陷（都用 `x or N` 把合法 0 换成 N）：
  1. guardian 方向性漂移阈值 `guard_directional_drift_pct=0`（任何漂移都告警）
     原写法 `float(th or 20)` 会静默按 20% 处理；
  2. 供应商账期 `payment_terms_days=0`（现结）在应付账款表里
     原写法 `s.payment_terms_days or 30` 会显示成 30 天；
  3. 灰测/AI 指标聚合 `min_sample=0`（不做样本量门槛）
     原写法 `int(min_sample or 30)` 会按 30 判 low_confidence。

每类都带「反例守卫」：默认值/非零值路径行为不变，证明修复不是把判据改成恒真/恒假。

全程离线：隔离临时库 + 假数据，零网络、零真实引擎、零 live 库写入。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.api_export import ExportDataProcessor
from app.services import guardian
from app.services import settings_service


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """每例独立临时库，绝不落 live demo 库。"""
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_falsy_zero.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


# ---------------------------------------------------------------
# 公共夹具：造一个跑着的实验 + 两期快照（漂移幅度可控）
# ---------------------------------------------------------------
def _log_extract(exp_id, grp, success, tokens_completion):
    rid = db.create_receipt(supplier_name="漂移供应商", status="parsed")
    db.log_ai_decision(
        receipt_id=rid, experiment_id=exp_id, grp=grp,
        engine="openai", model="test-model", decision_type="extract",
        ai_value={"status": "extract_ok"},
        extra={"tokens_prompt": 10, "tokens_completion": tokens_completion,
               "tokens_total": 10 + tokens_completion, "cost_hkd": 0.1,
               "elapsed_ms": {"total": 1000}, "success": bool(success)},
    )


def _prepare_drift_experiment():
    """治疗组 tokens 100 -> 101 -> 102：两期同向漂移各约 1%（远低于默认 20%）。"""
    exp = db.create_experiment("漂移阈值实验", min_sample=0)
    db.start_experiment(exp["id"])
    _log_extract(exp["id"], "control", True, 100)
    _log_extract(exp["id"], "treatment", True, 102)   # 当期均值 = 102

    # 预置前两期快照（check_once 会写第三期）
    db.write_experiment_directional_snapshot(exp["id"], avg_output_tokens=100.0)
    db.write_experiment_directional_snapshot(exp["id"], avg_output_tokens=101.0)
    return exp["id"]


def _drift_alerts(actions, exp_id):
    return [a for a in actions if a["kind"] == "alert" and a["experiment_id"] == exp_id]


# ---------------------------------------------------------------
# 1. guardian 方向性漂移阈值：0 必须生效（任何漂移都告警）
# ---------------------------------------------------------------
def test_drift_threshold_zero_alerts_on_tiny_drift():
    settings_service.set_value("guard_directional_drift_pct", 0)
    assert guardian._thresholds()["drift_pct"] == 0, \
        "前置断言：阈值应能从 settings 读到 0（settings_service 侧不吞 0）"

    exp_id = _prepare_drift_experiment()
    actions = guardian.check_once()

    assert _drift_alerts(actions, exp_id), \
        "阈值 0 应在约 1% 的连续同向漂移上告警（修复前被 `or 20` 按 20% 处理）"


def test_drift_threshold_default_still_gates_tiny_drift():
    """反例守卫：阈值保持默认 20% 时，约 1% 的漂移不得告警。"""
    assert guardian._thresholds()["drift_pct"] == 20, "前置断言：默认阈值应为 20"

    exp_id = _prepare_drift_experiment()
    actions = guardian.check_once()

    assert not _drift_alerts(actions, exp_id), \
        "默认 20% 阈值下约 1% 漂移不应告警（证明修复未把判据改成恒真）"


# ---------------------------------------------------------------
# 2. 供应商账期：0 天（现结）必须原样导出，None 才回落 30
# ---------------------------------------------------------------
def _payables_row_for(supplier_name):
    data = ExportDataProcessor._handle_supplier_payables_aging({}, None, False)
    for row in data["rows"]:
        if row[0] == supplier_name:
            return row
    raise AssertionError(f"未在应付账款表中找到供应商 {supplier_name}: {data['rows']}")


def test_payables_keeps_zero_payment_terms():
    db.create_supplier("零账期供应商", payment_terms_days=0)
    row = _payables_row_for("零账期供应商")
    assert row[3] == 0, f"账期 0 天被改写成了 {row[3]}（修复前为 30）"


def test_payables_none_payment_terms_falls_back_to_30():
    """反例守卫：未设置账期（None）仍回落 30。"""
    db.create_supplier("未设账期供应商", payment_terms_days=None)
    row = _payables_row_for("未设账期供应商")
    assert row[3] == 30, f"未设置账期应回落 30，实际 {row[3]}"


def test_payables_keeps_normal_payment_terms():
    """反例守卫：常规账期原样导出。"""
    db.create_supplier("月结供应商", payment_terms_days=45)
    row = _payables_row_for("月结供应商")
    assert row[3] == 45


# ---------------------------------------------------------------
# 3. 指标聚合 min_sample：0 必须生效（不做样本量门槛）
# ---------------------------------------------------------------
def test_grey_compare_zero_min_sample_is_not_low_confidence():
    data = db.get_grey_compare(min_sample=0)
    assert data["min_sample"] == 0, f"min_sample=0 被改写成了 {data['min_sample']}"
    assert data["low_confidence"] is False, \
        "min_sample=0 时不得判 low_confidence（修复前 n=0 < 30 恒为 True）"


def test_grey_compare_default_min_sample_still_gates():
    """反例守卫：默认门槛 30 下空数据仍判 low_confidence。"""
    data = db.get_grey_compare()
    assert data["min_sample"] == 30
    assert data["low_confidence"] is True


def test_ai_metrics_zero_min_sample_is_not_low_confidence():
    data = db.get_ai_metrics(min_sample=0)
    assert data["min_sample"] == 0, f"min_sample=0 被改写成了 {data['min_sample']}"
    assert data["total"]["low_confidence"] is False


def test_ai_metrics_default_min_sample_still_gates():
    data = db.get_ai_metrics()
    assert data["min_sample"] == 30
    assert data["total"]["low_confidence"] is True

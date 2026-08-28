# -*- coding: utf-8 -*-
"""
Issue U-10: 交叉审核开关启用与文案去技术化测试集
- 默认 engine_config 中 audit_enabled 与 grey_audit_enabled 均为 True
- PUT /api/admin/engine-config 支持持久化 audit_enabled 与 grey_audit_enabled
- supervisor._run_audit 正常执行并优雅降级（异常不阻断）
- supervisor._log_audit_decision 记录决策日志并携带有效 receipt_id
- 前端 main.js 文案去技术化（校验、人话提示、Pydantic 422 转化）
"""

import os
import sys
import json
import atexit
import shutil
import tempfile
import importlib
import pytest

# 隔离环境：独立 DB（必须在首次 import app.* 之前设置），
# 避免本文件裸跑时写仓库根 receipt_demo.db（同 test_legacy_db_migration.py 模式）
_TMP = tempfile.mkdtemp(prefix="u10_audit_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_u10.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))

from fastapi.testclient import TestClient

from app.main import app
from app import db as _db
from app.models import EngineConfig, ReceiptData, ReceiptItem, DocForm
from app.chains.supervisor import _run_audit, _log_audit_decision, run_pipeline

# 全量回归时 app.db 可能已被更早的测试模块以其他 DB_PATH 导入，
# reload 使引擎绑定到本文件的隔离库（行为断言不变）
importlib.reload(_db)

client = TestClient(app)


def test_u10_default_engine_config_audit_enabled():
    """AC-1: 默认引擎配置 audit_enabled 和 grey_audit_enabled 均为 True。"""
    cfg = EngineConfig()
    assert cfg.audit_enabled is True
    assert cfg.grey_audit_enabled is True

    # 验证 db.get_engine_config()
    db_cfg = _db.get_engine_config()
    assert hasattr(db_cfg, "audit_enabled")
    assert hasattr(db_cfg, "grey_audit_enabled")


def test_u10_admin_engine_config_persistence():
    """AC-2: PUT /api/admin/engine-config 持久化 audit_enabled 与 grey_audit_enabled。"""
    # 设为 False
    resp = client.put("/api/admin/engine-config", json={
        "audit_enabled": False,
        "grey_audit_enabled": False,
    }, headers={"X-Role": "admin", "X-User": "admin@test.com"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["audit_enabled"] is False
    assert data["grey_audit_enabled"] is False

    # 重新读取确认持久化
    saved_cfg = _db.get_engine_config()
    assert saved_cfg.audit_enabled is False
    assert saved_cfg.grey_audit_enabled is False

    # 恢复为 True
    resp2 = client.put("/api/admin/engine-config", json={
        "audit_enabled": True,
        "grey_audit_enabled": True,
    }, headers={"X-Role": "admin", "X-User": "admin@test.com"})
    assert resp2.status_code == 200
    assert resp2.json()["data"]["audit_enabled"] is True
    assert resp2.json()["data"]["grey_audit_enabled"] is True


def test_u10_run_audit_graceful_fallback():
    """AC-3: _run_audit 正常执行并在异常时优雅降级（不抛出未捕获异常）。"""
    sample_data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="德利行",
        date="2026-08-24",
        total=100.0,
        payment_marked=False,
        confidence=0.9,
        items=[ReceiptItem(name="生菜", qty=5.0, unit="斤", unit_price=20.0, amount=100.0)]
    )

    # 禁用 audit 时应返回 skipped
    disabled_cfg = EngineConfig(audit_enabled=False)
    res_disabled = _run_audit("nonexistent_path.jpg", sample_data, config=disabled_cfg, use_grey=False)
    assert res_disabled.get("skipped") is True

    # 路径非法或调用异常时应优雅降级
    res_err = _run_audit("invalid_image_path_for_test.xyz", sample_data, config=EngineConfig(audit_enabled=True), use_grey=False)
    assert isinstance(res_err, dict)
    assert "skipped" in res_err


def test_u10_log_audit_decision_with_receipt_id():
    """AC-4: _log_audit_decision 记录 audit 决策日志到 ai_decision_log 并绑定 receipt_id。"""
    rid = _db.create_receipt(supplier_name="审核测试店", status="uploaded")

    audit_payload = {
        "overall_consistent": True,
        "trust": 0.95,
        "discrepancies": [],
        "corrected_suggestions": {},
        "reason": "AI 识别与原图一致",
        "skipped": False,
    }

    cfg = EngineConfig(audit_enabled=True)
    _log_audit_decision(receipt_id=rid, experiment_id=None, config=cfg, use_grey=False, audit=audit_payload)

    decisions = _db.list_ai_decisions(rid)
    audit_decisions = [d for d in decisions if d.get("decision_type") == "audit"]
    assert len(audit_decisions) >= 1, f"未找到 receipt_id={rid} 的 audit 决策: {decisions}"
    
    val = audit_decisions[0].get("ai_value")
    if isinstance(val, str):
        val = json.loads(val)
    assert val.get("overall_consistent") is True
    assert val.get("trust") == 0.95


def test_u10_humanized_validation_and_error_handling_in_main_js():
    """AC-5: 前端 main.js 包含人话化校验文案与去技术化错误处理器。"""
    js_path = os.path.join(os.path.dirname(__file__), "..", "demo", "static", "js", "main.js")
    with open(js_path, "r", encoding="utf-8") as f:
        js_content = f.read()

    # 必须包含的人话文案
    required_phrases = [
        "请填写供应商名称",
        "请选择开单日期",
        "开单日期不正确，请选择真实存在的日历日期",
        "单据总金额必须大于 0",
        "请至少输入一行消费明细",
        "请选择结算方式（如现结或挂账）",
        "humanizeBackendError",
    ]
    for phrase in required_phrases:
        assert phrase in js_content, f"main.js 中缺少预期的人话提示: {phrase}"

    # 不应存在生硬的技术术语拼接
    assert "当前值：空" not in js_content, "main.js 中仍残留技术术语 '当前值：空'"

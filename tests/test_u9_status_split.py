# -*- coding: utf-8 -*-
"""
Issue U-9: 自动落库与人工编辑状态语义拆分测试集
- SaveEditedBody source="auto" vs source="manual" 行为差异
- auto_save 保持 status=parsed 并记录 action="auto_save" 及 decision_type="auto_save"
- save_edited 变更 status=edited 并记录 action="save_edited" 及 field diff
- approve 端点支持 parsed 和 edited 两种状态直接审批
- flag 端点支持 uploaded/parsing/parsed/edited 标记异常
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
_TMP = tempfile.mkdtemp(prefix="u9_status_split_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_u9.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))

from fastapi.testclient import TestClient

from app.main import app
from app import db as _db

# 全量回归时 app.db 可能已被更早的测试模块以其他 DB_PATH 导入，
# reload 使引擎绑定到本文件的隔离库（行为断言不变）
importlib.reload(_db)

client = TestClient(app)


def test_u9_auto_save_semantics():
    """测试 source='auto' (AI自动落库) 状态与审计履历语义。"""
    rid = _db.create_receipt(supplier_name="自动落库测试店", status="uploaded")
    row = _db.get_receipt_row(rid)
    
    # 模拟后台自动保存
    resp = client.post("/api/save_edited", json={
        "receipt_id": rid,
        "supplier_name": "自动落库测试店",
        "date": "2026-08-24",
        "total_amount": 180.0,
        "settlement_type": "cash",
        "version": row.version,
        "source": "auto",
        "items": [
            {"name": "新鲜生菜", "quantity": 10, "unit": "斤", "unit_price": 18.0, "amount": 180.0}
        ]
    }, headers={"X-Role": "staff"})
    
    assert resp.status_code == 200, resp.text
    
    # 1. 验证 status 保持为 parsed 而非 edited
    updated_row = _db.get_receipt_row(rid)
    assert updated_row.status == "parsed", f"预期 status='parsed'，实际='{updated_row.status}'"
    
    # 2. 验证 audit_logs 记录 action='auto_save' 与 details='AI自动识别落库'
    logs = json.loads(updated_row.audit_logs_json or "[]")
    auto_save_logs = [l for l in logs if l.get("action") == "auto_save"]
    assert len(auto_save_logs) >= 1, f"未找到 action='auto_save' 的审计记录: {logs}"
    assert auto_save_logs[0].get("details") == "AI自动识别落库"
    assert auto_save_logs[0].get("new") == "parsed"
    
    # 3. 验证 ai_decision_log 写入 decision_type='auto_save'
    decisions = _db.list_ai_decisions(rid)
    auto_decisions = [d for d in decisions if d.get("decision_type") == "auto_save"]
    assert len(auto_decisions) >= 1, f"未找到 decision_type='auto_save' 的 AI 决策记录: {decisions}"


def test_u9_manual_save_semantics():
    """测试 source='manual' (店员手工保存) 状态与审计履历语义。"""
    rid = _db.create_receipt(supplier_name="人工保存测试店", status="parsed")
    row = _db.get_receipt_row(rid)
    
    resp = client.post("/api/save_edited", json={
        "receipt_id": rid,
        "supplier_name": "人工保存测试店(修改版)",
        "date": "2026-08-24",
        "total_amount": 200.0,
        "settlement_type": "credit",
        "version": row.version,
        "source": "manual",
        "items": [
            {"name": "新鲜生菜", "quantity": 10, "unit": "斤", "unit_price": 20.0, "amount": 200.0}
        ]
    }, headers={"X-Role": "staff"})
    
    assert resp.status_code == 200, resp.text
    
    # 1. 验证 status 变为 edited
    updated_row = _db.get_receipt_row(rid)
    assert updated_row.status == "edited", f"预期 status='edited'，实际='{updated_row.status}'"
    
    # 2. 验证 audit_logs 记录 action='save_edited' 与 details='[店员手工修改/保存]'
    logs = json.loads(updated_row.audit_logs_json or "[]")
    save_edited_logs = [l for l in logs if l.get("action") == "save_edited"]
    assert len(save_edited_logs) >= 1, f"未找到 action='save_edited' 的审计记录: {logs}"
    status_log = [l for l in save_edited_logs if l.get("field") == "status"]
    assert len(status_log) >= 1
    assert status_log[0].get("details") == "[店员手工修改/保存]"
    assert status_log[0].get("new") == "edited"


def test_u9_approve_parsed_receipt_directly():
    """测试老板可直接审批通过 parsed 状态的单据（无需店员先编辑）。"""
    rid = _db.create_receipt(supplier_name="直通审批供应商", status="parsed")
    row = _db.get_receipt_row(rid)
    
    # 老板直接审批
    resp = client.post(f"/api/receipt/{rid}/approve", json={"version": row.version}, headers={"X-Role": "owner"})
    assert resp.status_code == 200, resp.text
    
    updated_row = _db.get_receipt_row(rid)
    assert updated_row.status == "approved"


def test_u9_approve_edited_receipt():
    """测试老板正常审批通过 edited 状态的单据。"""
    rid = _db.create_receipt(supplier_name="正常审批供应商", status="edited")
    row = _db.get_receipt_row(rid)
    
    resp = client.post(f"/api/receipt/{rid}/approve", json={"version": row.version}, headers={"X-Role": "owner"})
    assert resp.status_code == 200, resp.text
    
    updated_row = _db.get_receipt_row(rid)
    assert updated_row.status == "approved"


def test_u9_flag_receipt_all_valid_statuses():
    """测试老板可对 uploaded/parsing/parsed/edited 状态的单据标记异常。"""
    for initial_status in ["uploaded", "parsing", "parsed", "edited"]:
        rid = _db.create_receipt(supplier_name=f"标记异常_{initial_status}", status=initial_status)
        resp = client.post(f"/api/receipt/{rid}/flag", headers={"X-Role": "owner"})
        assert resp.status_code == 200, f"标记异常在 status={initial_status} 失败: {resp.text}"
        
        updated_row = _db.get_receipt_row(rid)
        assert updated_row.status == "flagged"

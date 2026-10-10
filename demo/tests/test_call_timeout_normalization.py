# -*- coding: utf-8 -*-
"""call_timeout_seconds 存量字段废弃后的行为验证（Round 7 重写）。

当前契约：db.get_engine_config() 在读取时 pop 掉 stored JSON 里的
call_timeout_seconds（db.py "清理已废弃的全局超时字段"），EngineConfig Field
default=60 兜底。per-leg 超时（OPENAI_REC_CALL_TIMEOUT 等）是现行的活跃机制。

守真不变量：
- 存量 call_timeout_seconds 无论写入什么值，get_engine_config() 一律返回 default 60
  （pop 保证旧值不泄露）
- set_engine_config 的写入闸门 clamp_call_timeout 仍然生效（90 落库为 60）
- PUT API 对 90 仍然钳到 60 并回显 warning
- llm._resolve_timeout 运行期取值不变
"""

import json
import os
import sqlite3
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db

client = TestClient(app)

DS_BASE = "https://dashscope.test.example.com/compatible-mode/v1"
DS_KEY = "sk-test-dashscope-1234567890"
DS_MODEL = "qwen-test-omni"
SF_BASE = "https://siliconflow.test.example.com/v1"
SF_KEY = "sk-test-siliconflow-1234567890"
SF_AUD_MODEL = "test-audit-vlm"

_DB = {}


def _raw_set_timeout(value):
    """绕过 ORM 与写闸门，直接改 SQLite 里的 JSON —— 模拟修复前遗留的存量值。"""
    con = sqlite3.connect(_DB["path"])
    try:
        row = con.execute(
            "select value from app_settings where key='engine_config'"
        ).fetchone()
        data = json.loads(row[0]) if row else {}
        data["call_timeout_seconds"] = value
        con.execute(
            "insert into app_settings(key, value) values('engine_config', ?) "
            "on conflict(key) do update set value=excluded.value",
            (json.dumps(data, ensure_ascii=False),),
        )
        con.commit()
    finally:
        con.close()


def _raw_get_timeout():
    con = sqlite3.connect(_DB["path"])
    try:
        row = con.execute(
            "select value from app_settings where key='engine_config'"
        ).fetchone()
        return json.loads(row[0]).get("call_timeout_seconds") if row else None
    finally:
        con.close()


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """隔离 DB + 钉住 .env 取值，避免依赖开发者本机 .env，也不写 live 库。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_call_timeout.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    _DB["path"] = test_db_path

    monkeypatch.setenv("DASHSCOPE_BASE_URL", DS_BASE)
    monkeypatch.setenv("OPENAI_REC_BASE_URL", DS_BASE)
    monkeypatch.setenv("DASHSCOPE_API_KEY", DS_KEY)
    monkeypatch.setenv("OPENAI_REC_API_KEY", DS_KEY)
    monkeypatch.setenv("QWEN_VL_MODEL", DS_MODEL)
    monkeypatch.setenv("OPENAI_REC_MODEL", DS_MODEL)
    monkeypatch.setenv("SILICONFLOW_BASE_URL", SF_BASE)
    monkeypatch.setenv("OPENAI_AUD_BASE_URL", SF_BASE)
    monkeypatch.setenv("SILICONFLOW_API_KEY", SF_KEY)
    monkeypatch.setenv("OPENAI_AUD_API_KEY", SF_KEY)
    monkeypatch.setenv("SILICONFLOW_AUDIT_MODEL", SF_AUD_MODEL)
    monkeypatch.setenv("OPENAI_AUD_MODEL", SF_AUD_MODEL)
    monkeypatch.delenv("ENGINE_CALL_TIMEOUT", raising=False)
    # 审核腿装配会做 /models 健康探针，离线测试统一视为健康，避免真实网络调用
    monkeypatch.setattr(db, "_openai_gateway_healthy", lambda *a, **k: True)

    # 先落一份完整配置，避免 raw 写入时没有基底 JSON
    db.set_engine_config(db.get_engine_config(), source="auto")

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


# ---------------------------------------------------------------
# 1. hydrate 存量归一
# ---------------------------------------------------------------
@pytest.mark.parametrize("stored", [90, 240, 61, 60, 30, 0, -5])
def test_stored_timeout_always_ignored(stored):
    """守真不变量：stored call_timeout_seconds 被 pop，无论存什么都返回 default 60。"""
    _raw_set_timeout(stored)
    assert _raw_get_timeout() == stored, "前置条件：raw 写入必须原样落库"

    db.hydrate_engine_config_from_env()
    cfg = db.get_engine_config()
    assert cfg.call_timeout_seconds == 60, (
        "存量值 %s 应被 pop 忽略，EngineConfig default=60 兜底" % stored)
    assert _raw_get_timeout() == 60, "normalize 后 set_engine_config 应回写 default"


def test_hydrate_timeout_for_manual_source():
    """守真不变量：manual 路径的存量 timeout 同样被 pop 忽略。"""
    cfg = db.get_engine_config()
    cfg.config_source = "manual"
    db.set_engine_config(cfg, source="manual")
    _raw_set_timeout(90)
    assert db.get_engine_config().config_source == "manual"

    db.hydrate_engine_config_from_env()

    after = db.get_engine_config()
    assert after.call_timeout_seconds == 60
    assert after.config_source == "manual", "归一不得把来源改回 auto"
    # manual 路径 hydrate 不调 set_engine_config → raw 值保持不变（正确行为：
    # stored 值已被 pop 忽略，无需回写）


@pytest.mark.parametrize("illegal", [0, -5])
def test_hydrate_stored_illegal_also_ignored(illegal):
    """守真不变量：非法值（0/负）同样被 pop 忽略——一律返回 default 60。"""
    _raw_set_timeout(illegal)
    db.hydrate_engine_config_from_env()
    assert db.get_engine_config().call_timeout_seconds == 60
    assert _raw_get_timeout() == 60


def test_hydrate_is_idempotent_for_compliant_value():
    _raw_set_timeout(60)
    db.hydrate_engine_config_from_env()
    db.hydrate_engine_config_from_env()
    assert db.get_engine_config().call_timeout_seconds == 60
    assert _raw_get_timeout() == 60


# ---------------------------------------------------------------
# 2. 写入闸门（覆盖 promote / rollback / guardian 等其余写入口）
# ---------------------------------------------------------------
def test_write_gate_clamps_timeout_even_without_hydrate():
    cfg = db.get_engine_config()
    cfg.call_timeout_seconds = 90
    db.set_engine_config(cfg, source="auto")
    assert _raw_get_timeout() == 60, "写闸门必须兜住所有绕过 hydrate 的写入路径"


# ---------------------------------------------------------------
# 3. API：保存归一 + 显式回显
# ---------------------------------------------------------------
def test_put_rejects_deprecated_timeout_field():
    """守真不变量：PUT 拒绝 call_timeout_seconds（UI 已移除，extra=forbid 拒绝）。"""
    resp = client.put("/api/admin/engine-config", json={"call_timeout_seconds": 90},
                      headers=_admin_headers())
    assert resp.status_code == 422
    detail = resp.json().get("detail", [])
    assert any(d.get("loc") == ["body", "call_timeout_seconds"] for d in detail if isinstance(d, dict)), \
        "422 必须指向 call_timeout_seconds 字段"


def test_put_rejects_deprecated_timeout_field_within_limit():
    """守真不变量：合法值 30 同样被拒绝（字段已废弃，不区分值是否在限内）。"""
    resp = client.put("/api/admin/engine-config", json={"call_timeout_seconds": 30},
                      headers=_admin_headers())
    assert resp.status_code == 422


def test_get_returns_default_after_pop():
    """守真不变量：GET 回显 default 60（stored 值被 pop 忽略）。"""
    _raw_set_timeout(90)
    resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    perf = resp.json()["perf"]
    assert perf["call_timeout_seconds"] == 60, (
        "stored 90 被 pop，EngineConfig default=60 兜底")


# ---------------------------------------------------------------
# 4. 运行期解析（唯一事实源 + 非法值口径）
# ---------------------------------------------------------------
def test_resolve_timeout_runtime_behavior():
    from app.llm import _resolve_timeout
    from app.models import EngineConfig, clamp_call_timeout, MAX_CALL_TIMEOUT_SECONDS

    assert MAX_CALL_TIMEOUT_SECONDS == 60
    assert _resolve_timeout(EngineConfig(call_timeout_seconds=90)) == 60
    assert _resolve_timeout(EngineConfig(call_timeout_seconds=60)) == 60
    assert _resolve_timeout(EngineConfig(call_timeout_seconds=30)) == 30
    # 0 / 负值 = 未设置 → 回落缺省 30
    assert _resolve_timeout(EngineConfig(call_timeout_seconds=0)) == 30
    assert _resolve_timeout(EngineConfig(call_timeout_seconds=-1)) == 30
    # clamp 口径：只钳超限，其余原样（bool 不得被当成 1）
    assert clamp_call_timeout(90) == 60
    assert clamp_call_timeout(60) == 60
    assert clamp_call_timeout(0) == 0
    assert clamp_call_timeout(None) is None
    assert clamp_call_timeout(True) is True
    assert clamp_call_timeout("90") == "90"

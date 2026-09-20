# -*- coding: utf-8 -*-
"""T9 收尾：call_timeout_seconds 存量值归一（含 manual 路径与写闸门）。

背景：EngineConfig.call_timeout_seconds 默认值已按用户拍板改为 60，llm._resolve_timeout
仍做 min(v,60) 硬钳。但 live 库 app_settings.engine_config 里存的仍是历史值 90，且
hydrate_engine_config_from_env() 不管这个字段 —— 管理台回显 90、实际生效 60，
正是要消除的「填 90 实际只有 60」误导。

修复点（三处共用 app.models.clamp_call_timeout，唯一事实源 MAX_CALL_TIMEOUT_SECONDS=60）：
  1. db._normalize_call_timeout —— hydrate 装配时归一存量值（auto 与 manual 两条路径都生效）
  2. db.set_engine_config —— 写入闸门兜底（覆盖 promote / rollback / guardian 等其余写入口）
  3. api_admin PUT /api/admin/engine-config —— 保存时归一并向管理台显式回显

覆盖：
- hydrate：存量 90 → 60；60/30 保持不变（幂等）；manual 来源同样被钳到 60
- 非法值 0 / 负值 / None 不被改写成 60（口径与 _resolve_timeout 一致：视为未设置）
- 写闸门：绕过 hydrate 直接 set_engine_config(90) 也会落库为 60
- PUT API：填 90 落库 60 且响应带 warning；填 30 原样保存
- GET API：perf 回显实际生效超时
- _resolve_timeout 运行期取值
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
    monkeypatch.setenv("DASHSCOPE_API_KEY", DS_KEY)
    monkeypatch.setenv("QWEN_VL_MODEL", DS_MODEL)
    monkeypatch.setenv("SILICONFLOW_BASE_URL", SF_BASE)
    monkeypatch.setenv("SILICONFLOW_API_KEY", SF_KEY)
    monkeypatch.setenv("SILICONFLOW_AUDIT_MODEL", SF_AUD_MODEL)
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
@pytest.mark.parametrize("stored,expected", [(90, 60), (240, 60), (61, 60), (60, 60), (30, 30)])
def test_hydrate_normalizes_legacy_timeout(stored, expected):
    _raw_set_timeout(stored)
    assert _raw_get_timeout() == stored, "前置条件：raw 写入必须原样落库（证明闸门被绕过）"

    db.hydrate_engine_config_from_env()

    assert db.get_engine_config().call_timeout_seconds == expected
    assert _raw_get_timeout() == expected, "归一结果必须真正落盘，不能只改内存"


def test_hydrate_normalizes_timeout_for_manual_source():
    """manual 路径在 _normalize_call_timeout 之前 return 的话，这条必挂（关键防回归）。"""
    cfg = db.get_engine_config()
    cfg.config_source = "manual"
    db.set_engine_config(cfg, source="manual")
    _raw_set_timeout(90)
    assert db.get_engine_config().config_source == "manual"

    db.hydrate_engine_config_from_env()

    after = db.get_engine_config()
    assert after.call_timeout_seconds == 60
    assert after.config_source == "manual", "归一不得把来源改回 auto"
    assert _raw_get_timeout() == 60


@pytest.mark.parametrize("illegal", [0, -5])
def test_hydrate_keeps_illegal_timeout_untouched(illegal):
    """0 / 负值属「未设置」，口径与 _resolve_timeout 一致，不得被改写成 60。"""
    _raw_set_timeout(illegal)
    db.hydrate_engine_config_from_env()
    assert db.get_engine_config().call_timeout_seconds == illegal
    assert _raw_get_timeout() == illegal


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
def test_put_clamps_and_warns():
    body = {"call_timeout_seconds": 90}
    resp = client.put("/api/admin/engine-config", json=body, headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["data"]["call_timeout_seconds"] == 60
    assert "90" in payload.get("warning", ""), "必须显式回显被归一，否则管理台仍以为填了 90 生效"
    assert "timeout_advice" in payload
    assert db.get_engine_config().call_timeout_seconds == 60
    assert _raw_get_timeout() == 60


def test_put_keeps_value_within_limit():
    resp = client.put("/api/admin/engine-config",
                      json={"call_timeout_seconds": 30}, headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["call_timeout_seconds"] == 30
    assert "warning" not in resp.json()
    assert db.get_engine_config().call_timeout_seconds == 30


def test_get_echoes_effective_timeout():
    _raw_set_timeout(90)
    resp = client.get("/api/admin/engine-config", headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    perf = resp.json()["perf"]
    assert perf["call_timeout_seconds"] == 90
    assert perf["effective_call_timeout_seconds"] == 60
    assert resp.json().get("timeout_advice"), "GET 也应提示存量超限已被钳制"


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

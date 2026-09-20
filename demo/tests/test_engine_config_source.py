# -*- coding: utf-8 -*-
"""T1：引擎配置来源（config_source）与 startup hydrate 覆盖行为回归测试。

为什么要有这组用例：此前 db.hydrate_engine_config_from_env() 每次启动都按 .env
无条件重装双腿，导致管理台保存的引擎配置活不过一次 uvicorn --reload（reload 会重跑
startup）。修复后由 EngineConfig.config_source 决定：manual 跳过覆盖，auto 仍按 .env 重装。

覆盖：
1. PUT /api/admin/engine-config 保存后 config_source=manual，且再跑 hydrate 不被打回
2. POST /api/admin/engine-config/reset 复位为 auto 并立即重新装配（回到 .env 值）
3. 反证：auto 来源下 hydrate 仍会按 .env 覆盖（证明第 1 条不是恒真断言）
4. 止损兜底：manual 且识别腿密钥缺失到无法调用时，用 .env 补全且来源保持 manual
5. EngineConfigBody 声明 config_source，避免 extra=forbid 导致带上该字段的请求 422
"""

import os
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


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """隔离 DB + 钉住 .env 取值，避免依赖开发者本机 .env，也不写 live 库。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_engine_config_source.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()

    monkeypatch.setenv("DASHSCOPE_BASE_URL", DS_BASE)
    monkeypatch.setenv("DASHSCOPE_API_KEY", DS_KEY)
    monkeypatch.setenv("QWEN_VL_MODEL", DS_MODEL)
    monkeypatch.setenv("SILICONFLOW_BASE_URL", SF_BASE)
    monkeypatch.setenv("SILICONFLOW_API_KEY", SF_KEY)
    monkeypatch.setenv("SILICONFLOW_AUDIT_MODEL", SF_AUD_MODEL)
    # 审核腿装配会做 /models 健康探针，离线测试统一视为健康，避免真实网络调用
    monkeypatch.setattr(db, "_openai_gateway_healthy", lambda *a, **k: True)

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


def _manual_body():
    return {
        "recognition_engine": "openai",
        "recognition_model": "my-rec-model",
        "openai_rec_base_url": "https://manual-rec.example.com/v1",
        "openai_rec_api_key": "sk-manual-rec-1234567890",
        "openai_rec_model": "my-rec-model",
        "audit_enabled": True,
        "audit_engine": "openai",
        "audit_model": "my-aud-model",
        "openai_aud_base_url": "https://manual-aud.example.com/v1",
        "openai_aud_api_key": "sk-manual-aud-1234567890",
        "openai_aud_model": "my-aud-model",
    }


def test_put_marks_manual_and_survives_hydrate():
    """PUT 保存 → manual；手动再跑 hydrate 不得把配置打回 .env 装配值。"""
    resp = client.put("/api/admin/engine-config", json=_manual_body(), headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["config_source"] == "manual"

    cfg = db.get_engine_config()
    assert cfg.config_source == "manual"
    assert cfg.openai_rec_model == "my-rec-model"
    assert cfg.openai_rec_base_url == "https://manual-rec.example.com/v1"
    assert cfg.openai_aud_model == "my-aud-model"

    # 模拟服务重启（startup 钩子）：值必须原样保留
    db.hydrate_engine_config_from_env()
    cfg2 = db.get_engine_config()
    assert cfg2.config_source == "manual"
    assert cfg2.openai_rec_model == "my-rec-model"
    assert cfg2.openai_rec_base_url == "https://manual-rec.example.com/v1"
    assert cfg2.openai_aud_model == "my-aud-model"
    assert cfg2.openai_aud_base_url == "https://manual-aud.example.com/v1"


def test_auto_config_is_still_overwritten_by_hydrate():
    """反证：auto 来源下 hydrate 仍按 .env 覆盖，说明上一条的 manual 分支在真正起作用。"""
    cfg = db.get_engine_config()
    cfg.config_source = "auto"
    cfg.openai_rec_base_url = "https://stale.example.com/v1"
    cfg.openai_rec_model = "stale-model"
    db.set_engine_config(cfg, source="auto")

    db.hydrate_engine_config_from_env()
    cfg2 = db.get_engine_config()
    assert cfg2.config_source == "auto"
    assert cfg2.openai_rec_base_url == DS_BASE
    assert cfg2.openai_rec_model == DS_MODEL
    assert cfg2.openai_aud_model == SF_AUD_MODEL


def test_reset_returns_to_env_assembly():
    """reset → auto 并立即重新装配；再跑 hydrate 仍为 .env 值（幂等）。"""
    assert client.put("/api/admin/engine-config", json=_manual_body(),
                      headers=_admin_headers()).status_code == 200

    resp = client.post("/api/admin/engine-config/reset", headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["config_source"] == "auto"

    cfg = db.get_engine_config()
    assert cfg.config_source == "auto"
    assert cfg.openai_rec_base_url == DS_BASE
    assert cfg.openai_rec_model == DS_MODEL
    assert cfg.openai_aud_model == SF_AUD_MODEL

    db.hydrate_engine_config_from_env()
    cfg2 = db.get_engine_config()
    assert cfg2.config_source == "auto"
    assert cfg2.openai_rec_model == DS_MODEL


def test_manual_missing_key_is_filled_from_env_but_stays_manual():
    """止损兜底：manual 且识别腿密钥空到无法调用时用 .env 补全，来源仍为 manual。"""
    cfg = db.get_engine_config()
    cfg.config_source = "manual"
    cfg.openai_rec_base_url = ""
    cfg.openai_rec_api_key = ""
    cfg.openai_rec_model = ""
    cfg.recognition_model = ""
    db.set_engine_config(cfg, source="manual")

    db.hydrate_engine_config_from_env()
    cfg2 = db.get_engine_config()
    assert cfg2.config_source == "manual", "兜底补全不得把来源改回 auto"
    assert cfg2.openai_rec_base_url == DS_BASE
    assert cfg2.openai_rec_api_key == DS_KEY
    assert cfg2.openai_rec_model == DS_MODEL


def test_reset_rbac_and_body_accepts_config_source():
    """reset 需 admin；EngineConfigBody 声明 config_source，带该字段的请求不得 422。"""
    anon = client.post("/api/admin/engine-config/reset")
    assert anon.status_code in (401, 403)

    body = _manual_body()
    body["config_source"] = "auto"  # 请求体声明应被忽略，后端仍强制 manual
    resp = client.put("/api/admin/engine-config", json=body, headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    assert db.get_engine_config().config_source == "manual"


# ---------------------------------------------------------------
# N4：推全/回滚（guardian.perform_engine_rollback）也是人工改配，
#     必须置 manual，否则重启后被 .env 打回，回滚白做
# ---------------------------------------------------------------
def _promote_like_snapshot_prev():
    """复刻 api_admin.promote_grey_config 写入 rollback_snapshot['prev'] 的字段集合。"""
    return {
        "recognition_engine": "openai",
        "recognition_model": "prev-rec-model",
        "audit_engine": "openai",
        "audit_model": "prev-aud-model",
        "audit_enabled": True,
        "openai_rec_base_url": "https://prev-rec.example.com/v1",
        "openai_rec_api_key": "sk-prev-rec-1234567890",
        "openai_rec_model": "prev-rec-model",
        "openai_aud_base_url": "https://prev-aud.example.com/v1",
        "openai_aud_api_key": "sk-prev-aud-1234567890",
        "openai_aud_model": "prev-aud-model",
    }


def test_guardian_rollback_marks_manual_and_survives_hydrate():
    """N4：guardian 回滚结果必须是 manual，且再跑 hydrate 不被打回 .env 装配值。"""
    from app.services.guardian import perform_engine_rollback

    # 现状 = 刚推全完的常规组配置（auto）+ 一份推全前的回滚快照
    cfg = db.get_engine_config()
    cfg.config_source = "auto"
    cfg.openai_rec_base_url = "https://after-promote.example.com/v1"
    cfg.openai_rec_model = "after-promote-model"
    cfg.rollback_snapshot = {"prev": _promote_like_snapshot_prev(),
                             "meta": {"action": "promote", "operator": "test"}}
    db.set_engine_config(cfg, source="auto")

    ok, new_cfg = perform_engine_rollback(who="test")
    assert ok and new_cfg is not None

    cur = db.get_engine_config()
    assert cur.config_source == "manual", "回滚属人工改配，必须置 manual"
    assert cur.openai_rec_base_url == "https://prev-rec.example.com/v1"
    assert cur.openai_rec_model == "prev-rec-model"
    assert cur.openai_aud_model == "prev-aud-model"
    assert cur.rollback_snapshot is None

    # 模拟服务重启（startup 钩子 hydrate）：回滚结果不得被打回 .env
    db.hydrate_engine_config_from_env()
    after = db.get_engine_config()
    assert after.config_source == "manual"
    assert after.openai_rec_base_url == "https://prev-rec.example.com/v1"
    assert after.openai_rec_model == "prev-rec-model"
    assert after.openai_aud_base_url == "https://prev-aud.example.com/v1"


def test_guardian_rollback_via_admin_endpoint_also_stays_manual():
    """N4：管理台 PUT /api/admin/engine-config/rollback 走同一函数，同样不得被打回。"""
    cfg = db.get_engine_config()
    cfg.rollback_snapshot = {"prev": _promote_like_snapshot_prev(), "meta": {}}
    db.set_engine_config(cfg, source="auto")

    resp = client.put("/api/admin/engine-config/rollback", headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["config_source"] == "manual"

    db.hydrate_engine_config_from_env()
    assert db.get_engine_config().config_source == "manual"
    assert db.get_engine_config().openai_rec_model == "prev-rec-model"

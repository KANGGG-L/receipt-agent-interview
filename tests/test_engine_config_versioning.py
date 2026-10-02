# -*- coding: utf-8 -*-
"""引擎配置版本化（本轮新增后端契约）测试集。

覆盖规格 01-spec.md 的新增 AC：
- AC-19 乐观锁：If-Match 头 / body.version 与库内不符 → 409，附当前 version 与字段级 diff
- AC-20 保存成功 → version + 1 且落一条 engine_config_history(action=save)
- AC-21 /history 分页，save/promote/rollback/reset 四动作各可查得
- AC-22 /routing 分组 DTO：含 version 与 has_key，不含 api_key 明文 / rollback_snapshot /
        vendor_name_* / engine 枚举
- AC-23 掩码回环：掩码 api_key 回写不得覆盖库内真实密钥
- AC-26 旧 GET /api/admin/engine-config 形态不变（data / perf / timeout_advice）

另含新端点 RBAC（admin 200 / staff 403 / 匿名 401）与 history 快照落库脱敏断言。
"""

import json
import os
import sys

import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.main import app
from app.services.security_guard import mask_secret_key

client = TestClient(app)

BASE = "https://93.184.216.34/v1"
REAL_KEY = "sk-versioning-real-key-1234567890"
REAL_REC_MODEL = "Qwen/Qwen3-VL-32B-Instruct"

ADMIN = {"X-Role": "admin", "X-Email": "admin@demo.hk"}
STAFF = {"X-Role": "staff", "X-Email": "staff@demo.hk"}

ROUTING_LEGS = ("rec", "aud", "parse", "grey_rec", "grey_aud", "grey_parse")


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """隔离 DB，并预置一份「可保存」的识别腿配置（公网 IP 字面量，SSRF 网关放行）。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_engine_versioning.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()

    cfg = db.get_engine_config()
    cfg.config_source = "manual"
    cfg.openai_rec_base_url = BASE
    cfg.openai_rec_api_key = REAL_KEY
    cfg.openai_rec_model = REAL_REC_MODEL
    db.set_engine_config(cfg, source="manual")

    yield

    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


def _body(**overrides):
    payload = {
        "openai_rec_base_url": BASE,
        "openai_rec_api_key": REAL_KEY,
        "openai_rec_model": REAL_REC_MODEL,
    }
    payload.update(overrides)
    return payload


def _history(params=None):
    resp = client.get("/api/admin/engine-config/history", headers=ADMIN, params=params or {})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _enable_grey(version):
    resp = client.put("/api/admin/engine-config", headers=ADMIN, json=_body(
        version=version,
        grey_enabled=True,
        grey_percent=10,
        grey_assign_mode="receipt",
        grey_openai_rec_base_url=BASE,
        grey_openai_rec_api_key=REAL_KEY,
        grey_openai_rec_model="grey-rec-model",
    ))
    assert resp.status_code == 200, resp.text
    return resp.json()["version"]


# -------------------------------------------------------------
# AC-19：乐观锁 409
# -------------------------------------------------------------
def test_put_body_version_conflict_returns_409_with_field_diff():
    stored = db.get_engine_config().version

    resp = client.put("/api/admin/engine-config", headers=ADMIN,
                      json=_body(version=stored + 5, openai_rec_model="changed-model"))

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "VERSION_CONFLICT"
    assert body["current_version"] == stored
    assert body["version"] == stored
    assert "openai_rec_model" in body["changed_keys"]
    entry = next(d for d in body["diff"] if d["key"] == "openai_rec_model")
    assert entry["old"] == REAL_REC_MODEL
    assert entry["new"] == "changed-model"
    # 冲突的写入不得落库，也不得推进版本
    cur = db.get_engine_config()
    assert cur.version == stored
    assert cur.openai_rec_model == REAL_REC_MODEL


def test_put_if_match_header_conflict_then_success():
    stored = db.get_engine_config().version

    conflict = client.put("/api/admin/engine-config",
                          headers={**ADMIN, "If-Match": str(stored + 1)},
                          json=_body(openai_rec_model="if-match-model"))
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["current_version"] == stored

    # If-Match 头优先于 body.version：头正确 + body 版本陈旧 → 仍按头放行
    ok = client.put("/api/admin/engine-config",
                    headers={**ADMIN, "If-Match": f'"{stored}"'},
                    json=_body(version=stored - 1, openai_rec_model="if-match-model"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["version"] == stored + 1
    cur = db.get_engine_config()
    assert cur.version == stored + 1
    assert cur.openai_rec_model == "if-match-model"


def test_put_without_version_stays_backward_compatible():
    """旧前端/脚本不带版本 → 不校验、可保存（乐观锁不得变成硬门槛）。"""
    stored = db.get_engine_config().version
    resp = client.put("/api/admin/engine-config", headers=ADMIN,
                      json=_body(openai_rec_model="legacy-model"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == stored + 1


# -------------------------------------------------------------
# AC-20：version + 1 与 action=save 历史
# -------------------------------------------------------------
def test_save_increments_version_and_records_history():
    stored = db.get_engine_config().version

    resp = client.put("/api/admin/engine-config", headers=ADMIN,
                      json=_body(version=stored, openai_rec_model="history-model"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == stored + 1

    cur = db.get_engine_config()
    assert cur.version == stored + 1
    assert cur.model_dump()["version"] == stored + 1

    data = _history()
    assert data["total"] >= 1
    latest = data["items"][0]
    assert latest["version"] == stored + 1
    assert latest["action"] == "save"
    assert latest["actor"] == "admin@demo.hk"
    assert "openai_rec_model" in latest["changed_keys"]
    assert latest["created_at"]


def test_engine_config_version_default_is_additive():
    """加法式字段：全新配置默认 version=1，EngineConfigBody 声明 version 避免 422。"""
    from app.api_admin import EngineConfigBody
    from app.models import EngineConfig

    assert EngineConfig().version == 1
    assert "version" in EngineConfigBody.model_fields
    parsed = EngineConfigBody(**{"version": 7, "openai_rec_model": "m"})
    assert parsed.version == 7


# -------------------------------------------------------------
# AC-21：/history 分页 + 四动作
# -------------------------------------------------------------
def test_history_records_save_promote_rollback_reset_and_paginates():
    v = db.get_engine_config().version
    v1 = client.put("/api/admin/engine-config", headers=ADMIN,
                    json=_body(version=v, openai_rec_model="save-1")).json()["version"]
    v2 = _enable_grey(v1)

    promote = client.put("/api/admin/engine-config/promote", headers=ADMIN)
    assert promote.status_code == 200, promote.text
    assert promote.json()["version"] == v2 + 1

    rollback = client.put("/api/admin/engine-config/rollback", headers=ADMIN)
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["version"] == v2 + 2

    reset = client.post("/api/admin/engine-config/reset", headers=ADMIN)
    assert reset.status_code == 200, reset.text
    assert reset.json()["version"] == v2 + 3
    assert db.get_engine_config().version == v2 + 3

    data = _history()
    by_action = {item["action"]: item for item in data["items"]}
    for action in ("save", "promote", "rollback", "reset"):
        assert action in by_action, f"history 缺少 action={action}: {data['items']}"
        assert isinstance(by_action[action]["changed_keys"], list)
    # version 倒序（首项即最新）
    versions = [item["version"] for item in data["items"]]
    assert versions == sorted(versions, reverse=True)
    assert data["total"] >= 4
    assert data["page"] == 1 and data["page_size"] == 20

    page1 = _history({"page": 1, "page_size": 2})
    assert len(page1["items"]) == 2
    assert page1["total"] == data["total"]
    page2 = _history({"page": 2, "page_size": 2})
    assert page2["items"][0]["version"] != page1["items"][0]["version"]

    # page_size 上限 100
    capped = _history({"page": 1, "page_size": 500})
    assert capped["page_size"] == 100


def test_history_snapshot_is_masked_and_never_exposed():
    client.put("/api/admin/engine-config", headers=ADMIN,
               json=_body(version=db.get_engine_config().version,
                          openai_rec_api_key="sk-brand-new-secret-key-9999"))

    s = db.get_session()
    try:
        rows = s.query(db._EngineConfigHistoryRow).all()
    finally:
        s.close()
    assert rows, "写操作必须落一条版本历史"
    for row in rows:
        assert REAL_KEY not in (row.snapshot or ""), "历史快照不得落真实密钥"
        assert "sk-brand-new-secret-key-9999" not in (row.snapshot or "")
    # 端点只回摘要，不回传 snapshot
    item = _history()["items"][0]
    assert "snapshot" not in item
    assert set(item) == {"version", "actor", "action", "created_at", "changed_keys"}


# -------------------------------------------------------------
# AC-22：分组瘦身 DTO
# -------------------------------------------------------------
def test_routing_dto_shape_and_no_internal_state():
    stored = db.get_engine_config().version

    resp = client.get("/api/admin/engine-config/routing", headers=ADMIN)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["version"] == stored
    assert set(data["has_key"]) == set(ROUTING_LEGS)
    assert data["has_key"]["rec"] is True
    assert all(isinstance(v, bool) for v in data["has_key"].values())
    for module in ("module_a", "module_b", "module_c", "module_d"):
        assert module in data
    assert set(data["module_a"]["legs"]) == {"rec", "aud", "parse"}
    assert set(data["module_b"]["prices"]) == set(ROUTING_LEGS)
    assert set(data["module_c"]["legs"]) == {"grey_rec", "grey_aud", "grey_parse"}
    assert data["module_a"]["legs"]["rec"]["model"] == REAL_REC_MODEL
    assert data["module_a"]["legs"]["rec"]["advanced"]["max_tokens"] is not None
    assert data["module_d"]["config_source"] == "manual"

    blob = json.dumps(data, ensure_ascii=False)
    # 禁含：明文密钥 / api_key 键名 / rollback_snapshot / vendor_name_* / engine 枚举
    assert REAL_KEY not in blob
    assert "api_key" not in blob
    assert "rollback_snapshot" not in blob
    assert "vendor_name" not in blob
    assert "recognition_engine" not in blob
    assert "grey_recognition_engine" not in blob
    assert "openai_rec_api_key" not in blob


# -------------------------------------------------------------
# AC-23：掩码回环保护
# -------------------------------------------------------------
def test_masked_api_key_roundtrip_keeps_stored_secret():
    stored = db.get_engine_config().version
    masked = mask_secret_key(REAL_KEY)
    assert "****" in masked

    resp = client.put("/api/admin/engine-config", headers=ADMIN, json=_body(
        version=stored, openai_rec_api_key=masked, openai_rec_model="mask-roundtrip-model"))
    assert resp.status_code == 200, resp.text
    assert db.get_engine_config().openai_rec_api_key == REAL_KEY
    assert REAL_KEY not in json.dumps(resp.json(), ensure_ascii=False)


# -------------------------------------------------------------
# AC-26：旧 GET 形态不变
# -------------------------------------------------------------
def test_legacy_get_engine_config_shape_unchanged():
    resp = client.get("/api/admin/engine-config", headers=ADMIN)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "success"
    assert "data" in body and "perf" in body
    assert set(body["perf"]["effective_timeouts"]) == set(ROUTING_LEGS)
    assert "call_timeout_seconds" in body["perf"]
    # 既有回退通道仍可用：data 内保留 rollback_snapshot 与 version
    assert "rollback_snapshot" in body["data"]
    assert body["data"]["version"] == db.get_engine_config().version
    assert REAL_KEY not in json.dumps(body, ensure_ascii=False)


# -------------------------------------------------------------
# RBAC：新端点与既有端点同口径
# -------------------------------------------------------------
def test_new_endpoints_rbac_admin_staff_anonymous():
    for path in ("/api/admin/engine-config/routing", "/api/admin/engine-config/history"):
        assert client.get(path, headers=ADMIN).status_code == 200, path
        assert client.get(path, headers=STAFF).status_code == 403, path
        assert client.get(path).status_code == 401, path

# -*- coding: utf-8 -*-
"""B1 回归：平台层——错误信封、鉴权会话、启动安全、健康检查、备份、静态资源、密钥加密、迁移。"""

import gzip
import inspect
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.main import app

try:  # 基线（修复前）代码没有这些模块：导入失败时置 None，让依赖它们的用例在基线上以失败收场
    from app import maintenance, money, secret_store, web_assets
    from app.main import resolve_bind_host
except ImportError:  # pragma: no cover
    maintenance = money = secret_store = web_assets = resolve_bind_host = None

client = TestClient(app)
ADMIN = {"X-Role": "admin"}
OWNER = {"X-Role": "owner"}
STAFF = {"X-Role": "staff"}
DEMO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TECH_WORDS = ("Bearer", "X-Role", "magic bytes", "status='", "GET /api", "JSON", "token", "schema")


@pytest.fixture(autouse=True)
def _reset_throttle():
    reset = getattr(auth, "reset_login_throttle", lambda: None)
    reset()
    yield
    reset()


# ---- S1-P2-01 / S1-P2-02 错误信封与人话 ----------------------------------
def _assert_envelope(resp, status, code=None):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["code"] and body["msg"]
    if code:
        assert body["code"] == code
    for w in TECH_WORDS:
        assert w not in body["msg"], (w, body["msg"])
    return body


def test_envelope_for_404_401_403_405():
    _assert_envelope(client.get("/api/nope", headers=OWNER), 404, "NOT_FOUND")
    _assert_envelope(client.get("/api/receipts"), 401, "UNAUTHENTICATED")
    b = _assert_envelope(client.get("/api/cost", headers=STAFF), 403, "FORBIDDEN")
    assert "老板" in b["msg"] and "店员" in b["msg"]
    _assert_envelope(client.delete("/api/receipts", headers=OWNER), 405, "METHOD_NOT_ALLOWED")


def test_validation_error_is_chinese_and_names_the_field():
    r = client.post("/api/save_edited", headers=STAFF,
                    json={"total_amount": "不是数字", "items": []})
    body = _assert_envelope(r, 422, "VALIDATION_ERROR")
    assert "单据总额" in body["msg"] and "数字" in body["msg"]
    assert isinstance(body["detail"], list) and body["detail"][0]["field"] == "total_amount"
    assert "loc" in body["detail"][0]  # 旧前端按 loc 末段定位字段
    r2 = client.post("/api/auth/login", json={"email": "a@b.c"})
    b2 = _assert_envelope(r2, 422, "VALIDATION_ERROR")
    assert "密码" in b2["msg"]


def test_version_errors_are_plain_language():
    rid = client.post("/api/save_edited", headers=STAFF, json={
        "supplier_name": "供应商", "date": "2026-10-01", "total_amount": 10.0,
        "settlement_type": "cash",
        "items": [{"name": "x", "quantity": 1, "unit": "个", "unit_price": 10, "amount": 10}]}
    ).json()["receipt_id"]
    r = client.post("/api/save_edited", headers=STAFF, json={
        "receipt_id": rid, "supplier_name": "供应商", "date": "2026-10-01",
        "total_amount": 10.0, "settlement_type": "cash", "items": []})
    body = _assert_envelope(r, 400, "VERSION_REQUIRED")
    assert "刷新" in body["msg"] and "{id}" not in body["msg"]
    r = client.post(f"/api/receipt/{rid}/approve", json={"version": 999}, headers=OWNER)
    body = _assert_envelope(r, 409, "VERSION_CONFLICT")
    assert "刷新" in body["msg"]


def test_invalid_status_message_has_no_status_literal():
    rid = client.post("/api/save_edited", headers=STAFF, json={
        "supplier_name": "供应商", "date": "2026-10-01", "total_amount": 10.0,
        "settlement_type": "cash",
        "items": [{"name": "x", "quantity": 1, "unit": "个", "unit_price": 10, "amount": 10}]}
    ).json()["receipt_id"]
    client.post(f"/api/receipt/{rid}/flag", headers=OWNER)
    ver = db.get_receipt_row(rid, tenant_id="default").version
    r = client.post(f"/api/receipt/{rid}/approve", json={"version": ver}, headers=OWNER)
    body = _assert_envelope(r, 409, "INVALID_STATUS")
    assert "flagged" not in body["msg"] and "解除" in body["msg"]


def test_business_errors_use_4xx_not_200():
    r = client.post("/api/save_edited", headers=STAFF, json={
        "supplier_name": "供应商", "date": "2026-10-01", "total_amount": 10.0,
        "settlement_type": None, "items": []})
    assert r.status_code == 422 and r.json()["code"] == "SETTLEMENT_REQUIRED"


def test_upload_rejection_is_plain_language(tmp_path):
    # 文件头像 JPEG、内容是垃圾：走「图片无法解析」分支，修复前文案带 magic bytes / 解码库英文报错
    files = {"receipt": ("a.jpg", b"\xff\xd8\xff\xe0" + b"junk" * 50, "image/jpeg")}
    r = client.post("/api/upload", headers=STAFF, files=files, data={"async_": "true"})
    assert r.status_code == 400
    body = r.json()
    for bad in ("magic", "Truncated", "cannot identify", "Errno", "verify"):
        assert bad.lower() not in body["msg"].lower(), body["msg"]
    assert "照片" in body["msg"] and "重新" in body["msg"]


def test_unhandled_exception_returns_envelope_without_internals():
    from fastapi import APIRouter
    r = APIRouter()

    @r.get("/api/_b1_boom")
    def boom():
        raise RuntimeError("secret internal detail /etc/passwd")

    before = list(app.router.routes)
    app.include_router(r)
    try:
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/api/_b1_boom", headers=OWNER)
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "INTERNAL_ERROR"
        assert "passwd" not in resp.text and "RuntimeError" not in resp.text
    finally:
        # 探测路由只活在本用例内。留在全局 app 上会让后续权限扫描看到未知端点。
        app.router.routes[:] = before
        app.openapi_schema = None


# ---- S1-P3-01 / S4-08 登录、哈希、吊销、Cookie、CSRF -----------------------
def test_passwords_are_hashed_and_verified_constant_time():
    for email, (stored, _role, _t) in auth.ACCOUNTS.items():
        assert stored.startswith("pbkdf2_sha256$")
        assert "123" not in stored
    assert auth.authenticate("boss@demo.hk", "boss123")["role"] == "owner"
    assert auth.authenticate("boss@demo.hk", "wrong") is None
    assert auth.authenticate("nobody@demo.hk", "boss123") is None
    src = inspect.getsource(auth.verify_password)
    assert "compare_digest" in src
    assert auth.verify_password("pw", "pw") and not auth.verify_password("pw", "px")


def test_login_throttle_and_backoff():
    for i in range(5):
        r = client.post("/api/auth/login", json={"email": "boss@demo.hk", "password": "bad"})
        assert r.status_code == 401
    assert "等" in r.json()["msg"]  # 第 5 次输错就开始提示要等
    r = client.post("/api/auth/login", json={"email": "boss@demo.hk", "password": "boss123"})
    assert r.status_code == 429 and r.json()["code"] == "LOGIN_THROTTLED"
    assert r.json()["retry_after"] >= 1 and r.headers["Retry-After"]
    # 别的账号不受影响
    ok = client.post("/api/auth/login", json={"email": "staff@demo.hk", "password": "staff123"})
    assert ok.status_code == 200


def test_login_success_resets_counter():
    for _ in range(3):
        client.post("/api/auth/login", json={"email": "boss@demo.hk", "password": "bad"})
    assert client.post("/api/auth/login", json={"email": "boss@demo.hk",
                                                "password": "boss123"}).status_code == 200
    for _ in range(4):
        assert client.post("/api/auth/login", json={"email": "boss@demo.hk",
                                                    "password": "bad"}).status_code == 401


def test_logout_revokes_token_and_clears_cookie(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    c = TestClient(app)
    login = c.post("/api/auth/login", json={"email": "boss@demo.hk", "password": "boss123"})
    token = login.json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    assert c.get("/api/receipts", headers=h).status_code == 200
    out = c.post("/api/auth/logout", headers=h)
    assert out.status_code == 200
    assert "receipt_session" in out.headers.get("set-cookie", "")
    assert c.get("/api/receipts", headers=h).status_code == 401
    assert auth.parse_token(token) is None


def test_revocation_survives_restart_via_store(monkeypatch):
    token = auth.issue_token("boss@demo.hk", "owner")
    full = auth.parse_token_full(token)
    auth.revoke_jti(full["jti"], full["exp"])
    auth._REVOKED.clear()
    assert auth.parse_token(token) is not None  # 内存清空 = 模拟重启
    auth.load_revoked_from_store()
    assert auth.parse_token(token) is None


def test_cookie_session_and_csrf_protection(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    c = TestClient(app)
    login = c.post("/api/auth/login", json={"email": "boss@demo.hk", "password": "boss123"})
    sc = login.headers["set-cookie"]
    assert "HttpOnly" in sc and "SameSite=lax" in sc.replace("Lax", "lax")
    assert c.cookies.get("receipt_session")
    # 只带 Cookie 的读请求可用
    assert c.get("/api/receipts").status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["identity_source"] == "cookie" and me["account"]["role"] == "owner"
    # Cookie 会话的写请求缺少自定义头 -> CSRF 拦截
    bad = c.post("/api/save_edited", json={"supplier_name": "x"})
    assert bad.status_code == 403 and bad.json()["code"] == "CSRF_REJECTED"
    ok = c.post("/api/save_edited", json={"supplier_name": "x", "settlement_type": "cash",
                                          "total_amount": 0, "items": []},
                headers={"X-Requested-With": "fetch"})
    assert ok.status_code != 403
    # Bearer 方式不受 CSRF 影响（向后兼容旧前端）
    token = login.json()["token"]
    c2 = TestClient(app)
    r = c2.post("/api/save_edited", headers={"Authorization": f"Bearer {token}"},
                json={"supplier_name": "x", "settlement_type": "cash", "total_amount": 0,
                      "items": []})
    assert r.status_code != 403
    # 登出后 Cookie 里的令牌作废
    c.post("/api/auth/logout", headers={"X-Requested-With": "fetch"})
    c3 = TestClient(app)
    c3.cookies.set("receipt_session", token)
    assert c3.get("/api/receipts").status_code == 401


def test_demo_x_email_header_cannot_forge_audit_operator():
    r = client.post("/api/save_edited", headers={**STAFF, "X-Email": "boss@evil.com"}, json={
        "supplier_name": "供应商", "date": "2026-10-01", "total_amount": 10.0,
        "settlement_type": "cash",
        "items": [{"name": "x", "quantity": 1, "unit": "个", "unit_price": 10, "amount": 10}]})
    rid = r.json()["receipt_id"]
    logs = json.loads(db.get_receipt_row(rid, tenant_id="default").audit_logs_json)
    assert all(e["who"] == "staff@demo.hk" for e in logs)
    me = client.get("/api/auth/me", headers={**STAFF, "X-Email": "boss@evil.com"}).json()
    assert me["account"]["email"] == "staff@demo.hk" and me["demo_identity"] is True


# ---- S1-P1-14 启动安全 -----------------------------------------------------
def test_bind_host_defaults_to_loopback_and_refuses_open_demo_mode():
    assert resolve_bind_host({}) == "127.0.0.1"
    assert resolve_bind_host({"APP_HOST": "localhost"}) == "127.0.0.1"
    with pytest.raises(SystemExit) as e:
        resolve_bind_host({"APP_HOST": "0.0.0.0", "AUTH_ENABLED": "0"})
    assert "AUTH_ENABLED" in str(e.value)
    with pytest.raises(SystemExit):
        resolve_bind_host({"APP_HOST": "192.168.1.8"})
    assert resolve_bind_host({"APP_HOST": "0.0.0.0", "AUTH_ENABLED": "1"}) == "0.0.0.0"


def test_python_m_app_main_does_not_reload_or_bind_all(monkeypatch):
    import app.main as m
    import uvicorn
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: seen.update(kw))
    monkeypatch.setattr(sys, "argv", ["app.main"])
    monkeypatch.delenv("APP_HOST", raising=False)
    monkeypatch.delenv("APP_RELOAD", raising=False)
    m.main()
    assert seen["host"] == "127.0.0.1" and seen["reload"] is False


# ---- S1-P3-03 / S1-P2-07 健康、WAL、备份 ------------------------------------
def test_health_anonymous_minimal_admin_detailed():
    anon = client.get("/api/health").json()
    assert set(anon) == {"status"} and anon["status"] in ("ok", "degraded")
    detail = client.get("/api/health", headers=ADMIN).json()
    assert {"db", "wal", "engine", "backup", "auth_enabled"} <= set(detail)
    assert client.get("/api/health", headers=OWNER).json().keys() == {"status"}


def test_health_does_not_run_integrity_check_per_request(monkeypatch):
    calls = []
    real = maintenance.run_integrity_check
    monkeypatch.setattr(maintenance, "run_integrity_check", lambda: calls.append(1) or real())
    for _ in range(3):
        client.get("/api/health")
        client.get("/api/health", headers=ADMIN)
    assert calls == []
    maintenance.run_integrity_check()
    snap = maintenance.snapshot()["integrity"]
    assert snap["ok"] is True and snap["checked_at"]
    assert client.get("/api/health", headers=ADMIN).json()["db"]["integrity_checked_at"] == snap["checked_at"]


def test_health_engine_status_only_looks_at_booleans(monkeypatch):
    from app import llm
    monkeypatch.setattr(llm, "_is_valid_dashscope_config", lambda b, k: False)
    cfg = db.get_engine_config()
    cfg.openai_rec_base_url = ""
    cfg.openai_rec_api_key = ""
    db.set_engine_config(cfg)
    d = client.get("/api/health", headers=ADMIN).json()
    assert d["engine"] == {"openai": False, "openai_valid_qwen": False}
    assert d["status"] == "degraded" and any("engine" in p for p in d["problems"])


def test_sqlite_uses_wal_and_busy_timeout():
    s = db.get_session()
    try:
        from sqlalchemy import text
        assert s.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert int(s.execute(text("PRAGMA busy_timeout")).scalar()) >= 5000
    finally:
        s.close()


def test_online_backup_is_restorable_and_keeps_n_copies(tmp_path):
    db.create_receipt(supplier_name="备份验证供应商", status="edited", tenant_id="default")
    last = None
    for _ in range(4):
        last = maintenance.run_backup(dest_dir=tmp_path, keep=2)
        assert last["ok"] is True
        time.sleep(0.01)
    files = sorted(tmp_path.glob("receipt_*.db"))
    assert len(files) == 2 and last["count"] == 2
    con = sqlite3.connect(str(files[-1]))
    try:
        names = [r[0] for r in con.execute("SELECT supplier_name FROM receipts")]
        assert "备份验证供应商" in names
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


def test_health_reports_backup_failure_honestly(monkeypatch, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    res = maintenance.run_backup(dest_dir=blocker / "sub")
    assert res["ok"] is False
    d = client.get("/api/health", headers=ADMIN).json()
    assert d["status"] == "degraded" and d["backup"]["dir_writable"] is False
    assert "备份" in d["backup"]["error"]
    maintenance.run_backup(dest_dir=tmp_path / "ok")
    assert client.get("/api/health", headers=ADMIN).json()["backup"]["dir_writable"] is True


def test_parsing_sweep_runs_on_a_timer_not_only_at_startup(monkeypatch):
    names = [t[0] for t in maintenance._tasks()]
    assert "parsing_sweep" in names and "job_purge" in names and "backup" in names
    rid = db.create_receipt(status="parsing", tenant_id="default")
    s = db.get_session()
    try:
        row = s.get(db._ReceiptRow, rid)
        row.created_at = "2000-01-01T00:00:00"
        row.updated_at = "2000-01-01T00:00:00"
        s.commit()
    finally:
        s.close()
    assert rid in maintenance.sweep_parsing()
    assert db.get_receipt_row(rid, tenant_id="default").status == "error"


# ---- S2-06 / S1-P2-06 上传不阻塞事件循环 ------------------------------------
def test_upload_handlers_are_sync_defs_and_no_sleep_polling():
    from app import api_receipts
    assert not inspect.iscoroutinefunction(api_receipts.upload_receipt)
    assert not inspect.iscoroutinefunction(api_receipts.upload_batch)
    src = inspect.getsource(api_receipts.upload_receipt)
    assert "time.sleep" not in src and "range(600)" not in src


# ---- S1-P1-07 / S2-22 Job ---------------------------------------------------
def test_job_is_tenant_scoped_full_uuid_and_ttl_purged():
    from app.services import receipt_utils as ru
    jid = "a" * 32
    with ru.JOBS_LOCK:
        ru.JOBS[jid] = {"job_id": jid, "job_status": "running", "receipt_id": 1,
                        "tenant_id": "tenant_a", "started_ts": time.time()}
    mine = client.get(f"/api/job/{jid}", headers={**STAFF, "X-Tenant-Id": "tenant_a"})
    assert mine.status_code == 200 and "tenant_id" not in mine.json()
    other = client.get(f"/api/job/{jid}", headers={**STAFF, "X-Tenant-Id": "tenant_b"})
    assert other.status_code == 404 and other.json()["code"] == "JOB_NOT_FOUND"
    # 完整 uuid
    import uuid as _uuid
    src = inspect.getsource(ru.start_recognition_job)
    assert "uuid.uuid4().hex" in src and "db.new_id()" not in src
    assert len(_uuid.uuid4().hex) == 32
    # TTL：运行中不动；结束后满 TTL 才淘汰
    with ru.JOBS_LOCK:
        ru.JOBS[jid]["job_status"] = "done"
    now = time.time()
    assert ru.purge_expired_jobs(now=now) == 0           # 第一次只记 finished_ts
    assert ru.purge_expired_jobs(now=now + 60) == 0
    assert ru.purge_expired_jobs(now=now + ru.JOB_TTL_SECONDS + 1) == 1
    assert ru.get_job(jid) is None


# ---- S4-04 / S4-07 / S4-10 / S4-15 / S4-22 静态资源与响应头 -----------------
def test_security_headers_on_every_response():
    for resp in (client.get("/"), client.get("/api/health"), client.get("/api/nope", headers=OWNER)):
        h = resp.headers
        assert h["x-frame-options"] == "DENY"
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"]
        assert "camera=(self)" in h["permissions-policy"]
        csp = h["content-security-policy"]
        report_only = h["content-security-policy-report-only"]
        assert csp == report_only
        assert "script-src 'self'" in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert "frame-ancestors 'none'" in csp and "/api/csp-report" in csp


def test_csp_report_endpoint_collects_without_auth_and_admin_can_list():
    web_assets._csp_counter.clear()
    r = client.post("/api/csp-report", content=json.dumps({"csp-report": {
        "effective-directive": "script-src-attr", "blocked-uri": "inline"}}),
        headers={"Content-Type": "application/csp-report"})
    assert r.status_code == 204
    assert client.get("/api/admin/csp-violations", headers=OWNER).status_code == 403
    items = client.get("/api/admin/csp-violations", headers=ADMIN).json()["data"]
    assert any("script-src-attr" in i["violation"] for i in items)


def test_gzip_enabled_for_large_responses():
    r = client.get("/static/js/main.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    r2 = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
    assert r2.headers.get("content-encoding") != "gzip"  # 小响应不压缩


def test_index_injects_content_hash_versions_and_no_hand_written_versions():
    html = client.get("/").text
    for rel in re.findall(r'(?:src|href)="/static/([^"?]+\.(?:js|css))\?v=([0-9a-f]{10})"', html):
        path, ver = rel
        assert ver == web_assets.file_version(__import__("pathlib").Path(DEMO_DIR, "static", path))
    assert "?v=2026" not in html and "_combobox_v" not in html
    assert re.search(r'/static/js/main\.js\?v=[0-9a-f]{10}', html)
    ev = client.get("/evalset/workbench/s1").text
    assert re.search(r'/static/js/eval_workbench\.js\?v=[0-9a-f]{10}', ev)


def test_asset_version_changes_with_content(tmp_path):
    static = tmp_path / "static"
    (static / "js").mkdir(parents=True)
    f = static / "js" / "a.js"
    f.write_text("var a=1;")
    page = '<script src="/static/js/a.js?v=old"></script>'
    v1 = web_assets.inject_asset_versions(page, static)
    f.write_text("var a=2;")
    os.utime(f, ns=(1, 2))  # 粗粒度时钟下 mtime 可能相同，显式错开
    v2 = web_assets.inject_asset_versions(page, static)
    assert v1 != v2 and "?v=old" not in v1
    assert web_assets.inject_asset_versions('<script src="/static/js/none.js?v=1">', static).endswith('none.js?v=1">')


def test_static_cache_policy_prod_versus_dev(tmp_path):
    from fastapi import FastAPI
    static = tmp_path / "s"
    static.mkdir()
    (static / "x.js").write_text("console.log(1)")
    prod = FastAPI()
    prod.mount("/static", web_assets.AssetStaticFiles(directory=str(static), dev_mode=False))
    c = TestClient(prod)
    r = c.get("/static/x.js?v=abc")
    assert "immutable" in r.headers["cache-control"] and "max-age=31536000" in r.headers["cache-control"]
    assert c.get("/static/x.js").headers["cache-control"] == "no-cache"
    dev = FastAPI()
    dev.mount("/static", web_assets.AssetStaticFiles(directory=str(static), dev_mode=True))
    d = TestClient(dev).get("/static/x.js?v=abc")
    assert "no-store" in d.headers["cache-control"] and d.headers.get("etag")
    cond = TestClient(dev).get("/static/x.js", headers={"If-None-Match": d.headers["etag"]})
    assert cond.status_code == 304


def test_internal_pages_and_big_unused_logos_gone_from_static():
    assert client.get("/static/test_strict.html").status_code == 404
    assert client.get("/static/js/app.js").status_code == 404
    assert os.path.exists(os.path.join(DEMO_DIR, "tests", "fixtures", "test_strict.html"))
    images = os.listdir(os.path.join(DEMO_DIR, "static", "images"))
    for name in ("logo_box_transparent.png", "logo_full.png", "logo_icon.png",
                 "logo_icon_transparent.png"):
        assert name not in images


# ---- S2-30 密钥加密存储 -----------------------------------------------------
def _raw_engine_row():
    s = db.get_session()
    try:
        return s.get(db._AppSettingRow, "engine_config").value
    finally:
        s.close()


def test_api_keys_encrypted_at_rest_decrypted_on_read_masked_in_response():
    cfg = db.get_engine_config()
    cfg.openai_rec_api_key = "sk-plain-1234567890abcdefXYZ"
    cfg.openai_aud_api_key = "sk-aud-1234567890abcdefQRS"
    db.set_engine_config(cfg, source="manual")
    raw = _raw_engine_row()
    assert "sk-plain-1234567890abcdefXYZ" not in raw and "sk-aud-1234567890abcdefQRS" not in raw
    stored = json.loads(raw)
    assert stored["openai_rec_api_key"].startswith("enc:v1:")
    assert db.get_engine_config().openai_rec_api_key == "sk-plain-1234567890abcdefXYZ"
    body = client.get("/api/admin/engine-config", headers=ADMIN).text
    assert "sk-plain-1234567890abcdefXYZ" not in body and "enc:v1" not in body


def test_plaintext_rows_are_migrated_at_startup():
    s = db.get_session()
    try:
        row = s.get(db._AppSettingRow, "engine_config")
        if row is None:
            row = db._AppSettingRow(key="engine_config")
            s.add(row)
        d = db.get_engine_config().model_dump()
        d["openai_rec_api_key"] = "sk-legacy-plain-0987654321"
        row.value = json.dumps(d)
        s.commit()
    finally:
        s.close()
    assert "sk-legacy-plain-0987654321" in _raw_engine_row()
    assert secret_store.migrate_plaintext_keys() >= 1
    assert "sk-legacy-plain-0987654321" not in _raw_engine_row()
    assert db.get_engine_config().openai_rec_api_key == "sk-legacy-plain-0987654321"
    assert secret_store.migrate_plaintext_keys() == 0


def test_missing_app_secret_key_generates_one_and_warns(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    monkeypatch.setattr(secret_store, "ENV_FILE", tmp_path / ".env")
    secret_store._fernet_cache.clear()
    with caplog.at_level("WARNING", logger="app.secret_store"):
        token = secret_store.encrypt("sk-x")
    assert token.startswith("enc:v1:")
    assert os.environ["APP_SECRET_KEY"] in (tmp_path / ".env").read_text()
    assert any("APP_SECRET_KEY" in r.message for r in caplog.records)
    assert secret_store.decrypt(token) == "sk-x"
    monkeypatch.setenv("APP_SECRET_KEY", "another-key")
    assert secret_store.decrypt(token) == ""  # 密钥不对 -> 按未配置处理，不抛错
    secret_store._fernet_cache.clear()


# ---- S1-P2-11 迁移 -----------------------------------------------------------
def test_schema_version_table_and_ordered_migrations():
    assert db.schema_version() == db.SCHEMA_MIGRATIONS[-1][0]
    versions = [v for v, _n, _f in db.SCHEMA_MIGRATIONS]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)
    db.ensure_schema_current()  # 幂等


def test_migration_failure_aborts_instead_of_warning(monkeypatch):
    def broken(engine):
        raise db.MigrationError("boom")
    monkeypatch.setattr(db, "SCHEMA_MIGRATIONS", db.SCHEMA_MIGRATIONS + [(99, "broken", broken)])
    with pytest.raises(db.MigrationError):
        db.ensure_schema_current()
    assert db.schema_version() < 99
    with pytest.raises(db.MigrationError):
        db._apply_migration_ddl(db._engine, "ALTER TABLE no_such_table ADD COLUMN x INTEGER")


def test_startup_runs_migrations_and_does_not_merge_skus_automatically():
    # 两个流水号变体的同名货品：启动巡检只读，不合并
    s = db.get_session()
    try:
        a = db._SkuRow(name="菜心", base_unit="斤", tenant_id="default", active=1, current_stock=1.0)
        b = db._SkuRow(name="菜心_1787140411", base_unit="斤", tenant_id="default", active=1,
                       current_stock=2.0)
        s.add_all([a, b])
        s.commit()
        ids = (a.id, b.id)
    finally:
        s.close()
    with TestClient(app) as c:   # 触发 startup 事件
        c.get("/api/health")
    s = db.get_session()
    try:
        assert all(s.get(db._SkuRow, i).active == 1 for i in ids)
    finally:
        s.close()
    groups = client.get("/api/inventory/sku-duplicates", headers=OWNER).json()["data"]
    assert len(groups) == 1 and groups[0]["canonical"] == "菜心"
    assert client.post("/api/inventory/sku-duplicates/merge", headers=STAFF).status_code == 403
    merged = client.post("/api/inventory/sku-duplicates/merge", headers=OWNER).json()
    assert merged["merged_groups"] == 1
    s = db.get_session()
    try:
        rows = [s.get(db._SkuRow, i) for i in ids]
        live = [r for r in rows if r is not None and r.active == 1]
        assert len(live) == 1 and abs(live[0].current_stock - 3.0) < 1e-9
        assert live[0].name == "菜心"
    finally:
        s.close()


# ---- S1-P2-08 租户兜底 -------------------------------------------------------
def test_dead_strict_tenant_hook_removed():
    assert not hasattr(db, "TenantBoundaryViolationError")
    assert not hasattr(db, "_strict_tenant_ctx")
    assert "TenantBoundaryViolationError" not in inspect.getsource(auth)
    assert "strict" not in inspect.signature(db.tenant_context).parameters


# ---- S1-P2-09 分页 -----------------------------------------------------------
def _mk(supplier, status="edited", date="2026-10-01"):
    rid = db.create_receipt(supplier_name=supplier, status=status, tenant_id="default")
    db.update_receipt(rid, receipt_date=date, total_amount=1.0)
    return rid


def test_receipts_list_backward_compatible_and_paginated():
    ids = [_mk(f"分页供应商{i}") for i in range(5)]
    _mk("别家鱼档", status="approved", date="2026-09-30")
    full = client.get("/api/receipts", headers=STAFF).json()
    assert full["total"] == len(full["data"]) == 6 and "limit" not in full
    page = client.get("/api/receipts?limit=2&offset=2", headers=STAFF).json()
    assert [r["id"] for r in page["data"]] == [r["id"] for r in full["data"][2:4]]
    assert page["total"] == 6 and page["limit"] == 2 and page["offset"] == 2
    st = client.get("/api/receipts?status=approved", headers=STAFF).json()
    assert st["total"] == 1 and st["data"][0]["supplier_name"] == "别家鱼档"
    q = client.get("/api/receipts?q=分页供应商3", headers=STAFF).json()
    assert q["total"] == 1
    byid = client.get(f"/api/receipts?q=%23{ids[0]}", headers=STAFF).json()
    assert byid["total"] == 1 and byid["data"][0]["id"] == ids[0]
    assert client.get("/api/receipts?limit=0", headers=STAFF).status_code == 422
    assert client.get("/api/receipts?offset=-1", headers=STAFF).status_code == 422
    like = client.get("/api/receipts?q=%25", headers=STAFF).json()
    assert like["total"] == 0  # % 不当通配符


# ---- S1-P2-10 金额精度 --------------------------------------------------------
def test_money_helpers_round_half_up_to_cent():
    assert money.money(0.1 + 0.2) == 0.3
    assert money.money(2.675) == 2.68
    assert money.money("1.005") == 1.01
    assert money.money(None) == 0.0 and money.money(float("nan")) == 0.0
    assert money.money_sum([0.1, 0.2, 0.3]) == 0.6
    assert money.qty(0.1 + 0.2) == 0.3
    assert repr(money.money(0.1 + 0.2)) == "0.3"


def test_db_layer_rounds_amounts_and_stock_without_float_tail():
    rid = db.create_receipt(supplier_name="精度", status="edited", tenant_id="default")
    db.update_receipt(rid, total_amount=0.1 + 0.2, service_fee=1.005, discount_amount=2.675)
    row = db.get_receipt_row(rid, tenant_id="default")
    assert (row.total_amount, row.service_fee, row.discount_amount) == (0.3, 1.01, 2.68)
    db.set_receipt_items(rid, [{"name": "x", "quantity": 0.1 + 0.2, "unit": "个",
                                "unit_price": 3.0, "amount": 0.1 + 0.2}], tenant_id="default")
    it = db.get_receipt_items(rid, tenant_id="default")[0]
    assert it["amount"] == 0.3 and it["quantity"] == 0.3
    sku_id, _ = db.create_sku("精度货", base_unit="个", tenant_id="default") \
        if isinstance(db.create_sku("精度货2", base_unit="个", tenant_id="default"), tuple) else (None, None)
    s = db.get_session()
    try:
        sku = db._SkuRow(name="精度SKU", base_unit="个", tenant_id="default", active=1,
                         current_stock=0.0)
        s.add(sku)
        s.commit()
        sid = sku.id
    finally:
        s.close()
    for _ in range(3):
        db.apply_stock_log(sid, "精度SKU", 0.1, "个", 0.1, "v", "2026-10-01", rid, "in",
                           tenant_id="default")
    s = db.get_session()
    try:
        stock = s.get(db._SkuRow, sid).current_stock
    finally:
        s.close()
    assert stock == 0.3 and repr(stock) == "0.3"


# ---- S3-25 解析成功率口径 -----------------------------------------------------
def test_parse_success_rate_counts_only_ai_parsed_and_gate_passed():
    from app.services import parsing_logs
    parsing_logs._SUMMARY_CACHE.clear()
    manual = [db.create_receipt(supplier_name=f"手工{i}", status="edited", tenant_id="s325")
              for i in range(3)]
    ai_ok = db.create_receipt(supplier_name="AI好", status="parsed", tenant_id="s325")
    ai_warn = db.create_receipt(supplier_name="AI警告", status="parsed_with_warnings", tenant_id="s325")
    ai_err = db.create_receipt(supplier_name="AI败", status="error", tenant_id="s325")
    ai_then_edited = db.create_receipt(supplier_name="AI后店员改", status="edited", tenant_id="s325")
    for rid in (ai_ok, ai_warn, ai_err, ai_then_edited):
        db.log_user_event(event_type="ocr_parse_started", receipt_id=rid, tenant_id="s325")
    db.log_user_event(event_type="ocr_parsed", receipt_id=ai_ok,
                      properties={"parse_status": "parsed"}, tenant_id="s325")
    db.log_user_event(event_type="ocr_parsed", receipt_id=ai_warn,
                      properties={"parse_status": "parsed_with_warnings"}, tenant_id="s325")
    db.log_user_event(event_type="ocr_parsed", receipt_id=ai_then_edited,
                      properties={"parse_status": "parsed"}, tenant_id="s325")
    db.log_user_event(event_type="ocr_error", receipt_id=ai_err, tenant_id="s325")
    r = client.get("/api/analytics/parsing-logs?tenant_id=s325", headers=ADMIN)
    summary = r.json()["data"]["summary"]
    assert summary["ai_parse_total"] == 4
    assert summary["success_count"] == 2          # ai_ok + 已被店员改过但 AI 当时解析成功的那张
    assert summary["success_rate"] == 0.5
    assert summary["total_parses"] == 7           # 全部单据数不变，只是不再拿来当分母
    parsing_logs._SUMMARY_CACHE.clear()
    s = db.get_session()
    try:
        trend = parsing_logs.compute_kpi_trends(s, "success_rate", days=7, effective_tenant="s325")
    finally:
        s.close()
    assert sum(p["denominator"] for p in trend["series"]) == 4   # 3 张手工单不进分母
    assert sum(p["numerator"] for p in trend["series"]) == 2
    assert manual


# ---- 补审：api_memory / api_evalset（B1-1 ~ B1-3） -------------------------------
def test_b1_1_pending_memory_concurrent_approve_ingests_once(monkeypatch):
    import concurrent.futures as cf
    from app.services import rag
    calls = []
    real = rag.approve_pending_to_memory

    def slow(*a, **kw):
        calls.append(1)
        time.sleep(0.05)
        return None

    monkeypatch.setattr(rag, "approve_pending_to_memory", slow)
    pid, _new = db.enqueue_pending_memory("并发供应商", "单位习惯：一箱=12瓶", tenant_id="default")

    def go(_):
        return client.post(f"/api/memory/pending/{pid}/approve", headers=ADMIN).status_code

    with cf.ThreadPoolExecutor(6) as ex:
        codes = list(ex.map(go, range(6)))
    assert codes.count(200) == 1 and codes.count(409) == 5
    assert len(calls) == 1, "同一条待审记忆只能写入记忆库一次"


def test_b1_2_memory_errors_are_plain_language(monkeypatch):
    from app.services import rag
    pid, _new = db.enqueue_pending_memory("报错供应商", "内容", tenant_id="default")

    def boom(*a, **kw):
        raise RuntimeError("chroma: segment lock Errno 11 /var/lib/x")

    monkeypatch.setattr(rag, "approve_pending_to_memory", boom)
    r = client.post(f"/api/memory/pending/{pid}/approve", headers=ADMIN)
    assert r.status_code == 500 and r.json()["code"] == "MEMORY_SAVE_FAILED"
    assert "chroma" not in r.text and "Errno" not in r.text
    assert db.get_pending_memory(pid, tenant_id="default")["status"] == "pending"
    monkeypatch.setattr(rag, "approve_pending_to_memory", lambda *a, **k: None)
    assert client.post(f"/api/memory/pending/{pid}/approve", headers=ADMIN).status_code == 200
    again = client.post(f"/api/memory/pending/{pid}/reject", headers=ADMIN)
    assert again.status_code == 409 and "已通过" in again.json()["msg"]
    assert "approved" not in again.json()["msg"]


def test_b1_3_evalset_manifest_write_is_atomic_and_writes_are_serialized(tmp_path, monkeypatch):
    from app import api_evalset
    d = str(tmp_path)
    rows = [{"sample_id": "s001", "image": "receipts/s001.png", "split": "val", "gt_status": "draft"}]
    api_evalset._save_manifest(d, rows)
    before = open(api_evalset._manifest_path(d), encoding="utf-8").read()
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("断电")))
    with pytest.raises(OSError):
        api_evalset._save_manifest(d, rows + [{"sample_id": "s002", "split": "test"}])
    assert open(api_evalset._manifest_path(d), encoding="utf-8").read() == before
    monkeypatch.undo()
    # 写操作端点被同一把锁串行化
    import threading
    active, peak = [0], [0]

    @api_evalset._serialized
    def critical():
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        time.sleep(0.03)
        active[0] -= 1

    ts = [threading.Thread(target=critical) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] == 1
    for fn in (api_evalset.confirm_sample, api_evalset.promote_candidate, api_evalset.reject_candidate):
        assert hasattr(fn, "__wrapped__"), f"{fn.__name__} 必须经 _serialized 串行化"


# ---- S1-P3-02 弃用 CLI 引擎兼容代码已删除，存量行经迁移改写 -----------------------
def test_legacy_cli_engine_compat_code_is_gone_and_rows_migrate_correctly():
    from app import models
    for name in ("is_legacy_cli_engine", "is_legacy_cli_model", "LEGACY_CLI_ENGINE_VALUES"):
        assert not hasattr(models, name)
    assert not hasattr(db, "_normalize_legacy_cli_leg")
    assert not hasattr(db, "_normalize_legacy_cli_engines")
    for rel in ("app/models.py", "app/api_admin.py"):
        src = open(os.path.join(DEMO_DIR, rel), encoding="utf-8").read().lower()
        assert "opencode" not in src and "codebuddy" not in src, rel
    models_src = open(os.path.join(DEMO_DIR, "app/models.py"), encoding="utf-8").read()
    assert "LEGACY_CLI" not in models_src and "is_legacy_cli" not in models_src
    # 存量行：引擎值、模型名、transport 键、回滚快照、历史快照、审计值
    legacy = db.get_engine_config().model_dump()
    legacy.update({"recognition_engine": "opencode", "recognition_model": "opencode/mimo-v2.5-free",
                   "audit_engine": "EngineKind.CODEBUDDY", "audit_model": "minimax-m3-pay",
                   "grey_parse_llm_engine": "codebuddy", "grey_parse_llm_model": "",
                   "recognition_transport": "cli",
                   "rollback_snapshot": {"prev": {"parse_llm_engine": "opencode",
                                                  "parse_llm_model": "codebuddy/x"}}})
    s = db.get_session()
    try:
        row = s.get(db._AppSettingRow, "engine_config") or db._AppSettingRow(key="engine_config")
        s.add(row)
        row.value = json.dumps(legacy)
        audit = s.get(db._AppSettingRow, "system_audit_json") or db._AppSettingRow(key="system_audit_json")
        s.add(audit)
        audit.value = json.dumps([{"action": "promote_grey_config", "old": "opencode", "new": "openai"}])
        s.commit()
    finally:
        s.close()
    db._migrate_v5_drop_legacy_cli_engines(db._engine)
    cfg = db.get_engine_config()
    assert cfg.recognition_engine == "openai" and cfg.recognition_model == "qwen3.5-omni-flash"
    assert cfg.audit_engine == "openai" and cfg.audit_model == "zai-org/GLM-4.5V"
    assert cfg.grey_parse_llm_engine == "openai" and cfg.grey_parse_llm_model == "Qwen/Qwen3-VL-32B-Instruct"
    raw = _raw_engine_row().lower()
    assert "opencode" not in raw and "codebuddy" not in raw and "_transport" not in raw
    assert json.loads(_raw_engine_row())["rollback_snapshot"]["prev"]["parse_llm_engine"] == "openai"
    s = db.get_session()
    try:
        assert "opencode" not in s.get(db._AppSettingRow, "system_audit_json").value
    finally:
        s.close()
    mapped = __import__("app.api_admin", fromlist=["x"])._normalize_audit_model_name("opencode")
    assert mapped == "opencode"  # 不再有特殊映射：旧值只会存在于迁移前的库里


# ---- S1-P2-12 依赖声明完整、带版本上限、有锁文件 -----------------------------------
def test_requirements_declare_every_direct_import_with_upper_bounds():
    import ast
    import re as _re
    root = os.path.abspath(os.path.join(DEMO_DIR, ".."))
    reqs = open(os.path.join(DEMO_DIR, "requirements.txt"), encoding="utf-8").read()
    declared = {}
    for line in reqs.splitlines():
        line = line.split("#")[0].strip()
        if line:
            m = _re.match(r"^([A-Za-z0-9_.-]+)\s*([<>=!~].*)?$", line)
            declared[m.group(1).lower().replace("_", "-")] = m.group(2) or ""
    for name, spec in declared.items():
        assert "<" in spec, f"{name} 没有版本上限: {spec!r}"
    mapping = {"PIL": "pillow", "yaml": "pyyaml", "dotenv": "python-dotenv",
               "cv2": "opencv-python-headless", "pillow_heif": "pillow-heif",
               "langchain_core": "langchain-core", "langchain_community": "langchain-community",
               "langchain_dashscope": "langchain-dashscope"}
    imported = set()
    for base in (os.path.join(DEMO_DIR, "app"), os.path.join(root, "ai_registry")):
        for dp, _dn, fns in os.walk(base):
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                tree = ast.parse(open(os.path.join(dp, fn), encoding="utf-8").read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(a.name.split(".")[0] for a in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        imported.add(node.module.split(".")[0])
    third_party = {m for m in imported
                   if m not in sys.stdlib_module_names and m not in ("app", "ai_registry")
                   and not m.startswith("_")}
    missing = sorted(m for m in third_party
                     if mapping.get(m, m).lower().replace("_", "-") not in declared)
    assert missing == [], f"这些直接 import 的包没有写进 requirements.txt: {missing}"
    for extra in ("pandas", "openpyxl", "opencv-python-headless", "numpy", "httpx", "cryptography", "dashscope"):
        assert extra in declared, extra
    lock = open(os.path.join(DEMO_DIR, "requirements.lock"), encoding="utf-8").read()
    for name in declared:
        assert _re.search(rf"^{_re.escape(name)}==", lock.lower().replace("_", "-"), _re.M), \
            f"requirements.lock 缺少 {name} 的精确版本"
    dev = open(os.path.join(DEMO_DIR, "requirements-dev.txt"), encoding="utf-8").read().lower()
    assert "beautifulsoup4" in dev and "segno" in dev and "pytest" in dev


def test_demo_script_has_no_hardcoded_interpreter_and_binds_loopback():
    sh = open(os.path.join(DEMO_DIR, "demo.sh"), encoding="utf-8").read()
    assert ".pyenv" not in sh and "3.9.6" not in sh
    assert "--host 127.0.0.1" in sh and "0.0.0.0" not in sh
    readme = open(os.path.join(DEMO_DIR, "README.md"), encoding="utf-8").read()
    assert "零配置" not in readme and "手工录入" in readme
    top = open(os.path.join(DEMO_DIR, "..", "README.md"), encoding="utf-8").read()
    assert "零配置" not in top
    env = open(os.path.join(DEMO_DIR, ".env.example"), encoding="utf-8").read()
    assert "APP_SECRET_KEY" in env


def test_fastapi_143_route_iteration_helper_sees_business_routes():
    from app.routes_util import iter_routes
    paths = {getattr(r, "path", "") for r in iter_routes(app)}
    assert {"/api/receipts", "/api/save_edited", "/api/trash/receipts", "/api/health"} <= paths

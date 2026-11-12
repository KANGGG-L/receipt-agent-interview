# -*- coding: utf-8 -*-
"""U04 上传操作幂等（C-STATE：同 key 重试同 ID、同 key 异内容 409、无 key 行为不变、
同内容异 key 不自动合并）。"""

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_u04_up_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_u04.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "u04-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)
# 测试隔离：置空引擎配置环境变量（load_dotenv override=False 不会再读入 demo/.env），
# 使识别引擎恒为未配置——既避开真实外呼，也让上传走 ENGINE_NOT_CONFIGURED 早退，
# 不受真实 .env 是否配置引擎影响的模糊前置门干扰。仓内有 .env 时同样可复现。
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
           "OPENAI_REC_API_KEY", "OPENAI_REC_BASE_URL", "OPENAI_REC_MODEL",
           "OPENAI_AUD_API_KEY", "OPENAI_AUD_BASE_URL", "OPENAI_AUD_MODEL",
           "OPENAI_PARSE_API_KEY", "OPENAI_PARSE_BASE_URL", "OPENAI_PARSE_MODEL",
           "DASHSCOPE_API_KEY"):
    os.environ[_k] = ""

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db  # noqa: E402
from app.main import app  # noqa: E402

db._make_engine()
client = TestClient(app)
_ACCOUNT_EMAIL = {"staff": "staff@demo.hk"}


@pytest.fixture(autouse=True)
def _clean_ops():
    s = db.get_session()
    try:
        s.execute(text("DELETE FROM upload_operations"))
        s.commit()
    finally:
        s.close()
    yield


def _hdr():
    return {"Authorization": "Bearer " + auth.issue_token(
        _ACCOUNT_EMAIL["staff"], "staff", "default")}


def _img_bytes(seed="a"):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), "white" if seed == "a" else "black").save(
        buf, format="JPEG")
    return buf.getvalue()


def _upload(data, request_key=None):
    files = {"receipt": ("synthetic-%s.jpg" % (request_key or "n"), data, "image/jpeg")}
    form = {"treatment": "false", "async_": "false"}
    if request_key:
        form["request_key"] = request_key
    return client.post("/api/upload", headers=_hdr(), files=files, data=form)


def test_upload_retry_same_operation():
    """同 key 同内容重试 → 回放同一 receipt_id，不重复建单。"""
    data = _img_bytes("a")
    r1 = _upload(data, request_key="op-u04-1")
    assert r1.status_code == 200, r1.json()
    rid1 = r1.json().get("receipt_id")
    assert rid1
    r2 = _upload(data, request_key="op-u04-1")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2.get("code") == "UPLOAD_IDEMPOTENT_REPLAY"
    assert body2["receipt_id"] == rid1
    s = db.get_session()
    try:
        n = s.execute(text("SELECT COUNT(*) FROM upload_operations")).scalar()
        receipts = s.execute(text(
            "SELECT COUNT(*) FROM receipts WHERE id=:r"), {"r": rid1}).scalar()
        assert n == 1 and receipts == 1
    finally:
        s.close()


def test_same_key_diff_content_409():
    """同 key 异内容 → 409 IDEMPOTENCY_REUSED（不建单）。"""
    r1 = _upload(_img_bytes("a"), request_key="op-u04-2")
    assert r1.status_code == 200
    r2 = _upload(_img_bytes("b"), request_key="op-u04-2")
    assert r2.status_code == 409 and r2.json().get("code") == "IDEMPOTENCY_REUSED"


def test_no_key_behavior_unchanged():
    """无 key：两次上传同图 → 两张单（旧行为；重复候选提示归交互层，不自动合并）。"""
    data = _img_bytes("a")
    r1 = _upload(data)
    r2 = _upload(data)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["receipt_id"] != r2.json()["receipt_id"]
    s = db.get_session()
    try:
        n = s.execute(text("SELECT COUNT(*) FROM upload_operations")).scalar()
        assert n == 0  # 无 key 不登记
    finally:
        s.close()


def test_same_content_diff_key_separate_operations():
    """同内容异 key → 各自独立建单（同文件不自动合并——ADR05 重复候选归 owner 确认）。"""
    data = _img_bytes("a")
    r1 = _upload(data, request_key="op-u04-3")
    r2 = _upload(data, request_key="op-u04-4")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["receipt_id"] != r2.json()["receipt_id"]


def test_batch_upload_per_file_keys():
    """批量：request_keys 逐张对齐——同键同内容回放、异内容 error 行、无键旧行为。"""
    import io
    import random
    from PIL import Image

    _cached = {}

    def _img(tag):
        # 高纹理噪声图：通过批量路径的模糊前置拦截；同 tag 缓存字节保证
        # 重放请求与首传逐字节一致（幂等以内容 hash 复核）
        if tag not in _cached:
            rng = random.Random(hash(tag) & 0xffff or 7)
            buf = io.BytesIO()
            noise = Image.new("RGB", (640, 480))
            noise.putdata([(v, v, v) for v in
                           [rng.randint(0, 255) for _ in range(640 * 480)]])
            noise.save(buf, format="JPEG")
            _cached[tag] = buf.getvalue()
        return _cached[tag]

    files = [
        ("files", ("b1.jpg", _img("n"), "image/jpeg")),
        ("files", ("b2.jpg", _img("m"), "image/jpeg")),
    ]
    # 两张同内容白图、各自键：首传两张独立建单
    r1 = client.post("/api/upload_batch", headers=_hdr(),
                     files=files, data={"treatment": "false", "async_": "false",
                                        "request_keys": "bk-1,bk-2"})
    assert r1.status_code == 200
    body1 = r1.json()
    results1 = body1.get("results") or body1.get("data") or []
    assert len(results1) >= 2, body1
    # 重放同键同内容：两行均回放（UPLOAD_IDEMPOTENT_REPLAY）
    r2 = client.post("/api/upload_batch", headers=_hdr(),
                     files=[("files", ("b1.jpg", _img("n"), "image/jpeg")),
                            ("files", ("b2.jpg", _img("m"), "image/jpeg"))],
                     data={"treatment": "false", "async_": "false",
                           "request_keys": "bk-1,bk-2"})
    results2 = (r2.json().get("results") or [])
    codes = [x.get("code") for x in results2]
    assert codes.count("UPLOAD_IDEMPOTENT_REPLAY") == 2, codes
    # 同键异内容：该行 IDEMPOTENCY_REUSED，另一行正常
    r3 = client.post("/api/upload_batch", headers=_hdr(),
                     files=[("files", ("b1.jpg", (lambda b: b[:-4] + b"DIFF" + b[-4:])(_img("n")), "image/jpeg")),
                            ("files", ("b2.jpg", _img("m"), "image/jpeg"))],
                     data={"treatment": "false", "async_": "false",
                           "request_keys": "bk-1,bk-2"})
    results3 = (r3.json().get("results") or [])
    code3 = [x.get("code") for x in results3]
    assert "IDEMPOTENCY_REUSED" in code3, code3

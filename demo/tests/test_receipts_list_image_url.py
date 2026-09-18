# -*- coding: utf-8 -*-
"""X1 归档列表死链治理回归守卫：image_url 指向的文件不存在时必须置空。

背景：live 库有 928 条历史 pytest 污染单据，image_path 指向已被清理的临时文件
（形如 '/uploads/img_2.png'、'/uploads/eval_reflow_src_*.png' 或 pytest 临时目录的
绝对路径）。public_image_url 仍按其 basename 产出 URL，导致归档列表逐行 404 破图。
修复只做展示层兜底：文件不存在时 image_url 返回空串，字段保留、不删历史行、不改
image_path、不影响任何仍可访问的 URL。

覆盖点：
- 文件存在的行：URL 原样保留（Web 格式仍是 /uploads/<name>）
- 文件缺失的行：URL 置空（Web 与非 Web 两种格式）
- image_path 为空的行：保持空串
- 绝对路径（pytest 污染形态）按其 basename 判定
- 字段结构不变：image_url 键仍存在、其余字段不受影响

不写 live 库：沿用 isolated_db + tmp_path + monkeypatch UPLOAD_DIR 模式。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_list_image_url_test.db")
if os.path.exists("/tmp/receipt_demo_list_image_url_test.db"):
    os.remove("/tmp/receipt_demo_list_image_url_test.db")

import pytest

from app import db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """把 DB 指到临时文件，跑前跑后都不触碰 live demo 库。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_list_image_url.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def upload_dir(monkeypatch, tmp_path):
    """上传落盘目录指向临时目录；list_receipts 的存在性判定读同一模块级变量。"""
    from app import api_receipts
    d = tmp_path / "uploads"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(api_receipts, "UPLOAD_DIR", d)
    return d


def _mk_receipt(image_path, name=None, status="parsed"):
    """建一条单据；name 非空时在 uploads 下落一个同名真文件。"""
    rid = db.create_receipt(supplier_name="測試供應商", status=status,
                            tenant_id="default")
    db.update_receipt(rid, image_path=image_path)
    return rid


def _list(client):
    r = client.get("/api/receipts", headers={"X-Role": "staff"})
    assert r.status_code == 200
    return {row["id"]: row for row in r.json()["data"]}


# -------------------------------------------------------------
# 1. 文件缺失 -> 置空；文件存在 -> 原样保留
# -------------------------------------------------------------
def test_missing_file_url_is_blanked_and_existing_kept(client, upload_dir):
    ok = _mk_receipt("uploads/ok.jpg")
    (upload_dir / "ok.jpg").write_bytes(b"x")
    gone = _mk_receipt("uploads/gone.jpg")

    rows = _list(client)
    assert rows[ok]["image_url"] == "/uploads/ok.jpg"
    assert rows[gone]["image_url"] == ""


def test_missing_file_row_keeps_field_and_other_data(client, upload_dir):
    """置空只影响 image_url 一个字段：键仍在、结构不变、其余字段照常回传。"""
    rid = _mk_receipt("uploads/gone.jpg")
    row = _list(client)[rid]
    assert "image_url" in row
    assert row["image_url"] == ""
    assert row["id"] == rid
    assert row["supplier_name"] == "測試供應商"
    assert row["status"] == "parsed"


# -------------------------------------------------------------
# 2. 非 Web 格式：源文件缺失同样置空；存在则仍是转码端点 URL
# -------------------------------------------------------------
def test_non_web_missing_source_is_blanked(client, upload_dir):
    rid = _mk_receipt("uploads/gone.heic")
    assert _list(client)[rid]["image_url"] == ""


def test_non_web_existing_source_keeps_endpoint_url(client, upload_dir):
    rid = _mk_receipt("uploads/here.heic")
    (upload_dir / "here.heic").write_bytes(b"not-a-real-heic")
    url = _list(client)[rid]["image_url"]
    assert url.startswith("/api/receipt/%d/image?v=here&sig=" % rid)


# -------------------------------------------------------------
# 3. 空 image_path / pytest 污染形态
# -------------------------------------------------------------
def test_empty_image_path_stays_empty(client, upload_dir):
    rid = _mk_receipt("")
    assert _list(client)[rid]["image_url"] == ""


def test_absolute_pytest_path_judged_by_basename(client, upload_dir):
    """历史 pytest 污染的 image_path 是临时目录绝对路径，按 basename 判定。"""
    gone = _mk_receipt("/private/var/folders/xx/pytest-of-ethan/pytest-8/img_2.png")
    assert _list(client)[gone]["image_url"] == ""

    here = _mk_receipt("/private/var/folders/xx/pytest-of-ethan/pytest-8/img_1.png")
    (upload_dir / "img_1.png").write_bytes(b"x")
    assert _list(client)[here]["image_url"] == "/uploads/img_1.png"


# -------------------------------------------------------------
# 4. 纯函数边界：basename 归一与目录穿越形态
# -------------------------------------------------------------
def test_target_exists_helper_edge_cases(upload_dir):
    from app import api_receipts
    (upload_dir / "real.jpg").write_bytes(b"x")
    assert api_receipts._image_url_target_exists("uploads/real.jpg") is True
    assert api_receipts._image_url_target_exists("/abs/path/real.jpg") is True
    assert api_receipts._image_url_target_exists("uploads/nope.jpg") is False
    assert api_receipts._image_url_target_exists("") is False
    assert api_receipts._image_url_target_exists(None) is False
    assert api_receipts._image_url_target_exists("..") is False


def test_list_is_idempotent(client, upload_dir):
    """重复调用不应继续改动 URL（兜底是幂等的展示层变换）。"""
    (upload_dir / "ok.jpg").write_bytes(b"x")
    _mk_receipt("uploads/ok.jpg")
    _mk_receipt("uploads/gone.jpg")
    first = _list(client)
    second = _list(client)
    assert {i: r["image_url"] for i, r in first.items()} == \
           {i: r["image_url"] for i, r in second.items()}

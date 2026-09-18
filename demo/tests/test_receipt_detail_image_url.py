# -*- coding: utf-8 -*-
"""Y1 详情接口死链治理回归守卫：GET /api/receipt/{id} 的 image_url 目标文件不存在时置空。

背景：X1 只收口了归档列表（list_receipts）与 admin 灰测样本，详情接口仍对
「DB 有行、磁盘无文件」的 928 条历史 pytest 污染单据输出 /uploads/<name>（非 Web
格式则输出转码端点 URL），归档弹窗打开这些老单据时 404 破图/死链——前端只有空值
守卫（main.js 的 `if (archiveImageUrl)`），挡不住非空死链。

修复只做展示层兜底：文件不存在时 image_url 返回空串，字段保留、不删历史行、不改
image_path、不影响任何仍可访问的 URL，也不触碰识别/审核链路。

覆盖点：
- 文件存在：URL 逐字节不变（Web 格式仍是 /uploads/<name>）
- 文件缺失：URL 置空（Web 与非 Web 两种格式）
- 非 Web 且文件存在：签名转码 URL 原样保留
- image_path 为空：保持空串
- 绝对路径（pytest 污染形态）按 basename 判定
- 详情与列表两侧判据同源（同一单据两边结论一致）

不写 live 库：沿用 isolated_db + tmp_path + monkeypatch UPLOAD_DIR 模式。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_detail_image_url_test.db")
if os.path.exists("/tmp/receipt_demo_detail_image_url_test.db"):
    os.remove("/tmp/receipt_demo_detail_image_url_test.db")

import pytest

from app import db


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """把 DB 指到临时文件，跑前跑后都不触碰 live demo 库。"""
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_detail_image_url.db")
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
    """上传落盘目录指向临时目录；详情/列表的存在性判定读同一模块级变量。"""
    from app import api_receipts
    d = tmp_path / "uploads"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(api_receipts, "UPLOAD_DIR", d)
    return d


def _mk_receipt(image_path, status="parsed"):
    """建一条单据（name 对应的真文件由调用方在 uploads 下落盘）。"""
    rid = db.create_receipt(supplier_name="測試供應商", status=status,
                            tenant_id="default")
    db.update_receipt(rid, image_path=image_path)
    return rid


def _detail(client, rid):
    r = client.get("/api/receipt/%d" % rid, headers={"X-Role": "staff"})
    assert r.status_code == 200, r.text
    return r.json()


def _list(client):
    r = client.get("/api/receipts", headers={"X-Role": "staff"})
    assert r.status_code == 200
    return {row["id"]: row for row in r.json()["data"]}


# -------------------------------------------------------------
# 1. 文件缺失 -> 置空；文件存在 -> 原样保留
# -------------------------------------------------------------
def test_missing_file_detail_url_is_blanked_and_existing_kept(client, upload_dir):
    ok = _mk_receipt("uploads/ok.jpg")
    (upload_dir / "ok.jpg").write_bytes(b"x")
    gone = _mk_receipt("uploads/gone.jpg")

    assert _detail(client, ok)["image_url"] == "/uploads/ok.jpg"
    assert _detail(client, gone)["image_url"] == ""


def test_missing_file_detail_keeps_field_and_other_data(client, upload_dir):
    """置空只影响 image_url 一个字段：键仍在、结构不变、其余字段照常回传。"""
    rid = _mk_receipt("uploads/gone.jpg")
    body = _detail(client, rid)
    assert "image_url" in body
    assert body["image_url"] == ""
    assert body["receipt_id"] == rid
    assert body["data"]["receipt_id"] == rid
    assert body["data"]["supplier_name"] == "測試供應商"
    assert body["data"]["status"] == "parsed"


# -------------------------------------------------------------
# 2. 非 Web 格式：源文件缺失同样置空；存在则签名 URL 原样保留
# -------------------------------------------------------------
def test_non_web_missing_source_is_blanked(client, upload_dir):
    rid = _mk_receipt("uploads/gone.heic")
    assert _detail(client, rid)["image_url"] == ""


def test_non_web_existing_source_keeps_signed_endpoint_url(client, upload_dir):
    """HEIC 源文件在盘上时，详情 URL 仍是后端签发的转码端点地址。"""
    rid = _mk_receipt("uploads/here.heic")
    (upload_dir / "here.heic").write_bytes(b"not-a-real-heic")
    url = _detail(client, rid)["image_url"]
    assert url.startswith("/api/receipt/%d/image?v=here&sig=" % rid)


# -------------------------------------------------------------
# 3. 空 image_path / pytest 污染形态
# -------------------------------------------------------------
def test_empty_image_path_stays_empty(client, upload_dir):
    rid = _mk_receipt("")
    assert _detail(client, rid)["image_url"] == ""


def test_absolute_pytest_path_judged_by_basename(client, upload_dir):
    """历史 pytest 污染的 image_path 是临时目录绝对路径，按 basename 判定。"""
    gone = _mk_receipt("/private/var/folders/xx/pytest-of-ethan/pytest-8/img_2.png")
    assert _detail(client, gone)["image_url"] == ""

    here = _mk_receipt("/private/var/folders/xx/pytest-of-ethan/pytest-8/img_1.png")
    (upload_dir / "img_1.png").write_bytes(b"x")
    assert _detail(client, here)["image_url"] == "/uploads/img_1.png"


# -------------------------------------------------------------
# 4. 详情与列表判据同源：同一批单据两侧结论必须一致
# -------------------------------------------------------------
def test_detail_and_list_agree_on_reachability(client, upload_dir):
    (upload_dir / "ok.jpg").write_bytes(b"x")
    (upload_dir / "here.heic").write_bytes(b"x")
    ids = [
        _mk_receipt("uploads/ok.jpg"),
        _mk_receipt("uploads/gone.jpg"),
        _mk_receipt("uploads/here.heic"),
        _mk_receipt("uploads/gone.heic"),
        _mk_receipt(""),
    ]
    rows = _list(client)
    for rid in ids:
        assert _detail(client, rid)["image_url"] == rows[rid]["image_url"]

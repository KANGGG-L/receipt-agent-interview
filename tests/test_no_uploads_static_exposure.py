# -*- coding: utf-8 -*-
"""P0-4 未授权文件面撤销回归守卫：/uploads 静态挂载不再存在。

背景：main.py 曾把 uploads/ 目录整体挂到 /uploads（零鉴权、零租户校验），任何知道
文件名的人都能读取全部原图与付款凭证；api_finance 还把凭证以
`/uploads/vouchers/<name>` 字符串落库。本批次撤销该挂载：原图改由
GET /api/receipt/{id}/image 提供，付款凭证改由 GET /api/payment/voucher/{name} 提供，
两条端点都做 HMAC 签名 + 租户归属 + basename/realpath 收敛。

覆盖点：
1. 响应体不得残留 /uploads/ 出口：/api/receipts、/api/receipt/{id}、/api/job/{id}、
   /api/payments 的响应文本均不含 "/uploads/"。
2. /uploads/<真实存在的文件名> 直取 → 404（挂载已撤销，即便文件真在磁盘上）。
3. 凭证端点 GET /api/payment/voucher/{name}：签名错误/缺失 → 403；正确签名 → 200；
   跨租户 → 404/403；`../` 路径穿越 → 404。
4. 鉴权收紧：/api/job/{job_id} 匿名 → 401；/api/convert-image 匿名 → 401。

不写 live 库：tests/conftest.py 的 autouse 夹具把 DB 与图片落盘目录（原图 / 凭证）
重定向到本轮临时库与临时目录，本文件只读写这些临时路径；live 的
demo/receipt_demo.db 与 demo/uploads 从不触碰。
"""

import io
import os
import re
import sys

import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import api_finance, api_receipts, db
from app.main import app
from app.services import receipt_utils
from app.services.receipt_utils import voucher_image_signature


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _upload_dir():
    """本轮用例的临时原图目录（conftest 已从 live demo/uploads 重定向）。"""
    return api_receipts.UPLOAD_DIR


def _voucher_dir():
    """本轮用例的临时凭证目录（conftest 已从 live demo/uploads/vouchers 重定向）。"""
    return api_finance.VOUCHER_DIR


def _mk_receipt(image_path, data=b"x", tenant_id="default"):
    """建一条单据；image_path 非空时在其 basename 处落下同名真文件。"""
    if image_path:
        (_upload_dir() / os.path.basename(image_path)).write_bytes(data)
    rid = db.create_receipt(supplier_name="測試供應商", status="parsed",
                            tenant_id=tenant_id)
    db.update_receipt(rid, image_path=image_path)
    return rid


def _real_jpeg(color=(200, 30, 30)):
    """付款凭证现在会校验文件内容并重新编码，测试必须上传真实图片。"""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "JPEG")
    return buf.getvalue()


def _create_payment_with_voucher(client, name="v1.jpg", content=None, tenant="default"):
    """走真实端点登记一笔带凭证的付款，返回 payment_id（凭证落临时凭证目录）。"""
    content = content or _real_jpeg()
    sup = db.find_supplier_by_name("凭证供应商", tenant_id=tenant)
    sid = sup.id if sup else db.create_supplier("凭证供应商", tenant_id=tenant)[0]
    r = client.post(
        "/api/payments",
        data={"supplier_id": str(sid), "amount": "10", "method": "cash",
              "paid_at": "2026-01-01", "notes": "test"},
        files={"voucher": (name, content, "image/jpeg")},
        headers={"X-Role": "owner", "X-Tenant-Id": tenant},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _payment_rows(client, tenant="default"):
    r = client.get("/api/payments",
                   headers={"X-Role": "owner", "X-Tenant-Id": tenant})
    assert r.status_code == 200, r.text
    return r.json()["data"]


# -------------------------------------------------------------
# 1. 响应体无 /uploads/ 出口
# -------------------------------------------------------------
def test_no_uploads_url_in_api_responses(client):
    """四个入口的响应文本都不得含 "/uploads/"（原图与凭证一律签名端点出口）。"""
    web = _mk_receipt("uploads/plain.jpg", data=b"jpeg-ish")
    heic = _mk_receipt("uploads/shot.heic", data=b"fake-heic")

    # /api/receipts（列表）
    r = client.get("/api/receipts", headers={"X-Role": "staff"})
    assert r.status_code == 200, r.text
    assert "/uploads/" not in r.text
    rows = {row["id"]: row for row in r.json()["data"]}
    for rid in (web, heic):
        url = rows[rid]["image_url"]
        assert url.startswith("/api/receipt/%d/image?v=" % rid), url
        assert "sig=" in url

    # /api/receipt/{id}（详情）
    r = client.get("/api/receipt/%d" % web, headers={"X-Role": "staff"})
    assert r.status_code == 200, r.text
    assert "/uploads/" not in r.text
    assert r.json()["image_url"].startswith("/api/receipt/%d/image" % web)

    # /api/job/{job_id}（任务结果体）——直接注入一条已完成任务，get_job 会按真实单据
    # 行重新签发 image_url，故这里断言的是真实产出而非手工构造的字符串。
    job_id = "job-no-uploads-static"
    with receipt_utils.JOBS_LOCK:
        receipt_utils.JOBS[job_id] = {
            "job_id": job_id, "job_status": "done", "receipt_id": web}
    try:
        r = client.get("/api/job/%s" % job_id, headers={"X-Role": "staff"})
        assert r.status_code == 200, r.text
        assert "/uploads/" not in r.text
        assert "/api/receipt/%d/image" % web in r.text
    finally:
        with receipt_utils.JOBS_LOCK:
            receipt_utils.JOBS.pop(job_id, None)

    # /api/payments：历史遗留的库内字符串 "/uploads/vouchers/<name>" 必须被就地改写成
    # 签名 URL（免 DB 迁移，旧数据继续可用）。
    pid = db.create_payment(supplier_id=1, amount=10.0, method="cash",
                            paid_at="2026-01-01", notes="",
                            voucher_image_path="/uploads/vouchers/legacy.jpg",
                            tenant_id="default")
    r = client.get("/api/payments", headers={"X-Role": "owner"})
    assert r.status_code == 200, r.text
    assert "/uploads/" not in r.text
    legacy = [p for p in r.json()["data"] if p["id"] == pid]
    assert len(legacy) == 1
    assert legacy[0]["voucher_image_url"].startswith("/api/payment/voucher/legacy.jpg?v=")
    assert "sig=" in legacy[0]["voucher_image_url"]


# -------------------------------------------------------------
# 2. 静态挂载已撤销
# -------------------------------------------------------------
def test_uploads_static_mount_is_gone(client):
    """/uploads/<真实存在的文件名> → 404，且文件内容不得被回吐。"""
    name = "static_exposure_probe.jpg"
    (_upload_dir() / name).write_bytes(b"REAL-FILE-ON-DISK")
    voucher_name = "static_exposure_probe_voucher.jpg"
    (_voucher_dir() / voucher_name).write_bytes(b"REAL-VOUCHER-ON-DISK")
    assert (_upload_dir() / name).is_file()
    assert (_voucher_dir() / voucher_name).is_file()

    r = client.get("/uploads/" + name)
    assert r.status_code == 404, r.text
    assert b"REAL-FILE-ON-DISK" not in r.content

    r = client.get("/uploads/vouchers/" + voucher_name)
    assert r.status_code == 404, r.text
    assert b"REAL-VOUCHER-ON-DISK" not in r.content

    # 路由表里不应再有 /uploads 挂载（静态挂载已整体移除）
    from app.routes_util import iter_routes
    assert not any(getattr(rt, "path", None) == "/uploads" for rt in iter_routes(app))


# -------------------------------------------------------------
# 3. 凭证端点：签名 / 租户 / 路径穿越
# -------------------------------------------------------------
def _voucher_url_parts(url):
    """把凭证签名 URL 拆成 (name, v, sig)；形态不符直接断言失败。"""
    m = re.match(r"^/api/payment/voucher/([^?]+)\?v=([^&]+)&sig=([0-9a-f]+)"
                 r"(?:&tenant_id=([^&]+))?$", url)
    assert m, "凭证 URL 形态不符: %r" % url
    return m.group(1), m.group(2), m.group(3), m.group(4)


def test_voucher_endpoint_requires_valid_signature(client):
    """正确签名 → 200 且字节与原件一致；篡改/缺失签名 → 403。"""
    content = _real_jpeg((10, 20, 30))
    pid = _create_payment_with_voucher(client, "v1.jpg", content)
    url = [p for p in _payment_rows(client) if p["id"] == pid][0]["voucher_image_url"]
    name, v, sig, tenant_param = _voucher_url_parts(url)
    assert name == v and name.endswith(".jpg"), url
    assert tenant_param is None, "default 租户的 URL 不应带 tenant_id"
    assert sig == voucher_image_signature(name, "default")
    assert "/uploads/" not in url

    ok = client.get(url)
    assert ok.status_code == 200, ok.text
    import io
    from PIL import Image
    assert Image.open(io.BytesIO(ok.content)).size == (8, 8)   # 重新编码后的同一张图
    assert ok.headers["x-content-type-options"] == "nosniff"

    # 篡改签名任一位 → 403，绝不因「参数形态对」而放行
    bad_sig = ("0" if sig[-1] != "0" else "1")
    r = client.get(url.replace("&sig=" + sig, "&sig=" + sig[:-1] + bad_sig))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "VOUCHER_SIGNATURE_INVALID"

    # 缺签名 / 缺版本参数 → 403
    assert client.get("/api/payment/voucher/" + name).status_code == 403
    assert client.get("/api/payment/voucher/%s?v=%s" % (name, name)).status_code == 403


def test_voucher_endpoint_cross_tenant(client):
    """跨租户：签名绑定租户，换租户签名/参数一律取不到图。"""
    pid = _create_payment_with_voucher(client, "cross.jpg", _real_jpeg((1, 2, 3)), tenant="tenant_b")
    url_tb = [p for p in _payment_rows(client, "tenant_b")
              if p["id"] == pid][0]["voucher_image_url"]
    name, v, sig, tenant_param = _voucher_url_parts(url_tb)
    assert tenant_param == "tenant_b", url_tb
    assert sig == voucher_image_signature(name, "tenant_b")
    assert client.get(url_tb).status_code == 200

    # 同一凭证名、同一签名，但去掉 tenant_id → 签名失配 → 403
    r = client.get("/api/payment/voucher/%s?v=%s&sig=%s" % (name, v, sig))
    assert r.status_code == 403, r.text

    # 用 default 租户重新签发（签名自洽）→ 该租户下无此凭证归属 → 404
    default_url = api_finance.public_voucher_url(name, "default")
    assert default_url != url_tb
    r = client.get(default_url)
    assert r.status_code == 404, r.text


def test_voucher_endpoint_path_traversal_is_confined(client):
    """`../` 穿越：父目录里的真实文件不得被读到（basename + realpath 收敛）。"""
    secret = _upload_dir() / "outside_secret.jpg"
    secret.write_bytes(b"OUTSIDE-SECRET")
    # 归属判定需要一行引用该凭证名（basename 与穿越目标同名），否则会先被 404 归属挡下
    db.create_payment(supplier_id=1, amount=1.0, method="cash",
                      paid_at="2026-01-01", notes="",
                      voucher_image_path="../outside_secret.jpg", tenant_id="default")
    sig = voucher_image_signature("outside_secret.jpg", "default")

    r = client.get("/api/payment/voucher/..%2Foutside_secret.jpg"
                   "?v=outside_secret.jpg&sig=" + sig)
    assert r.status_code == 404, r.text
    assert b"OUTSIDE-SECRET" not in r.content
    # 直线对照：目标文件确实存在于父目录，但不在凭证目录内
    assert secret.is_file()
    assert not (_voucher_dir() / "outside_secret.jpg").exists()


def test_resolve_voucher_file_confines_to_voucher_dir():
    """纯函数边界：basename 归一 + realpath 收敛，任何形态都落在 VOUCHER_DIR 内。"""
    outside = _upload_dir() / "outside_helper.jpg"
    outside.write_bytes(b"x")

    resolved = api_finance._resolve_voucher_file("../outside_helper.jpg")
    assert resolved is not None
    assert resolved.name == "outside_helper.jpg"
    assert resolved.parent == _voucher_dir().resolve()
    assert resolved != outside.resolve()

    # 绝对路径注入同样被 basename 归一（不落到真实系统文件上）
    assert api_finance._resolve_voucher_file("/etc/passwd").name == "passwd"
    # 退化形态一律 None
    assert api_finance._resolve_voucher_file("..") is None
    assert api_finance._resolve_voucher_file("") is None
    assert api_finance._resolve_voucher_file(None) is None


# -------------------------------------------------------------
# 4. 鉴权收紧：/api/job/{id} 与 /api/convert-image
# -------------------------------------------------------------
def test_job_status_requires_staff(client):
    """匿名轮询 → 401（原为 200）；带 staff 身份仍可用（仅任务不存在 → 404）。"""
    assert client.get("/api/job/whatever").status_code == 401
    r = client.get("/api/job/whatever", headers={"X-Role": "staff"})
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "JOB_NOT_FOUND"


def test_convert_image_requires_staff(client):
    """匿名上传字节 → 401；带 staff 身份仍可用 → 200 JPEG。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 12), (10, 20, 30)).save(buf, format="JPEG")
    jpeg = buf.getvalue()

    r = client.post("/api/convert-image",
                    files={"file": ("a.jpg", jpeg, "image/jpeg")})
    assert r.status_code == 401, r.text

    r = client.post("/api/convert-image",
                    files={"file": ("a.jpg", jpeg, "image/jpeg")},
                    headers={"X-Role": "staff"})
    assert r.status_code == 200, r.text
    assert r.content[:3] == b"\xff\xd8\xff"

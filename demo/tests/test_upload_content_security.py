# -*- coding: utf-8 -*-
"""W-SEC-3 上传内容与资源安全：伪 .jpg / 解压炸弹 / 超大图 / 超字节上限 400 拒绝。

why：此前 `_save_upload` 把上传字节原样落盘（只看扩展名），polyglot（图片+附加
载荷拼接）、伪造成 .jpg 的任意二进制、解压炸弹（声明维度巨大）都能进入识别链路
或打爆解码内存。修复后（demo/app/api_receipts.py::_save_upload）：
  - magic bytes 校验（JPEG/PNG/WEBP/HEIC/TIFF 签名，不信扩展名）→ 400
    UNSUPPORTED_IMAGE_FORMAT；
  - 单文件 15MB 字节上限（分块读取过程中即拒）→ 400 FILE_TOO_LARGE；
  - Pillow verify() 结构校验 → 400 INVALID_IMAGE；
  - 像素上限自查（对齐 image_web.MAX_IMAGE_PIXELS 口径，超限拒绝而非告警）→
    400 IMAGE_PIXELS_EXCEEDED；
  - 校验全过后**解码重编码落盘**（消毒 polyglot：落盘的是 Pillow 亲手编码的
    干净容器，附加载荷/恶意元数据被剥离）。

三个上传入口（/api/upload、/api/upload_batch、/api/receipt/{id}/replace-image）
都必须拒绝；/api/convert-image 改为分块限量读取（不再整文件进内存）且同受
字节/像素上限约束。

配套（同计划条目）：
  - preprocess._imread：字节 + 像素双自查，超限返回 None（拒绝）而非告警放行，
    PIL 主路径与 cv2 回退路径同受保护；
  - extract_chain：删除「临时置 None 放宽像素上限」的全局竞态（其行为回归钉在
    test_image_data_url_decode_failure.py 第 4 节）。

隔离：模块级先落 DB_PATH / RAG_DIR 到 /tmp，再用 demo/conftest.py 的 autouse
夹具兜底（UPLOAD_DIR 自动重定向到 tmp_path）；只挂 api_receipts 单路由（不
import app.main），成功路径的重活（画质门/守卫/预处理/识别 Job）打桩。
"""

import io
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_upload_security.db")
os.environ.setdefault("RAG_DIR", "/tmp/receipt_demo_upload_security_rag")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import api_receipts, db
from app.services import preprocess

STAFF_HEADERS = {"X-Role": "staff"}


# -------------------------------------------------------------
# 夹具与样本构造
# -------------------------------------------------------------
def _make_client():
    """只挂 api_receipts 路由：不 import app.main，避开 startup 自愈写库/建语料。"""
    app = FastAPI()
    app.include_router(api_receipts.router)
    return TestClient(app)


def _jpeg_bytes(w=48, h=32, color=(30, 120, 200), **save_kw):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG", quality=90, **save_kw)
    return buf.getvalue()


def _png_bytes(w=48, h=32, color=(30, 200, 90)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _heic_bytes(w=48, h=32, color=(200, 30, 30)):
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pytest.skip("pillow_heif 缺失，无法构造真 HEIC 样本")
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="HEIF")
    return buf.getvalue()


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _crafted_png(width: int, height: int) -> bytes:
    """声明超大维度的 PNG：IHDR 真、IDAT 极小。

    why 可行：拒绝发生在 **header 尺寸自查**（打开后读 img.size，不分配像素），
    不会真的解码 height 行像素——这正是解压炸弹防御的意义。40000x40000 超过
    Pillow 硬限（>2×默认上限 → DecompressionBombError），13000x10000（1.3 亿
    像素）超过本仓上限 image_web.MAX_IMAGE_PIXELS=120M 但低于 Pillow 硬限，
    两条拒绝路径分别被两个用例钉住。
    """
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00")  # 拒绝发生在尺寸自查，IDAT 内容不影响判定
    return (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b""))


@pytest.fixture()
def client():
    return _make_client()


@pytest.fixture()
def success_path_stubs(monkeypatch):
    """成功路径重活打桩：画质门/守卫/预处理/识别 Job/URL 签名/埋点。

    仅用于「合法上传仍须通过」的反向用例；拒绝类用例在 _save_upload 内即返回，
    不触达这些桩（删掉任何一个桩都不会让拒绝用例误通过）。
    """
    from app.services import preprocess
    job_calls = []

    def _stub_job(image_path, vendor_hint="", receipt_id=None, tenant_id=None,
                  image_guard=None, **kw):
        if receipt_id is None:
            receipt_id = db.create_receipt(status="parsing")
        db.update_receipt(receipt_id, status="parsing", image_path=image_path)
        job_calls.append({"image_path": image_path, "receipt_id": receipt_id})
        return ("job-stub-1", receipt_id)

    monkeypatch.setattr(api_receipts, "_laplacian_variance", lambda _p: None)
    monkeypatch.setattr(api_receipts, "_run_image_guard", lambda _p: {"flagged": False})
    monkeypatch.setattr(preprocess, "apply_pipeline", lambda p: (p, {"applied": False}))
    monkeypatch.setattr(api_receipts, "start_recognition_job", _stub_job)
    monkeypatch.setattr(api_receipts, "public_image_url", lambda *a, **k: "")
    monkeypatch.setattr(api_receipts.db, "log_user_event", lambda **kw: None)
    return job_calls


def _saved_files():
    """枚举本轮测试 UPLOAD_DIR（conftest autouse 已重定向到 tmp_path）内的落盘文件。

    只看文件：凭证目录（vouchers/）与本目录共用 tmp 上传根，不计入。
    """
    base = str(api_receipts.UPLOAD_DIR)
    return sorted(n for n in os.listdir(base)
                  if os.path.isfile(os.path.join(base, n)))


# -------------------------------------------------------------
# 1. 伪 .jpg：扩展名/伪造 magic bytes 一律 400
# -------------------------------------------------------------
def test_text_file_named_jpg_rejected(client):
    """纯文本改名为 .jpg → magic bytes 校验拒绝（原实现会原样落盘）。"""
    r = client.post("/api/upload",
                    files=[("receipt", ("fake.jpg", b"this is not an image at all", "image/jpeg"))],
                    data={"vendor_hint": "预填供应商"}, headers=STAFF_HEADERS)
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "UNSUPPORTED_IMAGE_FORMAT", body
    assert _saved_files() == [], "拒绝的上传不得在 uploads/ 落任何文件"


def test_forged_magic_jpg_rejected(client):
    """JPEG magic 开头但内容损坏 → verify()/解码拒绝（伪造签名骗不过结构校验）。"""
    forged = b"\xff\xd8\xff\xe0" + b"garbage-not-a-jpeg" * 8
    r = client.post("/api/upload",
                    files=[("receipt", ("forged.jpg", forged, "image/jpeg"))],
                    data={"vendor_hint": "预填供应商"}, headers=STAFF_HEADERS)
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "INVALID_IMAGE", body
    assert _saved_files() == []


def test_fake_jpg_rejected_on_replace_image(client):
    """入口三：换图同样拒绝伪 .jpg（原实现 _save_upload 原样落盘）。"""
    rid = db.create_receipt(supplier_name="德利行", status="edited")
    r = client.post(f"/api/receipt/{rid}/replace-image",
                    files={"receipt": ("retake.jpg", b"still not an image", "image/jpeg")},
                    data={"force": "true"}, headers=STAFF_HEADERS)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "UNSUPPORTED_IMAGE_FORMAT"


def test_upload_batch_fake_file_recorded_per_file(client, success_path_stubs):
    """入口二：批量中合法文件照常入队、伪 .jpg 按文件逐条记 400 错误。"""
    r = client.post("/api/upload_batch",
                    files=[
                        ("files", ("bad.jpg", b"not an image", "image/jpeg")),
                        ("files", ("ok.png", _png_bytes(), "image/png")),
                    ], headers=STAFF_HEADERS)
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == 2
    assert results[0]["status"] == "error"
    assert results[0]["code"] == "UNSUPPORTED_IMAGE_FORMAT"
    assert results[1]["status"] == "queued", results[1]


# -------------------------------------------------------------
# 2. 解压炸弹 / 超大图：像素上限自查拒绝（400 IMAGE_PIXELS_EXCEEDED）
# -------------------------------------------------------------
def test_decompression_bomb_png_rejected(client):
    """声明 1.6G 像素的 PNG（字节仅数百）：Pillow 硬限 → 拒绝，绝不进入像素分配。"""
    bomb = _crafted_png(40000, 40000)
    assert len(bomb) < 1024, "样本必须是「小字节、大声明」的炸弹形态"
    r = client.post("/api/upload",
                    files=[("receipt", ("bomb.png", bomb, "image/png"))],
                    data={"vendor_hint": "预填供应商"}, headers=STAFF_HEADERS)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "IMAGE_PIXELS_EXCEEDED", r.json()
    assert _saved_files() == []


def test_oversized_pixel_png_rejected(client):
    """声明 1.3 亿像素（>120M 上限、低于 Pillow 硬限）→ 本仓口径自查拒绝。"""
    oversized = _crafted_png(13000, 10000)
    r = client.post("/api/upload",
                    files=[("receipt", ("huge.png", oversized, "image/png"))],
                    data={"vendor_hint": "预填供应商"}, headers=STAFF_HEADERS)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "IMAGE_PIXELS_EXCEEDED", r.json()
    assert _saved_files() == []


def test_convert_image_bomb_png_rejected(client):
    """/api/convert-image 同受像素上限约束（解码前置自查，不整文件进内存）。"""
    r = client.post("/api/convert-image",
                    files={"file": ("bomb.png", _crafted_png(40000, 40000), "image/png")},
                    headers=STAFF_HEADERS)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "IMAGE_PIXELS_EXCEEDED", r.json()


# -------------------------------------------------------------
# 3. 字节上限：15MB 分块限量，超限即拒
# -------------------------------------------------------------
def test_upload_too_large_rejected(client):
    r = client.post("/api/upload",
                    files=[("receipt", ("big.jpg", b"\xff\xd8\xff" + b"\x00" * (16 * 1024 * 1024),
                                        "image/jpeg"))],
                    data={"vendor_hint": "预填供应商"}, headers=STAFF_HEADERS)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "FILE_TOO_LARGE", r.json()
    assert _saved_files() == []


def test_convert_image_too_large_rejected(client):
    """/api/convert-image 分块限量读取：超限在读取过程中即拒（不再整文件进内存）。"""
    r = client.post("/api/convert-image",
                    files={"file": ("big.jpg", b"\xff\xd8\xff" + b"\x00" * (16 * 1024 * 1024),
                                     "image/jpeg")},
                    headers=STAFF_HEADERS)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "FILE_TOO_LARGE", r.json()


def test_convert_image_empty_rejected(client):
    r = client.post("/api/convert-image",
                    files={"file": ("empty.jpg", b"", "image/jpeg")},
                    headers=STAFF_HEADERS)
    assert r.status_code == 400, r.text
    assert "上传文件为空" in r.json()["msg"], r.json()


# -------------------------------------------------------------
# 4. 合法上传照常通过，且落盘的是重编码消毒后的干净容器
# -------------------------------------------------------------
def test_valid_jpeg_sanitized_on_disk(client, success_path_stubs):
    """JPEG + 附加载荷（polyglot）→ 200，且落盘文件不含附加载荷（重编码剥离）。"""
    payload = _jpeg_bytes() + b"EVIL_PAYLOAD_POLYGLOT_TAIL" * 16
    r = client.post("/api/upload",
                    files=[("receipt", ("ok.jpg", payload, "image/jpeg"))],
                    data={"vendor_hint": "预填供应商"}, headers=STAFF_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    files = _saved_files()
    assert len(files) == 1 and files[0].endswith(".jpg"), files
    saved = (api_receipts.UPLOAD_DIR / files[0]).read_bytes()
    assert b"EVIL_PAYLOAD_POLYGLOT_TAIL" not in saved, "重编码必须剥离 polyglot 附加载荷"
    with Image.open(io.BytesIO(saved)) as img:
        assert img.format == "JPEG"
        assert img.size == (48, 32), "消毒不得改变图像内容"
    assert success_path_stubs[0]["image_path"] == str(api_receipts.UPLOAD_DIR / files[0]), \
        "识别 Job 必须拿消毒后的落盘文件"


def test_valid_heic_upload_keeps_container(client, success_path_stubs):
    """真 HEIC 上传 → 200，重编码保持 HEIF 容器（最小化下游行为漂移）。"""
    r = client.post("/api/upload",
                    files=[("receipt", ("photo.heic", _heic_bytes(), "image/heic"))],
                    data={"vendor_hint": "预填供应商"}, headers=STAFF_HEADERS)
    assert r.status_code == 200, r.text
    files = _saved_files()
    assert len(files) == 1 and files[0].endswith(".heic"), files
    saved = (api_receipts.UPLOAD_DIR / files[0]).read_bytes()
    with Image.open(io.BytesIO(saved)) as img:
        assert img.format in ("HEIF", "HEIC"), img.format


def test_valid_webp_upload_ok(client, success_path_stubs):
    r = client.post("/api/upload",
                    files=[("receipt", ("ok.webp", _png_bytes(), "image/webp"))],
                    data={"vendor_hint": "预填供应商"}, headers=STAFF_HEADERS)
    # webp 扩展名但内容是 PNG：按**内容**判家族落盘（.png），上传本身放行
    assert r.status_code == 200, r.text
    files = _saved_files()
    assert len(files) == 1 and files[0].endswith(".png"), files


# -------------------------------------------------------------
# 5. preprocess._imread：像素/字节双上限，超限拒绝（None）而非告警
# -------------------------------------------------------------
def test_imread_rejects_oversized_pixel_image(tmp_path):
    """超大像素图（>120M）在 header 自查即拒，两条解码路径都不碰像素分配。"""
    p = tmp_path / "huge.png"
    p.write_bytes(_crafted_png(13000, 10000))
    assert preprocess._imread(str(p)) is None


def test_imread_rejects_pillow_bomb(tmp_path):
    p = tmp_path / "bomb.png"
    p.write_bytes(_crafted_png(40000, 40000))
    assert preprocess._imread(str(p)) is None


def test_imread_rejects_oversized_bytes(tmp_path, monkeypatch):
    """字节上限（cv2 回退路径同受保护）：超限直接 None，不进 cv2.imread。"""
    p = tmp_path / "big.png"
    p.write_bytes(_png_bytes())
    monkeypatch.setattr(preprocess, "MAX_IMAGE_BYTES", 10)
    assert preprocess._imread(str(p)) is None


def test_imread_still_decodes_normal_image(tmp_path):
    p = tmp_path / "ok.png"
    p.write_bytes(_png_bytes())
    arr = preprocess._imread(str(p))
    assert arr is not None and arr.shape == (32, 48, 3), arr.shape

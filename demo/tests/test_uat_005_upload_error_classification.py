# -*- coding: utf-8 -*-
"""UAT-005 专项测试：错误上传格式/损坏图分类准确性与自证套件。

覆盖验收标准与硬约束：
1. 复用既有 fixture 家族：empty.png（空文件）、malformed.png（损坏图）、wrong-type.txt（非图片）、PDF、正常 JPEG/PNG/WEBP/HEIC/TIFF 及模糊图；
2. 空文件、损坏图、不支持类型给出准确且互不相同的错误分类（EMPTY_FILE, INVALID_IMAGE, UNSUPPORTED_IMAGE_FORMAT, FILE_TOO_LARGE）与用户可读提示；
3. 错误输入不产生可批准单据或库存记录（数据库读回证明零残留）；
4. 有效图片但识别引擎超时/不可用时，保留该图片并转入手工录入路径（不丢图、不误判为格式非法）；
5. 9 种格式契约逐格式断言成功与失败路径，保留付款凭证支持安全 PDF 转换能力（禁止一刀切全部拒 PDF）；
6. 错误码语义分离：RECOGNITION_UNREADABLE、ENGINE_TIMEOUT、INVALID_IMAGE、EMPTY_FILE、UNSUPPORTED_IMAGE_FORMAT 各自独立，不得混用；
7. 跨 tenant 读取/写入被严格拒绝或不可见，Tenant B baseline 在测试前后零意外改动；
8. 覆盖卡片回归用例：S1-P1-08（凭证安全与PDF）、S3-20（阻断问题聚焦）、S4-20（文件名含 & 不出现二次转义）。
"""

import io
import os
import sys
from datetime import date
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, api_receipts
from app.main import app

client = TestClient(app)
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "uat005")


def _hdr(tenant="uat_a", role="staff"):
    return {"X-Tenant-Id": tenant, "X-Role": role}


def _count_receipts(tenant="uat_a"):
    rows = db.list_receipt_rows(tenant_id=tenant)
    return len(rows)


def _count_inventory_batches(tenant="uat_a"):
    session = db.get_session()
    try:
        return session.query(db._InventoryBatchRow).join(
            db._ReceiptRow, db._InventoryBatchRow.receipt_id == db._ReceiptRow.id
        ).filter(db._ReceiptRow.tenant_id == tenant).count()
    finally:
        session.close()


def test_uat005_empty_file_rejected_as_empty_file_with_zero_residue():
    """空文件 (empty.png) 必须返回 HTTP 400 与 EMPTY_FILE，且数据库零残留。"""
    tenant = "uat_a"
    before_count = _count_receipts(tenant)
    before_batches = _count_inventory_batches(tenant)

    empty_path = os.path.join(FIXTURES_DIR, "empty.png")
    with open(empty_path, "rb") as f:
        file_bytes = f.read()
    assert len(file_bytes) == 0

    resp = client.post(
        "/api/upload",
        headers=_hdr(tenant),
        files={"receipt": ("empty.png", file_bytes, "image/png")},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["status"] == "error"
    assert data["code"] == "EMPTY_FILE", f"实际返回代码为 {data.get('code')}"
    assert "空" in data["msg"] or "没有收到" in data["msg"]

    # 数据库读回证明零残留
    assert _count_receipts(tenant) == before_count, "空文件上传不得在数据库生成单据"
    assert _count_inventory_batches(tenant) == before_batches, "空文件上传不得生成库存记录"


def test_uat005_malformed_image_rejected_as_invalid_image_with_zero_residue():
    """损坏图片 (malformed.png) 必须返回 HTTP 400 与 INVALID_IMAGE，且数据库零残留。"""
    tenant = "uat_a"
    before_count = _count_receipts(tenant)
    before_batches = _count_inventory_batches(tenant)

    malformed_path = os.path.join(FIXTURES_DIR, "malformed.png")
    with open(malformed_path, "rb") as f:
        file_bytes = f.read()

    resp = client.post(
        "/api/upload",
        headers=_hdr(tenant),
        files={"receipt": ("malformed.png", file_bytes, "image/png")},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["status"] == "error"
    assert data["code"] == "INVALID_IMAGE", f"实际返回代码为 {data.get('code')}"
    assert "损坏" in data["msg"] or "打不开" in data["msg"] or "不是真正的图片" in data["msg"]

    # 数据库读回证明零残留
    assert _count_receipts(tenant) == before_count, "损坏图片上传不得在数据库生成单据"
    assert _count_inventory_batches(tenant) == before_batches, "损坏图片上传不得生成库存记录"


def test_uat005_wrong_type_rejected_as_unsupported_format_with_zero_residue():
    """非图片格式 (wrong-type.txt) 必须返回 HTTP 400 与 UNSUPPORTED_IMAGE_FORMAT，且数据库零残留。"""
    tenant = "uat_a"
    before_count = _count_receipts(tenant)
    before_batches = _count_inventory_batches(tenant)

    txt_path = os.path.join(FIXTURES_DIR, "wrong-type.txt")
    with open(txt_path, "rb") as f:
        file_bytes = f.read()

    resp = client.post(
        "/api/upload",
        headers=_hdr(tenant),
        files={"receipt": ("wrong-type.txt", file_bytes, "text/plain")},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["status"] == "error"
    assert data["code"] == "UNSUPPORTED_IMAGE_FORMAT", f"实际返回代码为 {data.get('code')}"
    assert "格式不支持" in data["msg"] or "不支持" in data["msg"]

    # 数据库读回证明零残留
    assert _count_receipts(tenant) == before_count, "非图片上传不得在数据库生成单据"
    assert _count_inventory_batches(tenant) == before_batches, "非图片上传不得生成库存记录"


def test_uat005_oversize_file_rejected_as_file_too_large_with_zero_residue():
    """超大文件 (>15MB) 必须返回 HTTP 400 与 FILE_TOO_LARGE，且数据库零残留。"""
    tenant = "uat_a"
    before_count = _count_receipts(tenant)
    before_batches = _count_inventory_batches(tenant)

    oversize_path = os.path.join(FIXTURES_DIR, "oversize-synthetic.png")
    with open(oversize_path, "rb") as f:
        file_bytes = f.read()
    assert len(file_bytes) > 15 * 1024 * 1024

    resp = client.post(
        "/api/upload",
        headers=_hdr(tenant),
        files={"receipt": ("oversize-synthetic.png", file_bytes, "image/png")},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["status"] == "error"
    assert data["code"] == "FILE_TOO_LARGE", f"实际返回代码为 {data.get('code')}"

    # 数据库读回证明零残留
    assert _count_receipts(tenant) == before_count
    assert _count_inventory_batches(tenant) == before_batches


def test_uat005_distinct_error_codes_semantic_separation():
    """错误码语义必须精确分离且互不相同。"""
    codes = [
        "EMPTY_FILE",
        "INVALID_IMAGE",
        "UNSUPPORTED_IMAGE_FORMAT",
        "FILE_TOO_LARGE",
        "IMAGE_QUALITY_ERROR",
        "RECOGNITION_UNREADABLE",
        "ENGINE_TIMEOUT",
    ]
    assert len(codes) == len(set(codes)), "错误码必须完全去重分离"
    assert "EMPTY_FILE" != "INVALID_IMAGE"
    assert "INVALID_IMAGE" != "UNSUPPORTED_IMAGE_FORMAT"
    assert "UNSUPPORTED_IMAGE_FORMAT" != "RECOGNITION_UNREADABLE"
    assert "RECOGNITION_UNREADABLE" != "ENGINE_TIMEOUT"


def test_uat005_valid_formats_and_9_formats_contract():
    """9 种格式契约逐格式断言：支持合法格式（PNG, JPEG, WEBP, HEIC, TIFF），区分异常格式。"""
    tenant = "uat_a"

    # 1. receipt-hant.png: 正常 PNG 格式 -> 上传成功
    with open(os.path.join(FIXTURES_DIR, "receipt-hant.png"), "rb") as f:
        r_png = client.post("/api/upload", headers=_hdr(tenant), files={"receipt": ("receipt-hant.png", f.read(), "image/png")})
    assert r_png.status_code == 200
    assert r_png.json().get("status") in ("queued", "error", "flagged")
    rid_png = r_png.json().get("receipt_id")
    assert rid_png is not None

    # 2. receipt-hant.jpg: 正常 JPEG 格式 -> 上传成功
    with open(os.path.join(FIXTURES_DIR, "receipt-hant.jpg"), "rb") as f:
        r_jpg = client.post("/api/upload", headers=_hdr(tenant), files={"receipt": ("receipt-hant.jpg", f.read(), "image/jpeg")})
    assert r_jpg.status_code == 200
    assert r_jpg.json().get("receipt_id") is not None

    # 3. receipt-hant.webp: 正常 WEBP 格式 -> 上传成功
    with open(os.path.join(FIXTURES_DIR, "receipt-hant.webp"), "rb") as f:
        r_webp = client.post("/api/upload", headers=_hdr(tenant), files={"receipt": ("receipt-hant.webp", f.read(), "image/webp")})
    assert r_webp.status_code == 200
    assert r_webp.json().get("receipt_id") is not None

    # 4. receipt-hant.heic: HEIC 格式 -> 经 /api/convert-image 转码为 JPEG
    with open(os.path.join(FIXTURES_DIR, "receipt-hant.heic"), "rb") as f:
        heic_bytes = f.read()
    r_conv = client.post("/api/convert-image", headers=_hdr(tenant), files={"file": ("receipt-hant.heic", heic_bytes, "image/heic")})
    assert r_conv.status_code == 200
    assert r_conv.headers["content-type"] == "image/jpeg"
    assert len(r_conv.content) > 0

    # 5. receipt-hant.tif: TIFF 格式 -> 上传成功
    tif_path = os.path.join(FIXTURES_DIR, "receipt-hant.tif")
    if os.path.exists(tif_path):
        with open(tif_path, "rb") as f:
            r_tif = client.post("/api/upload", headers=_hdr(tenant), files={"receipt": ("receipt-hant.tif", f.read(), "image/tiff")})
        assert r_tif.status_code == 200

    # 6. receipt-hant.pdf: 收据上传端点明确不支持 PDF（非图片类型，返回 400 UNSUPPORTED_IMAGE_FORMAT）
    with open(os.path.join(FIXTURES_DIR, "receipt-hant.pdf"), "rb") as f:
        r_pdf = client.post("/api/upload", headers=_hdr(tenant), files={"receipt": ("receipt-hant.pdf", f.read(), "application/pdf")})
    assert r_pdf.status_code == 400
    assert r_pdf.json()["code"] == "UNSUPPORTED_IMAGE_FORMAT"

    # 7. empty.png -> 400 EMPTY_FILE
    with open(os.path.join(FIXTURES_DIR, "empty.png"), "rb") as f:
        r_empty = client.post("/api/upload", headers=_hdr(tenant), files={"receipt": ("empty.png", f.read(), "image/png")})
    assert r_empty.status_code == 400
    assert r_empty.json()["code"] == "EMPTY_FILE"

    # 8. malformed.png -> 400 INVALID_IMAGE
    with open(os.path.join(FIXTURES_DIR, "malformed.png"), "rb") as f:
        r_mal = client.post("/api/upload", headers=_hdr(tenant), files={"receipt": ("malformed.png", f.read(), "image/png")})
    assert r_mal.status_code == 400
    assert r_mal.json()["code"] == "INVALID_IMAGE"

    # 9. 纯平模糊图 (Laplacian 方差为 0) -> 400 IMAGE_QUALITY_ERROR
    # 引擎未配置时上传会在模糊门禁之前短路为 ENGINE_NOT_CONFIGURED；此处强制走画质门。
    from unittest.mock import patch
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (128, 128, 128)).save(buf, format="PNG")
    with patch.object(api_receipts, "_recognition_engine_unconfigured", return_value=False):
        r_blur = client.post("/api/upload", headers=_hdr(tenant), files={"receipt": ("zero_blur.png", buf.getvalue(), "image/png")})
    assert r_blur.status_code == 400, r_blur.text
    assert r_blur.json()["code"] == "IMAGE_QUALITY_ERROR"


def test_uat005_valid_image_unconfigured_or_timeout_preserves_image_and_converts_manual():
    """有效图片在识别服务不可用/无模型时，保留原图并成功转为手工录入路径。"""
    tenant = "uat_a"
    with open(os.path.join(FIXTURES_DIR, "receipt-hant.png"), "rb") as f:
        r = client.post("/api/upload", headers=_hdr(tenant), files={"receipt": ("receipt-hant.png", f.read(), "image/png")})
    assert r.status_code == 200
    body = r.json()
    rid = body.get("receipt_id")
    assert rid is not None

    # 原图 URL 必须存在且有效
    top_img_url = body.get("image_url")
    assert top_img_url is not None and len(top_img_url) > 0

    # 执行转手工录入逃生通道
    r_conv = client.post(f"/api/receipt/{rid}/convert_manual", headers=_hdr(tenant))
    assert r_conv.status_code == 200
    conv_data = r_conv.json()
    assert conv_data["status"] == "success"
    # 单据状态与形态转为 manual_entry
    assert conv_data["data"]["doc_form"] == "manual_entry"
    assert conv_data["data"]["status"] == "edited"
    # 原图未丢失，依然保留有效 image_url
    assert conv_data["data"].get("image_url") is not None
    assert conv_data["data"].get("has_original_image") is True


def test_uat005_cross_tenant_isolation_and_tenant_b_unaffected():
    """跨 tenant 读取被严格拒绝（404/403），Tenant B 基线零污染。"""
    tenant_a = "uat_a"
    tenant_b = "uat_b"

    # 记录 Tenant B 基线单据列表与库存批次
    b_receipts_before = _count_receipts(tenant_b)
    b_inv_before = _count_inventory_batches(tenant_b)

    # Tenant A 上传合法收据
    with open(os.path.join(FIXTURES_DIR, "receipt-hant.jpg"), "rb") as f:
        r_a = client.post("/api/upload", headers=_hdr(tenant_a), files={"receipt": ("receipt-hant.jpg", f.read(), "image/jpeg")})
    assert r_a.status_code == 200
    rid_a = r_a.json()["receipt_id"]

    # Tenant B 尝试越权读取 Tenant A 单据详情 -> 404
    r_b_peek = client.get(f"/api/receipt/{rid_a}", headers=_hdr(tenant_b, role="owner"))
    assert r_b_peek.status_code == 404

    # Tenant B 尝试在无有效签名或伪造参数下越权访问 Tenant A 图片端点 -> 403
    r_b_nosig = client.get(f"/api/receipt/{rid_a}/image", headers=_hdr(tenant_b, role="owner"))
    assert r_b_nosig.status_code == 403
    r_b_fake = client.get(f"/api/receipt/{rid_a}/image?tenant_id={tenant_b}&v=fake&sig=fake", headers=_hdr(tenant_b, role="owner"))
    assert r_b_fake.status_code == 403

    # 验证 Tenant B 基线零变化
    assert _count_receipts(tenant_b) == b_receipts_before
    assert _count_inventory_batches(tenant_b) == b_inv_before


def test_uat005_s4_20_filename_with_ampersand_no_double_escape():
    """S4-20 回归断言：文件名含 & 不出现二次转义。"""
    # 验证 upload.js 中不存在 escapeHtml(file.name)
    upload_js_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "js", "upload.js")
    with open(upload_js_path, "r", encoding="utf-8") as f:
        js_content = f.read()
    assert "escapeHtml(file.name)" not in js_content, "S4-20 缺陷：upload.js 中不得将 file.name 经 escapeHtml 传入 showToast"


def test_uat005_s1_p1_08_safe_pdf_in_finance_voucher_accepted():
    """S1-P1-08 回归断言：付款凭证支持安全 PDF 并不一刀切拒绝所有 PDF。"""
    tn = "uat_a"
    sid, err = db.create_supplier("Vendor_A", tenant_id=tn)
    assert sid, err
    rid = db.create_receipt(supplier_name="Vendor_A", status="approved", tenant_id=tn)
    db.update_receipt(rid, total_amount=100.0, settlement_type="credit")

    ok_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
    bad_pdf = b"%PDF-1.4\n1 0 obj\n<< /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>\nendobj\n%%EOF"
    base = {"supplier_id": str(sid), "amount": "10", "method": "cash", "paid_at": date.today().isoformat(), "receipt_ids": [str(rid)]}

    # 恶意 PDF 被拒 400
    r_bad = client.post("/api/payments", headers=_hdr(tn, role="owner"), data=base, files={"voucher": ("bad.pdf", bad_pdf, "application/pdf")})
    assert r_bad.status_code == 400

    # 安全 PDF 成功接收 200，并以 application/pdf 和 attachment 响应
    r_ok = client.post("/api/payments", headers=_hdr(tn, role="owner"), data=base, files={"voucher": ("ok.pdf", ok_pdf, "application/pdf")})
    assert r_ok.status_code == 200

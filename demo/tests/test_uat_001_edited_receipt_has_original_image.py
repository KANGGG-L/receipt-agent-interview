# -*- coding: utf-8 -*-
"""UAT-001 专项测试：已有原图单据在复核工作台及归档详情不得被误判为「手工无原图」。

覆盖验收标准与负边界：
1. 预置有原图 edited 单与无图 manual 单；详情读取时，edited 单在顶层及 data 内部均返回签名 image_url，且 has_original_image 为 True；
2. 无图 manual 单据返回空 image_url，且 has_original_image 为 False；
3. 有图时经端点 GET /api/receipt/{id}/image 验证返回 HTTP 200 与图片真实内容；
4. 签名失效/篡改/缺失时被 403 明确拒绝，不得把 URL 加载失败伪作为「无原图」；重新拉取详情可刷新签名恢复；
5. 跨租户访问被严格拒绝（404 / 403），B 租户基线无污染；
6. 前端脚本（review_core.js / archive_list.js）消除对 ret.data.image_url 缺失导致的误判，并提供失效恢复与明确错误提示。
"""

import io
import os
import sys
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, api_receipts
from app.main import app

client = TestClient(app)
STAFF = {"X-Role": "staff"}
OWNER = {"X-Role": "owner"}


def _create_png(path, color=(50, 150, 250), size=(64, 64)):
    img = Image.new("RGB", size, color)
    img.save(path, format="PNG")


def test_uat001_edited_receipt_with_image_returns_signed_url_in_data_and_top_level(tmp_path):
    """有原图 edited 单据：GET /api/receipt/{id} 在顶层及 data 内部均必须返回有效签名 image_url，且 has_original_image 为 True。"""
    img_name = "uat_001_fixture_edited.png"
    img_path = api_receipts.UPLOAD_DIR / img_name
    _create_png(img_path)

    rid = db.create_receipt(supplier_name="Vendor_A", status="edited", tenant_id="default")
    db.update_receipt(
        rid,
        receipt_date="2026-10-15",
        total_amount=230.0,
        settlement_type="credit",
        doc_form="printed",
        image_path=str(img_path),
        version=1,
    )
    db.set_receipt_items(
        rid,
        [
            {"name": "UAT_Rice", "quantity": 10, "unit": "kg", "unit_price": 12, "amount": 120},
            {"name": "UAT_Oil", "quantity": 5, "unit": "L", "unit_price": 20, "amount": 100},
        ],
        tenant_id="default",
    )

    resp = client.get(f"/api/receipt/{rid}", headers=OWNER)
    assert resp.status_code == 200
    body = resp.json()

    # 顶层必须有非空签名 image_url
    assert "image_url" in body
    top_url = body["image_url"]
    assert top_url and top_url.startswith(f"/api/receipt/{rid}/image")
    assert "sig=" in top_url

    # 关键断言（解决复核工作台误判为手工无原图的根因）：
    # ret.data 内部必须同步包含 image_url，供 workbench_queue.js 与 review_core.js 读取
    data = body.get("data")
    assert data is not None
    assert "image_url" in data, "ret.data 内部缺少 image_url 字段"
    assert data["image_url"] == top_url, "ret.data.image_url 必须与顶层签名 image_url 一致"
    assert data.get("has_original_image") is True, "有图单据 data.has_original_image 必须为 True"


def test_uat001_manual_entry_without_image_returns_empty_image_url():
    """纯手工无原图单据：GET /api/receipt/{id} 返回空串 image_url，且 has_original_image 为 False。"""
    rid = db.create_receipt(supplier_name="Vendor_A", status="edited", tenant_id="default")
    db.update_receipt(
        rid,
        receipt_date="2026-10-15",
        total_amount=10.0,
        settlement_type="cash",
        doc_form="manual_entry",
        image_path="",
        version=1,
    )
    db.set_receipt_items(
        rid,
        [{"name": "Synthetic_Rice", "quantity": 1, "unit": "kg", "unit_price": 10, "amount": 10}],
        tenant_id="default",
    )

    resp = client.get(f"/api/receipt/{rid}", headers=OWNER)
    assert resp.status_code == 200
    body = resp.json()

    assert body["image_url"] == ""
    data = body["data"]
    assert data.get("image_url") == ""
    assert data.get("has_original_image") is False


def test_uat001_image_endpoint_serves_valid_image_with_signature():
    """有原图时通过签名 URL 访问 GET /api/receipt/{id}/image 必须返回 HTTP 200 与真实图片内容。"""
    img_name = "uat_001_preview_valid.png"
    img_path = api_receipts.UPLOAD_DIR / img_name
    _create_png(img_path, color=(10, 80, 200))

    rid = db.create_receipt(supplier_name="Vendor_A", status="edited", tenant_id="default")
    db.update_receipt(rid, receipt_date="2026-10-15", total_amount=100.0, doc_form="printed", image_path=str(img_path))

    resp = client.get(f"/api/receipt/{rid}", headers=STAFF)
    assert resp.status_code == 200
    img_url = resp.json()["image_url"]
    assert img_url

    # 模拟浏览器原生访问图片端点（不带认证头）
    img_resp = client.get(img_url)
    assert img_resp.status_code == 200
    assert img_resp.headers["content-type"].startswith("image/")
    assert len(img_resp.content) > 0
    # 验证真实图片头部魔数
    assert img_resp.content[:4] == b"\x89PNG" or img_resp.content[:2] == b"\xff\xd8"


def test_uat001_expired_or_tampered_signature_is_rejected_then_recoverable():
    """过期/失效/篡改签名必须返回 403 明确拒绝（不得伪作无图）；重新拉取详情可刷新签名恢复。"""
    img_name = "uat_001_tamper_test.png"
    img_path = api_receipts.UPLOAD_DIR / img_name
    _create_png(img_path)

    rid = db.create_receipt(supplier_name="Vendor_A", status="edited", tenant_id="default")
    db.update_receipt(rid, receipt_date="2026-10-15", total_amount=50.0, doc_form="printed", image_path=str(img_path))

    # 1. 篡改签名 -> 403 明确拒绝
    tampered_url = f"/api/receipt/{rid}/image?v=uat_001_tamper_test&sig=corrupted_invalid_sig"
    t_resp = client.get(tampered_url)
    assert t_resp.status_code == 403
    t_data = t_resp.json()
    assert t_data.get("code") in ("IMAGE_SIGNATURE_INVALID", "IMAGE_FORBIDDEN")

    # 2. 缺少签名 -> 403 明确拒绝
    no_sig_url = f"/api/receipt/{rid}/image?v=uat_001_tamper_test"
    ns_resp = client.get(no_sig_url)
    assert ns_resp.status_code == 403

    # 3. 正常重开/刷新详情重新签发恢复
    refresh_resp = client.get(f"/api/receipt/{rid}", headers=OWNER)
    assert refresh_resp.status_code == 200
    fresh_url = refresh_resp.json()["image_url"]
    assert fresh_url and "sig=" in fresh_url

    recovered_img_resp = client.get(fresh_url)
    assert recovered_img_resp.status_code == 200
    assert len(recovered_img_resp.content) > 0


def test_uat001_cross_tenant_access_denied_and_tenant_b_isolated():
    """跨 tenant 读取收据详情及图片必须被拒绝；租户隔离严格保证。"""
    img_name_a = "uat_001_tenant_a.png"
    img_path_a = api_receipts.UPLOAD_DIR / img_name_a
    _create_png(img_path_a, color=(120, 20, 20))

    # 在 uat_a 创建单据
    rid_a = db.create_receipt(supplier_name="Vendor_A", status="edited", tenant_id="uat_a")
    db.update_receipt(rid_a, receipt_date="2026-10-15", total_amount=88.0, doc_form="printed", image_path=str(img_path_a))

    # 租户 uat_b 尝试读取详情 -> 404
    resp_b = client.get(f"/api/receipt/{rid_a}", headers={"X-Role": "owner", "X-Tenant-Id": "uat_b"})
    assert resp_b.status_code == 404

    # 租户 uat_a 获取合法 URL
    resp_a = client.get(f"/api/receipt/{rid_a}", headers={"X-Role": "owner", "X-Tenant-Id": "uat_a"})
    assert resp_a.status_code == 200
    url_a = resp_a.json()["image_url"]
    assert "tenant_id=uat_a" in url_a

    # 篡改为 uat_b 的 tenant_id 访问图片端点 -> 403
    tampered_tenant_url = url_a.replace("tenant_id=uat_a", "tenant_id=uat_b")
    cross_img_resp = client.get(tampered_tenant_url)
    assert cross_img_resp.status_code == 403

# -*- coding: utf-8 -*-
"""P0 旋转重构与凭证真理源保真测试套件。

覆盖验收标准与质量矩阵：
- AC-BE-01 (正交纠偏下线): preprocess_orthogonal_enabled 默认 False，大角度翻转彻底停用。
- AC-BE-02 (小角度纠偏上限): |angle| > 15.0° 严禁自动旋转，原图返回且 reason="angle_exceeds_threshold"。
- AC-BE-03 (小角度死区规范): |angle| <= 1.0° 保持矩阵不变，不执行重采样。
- AC-BE-04 (原图完整性保障): 上传与批量入库的 receipts.image_path 恒指向原图，绝不被 _prep.jpg 篡改。
- AC-BE-05 (元数据一致性): orthogonal_angle 恒为 0，skew_angle 真实记录。
- AC-FE-01 ~ AC-FE-04 & AC-E2E-01 ~ AC-E2E-03: 前端交互契约与重识别全链路验证。
"""
import io
import json
import os
import subprocess
import cv2
import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import preprocess, settings_service
from app.services.preprocess import (
    DESKEW_DEADZONE_DEG,
    DESKEW_MAX_ANGLE_DEG,
    _orthogonal_correct,
    apply_pipeline,
    deskew,
)
from app.services.receipt_utils import start_recognition_job, public_image_url


H, W = 600, 400


def _create_mock_receipt_image(path, skew_angle=0.0):
    img = np.full((H, W, 3), 245, dtype=np.uint8)
    for y in (60, 120, 180, 240):
        cv2.rectangle(img, (40, y), (W - 60, y + 26), (30, 30, 30), -1)
    cv2.rectangle(img, (40, 520), (W - 60, 534), (60, 60, 60), -1)
    if skew_angle != 0.0:
        M = cv2.getRotationMatrix2D((W / 2.0, H / 2.0), skew_angle, 1.0)
        img = cv2.warpAffine(img, M, (W, H), borderValue=(245, 245, 245))
    cv2.imwrite(str(path), img)
    return img


def _sharp_png_bytes(seed=42):
    """随机高方差图像：Laplacian 方差 > 30，可通过画质拦截门禁。"""
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(128, 128), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_p0_rotation.db")
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
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def upload_dir(monkeypatch, tmp_path):
    from app import api_receipts
    d = tmp_path / "uploads"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(api_receipts, "UPLOAD_DIR", d)
    return d


@pytest.fixture()
def job_stub(monkeypatch):
    calls = []

    def _stub(image_path, vendor_hint="", receipt_id=None, tenant_id=None,
              image_guard=None, vendor_hint_origin="", raw_image_path=None):
        _tenant = str(tenant_id or "default").strip() or "default"
        if receipt_id is None:
            receipt_id = db.create_receipt(status="uploaded", tenant_id=_tenant)
        db_image_path = raw_image_path if raw_image_path else image_path
        db.update_receipt(receipt_id, status="parsing", image_path=db_image_path)
        calls.append({
            "image_path": image_path,
            "raw_image_path": raw_image_path,
            "vendor_hint": vendor_hint,
            "receipt_id": receipt_id,
            "tenant_id": tenant_id,
        })
        return ("job-test-p0", receipt_id)

    from app import api_receipts
    monkeypatch.setattr(api_receipts, "start_recognition_job", _stub)
    return calls


# -------------------------------------------------------------
# 1. AC-BE-01: 正交大角度纠偏彻底下线
# -------------------------------------------------------------
def test_ac_be_01_orthogonal_correction_permanently_disabled():
    """AC-BE-01: 正交纠偏彻底下线，默认配置为 False，且无论输入角度如何均不翻转。"""
    assert settings_service.SETTINGS_DEFAULTS["preprocess_orthogonal_enabled"] is False

    img = np.full((H, W, 3), 200, dtype=np.uint8)
    for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE):
        rotated = cv2.rotate(img, rot)
        out, angle = _orthogonal_correct(rotated)
        assert angle == 0
        assert out is rotated


def test_ac_be_01_orthogonal_setting_ignored_even_if_true(monkeypatch):
    """AC-BE-01: 即使配置被外部强行置为 True，_orthogonal_correct 也恒定返回 (image, 0)。"""
    monkeypatch.setattr(settings_service, "get_bool", lambda k, d=False: True)
    img = np.full((H, W, 3), 200, dtype=np.uint8)
    rotated = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    out, angle = _orthogonal_correct(rotated)
    assert angle == 0
    assert out is rotated


# -------------------------------------------------------------
# 2. AC-BE-02: 超限区间 (>15.0°) 严禁自动纠偏
# -------------------------------------------------------------
@pytest.mark.parametrize("angle", [15.1, 16.0, 30.0, 45.0, -15.1, -25.0, -45.0])
def test_ac_be_02_deskew_max_angle_limit(tmp_path, monkeypatch, angle):
    """AC-BE-02: 偏角绝对值 > 15.0° 严禁自动纠偏，原图返回并标记 reason='angle_exceeds_threshold'。"""
    img_path = tmp_path / f"exceed_{angle}.jpg"
    _create_mock_receipt_image(img_path)

    monkeypatch.setattr(settings_service, "get_bool", lambda k, d=False: True if k == "preprocess_enabled" else False)
    monkeypatch.setattr(preprocess, "_estimate_skew_angle", lambda g: angle)

    out_path, meta = apply_pipeline(str(img_path))
    assert out_path == str(img_path)
    assert meta["applied"] is False
    assert meta["deskew_skipped"] is True
    assert meta["reason"] == "angle_exceeds_threshold"
    assert meta["skew_angle"] == angle
    assert meta["orthogonal_angle"] == 0


# -------------------------------------------------------------
# 3. AC-BE-03: 小角度死区规范 (<=1.0°)
# -------------------------------------------------------------
@pytest.mark.parametrize("angle", [0.0, 0.5, 1.0, -0.5, -1.0])
def test_ac_be_03_deskew_deadzone_preserves_matrix(tmp_path, monkeypatch, angle):
    """AC-BE-03: 偏角 <= 1.0° 处于死区，原样返回原图矩阵，严禁几何旋转重采样。"""
    img_path = tmp_path / f"deadzone_{angle}.jpg"
    orig_img = _create_mock_receipt_image(img_path)

    monkeypatch.setattr(preprocess, "_estimate_skew_angle", lambda g: angle)

    out, res_angle = deskew(orig_img)
    assert abs(res_angle) <= DESKEW_DEADZONE_DEG
    assert np.array_equal(out, orig_img)

    # 默认设置 preprocess_enabled=False 下，apply_pipeline 恒返回原图路径与 disabled
    out_path_default, meta_default = apply_pipeline(str(img_path))
    assert out_path_default == str(img_path)
    assert meta_default["applied"] is False
    assert meta_default["reason"] == "disabled"

    # 若开启 preprocess_enabled=True，死区内 deskew 不做几何旋转，仅记录角度
    monkeypatch.setattr(settings_service, "get_bool", lambda k, d=False: True if k == "preprocess_enabled" else False)
    out_path_enabled, meta_enabled = apply_pipeline(str(img_path))
    assert out_path_enabled.endswith("_prep.jpg")
    assert meta_enabled["applied"] is True
    assert meta_enabled["skew_angle"] == angle
    assert meta_enabled["orthogonal_angle"] == 0


# -------------------------------------------------------------
# 4. AC-BE-05: 合法区间 (1.0° < |angle| <= 15.0°) 正常纠偏
# -------------------------------------------------------------
@pytest.mark.parametrize("angle", [1.1, 5.0, 8.5, 15.0, -1.1, -8.0, -15.0])
def test_ac_be_05_valid_skew_window(tmp_path, monkeypatch, angle):
    """AC-BE-05: 合法区间内执行微角度纠正，orthogonal_angle 恒为 0，skew_angle 真实反映纠正角度。"""
    img_path = tmp_path / f"valid_skew_{angle}.jpg"
    _create_mock_receipt_image(img_path)

    monkeypatch.setattr(settings_service, "get_bool", lambda k, d=False: True if k == "preprocess_enabled" else False)
    monkeypatch.setattr(preprocess, "_estimate_skew_angle", lambda g: angle)

    out_path, meta = apply_pipeline(str(img_path))
    assert out_path.endswith("_prep.jpg")
    assert meta["applied"] is True
    assert meta["orthogonal_angle"] == 0
    assert meta["skew_angle"] == angle
    assert meta["reason"] == ""


# -------------------------------------------------------------
# 5. AC-BE-04: 原图真理源保护 (数据库与接口 URL)
# -------------------------------------------------------------
def test_ac_be_04_source_of_truth_db_not_overwritten(tmp_path):
    """AC-BE-04: receipts.image_path 必须持久化原始图片路径，绝不被 _prep.jpg 覆盖。"""
    orig_path = str(tmp_path / "my_receipt.jpg")
    _create_mock_receipt_image(orig_path)

    prep_path = str(tmp_path / "my_receipt_prep.jpg")
    _create_mock_receipt_image(prep_path)

    job_id, receipt_id = start_recognition_job(
        prep_path,
        vendor_hint="",
        raw_image_path=orig_path
    )

    row = db.get_receipt_row(receipt_id)
    assert row is not None
    # 数据库真理源必须是指向原图
    assert row.image_path == orig_path
    assert row.image_path != prep_path


def test_ac_be_04_upload_endpoint_preserves_raw_image(client, upload_dir, job_stub, monkeypatch):
    """AC-BE-04: POST /api/upload 即使执行了预处理产生 _prep.jpg，落库的也是原始图片。"""
    from app.services import preprocess

    def _fake_apply_pipeline(path):
        prep = path.replace(".png", "_prep.png")
        with open(prep, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"x" * 60)
        return prep, {"applied": True, "skew_angle": 3.5, "orthogonal_angle": 0}

    monkeypatch.setattr(preprocess, "apply_pipeline", _fake_apply_pipeline)

    resp = client.post(
        "/api/upload",
        files={"receipt": ("test_receipt.png", _sharp_png_bytes(seed=1), "image/png")},
        headers={"X-Role": "staff"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # queued when engine configured; ENGINE_NOT_CONFIGURED still keeps raw image (AC-BE-04)
    assert body["status"] in ("queued", "error"), body
    if body["status"] == "error":
        assert body.get("code") == "ENGINE_NOT_CONFIGURED", body
    receipt_id = body["receipt_id"]
    image_url = body["image_url"]

    # 验证数据库中持久化的 image_path 绝对不含 _prep
    row = db.get_receipt_row(receipt_id)
    assert row is not None
    assert "_prep" not in row.image_path
    assert os.path.exists(row.image_path)

    # 验证返回的 image_url 指向原图签名端点
    expected_url = public_image_url(receipt_id, row.image_path)
    assert image_url == expected_url


def test_ac_be_04_upload_batch_preserves_raw_image(client, upload_dir, job_stub, monkeypatch):
    """AC-BE-04: POST /api/upload_batch 批量上传落库的每个 receipt.image_path 均不含 _prep.jpg。"""
    from app.services import preprocess

    def _fake_apply_pipeline(path):
        prep = path.replace(".png", "_prep.png")
        with open(prep, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"x" * 60)
        return prep, {"applied": True, "skew_angle": 4.0, "orthogonal_angle": 0}

    monkeypatch.setattr(preprocess, "apply_pipeline", _fake_apply_pipeline)

    resp = client.post(
        "/api/upload_batch",
        files=[
            ("files", ("batch_1.png", _sharp_png_bytes(seed=11), "image/png")),
            ("files", ("batch_2.png", _sharp_png_bytes(seed=12), "image/png")),
        ],
        headers={"X-Role": "staff"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert len(body["results"]) == 2

    for item in body["results"]:
        rid = item["receipt_id"]
        row = db.get_receipt_row(rid)
        assert row is not None
        assert "_prep" not in row.image_path
        assert os.path.exists(row.image_path)
        assert item["image_url"] == public_image_url(rid, row.image_path)


# -------------------------------------------------------------
# 6. AC-E2E-01 ~ AC-E2E-03: 旋转换图与重跑重试闭环
# -------------------------------------------------------------
def test_ac_e2e_replace_image_full_workflow(client, upload_dir, job_stub, tmp_path):
    """AC-E2E-02: 用户旋转单据后调用 /api/receipt/{id}/replace-image 重新识别全流程。"""
    # 1. 创建一张已解析完成（status='parsed'）的收据
    orig_file = tmp_path / "initial_receipt.png"
    orig_file.write_bytes(_sharp_png_bytes(seed=21))
    rid = db.create_receipt(supplier_name="測試商戶", status="parsed")
    db.update_receipt(rid, image_path=str(orig_file))

    # 2. 模拟前端 Canvas 烘焙旋转 90° 后的新图片
    rotated_bytes = _sharp_png_bytes(seed=22)

    # 3. 调用 replace-image 接口
    resp = client.post(
        f"/api/receipt/{rid}/replace-image",
        files={"receipt": ("rotated_90.png", rotated_bytes, "image/png")},
        data={"force": "false"},
        headers={"X-Role": "staff"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["receipt_id"] == rid
    assert "image_url" in body

    # 4. 验证数据库已更新为新图且旧图不被物理删除
    row = db.get_receipt_row(rid)
    assert row.image_path != str(orig_file)
    assert os.path.exists(row.image_path)
    assert os.path.exists(str(orig_file)), "旧原图必须保留以备审计"

    # 5. 验证审计日志落库 replace_image 动作
    logs = json.loads(row.audit_logs_json or "[]")
    replace_logs = [l for l in logs if l.get("action") == "replace_image"]
    assert len(replace_logs) == 1
    assert replace_logs[0]["field"] == "image_path"
    assert replace_logs[0]["old"] == "initial_receipt.png"
    assert replace_logs[0]["new"] == os.path.basename(row.image_path)

    # 6. 验证识别 Job 已被派发且使用的是换图后的新路径
    assert len(job_stub) == 1
    assert job_stub[0]["receipt_id"] == rid
    assert job_stub[0]["image_path"] == row.image_path


# -------------------------------------------------------------
# 7. AC-FE-01 ~ AC-FE-04: 前端契约与静态语法完整性
# -------------------------------------------------------------
def test_ac_fe_ui_elements_and_static_contract():
    """AC-FE-01 ~ AC-FE-04: 验证 HTML 模板中旋转重识别按钮与 JS 核心旋转函数的契约。"""
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    html_path = os.path.join(repo_root, "demo", "templates", "index.html")
    js_path = os.path.join(repo_root, "demo", "static", "js", "main.js")

    assert os.path.exists(html_path), "index.html 必须存在"
    assert os.path.exists(js_path), "main.js 必须存在"

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # 验证已解析态【按当前视角重新识别】按钮存在
    assert 'id="btnReparseRotated"' in html_content
    # 验证错误态【按当前视角重试】按钮存在
    assert 'id="btnRetryRotated"' in html_content
    # 验证常驻旋转 90° 按钮存在
    assert 'id="btnRotate"' in html_content

    # F5 S4-11：旋转烘焙与重识别实现已随上传域迁至 upload.js（状态变量仍在 main.js）。
    # 静态契约判据改为 main.js + 全部域文件联合全文（与浏览器加载集合同构，判据不放宽）。
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import f4_frontend_sources as _ffs
    js_content = _ffs.frontend_union()

    # 验证核心旋转烘焙与重识别函数
    assert "async function bakeCurrentRotation" in js_content or "bakeCurrentRotation(" in js_content
    assert "submitRotatedImage(" in js_content
    assert "_isBakingRotation" in js_content
    assert "_hasRotatedAfterParse" in js_content
    assert "_hasRotatedFile" in js_content

    # 执行 node 语法检查
    result = subprocess.run(["node", "--check", js_path], capture_output=True, text=True)
    assert result.returncode == 0, f"main.js 存在语法错误: {result.stderr}"

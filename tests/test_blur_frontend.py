# -*- coding: utf-8 -*-
"""D-P0-1 前端链路回归：极模糊单 IMG_5895 上传必须 <1s 被 image_quality_guard
前置拦截（HTTP 400 + quality_warnings=[image_blur]），不再进入 opencode 管线超时。

测试自足：无外部素材时用 PIL 合成确定性极模糊图（Laplacian 方差 <30），
命名 IMG_5895.jpg 模拟 batch1/_previews 真实回归样本。
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))

_TEST_DB = "/tmp/receipt_blur_frontend_test.db"
if os.path.exists(_TEST_DB):
    os.remove(_TEST_DB)
os.environ.setdefault("DB_PATH", _TEST_DB)

import app.db as _db  # noqa: E402

_db.DB_PATH = _TEST_DB
_db._make_engine()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

BLUR_THRESHOLD = 30.0


def _laplacian_variance(path):
    """与 api_receipts._laplacian_variance 同口径的清晰度度量（用于断言样本确实极模糊）。"""
    from PIL import Image
    import numpy as np

    with Image.open(path) as img:
        gray = img.convert("L")
    arr = np.asarray(gray, dtype=np.float32)
    lap = (-4 * arr[1:-1, 1:-1] + arr[:-2, 1:-1] + arr[2:, 1:-1]
           + arr[1:-1, :-2] + arr[1:-1, 2:])
    return float(lap.var())


def _make_blur_img_5895(out_path):
    """合成确定性极模糊 IMG_5895.jpg：白底黑字收据样张 + 重度高斯模糊。

    多轮 GaussianBlur 保证 Laplacian 方差稳定 <30（阈值以下）。
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(img)
    lines = [
        ("祥興食品供應商", 60, 80),
        ("送貨單 NO.20260821", 60, 160),
        ("菜心 5斤 x10 = 50", 60, 280),
        ("牛肉 2斤 x30 = 60", 60, 380),
        ("合計 HK$110", 60, 500),
        ("2026-08-21", 60, 620),
        ("月結 30 天", 60, 740),
    ]
    for text, x, y in lines:
        draw.text((x, y), text, fill="black")
    for _ in range(6):
        img = img.filter(ImageFilter.GaussianBlur(radius=18))
    img.save(out_path, format="JPEG", quality=85)

    var = _laplacian_variance(out_path)
    assert var < BLUR_THRESHOLD, f"合成模糊样本 Laplacian 方差 {var} 应 <{BLUR_THRESHOLD}"
    return var


def test_blur_img_5895_rejected_within_1s():
    """IMG_5895 极模糊上传：<1s 返回 400 + image_blur，confidence<=0.40。"""
    import tempfile

    tmp_dir = tempfile.mkdtemp(prefix="blur5895_")
    blur_path = os.path.join(tmp_dir, "IMG_5895.jpg")
    _make_blur_img_5895(blur_path)

    client = TestClient(app)
    with open(blur_path, "rb") as f:
        start = time.time()
        resp = client.post(
            "/api/upload",
            files={"receipt": ("IMG_5895.jpg", f, "image/jpeg")},
            headers={"X-Role": "staff"},
            data={"codebuddy": "true", "async": "true"},
        )
        elapsed = time.time() - start

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["code"] == "IMAGE_QUALITY_ERROR"
    assert "image_blur" in body.get("quality_warnings", [])
    assert "模糊" in body.get("msg", "")
    assert body.get("confidence", 1.0) <= 0.40
    assert body.get("blur_score", BLUR_THRESHOLD) < BLUR_THRESHOLD
    assert elapsed < 1.0, f"极模糊拦截耗时 {elapsed:.3f}s，应 <1s 快速失败"


def test_blur_batch_rejected_with_image_blur_warning():
    """批量入口同样前置拦截：results 内返回 status=error + quality_warnings=[image_blur]。"""
    import tempfile

    tmp_dir = tempfile.mkdtemp(prefix="blur5895_batch_")
    blur_path = os.path.join(tmp_dir, "IMG_5895.jpg")
    _make_blur_img_5895(blur_path)

    client = TestClient(app)
    with open(blur_path, "rb") as f:
        start = time.time()
        resp = client.post(
            "/api/upload_batch",
            files=[("files", ("IMG_5895.jpg", f, "image/jpeg"))],
            headers={"X-Role": "staff"},
        )
        elapsed = time.time() - start

    assert resp.status_code == 200
    results = resp.json().get("results", [])
    assert len(results) == 1
    r = results[0]
    assert r["status"] == "error"
    assert r.get("quality_warnings") == ["image_blur"]
    assert elapsed < 1.0


def test_clear_image_not_blocked_by_blur_guard():
    """负例对照：清晰热敏小票（Laplacian 方差远超阈值）不被误拦，正常 queued。"""
    clear_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "demo", "samples", "20161001_711_thermal_receipt.jpg")
    if not os.path.exists(clear_path):
        return  # 样本缺失时跳过对照

    var = _laplacian_variance(clear_path)
    assert var >= BLUR_THRESHOLD, f"对照样本应清晰（方差 {var} >= {BLUR_THRESHOLD}）"

    client = TestClient(app)
    with open(clear_path, "rb") as f:
        resp = client.post(
            "/api/upload",
            files={"receipt": ("clear_sample.jpg", f, "image/jpeg")},
            headers={"X-Role": "staff"},
            data={"codebuddy": "true", "async": "true"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "queued"
    assert "job_id" in body


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))

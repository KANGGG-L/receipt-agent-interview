# -*- coding: utf-8 -*-
"""T10 Gap E3+E4：settings_service 类型化读写 + 预处理纠偏。

覆盖：
1. settings 覆盖默认值生效、缺失回退默认、类型化（int/float/bool/str）
2. deskew 对已知角度的旋转图能还原（±1°），±1° 内不纠偏
3. 纠偏后模糊评分（Laplacian 方差）不下降
4. 开关关闭（默认）时 apply_pipeline 原样返回，开关打开才真正处理
5. api_receipts 模糊拦截阈值改经 settings 实时读取

测试自足：PIL 合成确定性图像，独立临时 DB，不依赖真实素材与外部服务。
"""

import os
import sys
import uuid

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))

_TEST_DB = f"/tmp/receipt_settings_preprocess_test_{uuid.uuid4().hex[:8]}.db"
os.environ["DB_PATH"] = _TEST_DB

import app.db as _db  # noqa: E402

_db.DB_PATH = _TEST_DB
_db._make_engine()

from app.services import settings_service  # noqa: E402
from app.services import preprocess  # noqa: E402


# -------------------------------------------------------------
# 合成图像：白底 + 黑色横条（模拟文字行，Laplacian 高、方向可辨）
# -------------------------------------------------------------
def _text_like_image(width=640, height=480):
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    for y in range(60, height - 60, 36):
        draw.rectangle([60, y, width - 60, y + 16], fill=0)
    return img


def _rotate(img, angle):
    return img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)


def _gray_var(gray_arr):
    return preprocess._laplacian_variance_gray(gray_arr)


@pytest.fixture(autouse=True)
def _clean_settings():
    """每个用例独立：清掉本测试用到的键，避免互相污染。"""
    for key in list(settings_service.SETTINGS_DEFAULTS.keys()):
        try:
            _db.set_app_setting(key, None)
        except Exception:
            pass
    yield


# -------------------------------------------------------------
# 1. settings：覆盖 / 缺省回退 / 类型化
# -------------------------------------------------------------
def test_default_fallback_when_missing():
    assert settings_service.get("blur_laplacian_threshold") == 30.0
    assert settings_service.get("eval_candidate_low_confidence") == 0.6
    assert settings_service.get("eval_candidate_max_pending") == 500
    assert settings_service.get("preprocess_enabled") is False


def test_override_takes_effect_typed():
    settings_service.set_value("blur_laplacian_threshold", 42.5)
    assert settings_service.get_float("blur_laplacian_threshold") == 42.5
    settings_service.set_value("eval_candidate_max_pending", 7)
    assert settings_service.get_int("eval_candidate_max_pending") == 7
    assert isinstance(settings_service.get("eval_candidate_max_pending"), int)
    settings_service.set_value("preprocess_enabled", True)
    assert settings_service.get_bool("preprocess_enabled") is True


def test_bool_coercion_from_string():
    settings_service.set_value("preprocess_enabled", "true")
    assert settings_service.get_bool("preprocess_enabled") is True
    settings_service.set_value("preprocess_enabled", "off")
    assert settings_service.get_bool("preprocess_enabled") is False


def test_invalid_value_falls_back_to_default():
    _db.set_app_setting("blur_laplacian_threshold", "not_a_number")
    assert settings_service.get("blur_laplacian_threshold") == 30.0
    _db.set_app_setting("preprocess_enabled", "maybe")
    assert settings_service.get_bool("preprocess_enabled") is False


def test_all_settings_contains_defaults():
    got = settings_service.all_settings()
    for key in settings_service.SETTINGS_DEFAULTS:
        assert key in got


# -------------------------------------------------------------
# 2. deskew：已知角度旋转图还原（±1°）
# -------------------------------------------------------------
def test_deskew_restores_known_rotation():
    for angle in (8.0, -6.0, 12.0):
        img = np.array(_rotate(_text_like_image(), angle))
        fixed, detected = preprocess.deskew(img)
        assert abs(detected - angle) <= 1.5, f"估计角 {detected} 偏离真值 {angle}"
        residual = abs(preprocess._estimate_skew_angle(
            fixed if fixed.ndim == 2 else cv2.cvtColor(fixed, cv2.COLOR_BGR2GRAY)))
        assert residual <= 1.0, f"纠偏后残余倾斜 {residual}° 超过 ±1°（真值 {angle}）"


def test_deskew_deadzone_no_op():
    """±1° 内不纠偏：返回原图与一个小角度。"""
    img = np.array(_rotate(_text_like_image(), 0.4))
    fixed, detected = preprocess.deskew(img)
    assert abs(detected) <= 1.0
    assert fixed is img  # 死区内原样返回，不做重采样


# -------------------------------------------------------------
# 3. 纠偏后模糊评分不下降
# -------------------------------------------------------------
def test_sharpness_not_degraded_after_pipeline():
    angle = 9.0
    rotated = np.array(_rotate(_text_like_image(), angle))
    before = _gray_var(rotated)
    fixed, _ = preprocess.deskew(rotated)
    enhanced = preprocess.enhance(fixed)
    gray = enhanced if enhanced.ndim == 2 else cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
    after = _gray_var(gray)
    assert after >= before * 0.95, \
        f"纠偏后清晰度下降过多：{before:.1f} -> {after:.1f}"


# -------------------------------------------------------------
# 4. 开关：默认 OFF 原样返回；ON 才真正处理
# -------------------------------------------------------------
def test_apply_pipeline_disabled_returns_original(tmp_path):
    settings_service.set_value("preprocess_enabled", False)
    p = tmp_path / "r.jpg"
    _rotate(_text_like_image(), 7).convert("RGB").save(p, format="JPEG", quality=92)
    out_path, meta = preprocess.apply_pipeline(str(p))
    assert out_path == str(p)
    assert meta["applied"] is False


def test_apply_pipeline_enabled_produces_corrected(tmp_path):
    settings_service.set_value("preprocess_enabled", True)
    p = tmp_path / "r.jpg"
    _rotate(_text_like_image(), 10).convert("RGB").save(p, format="JPEG", quality=92)
    out_path, meta = preprocess.apply_pipeline(str(p))
    assert meta["applied"] is True
    assert out_path != str(p) and os.path.exists(out_path)
    assert abs(meta["skew_angle"] - 10.0) <= 2.0


# -------------------------------------------------------------
# 5. 模糊拦截阈值走 settings 实时读取
# -------------------------------------------------------------
def test_blur_threshold_reads_settings():
    from app import api_receipts
    assert api_receipts._blur_threshold() == 30.0
    settings_service.set_value("blur_laplacian_threshold", 55.5)
    assert api_receipts._blur_threshold() == 55.5


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

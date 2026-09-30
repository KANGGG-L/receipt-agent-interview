# -*- coding: utf-8 -*-
"""正交纠偏下线与小角度纠偏硬约束单测。

对齐 P0 规范：
1. 正交纠偏彻底下线：_orthogonal_correct 恒返回 (image, 0)，严禁大角度自动翻转；
2. 小角度纠偏硬约束：
   - 死区：|angle| <= 1.0°，原样返回；
   - 合法区间：1.0° < |angle| <= 15.0°，允许微纠偏；
   - 超限区间：|angle| > 15.0°，严禁自动纠偏，原图返回并标记 reason="angle_exceeds_threshold"。
"""
import cv2
import numpy as np
import pytest

from app.services import preprocess
from app.services.preprocess import (
    DESKEW_DEADZONE_DEG,
    DESKEW_MAX_ANGLE_DEG,
    _orthogonal_correct,
    apply_pipeline,
    deskew,
)


H, W = 600, 400


def _make_receipt():
    """白底 + 顶部集中深色横条的合成单据图。"""
    img = np.full((H, W, 3), 240, dtype=np.uint8)
    for y in (60, 120, 180, 240):
        cv2.rectangle(img, (40, y), (W - 60, y + 26), (30, 30, 30), -1)
    cv2.rectangle(img, (40, 520), (W - 60, 534), (60, 60, 60), -1)
    return img


def test_orthogonal_none_input_passthrough():
    out, angle = _orthogonal_correct(None)
    assert out is None and angle == 0


def test_orthogonal_disabled_never_rotates():
    """正交自动纠偏已彻底下线，任何输入恒返回 angle=0，不改变图像。"""
    img = _make_receipt()
    for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE):
        rotated = cv2.rotate(img, rot)
        out, angle = _orthogonal_correct(rotated)
        assert angle == 0
        assert out is rotated


def test_orthogonal_gray_input_passthrough():
    gray = cv2.cvtColor(_make_receipt(), cv2.COLOR_BGR2GRAY)
    rotated = cv2.rotate(gray, cv2.ROTATE_180)
    out, angle = _orthogonal_correct(rotated)
    assert angle == 0
    assert out is rotated


def test_deskew_deadzone():
    """死区约束：|angle| <= 1.0° 原样返回，不旋转。"""
    img = _make_receipt()
    # 模拟估算角度在死区内
    out, angle = deskew(img)
    # 对于未旋转图，估算角接近 0 <= 1.0
    assert abs(angle) <= DESKEW_DEADZONE_DEG
    assert out is img or np.array_equal(out, img)


def test_deskew_exceeds_threshold_untouched(monkeypatch):
    """超限约束：|angle| > 15.0° 严禁自动纠偏，原图返回。"""
    img = _make_receipt()
    monkeypatch.setattr(preprocess, "_estimate_skew_angle", lambda g: 16.5)
    out, angle = deskew(img)
    assert angle == 16.5
    # 超限原样返回，矩阵未变
    assert out is img


def test_deskew_valid_window(monkeypatch):
    """合法区间：1.0° < |angle| <= 15.0° 执行微纠偏。"""
    img = _make_receipt()
    monkeypatch.setattr(preprocess, "_estimate_skew_angle", lambda g: 8.5)
    out, angle = deskew(img)
    assert angle == 8.5
    assert out.shape == img.shape
    # 发生了旋转变换
    assert not np.array_equal(out, img)


def test_apply_pipeline_exceeds_threshold(tmp_path, monkeypatch):
    """超限区间集成测试：偏角超 15° 时，原图返回，reason='angle_exceeds_threshold'。"""
    from app.services import settings_service
    monkeypatch.setattr(settings_service, "get_bool", lambda k, d=False: True if k == "preprocess_enabled" else False)
    monkeypatch.setattr(preprocess, "_estimate_skew_angle", lambda g: 25.0)

    img = _make_receipt()
    img_path = str(tmp_path / "test_skew.jpg")
    cv2.imwrite(img_path, img)

    out_path, meta = apply_pipeline(img_path)
    assert out_path == img_path
    assert meta["applied"] is False
    assert meta["deskew_skipped"] is True
    assert meta["reason"] == "angle_exceeds_threshold"
    assert meta["orthogonal_angle"] == 0


def test_apply_pipeline_deadzone(tmp_path, monkeypatch):
    """死区集成测试：偏角 <= 1° 时，deskew 不旋转，仅 enhance，skew_angle 准确记录。"""
    from app.services import settings_service
    monkeypatch.setattr(settings_service, "get_bool", lambda k, d=False: True if k == "preprocess_enabled" else False)
    monkeypatch.setattr(preprocess, "_estimate_skew_angle", lambda g: 0.5)

    img = _make_receipt()
    img_path = str(tmp_path / "test_deadzone.jpg")
    cv2.imwrite(img_path, img)

    out_path, meta = apply_pipeline(img_path)
    assert out_path.endswith("_prep.jpg")
    assert meta["applied"] is True
    assert meta["skew_angle"] == 0.5
    assert meta["orthogonal_angle"] == 0
    assert meta["reason"] == ""

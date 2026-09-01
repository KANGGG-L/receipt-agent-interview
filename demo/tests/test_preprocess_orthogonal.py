# -*- coding: utf-8 -*-
"""正交矫正（preprocess._orthogonal_correct）单测——补齐 P1-2 覆盖缺口。

策略对齐实现注释：四假设（0/90/180/270）行投影方差取最大者为水平组，
水平组内以重心最靠上（cy 最小）挑直立方向。合成"收据样"图（白底 + 顶部
集中深色横条）满足：有文字行的方向行方差远大于垂直方向（> _ORTHO_RATIO_FILTER），
顶部重心比底部重可区分 0/180。
"""
import cv2
import numpy as np
import pytest

from app.services.preprocess import _orthogonal_correct


H, W = 600, 400


def _make_receipt():
    """白底 + 顶部集中深色横条（表头重心偏上）的合成单据图。"""
    img = np.full((H, W, 3), 240, dtype=np.uint8)
    for y in (60, 120, 180, 240):
        cv2.rectangle(img, (40, y), (W - 60, y + 26), (30, 30, 30), -1)
    # 底部仅一条细横线，保证重心显著偏上
    cv2.rectangle(img, (40, 520), (W - 60, 534), (60, 60, 60), -1)
    return img


def test_orthogonal_none_input_passthrough():
    out, angle = _orthogonal_correct(None)
    assert out is None and angle == 0


def test_orthogonal_blank_image_falls_back():
    blank = np.full((H, W, 3), 200, dtype=np.uint8)
    out, angle = _orthogonal_correct(blank)
    assert angle == 0
    assert out.shape == blank.shape


def test_orthogonal_upright_untouched():
    img = _make_receipt()
    out, angle = _orthogonal_correct(img)
    assert angle == 0
    assert out is img


def test_orthogonal_180_corrected():
    img = _make_receipt()
    rotated = cv2.rotate(img, cv2.ROTATE_180)
    out, angle = _orthogonal_correct(rotated)
    assert angle == 180
    assert out.shape == rotated.shape
    # 纠正后与原图一致（BGR 数值级）
    assert np.array_equal(out, img)


@pytest.mark.parametrize("rot,expect", [
    # 期望值 = 恢复摆正所需的顺时针角度：输入被顺时针转 90 → 需逆时针（=顺时针 270）恢复
    (cv2.ROTATE_90_CLOCKWISE, 270),
    (cv2.ROTATE_90_COUNTERCLOCKWISE, 90),
])
def test_orthogonal_90_270_corrected(rot, expect):
    img = _make_receipt()
    rotated = cv2.rotate(img, rot)
    out, angle = _orthogonal_correct(rotated)
    assert angle == expect
    # 90/270 纠正后形状应换回竖版（cv2.rotate 换边语义）
    assert out.shape == img.shape


def test_orthogonal_low_variance_uniform_falls_back():
    """低方差均匀图不判定（OTSU 后行方差 ~0 < _ORTHO_ROWVAR_MIN → 回落 0，不阻断）。"""
    uniform = np.full((H, W, 3), 128, dtype=np.uint8)
    out, angle = _orthogonal_correct(uniform)
    assert angle == 0
    assert out is uniform


def test_orthogonal_gray_input_supported():
    """灰度输入同样可用（ndim==2 分支）。"""
    gray = cv2.cvtColor(_make_receipt(), cv2.COLOR_BGR2GRAY)
    rotated = cv2.rotate(gray, cv2.ROTATE_180)
    out, angle = _orthogonal_correct(rotated)
    assert angle == 180
    assert out.ndim == 2

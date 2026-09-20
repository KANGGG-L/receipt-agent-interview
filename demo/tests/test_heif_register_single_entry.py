# -*- coding: utf-8 -*-
"""HEIF 解码器注册收敛为单一加固入口（三处裸注册只改了一处的收口验证）。

背景（只读审计结论，本次复核后确认）：
`pillow_heif.register_heif_opener()` 全仓原有三处调用点，只有
`app/services/image_web._register_heif_opener` 是加固版（加锁、只注册 HEIF、失败只
WARN 一次并记录 `_HEIF_REGISTER_ERROR`、由调用方按「输入是否真需要 HEIF 解码」收窄
后果）；另两处是裸注册：

- `app/api_receipts._laplacian_variance`（极模糊前置拦截）
- `app/services/preprocess._imread`（上传预处理纠偏读图）

两者的 `except Exception: pass` 让 pillow-heif 缺失/版本不兼容时彻底不可观测：
HEIC 打不开 → 被外层 except 吞掉 → 返回 None → 「极模糊前置拦截」对 HEIC 静默失效，
而非 HEIC 输入一切正常；预处理链路同理整体静默降级成 no-op。

本文件锁三件事：
1. 两处都改为调用 image_web 的加固入口（用间谍函数证明「真的被调用」）；
2. 注册失败时至少有一条 WARN（可观测），且非 HEIC 输入行为不变（仍返回数值）；
3. 源码里不再存在裸注册写法（静态守卫，防再次漂移）。

不写 live 库：本文件只读图/算方差，不碰 db；沿用 isolated_db 惯例仅为防误触。
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_heif_entry_test.db")

import pytest
from PIL import Image

from app.services import image_web

_REPO_DEMO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _reset_heif_state(monkeypatch):
    """把 image_web 的 HEIF 注册状态复位，模拟「本进程内尚未注册过」。"""
    monkeypatch.setattr(image_web, "_HEIF_REGISTERED", False)
    monkeypatch.setattr(image_web, "_HEIF_WARNED", False)
    monkeypatch.setattr(image_web, "_HEIF_REGISTER_ERROR", None)


def _make_jpeg(path):
    Image.new("RGB", (40, 30), (120, 130, 140)).save(str(path), "JPEG")
    return str(path)


def _make_fake_heic(path):
    """非真 HEIC：只要证明「打开失败时返回 None」即可，不需要真解码器。"""
    with open(str(path), "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32)
    return str(path)


# ------------------------------------------------------------------
# 1. 接线：两处都真的调用 image_web 的加固入口
# ------------------------------------------------------------------
def test_laplacian_variance_uses_image_web_entry(monkeypatch):
    """api_receipts 的清晰度计算必须委托 image_web._register_heif_opener（间谍证明）。"""
    from app import api_receipts

    calls = []

    def _spy():
        calls.append(1)
        return True

    monkeypatch.setattr(image_web, "_register_heif_opener", _spy)
    # 路径不存在：外层实现会回 None，但注册调用必须先发生
    assert api_receipts._laplacian_variance("/tmp/__no_such_receipt_image__.heic") is None
    assert len(calls) == 1


def test_preprocess_imread_uses_image_web_entry(monkeypatch):
    """preprocess 的读图必须委托 image_web._register_heif_opener（间谍证明）。"""
    from app.services import preprocess

    calls = []

    def _spy():
        calls.append(1)
        return True

    monkeypatch.setattr(image_web, "_register_heif_opener", _spy)
    # 非图片文件：PIL 与 cv2 都失败 → None；但注册调用必须已发生
    assert preprocess._imread("/tmp/__no_such_receipt_image__.heic") is None
    assert len(calls) == 1


# ------------------------------------------------------------------
# 2. 注册失败可观测 + 非 HEIC 行为不变
# ------------------------------------------------------------------
def test_register_failure_is_observable_and_non_heic_unaffected(monkeypatch, tmp_path, caplog):
    """pillow_heif 不可用时：有 WARN；非 HEIC 图仍算得出清晰度（不阻断主链路）。"""
    from app import api_receipts

    pillow_heif = pytest.importorskip("pillow_heif")

    def _boom():
        raise ImportError("模拟 pillow-heif 不可用")

    monkeypatch.setattr(pillow_heif, "register_heif_opener", _boom)
    _reset_heif_state(monkeypatch)

    caplog.set_level(logging.WARNING, logger="image_web")

    jpg = _make_jpeg(tmp_path / "clear.jpg")
    score = api_receipts._laplacian_variance(jpg)
    # 非 HEIC 输入不受解码器注册失败影响：仍返回数值而非 None
    assert isinstance(score, float)

    heic_like = _make_fake_heic(tmp_path / "broken.heic")
    # HEIC 打开失败 → 仍返回 None（保留「可选依赖缺失不阻断主链路」的既有语义）
    assert api_receipts._laplacian_variance(heic_like) is None

    warns = [r for r in caplog.records if "pillow_heif.register_heif_opener 失败" in r.getMessage()]
    assert warns, "注册失败必须有可观测 WARN（旧裸注册写法为 except: pass，无任何输出）"
    # 只 WARN 一次：再次调用不重复刷屏
    assert len(warns) == 1


def test_preprocess_register_failure_is_observable(monkeypatch, tmp_path, caplog):
    """preprocess 读图路径同样复用加固入口：失败时有 WARN。"""
    from app.services import preprocess

    pillow_heif = pytest.importorskip("pillow_heif")

    def _boom():
        raise ImportError("模拟 pillow-heif 不可用")

    monkeypatch.setattr(pillow_heif, "register_heif_opener", _boom)
    _reset_heif_state(monkeypatch)
    caplog.set_level(logging.WARNING, logger="image_web")

    jpg = _make_jpeg(tmp_path / "prep.jpg")
    arr = preprocess._imread(jpg)
    # 非 HEIC 仍能读成 BGR ndarray（不因注册失败而降级）
    assert arr is not None and arr.ndim == 3

    assert any("pillow_heif.register_heif_opener 失败" in r.getMessage()
               for r in caplog.records)


# ------------------------------------------------------------------
# 3. 静态守卫：两处不得再出现裸注册
# ------------------------------------------------------------------
def test_no_bare_registration_remains():
    """api_receipts.py / preprocess.py 不得再自行 import/注册 pillow_heif。"""
    for rel in ("app/api_receipts.py", "app/services/preprocess.py"):
        with open(os.path.join(_REPO_DEMO, rel), encoding="utf-8") as f:
            src = f.read()
        assert "import pillow_heif" not in src, (
            f"{rel} 仍自行 import pillow_heif，应委托 image_web 单一入口")
        assert "pillow_heif.register_heif_opener" not in src, (
            f"{rel} 仍自行注册 HEIF 解码器，应委托 image_web 单一入口")

# -*- coding: utf-8 -*-
"""评测脚本 app_settings 读取收敛为单一实现（C 项）。

原状：`build_evalset.py` 与 `gen_gt_candidates.py` 各有一份 `_settings_value`，
两份都是 `except Exception: return default` —— 脚本在别的 cwd 跑、或 app 依赖缺失
导致 sys.path 插入失败时，阈值（`evalset_min_short_side` / `gt_min_short_side` /
`gt_preview_long_side` / `gt_jpeg_quality` 等）会**静默退回硬编码默认值**，于是评测
语料按默认值而不是库内现值构建，日志里毫无线索。

收口后：单一实现 `demo/scripts/_settings.py`，两侧 import；回落至少 WARN 一次。
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_scripts_settings_test.db")

import pytest

import _settings


@pytest.fixture(autouse=True)
def _clear_warned():
    _settings._WARNED_KEYS.clear()
    yield
    _settings._WARNED_KEYS.clear()


# ------------------------------------------------------------------
# 1. 单一实现：两侧都指向同一个函数对象
# ------------------------------------------------------------------
def test_both_scripts_use_the_single_implementation():
    import build_evalset
    import gen_gt_candidates

    assert build_evalset._settings_value is _settings.get
    assert gen_gt_candidates._settings is _settings


def test_no_duplicate_settings_value_left():
    """两份复制实现必须消失（防「修了一份漏另一份」）。"""
    demo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("scripts/build_evalset.py", "scripts/gen_gt_candidates.py"):
        with open(os.path.join(demo, rel), encoding="utf-8") as f:
            src = f.read()
        assert "def _settings_value" not in src, f"{rel} 仍保留本地 _settings_value 实现"
        assert "_settings" in src, f"{rel} 未使用 scripts/_settings 单一实现"


# ------------------------------------------------------------------
# 2. 正常路径：两侧同键同值
# ------------------------------------------------------------------
def test_both_sides_resolve_same_value(monkeypatch):
    from app.services import settings_service

    seen = []

    def _fake_get(key, default=None):
        seen.append(key)
        return 1234

    monkeypatch.setattr(settings_service, "get", _fake_get)
    import build_evalset
    import gen_gt_candidates

    assert build_evalset._settings_value("gt_min_short_side", 1000) == 1234
    assert gen_gt_candidates._settings.get("gt_min_short_side", 1000) == 1234
    assert seen == ["gt_min_short_side", "gt_min_short_side"]


# ------------------------------------------------------------------
# 3. 回落可观测：默认值生效 + WARN 一次（不刷屏）
# ------------------------------------------------------------------
def test_fallback_is_observable_and_warns_once(monkeypatch, caplog):
    from app.services import settings_service

    def _boom(key, default=None):
        raise RuntimeError("模拟 app_settings 不可读")

    monkeypatch.setattr(settings_service, "get", _boom)
    caplog.set_level(logging.WARNING, logger="scripts._settings")

    assert _settings.get("gt_jpeg_quality", 85) == 85
    assert _settings.get("gt_jpeg_quality", 85) == 85
    warns = [r for r in caplog.records if "回退硬编码默认值" in r.getMessage()]
    assert len(warns) == 1, "同一键的回落只提示一次"
    assert "gt_jpeg_quality" in warns[0].getMessage()


def test_app_import_failure_falls_back_with_warning(monkeypatch, caplog):
    """app 不可导入（脚本在别的 cwd 跑的真实故障形态）同样回默认值并告警。"""
    def _boom():
        raise ImportError("模拟 sys.path 插入失败")

    monkeypatch.setattr(_settings, "_ensure_app_on_path", _boom)
    caplog.set_level(logging.WARNING, logger="scripts._settings")

    assert _settings.get("evalset_min_short_side", 1000) == 1000
    assert any("回退硬编码默认值" in r.getMessage() for r in caplog.records)

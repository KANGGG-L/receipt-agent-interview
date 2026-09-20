# -*- coding: utf-8 -*-
"""评测脚本共用的 app_settings 缺省读取（单一实现）。

why: `build_evalset.py` 与 `gen_gt_candidates.py` 各自复制了一份 `_settings_value`
（同一根因两份实现），两份都是 `except Exception: return default`：脚本在别的 cwd 跑、
或 app 依赖缺失导致 sys.path 插入失败时，阈值（`evalset_min_short_side` /
`gt_min_short_side` / `gt_preview_long_side` / `gt_jpeg_quality` 等）会**静默退回
硬编码默认值**，于是评测语料按默认值而不是库内现值构建，且日志里没有任何线索。
收口为单一实现，并让回落至少 WARN 一次（每个键一次，避免刷屏）。
"""

import logging
import os
import sys

logger = logging.getLogger("scripts._settings")

_HERE = os.path.dirname(os.path.abspath(__file__))
# demo 根目录（本文件位于 demo/scripts/）
DEMO_DIR = os.path.abspath(os.path.join(_HERE, ".."))

# 已经告警过的键：同类回落只提示一次（脚本会多次调用同一个键）
_WARNED_KEYS = set()


def _ensure_app_on_path():
    """保证 `app.*` 可导入（脚本被从任意 cwd 调用时 sys.path 未必含 demo/）。"""
    if DEMO_DIR not in sys.path:
        sys.path.insert(0, DEMO_DIR)


def get(key, default=None):
    """读取 app_settings 的 `key`；任何失败回退 `default` 并 WARN（每键一次）。"""
    try:
        _ensure_app_on_path()
        from app.services import settings_service
        return settings_service.get(key, default)
    except Exception as e:
        if key not in _WARNED_KEYS:
            _WARNED_KEYS.add(key)
            logger.warning(
                "[WARN] 读取 app_settings[%s] 失败，回退硬编码默认值 %r"
                "（评测语料将按该默认值构建，可能与库内设置不一致）: %s",
                key, default, e)
        return default

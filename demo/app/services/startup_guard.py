# -*- coding: utf-8 -*-
from __future__ import annotations
"""启动期自检：AI 管线相关的危险配置在生产环境直接拒绝启动。"""

import logging
import os

_OFF_WORDS = ("", "0", "false", "no", "off")


def mock_qwen_flash_requested() -> bool:
    return os.environ.get("MOCK_QWEN_FLASH", "").strip().lower() not in _OFF_WORDS


def enforce_ai_startup_checks() -> None:
    """MOCK_QWEN_FLASH 是历史测试后门（会让任何图片都返回固定假数据）。

    运行时代码已不再读取它，测试桩放在 tests 里；生产环境一旦检测到这个变量说明部署
    配置被污染，拒绝启动，避免运维误以为假数据路径仍然生效。
    """
    if not mock_qwen_flash_requested():
        return
    from app import auth
    if auth.is_production_env():
        raise RuntimeError(
            "检测到测试专用环境变量 MOCK_QWEN_FLASH，生产环境不允许设置它。"
            "请从部署环境里删掉这个变量后再启动。")
    logging.getLogger("startup").warning(
        "环境变量 MOCK_QWEN_FLASH 已不再生效（假数据桩只存在于测试代码中），建议从 .env 删除")

# -*- coding: utf-8 -*-
"""demo/ 顶层夹具：注册 `--run-eval` 开关与 eval 标记。

评测语料（demo/evalsets/，含真实商户收据图与 GT）**不在版本库内**，因此所有
依赖真实语料的评测用例默认跳过，保证他人克隆仓库后裸跑 pytest 不报错：

    pytest demo/                  # eval 用例跳过
    pytest demo/ --run-eval       # 本机有语料时才真正跑评测用例

即便传了 --run-eval，语料缺失（manifest.csv 不存在）时仍自动 skip。
"""

import os
import sys

import pytest

# demo 应用根目录（app.*）与脚本目录（build_evalset / run_eval）
DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(DEMO_DIR, "scripts")
for _p in (SCRIPTS_DIR, DEMO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 评测语料目录（真实收据图与 GT，已 gitignore）
EVALSET_DIR = os.path.join(DEMO_DIR, "evalsets")
MANIFEST_PATH = os.path.join(EVALSET_DIR, "manifest.csv")


def pytest_addoption(parser):
    parser.addoption(
        "--run-eval",
        action="store_true",
        default=False,
        help="运行依赖 demo/evalsets/ 真实语料的评测用例（语料缺失时仍自动 skip）",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "eval: 依赖 demo/evalsets/ 真实语料，需 --run-eval 且语料存在时才执行"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-eval"):
        return
    skip_eval = pytest.mark.skip(reason="需要 --run-eval 且 demo/evalsets/ 语料存在")
    for item in items:
        if "eval" in item.keywords:
            item.add_marker(skip_eval)


@pytest.fixture
def evalset_dir():
    """真实评测语料目录；语料缺失（无 manifest.csv）时自动 skip。"""
    if not os.path.exists(MANIFEST_PATH):
        pytest.skip(f"评测语料缺失：{MANIFEST_PATH}（真实收据属业务数据，不入库）")
    return EVALSET_DIR

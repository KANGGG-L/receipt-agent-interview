# -*- coding: utf-8 -*-
"""P2-T15 (G7): 前端治理面板自动化回归套件。

将既有 3 个基于 Node vm 沙箱的前端测试脚本纳入 pytest 自动化回归：
  1. demo/tests/test_memory_metrics_panel.js（度量看板渲染与样式消费方）
  2. demo/tests/test_vendor_memory_governance_panel.js（记忆治理区与删除操作）
  3. demo/tests/frontend_pending_memory_badge_harness.js（待审角标与流转状态）

同时包含突变体对抗反证（Counterproof）：
在 /tmp 沙箱副本中故意注入破坏（例如破坏 written_by 字段渲染或 mm-* 样式类），
执行 harness 并断言其退出码非 0，反证测试守卫具有真实判别力，防假绿。
"""

import os
import shutil
import subprocess
import tempfile
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
_HARNESS_DIR = os.path.join(_DEMO_DIR, "tests")

_METRICS_PANEL_JS = os.path.join(_HARNESS_DIR, "test_memory_metrics_panel.js")
_CREDIT_WORKBENCH_JS = os.path.join(_HARNESS_DIR, "test_credit_workbench_harness.js")
_GOVERNANCE_PANEL_JS = os.path.join(_HARNESS_DIR, "test_vendor_memory_governance_panel.js")
_BADGE_HARNESS_JS = os.path.join(_HARNESS_DIR, "frontend_pending_memory_badge_harness.js")


def _run_node_harness(script_path, *extra_args):
    """运行 Node harness 脚本，返回 subprocess.CompletedProcess。"""
    cmd = ["node", script_path] + list(extra_args)
    return subprocess.run(
        cmd,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_memory_metrics_panel_harness_passes():
    """验证度量看板 JS harness 在 pytest 下全部通过。"""
    res = _run_node_harness(_METRICS_PANEL_JS)
    assert res.returncode == 0, (
        f"test_memory_metrics_panel.js failed with exit code {res.returncode}\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    assert "PASS" in res.stdout
    assert "0 failed" in res.stdout


def test_credit_workbench_harness_passes():
    """U03：信用工作台 JS harness 全过（金额纪律/payload 校验/D-03 口径文案）。"""
    res = _run_node_harness(_CREDIT_WORKBENCH_JS)
    assert res.returncode == 0, (
        f"test_credit_workbench_harness.js failed with exit code {res.returncode}\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    assert "PASS" in res.stdout
    assert "0 failed" in res.stdout


def test_vendor_memory_governance_panel_harness_passes():
    """验证记忆治理区 JS harness 在 pytest 下全部通过。"""
    res = _run_node_harness(_GOVERNANCE_PANEL_JS)
    assert res.returncode == 0, (
        f"test_vendor_memory_governance_panel.js failed with exit code {res.returncode}\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    assert "PASS" in res.stdout
    assert "0 failed" in res.stdout


def test_frontend_pending_memory_badge_harness_passes():
    """验证待审角标与流转状态 JS harness 在 pytest 下全部通过。"""
    res = _run_node_harness(_BADGE_HARNESS_JS)
    assert res.returncode == 0, (
        f"frontend_pending_memory_badge_harness.js failed with exit code {res.returncode}\n"
        f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    )
    assert "PASS" in res.stdout
    assert "0 failed" in res.stdout


def test_governance_panel_mutation_counterproof():
    """突变体反证：故意破坏 engine_admin.js 中的 written_by 字段渲染，harness 必须翻红报错。"""
    src_file = os.path.join(_DEMO_DIR, "static", "js", "engine_admin.js")
    with open(src_file, "r", encoding="utf-8") as f:
        src = f.read()

    # 突变：将 written_by 替换为未知字段，破坏逐字段渲染契约
    mutant_src = src.replace("written_by", "broken_written_by_mutant")
    assert mutant_src != src, "突变体替换失败，未找到 target"

    tmp_dir = tempfile.mkdtemp(prefix="mutant_gov_")
    try:
        mutant_path = os.path.join(tmp_dir, "engine_admin.js")
        with open(mutant_path, "w", encoding="utf-8") as f:
            f.write(mutant_src)

        res = _run_node_harness(_GOVERNANCE_PANEL_JS, mutant_path)
        assert res.returncode != 0, "突变体未翻红！harness 失去判别力！"
        assert "FAIL" in res.stdout
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_metrics_panel_mutation_counterproof():
    """突变体反证：故意篡改度量看板 mm-rate 类名，破坏样式消费方断言，harness 必须翻红报错。"""
    src_file = os.path.join(_DEMO_DIR, "static", "js", "engine_admin.js")
    with open(src_file, "r", encoding="utf-8") as f:
        src = f.read()

    # 突变：将 mm-rate 破坏为 mm-broken-rate
    mutant_src = src.replace("mm-rate", "mm-broken-rate")
    assert mutant_src != src, "突变体替换失败，未找到 target"

    tmp_dir = tempfile.mkdtemp(prefix="mutant_metrics_")
    try:
        mutant_path = os.path.join(tmp_dir, "engine_admin.js")
        with open(mutant_path, "w", encoding="utf-8") as f:
            f.write(mutant_src)

        res = _run_node_harness(_METRICS_PANEL_JS, mutant_path)
        assert res.returncode != 0, "突变体未翻红！harness 失去判别力！"
        assert "FAIL" in res.stdout
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

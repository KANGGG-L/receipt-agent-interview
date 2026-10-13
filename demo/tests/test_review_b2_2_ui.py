# -*- coding: utf-8 -*-
"""批 1 / B2-2：每日消耗提交必须带 request_id，失败重试复用，成功后换新。"""

import json
import os
import shutil
import subprocess

import pytest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_HARNESS = os.path.join(_TESTS, "frontend_batch01_harness.js")


def test_daily_consumption_request_id_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 node")
    proc = subprocess.run([node, _HARNESS], capture_output=True, text=True, timeout=60)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, "脚手架无 JSON：stdout=%s stderr=%s" % (proc.stdout, proc.stderr)
    result = json.loads(lines[-1])
    assert proc.returncode == 0, result.get("failures")
    assert result["fail"] == 0, result["failures"]
    names = " ".join(result.get("failures") or [])
    assert "request_id" not in names

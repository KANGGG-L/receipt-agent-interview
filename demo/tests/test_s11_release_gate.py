# -*- coding: utf-8 -*-
"""S11：缺任一硬门禁即阻启用；开关默认关；停新收费不停止善后能力。"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.billing import release_gate as s11  # noqa: E402


def _all_gates():
    return {n: {"ok": True, "evidence": "evidence/%s.json" % n}
            for n in s11.REQUIRED_GATES}


def test_missing_each_gate_blocks():
    for name in s11.REQUIRED_GATES:
        gates = _all_gates()
        gates[name] = {"ok": False, "evidence": ""}
        out = s11.evaluate(gates)
        assert out["ready"] is False
        assert name in out["missing"]


def test_gate_without_evidence_blocks():
    gates = _all_gates()
    gates["policy_version"] = {"ok": True, "evidence": ""}
    out = s11.evaluate(gates)
    assert out["ready"] is False
    assert "policy_version" in out["missing_evidence"]


def test_all_gates_ready():
    out = s11.evaluate(_all_gates())
    assert out["ready"] is True
    assert out["missing"] == [] and out["missing_evidence"] == []


def test_flags_default_off_and_stop_keeps_aftercare(monkeypatch):
    monkeypatch.delenv("BILLING_ACCEPT_NEW_CHECKOUT", raising=False)
    monkeypatch.delenv("BILLING_AUTO_RENEW", raising=False)
    flags = s11.runtime_flags()
    assert flags["billing_accept_new_checkout"] is False
    assert flags["billing_auto_renew"] is False
    assert flags["verified_inbox"] and flags["reconcile"] and flags["refund_aftercare"]
    # 停新收费：善后能力不随开关关闭
    out = s11.evaluate(_all_gates(), flags=dict(flags,
                                                billing_accept_new_checkout=False))
    assert out["stop_policy"]["keep_working"] == list(s11.ALWAYS_ON_AFTER_STOP)

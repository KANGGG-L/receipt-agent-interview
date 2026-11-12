# -*- coding: utf-8 -*-
"""S11 商业启用硬门禁（预检查；不授权真实启用）。

- 任一必需门禁缺失/未通过 → `ready=false`，逐项列出。
- 运行开关默认全关；`billing_accept_new_checkout` / `billing_auto_renew` 可独立关闭，
  但 verified inbox / reconcile / 退款善后不受影响（它们不是收费入口开关）。
- 本模块只做判定与清单，不做真实启用、不读真实凭据。
"""

import os

REQUIRED_GATES = (
    "real_merchant",
    "endpoint_and_signing_key",
    "policy_version",
    "restore_and_reconcile",
    "consumer_availability",
    "ai_12s_and_quality",
    "major_risks_closed",
    "d06_fully_signed",
    "s10_manual_verification",
    "v03_ops_rehearsal",
    "h07_history_scope",
)

# 与收费入口无关、启用与否都必须继续工作的能力（停收不等于停善后）。
ALWAYS_ON_AFTER_STOP = ("verified_inbox", "reconcile", "refund_aftercare")


def _flag(name, default="0"):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def runtime_flags():
    return {
        "billing_accept_new_checkout": _flag("BILLING_ACCEPT_NEW_CHECKOUT"),
        "billing_auto_renew": _flag("BILLING_AUTO_RENEW"),
        "verified_inbox": True,
        "reconcile": True,
        "refund_aftercare": True,
    }


def evaluate(gates, flags=None):
    """gates: {gate_name: {"ok": bool, "evidence": str}}；返回裁决。"""
    missing = []
    blocked = []
    detail = {}
    for name in REQUIRED_GATES:
        g = (gates or {}).get(name) or {}
        ok = bool(g.get("ok"))
        evidence = str(g.get("evidence") or "").strip()
        detail[name] = {"ok": ok, "evidence": evidence}
        if not ok:
            missing.append(name)
        elif not evidence:
            blocked.append(name)
    ready = not missing and not blocked
    return {
        "schema": "s11-release-gate.v1",
        "ready": ready,
        "missing": missing,
        "missing_evidence": blocked,
        "gates": detail,
        "flags": flags if flags is not None else runtime_flags(),
        "stop_policy": {
            "stop_accept_new_checkout": True,
            "keep_working": list(ALWAYS_ON_AFTER_STOP),
        },
    }

# -*- coding: utf-8 -*-
"""S01 商业 policy 加载（fail-closed）：任一 D06 必需字段未签发即拒绝，不设默认值。"""

POLICY_REQUIRED_FIELDS = (
    "trial.eligibility", "trial.start_rule", "trial.bind_card_required",
    "plan.tiers", "channel.provider", "renewal.auto_renew",
    "cancellation.effective_at", "refund.policy",
)


class PolicyNotConfigured(Exception):
    def __init__(self, missing):
        super().__init__("billing policy 未签发或字段缺失: %s" % ", ".join(missing))
        self.code = "POLICY_NOT_CONFIGURED"
        self.missing = list(missing)


def _env_policy():
    """受控配置来源 v1：BILLING_POLICY_JSON 环境变量（签发版本/审批人随 JSON 带）。"""
    import json
    import os
    raw = os.environ.get("BILLING_POLICY_JSON", "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def load_policy():
    """返回 (policy_dict, policy_ref)；未签发/缺字段抛 PolicyNotConfigured。

    policy_ref = 版本+审批人+effective_at（SA09 配置来源可追）。
    """
    obj = _env_policy()
    if obj is None:
        raise PolicyNotConfigured(["<entire policy: not issued>"])
    def _has(path):
        cur = obj
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return False
            cur = cur[part]
        return True
    missing = [f for f in POLICY_REQUIRED_FIELDS if not _has(f)]
    ref = obj.get("policy_ref") or {}
    if not ref.get("version") or not ref.get("approved_by"):
        missing.append("policy_ref(version/approved_by)")
    if missing:
        raise PolicyNotConfigured(missing)
    return obj, "%s@%s" % (ref.get("version"), ref.get("approved_by"))

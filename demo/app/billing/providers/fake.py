# -*- coding: utf-8 -*-
"""S01 FakeAdapter：进程内确定性实现（测试/演示专用，标识明确；不含真实渠道逻辑）。

- create_checkout 返回确定性 checkout id（按 order_no 派生）；
- settle(order) 产出 verified 事件（provider_payment_id 确定性派生）；
- verify_event 幂等：同事件重复验证返回同事实。
真实渠道（vendor_a）未选未授权：占位 Adapter 一律 NotImplemented。
合成金额仅进私有证据，对外报告 $***（S6）。
"""

import hashlib


class FakeAdapter:
    provider = "fake"
    environment = "sandbox"

    def create_checkout(self, order_no, amount_minor, currency, idempotency_key):
        h = hashlib.sha256(("%s|%s|%s|%s" % (order_no, amount_minor, currency,
                                             idempotency_key)).encode()).hexdigest()[:24]
        return {"checkout_id": "fake-co-" + h, "provider": self.provider,
                "environment": self.environment, "amount_minor": int(amount_minor),
                "currency": currency}

    def settle(self, order_no, amount_minor, currency):
        h = hashlib.sha256(("settle|%s|%s" % (order_no, amount_minor)).encode()).hexdigest()[:24]
        return {"provider_payment_id": "fake-pay-" + h, "fact_kind": "payment_succeeded",
                "amount_minor": int(amount_minor), "currency": currency,
                "provider": self.provider, "environment": self.environment}

    def refund(self, provider_payment_id, amount_minor):
        h = hashlib.sha256(("refund|%s|%s" % (provider_payment_id, amount_minor)).encode())
        return {"provider_refund_id": "fake-ref-" + h.hexdigest()[:24],
                "fact_kind": "refund_succeeded", "amount_minor": int(amount_minor)}

    # 查询接口只读：结果未知时返回 unknown，调用方必须先查询、不得再次发起扣款。
    def query_checkout(self, order_no, checkout_id=None):
        return {"status": "unknown", "checkout_id": checkout_id, "order_no": order_no}

    def query_payment(self, order_no, provider_payment_id=None):
        return {"status": "unknown", "order_no": order_no,
                "provider_payment_id": provider_payment_id}

    def query_refund(self, provider_request_key):
        return {"status": "unknown", "provider_request_key": provider_request_key}


def get_adapter(name):
    if name == "fake":
        return FakeAdapter()
    raise NotImplementedError(
        "渠道 %r 未接入（真实渠道未选定/未授权，占位 Adapter 不假装支持）" % name)

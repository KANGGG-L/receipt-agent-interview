# -*- coding: utf-8 -*-
"""业务拒绝：带错误码和人话提示，由 main.py 的统一处理器转成 HTTP 响应。"""


class DomainError(Exception):
    status_code = 422
    code = "BUSINESS_RULE"

    def __init__(self, msg, code=None, data=None, status_code=None):
        super().__init__(msg)
        self.msg = msg
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.data = data or {}

    def payload(self):
        return {"status": "error", "code": self.code, "msg": self.msg, "data": self.data}


class UnitConversionMissing(DomainError):
    code = "UNIT_CONVERSION_MISSING"

    def __init__(self, pairs):
        """pairs: [{"from_unit","to_unit","item_name"}]，按（单位对）去重后的完整清单。"""
        first = pairs[0]
        msg = ("「%s」和库存单位「%s」之间还没有换算关系，请老板到「设置」里添加换算，"
               "或把这一行的单位改成「%s」" % (first["from_unit"], first["to_unit"], first["to_unit"]))
        if len(pairs) > 1:
            msg += "（一共有 %d 行需要处理）" % len(pairs)
        super().__init__(msg, data={"pairs": pairs})
        self.pairs = pairs


class FxRateMissing(DomainError):
    code = "FX_RATE_MISSING"

    def __init__(self, currency):
        super().__init__(
            "这张单是「%s」单，设置里还没有 %s 兑港币的汇率，请老板到「设置」里填好汇率后再入账" % (currency, currency),
            data={"currency": currency})

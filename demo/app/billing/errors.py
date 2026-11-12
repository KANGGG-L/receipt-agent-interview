# -*- coding: utf-8 -*-
"""S03–S08 计费域错误：统一带 code / 人话 msg / HTTP 状态，端点与模块共用。"""


class BillingError(Exception):
    status_code = 400

    def __init__(self, code, msg, status_code=None):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        if status_code is not None:
            self.status_code = status_code

    def payload(self):
        return {"status": "error", "code": self.code, "msg": self.msg}


class BillingConflict(BillingError):
    status_code = 409


class BillingDenied(BillingError):
    status_code = 403


class BillingNotFound(BillingError):
    status_code = 404


class SecurityReject(BillingError):
    status_code = 400


class ProviderUnknown(BillingError):
    """渠道结果不确定（超时/断网）：只能先查询，不得再发起一次扣款。"""

    status_code = 409

    def __init__(self, msg="暂时无法确认支付渠道的处理结果，系统会先查询，不会重复扣款。"):
        super().__init__("PROVIDER_RESULT_UNKNOWN", msg)

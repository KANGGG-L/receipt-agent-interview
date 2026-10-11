"""
PII Masker Tool v1.0.0 (Production Active)
香港身份证/内地身份证/银行卡/私人手机号敏感信息脱敏工具。

demo/app/services/llm_logger 与 registry 其它调用方共用这一份实现（SSOT）。
"""

import re


class PIIMaskerTool:
    def __init__(self):
        # 香港身份证号：A123456(7) 与不带括号的 A1234567 两种写法
        self.hkid_pattern = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,2}\d{6}\(?[\dA]\)?(?![A-Za-z0-9])", re.IGNORECASE)
        # 内地 18 位身份证：保留前 6 后 4
        self.id_card_pattern = re.compile(r"(?<!\d)([1-9]\d{5})\d{8}(\d{3}[\dXx])(?![\dXx])")
        # 香港 8 位私人手机号（5/6/9 开头），可带 +852 / 852 前缀，中间可有空格或横线
        self.phone_pattern = re.compile(r"(?<![\d-])(?:\+?852[\s-]*)?[569]\d{3}[\s-]?\d{4}(?![\d-])")
        # 银行卡号：四位一组带分隔（至少三组），或连续 13~19 位
        self.bank_pattern = re.compile(r"(?<!\d)(?:\d{4}[-\s]){3}\d{1,4}(?!\d)|(?<!\d)\d{13,19}(?!\d)")

    @staticmethod
    def _mask_phone(match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return f"****{digits[-2:]}"

    def execute(self, text: str) -> str:
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        res = self.id_card_pattern.sub(r"\g<1>********\g<2>", text)
        res = self.hkid_pattern.sub("[HKID_MASKED]", res)
        res = self.bank_pattern.sub(lambda m: f"****-****-****-{re.sub(r'[^0-9]', '', m.group(0))[-4:]}", res)
        res = self.phone_pattern.sub(self._mask_phone, res)
        return res


Tool = PIIMaskerTool

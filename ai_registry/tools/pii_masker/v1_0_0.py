"""
PII Masker Tool v1.0.0 (Production Active)
香港身份证/银行卡/私人手机号敏感信息脱敏工具。
"""

import re

class PIIMaskerTool:
    def __init__(self):
        # 香港身份证号正则
        self.hkid_pattern = re.compile(r"[A-Z]{1,2}\d{6}\([\dA]\)", re.IGNORECASE)
        # 香港手机号
        self.phone_pattern = re.compile(r"(?:\+852\s*)?[569]\d{3}\s*\d{4}")
        # 银行账号 (8~16位)
        self.bank_pattern = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4,8}\b")

    def execute(self, text: str) -> str:
        res = self.hkid_pattern.sub("[HKID_MASKED]", text)
        res = self.phone_pattern.sub(lambda m: f"{m.group(0)[:2]}****{m.group(0)[-2:]}", res)
        res = self.bank_pattern.sub(lambda m: f"****-****-****-{m.group(0)[-4:]}", res)
        return res

Tool = PIIMaskerTool

# -*- coding: utf-8 -*-
"""测试桩：固定返回「新協興 1080」识别结果的假模型（原先是 llm.py 里的 MOCK_QWEN_FLASH 后门）。

只用于测试：写 llm_call_log 的方式与真实通道一致，便于验证调用拦截、脱敏与 token 归一。
"""

import json
import time

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.llm import OpenAIChatModel, _calc_cost_hkd

MOCK_USAGE = {"prompt_tokens": 2100, "completion_tokens": 470, "cached_tokens": 1200, "total_tokens": 2570}
MOCK_RESULT = {
    "doc_form": "printed_delivery_note",
    "vendor": "新協興",
    "date": "2024-02-02",
    "items": [
        {"name": "测试长单项1", "qty": 21.5, "unit": "斤", "unit_price": 40, "amount": 860},
        {"name": "测试长单项2", "qty": 11, "unit": "斤", "unit_price": 20, "amount": 220},
    ],
    "total": 1080, "payment_marked": True, "confidence": 0.95,
}


class MockQwenFlashModel(OpenAIChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        start = time.time()
        content = json.dumps(MOCK_RESULT, ensure_ascii=False)
        try:
            from app.services.llm_logger import record_llm_call
            record_llm_call(
                engine="mock", model=str(self.model), prompt_messages=messages,
                raw_output=content, token_usage=MOCK_USAGE,
                cost_hkd=_calc_cost_hkd(MOCK_USAGE, model=self.model),
                elapsed_ms=round((time.time() - start) * 1000, 1), status="success")
        except Exception:
            pass
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=content,
            response_metadata={"token_usage": MOCK_USAGE, "model": self.model, "usage": MOCK_USAGE}))])

# -*- coding: utf-8 -*-
"""F1 基线运行配套判别式单测：OpenAIChatModel 的 max_tokens 环境变量覆盖。

背景：phase0-measurement 分支跑 deepseek/deepseek-v4.1-flash（推理型模型）做首次真实
per-field F1 基线时，其 reasoning 段也消耗 completion 预算，llm.py 写死 max_tokens=4000
导致长图任务 finish_reason=length、content 为空（"空输出"）。
本用例用 mock requests.post 判别两种端点形态：
  1) 未设 OPENAI_REC_MAX_TOKENS → 载荷 max_tokens 保持 4000 原口径（回归守卫）；
  2) 设 OPENAI_REC_MAX_TOKENS=16384 → 载荷 max_tokens=16384，且请求路径仍是
     base_url + "/chat/completions"（非 /responses，判别端点形态不漂移）；
  3) DeepSeek 官方硬上限守卫：模型名含 deepseek 且 base_url 非已知网关时
     （classify_model_profile 判 is_deepseek_official），env 抬到 16384 会被
     钳到 8192（llm.py clamp_max_tokens_8192）。

2026-10-10 契约对齐：env 覆盖判别改用非 deepseek 模型（qwen3.5-omni-flash）——
原用的 deepseek 模型 + commandcode.ai base_url 在新增的模型画像分类下命中
官方 DeepSeek 钳制，会污染「env 覆盖」这一被测机制的判别。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.llm import OpenAIChatModel  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402


def _fake_response(content="ok"):
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return resp


class TestMaxTokensEnv(unittest.TestCase):
    # 非 deepseek 模型：classify_model_profile 不判官方 DeepSeek，
    # 请求路径无 8192 钳制 → env 覆盖机制可被纯净判别。
    _PLAIN_MODEL = "qwen3.5-omni-flash"
    _DEEPSEEK_MODEL = "deepseek/deepseek-v4.1-flash"

    def _invoke(self, captured, model=None):
        m = OpenAIChatModel(base_url="https://api.commandcode.ai/provider/v1",
                            api_key="k", model=model or self._PLAIN_MODEL)
        with mock.patch("app.llm.requests.post",
                        side_effect=lambda url, **kw: (captured.append((url, kw)), _fake_response())[1]):
            m.invoke([HumanMessage(content="hi")])

    def test_default_keeps_4000(self):
        captured = []
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_REC_MAX_TOKENS", None)
            self._invoke(captured)
        url, kw = captured[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(kw["json"]["max_tokens"], 4000)

    def test_env_override_raises_budget(self):
        captured = []
        with mock.patch.dict(os.environ, {"OPENAI_REC_MAX_TOKENS": "16384"}):
            self._invoke(captured)
        url, kw = captured[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(kw["json"]["max_tokens"], 16384)

    def test_deepseek_model_clamps_env_override_to_8192(self):
        """DeepSeek 官方硬上限守卫：模型名含 deepseek 且 base_url 非已知网关时，
        即使 env 抬到 16384，载荷也会被 _adapt_request_payload 钳到 8192
        （classify_model_profile 判 is_deepseek_official，llm.py clamp_max_tokens_8192）。"""
        captured = []
        with mock.patch.dict(os.environ, {"OPENAI_REC_MAX_TOKENS": "16384"}):
            self._invoke(captured, model=self._DEEPSEEK_MODEL)
        url, kw = captured[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(kw["json"]["max_tokens"], 8192)

    def test_invalid_env_falls_back_to_4000(self):
        captured = []
        with mock.patch.dict(os.environ, {"OPENAI_REC_MAX_TOKENS": "abc"}):
            self._invoke(captured)
        self.assertEqual(captured[0][1]["json"]["max_tokens"], 4000)

    def test_call_timeout_default_unchanged(self):
        captured = []
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_REC_CALL_TIMEOUT", None)
            self._invoke(captured)
        self.assertEqual(captured[0][1]["timeout"], 60)  # CALL_TIMEOUT_SECONDS 硬上限原口径

    def test_call_timeout_env_raises_transport_cap(self):
        captured = []
        with mock.patch.dict(os.environ, {"OPENAI_REC_CALL_TIMEOUT": "300", "RECEIPT_OFFLINE_EVAL": "1"}):
            self._invoke(captured)
        self.assertEqual(captured[0][1]["timeout"], 300)

    def test_call_timeout_env_ignored_online(self):
        # 线上进程（没有声明离线评测）不得被环境变量把超时抬到分钟级
        captured = []
        env = {"OPENAI_REC_CALL_TIMEOUT": "300"}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("RECEIPT_OFFLINE_EVAL", None)
            self._invoke(captured)
        self.assertEqual(captured[0][1]["timeout"], 60)

    def test_call_timeout_env_ignored_in_production_even_if_offline_flag(self):
        captured = []
        with mock.patch.dict(os.environ, {"OPENAI_REC_CALL_TIMEOUT": "300", "RECEIPT_OFFLINE_EVAL": "1", "ENV": "production"}):
            self._invoke(captured)
        self.assertEqual(captured[0][1]["timeout"], 60)

    def test_call_timeout_env_clamped_to_600(self):
        captured = []
        with mock.patch.dict(os.environ, {"OPENAI_REC_CALL_TIMEOUT": "9999", "RECEIPT_OFFLINE_EVAL": "1"}):
            self._invoke(captured)
        self.assertEqual(captured[0][1]["timeout"], 600)


if __name__ == "__main__":
    unittest.main()

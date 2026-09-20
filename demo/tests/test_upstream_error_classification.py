# -*- coding: utf-8 -*-
"""T10（P9）单元测试：上游 50507 / 5xx 被当成普通失败导致的误导性归因。

why：
真实单据曾报 `OpenAI 兼容接口失败 500 code=50507 Request failed: Unknown error`
（DashScope 服务端故障码）。修复前 OpenAIChatModel._generate 把它和鉴权失败一样
统一抛 RuntimeError，调用方（extract_chain / supervisor）只能截字符串猜归因，于是
上游服务商侧临时故障被写成「引擎调用异常」，用户看到的结论是"模型/引擎不行"——
排查方向从一开始就是错的。

本文件锁定四件事：
  1. llm 把 5xx / code=50507 归类为 UpstreamServiceError（category=upstream），
     与鉴权（auth）/ 参数（param）错误可区分；
  2. 错误文案可读，含建议（稍后重试 / 切换引擎），且不出现"鉴权失败"等错误归因；
  3. extract_chain 据 category 生成降级原因，并把 error_category 透传进返回 dict，
     使上游故障不再被记成模型能力问题；
  4. 反证：修复前的 RuntimeError 无 category，上游与鉴权在机器可读层面完全同质。

全程不碰 DB、不发真实网络请求（requests.post 与 URL 安全校验均被 mock）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image
from langchain_core.messages import HumanMessage

from app import llm
from app.chains import extract_chain

# 用 getattr 取异常类型：这样"回滚本次修复"后本文件以断言失败暴露回归，
# 而不是在收集期直接 ImportError。
UpstreamServiceError = getattr(llm, "UpstreamServiceError", None)
EngineAuthError = getattr(llm, "EngineAuthError", None)
EngineParamError = getattr(llm, "EngineParamError", None)

# DashScope 服务端故障体（JSON 规范形态，code 为数字）
_UPSTREAM_BODY = '{"error": {"code": 50507, "message": "Request failed: Unknown error", "type": "internal_error"}}'
# SDK 包装成纯文本后的形态
_UPSTREAM_TEXT = "OpenAI 兼容接口失败 500: code=50507 Request failed: Unknown error"
_AUTH_BODY = '{"error": {"code": "invalid_api_key", "message": "Incorrect API key provided"}}'
_PARAM_BODY = '{"error": {"code": "invalid_parameter", "message": "model not found"}}'


class _Resp:
    """最小 requests.Response 替身（只用到 status_code / text / json）。"""

    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        import json as _j

        return _j.loads(self.text)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """屏蔽 URL 安全校验里的 DNS 解析，保证用例离线可跑。"""
    from app.services import security_guard

    monkeypatch.setattr(
        security_guard, "validate_safe_external_url", lambda url, allow_localhost=False: (True, None)
    )


def _png(tmp_path):
    p = str(tmp_path / "ok.png")
    Image.new("RGB", (40, 20), (7, 7, 7)).save(p, format="PNG")
    return p


def _mock_post(monkeypatch, resp):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: resp)


# -------------------------------------------------------------
# 1. 归类：5xx / 50507 → upstream，与 auth / param 可区分
# -------------------------------------------------------------
def test_50507_json_body_classified_as_upstream():
    err = llm._classify_http_error(500, _UPSTREAM_BODY)
    assert UpstreamServiceError is not None and isinstance(err, UpstreamServiceError)
    assert err.category == "upstream"
    assert err.status_code == 500
    assert err.code == "50507"
    msg = str(err)
    assert "上游服务故障" in msg and "50507" in msg, msg
    assert "稍后重试" in msg and "切换引擎" in msg, "上游故障文案必须给出可操作建议"
    assert "鉴权失败" not in msg, "上游故障不得被写成鉴权失败"
    # 仍是 RuntimeError：既有 except RuntimeError 的调用方行为不变
    assert isinstance(err, RuntimeError)


def test_50507_plain_text_body_classified_as_upstream():
    """SDK 把响应体包成字符串时也要能取到码（文本 fallback 解析）。"""
    err = llm._classify_http_error(500, _UPSTREAM_TEXT)
    assert err.category == "upstream"
    assert err.code == "50507"
    assert "上游服务故障" in str(err)


def test_5xx_without_known_code_still_upstream():
    """状态码 5xx 本身就足以判为上游故障（码只用于文案增强）。"""
    err = llm._classify_http_error(503, "Service Unavailable")
    assert err.category == "upstream"


def test_auth_error_classified_separately():
    err = llm._classify_http_error(401, _AUTH_BODY)
    assert EngineAuthError is not None and isinstance(err, EngineAuthError)
    assert err.category == "auth"
    assert "鉴权失败" in str(err)
    assert "上游服务故障" not in str(err), "鉴权失败不得混入上游故障归因"
    assert llm._classify_http_error(403, _AUTH_BODY).category == "auth"


def test_param_error_classified_separately():
    err = llm._classify_http_error(400, _PARAM_BODY)
    assert EngineParamError is not None and isinstance(err, EngineParamError)
    assert err.category == "param"
    assert "参数错误" in str(err)
    assert "上游服务故障" not in str(err)


def test_unknown_status_has_no_upstream_attribution():
    """404 等非归类状态：保持通用文案，且不带任何归因标签。"""
    err = llm._classify_http_error(404, "not found")
    assert err.category == "engine_error"
    assert "上游服务故障" not in str(err)
    assert "鉴权失败" not in str(err)


def test_error_code_extraction_variants():
    assert llm._extract_error_code('{"error": {"code": 50507}}') == "50507"
    assert llm._extract_error_code('{"code": "50507"}') == "50507"
    assert llm._extract_error_code("x code=50507 y") == "50507"
    assert llm._extract_error_code("") == ""
    assert llm._extract_error_code(None) == ""


# -------------------------------------------------------------
# 2. _generate 端到端：mock 50507 响应 → 抛可读的上游故障异常
# -------------------------------------------------------------
def test_generate_raises_readable_upstream_error_on_50507(monkeypatch):
    _mock_post(monkeypatch, _Resp(500, _UPSTREAM_BODY))
    model = llm.OpenAIChatModel(
        base_url="https://api.example.com/v1", api_key="sk-test", model="m", call_timeout=5
    )
    with pytest.raises(Exception) as ei:
        model._generate([HumanMessage(content="hi")])
    err = ei.value
    assert isinstance(err, UpstreamServiceError)
    msg = str(err)
    assert "上游服务故障" in msg and "50507" in msg, msg
    assert "稍后重试" in msg or "切换引擎" in msg


def test_generate_auth_error_message_not_confused_with_upstream(monkeypatch):
    _mock_post(monkeypatch, _Resp(401, _AUTH_BODY))
    model = llm.OpenAIChatModel(
        base_url="https://api.example.com/v1", api_key="sk-bad", model="m", call_timeout=5
    )
    with pytest.raises(Exception) as ei:
        model._generate([HumanMessage(content="hi")])
    err = ei.value
    assert err.category == "auth"
    assert "鉴权失败" in str(err)
    assert "上游服务故障" not in str(err)


# -------------------------------------------------------------
# 3. extract_chain：据归类生成降级原因 + 透传 error_category
# -------------------------------------------------------------
class _UpstreamFailingModel:
    """kind=openai 且 invoke 抛上游故障（模拟真实 50507 路径）。"""

    kind = "openai"

    def invoke(self, prompt):
        raise llm.UpstreamServiceError(
            "上游服务故障（50507）（HTTP 500）：这是服务商侧临时故障，"
            "并非密钥或参数错误、也不是模型能力问题，建议稍后重试或切换引擎。",
            status_code=500,
            code="50507",
        )


def test_extract_reports_upstream_category_and_reason(tmp_path, monkeypatch):
    """上游故障 → error_category=upstream，降级原因指明是服务商侧故障。"""
    # 备用引擎也失败，走「首选 + 降级均失败」的返回分支
    # 备用引擎也失败：把兜底构建器打桩成抛错。
    # 历史注记：原先是打桩 app.llm._build，但 extract_chain 已不再使用它
    # （CLI 兜底已换成 DashScope 官方通道兜底，见 _build_dashscope_fallback），
    # 打桩旧目标会静默失效、让真实 DashScope 调用接管。
    monkeypatch.setattr(extract_chain, "_build_dashscope_fallback",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fallback boom")))
    ret = extract_chain.extract_receipt(
        _png(tmp_path), model=_UpstreamFailingModel(), enable_canary=False
    )

    assert ret["data"] is None
    assert ret["error_category"] == "upstream"
    assert "上游服务故障" in ret["error"]
    assert "50507" in ret["error"]
    # 不得被写成鉴权失败或模型能力问题
    assert "鉴权失败" not in ret["error"]
    assert ret["fallback_triggered"] is True
    assert "上游服务故障" in ret["fallback_reason"], ret["fallback_reason"]
    assert "非模型能力问题" in ret["fallback_reason"], ret["fallback_reason"]


def test_extract_auth_category_is_distinguishable(tmp_path, monkeypatch):
    """同一路径下鉴权失败归为 auth，且降级原因与上游故障文案不同。"""

    class _AuthFailingModel:
        kind = "openai"

        def invoke(self, prompt):
            raise llm.EngineAuthError(
                "OpenAI 兼容接口鉴权失败（HTTP 401）：API 密钥无效或已过期。",
                status_code=401,
            )

    # 备用引擎也失败：把兜底构建器打桩成抛错。
    # 历史注记：原先是打桩 app.llm._build，但 extract_chain 已不再使用它
    # （CLI 兜底已换成 DashScope 官方通道兜底，见 _build_dashscope_fallback），
    # 打桩旧目标会静默失效、让真实 DashScope 调用接管。
    monkeypatch.setattr(extract_chain, "_build_dashscope_fallback",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fallback boom")))
    ret = extract_chain.extract_receipt(
        _png(tmp_path), model=_AuthFailingModel(), enable_canary=False
    )
    assert ret["error_category"] == "auth"
    assert "鉴权失败" in ret["fallback_reason"]
    assert "上游服务故障" not in ret["fallback_reason"]


def test_extract_unclassified_error_keeps_empty_category(tmp_path, monkeypatch):
    """未归类异常 category 为空串，既有文案与语义不变。"""
    # 关闭识别引擎的自动兜底：本用例要看的是「首选引擎未归类失败」返回本身，
    # 兜底可用会真的发起 DashScope 调用并返回正常结果，测不到该分支。
    monkeypatch.setattr(extract_chain, "_dashscope_fallback_available",
                        lambda *a, **kw: False)

    class _BoomModel:
        kind = "openai"  # 兜底已关闭 → 直接走"调用失败"返回

        def invoke(self, prompt):
            raise RuntimeError("engine boom")

    ret = extract_chain.extract_receipt(
        _png(tmp_path), model=_BoomModel(), enable_canary=False
    )
    assert ret["error_category"] == ""
    assert "VLM 调用失败" in ret["error"]
    assert llm._engine_error_category(RuntimeError("plain")) == ""


def test_describe_engine_failure_labels():
    assert "上游服务故障" in extract_chain._describe_engine_failure("openai", RuntimeError("x"), "upstream")
    assert "鉴权失败" in extract_chain._describe_engine_failure("openai", RuntimeError("x"), "auth")
    assert "参数错误" in extract_chain._describe_engine_failure("openai", RuntimeError("x"), "param")
    assert "调用异常" in extract_chain._describe_engine_failure("openai", RuntimeError("x"), "")


# -------------------------------------------------------------
# 4. 反证：修复前统一 RuntimeError → 上游与鉴权在机器可读层面完全同质
# -------------------------------------------------------------
def test_reverse_proof_legacy_runtimeerror_indistinguishable():
    """反证：修复前两者都是裸 RuntimeError，category 均取不到。

    即调用方只能靠截字符串猜归因 —— 上游 50507 因此被记成普通引擎异常，
    这正是 T10 要消除的误导性归因。若本用例失败，说明异常类层次被移除了。
    """
    legacy_upstream = RuntimeError(_UPSTREAM_TEXT)
    legacy_auth = RuntimeError("OpenAI 兼容接口鉴权失败（HTTP 401）：API 密钥无效或已过期")
    assert llm._engine_error_category(legacy_upstream) == ""
    assert llm._engine_error_category(legacy_auth) == ""
    assert not isinstance(legacy_upstream, UpstreamServiceError)
    # 裸 RuntimeError 文案里没有任何"上游故障"归因，用户只会看到"接口失败"
    assert "上游服务故障" not in str(legacy_upstream)

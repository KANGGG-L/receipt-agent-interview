# -*- coding: utf-8 -*-
"""G3 回归：embedding provider 三档可切换（修 gap M7 的「假开关」）。

背景：`rag._CharNGramEmbeddings` 的 docstring 宣称生产可通过
`RAG_EMBEDDING_PROVIDER` 换 DashScope/OpenAI embedding，但该键全仓零读取 —— 实际
只有 char-2/3-gram 哈希一档。本文件钉住新模块 `app/services/embeddings.py` 的契约：

1. 三档 provider（hash / siliconflow / dashscope）identity 正确且互不相同；
2. hash 档纯离线、确定性、256 维、L2 归一（测试与 CI 的默认档）；
3. 未设置环境变量时缺省为 hash（与现状行为一致，零行为变化）；
4. 非法 provider 名 → 显式报错（**有日志**），**不回落 hash**（不猜用户意图）；
5. siliconflow / dashscope 凭据不成对 → 显式报错（**有日志**），不借用别家凭据；
6. **可用性探测失败**（凭据齐全但真调用失败：HTTP 错误 / 免费额度耗尽 / 维度不符 /
   空向量）→ 三要素 warning（请求档位 + 失败原因 + 实际回落档位）后回落到可用档，
   且**返回对象的 identity 必须反映实际生效档**（维度与集合后缀随之变化，
   避免 256 维与 1024 维混写同一 collection）；
7. J4-3 自校准：先注入一条已知 log 证明采集通道有效，再断言健康路径零失败日志；
8. J4-6 三档（含回落档）都是 `langchain_core.embeddings.Embeddings` 实例；
9. J4-7 `embeddings.py` 不 import `app.services.rag`、不出现 RAG_DIR / PERSIST_DIR，
   且 env 解析与客户端构造/网络探测都不在 import 期发生（只在 `get_embeddings()`
   调用链上）；
10. AC-4 的「离线」判据在 **socket 层**证明（不只盯 `requests.post`），且陷阱先
    自校准 —— 否则「0 连接」既可能是真离线、也可能是陷阱没覆盖到（假绿）。

测试一律不联网：hash 档只做本地计算；siliconflow / dashscope 档的「可用性探测」与
后续调用都用假的 HTTP 响应对象替换 `requests.post`。
"""

import ast
import logging
import os
import socket
import sys

import pytest
from langchain_core.embeddings import Embeddings

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_DEMO_DIR = os.path.join(_REPO_ROOT, "demo")
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.services.embeddings as emb  # noqa: E402

_EMB_MODULE_PATH = os.path.join(_DEMO_DIR, "app", "services", "embeddings.py")

# 所有会影响档位解析的环境变量：逐个清空，保证用例不依赖（也不污染）本机 .env
_PROVIDER_ENV_KEYS = (
    "RAG_EMBEDDING_PROVIDER",
    "SILICONFLOW_BASE_URL", "SILICONFLOW_API_KEY",
    "OPENAI_BASE_URL", "OPENAI_API_KEY",
    "DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    """清空全部 provider 相关环境变量，用例自己按需设置（避免读本机真实凭据）。"""
    for key in _PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _set_env(monkeypatch, **kv):
    for key, value in kv.items():
        monkeypatch.setenv(key, value)


class _FakeResp:
    """最小 HTTP 响应替身（只需 status_code / text / json 三个被实现读取的属性）。"""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("响应体不是 JSON")
        return self._payload


def _ok_response(dim, index=0):
    """合法响应：维度与 identity 声明一致，探测通过。"""
    return _FakeResp(payload={"data": [{"index": index, "embedding": [0.1] * dim}]})


def _install_healthy_post(monkeypatch, dim):
    """让可用性探测通过（探测会真发一次请求，必须由用例接管）。"""
    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: _ok_response(dim))


def _offline_siliconflow(monkeypatch):
    """构造 siliconflow 档（凭据齐全 + 探测用假响应通过），返回嵌入对象。"""
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="siliconflow",
             SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
             SILICONFLOW_API_KEY="sk-fake-for-test")
    _install_healthy_post(monkeypatch, emb.SILICONFLOW_DIM)
    e = emb.get_embeddings()
    assert e.identity.provider == emb.PROVIDER_SILICONFLOW, "前置条件：探测应通过，不得落到回落档"
    return e


def _offline_dashscope(monkeypatch):
    """构造 dashscope 档（凭据齐全 + 探测用假响应通过），返回嵌入对象。"""
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="dashscope",
             DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
             DASHSCOPE_API_KEY="sk-fake-for-test")
    _install_healthy_post(monkeypatch, emb.DASHSCOPE_DIM)
    e = emb.get_embeddings()
    assert e.identity.provider == emb.PROVIDER_DASHSCOPE, "前置条件：探测应通过，不得落到回落档"
    return e


# ---------------------------------------------------------------- 档位解析

def test_default_provider_is_hash_when_env_absent():
    """未设置 RAG_EMBEDDING_PROVIDER 时缺省 hash（与现状行为一致，零行为变化）。

    hash 档恒可用且离线，因此 `get_embeddings()` 不得对它发起任何网络探测。
    """
    assert emb.resolve_provider() == emb.PROVIDER_HASH
    assert emb.resolve_provider(None) == emb.PROVIDER_HASH
    got = emb.get_embeddings()
    assert isinstance(got, emb.HashEmbeddings)
    assert got.identity.provider == emb.PROVIDER_HASH


def test_empty_string_env_falls_back_to_default_hash(monkeypatch):
    """空串/纯空白视为未设置（避免 .env 里写了 key 没写值就变成「非法档位」）。"""
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "   ")
    assert emb.resolve_provider() == emb.PROVIDER_HASH


@pytest.mark.parametrize("bad", ["openai", "bge-m3", "hash256", "HASH2", "本地"])
def test_invalid_provider_name_raises_instead_of_falling_back(monkeypatch, bad):
    """非法档位名必须显式报错；回落 hash 会让 M7 的假开关原样复活。"""
    with pytest.raises(ValueError) as ei:
        emb.resolve_provider(bad)
    assert bad in str(ei.value)
    assert "不静默回落" in str(ei.value)

    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", bad)
    with pytest.raises(ValueError):
        emb.get_embeddings()


def test_invalid_provider_name_is_logged_not_just_raised(caplog):
    """J4-1（第 1 类失败路径）：非法档位名除了抛错，还必须**留下日志**。

    why：只抛错的话，异常可能被上层宽泛的 except 吞掉（本项目高发缺陷 ③），
    运维从日志里什么也看不到；日志是「到底配错了什么」的唯一持久线索。
    """
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError):
            emb.resolve_provider("openai")
    assert "未知的 embedding provider" in caplog.text
    assert "openai" in caplog.text


def test_explicit_provider_overrides_env(monkeypatch):
    """显式入参优先于环境变量（便于按租户/实验分流）。"""
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    assert emb.resolve_provider("hash") == emb.PROVIDER_HASH


# ---------------------------------------------------------------- identity

def test_identity_of_each_tier(monkeypatch):
    """三档 identity 的 provider / 模型 / 维度都要正确且可写进集合名。"""
    h = emb.HashEmbeddings().identity
    assert (h.provider, h.model, h.dim) == (emb.PROVIDER_HASH, emb.HASH_MODEL, 256)
    assert h.label == "hash:char-ngram-hash:256"

    s = _offline_siliconflow(monkeypatch).identity
    assert (s.provider, s.model, s.dim) == (emb.PROVIDER_SILICONFLOW, "BAAI/bge-m3", 1024)

    d = _offline_dashscope(monkeypatch).identity
    assert (d.provider, d.model, d.dim) == (emb.PROVIDER_DASHSCOPE, "text-embedding-v4", 1024)

    # metadata 形态（供巡检工具比对集合与 provider 是否一致）
    assert s.as_dict() == {"embedding_provider": "siliconflow",
                           "embedding_model": "BAAI/bge-m3",
                           "embedding_dim": 1024}


def test_identities_are_pairwise_distinct_and_suffixes_differ(monkeypatch):
    """hash 与 bge-m3 维度不同 → 集合名后缀必须不同，否则会混写同一集合。"""
    ids = [
        emb.HashEmbeddings().identity,
        _offline_siliconflow(monkeypatch).identity,
        _offline_dashscope(monkeypatch).identity,
    ]
    labels = [i.label for i in ids]
    assert len(set(labels)) == len(labels)
    suffixes = [i.collection_suffix() for i in ids]
    assert len(set(suffixes)) == len(suffixes)
    for suf in suffixes:  # 集合名安全：只含 [a-z0-9_-]
        assert suf and all(c.isalnum() or c in "_-" for c in suf)


# ---------------------------------------------------------------- hash 档离线确定性

def test_hash_tier_is_offline_deterministic_and_normalized(monkeypatch):
    """hash 档：同输入同输出、维度 256、L2 归一、且完全不发包。"""
    def _boom(*a, **kw):  # 一旦发包即视为违反「离线档」契约
        raise AssertionError("hash 档不得发起网络请求")

    monkeypatch.setattr(emb.requests, "post", _boom)
    e = emb.HashEmbeddings()

    v1 = e.embed_query("德利行 本地牛腩 2斤")
    v2 = e.embed_query("德利行 本地牛腩 2斤")
    assert v1 == v2
    assert len(v1) == 256
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-9

    docs = e.embed_documents(["供应商甲 白糖", "供应商乙 白糖"])
    assert len(docs) == 2 and all(len(d) == 256 for d in docs)
    assert docs[0] != docs[1]  # 不同文本不同向量（避免恒等实现的假通过）


def test_hash_tier_empty_input_is_safe():
    """空输入不得抛错（Chroma 在无候选时也会调用）。"""
    e = emb.HashEmbeddings()
    assert e.embed_documents([]) == []
    assert e.embed_documents(None) == []


def test_hash_algorithm_matches_rag_legacy_implementation():
    """与 rag._CharNGramEmbeddings 的算法逐值一致（缺省档零行为变化）。

    这里独立复刻一遍旧算法（不从被测模块取），避免「用被测实现证明被测实现」。
    待接线（rag 改调本模块并删除旧类）后，本用例可一并删除。
    """
    import hashlib
    import unicodedata

    def legacy(text, dim=256):
        norm = unicodedata.normalize("NFKC", str(text or "")).casefold().replace(" ", "")
        vec = [0.0] * dim
        for n in (2, 3):
            if len(norm) < n:
                bucket = int(hashlib.sha256(norm.encode()).hexdigest(), 16) % dim
                vec[bucket] += 1.0
                continue
            for i in range(len(norm) - n + 1):
                gram = norm[i:i + n]
                bucket = int(hashlib.sha256(gram.encode()).hexdigest(), 16) % dim
                vec[bucket] += 1.0
        factor = sum(c * c for c in vec) ** 0.5
        if factor > 0:
            vec = [c / factor for c in vec]
        return vec

    e = emb.HashEmbeddings()
    for text in ("", "A", "德利行", "ANIKAR A雞粉 x2 @HK$88.00", "7-ELEVEN HK (1542)"):
        assert e.embed_query(text) == legacy(text), "hash 档与旧实现不一致：%r" % text


def test_hash_tier_opens_no_socket_with_self_calibrated_trap(monkeypatch):
    """AC-4（J4-4）：hash 档的「离线」在 socket 层证明，且陷阱先自校准。

    why 不能只盯 `requests.post` 的调用次数：那只覆盖 requests 这一条路径 —— 若实现
    将来改用别的 HTTP 客户端、或某个依赖自己在底下建连，计数仍是 0，于是「0 连接」
    既可能是真离线、也可能是陷阱没覆盖到（假绿）。所以这里把陷阱装在
    `socket.socket.connect`（所有 Python 层网络出口的公共必经点）上；并且**先自校准**
    （手动触发一次连接企图，确认它真的会被拦），再断言 hash 档 0 连接、0 发包。
    """
    attempts = []

    def _trap_connect(self, address):
        attempts.append(address)
        raise AssertionError("hash 档不得建立任何网络连接（address=%r）" % (address,))

    monkeypatch.setattr(socket.socket, "connect", _trap_connect)

    # 自校准：先证明陷阱真的能拦住一次连接企图，否则下面的「0 连接」不可信
    probe = socket.socket()
    try:
        with pytest.raises(AssertionError):
            probe.connect(("127.0.0.1", 9))
    finally:
        probe.close()
    assert attempts, "陷阱自校准失败：connect 未被拦截，「0 连接」断言不可信"
    attempts.clear()

    post_calls = []
    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: post_calls.append(a))

    e = emb.get_embeddings()  # 未设 RAG_EMBEDDING_PROVIDER → hash 档，不得做可用性探测
    assert e.identity.provider == emb.PROVIDER_HASH
    e.embed_query("德利行 本地牛腩 2斤")
    e.embed_documents(["供应商甲 白糖", "供应商乙 白糖"])

    assert attempts == [], "hash 档发起了网络连接：%r" % (attempts,)
    assert post_calls == [], "hash 档发起了 HTTP 请求"


# ---------------------------------------------------------------- 凭据解析

def test_siliconflow_missing_all_credentials_raises(monkeypatch):
    """siliconflow 档缺凭据必须显式报错，且错误里点明缺哪些键、声明不换档。"""
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "siliconflow")
    with pytest.raises(RuntimeError) as ei:
        emb.get_embeddings()
    msg = str(ei.value)
    assert "SILICONFLOW_BASE_URL" in msg and "OPENAI_API_KEY" in msg
    assert "不静默降级" in msg


def test_unpaired_credentials_are_logged_not_just_raised(monkeypatch, caplog):
    """J4-1（第 2 类失败路径）：凭据不成对除了抛错，还必须**留下日志**。

    why：凭据类配置错误是「连探测都发不出去」的场景，不能靠回落掩盖（否则运维会
    以为 bge-m3 在跑、实际是 256 维哈希）；日志必须点明缺哪个键，且不打印密钥值。
    """
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="siliconflow",
             SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError):
            emb.get_embeddings()
    assert "凭据不完整" in caplog.text
    assert "SILICONFLOW_API_KEY=缺失" in caplog.text
    assert "sk-" not in caplog.text  # 日志不得泄漏密钥内容


def test_siliconflow_base_without_key_does_not_borrow_other_provider_key(monkeypatch):
    """base 有值而 key 为空时不得借用别家 key（跨 provider 错配必然 401）。"""
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="siliconflow",
             SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1")
    with pytest.raises(RuntimeError) as ei:
        emb.get_embeddings()
    assert "SILICONFLOW_API_KEY=缺失" in str(ei.value)


def test_siliconflow_uses_openai_pair_when_siliconflow_pair_absent(monkeypatch):
    """SILICONFLOW_* 未配置时按成对候选回落 OPENAI_*（本项目该对即 SiliconFlow）。"""
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="siliconflow",
             OPENAI_BASE_URL="https://api.siliconflow.cn/v1",
             OPENAI_API_KEY="sk-fake-for-test")
    _install_healthy_post(monkeypatch, emb.SILICONFLOW_DIM)
    e = emb.get_embeddings()
    assert e.base_url == "https://api.siliconflow.cn/v1"
    assert e.identity.provider == emb.PROVIDER_SILICONFLOW


def test_dashscope_missing_credentials_raises(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    with pytest.raises(RuntimeError) as ei:
        emb.get_embeddings()
    assert "DASHSCOPE_API_KEY" in str(ei.value)


# ------------------------------------------------- J4-1 探测失败：有日志 + 回落可用档

# 四类「只有真调用才暴露」的探测失败：HTTP 错误 / 额度耗尽 / 维度不符 / 空向量
_PROBE_FAILURE_CASES = (
    ("http_error", {"status_code": 500, "text": "upstream boom"}, "HTTP 500"),
    ("quota_exhausted",
     {"status_code": 400,
      "text": '{"error":{"code":"AllocationQuota.FreeTierOnly","message":"free tier only"}}'},
     "AllocationQuota.FreeTierOnly"),
    ("dim_mismatch", {"payload": {"data": [{"index": 0, "embedding": [0.1] * 128}]}}, "维度"),
    ("empty_vector", {"payload": {"data": [{"index": 0, "embedding": []}]}}, "空向量"),
)


@pytest.mark.parametrize("case,resp_kwargs,marker", _PROBE_FAILURE_CASES,
                         ids=[c[0] for c in _PROBE_FAILURE_CASES])
def test_probe_failure_is_logged_and_falls_back(monkeypatch, caplog, case, resp_kwargs, marker):
    """J4-1（第 3 类失败路径）：四类探测失败都要**留下 warning** 并回落到可用档。

    why 断言「有日志」而不只是「抛错」：抛错可能发生在别处（例如被上层吞掉），
    运维真正需要的是从日志里看见「请求的是哪一档、为什么没用成、实际用了哪一档」。
    """
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="siliconflow",
             SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
             SILICONFLOW_API_KEY="sk-fake-for-test")
    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: _FakeResp(**resp_kwargs))

    with caplog.at_level(logging.WARNING):
        e = emb.get_embeddings()

    assert e.identity.provider == emb.PROVIDER_HASH, "探测失败应回落到可用档"
    assert "请求档位=siliconflow" in caplog.text   # 三要素之一：请求档位
    assert marker in caplog.text                   # 三要素之二：失败原因（可读）
    assert "实际回落档位=hash" in caplog.text      # 三要素之三：实际回落档位


# ---------------------------------------------------------------- J4-3 自校准

def test_healthy_path_emits_no_failure_log_after_self_calibration(monkeypatch, caplog):
    """J4-3 自校准：先注入一条已知 log 证明采集通道有效，再断言健康路径零失败日志。

    why：不做自校准的话，「0 条日志」既可能是「真的没出错」，也可能是「采集根本没
    生效」（caplog 没接上 / 级别被调高 / logger 名改了）—— 后者会让本文件的日志断言
    全部变成假通过。因此必须先把通道证活，再下「零日志」的结论。
    """
    with caplog.at_level(logging.WARNING):
        emb.logger.warning("self-calibration-marker: 日志采集通道自检")
        assert "self-calibration-marker" in caplog.text, "采集通道未生效，零日志断言无意义"
        caplog.clear()

        e = _offline_siliconflow(monkeypatch)
        vec = e.embed_query("白糖")

    assert e.identity.provider == emb.PROVIDER_SILICONFLOW
    assert len(vec) == emb.SILICONFLOW_DIM
    assert caplog.text == "", "健康路径不得产生任何 warning/error，实际：%s" % caplog.text


# ---------------------------------------------------------------- J4-6 三档类型

def test_all_tiers_and_fallback_are_langchain_embeddings(monkeypatch):
    """J4-6：三档（含回落档）都必须是 langchain_core 的 Embeddings 实例。

    why：上层（Chroma）只按 `Embeddings` 接口消费；某一档若只是「看着像」而非该基类
    的实例，接线后会在真正写入向量时才发现（例如 Chroma 内部校验失败）。
    """
    objects = [
        emb.HashEmbeddings(),
        _offline_siliconflow(monkeypatch),
        _offline_dashscope(monkeypatch),
    ]
    # 回落档也要满足同一契约
    _set_env(monkeypatch, RAG_EMBEDDING_PROVIDER="siliconflow",
             SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
             SILICONFLOW_API_KEY="sk-fake-for-test")
    monkeypatch.setattr(emb.requests, "post",
                        lambda *a, **kw: _FakeResp(status_code=500, text="boom"))
    objects.append(emb.get_embeddings())

    for obj in objects:
        assert isinstance(obj, Embeddings), "%r 不是 Embeddings 实例" % (obj,)
        # 抽象方法真的实现了（不是靠 __getattr__ 假装的）
        assert callable(obj.embed_query) and callable(obj.embed_documents)


# ------------------------------------------------- J4-7 import 期零副作用（ast 遍历）

def _nodes_executed_at_import(tree):
    """收集「import 期会执行」的节点：函数/lambda 体内不算，类体算（类属性在 import 期求值）。

    why 用 AST 而不是「import 一下看看」：import 期副作用一旦发生（读 env / 联网 /
    建连接）就已落地，只能事后救火；静态判据才能把它变成 CI 红灯。
    """
    collected = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            pass  # 函数体只在调用期执行

        def visit_AsyncFunctionDef(self, node):
            pass

        def visit_Lambda(self, node):
            pass

        def generic_visit(self, node):
            collected.append(node)
            super().generic_visit(node)

    for node in tree.body:
        _Visitor().visit(node)
    return collected


def _module_source():
    with open(_EMB_MODULE_PATH, encoding="utf-8") as fh:
        return fh.read()


def test_module_does_not_import_rag_or_touch_vector_store_paths():
    """J4-7a：不 import `app.services.rag`，也不出现 RAG_DIR / PERSIST_DIR。

    why：本模块必须能在「还没有向量库」的场景下独立构造（import 期零副作用）。
    一旦 import rag，import 本模块就会连带加载 chromadb 与持久化目录语义，
    「测试不依赖向量库」这条前提会静默失效。
    """
    src = _module_source()
    tree = ast.parse(src)

    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert not [m for m in modules if m == "rag" or m.endswith(".rag")], modules
    assert "import rag" not in src and "from app.services import rag" not in src

    assert "RAG_DIR" not in src
    assert "PERSIST_DIR" not in src


def test_no_env_parsing_client_build_or_network_at_import_time():
    """J4-7b：env 解析 / 客户端构造 / 网络探测都只发生在 `get_embeddings()` 调用期。

    判据（静态、可复算）：import 期会执行的语句里既没有 `os.environ` 读取，也没有
    嵌入客户端构造、没有 `requests.post`。反向再验「定义了有接线」——这些动作确实
    出现在 `get_embeddings` 的调用链上，而不是死代码。
    """
    src = _module_source()
    tree = ast.parse(src)

    _FORBIDDEN_CALLS = {"HashEmbeddings", "OpenAICompatEmbeddings", "get_embeddings",
                        "_build_tier", "_probe_tier", "_resolve_pair", "resolve_provider"}
    for node in _nodes_executed_at_import(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "environ", "import 期不得读取环境变量（第 %d 行）" % node.lineno
            assert node.attr != "post", "import 期不得发起网络请求（第 %d 行）" % node.lineno
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            assert name not in _FORBIDDEN_CALLS, \
                "import 期不得构造客户端/解析配置：%s（第 %d 行）" % (name, node.lineno)

    # 反向断言（防「定义了没接线」）：这些动作必须在 get_embeddings 里真的被调用
    entry = next(n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "get_embeddings")
    entry_src = ast.get_source_segment(src, entry)
    assert "resolve_provider(" in entry_src   # env 解析（调用期）
    assert "_build_tier(" in entry_src        # 客户端构造（调用期）
    assert "_probe_tier(" in entry_src        # 网络探测（调用期）


# ------------------------------------------------------- 回落档可断言（identity 不许谎报）

def test_fallback_identity_reflects_effective_tier_not_requested(monkeypatch, caplog):
    """回落档三件套：(a) identity 是实际回落档；(b) warning 三要素齐备；(c) 维度与身份一致。

    why 这三条必须一起断言：只断言「回落了」不够 —— 若返回对象的 identity 谎报请求档
    （siliconflow:1024），上层就会把 256 维向量写进名为 siliconflow_bge_m3_1024 的集合，
    换回 bge-m3 后同一集合里混两种维度，检索要么报错要么静默错配。
    """
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="siliconflow",
             SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
             SILICONFLOW_API_KEY="sk-fake-for-test")
    monkeypatch.setattr(emb.requests, "post",
                        lambda *a, **kw: _FakeResp(status_code=500, text="upstream boom"))

    with caplog.at_level(logging.WARNING):
        e = emb.get_embeddings()

    # (a) 生效档 = 回落档，不是请求档
    assert e.identity.provider == emb.PROVIDER_HASH
    assert e.identity.provider != emb.PROVIDER_SILICONFLOW
    assert e.identity.dim == emb.HASH_DIM == 256
    assert e.identity.collection_suffix() != emb.EmbeddingIdentity(
        emb.PROVIDER_SILICONFLOW, emb.SILICONFLOW_MODEL,
        emb.SILICONFLOW_DIM).collection_suffix(), "集合后缀必须随实际档变化"

    # (b) warning 三要素：请求档位 / 失败原因 / 实际回落档位
    log = caplog.text
    assert "请求档位=siliconflow" in log
    assert "HTTP 500" in log and "upstream boom" in log
    assert "实际回落档位=hash" in log

    # (c) 维度与身份一致：实际产出的向量长度 == identity.dim
    vec = e.embed_query("德利行 本地牛腩 2斤")
    assert len(vec) == e.identity.dim


def test_fallback_prefers_siliconflow_over_hash_when_dashscope_unavailable(monkeypatch, caplog):
    """dashscope（本环境免费额度耗尽）→ 回落 siliconflow（同为 1024 维），不直接退 hash。

    why：直接退 hash 会让语义检索质量从 bge-m3 掉到 256 维字面哈希；回落链要
    「先试语义等价档，再退离线档」，并逐档留下可读日志。
    """
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="dashscope",
             DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
             DASHSCOPE_API_KEY="sk-fake-for-test",
             SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
             SILICONFLOW_API_KEY="sk-fake-for-test")

    def _post(url, **kw):
        if "dashscope" in url:
            return _FakeResp(status_code=400, text='{"error":{"code":"AllocationQuota.FreeTierOnly"}}')
        return _ok_response(emb.SILICONFLOW_DIM)

    monkeypatch.setattr(emb.requests, "post", _post)

    with caplog.at_level(logging.WARNING):
        e = emb.get_embeddings()

    assert e.identity.provider == emb.PROVIDER_SILICONFLOW
    assert e.identity.dim == emb.SILICONFLOW_DIM == 1024
    log = caplog.text
    assert "请求档位=dashscope" in log
    assert "AllocationQuota.FreeTierOnly" in log
    assert "实际回落档位=siliconflow" in log
    assert len(e.embed_query("白糖")) == e.identity.dim


def test_fallback_to_hash_when_remote_tiers_all_unavailable(monkeypatch, caplog):
    """回落链上的候选档自己也不可用时，继续往下试到 hash（终点）并逐档留日志。"""
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="dashscope",
             DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
             DASHSCOPE_API_KEY="sk-fake-for-test",
             SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1",
             SILICONFLOW_API_KEY="sk-fake-for-test")
    monkeypatch.setattr(emb.requests, "post",
                        lambda *a, **kw: _FakeResp(status_code=500, text="upstream boom"))

    with caplog.at_level(logging.WARNING):
        e = emb.get_embeddings()

    assert e.identity.provider == emb.PROVIDER_HASH
    assert "候选档位=siliconflow 不可用" in caplog.text
    assert "实际回落档位=hash" in caplog.text
    assert len(e.embed_query("白糖")) == e.identity.dim == 256


def test_fallback_to_hash_when_dashscope_creds_missing_and_siliconflow_absent(monkeypatch, caplog):
    """dashscope 凭据齐全但探测失败、且 siliconflow 凭据缺失 → 终点 hash（而不是报错）。

    这里与「请求档位凭据缺失」的处置刻意不同：请求档位缺凭据属配置错误（直接报错），
    而**回落候选**缺凭据只是「这一档不可用」，继续往下试即可。
    """
    _set_env(monkeypatch,
             RAG_EMBEDDING_PROVIDER="dashscope",
             DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
             DASHSCOPE_API_KEY="sk-fake-for-test")
    monkeypatch.setattr(emb.requests, "post",
                        lambda *a, **kw: _FakeResp(status_code=400, text="AllocationQuota.FreeTierOnly"))

    with caplog.at_level(logging.WARNING):
        e = emb.get_embeddings()

    assert e.identity.provider == emb.PROVIDER_HASH
    assert "候选档位=siliconflow 不可用" in caplog.text
    assert "实际回落档位=hash" in caplog.text


# ---------------------------------------------------------------- 调用失败不吞

def test_quota_exhausted_error_is_readable(monkeypatch):
    """免费额度耗尽（AllocationQuota.FreeTierOnly）必须转成可读错误，不得吞成空向量。"""
    e = _offline_dashscope(monkeypatch)
    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: _FakeResp(
        status_code=400,
        text='{"error":{"code":"AllocationQuota.FreeTierOnly","message":"free tier only"}}',
    ))
    with pytest.raises(RuntimeError) as ei:
        e.embed_query("德利行")
    msg = str(ei.value)
    assert "AllocationQuota.FreeTierOnly" in msg
    assert "免费额度" in msg
    assert "RAG_EMBEDDING_PROVIDER=siliconflow" in msg  # 给出可执行的替代路径


def test_http_error_is_not_swallowed(monkeypatch):
    """HTTP 非 200 抛 RuntimeError（带状态码与响应体），不返回空向量。"""
    e = _offline_siliconflow(monkeypatch)
    monkeypatch.setattr(emb.requests, "post",
                        lambda *a, **kw: _FakeResp(status_code=500, text="upstream boom"))
    with pytest.raises(RuntimeError) as ei:
        e.embed_documents(["白糖"])
    assert "500" in str(ei.value) and "upstream boom" in str(ei.value)


def test_connection_error_is_not_swallowed(monkeypatch):
    """网络异常必须抛出（而不是静默返回空/回落 hash）。"""
    e = _offline_siliconflow(monkeypatch)

    def _raise(*a, **kw):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(emb.requests, "post", _raise)
    with pytest.raises(RuntimeError) as ei:
        e.embed_query("白糖")
    assert "ConnectionError" in str(ei.value)


def test_dimension_mismatch_is_rejected(monkeypatch):
    """维度与 identity 不符必须拒绝：否则会污染同集合（hash 256 vs bge-m3 1024）。"""
    e = _offline_siliconflow(monkeypatch)
    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: _FakeResp(
        payload={"data": [{"index": 0, "embedding": [0.1] * 128}]}))
    with pytest.raises(RuntimeError) as ei:
        e.embed_query("白糖")
    assert "维度" in str(ei.value)


def test_empty_vector_is_rejected(monkeypatch):
    """空向量不得写入（本项目高发的「异常/空值被吞成正常结果」）。"""
    e = _offline_siliconflow(monkeypatch)
    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: _FakeResp(
        payload={"data": [{"index": 0, "embedding": []}]}))
    with pytest.raises(RuntimeError):
        e.embed_query("白糖")


def test_empty_documents_makes_no_request(monkeypatch):
    """空输入不发包（避免网关把空数组当参数错误），返回空列表。"""
    e = _offline_siliconflow(monkeypatch)
    calls = []

    def _record(*a, **kw):
        calls.append(kw)
        return _FakeResp(payload={"data": []})

    monkeypatch.setattr(emb.requests, "post", _record)
    assert e.embed_documents([]) == []
    assert calls == []


def test_response_rows_are_ordered_by_index(monkeypatch):
    """响应按 index 排序后再返回，避免与输入顺序错位（错位即把 A 的向量记给 B）。"""
    e = _offline_siliconflow(monkeypatch)
    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: _FakeResp(
        payload={"data": [
            {"index": 1, "embedding": [2.0] * 1024},
            {"index": 0, "embedding": [1.0] * 1024},
        ]}))
    docs = e.embed_documents(["第一", "第二"])
    assert docs[0][0] == 1.0 and docs[1][0] == 2.0


def test_successful_call_passes_model_and_bearer_header(monkeypatch):
    """请求体/请求头带上模型名与 Bearer 凭据（否则线上 401 无法定位）。"""
    e = _offline_siliconflow(monkeypatch)
    seen = {}

    def _record(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = headers
        return _ok_response(emb.SILICONFLOW_DIM)

    monkeypatch.setattr(emb.requests, "post", _record)
    e.embed_query("白糖")
    assert seen["url"] == "https://api.siliconflow.cn/v1/embeddings"
    assert seen["json"]["model"] == "BAAI/bge-m3"
    assert seen["headers"]["Authorization"] == "Bearer sk-fake-for-test"

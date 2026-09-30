# -*- coding: utf-8 -*-
"""VendorMemory 语义检索的嵌入 provider 三档实现（修 gap M7 的「假开关」）。

why 需要这个模块：`rag._CharNGramEmbeddings` 的 docstring 曾宣称「生产换
DashScope/OpenAI embedding（RAG_EMBEDDING_PROVIDER 开关）」，但该环境变量在全仓
**零读取**（gap 分析 M7）—— 实际只有 char-2/3-gram 哈希一档，开关是假的：运维按
文档改了键却没有任何效果，且这种「改了但没生效」不会有任何日志。这里把开关做成
真的（rag 侧已接线，见 `get_embeddings` 的消费方段），并提供 identity 供上层把
provider/模型/维度写进 collection 名：hash 档 256 维、bge-m3 档 1024 维，两者
**不可混写同一集合**（混写即维度报错或静默语义错配）。

三档语义：
- hash（缺省）：纯离线确定性 char-2/3-gram 哈希 + L2 归一，零网络依赖，测试/CI 的
  默认档，行为与现状逐行一致（零行为变化）；
- siliconflow：OpenAI 兼容 /embeddings，缺省 BAAI/bge-m3（本机实测 HTTP 200、
  1024 维）；base 与 key 从环境变量**成对**解析，缺任一则显式报错；
- dashscope：text-embedding-v4（1024 维）；本机免费额度已耗尽，接口会返回
  AllocationQuota.FreeTierOnly —— 必须转成可读错误信息抛出，不得吞成空向量。

失败处理纪律（对应本仓高发的「异常被静默吞掉致功能静默失效」）分两类，判据是
「这个失败还能不能保住请求档位的语义」：

- **非法 provider 名 / 凭据不成对** → `logger.error` + raise，不猜用户意图、不借别家
  凭据。非法名回落会把 M7 的假开关原样复活（按文档改了键、实际仍在跑哈希）；
  凭据错配回落则会让「以为 bge-m3 在跑、实际是 256 维哈希」的错觉长期无人察觉；
- **可用性探测失败**（凭据齐全、但真调用失败：HTTP 错误、免费额度耗尽、返回维度与
  identity 不符、返回空向量）→ **显式 warning 后回落到可用档**（`_fallback_to_available`）。
  warning 必须含「请求档位 / 失败原因 / 实际回落档位」三要素，且**返回对象的
  `identity` 必须反映实际生效档** —— 否则 256 维与 1024 维会被写进同一个 collection
  名，`collection_suffix()` 必须随实际档变化。

两类都不静默：前者抛错、后者留日志，任何情况下都不「无声地换档」。
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import requests
from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

# provider 白名单（三档，恰为此三值；其他一律显式报错）
PROVIDER_HASH = "hash"
PROVIDER_SILICONFLOW = "siliconflow"
PROVIDER_DASHSCOPE = "dashscope"
VALID_PROVIDERS = (PROVIDER_HASH, PROVIDER_SILICONFLOW, PROVIDER_DASHSCOPE)

# 环境变量：RAG_EMBEDDING_PROVIDER 是本模块的唯一开关（缺省 hash，与现状一致）
ENV_PROVIDER = "RAG_EMBEDDING_PROVIDER"
DEFAULT_PROVIDER = PROVIDER_HASH

# 各档模型与维度（维度用于 identity，是「不可混写同一 collection」的判据）
HASH_MODEL = "char-ngram-hash"
HASH_DIM = 256
SILICONFLOW_MODEL = "BAAI/bge-m3"
SILICONFLOW_DIM = 1024
DASHSCOPE_MODEL = "text-embedding-v4"
DASHSCOPE_DIM = 1024

# 成对凭据候选（顺序即优先级）：base 与 key 必须来自同一对，见 _resolve_pair
SILICONFLOW_PAIRS = (
    ("SILICONFLOW_BASE_URL", "SILICONFLOW_API_KEY"),
    ("OPENAI_BASE_URL", "OPENAI_API_KEY"),
)
DASHSCOPE_PAIRS = (("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY"),)

_HTTP_TIMEOUT_SECONDS = 30.0

# 可用性自检的探测文本（短、固定、无业务含义：只验证「这一档现在能不能用」）
_PROBE_TEXT = "embedding 可用性探测"

# 探测失败后的回落链：请求档位 → 依序尝试的候选档。
# why 逐个候选而不是直接退 hash：dashscope 不可用时 siliconflow（同为 1024 维
# bge-m3）仍可能可用，直接退 hash 会让语义检索质量白白降到 256 维哈希档；
# hash 是链路终点（离线确定性、恒可用），保证回落必有结果。
FALLBACK_CHAIN = {
    PROVIDER_SILICONFLOW: (PROVIDER_HASH,),
    PROVIDER_DASHSCOPE: (PROVIDER_SILICONFLOW, PROVIDER_HASH),
}


@dataclass(frozen=True)
class EmbeddingIdentity:
    """嵌入身份：provider 名 + 模型名 + 向量维度。

    why 需要它：不同 provider/模型产出的向量维度与语义空间都不同
    （hash 256 维 vs bge-m3 1024 维），写进同一个 Chroma collection 会直接维度
    报错或静默语义错配。上层用 `collection_suffix()` 拼集合名，切换 provider
    即新建集合而不是混写。
    """

    provider: str
    model: str
    dim: int

    @property
    def label(self) -> str:
        """人类可读身份串，如 `hash:char-ngram-hash:256`。"""
        return "%s:%s:%d" % (self.provider, self.model, self.dim)

    def collection_suffix(self) -> str:
        """collection 名安全后缀（Chroma 只接受字母数字与 -_，其余压成下划线）。"""
        return re.sub(r"[^a-zA-Z0-9]+", "_", self.label).strip("_").lower()

    def as_dict(self) -> dict:
        """供写进 collection metadata（键名固定，便于巡检工具比对）。"""
        return {"embedding_provider": self.provider,
                "embedding_model": self.model,
                "embedding_dim": self.dim}


class HashEmbeddings(Embeddings):
    """离线确定性嵌入（无 API 依赖）：char-2/3-gram 哈希 + L2 归一。

    算法与 `rag._CharNGramEmbeddings`（曾位于 demo/app/services/rag.py:66-98）逐行一致，
    why：本档是缺省档，必须与接线前产生同一批向量（零行为变化）。**rag.py 里那份副本
    已随接线删除**（`rag._embedding_provider` 改为调用本模块）—— 同一算法只此一份，
    否则日后改算法只改一处，会出现「同一份语料用两种嵌入写入同一集合」的静默错配。
    """

    def __init__(self, dim: int = HASH_DIM):
        self.dim = int(dim)
        self.identity = EmbeddingIdentity(PROVIDER_HASH, HASH_MODEL, self.dim)

    def embed_documents(self, texts) -> List[List[float]]:
        return [self._embed(t) for t in (texts or [])]

    def embed_query(self, text) -> List[float]:
        return self._embed(text)

    def _embed(self, text) -> List[float]:
        import hashlib
        import unicodedata
        norm = unicodedata.normalize("NFKC", str(text or "")).casefold().replace(" ", "")
        vec = [0.0] * self.dim
        for n in (2, 3):
            if len(norm) < n:
                bucket = int(hashlib.sha256(norm.encode()).hexdigest(), 16) % self.dim
                vec[bucket] += 1.0
                continue
            for i in range(len(norm) - n + 1):
                gram = norm[i:i + n]
                bucket = int(hashlib.sha256(gram.encode()).hexdigest(), 16) % self.dim
                vec[bucket] += 1.0
        norm_factor = sum(c * c for c in vec) ** 0.5
        if norm_factor > 0:
            vec = [c / norm_factor for c in vec]
        return vec


class OpenAICompatEmbeddings(Embeddings):
    """OpenAI 兼容 /embeddings 客户端（siliconflow 与 dashscope 共用一份实现）。

    why 不用 openai SDK：本环境未安装 openai / langchain_openai（实测
    ModuleNotFoundError），而 requests 已是 demo/requirements.txt 的既有依赖
    （第 11 行），不为嵌入新增依赖。

    错误纪律：HTTP 非 200、响应缺 data、向量维度与 identity 不符、连接异常，
    一律 logger.warning + raise（绝不返回空向量）。本类**不在内部换档** —— 换档
    只发生在 `get_embeddings` 的探测/回落策略里，且必定留三要素 warning，因此
    「用哪一档」始终可从日志与返回对象的 identity 反推。错误文案里带
    provider / model / 状态码，且不打印密钥。
    """

    def __init__(self, provider: str, model: str, dim: int, base_url: str,
                 api_key: str, timeout: float = _HTTP_TIMEOUT_SECONDS):
        self.provider = provider
        self.model = model
        self.dim = int(dim)
        self.base_url = base_url
        self._api_key = api_key
        self.timeout = float(timeout)
        self.identity = EmbeddingIdentity(provider, model, self.dim)

    def embed_documents(self, texts) -> List[List[float]]:
        items = [str(t or "") for t in (texts or [])]
        if not items:
            return []  # 空输入不发起请求（也避免某些网关把空数组当参数错误）
        return self._request(items)

    def embed_query(self, text) -> List[float]:
        return self._request([str(text or "")])[0]

    def _request(self, texts: Sequence[str]) -> List[List[float]]:
        url = self.base_url.rstrip("/") + "/embeddings"
        try:
            resp = requests.post(
                url,
                json={"model": self.model, "input": list(texts)},
                headers={"Authorization": "Bearer %s" % self._api_key},
                timeout=self.timeout,
            )
        except Exception as exc:  # 网络/超时/连接错误：绝不吞
            msg = ("%s embedding 请求失败（url=%s, model=%s）：%s: %s"
                   % (self.provider, url, self.model, type(exc).__name__, exc))
            logger.warning(msg)
            raise RuntimeError(msg) from exc

        if getattr(resp, "status_code", 0) != 200:
            msg = self._readable_http_error(resp)
            logger.warning(msg)
            raise RuntimeError(msg)

        try:
            body = resp.json()
        except Exception as exc:
            msg = ("%s embedding 响应不是合法 JSON（model=%s）：%s"
                   % (self.provider, self.model, exc))
            logger.warning(msg)
            raise RuntimeError(msg) from exc

        data = body.get("data") if isinstance(body, dict) else None
        if not data:
            msg = ("%s embedding 响应缺少 data 字段（model=%s）：%s"
                   % (self.provider, self.model, str(body)[:300]))
            logger.warning(msg)
            raise RuntimeError(msg)

        rows = sorted(data, key=lambda d: d.get("index", 0) if isinstance(d, dict) else 0)
        vectors = []
        for row in rows:
            vec = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(vec, list) or not vec:
                msg = ("%s embedding 返回了空向量（model=%s）——拒绝写入，"
                       "避免污染向量库" % (self.provider, self.model))
                logger.warning(msg)
                raise RuntimeError(msg)
            if len(vec) != self.dim:
                msg = ("%s embedding 返回维度 %d 与 identity 声明 %d 不一致："
                       "不同维度的向量不可混写同一 collection，请把 provider 标识"
                       "写进集合名（identity.collection_suffix()）后重建集合"
                       % (self.provider, len(vec), self.dim))
                logger.warning(msg)
                raise RuntimeError(msg)
            vectors.append(vec)
        return vectors

    def _readable_http_error(self, resp) -> str:
        """把 HTTP 错误转成可读信息；免费额度耗尽要单独说清。

        why：百炼免费额度耗尽时返回 400 + `AllocationQuota.FreeTierOnly`，若原样
        抛出或吞成空向量，运维只会看到「检索没有结果 / 记忆不生效」，查不到真因。
        """
        body = (getattr(resp, "text", "") or "")[:500]
        status = getattr(resp, "status_code", "?")
        if "AllocationQuota" in body or "FreeTierOnly" in body:
            hint = ""
            if self.provider == PROVIDER_DASHSCOPE:
                hint = ("请在百炼控制台开通付费额度，或改用 "
                        "RAG_EMBEDDING_PROVIDER=siliconflow（BAAI/bge-m3）或 hash。")
            return ("%s 嵌入免费额度已耗尽（AllocationQuota.FreeTierOnly，"
                    "HTTP %s, model=%s）。%s原始响应：%s"
                    % (self.provider, status, self.model, hint, body))
        return ("%s embedding 接口返回 HTTP %s（model=%s）：%s"
                % (self.provider, status, self.model, body))


def resolve_provider(provider: Optional[str] = None) -> str:
    """解析生效档位：显式入参 > 环境变量 RAG_EMBEDDING_PROVIDER > 缺省 hash。

    非法档位名**显式报错**（`logger.error` + ValueError），不回落 hash：回落会让
    「按文档改了键、实际却仍在用哈希」这个 M7 的原始缺陷原样复活。不猜用户意图
    （`openai` / `bge-m3` 这类看着像档位名的拼写也一律报错，让人看到可选值）。
    """
    raw = provider if provider is not None else os.environ.get(ENV_PROVIDER)
    name = (raw or "").strip().lower()
    if not name:
        return DEFAULT_PROVIDER
    if name not in VALID_PROVIDERS:
        msg = ("未知的 embedding provider %r：可选 %s（环境变量 %s）；"
               "不静默回落 hash —— 回落会让维度与语义变化无人知晓"
               % (raw, "/".join(VALID_PROVIDERS), ENV_PROVIDER))
        logger.error(msg)
        raise ValueError(msg)
    return name


# 档位 → (模型, 维度)：命名/观测用途的唯一映射（真实对象仍由 _build_tier 构造）
_TIER_SPECS = {
    PROVIDER_HASH: (HASH_MODEL, HASH_DIM),
    PROVIDER_SILICONFLOW: (SILICONFLOW_MODEL, SILICONFLOW_DIM),
    PROVIDER_DASHSCOPE: (DASHSCOPE_MODEL, DASHSCOPE_DIM),
}


def requested_identity(provider: Optional[str] = None) -> EmbeddingIdentity:
    """按**请求档位**给出 identity：不构造客户端、不读凭据、不联网。

    why 需要它：集合名必须在**建库之前**就确定（Chroma 构造时 collection 名与嵌入
    函数一次定死），而 `get_embeddings()` 会做可用性探测（对远端档是真发一次请求）
    并可能按回落链换档 —— 命名若走它，等于为取个名字多打一次网络，还会在「探测
    失败回落」时拿到与实际生效档不一致的结论（名字写 bge-m3、向量却是 256 维哈希，
    正是本模块最要防的错配）。

    两处口径分工（缺一不可）：
    - **命名 / 巡检 / 测试**取「请求档」→ 本函数（零副作用）；
    - **真正决定写入哪一档**的地方取「实际生效档」→ `get_embeddings()` 返回对象的
      `.identity`（探测失败时它已变成回落档）。
    非法档位名与 `get_embeddings` 同口径：显式报错，不回落 hash。
    """
    name = resolve_provider(provider)
    model, dim = _TIER_SPECS[name]
    return EmbeddingIdentity(name, model, dim)


def _resolve_pair(provider: str, candidates: Sequence[Tuple[str, str]]) -> Tuple[str, str]:
    """按候选顺序解析**成对**凭据 (base_url, api_key)。

    why 成对：base 与 key 跨 provider 错配（A 家地址 + B 家密钥）必然 401，还会让
    「切了 provider」看起来像「服务坏了」。因此只接受完整的一对：某一对的 base 有值
    而 key 为空时，不借用别家的 key，直接尝试下一对。全部候选都不完整则显式报错，
    错误里只说明哪些键缺失（不打印任何密钥值）。

    这是**配置错误**（连探测都无法发起），因此 `logger.error` + raise，不走回落：
    靠回落掩盖会让「以为 bge-m3 在跑、实际是 256 维哈希」的错觉长期存在。
    """
    missing = []
    for base_env, key_env in candidates:
        base = (os.environ.get(base_env) or "").strip()
        key = (os.environ.get(key_env) or "").strip()
        if base and key:
            return base, key
        missing.append("%s=%s / %s=%s" % (
            base_env, "已设" if base else "缺失",
            key_env, "已设" if key else "缺失",
        ))
    msg = ("embedding provider=%s 的凭据不完整（base 与 key 必须成对提供）：%s；"
           "不静默降级到其他档位（含 hash）"
           % (provider, "；".join(missing)))
    logger.error(msg)
    raise RuntimeError(msg)


def _build_tier(name: str) -> Embeddings:
    """按档位名构造嵌入对象（只解析配置、**不联网**）。

    why 构造与探测拆成两步：构造期只能发现「凭据缺了」，而 base 写错、模型下线、
    免费额度耗尽、返回维度不符、返回空向量这些**只有真调用才暴露**；分开之后
    `get_embeddings` 才能在「构造成功但探测失败」时按回落链换档而不误报配置错误。
    """
    if name == PROVIDER_HASH:
        return HashEmbeddings()
    if name == PROVIDER_SILICONFLOW:
        base, key = _resolve_pair(PROVIDER_SILICONFLOW, SILICONFLOW_PAIRS)
        return OpenAICompatEmbeddings(PROVIDER_SILICONFLOW, SILICONFLOW_MODEL,
                                      SILICONFLOW_DIM, base, key)
    # 到这里只剩 dashscope（resolve_provider 已做白名单校验）
    base, key = _resolve_pair(PROVIDER_DASHSCOPE, DASHSCOPE_PAIRS)
    return OpenAICompatEmbeddings(PROVIDER_DASHSCOPE, DASHSCOPE_MODEL,
                                  DASHSCOPE_DIM, base, key)


def _probe_tier(obj: Embeddings) -> None:
    """可用性自检：真发一次最小请求（失败按异常抛出，由调用方决定是否回落）。

    why 必须真调用而不是只看凭据非空：本环境 dashscope 档就是「key 有效但免费额度
    耗尽」（HTTP 400 + AllocationQuota.FreeTierOnly），只查凭据会判它「可用」。
    `OpenAICompatEmbeddings._request` 已把 HTTP 错误 / 维度不符 / 空向量 / 连接异常
    统一转成带 provider、model、状态码的 RuntimeError，探测直接复用这条链路。
    """
    obj.embed_query(_PROBE_TEXT)


def _log_fallback(requested: str, reason: str, actual: str) -> None:
    """回落必须可见：请求档位 / 失败原因 / 实际回落档位三要素缺一不可。

    why 三要素：回落本身意味着「维度与语义空间都换了」（hash 256 维 vs bge-m3
    1024 维），运维要从一条日志里就能判断「这次检索实际用的是哪一档、为什么、
    原来想用哪一档」，否则只能靠猜 —— 那正是 M7「改了键却不知道生效了没有」的土壤。
    """
    logger.warning(
        "embedding provider 回落：请求档位=%s；失败原因=%s；实际回落档位=%s；"
        "维度与语义空间已改变，collection 名须按实际档的 identity.collection_suffix() "
        "重建，不得与请求档位的向量混写", requested, reason, actual)


def _fallback_to_available(requested: str, reason: str) -> Embeddings:
    """按 `FALLBACK_CHAIN` 逐个尝试候选档，返回**首个探测通过**的档。

    候选档自己不可用时（凭据缺失 / 探测失败）继续往下试并各留一条 warning，
    绝不中途返回「没探测过的档」。hash 是链路终点且离线恒可用，故本函数要么返回
    一个探测通过的档、要么显式抛错（不会静默返回半可用对象）。
    """
    candidates = FALLBACK_CHAIN.get(requested, (PROVIDER_HASH,))
    for candidate in candidates:
        try:
            obj = _build_tier(candidate)
            _probe_tier(obj)
        except Exception as exc:
            # 显式记录类型与信息（不静默）：候选不可用本身可能是运维的关键线索
            logger.warning(
                "embedding 回落候选档位=%s 不可用（%s: %s），继续尝试下一档",
                candidate, type(exc).__name__, exc)
            continue
        _log_fallback(requested, reason, candidate)
        return obj
    raise RuntimeError(
        "embedding 请求档位=%s 探测失败（%s），且回落链 %s 全部不可用"
        % (requested, reason, "/".join(candidates)))


def get_embeddings(provider: Optional[str] = None) -> Embeddings:
    """按 provider 构造嵌入对象；provider=None 时读 RAG_EMBEDDING_PROVIDER（缺省 hash）。

    返回对象一律带 `identity`（EmbeddingIdentity：provider / 模型 / 维度），供上层
    把标识写进 collection 名或 metadata。

    生效档与 identity 的对应关系（**identity 永远反映实际生效档**）：
    - 请求档位探测通过 → 返回该档，identity 即该档；
    - 请求档位探测失败 → 显式 warning（三要素）后回落到首个可用档，返回**回落档**的
      对象与 identity，因此 `collection_suffix()` 随之变化（256 维与 1024 维不会混写）；
    - 非法档位名 / 凭据不成对 → 直接报错，不回落（见模块 docstring 的两类判据）。

    构造期不读 env、不联网；env 解析与客户端构造都发生在本函数被调用时（import
    `app.services.embeddings` 本身零副作用、零网络）。

    消费方：`rag._embedding_provider`（demo/app/services/rag.py）—— 已接线为
    `return get_embeddings()`，原地的 `_CharNGramEmbeddings` 副本已删除，故
    `RAG_EMBEDDING_PROVIDER` 现在是真的开关（gap M7 解除）。rag 侧另用
    `requested_identity()` 取集合名后缀，并用本函数返回对象的 `.identity` 保证
    「集合名 ↔ 实际生效档」一致。
    """
    name = resolve_provider(provider)
    if name == PROVIDER_HASH:
        return HashEmbeddings()  # 离线确定性、恒可用，不需要探测（也不许发起网络请求）
    obj = _build_tier(name)  # 凭据不成对在这里显式报错（配置错误，不回落）
    try:
        _probe_tier(obj)
    except Exception as exc:
        return _fallback_to_available(name, "%s: %s" % (type(exc).__name__, exc))
    return obj

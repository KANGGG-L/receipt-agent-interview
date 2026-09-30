# -*- coding: utf-8 -*-
"""VendorMemory 混合召回（关键词腿 + 向量腿）与治理信号 rerank（P3-T6 / P7·Y2）。

为什么关键词腿是**必选腿**而不是"可选增强"：本项目的记忆语料是「短商品名 + 供应商名 +
单位」（如 `菜心 斤 8.00-10.00`、`别名：本吧 → 本地牛腩`），且缺省嵌入档是 char-ngram
哈希（离线确定性、**没有语义能力**，见 embeddings 模块）。在这种短文本上，字符 n-gram +
词频/逆文档频率加权的区分度明显强于纯向量相似度（共享的"供应商 X 的已确认进货明细："
前缀会把余弦相似度整体拉平）。故本模块自带一个确定性的 BM25 近似实现：**零网络、零新
依赖**，与向量腿候选合并去重后统一重排。

为什么 rerank 必须消费治理信号（本 Task 是 R2 的处置）：`hit_count` / `decay_score` /
`last_hit_at` 此前是**只写列** —— 写侧（bump_vendor_memory_hit / T8 衰减）一直在维护，
读侧（注入排序）完全不看，等于"记忆的治理状态不影响任何注入决策"。现在排序分显式由
五个分项相加，每一项都可追溯到某个输入（权重见 `RecallWeights`，**全部可配可审计**）：

    score = w_leg        * leg_base[leg]      # 来源腿基础分（精确腿 > 关键词腿 > 向量腿）
          + w_relevance  * relevance          # 该腿自己的相关度（BM25 归一值 / 向量腿排名代理）
          + w_exact_match* exact_vendor_match  # 查询供应商名与候选来源名**归一后相等**
          + w_decay      * decay_score        # 治理信号①：衰减分（越高越可信，T8 维护）
          + w_hit        * hit_score          # 治理信号②：历史命中次数（log 饱和，微权重）
          + w_recency    * freshness          # 治理信号③：最近命中时间的新鲜度

每一项都归一化到 [0,1]，故总分区间约 [0, 2.5]（缺省权重下）。两条"文本相关"信号严格
分工，避免同一根因两份实现：**精确匹配 boost 只管"是不是这家供应商"（供应商名归一相等，
0/1）**，**文本相关度只由关键词腿的 BM25 负责（分级、idf 加权、长度归一）**；故"关掉
关键词腿"的对照实验真的会让排序退化为与查询无关的原序（见 `rerank` 的排序契约）。

**可审计**（任务要求"权重必须可配/可审计，不得黑箱"）：
- 可配：`RecallWeights.from_settings()` 读 settings 键 `memory_recall_weights`（JSON 对象，
  只覆盖显式给出的字段）；缺省值即模块级 `DEFAULT_WEIGHTS`，读失败留 warning 后回退缺省。
- 可审计：`RecallScore.components` 给出**每一分项的实际贡献**，`explain()` 把它拍平成可
  打印结构；rag 侧在 DEBUG 级别打一条「权重 + 前若干条分数明细」的汇总日志（不改检索行为）。

**确定性**：分词、BM25、打分、排序全部无随机、无网络、无时钟依赖（时间经 `now` 入参注入，
缺省取当前时间）—— 同一输入必得同一排序，验收数字可复算。
"""

import json
import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 来源腿标识（与 rag 侧传参同名；单列常量避免两处各抄字面量 —— 本仓①类缺陷）
RECALL_LEG_EXACT = "exact"      # 精确/别名腿：scope 收窄后该供应商自己的记忆行
RECALL_LEG_KEYWORD = "keyword"  # 关键词腿（必选）：BM25 近似，本模块实现
RECALL_LEG_VECTOR = "vector"    # 向量腿：Chroma 相似度检索

# 治理信号权重的 settings 键（JSON 对象；只覆盖显式给出的字段）
WEIGHTS_SETTINGS_KEY = "memory_recall_weights"

# BM25 参数（标准缺省：k1 控制词频饱和、b 控制长度归一强度）
BM25_K1 = 1.2
BM25_B = 0.75

# hit_count 的饱和点：达到该次数后 hit_score 视为 1.0。
# why 需要饱和：hit_count 是**累计**计数，热门行会无限增长；若不设上限，老行会靠
# "被注入得多"永久霸榜，把新记忆彻底挤出预算（治理信号变成路径依赖）。
HIT_SATURATION = 10.0

# last_hit_at 新鲜度的半衰期（天）：新鲜度 = 0.5 ** (age_days / 30)。
# 语义（写清，避免黑箱）：它度量的是"这条记忆**最近一次被注入**距今多久"，不是
# "这条记忆什么时候被创建"（创建时间是 created_at，本模块**不读**它）。
# 空值 / 不可解析 → 0.0，即"没有新鲜度加成"，**不是惩罚** —— "从未命中"不是负面证据，
# 质量判断由 decay_score 表达（两者刻意分开：命中次数少 ≠ 记忆质量差）。
FRESHNESS_HALF_LIFE_DAYS = 30.0

# 精确命中率的取词口径：只取长度 >= 2 的 token。
# why 不取单字 CJK token：单字（如"菜""心""的"）在任何中文文本里都可能出现，用它算
# "精确命中率"会把所有候选一起抬高、失去区分度。长度 >= 2 的 token = CJK bigram +
# ASCII/数字词，正是"短商品名/单位/供应商名"的有信息量单位。
_EXACT_TOKEN_MIN_LEN = 2

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_ALNUM_RE = re.compile(r"[a-z0-9]+")


def tokenize(text) -> Tuple[str, ...]:
    """确定性分词：NFKC + casefold，CJK 取单字 + 二元组，ASCII/数字取整串。

    why CJK 同时给单字与二元组：语料是短商品名（"菜心"、"急凍牛肋條"），二元组能精确
    命中商品名，单字保证"二字写法只差一个字"时仍有重叠（召回优先于精确，排序交给
    rerank）。为什么不用 jieba 之类的分词：本仓禁新增依赖，且外部词典带来的不确定性会让
    排序不可复算（本模块的硬要求是"同一输入必得同一排序"）。
    """
    norm = unicodedata.normalize("NFKC", str(text or "")).casefold()
    tokens: List[str] = []
    for m in _ALNUM_RE.finditer(norm):
        tokens.append(m.group())          # 数字/英文词整串（单位、货号、价格）
    for m in _CJK_RE.finditer(norm):
        seg = m.group()
        tokens.extend(seg)                # 单字
        for i in range(len(seg) - 1):
            tokens.append(seg[i:i + 2])   # 二元组
    return tuple(tokens)


def _exact_tokens(text) -> set:
    """参与「精确命中率」的 token 集合（见 _EXACT_TOKEN_MIN_LEN 的口径说明）。"""
    return {t for t in tokenize(text) if len(t) >= _EXACT_TOKEN_MIN_LEN}


def bm25_scores(query: str, docs: Sequence[Tuple[str, str]]) -> Dict[str, float]:
    """BM25 近似打分（自建，零网络）：docs = [(doc_id, text)] → {doc_id: 归一值}。

    公式（BM25 okapi，标准缺省参数 k1=1.2 / b=0.75）：

        idf(t)   = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
        score(d) = Σ_t idf(t) * f(t,d)*(k1+1) / (f(t,d) + k1*(1 - b + b*|d|/avgdl))

    为什么用 BM25 而不是"词频归一"（任务文字里的备选）：idf 项正是本场景的关键 ——
    每条记忆都带"供应商 X 的已确认进货明细："这类**所有文档共有**的前缀，纯词频会让
    这些无区分度的字把分数整体抬高；idf 把它们压到接近 0，让商品名/单位这类稀有 token
    主导排序。

    输出归一化到 [0,1]（除以本批最大值）—— 目的是让"相关度"能与其它分项相加比较。
    **归一值是批内相对量**：只用于同一次调用的同一批候选之间比较，不得跨批次/跨 query
    当绝对指标使用（契约写在这里，避免被当成相似度复用）。

    空查询 / 空语料 → 全 0（不是异常：检索腿必须 fail-open，不得阻断识别链路）。
    """
    doc_tokens = {str(did): tokenize(text) for did, text in (docs or [])}
    if not doc_tokens:
        return {}
    q_tokens = list(set(tokenize(query)))
    if not q_tokens:
        return {did: 0.0 for did in doc_tokens}

    n_docs = len(doc_tokens)
    avgdl = sum(len(t) for t in doc_tokens.values()) / float(n_docs) or 1.0
    df = Counter()
    for toks in doc_tokens.values():
        df.update(set(toks))

    raw: Dict[str, float] = {}
    for did, toks in doc_tokens.items():
        dl = len(toks) or 1
        tf = Counter(toks)
        s = 0.0
        for q in q_tokens:
            f = tf.get(q, 0)
            if not f:
                continue
            idf = math.log(1.0 + (n_docs - df[q] + 0.5) / (df[q] + 0.5))
            s += idf * (f * (BM25_K1 + 1.0)) / (
                f + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl))
        raw[did] = s
    top = max(raw.values()) if raw else 0.0
    if top <= 0.0:
        return {did: 0.0 for did in raw}
    return {did: v / top for did, v in raw.items()}


# 精确匹配 boost 的归一化（与 rag._norm_vendor_name 同口径的"轻量"版本）：
# 去空白 + 标点 + casefold + NFKC。why 不 import rag 的实现：rag 依赖 langchain/Chroma，
# 本模块要能被纯逻辑单测独立导入（零重依赖）；两处口径若漂移，由验收用例的
# 「同一条记忆在繁简/空白/标点差异下仍算精确匹配」断言守住。
_PUNCT_RE = re.compile(r"[()（）\[\]【】\-_/\\,.:;\"'·\s]+")


def _norm_name(name) -> str:
    """供应商名归一（NFKC + casefold + 去空白标点）——只用于「精确匹配」判定。"""
    s = unicodedata.normalize("NFKC", str(name or "")).casefold()
    return _PUNCT_RE.sub("", s).strip()


def exact_vendor_match(doc_vendor, query_vendor) -> float:
    """精确匹配 boost（0/1）：候选记忆的来源供应商名与查询供应商名**逐字归一相等**。

    why 只认"归一后相等"而不是"子串/相关"：后者是名字相关度门（fuzzy）的活；本项要表达
    的是「这条记忆就是这个供应商的，不是名字相近的另一家」—— 它与 fuzzy 的边界正是
    "大生" vs "大生隆" 这类互相包含的真实商号。两者都由 DB 侧的 scope 收窄与
    `_vendor_related` 承担召回，本项只在**排序**上给"完全同名"一点加成。

    why 不把「查询串在候选文本里出现」也算进本项：那等于在本模块里再实现一份（更弱的）
    BM25，与关键词腿同源重复（本仓①类缺陷），而且会让"关掉关键词腿"的对照实验失去意义
    （排序仍被一个关键词类信号支配）。文本相关度只由关键词腿（BM25）一条通道负责。
    """
    q = _norm_name(query_vendor)
    d = _norm_name(doc_vendor)
    if not q or not d:
        return 0.0
    return 1.0 if q == d else 0.0


def hit_score(hit_count) -> float:
    """命中次数 → [0,1]（log 饱和，见 HIT_SATURATION）。

    非法值（None / 非数字）→ 0.0：治理信号读不出来时**不给加成**，也不抛错 ——
    它是加分项，不是判据（判据在 rag 的治理门里）。
    """
    try:
        n = float(hit_count or 0)
    except (TypeError, ValueError):
        return 0.0
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(HIT_SATURATION))


def freshness(last_hit_at, now: Optional[datetime] = None) -> float:
    """最近命中时间的新鲜度 ∈[0, 1]（语义见 FRESHNESS_HALF_LIFE_DAYS 的注释）。

    空值 / 不可解析 → 0.0（无加成、不惩罚）。未来时间（时钟漂移/脏数据）按 age=0
    处理（给满分而不是负分：不因为时间戳异常去惩罚一条记忆）。
    """
    raw = str(last_hit_at or "").strip()
    if not raw:
        return 0.0
    try:
        ts = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return 0.0
    ref = now or datetime.now()
    try:
        if (ts.tzinfo is None) != (ref.tzinfo is None):
            # 一边带时区一边不带：无法可靠比较 → 按"无新鲜度加成"处理（契约见上）
            return 0.0
        age_days = (ref - ts).total_seconds() / 86400.0
    except Exception:
        return 0.0
    if age_days < 0:
        age_days = 0.0
    return 0.5 ** (age_days / FRESHNESS_HALF_LIFE_DAYS)


def exact_hit_ratio(query: str, text: str) -> float:
    """（保留的诊断函数，**不参与打分**）查询词在候选文本里的 token 命中率 ∈[0,1]。

    why 不删：验收/排查时需要"这条候选与查询在文本上重合多少"这个读数（例如给出
    "混合召回命中的那条与查询的 token 重合率"）。why 不参与打分：文本相关度只由关键词腿
    的 BM25 一条通道负责，本函数若也进公式，就是同一信号的第二份实现（见
    `exact_vendor_match` 的说明）。
    """
    q = _exact_tokens(query)
    if not q:
        return 0.0
    return len(q & _exact_tokens(text)) / float(len(q))


@dataclass(frozen=True)
class RecallWeights:
    """rerank 权重（全部可在 settings 键 `memory_recall_weights` 里覆盖）。

    字段名 → 公式里哪一项：
      leg_exact / leg_keyword / leg_vector -> leg_base[leg]（来源腿基础分）
      relevance   -> w_relevance（该腿自己的相关度）
      exact_match -> w_exact_match（精确匹配 boost：查询供应商名与候选来源名归一后相等）
      decay       -> w_decay（治理信号①：decay_score）
      hit         -> w_hit（治理信号②：hit_count，log 饱和的微权重）
      recency     -> w_recency（治理信号③：last_hit_at 新鲜度）

    why 用 dataclass 而不是 dict：字段名即公式项名，改权重时不可能"改了 key 名却没人
    消费"（本仓②类缺陷）；`as_dict()` 让它同时可写进日志/返回体，满足"可审计"。
    """

    leg_exact: float = 1.0
    leg_keyword: float = 0.6
    leg_vector: float = 0.5
    relevance: float = 0.5
    exact_match: float = 0.6
    decay: float = 0.3
    hit: float = 0.05
    recency: float = 0.05

    def leg_base(self, leg: str) -> float:
        """来源腿基础分：精确腿最高（它是 scope 收窄后的权威命中）。"""
        if leg == RECALL_LEG_EXACT:
            return self.leg_exact
        if leg == RECALL_LEG_KEYWORD:
            return self.leg_keyword
        if leg == RECALL_LEG_VECTOR:
            return self.leg_vector
        return 0.0

    def as_dict(self) -> dict:
        """可打印/可落日志的权重快照（审计用）。"""
        return {
            "leg_exact": self.leg_exact,
            "leg_keyword": self.leg_keyword,
            "leg_vector": self.leg_vector,
            "relevance": self.relevance,
            "exact_match": self.exact_match,
            "decay": self.decay,
            "hit": self.hit,
            "recency": self.recency,
        }

    @classmethod
    def from_settings(cls) -> "RecallWeights":
        """按 settings 键 `memory_recall_weights` 覆盖缺省值（只覆盖显式给出的字段）。

        契约：读取失败 / JSON 非法 / 值非数字 → 保留该字段的缺省值并 logger.warning
        （**不抛错**：权重读取故障不得阻断识别链路），warning 里带原始值以便排查。
        未配置该键（缺省）时直接返回 `DEFAULT_WEIGHTS`，零额外副作用。
        """
        try:
            from app.services import settings_service
            raw = settings_service.get_str(WEIGHTS_SETTINGS_KEY, "")
        except Exception as e:
            logger.warning(
                "[recall] 读 %s 失败，rerank 权重回退缺省 %s：%s",
                WEIGHTS_SETTINGS_KEY, DEFAULT_WEIGHTS.as_dict(), e)
            return DEFAULT_WEIGHTS
        text = str(raw or "").strip()
        if not text:
            return DEFAULT_WEIGHTS
        try:
            data = json.loads(text)
        except Exception as e:
            logger.warning(
                "[recall] %s 不是合法 JSON（raw=%r），rerank 权重整体回退缺省：%s",
                WEIGHTS_SETTINGS_KEY, text, e)
            return DEFAULT_WEIGHTS
        if not isinstance(data, dict):
            logger.warning("[recall] %s 必须是 JSON 对象，实际 %s；回退缺省",
                           WEIGHTS_SETTINGS_KEY, type(data).__name__)
            return DEFAULT_WEIGHTS
        base = DEFAULT_WEIGHTS.as_dict()
        for key, value in data.items():
            if key not in base:
                logger.warning("[recall] %s 含未知权重字段 %r，忽略（可选 %s）",
                               WEIGHTS_SETTINGS_KEY, key, sorted(base))
                continue
            try:
                base[key] = float(value)
            except (TypeError, ValueError):
                logger.warning("[recall] %s 的字段 %s=%r 不是数字，保留缺省 %s",
                               WEIGHTS_SETTINGS_KEY, key, value, base[key])
        return cls(**base)


# 缺省权重实例（不读 settings；生产走 RecallWeights.from_settings()）
DEFAULT_WEIGHTS = RecallWeights()


@dataclass
class RecallDoc:
    """一条召回候选（多腿合并后的统一形态）。

    - doc_id：**去重键**（生产用 memory_id；无 memory_id 的行由调用方给合成键）
    - memory_id：可回溯的治理主键（缺失时为空串 —— 该候选不参与命中记账）
    - leg：该候选**保留下来**的那条腿（文本形态随腿不同：精确腿是注入形态，
      向量腿是写库时的 page_content）
    - relevance：该腿自己的相关度 ∈[0,1]（BM25 归一值 / 向量腿的排名代理）
    - legs：命中过该文档的全部腿（审计用："为什么召回了它"）
    - 治理信号：decay_score / hit_count / last_hit_at（由调用方从 DB 行带过来）
    """

    doc_id: str
    text: str
    leg: str
    memory_id: str = ""
    relevance: float = 0.0
    decay_score: float = 1.0
    hit_count: int = 0
    last_hit_at: str = ""
    vendor: str = ""
    legs: Tuple[str, ...] = ()

    def __post_init__(self):
        if not self.legs:
            self.legs = (self.leg,)


@dataclass(frozen=True)
class RecallScore:
    """打分结果（含**逐分项**贡献，可审计）。"""

    doc: RecallDoc
    total: float
    leg_base: float
    components: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """扁平可打印形态：memory_id / 腿 / 总分 / 各分项贡献。"""
        return {
            "doc_id": self.doc.doc_id,
            "memory_id": self.doc.memory_id,
            "vendor": self.doc.vendor,
            "leg": self.doc.leg,
            "legs": list(self.doc.legs),
            "total": round(self.total, 6),
            "components": {k: round(float(v), 6) for k, v in self.components.items()},
        }


def merge_candidates(docs: Sequence[RecallDoc],
                     weights: Optional[RecallWeights] = None) -> List[RecallDoc]:
    """多腿候选合并去重：同一 doc_id 只留一条。

    合并规则（三条都由"为什么要合并"决定）：
    - 文本/腿：保留 `leg_base` **最高**的那条（精确腿的文本是注入形态，向量腿是
      page_content；同一条记忆被两条腿召回时，注入形态才是要给人看的）。谁的腿基础分高
      由传入权重决定（缺省 `DEFAULT_WEIGHTS`）—— 与打分用同一份权重，避免"合并按一套
      权重、排序按另一套"；
    - relevance：取各腿的**最大值**（被两条腿同时命中＝更强的召回证据）；
    - legs：累计命中过的腿（审计用；也保证"关键词腿关掉"的对照实验里 legs 只剩实际腿）。
    首见顺序保留 —— 排序由 `rerank` 负责，本函数不排序（避免两处各排一次）。
    """
    w = weights or DEFAULT_WEIGHTS
    order: List[str] = []
    by_id: Dict[str, RecallDoc] = {}
    for d in docs or []:
        key = str(d.doc_id or "")
        if not key:
            continue
        prev = by_id.get(key)
        if prev is None:
            order.append(key)
            by_id[key] = d
            continue
        # 谁的腿基础分高，谁的文本胜出；legs 合并、relevance 取最大
        winner = d if w.leg_base(d.leg) > w.leg_base(prev.leg) else prev
        merged_legs = tuple(dict.fromkeys(list(prev.legs) + list(d.legs)))
        by_id[key] = RecallDoc(
            doc_id=winner.doc_id,
            text=winner.text,
            leg=winner.leg,
            memory_id=winner.memory_id or prev.memory_id,
            relevance=max(float(prev.relevance or 0.0), float(d.relevance or 0.0)),
            decay_score=winner.decay_score,
            hit_count=max(int(prev.hit_count or 0), int(d.hit_count or 0)),
            last_hit_at=winner.last_hit_at or prev.last_hit_at,
            vendor=winner.vendor or prev.vendor,
            legs=merged_legs,
        )
    return [by_id[k] for k in order]


def score_doc(query: str, doc: RecallDoc, weights: RecallWeights,
              query_vendor: str = "",
              now: Optional[datetime] = None) -> RecallScore:
    """单条候选打分（公式见模块 docstring；每一项都写进 components 以便审计）。

    `query_vendor`：查询方的供应商名（rag 传 `vendor`），用于「精确匹配 boost」；空串表示
    调用方不提供 → 该项恒 0（不猜）。
    """
    w = weights
    components = {
        "leg": w.leg_base(doc.leg),
        "relevance": w.relevance * float(doc.relevance or 0.0),
        "exact_match": w.exact_match * exact_vendor_match(doc.vendor, query_vendor),
        "decay": w.decay * float(doc.decay_score if doc.decay_score is not None else 1.0),
        "hit": w.hit * hit_score(doc.hit_count),
        "recency": w.recency * freshness(doc.last_hit_at, now=now),
    }
    total = sum(components.values())
    return RecallScore(doc=doc, total=total,
                       leg_base=w.leg_base(doc.leg), components=components)


def rerank(query: str, docs: Sequence[RecallDoc],
           weights: Optional[RecallWeights] = None,
           top_k: Optional[int] = None,
           now: Optional[datetime] = None,
           query_vendor: str = "") -> List[RecallScore]:
    """合并去重 → 逐条打分 → 降序返回（top_k 为 None 时不截断）。

    排序的确定性契约：总分降序 → 腿基础分降序 → **候选插入序**。
    第三条刻意用插入序（而不是 doc_id 字典序）：调用方的候选序是确定性的（DB 按
    rowid DESC 给行、BM25 逐条打分），用插入序破平局既保证"同一输入必得同一顺序"
    （可复算），又让**关掉关键词腿时排序自然退回调用方给的原序** —— 那正是改造前
    "按写入时间倒序、与查询无关"的旧行为，反证实验因此测的是真实退化而不是人为打乱。

    why 内部再 merge 一次：调用方可能自己先合并过（幂等），也可能直接把三条腿的原始
    候选全部传进来（生产 rag 就是这样）。合并逻辑只此一份，避免"两个入口两种去重口径"
    （本仓①类缺陷）。
    """
    w = weights or DEFAULT_WEIGHTS
    merged = merge_candidates(docs, w)
    scored = [(i, score_doc(query, d, w, query_vendor=query_vendor, now=now))
              for i, d in enumerate(merged)]
    scored.sort(key=lambda t: (-t[1].total, -t[1].leg_base, t[0]))
    out = [s for _i, s in scored]
    if top_k is not None and top_k >= 0:
        return out[:int(top_k)]
    return out


def explain(scores: Sequence[RecallScore]) -> List[dict]:
    """把打分结果拍平成可打印/可落盘的明细（审计面：逐条候选的每个分项贡献）。"""
    return [s.as_dict() for s in (scores or [])]

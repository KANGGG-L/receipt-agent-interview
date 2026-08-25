# -*- coding: utf-8 -*-
"""VendorMemory RAG：按供应商积累的识别上下文（护城河叙事）。

- 语料：供应商确认的单据 → 提取 layout/单位/别称线索，写入 Chroma
- 检索：识别前按「供应商名 + 语料相似度」top-k 注入 <vendor_context>
- 对齐完整版 D36：案例 RAG + 实体 RAG；写时只认正向信号（人工 approve 后回写）
"""

import os
from typing import Optional

from langchain_community.vectorstores import Chroma
from langchain_core.embeddings import Embeddings
from langchain_core.documents import Document

from app import db

PERSIST_DIR = os.environ.get("RAG_DIR", "./.rag_chroma")


class _CharNGramEmbeddings(Embeddings):
    """离线确定性嵌入（无 API 依赖）：char-n-gram 哈希 + L2 归一。

    Demo 用；生产换 DashScope/OpenAI embedding（RAG_EMBEDDING_PROVIDER 开关）。
    """

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)

    def _embed(self, text):
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


def _embedding_provider() -> Embeddings:
    return _CharNGramEmbeddings()


_vs: Optional[Chroma] = None
_vs_tenant: dict[str, Chroma] = {}


def _tenant_collection_name(tenant_id: str) -> str:
    tid = str(tenant_id or "default").strip() or "default"
    # 安全：仅允许字母数字下划线与中划线，其余替换
    import re
    tid = re.sub(r"[^a-zA-Z0-9_-]", "_", tid)[:64]
    return f"tenant_{tid}_vendor_memory"


def _store(tenant_id: Optional[str] = None):
    """租户隔离的 Chroma 向量库。

    每个 tenant_id 拥有独立 collection，物理硬隔离（FR-9）。
    向后兼容：tenant_id 为空或 default 时回退到全局 vendor_memory。
    """
    global _vs, _vs_tenant
    if tenant_id is None or str(tenant_id).strip() in ("", "default"):
        if _vs is None:
            _vs = Chroma(
                collection_name="vendor_memory",
                embedding_function=_embedding_provider(),
                persist_directory=PERSIST_DIR,
            )
        return _vs
    tid = str(tenant_id).strip()
    if tid not in _vs_tenant:
        _vs_tenant[tid] = Chroma(
            collection_name=_tenant_collection_name(tid),
            embedding_function=_embedding_provider(),
            persist_directory=PERSIST_DIR,
        )
    return _vs_tenant[tid]


def ingest_memory(vendor: str, items_text: str, notes: str = "", tenant_id: str = "default"):
    """人工 approve 后回写 VendorMemory（只认正向信号）。

    语料结构：供应商 + 单位/别称线索 + 已确认明细（few-shot 来源）。
    tenant_id 用于 Chroma 租户隔离（FR-9）。
    """
    docs = [
        Document(
            page_content=notes or "",
            metadata={"vendor": vendor, "kind": "notes", "tenant_id": tenant_id or "default"},
        ),
        Document(
            page_content=f"供应商 {vendor} 的已确认进货明细：\n{items_text}",
            metadata={"vendor": vendor, "kind": "sample", "tenant_id": tenant_id or "default"},
        ),
    ]
    _store(tenant_id).add_documents(docs)
    # 同时落库 vendor_memory 表（精确匹配用）
    try:
        db.upsert_vendor_memory(vendor, notes or "", items_text or "")
    except Exception:
        pass
    return vendor


# 繁简字符快速转换映射表（针对香港餐饮常见供应商字词）
_T2S_MAP = str.maketrans({
    '興': '兴', '糧': '粮', '發': '发', '記': '记', '聯': '联', '號': '号',
    '華': '华', '寶': '宝', '豐': '丰', '榮': '荣', '達': '达', '順': '顺',
    '億': '亿', '誠': '诚', '匯': '汇', '廣': '广', '慶': '庆', '業': '业',
    '實': '实', '總': '总', '標': '标', '龍': '龙', '雞': '鸡', '鴨': '鸭',
    '魚': '鱼', '麵': '面', '飯': '饭', '蛋': '蛋', '肉': '肉', '菜': '菜',
    '菇': '菇', '筍': '笋', '椒': '椒', '姜': '姜', '蒜': '蒜', '蔥': '葱',
    '進': '进', '貨': '货', '買': '买', '賣': '卖', '門': '门', '車': '车',
    '東': '东', '南': '南', '西': '西', '北': '北', '海': '海', '鮮': '鲜',
    '凍': '冻', '莊': '庄', '頭': '头', '條': '条', '隻': '只', '紥': '扎',
    '兩': '两', '廳': '厅', '館': '馆', '舖': '铺', '鋪': '铺', '市': '市',
    '場': '场', '檔': '档', '區': '区', '灣': '湾', '環': '环', '角': '角',
})

# 常见香港餐饮知名供应商别名组 (Canonical Alias Groups)
_VENDOR_ALIAS_GROUPS = [
    {"德利行", "takleehong", "tak lee hong", "德利行粮油", "德利行粮油批发", "德利行批发", "德利行有限公司"},
    {"祥兴", "祥興", "xiangxing", "cheunghing", "cheung hing", "祥兴快餐用品", "祥興快餐用品", "祥兴餐具", "祥兴餐具批发"},
    {"金百加", "kampery", "kamperky", "金百加发展", "金百加發展", "金百加发展有限公司", "金百加红茶", "金百加紅茶"},
    {"联丰", "聯豐", "luen fung", "luenfung", "lian feng", "lianfeng", "联丰食品", "聯豐食品", "联丰行", "聯豐行"},
    {"大生", "tai sang", "taisang", "da sheng", "dasheng", "大生行", "大生粮油", "大生糧油", "大生批发", "大生批發"},
    {"联记", "聯記", "luenkee", "luen kee", "联记号", "聯記號"},
    {"广昌泰", "廣昌泰", "kwongcheongthye", "kwong cheong thye"},
    {"九龙酱油", "九龍醬油", "kowloonsoysauce", "kowloon soy sauce"},
]

# 常见公司实体/业务后缀（用于归一化剥离对比）
_VENDOR_SUFFIXES = [
    "有限公司", "有限责任公司", "股份有限公司", "公司", "批發", "批发",
    "商行", "貿易", "贸易", "發展", "发展", "快餐用品", "用品", "餐具",
    "co.,ltd.", "co., ltd.", "co.ltd", "coltd", "ltd.", "ltd", "limited", "company", "co."
]


def _norm_vendor_name(name: str) -> str:
    """供应商名归一：NFKC + 繁转简 + 标点剥离 + casefold + 去空白（fuzzy 相关性门用）。"""
    import unicodedata
    import re
    s = unicodedata.normalize("NFKC", str(name or "")).translate(_T2S_MAP).casefold()
    s = re.sub(r"[()（）\[\]【】\-_/\\,.:;\"'·\s]+", "", s)
    return s.strip()


def _vendor_core_token(norm_name: str) -> str:
    """剥离常见公司与行业后缀，提取核心字根。"""
    s = norm_name
    for suf in _VENDOR_SUFFIXES:
        norm_suf = _norm_vendor_name(suf)
        if norm_suf and s.endswith(norm_suf) and len(s) > len(norm_suf) + 1:
            s = s[:-len(norm_suf)]
    return s


def _vendor_related(query_vendor: str, doc_vendor: str) -> bool:
    """AC4-a 供应商相关性门：查询名与记忆来源名归一后互为包含或共享别名组才放行（防他商记忆泄漏）。"""
    q = _norm_vendor_name(query_vendor)
    d = _norm_vendor_name(doc_vendor)
    if not q or not d:
        return False
    if q in d or d in q:
        return True

    # 核心词根匹配（如 "德利行" 与 "德利行takleehong" 或 "祥兴" 与 "祥兴快餐用品"）
    q_core = _vendor_core_token(q)
    d_core = _vendor_core_token(d)
    if q_core and d_core and (q_core in d or d_core in q or q_core in d_core or d_core in q_core):
        if len(q_core) >= 2 or len(d_core) >= 2:
            return True

    # 别名组匹配
    for group in _VENDOR_ALIAS_GROUPS:
        norm_group = {_norm_vendor_name(alias) for alias in group}
        q_in_group = any(q == a or a in q or q in a for a in norm_group)
        d_in_group = any(d == a or a in d or d in a for a in norm_group)
        if q_in_group and d_in_group:
            return True

    return False


def retrieve_context(vendor: str, top_k: int = 3, tenant_id: str = "default") -> str:
    """识别前检索 vendor_context：先精确匹配该供应商，再按相似度兜底。

    tenant_id 用作 Chroma 租户硬隔离过滤。
    冷启动供应商（无记忆且无同名 fuzzy 命中）返回空串，绝不携带他商记忆。
    """
    if not vendor or not str(vendor).strip():
        return ""
    # 1) 精确匹配：该供应商自己的历史记忆最可信（异常不阻断识别链路）
    exact = ""
    try:
        mem = db.get_vendor_memory(vendor)
        if mem:
            # db.get_vendor_memory 返回 dict（vendor, notes, sample）
            matched_vendor = (mem.get("vendor") if isinstance(mem, dict) else getattr(mem, "vendor", "")) or vendor
            notes = mem.get("notes") if isinstance(mem, dict) else getattr(mem, "notes", "")
            sample = mem.get("sample") if isinstance(mem, dict) else getattr(mem, "sample", "")
            exact_parts = []
            if notes:
                exact_parts.append(notes)
            if sample:
                exact_parts.append(sample)
            if exact_parts:
                exact = f"[{matched_vendor}]\n" + "\n".join(exact_parts)
    except Exception:
        exact = ""

    # 2) 相似度兜底：其他供应商的近似单据（冷启动/名字微变），按租户隔离
    #    相关性门：fuzzy 结果按来源供应商名与查询名归一匹配，不匹配的丢弃
    try:
        results = _store(tenant_id).similarity_search(
            f"供应商 {vendor} 的收据版式与单位习惯", k=top_k
        )
        fuzzy = "\n\n".join(
            f"[{d.metadata.get('vendor')}] {d.page_content}" for d in results
            if d.page_content.strip() and _vendor_related(vendor, d.metadata.get("vendor"))
        )
    except Exception:
        fuzzy = ""

    ctx = exact
    if fuzzy and fuzzy not in ctx:
        ctx = (ctx + "\n\n" + fuzzy).strip() if ctx else fuzzy
    return ctx or ""


def ingest_feedback_memory(vendor: str, comment: str, quality_warnings=None, tenant_id: str = "default"):
    """FR-9 三次连续点踩提炼：把连续纠偏的反馈沉淀为供应商记忆。

    仅当同供应商同租户最近 3 次反馈均为点踩时调用，写入 Chroma 租户隔离集合，
    避免错误记忆自我强化（单次点踩不沉淀）。
    """
    qw = quality_warnings or []
    content = f"供应商 {vendor} 反馈纠偏：{comment}".strip()
    if qw:
        content += f"\n关联质量告警：{'; '.join(qw)}"
    doc = Document(
        page_content=content,
        metadata={"vendor": vendor, "kind": "feedback_distilled", "tenant_id": tenant_id or "default"},
    )
    try:
        _store(tenant_id).add_documents([doc])
    except Exception:
        pass
    # 同时更新 vendor_memory 备注（累积提炼），用于精确检索
    try:
        existing = db.get_vendor_memory(vendor)
        prev_notes = (existing.get("notes") if existing else "") or ""
        merged = (prev_notes + "\n" + content).strip()[-4000:]
        db.upsert_vendor_memory(vendor, merged, "")
    except Exception:
        pass
    return content
